"""Phase 0 benchmark: boot time (minidom eval) + RSS footprint.

Run against whichever dylib is installed in site-packages; flip the feature
off and rerun to produce the A/B pair.
"""
from __future__ import annotations

import statistics
import sys
import time
import resource
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO.parent))

MINIDOM_JS = (REPO.parent / "webresearch" / "engine" / "minidom.js").read_text(
    encoding="utf-8"
)

from quickjs import Context as QJSContext          # noqa: E402
from doshgentv8_rust import DoshContext as RustContext  # noqa: E402


def rss_kb() -> float:
    if sys.platform == "darwin":
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return float(line.split()[1])
    return 0.0


N = 30


def bench_new_ctx(name: str, new_fn) -> None:
    r0 = rss_kb()
    boots, stamps = [], []
    for i in range(N):
        ctx = new_fn()
        t0 = time.perf_counter()
        ctx.eval(MINIDOM_JS)
        boots.append((time.perf_counter() - t0) * 1000)
        stamps.append(rss_kb())
        del ctx
    print(
        f"  boot median={statistics.median(boots):6.1f}ms "
        f"p10={sorted(boots)[N // 10]:5.1f} p90={sorted(boots)[9 * N // 10]:6.1f} "
        f"Δrss(norm)={statistics.median(stamps) - statistics.median(stamps[:2]):+.0f}KB"
    )


def qjs_new():
    return QJSContext()


def rust_new():
    return RustContext()


if __name__ == "__main__":
    n0 = rss_kb()
    print(f"RSS baseline={n0:.0f}KB  N={N}")
    print("[rust-v8]")
    bench_new_ctx("rust-v8", rust_new)
    print("[quickjs]")
    bench_new_ctx("quickjs", qjs_new)
    print(f"RSS total after both={rss_kb():.0f}KB")
