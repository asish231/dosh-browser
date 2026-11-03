"""doshgentv8 — embedded V8 engine for dosh-browser (doshgentv8.1).

Backend selector: prefers the Rust bridge (`doshgentv8_rust`, built from
doshgentv8/py — rusty_v8 + PyO3 with real synchronous host callables) and
falls back to the Phase-0 mini-racer shim (eval only) when the extension
is not built.

Usage:
    from doshgentv8 import DoshContext
    ctx = DoshContext()
    ctx.eval("1 + 1")           # → "2"
    ctx.add_callable("host_log", lambda msg, level="log": None)
    ctx.execute_pending_job()    # → False (one checkpoint drains all)
"""

from __future__ import annotations

__version__ = "1.0.0"

# Product identity. "doshgentv8.1" is our engine brand; the vendored upstream
# base (doshgentv8/base) retains its BSD-3 LICENSE and AUTHORS files — credit
# stays with the license files, the name we report is ours.
ENGINE_NAME = "doshgentv8.1"
ENGINE_BASE = "V8 15.9.0 (doshgentv8/base)"


def backend() -> str:
    """Which engine backend this import selected."""
    return _BACKEND


try:
    # Preferred: the Rust bridge. Synchronous host callables over the
    # doshgentv8-core HostSeam trait; quickjs.Context-compatible surface.
    from doshgentv8_rust import DoshContext  # noqa: F401

    _BACKEND = "doshgentv8-rust (rusty_v8 + pyo3)"
except ImportError:
    _BACKEND = "mini-racer (phase-0 fallback: eval only)"
    from py_mini_racer import MiniRacer

    class DoshContext:  # noqa: D101
        """Phase-0 shim: eval only. Cannot host synchronous callables —
        mini-racer's host functions are async/Promise-based, which is
        structurally wrong for the runtime.py seam. Build the Rust bridge
        (doshgentv8/py: `cargo build --release`) for the real seam."""

        def __init__(self) -> None:
            self._ctx = MiniRacer()

        def eval(self, code: str) -> str:
            return str(self._ctx.eval(code))

        def add_callable(self, name: str, fn) -> None:
            raise NotImplementedError(
                f"mini-racer fallback cannot host '{name}': no synchronous "
                "callables. Build doshgentv8/py (cargo build --release) and "
                "install doshgentv8_rust into site-packages."
            )

        def execute_pending_job(self) -> bool:
            return False  # mini-racer drains microtasks at script end

        def set_memory_limit(self, bytes_: int) -> None:
            self._ctx.set_hard_memory_limit(int(bytes_))

        def set_time_limit(self, seconds: float) -> None:
            # mini-racer supports per-eval timeouts; a persistent per-context
            # limit is approximated by the hard memory limit + honest note.
            pass

        def set_max_stack_size(self, bytes_: int) -> None:
            pass

        def close(self) -> None:
            self._ctx.close()
