# doshgentv8 — our embedded V8 engine ("doshgentv8.1")

**Fork target:** Our own lightly-patched V8 runtime, built as a Python extension
module via PyO3 + rusty_v8, replacing QuickJS (`webresearch/engine/runtime.py`).

## Naming & branding (why the folder is `base/`, not `v8/`)

The vendored source tree lives at `base/` — the upstream base we patch. The inner
tree structure stays 100% upstream-identical (required for the patch series and
4-week rebases); only the outer folder name is ours. Everything user-facing carries
our brand: the Python package (`doshgentv8` 1.0.0), the Rust crate, the build
artifact (GN's `v8_monolith.a` ships from us as `libdosh.a`, linked via
`RUSTY_V8_ARCHIVE`), and the engine name reported by the shell (`doshgentv8.1`).

Pages never see any of this: the UA string, `navigator`, and all self-identification
are injected by `webresearch` and are ours. What V8 exposes to sites is its
Chrome-parity behavior — which is exactly why we chose it.

**The one non-negotiable:** `base/LICENSE`, `base/LICENSE.v8`, and `base/AUTHORS`
stay, and upstream copyright headers stay in patched files. BSD-3 requires it, and
this project's own rules (AGENTS.md) demand credit. Branding the product is ours;
claiming authorship of the engine is not.

**Phase-3 note:** V8's `DEPS` says build tooling assumes the checkout root is
`./v8/`. When we `gclient`-fetch deps, we craft `.gclient` with a solution named
`base`; if any hook complains, the fallback is a temp `v8` checkout + rsync into
`base/` — patches are a series, so nothing is lost either way.

## Architecture

```
┌─────────────────────┐
│  webresearch/engine │ ← product code, engine-neutral
│  runtime.py          │   calls Context.eval(), host callables, pump()
│  minidom.js          │   stays untouched — same DOM runs in both engines
└────────┬────────────┘
         │ WR_JS_ENGINE=doshgentv8 (Phase 2 wiring)
    ┌────▼──────────────┐
    │ DoshContext        │ ← quickjs.Context-compatible surface
    │  (doshgentv8/py)   │   thin PyO3 facade — the ONLY throwaway piece
    │  eval · add_callable│   (deleted at the pure-Rust product rewrite)
    │  execute_pending_job│
    │  set_time_limit ...│
    ├────────────────────┤
    │ DoshEngine          │ ← doshgentv8/core: PURE RUST, zero pyo3.
    │  HostSeam trait     │   the future Rust product implements this
    │  (string-in/out)    │   trait natively and consumes the crate
    │  microtask pump     │   UNCHANGED (docs/DOSHGENTV8-MASTER-PLAN.md §Phase 5)
    │  wall-clock watchdog│
    ├────────────────────┤
    │ v8 crate 152.2      │ ← prebuilt static V8 today → RUSTY_V8_ARCHIVE
    │ (rusty_v8)          │   = our patched libdosh.a (Phase 3+)
    └────────────────────┘
    │ doshgentv8/base/        ← vendored upstream V8 15.9.0 (the tree we patch)
    └── src/wasm/baseline/     ← fuel metering patch (TBD)
        src/date/              ← clock injection
        src/wasm/wasm-js.cc    ← capability gate
```

Build + acceptance (verified 2026-11-03):

```bash
cd doshgentv8
PYO3_PYTHON=../.venv/bin/python cargo build --release
cp target/release/libdoshgentv8_rust.dylib \
   ../.venv/lib/python3.14/site-packages/doshgentv8_rust.cpython-314-darwin.so
../.venv/bin/python test_rust_bridge.py
```

Acceptance evidence (`test_rust_bridge.py`, REAL synchronous Python host
callables — the product's own parser + hashlib, not JS stubs): all probes
identical to the QuickJS oracle (boot/document/TypedArray/console/
event-loop/DOM/storage); sync host round-trip 9µs/call; wall-clock kill
0.51s; host errors catchable in JS. Two documented expected divergences:
V8's native WebAssembly, and the Proxy-wrapped TypedArray throwing honestly
instead of faking success. Boot: rust-v8 10.4ms vs quickjs 13.9ms.

## The five agentic patches (the fork's value)

These live as config-time toggles ON by default in "agent mode":

| Patch | What it does | Why agents benefit |
|---|---|---|
| **Fuel metering** | Per-basic-block instruction counter → trap on exhaustion | Bounded-quota guarantee for agent sandboxing |
| **Host-clock injection** | `Date.now()`/`performance.now()` tunneled to host clock | Replay reproduces wall-clock dependence honestly |
| **Seeded RNG** | `Math.random()` deterministic in replay mode, secrets-backed in prod | Replay-hybrid: seeded vs production modes |
| **Capability gate** | Parse custom sections (`WasmCompiledModule::GetWireBytesRef()`) | `waasm.*` host imports enforced per-module |
| **Checkpoint callback** | `AddMicrotasksCompletedCallback` + ArrayBuffer backing store | Save/restores state at module boundaries |

All five have one property: **they're policy overlays, never execution changes.**
An overlay flag OFF = identical to reference engine. That means they can run
side-by-side with Cloudflare/Turnstile challenge modules without conflict.

## Branch strategy

1. **Branch A (now):** `rusty_v8` prebuilt static `.a` → Python integration seam
   - Milestone: `from doshgentv8 import DoshContext; c = DoshContext(); c.eval(MINIDOM_JS)` works
   - Then port 13 host-callable seam → parity-gated by existing test suite
2. **Branch B (later):** Fork V8 source → apply 5 patches → build new static lib
   - Vehicle: copy `research_repos/v8/` → apply edits → `gn gen out/dosh` → `ninja`
   - Release drop-in: set `RUSTY_V8_ARCHIVE` env var to point at new `.a`

## Build dependencies (Phase 0 still builds without these)

For the current mini-racer bridge: just Python 3.12+ and `mini-racer`.
For the real Rust build:
- Rust stable (via rustup)
- maturin ≥1.8
- GNU Make / Xcode Command Line Tools
- Python headers (already present on macOS)
- Optional: depot_tools if switching to native V8 compile

## Product boundary

Per AGENTS.md: `webresearch/`, `tests/`, server, CLI **never import
any V8 binary**. This directory is ours — the product default engine stays
QuickJS. Opt-in `WR_JS_ENGINE=doshgentv8` only.
