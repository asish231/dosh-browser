# doshgentv8.1 — Master Plan

One file, the whole plan: what doshgentv8.1 is, the exact build sequence, and how the
agentic inventions attach to it. Status markers are verified against the tree as of
2026-11-03. Companion docs: `docs/AGENTIC-VERIFY-CLASSIFY.md` (verified audit),
`docs/AGENTIC-NATIVE-CAPABILITY-MAP.md` (modes + inventions),
`v8_lane/RESULTS.md` (smoke evidence), `doshgentv8/README.md` (package layout).

---

## 1. What doshgentv8.1 is (one sentence each)

- **doshgentv8.1** = V8 15.9.0 (vendored at `doshgentv8/base/`) + five agentic patches +
  our build recipe → a static library.
- **doshgentv8 the package** = that library linked through rusty_v8 + PyO3 →
  `from doshgentv8 import DoshContext`, a drop-in for `quickjs.Context`.
- **The product** (`webresearch/`) stays engine-neutral: same `minidom.js` (9,494 lines,
  unmodified), same 13 host callables, selected via `WR_JS_ENGINE=quickjs|doshgentv8`.

The five patches (all **policy overlays** — flag OFF = byte-identical to stock V8, so
vendor wasm like Cloudflare/Turnstile always runs in spec mode):

| # | Patch | Where it goes (15.9.0 tree, verified paths) |
|---|---|---|
| 1 | **Fuel metering** — per-basic-block counter, trap at budget | `v8/src/wasm/baseline/` (Liftoff), `v8/src/wasm/module-compiler.cc`, `v8/src/wasm/turboshaft-graph-interface.cc` |
| 2 | **Host-clock injection** — `Date.now()`/`performance.now()` from host | `v8/src/date/` runtime entries |
| 3 | **Seeded RNG** — deterministic `Math.random()` in replay mode | `v8/src/base/` random plumbing |
| 4 | **Capability gate** — custom-section manifest → allowed host imports | parse `WasmCompiledModule::GetWireBytesRef()` wire bytes (~40 lines, embedder side; the internal `GetCustomSections` at `v8.h:3358` is not public API) |
| 5 | **Checkpoint callback** — microtask-boundary state snapshot hook | `v8/include/v8-isolate.h:1435-1478` (microtask API block, stock V8) |

## 2. Build sequence

### Phase 0 — DONE ✅ (verified this session)
- [x] V8 smoke lane (`v8_lane/smoke.py`): minidom.js boots in real V8, 7/8 probes
      identical to QuickJS oracle, native WASM confirmed, TypedArray-Proxy flagged
      unnecessary under V8 (`v8_lane/RESULTS.md`)
- [x] `doshgentv8/` scaffold: `__init__.py` (mini-racer bridge, integration test green,
      minidom boots 10.8ms), `src/lib.rs` + `Cargo.toml` + `pyproject.toml` (rusty_v8+PyO3)
- [x] V8 15.9.0 source vendored at `doshgentv8/base/` (208MB, .git stripped)
- [x] AGENTS.md engine row documents the dual-engine contract

