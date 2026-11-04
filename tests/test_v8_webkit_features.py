"""Tests for doshgentv8 WebKit-inspired features and subsystem pruning:
- Contiguous Butterfly object layout
- Deterministic instruction fuel metering and trap
- Virtual host-clock injection (Date.now / performance.now)
- Deterministic seeded PRNG (XorShift128+)
- Scoped capability gates (eval, wasm, network)
- Proper tail call (PTC) trampoline
"""

import pytest

try:
    import doshgentv8_rust
    HAS_V8 = True
except ImportError:
    HAS_V8 = False


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_virtual_host_clock():
    ctx = doshgentv8_rust.DoshContext()
    ctx.set_virtual_time(1700000000000.0)
    assert ctx.eval("Date.now()") == 1700000000000

    ctx.advance_virtual_time(500.0)
    assert ctx.eval("Date.now()") == 1700000000500


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_deterministic_seeded_prng():
    ctx1 = doshgentv8_rust.DoshContext()
    ctx2 = doshgentv8_rust.DoshContext()

    ctx1.set_random_seed(987654)
    ctx2.set_random_seed(987654)

    r1_a = ctx1.eval("Math.random()")
    r1_b = ctx1.eval("Math.random()")

    r2_a = ctx2.eval("Math.random()")
    r2_b = ctx2.eval("Math.random()")

    assert r1_a == r2_a
    assert r1_b == r2_b
    assert r1_a != r1_b


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_butterfly_memory_layout():
    ctx = doshgentv8_rust.DoshContext()
    res = ctx.eval("""
        const b = __createButterfly(["id", "className", "role"], [10, 20, 30]);
        b.setProperty("id", "submit-btn");
        b.setProperty("role", "button");
        b.push(40);
        JSON.stringify({
            id: b.getProperty("id"),
            role: b.getProperty("role"),
            elem0: b.getElement(0),
            elem3: b.getElement(3),
            len: b.length
        });
    """)
    assert "submit-btn" in res
    assert "button" in res
    assert '"elem3":40' in res
    assert '"len":4' in res


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_capability_gate_eval():
    ctx = doshgentv8_rust.DoshContext()
    assert ctx.eval("eval('5 * 5')") == 25

    ctx.set_eval_allowed(False)
    with pytest.raises(RuntimeError, match="disallowed"):
        ctx.eval("eval('5 * 5')")

    ctx.set_eval_allowed(True)
    assert ctx.eval("eval('5 * 5')") == 25


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_fuel_metering():
    ctx = doshgentv8_rust.DoshContext()
    ctx.set_fuel(20)
    assert ctx.fuel_remaining() == 20

    ctx.eval("__dosh_step(5)")
    assert ctx.fuel_remaining() == 15

    with pytest.raises(RuntimeError, match="fuel exhausted"):
        ctx.eval("__dosh_step(20)")

    assert ctx.fuel_remaining() == 0


@pytest.mark.skipif(not HAS_V8, reason="doshgentv8_rust not available")
def test_v8_proper_tail_call_trampoline():
    ctx = doshgentv8_rust.DoshContext()
    res = ctx.eval("""
        const recurse = __tco(function f(n, acc = 0) {
            if (n <= 0) return acc;
            return __thunk(f, n - 1, acc + 1);
        });
        recurse(25000);
    """)
    assert res == 25000

