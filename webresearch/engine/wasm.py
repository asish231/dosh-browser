"""Scoped WebAssembly Binary Decoder & Agentic Interpreter.

Fulfills the Layer 1 Document & Runtime 'Scoped WASM interpreter' roadmap requirement.
Implements the essential opcode subset used by real-world anti-bot challenges
(Cloudflare Turnstile, Kasada, DataDome, Google Botguard):
- Binary format decoder (magic, version, sections 1..11)
- Linear memory allocation and growth (64KB pages)
- Scoped integer arithmetic, bitwise, comparison, and control flow opcodes
- Instruction fuel metering (anti-tarpit defense)
- Causal telemetry recording for challenge exports
"""

from __future__ import annotations

import struct
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


WASM_MAGIC = b"\x00asm"
WASM_VERSION = b"\x01\x00\x00\x00"
PAGE_SIZE = 65536


class WasmTrap(Exception):
    """Raised when an unrecoverable trap or fuel exhaustion occurs."""
    pass


class WasmFuelExhausted(WasmTrap):
    """Raised when fuel counter reaches zero during execution."""
    pass


def decode_u32_leb(buf: bytes, offset: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    cur = offset
    while True:
        if cur >= len(buf):
            raise ValueError("unexpected EOF decoding u32 leb128")
        b = buf[cur]
        cur += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, cur


def decode_i32_leb(buf: bytes, offset: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    cur = offset
    while True:
        if cur >= len(buf):
            raise ValueError("unexpected EOF decoding i32 leb128")
        b = buf[cur]
        cur += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            if shift < 32 and (b & 0x40) != 0:
                result |= (~0 << shift)
            break
    # Wrap to 32-bit signed
    if result >= (1 << 31):
        result -= (1 << 32)
    elif result < -(1 << 31):
        result += (1 << 32)
    return result, cur


def decode_i64_leb(buf: bytes, offset: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    cur = offset
    while True:
        if cur >= len(buf):
            raise ValueError("unexpected EOF decoding i64 leb128")
        b = buf[cur]
        cur += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            if shift < 64 and (b & 0x40) != 0:
                result |= (~0 << shift)
            break
    return result, cur


class WasmMemory:
    """WASM Linear Memory with 64KB page allocation."""

    def __init__(self, initial_pages: int = 1, maximum_pages: Optional[int] = None):
        self.initial_pages = initial_pages
        self.maximum_pages = maximum_pages
        self.data = bytearray(initial_pages * PAGE_SIZE)

    def size(self) -> int:
        return len(self.data) // PAGE_SIZE

    def grow(self, delta_pages: int) -> int:
        old_size = self.size()
        new_size = old_size + delta_pages
        if self.maximum_pages is not None and new_size > self.maximum_pages:
            return -1
        if new_size > 1024:  # Bounded to 64MB for agent safety
            return -1
        self.data.extend(b"\x00" * (delta_pages * PAGE_SIZE))
        return old_size

    def read_u8(self, addr: int) -> int:
        if addr < 0 or addr >= len(self.data):
            raise WasmTrap(f"out of bounds memory read: addr={addr}")
        return self.data[addr]

    def read_i32(self, addr: int) -> int:
        if addr < 0 or addr + 4 > len(self.data):
            raise WasmTrap(f"out of bounds memory read: addr={addr}")
        v = struct.unpack_from("<I", self.data, addr)[0]
        if v >= 0x80000000:
            v -= 0x100000000
        return v

    def read_u32(self, addr: int) -> int:
        if addr < 0 or addr + 4 > len(self.data):
            raise WasmTrap(f"out of bounds memory read: addr={addr}")
        return struct.unpack_from("<I", self.data, addr)[0]

    def read_i64(self, addr: int) -> int:
        if addr < 0 or addr + 8 > len(self.data):
            raise WasmTrap(f"out of bounds memory read: addr={addr}")
        return struct.unpack_from("<q", self.data, addr)[0]

    def write_u8(self, addr: int, val: int) -> None:
        if addr < 0 or addr >= len(self.data):
            raise WasmTrap(f"out of bounds memory write: addr={addr}")
        self.data[addr] = val & 0xFF

    def write_i32(self, addr: int, val: int) -> None:
        if addr < 0 or addr + 4 > len(self.data):
            raise WasmTrap(f"out of bounds memory write: addr={addr}")
        struct.pack_into("<I", self.data, addr, val & 0xFFFFFFFF)

    def write_i64(self, addr: int, val: int) -> None:
        if addr < 0 or addr + 8 > len(self.data):
            raise WasmTrap(f"out of bounds memory write: addr={addr}")
        struct.pack_into("<q", self.data, addr, val)


class WasmFunction:
    def __init__(self, type_idx: int, locals_types: List[int], code: bytes):
        self.type_idx = type_idx
        self.locals_types = locals_types
        self.code = code


class WasmModule:
    """Decoded WASM module."""

    def __init__(self, binary: bytes):
        self.binary = bytes(binary)
        self.types: List[Tuple[List[int], List[int]]] = []
        self.func_type_indices: List[int] = []
        self.imports: List[Dict[str, Any]] = []
        self.exports: Dict[str, Tuple[str, int]] = {}
        self.functions: List[WasmFunction] = []
        self.memories: List[Tuple[int, Optional[int]]] = []
        self.globals: List[Tuple[int, bool, Any]] = []
        self.data_segments: List[Tuple[int, bytes]] = []
        self._decode()

    def _decode(self) -> None:
        if len(self.binary) < 8:
            raise ValueError("WASM binary too short")
        if self.binary[:4] != WASM_MAGIC:
            raise ValueError("invalid WASM magic header")
        if self.binary[4:8] != WASM_VERSION:
            raise ValueError("unsupported WASM version")

        offset = 8
        func_bodies = []
        while offset < len(self.binary):
            sec_id = self.binary[offset]
            offset += 1
            sec_len, offset = decode_u32_leb(self.binary, offset)
            sec_end = offset + sec_len

            if sec_id == 1:  # Type section
                num_types, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_types):
                    form = self.binary[offset]
                    offset += 1
                    num_params, offset = decode_u32_leb(self.binary, offset)
                    params = list(self.binary[offset:offset + num_params])
                    offset += num_params
                    num_results, offset = decode_u32_leb(self.binary, offset)
                    results = list(self.binary[offset:offset + num_results])
                    offset += num_results
                    self.types.append((params, results))

            elif sec_id == 2:  # Import section
                num_imports, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_imports):
                    mod_len, offset = decode_u32_leb(self.binary, offset)
                    mod_name = self.binary[offset:offset + mod_len].decode("utf-8", "replace")
                    offset += mod_len
                    field_len, offset = decode_u32_leb(self.binary, offset)
                    field_name = self.binary[offset:offset + field_len].decode("utf-8", "replace")
                    offset += field_len
                    kind = self.binary[offset]
                    offset += 1
                    desc, offset = decode_u32_leb(self.binary, offset)
                    self.imports.append({"module": mod_name, "field": field_name, "kind": kind, "desc": desc})
                    if kind == 0:  # Function import
                        self.func_type_indices.append(desc)

            elif sec_id == 3:  # Function section
                num_funcs, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_funcs):
                    type_idx, offset = decode_u32_leb(self.binary, offset)
                    self.func_type_indices.append(type_idx)

            elif sec_id == 5:  # Memory section
                num_mems, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_mems):
                    flags, offset = decode_u32_leb(self.binary, offset)
                    initial, offset = decode_u32_leb(self.binary, offset)
                    maximum = None
                    if flags & 1:
                        maximum, offset = decode_u32_leb(self.binary, offset)
                    self.memories.append((initial, maximum))

            elif sec_id == 7:  # Export section
                num_exports, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_exports):
                    name_len, offset = decode_u32_leb(self.binary, offset)
                    name = self.binary[offset:offset + name_len].decode("utf-8", "replace")
                    offset += name_len
                    kind_byte = self.binary[offset]
                    offset += 1
                    idx, offset = decode_u32_leb(self.binary, offset)
                    kind_map = {0: "function", 1: "table", 2: "memory", 3: "global"}
                    self.exports[name] = (kind_map.get(kind_byte, "unknown"), idx)

            elif sec_id == 10:  # Code section
                num_codes, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_codes):
                    code_len, offset = decode_u32_leb(self.binary, offset)
                    code_bytes = self.binary[offset:offset + code_len]
                    offset += code_len
                    func_bodies.append(code_bytes)

            elif sec_id == 11:  # Data section
                num_data, offset = decode_u32_leb(self.binary, offset)
                for _ in range(num_data):
                    mem_idx, offset = decode_u32_leb(self.binary, offset)
                    # Expression for offset (i32.const offset end)
                    op = self.binary[offset]
                    offset += 1
                    val = 0
                    if op == 0x41:  # i32.const
                        val, offset = decode_i32_leb(self.binary, offset)
                    if self.binary[offset] == 0x0B:  # end
                        offset += 1
                    data_len, offset = decode_u32_leb(self.binary, offset)
                    data_bytes = self.binary[offset:offset + data_len]
                    offset += data_len
                    self.data_segments.append((val, data_bytes))

            offset = sec_end

        # Match functions
        import_func_count = sum(1 for imp in self.imports if imp["kind"] == 0)
        for i, code_bytes in enumerate(func_bodies):
            type_idx = self.func_type_indices[import_func_count + i]
            # Parse locals
            loc_offset = 0
            num_local_decls, loc_offset = decode_u32_leb(code_bytes, loc_offset)
            locals_list = []
            for _ in range(num_local_decls):
                count, loc_offset = decode_u32_leb(code_bytes, loc_offset)
                val_type = code_bytes[loc_offset]
                loc_offset += 1
                locals_list.extend([val_type] * count)
            body_code = code_bytes[loc_offset:]
            self.functions.append(WasmFunction(type_idx, locals_list, body_code))


