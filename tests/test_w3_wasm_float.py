"""W3: WASM float + extended opcodes — hermetic regression tests.

Builds real WASM binaries by hand (LEB128 + sections) and executes them
through the minidom.js WebAssembly engine. Covers the Phase 5 W3 scope:
f32/f64 constants, arithmetic (spec opcodes 0x8B–0xA6), comparisons,
memory load/store, conversions, i64-as-i32 integer ops, sign extensions.

Before the fix, the float handlers lived at WRONG opcode numbers
(0x7B–0x96) that hijacked the real i64 range: add(1.5, 2.5) returned 2
(args truncated to int, then 0x92 executed as "f64.mul": 1*2) and
add(3.0, 4.0) returned 12 (3*4). These tests pin the corrected behavior.
"""

import json

import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx() -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree("<html><body></body></html>")
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def leb(n: int) -> bytes:
    out = []
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sleb32(val: int) -> bytes:
    # signed LEB128 for 32-bit values
    out = []
    more = True
    while more:
        b = val & 0x7F
        val >>= 7
        if (val == 0 and not (b & 0x40)) or (val == -1 and (b & 0x40)):
            more = False
        else:
            b |= 0x80
        out.append(b)
    return bytes(out)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + leb(len(payload)) + payload


def build_module(*, params: bytes, results: bytes, body: bytes,
                 export_name: bytes = b"run", memory: bool = False) -> bytes:
    """One exported function; optional memory (min 1 page)."""
    mod = b"\x00asm\x01\x00\x00\x00"
    mod += section(1, leb(1) + b"\x60" + leb(len(params)) + params
                   + leb(len(results)) + results)
    mod += section(3, leb(1) + leb(0))
    if memory:
        mod += section(5, leb(1) + b"\x00" + leb(1))
    mod += section(7, leb(1) + leb(len(export_name)) + export_name + b"\x00" + leb(0))
    mod += section(10, leb(1) + leb(len(body)) + body)
    return mod


def run_wasm(ctx: Context, wasm: bytes, *args) -> object:
    arr = ",".join(str(b) for b in wasm)
    arglist = ",".join(json.dumps(a) for a in args)
    return ctx.eval(
        f"""
        (function() {{
            var mod = new WebAssembly.Module(new Uint8Array([{arr}]));
            var inst = new WebAssembly.Instance(mod, {{}});
            return inst.exports.run({arglist});
        }})()
        """
    )


I32, I64, F32, F64 = b"\x7f", b"\x7e", b"\x7d", b"\x7c"


def test_f32_add_sub_mul_div():
    ctx = _ctx()
    cases = [
        (0x92, 1.5, 2.5, 4.0),    # f32.add
        (0x93, 5.5, 2.0, 3.5),    # f32.sub
        (0x94, 3.0, 4.0, 12.0),   # f32.mul
        (0x95, 7.5, 2.5, 3.0),    # f32.div
    ]
    for op, a, b, want in cases:
        body = b"\x00" + b"\x20\x00" + b"\x20\x01" + bytes([op]) + b"\x0b"
        wasm = build_module(params=F32 + F32, results=F32, body=body)
        got = run_wasm(ctx, wasm, a, b)
        assert got == pytest.approx(want, abs=1e-5), \
            f"op=0x{op:02x}: expected {want}, got {got}"


def test_f64_add_sub_mul_div():
    ctx = _ctx()
    cases = [
        (0xA0, 1.5, 2.5, 4.0),    # f64.add
        (0xA1, 5.5, 2.0, 3.5),    # f64.sub
        (0xA2, 3.0, 4.0, 12.0),   # f64.mul
        (0xA3, 7.5, 2.5, 3.0),    # f64.div
    ]
    for op, a, b, want in cases:
        body = b"\x00" + b"\x20\x00" + b"\x20\x01" + bytes([op]) + b"\x0b"
        wasm = build_module(params=F64 + F64, results=F64, body=body)
        got = run_wasm(ctx, wasm, a, b)
        assert got == pytest.approx(want, abs=1e-9), \
            f"op=0x{op:02x}: expected {want}, got {got}"


def test_f32_unary_sqrt_abs_neg():
    ctx = _ctx()
    # f32.sqrt (0x91) of param
    body = b"\x00" + b"\x20\x00" + b"\x91" + b"\x0b"
    got = run_wasm(ctx, build_module(params=F32, results=F32, body=body), 9.0)
    assert got == pytest.approx(3.0, abs=1e-6)
    # f32.abs (0x8B)
    body = b"\x00" + b"\x20\x00" + b"\x8B" + b"\x0b"
    got = run_wasm(ctx, build_module(params=F32, results=F32, body=body), -2.5)
    assert got == pytest.approx(2.5, abs=1e-6)
    # f32.neg (0x8C)
    body = b"\x00" + b"\x20\x00" + b"\x8C" + b"\x0b"
    got = run_wasm(ctx, build_module(params=F32, results=F32, body=body), 1.25)
    assert got == pytest.approx(-1.25, abs=1e-6)


def test_f32_const_0x43_and_f64_const_0x44():
    """f32.const/f64.const decode IEEE754 little-endian payload bytes."""
    import struct
    ctx = _ctx()
    # (result f32) f32.const 2.5
    payload = struct.pack("<f", 2.5)
    body = b"\x00" + b"\x43" + payload + b"\x0b"
    got = run_wasm(ctx, build_module(params=b"", results=F32, body=body))
    assert got == pytest.approx(2.5, abs=1e-6)
    # (result f64) f64.const -1.5
    payload = struct.pack("<d", -1.5)
    body = b"\x00" + b"\x44" + payload + b"\x0b"
    got = run_wasm(ctx, build_module(params=b"", results=F64, body=body))
    assert got == pytest.approx(-1.5, abs=1e-9)


