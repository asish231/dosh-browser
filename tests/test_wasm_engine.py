"""Hermetic tests for the Scoped WebAssembly Engine.

Fulfills Layer 1 Document & Runtime 'Scoped WASM interpreter' roadmap requirement.
Verifies:
- WASM binary format validation
- Scoped arithmetic, bitwise, memory, and control flow
- Linear memory grow and bound checking
- Host import calls and causal export tracking
- Fuel exhaustion trap (anti-tarpit defense)
"""

import pytest
from webresearch.engine.wasm import (
    WasmFuelExhausted,
    WasmInstance,
    WasmMemory,
    WasmModule,
    WasmTrap,
    validate_wasm,
)


def test_wasm_validation():
    # Valid minimal module (magic + version)
    valid_header = b"\x00asm\x01\x00\x00\x00"
    assert validate_wasm(valid_header) is True

    # Invalid magic
    invalid_magic = b"WASM\x01\x00\x00\x00"
    assert validate_wasm(invalid_magic) is False

    # Short binary
    assert validate_wasm(b"\x00asm") is False


def test_wasm_arithmetic_export():
    # add(i32, i32) -> i32
    wasm_add = bytes.fromhex(
        "0061736d0100000001070160027f7f017f030201000707010361646400000a09010700200020016a0b"
    )
    mod = WasmModule(wasm_add)
    inst = WasmInstance(mod)

    assert "add" in inst.exports
    assert inst.exports["add"](15, 27) == 42
    assert inst.exports["add"](-10, 5) == -5

    # Check causal call tracking
    assert len(inst.call_history) == 2
    assert inst.call_history[0]["name"] == "add"
    assert inst.call_history[0]["args"] == [15, 27]
    assert inst.call_history[0]["result"] == 42
    assert inst.call_history[0]["fuel_used"] > 0


def test_wasm_memory_store_and_load():
    # store_load(offset: i32, val: i32) -> i32
    wasm_mem = bytes([
        0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
        0x01, 0x07, 0x01, 0x60, 0x02, 0x7f, 0x7f, 0x01, 0x7f,
        0x03, 0x02, 0x01, 0x00,
        0x05, 0x03, 0x01, 0x00, 0x01,  # 1 page of memory
        0x07, 0x0E, 0x01, 0x0A, 0x73, 0x74, 0x6F, 0x72, 0x65, 0x5F, 0x6C, 0x6F, 0x61, 0x64, 0x00, 0x00,
        0x0A, 0x10, 0x01, 0x0E, 0x00,
        0x20, 0x00,        # local.get 0 (offset)
        0x20, 0x01,        # local.get 1 (val)
        0x36, 0x02, 0x00,  # i32.store align=2 offset=0
        0x20, 0x00,        # local.get 0 (offset)
        0x28, 0x02, 0x00,  # i32.load align=2 offset=0
        0x0B,              # end
    ])
    mod = WasmModule(wasm_mem)
    inst = WasmInstance(mod)

    assert inst.exports["store_load"](0, 123456) == 123456
    assert inst.exports["store_load"](64, -789) == -789

    # Out of bounds access must raise WasmTrap
    with pytest.raises(WasmTrap, match="out of bounds"):
        inst.exports["store_load"](70000, 1)


def test_wasm_memory_growth():
    mem = WasmMemory(initial_pages=1, maximum_pages=3)
    assert mem.size() == 1

    old = mem.grow(1)
    assert old == 1
    assert mem.size() == 2

    # Beyond maximum pages returns -1
    assert mem.grow(2) == -1
    assert mem.size() == 2


def test_wasm_imported_host_function():
    # Module imports env.hash_step(i32) -> i32 and exports compute(i32) -> i32
    wasm_imp = bytes([
        0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
        0x01, 0x06, 0x01, 0x60, 0x01, 0x7f, 0x01, 0x7f,
        0x02, 0x0F, 0x01, 0x03, 0x65, 0x6E, 0x76, 0x09, 0x68, 0x61, 0x73, 0x68, 0x5F, 0x73, 0x74, 0x65, 0x70, 0x00, 0x00,
        0x03, 0x02, 0x01, 0x00,
        0x07, 0x0B, 0x01, 0x07, 0x63, 0x6F, 0x6D, 0x70, 0x75, 0x74, 0x65, 0x00, 0x01,
        0x0A, 0x08, 0x01, 0x06, 0x00,
        0x20, 0x00,  # local.get 0
        0x10, 0x00,  # call 0 (imported)
        0x0B,        # end
    ])
    mod = WasmModule(wasm_imp)
    inst = WasmInstance(mod, import_object={"env": {"hash_step": lambda x: (x ^ 0x5A5A5A5A) + 1}})

    assert inst.exports["compute"](10) == (10 ^ 0x5A5A5A5A) + 1


def test_wasm_fuel_exhaustion_trap():
    # Infinite loop module: loop br 0 end end
    wasm_infinite = bytes([
        0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
        0x01, 0x04, 0x01, 0x60, 0x00, 0x00,
        0x03, 0x02, 0x01, 0x00,
        0x07, 0x08, 0x01, 0x04, 0x6C, 0x6F, 0x6F, 0x70, 0x00, 0x00,
        0x0A, 0x09, 0x01, 0x07, 0x00,
        0x03, 0x40,  # loop
        0x0C, 0x00,  # br 0
        0x0B,        # end loop
        0x0B,        # end func
    ])
    mod = WasmModule(wasm_infinite)
    inst = WasmInstance(mod, max_fuel=500)

    with pytest.raises(WasmFuelExhausted, match="fuel exhausted"):
        inst.exports["loop"]()