class WasmInstance:
    """Executable instance of a WASM module with instruction fuel metering."""

    def __init__(
        self,
        module: WasmModule,
        import_object: Optional[Dict[str, Dict[str, Any]]] = None,
        max_fuel: int = 5_000_000,
    ):
        self.module = module
        self.import_object = import_object or {}
        self.max_fuel = max_fuel
        self.fuel = max_fuel
        self.memory: Optional[WasmMemory] = None
        self.imported_funcs: List[Callable[..., Any]] = []
        self.call_history: List[Dict[str, Any]] = []

        # Initialize memory
        if module.memories:
            init_p, max_p = module.memories[0]
            self.memory = WasmMemory(init_p, max_p)
        elif "env" in self.import_object and "memory" in self.import_object["env"]:
            self.memory = self.import_object["env"]["memory"]

        # Apply data segments
        if self.memory:
            for seg_offset, seg_bytes in module.data_segments:
                for b_idx, b in enumerate(seg_bytes):
                    self.memory.write_u8(seg_offset + b_idx, b)

        # Resolve imported functions
        for imp in module.imports:
            if imp["kind"] == 0:  # function
                mod_name = imp["module"]
                field_name = imp["field"]
                fn = self.import_object.get(mod_name, {}).get(field_name)
                if not callable(fn):
                    # Fallback dummy import
                    fn = lambda *args: 0
                self.imported_funcs.append(fn)

        # Build exports
        self.exports: Dict[str, Any] = {}
        if self.memory:
            self.exports["memory"] = self.memory

        import_func_count = len(self.imported_funcs)
        for name, (kind, idx) in module.exports.items():
            if kind == "function":
                # Create callable wrapper
                self.exports[name] = self._make_export_callable(name, idx, import_func_count)
            elif kind == "memory" and self.memory:
                self.exports[name] = self.memory

    def _make_export_callable(self, name: str, func_idx: int, import_func_count: int) -> Callable[..., Any]:
        def export_wrapper(*args: Any) -> Any:
            initial_fuel = self.fuel
            res = self.invoke_function(func_idx, list(args))
            fuel_used = initial_fuel - self.fuel
            self.call_history.append({
                "name": name,
                "func_idx": func_idx,
                "args": list(args),
                "result": res,
                "fuel_used": fuel_used,
            })
            return res
        return export_wrapper

    def invoke_function(self, func_idx: int, args: List[Any]) -> Any:
        import_func_count = len(self.imported_funcs)
        if func_idx < import_func_count:
            return self.imported_funcs[func_idx](*args)

        func = self.module.functions[func_idx - import_func_count]
        param_types, result_types = self.module.types[func.type_idx]

        # Initialize locals: params followed by local zero values
        locals_vals: List[int] = [int(a) & 0xFFFFFFFF for a in args]
        for _ in func.locals_types:
            locals_vals.append(0)

        stack: List[int] = []
        code = func.code
        pc = 0

        # Control block stack for block/loop/if branching: (kind, pc_start, pc_end, stack_depth)
        # kind: 0 = block, 1 = loop, 2 = if
        blocks: List[Tuple[int, int, int]] = []

        while pc < len(code):
            self.fuel -= 1
            if self.fuel <= 0:
                raise WasmFuelExhausted("instruction fuel exhausted during WASM execution")

            op = code[pc]
            pc += 1

            if op == 0x00:  # unreachable
                raise WasmTrap("unreachable instruction executed")
            elif op == 0x01:  # nop
                continue
            elif op == 0x02:  # block
                block_type = code[pc]
                pc += 1
                # Find matching end
                end_pc = self._find_matching_end(code, pc)
                blocks.append((0, pc, end_pc))
            elif op == 0x03:  # loop
                block_type = code[pc]
                pc += 1
                end_pc = self._find_matching_end(code, pc)
                blocks.append((1, pc - 2, end_pc))
            elif op == 0x04:  # if
                cond = stack.pop() if stack else 0
                block_type = code[pc]
                pc += 1
                end_pc = self._find_matching_end(code, pc)
                else_pc = self._find_else(code, pc, end_pc)
                if cond != 0:
                    blocks.append((2, pc, end_pc))
                else:
                    if else_pc is not None:
                        pc = else_pc + 1
                        blocks.append((2, pc, end_pc))
                    else:
                        pc = end_pc
            elif op == 0x05:  # else
                # Skip to end of if block
                if blocks:
                    _, _, end_pc = blocks.pop()
                    pc = end_pc
            elif op == 0x0B:  # end
                if blocks:
                    blocks.pop()
                if pc >= len(code):
                    break
            elif op == 0x0C:  # br
                label_idx, pc = decode_u32_leb(code, pc)
                target = blocks[len(blocks) - 1 - label_idx]
                kind, start_pc, end_pc = target
                if kind == 1:  # loop branches back to start
                    pc = start_pc + 2
                else:  # block branches forward to end
                    pc = end_pc
            elif op == 0x0D:  # br_if
                label_idx, pc = decode_u32_leb(code, pc)
                cond = stack.pop() if stack else 0
                if cond != 0:
                    target = blocks[len(blocks) - 1 - label_idx]
                    kind, start_pc, end_pc = target
                    if kind == 1:
                        pc = start_pc + 2
                    else:
                        pc = end_pc
            elif op == 0x0F:  # return
                break
            elif op == 0x10:  # call
                callee_idx, pc = decode_u32_leb(code, pc)
                # Grab parameters from stack
                callee_type_idx = self.module.func_type_indices[callee_idx]
                c_params, c_results = self.module.types[callee_type_idx]
                c_args = []
                for _ in range(len(c_params)):
                    c_args.insert(0, stack.pop())
                c_res = self.invoke_function(callee_idx, c_args)
                if c_results and c_res is not None:
                    stack.append(c_res & 0xFFFFFFFF)
            elif op == 0x1A:  # drop
                if stack:
                    stack.pop()
            elif op == 0x1B:  # select
                c = stack.pop() if stack else 0
                v2 = stack.pop() if stack else 0
                v1 = stack.pop() if stack else 0
                stack.append(v1 if c != 0 else v2)
            elif op == 0x20:  # local.get
                loc_idx, pc = decode_u32_leb(code, pc)
                val = locals_vals[loc_idx] if loc_idx < len(locals_vals) else 0
                stack.append(val)
            elif op == 0x21:  # local.set
                loc_idx, pc = decode_u32_leb(code, pc)
                val = stack.pop() if stack else 0
                if loc_idx < len(locals_vals):
                    locals_vals[loc_idx] = val & 0xFFFFFFFF
            elif op == 0x22:  # local.tee
                loc_idx, pc = decode_u32_leb(code, pc)
                val = stack[-1] if stack else 0
                if loc_idx < len(locals_vals):
                    locals_vals[loc_idx] = val & 0xFFFFFFFF
            elif op == 0x28:  # i32.load
                align, pc = decode_u32_leb(code, pc)
                mem_offset, pc = decode_u32_leb(code, pc)
                base = stack.pop() if stack else 0
                if self.memory:
                    stack.append(self.memory.read_i32(base + mem_offset))
                else:
                    stack.append(0)
            elif op == 0x2C:  # i32.load8_s
                align, pc = decode_u32_leb(code, pc)
                mem_offset, pc = decode_u32_leb(code, pc)
                base = stack.pop() if stack else 0
                if self.memory:
                    v = self.memory.read_u8(base + mem_offset)
                    if v & 0x80:
                        v |= -256
                    stack.append(v)
                else:
                    stack.append(0)
            elif op == 0x2D:  # i32.load8_u
                align, pc = decode_u32_leb(code, pc)
                mem_offset, pc = decode_u32_leb(code, pc)
                base = stack.pop() if stack else 0
                if self.memory:
                    stack.append(self.memory.read_u8(base + mem_offset))
                else:
                    stack.append(0)
            elif op == 0x36:  # i32.store
                align, pc = decode_u32_leb(code, pc)
                mem_offset, pc = decode_u32_leb(code, pc)
                val = stack.pop() if stack else 0
                base = stack.pop() if stack else 0
                if self.memory:
                    self.memory.write_i32(base + mem_offset, val)
            elif op == 0x3A:  # i32.store8
                align, pc = decode_u32_leb(code, pc)
                mem_offset, pc = decode_u32_leb(code, pc)
                val = stack.pop() if stack else 0
                base = stack.pop() if stack else 0
                if self.memory:
                    self.memory.write_u8(base + mem_offset, val & 0xFF)
            elif op == 0x3F:  # memory.size
                mem_idx = code[pc]
                pc += 1
                stack.append(self.memory.size() if self.memory else 0)
            elif op == 0x40:  # memory.grow
                mem_idx = code[pc]
                pc += 1
                delta = stack.pop() if stack else 0
                stack.append(self.memory.grow(delta) if self.memory else -1)
            elif op == 0x41:  # i32.const
                val, pc = decode_i32_leb(code, pc)
                stack.append(val)
            elif op == 0x45:  # i32.eqz
                a = stack.pop() if stack else 0
                stack.append(1 if (a & 0xFFFFFFFF) == 0 else 0)
            elif op == 0x46:  # i32.eq
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append(1 if (a & 0xFFFFFFFF) == (b & 0xFFFFFFFF) else 0)
            elif op == 0x47:  # i32.ne
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append(1 if (a & 0xFFFFFFFF) != (b & 0xFFFFFFFF) else 0)
            elif op == 0x48:  # i32.lt_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                stack.append(1 if a < b else 0)
            elif op == 0x49:  # i32.lt_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                stack.append(1 if a < b else 0)
            elif op == 0x4A:  # i32.gt_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                stack.append(1 if a > b else 0)
            elif op == 0x4B:  # i32.gt_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                stack.append(1 if a > b else 0)
            elif op == 0x4C:  # i32.le_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                stack.append(1 if a <= b else 0)
            elif op == 0x4D:  # i32.le_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                stack.append(1 if a <= b else 0)
            elif op == 0x4E:  # i32.ge_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                stack.append(1 if a >= b else 0)
            elif op == 0x4F:  # i32.ge_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                stack.append(1 if a >= b else 0)
            elif op == 0x6A:  # i32.add
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append((a + b) & 0xFFFFFFFF)
            elif op == 0x6B:  # i32.sub
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append((a - b) & 0xFFFFFFFF)
            elif op == 0x6C:  # i32.mul
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append((a * b) & 0xFFFFFFFF)
            elif op == 0x6D:  # i32.div_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                if b == 0:
                    raise WasmTrap("integer divide by zero")
                stack.append(int(a / b) & 0xFFFFFFFF)
            elif op == 0x6E:  # i32.div_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                if b == 0:
                    raise WasmTrap("integer divide by zero")
                stack.append((a // b) & 0xFFFFFFFF)
            elif op == 0x6F:  # i32.rem_s
                b = self._to_i32(stack.pop() if stack else 0)
                a = self._to_i32(stack.pop() if stack else 0)
                if b == 0:
                    raise WasmTrap("integer divide by zero")
                stack.append((a % b) & 0xFFFFFFFF)
            elif op == 0x70:  # i32.rem_u
                b = (stack.pop() if stack else 0) & 0xFFFFFFFF
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                if b == 0:
                    raise WasmTrap("integer divide by zero")
                stack.append((a % b) & 0xFFFFFFFF)
            elif op == 0x71:  # i32.and
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append(a & b)
            elif op == 0x72:  # i32.or
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append(a | b)
            elif op == 0x73:  # i32.xor
                b = stack.pop() if stack else 0
                a = stack.pop() if stack else 0
                stack.append(a ^ b)
            elif op == 0x74:  # i32.shl
                b = (stack.pop() if stack else 0) & 0x1F
                a = stack.pop() if stack else 0
                stack.append((a << b) & 0xFFFFFFFF)
            elif op == 0x75:  # i32.shr_s
                b = (stack.pop() if stack else 0) & 0x1F
                a = self._to_i32(stack.pop() if stack else 0)
                stack.append((a >> b) & 0xFFFFFFFF)
            elif op == 0x76:  # i32.shr_u
                b = (stack.pop() if stack else 0) & 0x1F
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                stack.append((a >> b) & 0xFFFFFFFF)
            elif op == 0x77:  # i32.rotl
                b = (stack.pop() if stack else 0) & 0x1F
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                res = ((a << b) | (a >> (32 - b))) & 0xFFFFFFFF
                stack.append(res)
            elif op == 0x78:  # i32.rotr
                b = (stack.pop() if stack else 0) & 0x1F
                a = (stack.pop() if stack else 0) & 0xFFFFFFFF
                res = ((a >> b) | (a << (32 - b))) & 0xFFFFFFFF
                stack.append(res)
            else:
                # Unsupported opcode in scoped challenge set
                raise WasmTrap(f"unsupported WASM opcode 0x{op:02x}")

        if result_types and stack:
            res_val = stack[-1]
            return self._to_i32(res_val)
        return None

    @staticmethod
    def _to_i32(v: int) -> int:
        v = v & 0xFFFFFFFF
        if v >= 0x80000000:
            v -= 0x100000000
        return v

    @staticmethod
    def _find_matching_end(code: bytes, start_pc: int) -> int:
        depth = 1
        cur = start_pc
        while cur < len(code) and depth > 0:
            b = code[cur]
            cur += 1
            if b in (0x02, 0x03, 0x04):  # block, loop, if
                depth += 1
            elif b == 0x0B:  # end
                depth -= 1
        return cur

    @staticmethod
    def _find_else(code: bytes, start_pc: int, end_pc: int) -> Optional[int]:
        depth = 0
        cur = start_pc
        while cur < end_pc:
            b = code[cur]
            if b in (0x02, 0x03, 0x04):
                depth += 1
            elif b == 0x05 and depth == 0:  # matching else
                return cur
            elif b == 0x0B:
                depth -= 1
            cur += 1
        return None


def validate_wasm(binary: bytes) -> bool:
    """Returns True if binary is a valid WASM header and decodable module."""
    try:
        WasmModule(binary)
        return True
    except Exception:
        return False