### Phase 1 — The bridge in Rust — DONE ✅ (2026-11-03, evidence below)
Goal achieved: `DoshContext` = rusty_v8 through PyO3, with REAL synchronous host
callables (mini-racer's async-only `wrap_py_function` dead-end bypassed).
Built as the **core/facade split** so the pure-Rust product rewrite consumes
the engine crate unchanged (this was the deciding design move):
- `doshgentv8/core` — pure Rust (`doshgentv8-core`), ZERO pyo3: `HostSeam` trait
  (string-in/string-out, the QuickJS marshalling contract), `DoshEngine` on
  v8 152.2.0 prebuilt, single-`FunctionTemplate` dispatcher + JS wrappers
  (no template-data API needed), thread-local bridge pointer, explicit
  microtasks + `pump_jobs` checkpoint, per-eval wall-clock watchdog via
  `IsolateHandle::terminate_execution`.
- `doshgentv8/py` — thin PyO3 facade (`doshgentv8-py`, cdylib `doshgentv8_rust`,
  pyo3 0.30.1): `eval`/`add_callable`/`execute_pending_job`/`set_memory_limit`/
  `set_time_limit`/`set_max_stack_size` — the quickjs.Context surface. The only
  throwaway piece of the engine.
Build: `cargo build --release` (needs `PYO3_PYTHON=.venv/bin/python`; macOS link
needs `-Wl,-undefined,dynamic_lookup` — in `doshgentv8/.cargo/config.toml`), then
copy `target/release/libdoshgentv8_rust.dylib` →
`.venv/lib/python3.14/site-packages/doshgentv8_rust.cpython-314-darwin.so`.
Acceptance (`doshgentv8/test_rust_bridge.py`, REAL host callables — product parser
+ hashlib — not JS stubs): all probes identical to the QuickJS oracle
(boot/document/TypedArray/console/event-loop/DOM/storage), two documented
expected divergences (native WebAssembly; Proxy-wrapped TypedArray throws
honestly under V8). Sync host round-trip **9µs/call**; wall-clock kill 0.51s;
host errors catchable in JS (better than QuickJS's deferred SystemError).
Boot: rust-v8 **10.4ms** vs quickjs 13.9ms. `__init__.py` selector prefers the
Rust bridge; `test_integration.py` green through it.

### Phase 2 — Full seam parity (the next real work)
Port all 13 host callables with exact QuickJS behavioral parity
(verified inventory: `runtime.py:322-813` — `host_parse_html:322`, `host_log:326`,
`host_ws_*:350-353`, `host_post_message:374-413`, `host_get/set_cookie:415-431`,
`host_crypto_digest:433-452`, `host_load_module:461-510`, `host_fetch:586-813`).
Plus:
- Timer/clock contract: `__advanceTimerClock` bursts (`runtime.py` 4× pump passes) and
  microtask drain via `PerformMicrotaskCheckpoint` — the biggest behavioral seam.
- Limits: replace `shell.py:397-400` sandbox limits with
  `CreateParams::heap_limits` + wall-clock `RequestInterrupt`.
- ESM: gate the QuickJS-gap patches in `engine/modules.py` (`_fix_class_static_selfref`,
  `_fix_private_in_checks`) OFF for V8 (native support), keep page-bundle fixes.
- TypedArray Proxy (`minidom.js:5-39`): skip under V8 (smoke probe B/H evidence) —
  engine-conditional, not deletion.
- `WR_JS_ENGINE=doshgentv8` env switch in `runtime.py` + `shell.py`.
- **Gate:** the existing test suite passes on BOTH engines; parity matrix written.

### Phase 3 — First V8 build (fork begins)
1. Install depot_tools; `fetch` the 15.9.0 deps into `doshgentv8/base/` (DEPS file is present).
2. Stock build first: `gn gen out/dosh --args='...'` → `ninja v8_monolith` — an
   unpatched static lib, built by us, as the fork baseline.
3. Wire that `.a` via `RUSTY_V8_ARCHIVE` → Rust bridge → re-run smoke + parity suite.
   (Binding-generation coupling means this may need a rusty_v8 version bump or vendored
   binding gen — decide when the archive lands.)
- **Gate:** our-built stock V8 passes the same parity suite the prebuilt did.

### Phase 4 — The five patches (the actual fork)
One patch per PR-sized unit, each with a hermetic test, each flag-gated OFF by default:
1. Patch 1 fuel (wasm Liftoff decrement+branch; trap handler precedent at
   `include/v8-wasm-trap-handler-posix.h`).
2. Patch 2 host-clock. 3. Patch 3 seeded RNG (replay mode only).
4. Patch 4 capability custom-sections parse (embedder-side, no core edit).
5. Patch 5 checkpoint callback.
- **Gate:** each patch green alone + spec-mode byte-identity test (overlays OFF =
   identical output to stock on the conformance corpus).

### Phase 5 — Agentic inventions on top (the product work, parallel to 3–4) — includes the pure-Rust product rewrite
When the product rewrites to Rust, it consumes `doshgentv8-core` directly as a
crate dependency (implementing `HostSeam` natively — no bridge at all); only the
`doshgentv8/py` facade is deleted. Port product modules incrementally against
the Python side as the test oracle: `fetch` → `tree/parse` → `runtime seam` →
`session` → `shell/tools` → `server`.
Ordered from the verified audit (P0 first — see `AGENTIC-VERIFY-CLASSIFY.md` §7):
1. **Honest crypto stubs** (one-line fixes; kills silent false-success) → then
   **NativeCrypto suite** as host callables (AES-GCM/ChaCha20, HMAC, sign/verify
   ECDSA/Ed25519/RSA-PSS, X25519/HKDF, key vault per origin).
2. **Transcript lane** + audio-only + `video sweep` audit table.
3. **Research mode**: `plan/checkpoint/resume/track`.
4. **`browser_find`/`find_next` + `page_query`.**
5. **Replay tape + step API + cost ledger** (mode-derived inventions, map §5.6) —
   these consume the fork's fuel/clock/RNG directly.

### Phase 6 — Parity & disclosure survey (the finish line, already defined)
The 6-pass survey (DOM byte-diff per fixture, capability/disclosure table, full regression
both engines, behavioral probes, fingerprint honesty, perf table) → results into
`OPEN_ISSUES.md` + README. Nothing ships silently.

## 3. Cross-arch builds (the "all CPU types" answer)

| Target | Plan | Notes |
|---|---|---|
| macOS arm64 (dev) | rusty_v8 prebuilt (dev), our GN build (fork) | smoke already green here |
| Linux x86_64 | rusty_v8 prebuilt `x86_64-unknown-linux-gnu` asset (verified: release ships it) → our GN build per-target | serverless/container lane |
| Linux aarch64 | rusty_v8 prebuilt asset (verified) → GN build | ARM servers / Graviton |
| x86 Windows / others | GN build on demand; not Phase 1–4 priority | honest: untested |

GN build flags per arch (pointer compression ON to match Chrome, sandbox per target,
`v8_enable_i18n_support` minimal-ICU keep). CI matrix: build + parity suite on the two
Linux targets + macOS arm64 from Phase 3 onward.

## 4. Rule-of-engagement with AGENTS.md

- `webresearch/`, `tests/`, server, CLI **never import V8 binaries**; default engine is
  QuickJS. AGENTS.md already documents the dual-engine contract (updated this session).
- The fork tree is ours (BSD-3 retained, AUTHORS/LICENSE kept in place at
  `doshgentv8/base/LICENSE*`); patches tracked as a series; upstream credit preserved.
- Every patch is disclosed behavior, never a stealth lane. Vendor wasm = spec mode always.
