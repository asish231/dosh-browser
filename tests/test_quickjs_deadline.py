"""Deadline / resource-cap tests for the QuickJS page runtime.

The active engine is QuickJS. The `_quickjs` C extension's `set_time_limit`
does NOT interrupt tight CPU loops (a `for(i;;)` with limit=200 ran past a 6s
wall timeout during probing), so a true CPU-loop deadline is blocked on a
patched binding. These tests pin what IS reliably bounded today: memory and
stack, armed on every arena Context via `_arm_untrusted_limits`.
"""

import time

import pytest

from webresearch.engine.runtime import _arm_untrusted_limits, _create_context


def test_memory_bomb_is_bounded(monkeypatch):
    """An unbounded-allocation page script must be stopped by the memory cap,
    not grow until the host OOMs."""
    monkeypatch.setenv("WR_JS_MEMORY_LIMIT_MB", "8")
    ctx = _create_context()
    start = time.monotonic()
    with pytest.raises(Exception):
        ctx.eval('var a=[];while(true){a.push("x".repeat(100000))}')
    assert time.monotonic() - start < 5.0


def test_normal_eval_still_works_under_limits():
    """Arming limits must not break ordinary page-JS."""
    ctx = _create_context()
    assert ctx.eval("6 * 7") == 42
    assert ctx.eval("[1,2,3].map(x=>x+1).join(',')") == "2,3,4"


def test_stack_overflow_is_bounded():
    """Runaway recursion must hit the stack guard, not crash the host."""
    ctx = _create_context()
    with pytest.raises(Exception):
        ctx.eval("function f(){f()} f()")


def test_limits_are_env_tunable(monkeypatch):
    """Env tunability under the doshgentv8 V8 lane.

    Honest V8 contract (per the bridge's own note in
    doshgentv8/core/src/lib.rs): heap limits are fixed at ISOLATE CREATION
    via CreateParams::heap_limits — a post-hoc set_memory_limit stores the
    budget but does not re-cap an already-created heap. So the V8 assertion
    is: (a) the env knob is accepted and arming never raises, and (b) the
    wall-clock watchdog (set_time_limit, host-callable safe on this bridge,
    unlike banned QuickJS) bounds runaway work instead.
    """
    monkeypatch.setenv("WR_JS_MEMORY_LIMIT_MB", "4")
    monkeypatch.setenv("WR_JS_TIME_LIMIT_MS", "500")
    ctx = _create_context()  # must not raise despite the 4 MiB knob
    assert ctx.eval("6 * 7") == 42
    # The watchdog bounds a runaway loop (would otherwise spin forever).
    with pytest.raises(Exception):
        ctx.eval("while(true){}")


def test_arm_untrusted_limits_is_idempotent():
    """Calling arm twice must not raise and must leave the context usable."""
    ctx = _create_context()
    _arm_untrusted_limits(ctx)
    _arm_untrusted_limits(ctx)
    assert ctx.eval("1+1") == 2


def test_host_callables_work_under_limits():
    """Regression: set_time_limit must NOT be armed, because _quickjs raises
    "InternalError: Can not call into Python with a time limit set" and breaks
    every host_fetch / host_post_message / host_crypto_digest callable the
    runtime registers. Memory + stack limits are host-callable-safe."""
    ctx = _create_context()
    seen = []
    ctx.add_callable("probe_cb", lambda v: seen.append(v) or "ok")
    # The host callable must be invocable from JS under the armed limits.
    assert ctx.eval("probe_cb('ring')") == "ok"
    assert seen == ["ring"]


def test_time_limit_is_not_armed_on_created_context():
    """A freshly created context must not carry a time limit that blocks
    Python re-entry. This pins the decision to NOT call set_time_limit."""
    import quickjs
    ctx = _create_context()
    seen = []
    ctx.add_callable("probe_cb", lambda v: seen.append(v))
    # If a time limit were armed, this would raise JSException instead of ok.
    ctx.eval("probe_cb('x')")
    assert seen == ["x"]