def test_f32_comparisons():
    ctx = _ctx()
    for op, a, b, want in [
        (0x5B, 1.5, 1.5, 1),   # f32.eq
        (0x5D, 1.0, 2.0, 1),   # f32.lt
        (0x5E, 3.0, 2.0, 1),   # f32.gt
        (0x5B, 1.0, 2.0, 0),   # f32.eq false
        (0x5F, 2.0, 2.0, 1),   # f32.le
        (0x60, 2.0, 2.0, 1),   # f32.ge
    ]:
        body = b"\x00" + b"\x20\x00" + b"\x20\x01" + bytes([op]) + b"\x0b"
        wasm = build_module(params=F32 + F32, results=I32, body=body)
        got = run_wasm(ctx, wasm, a, b)
        assert got == want, f"op=0x{op:02x}: expected {want}, got {got}"


def test_f64_comparisons():
    ctx = _ctx()
    for op, a, b, want in [
        (0x61, 2.5, 2.5, 1),   # f64.eq
        (0x63, 1.0, 2.0, 1),   # f64.lt
        (0x66, 3.0, 2.0, 1),   # f64.ge
        (0x62, 1.0, 2.0, 1),   # f64.ne
    ]:
        body = b"\x00" + b"\x20\x00" + b"\x20\x01" + bytes([op]) + b"\x0b"
        wasm = build_module(params=F64 + F64, results=I32, body=body)
        got = run_wasm(ctx, wasm, a, b)
        assert got == want, f"op=0x{op:02x}: expected {want}, got {got}"


def test_f32_memory_store_load():
    """f32.store (0x38) then f32.load (0x2A) roundtrips the float exactly."""
    ctx = _ctx()
    # (memory 1) (func (param f32) (result f32) i32.const 0; local.get 0; f32.store; i32.const 0; f32.load)
    body = (b"\x00"
            + b"\x41" + sleb32(0)        # i32.const 0
            + b"\x20\x00"                # local.get 0
            + b"\x38\x02\x00"            # f32.store align=2 offset=0
            + b"\x41" + sleb32(0)        # i32.const 0
            + b"\x2A\x02\x00"            # f32.load align=2 offset=0
            + b"\x0b")
    wasm = build_module(params=F32, results=F32, body=body, memory=True)
    got = run_wasm(ctx, wasm, 3.75)
    assert got == pytest.approx(3.75, abs=1e-6)


def test_f64_memory_store_load():
    ctx = _ctx()
    body = (b"\x00"
            + b"\x41" + sleb32(8)
            + b"\x20\x00"
            + b"\x39\x03\x00"            # f64.store align=3 offset=0
            + b"\x41" + sleb32(8)
            + b"\x2B\x03\x00"            # f64.load align=3 offset=0
            + b"\x0b")
    wasm = build_module(params=F64, results=F64, body=body, memory=True)
    got = run_wasm(ctx, wasm, 2.25)
    assert got == pytest.approx(2.25, abs=1e-9)


def test_conversions_f64_to_i32_and_back():
    ctx = _ctx()
    # i32.trunc_f64_s (0xAA): 7.9 -> 7
    body = b"\x00" + b"\x20\x00" + b"\xAA" + b"\x0b"
    got = run_wasm(ctx, build_module(params=F64, results=I32, body=body), 7.9)
    assert got == 7
    # f64.convert_i32_s (0xB7): 42 -> 42.0
    body = b"\x00" + b"\x20\x00" + b"\xB7" + b"\x0b"
    got = run_wasm(ctx, build_module(params=I32, results=F64, body=body), 42)
    assert got == pytest.approx(42.0, abs=1e-9)


def test_i64_add_i32_approximated():
    """i64.add (0x7C) computes on the engine's i32-approximated lane.

    Before the fix, 0x7C was hijacked by a wrong "f32.abs" handler.
    """
    ctx = _ctx()
    body = b"\x00" + b"\x20\x00" + b"\x20\x01" + b"\x7C" + b"\x0b"
    wasm = build_module(params=I64 + I64, results=I64, body=body)
    got = run_wasm(ctx, wasm, 7, 5)
    assert got == 12
    got = run_wasm(ctx, wasm, -3, 10)
    assert got == 7


def test_sign_extension_0xC0():
    """i32.extend8_s (0xC0) sign-extends a byte."""
    ctx = _ctx()
    # i32.const 0xFF (=255), extend8_s -> -1
    body = b"\x00" + b"\x41" + sleb32(255) + b"\xC0" + b"\x0b"
    got = run_wasm(ctx, build_module(params=b"", results=I32, body=body))
    assert got == -1


def test_float_params_survive_entry():
    """The old entry coercion (args[a] | 0) truncated 2.75 to 2.

    Pins the type-aware local init: an untouched param returns unchanged.
    """
    ctx = _ctx()
    body = b"\x00" + b"\x20\x00" + b"\x0b"   # local.get 0
    wasm = build_module(params=F64, results=F64, body=body)
    got = run_wasm(ctx, wasm, 2.75)
    assert got == pytest.approx(2.75, abs=1e-9)
