# Audit Remediation & Partial-Implementation Completion Benchmark (2026-11-08)

**Scope:** Empirical verification and performance benchmarking of all items completed across the Section-2 Partially Implemented / Audit Backlog pass:
1. `doshgentv8-core`: DCP scratch-backed pointer safety & owned responses (`Audit P0-04`), Borrowed-View JS helpers (`dosh.parenaNode`, `dosh.parenaStats` — `State Weave Phase D`), and Parena Future A (`attr_data` column spill tier, `dirty_log` cold watermark, and `warm_checkpoint` with `spill_path`).
2. `dosh-browser-core`: Live actor `heap`/`wasm` measurements (`Audit P0-05`), transactional live-actor config (`clock`/`rng`/`gate` — `Audit P0-06`), native memory-only `login` & `submit_otp` (`Audit P0-07`), unified `BrowserControl` dispatch (`Audit P1-01`), `ActionGateway` epoch fencing + `idempotency_key` replay (`State Weave §8` / `Audit P1-04`), `NetworkEngine` rule evaluation (`net.rule` mock/block/redirect — `Audit P1-06`), resident-runtime `layers`/`screenshot`/`restore`/`frame.eval` (`Audit P1-07`), causal `trace` with `caused_by` propagation (`Audit P1-08`), external script & ES module loader (`Audit P2-01`), and `find` / `page_query` / `canvas2digest` (`AGENTIC-VERIFY-CLASSIFY §3–§4`).
3. Shared `minidom.js` & Python companion: NativeCrypto suite (`crypto.subtle` HMAC, Ed25519, AES-GCM, PBKDF2, SHA-1/256/384/512 + error-honest `NotSupportedError` rejections), Shadow DOM `>>>` piercing, `Document.prototype.title` live getter/setter, ACD v2 (`include_references`, child-frame content blocks, expanded JSON-LD schemas), and native DataDome protocol extraction & solver (`datadome.py`).

## Repro Commands

```bash
cargo run --manifest-path doshgentv8/Cargo.toml -p doshgentv8-core --release --example remediation_bench
cargo run --manifest-path dosh-browser-core/Cargo.toml --release --example control_plane_bench
cargo test --manifest-path doshgentv8/Cargo.toml -p doshgentv8-core --lib -q
cargo test --manifest-path dosh-browser-core/Cargo.toml --lib -q
.venv/bin/python -m pytest tests/test_acd_coverage.py -q
```

---

## 1. Engine & State Weave Benchmarks (`remediation_bench.rs`, `--release`)

| Benchmark | Metric | Measured Value | Target / Baseline | Verdict |
|---|---|---|---|---|
| Cold V8 Isolate Boot (`new_with_limit`) | Wall time | **9.362 ms** | 10.4 ms baseline | ✅ |
| Snapshot Blob Creation (`create_dosh_snapshot`) | Wall time / Size | **12.217 ms** / **373.0 KB** | One-time build cost | ✅ |
| Warm V8 Isolate Boot (`new_with_snapshot`) | Wall time | **0.655 ms** (**14.30× faster**) | ≤ 2.0 ms KPI (`DOSH-STATE-WEAVE-PLAN §5`) | ✅ **3× faster than KPI** |
| DCP `SetFuel` / `GetFuel` (`dispatch_command`) | Latency / op | **3.0 ns** | < 50 ns | ✅ |
| DCP Owned `BatchRead` (64 DOM nodes, 6,148 B) | Latency / batch | **4.92 µs** | Zero dangling pointers (`P0-04`) | ✅ |
| Borrowed-View JS (`dosh.parenaNode(id)`) | Latency / call | **2.48 µs** | Zero V8 DOM graph allocation (`Phase D`) | ✅ |
| Parena Future A: Text + Attr Spill (`10,000` nodes) | Resident RAM | **24,929 KB → 1,377 KB** (**94.5% reduction**) | 14,337 KB offloaded to disk | ✅ |
| Parena `warm_checkpoint(512 KiB)` | Wall time | **2.88 ms** | `spill_path` preserved in `Checkpoint` | ✅ |
| Stitched Read Across Hot/Spill Text + Attr | Latency / node | **3.82 µs** | Byte-identical across tier boundary | ✅ |

---

## 2. Browser Control Plane, Crypto, Shadow DOM & ACD v2 (`control_plane_bench.rs`, `--release`)

| Subsystem / Operation | Measured Value | Details |
|---|---|---|
| Full SPA Load + V8 Hydration + Parena + ACD 2.0 | **37.60 ms** | Includes Canvas 2D draw, `<iframe srcdoc>`, and action surface extraction |
| Live `act` (click → JS handler → `fetch` → DOM serialize) | **8.47 ms** | Increments `document_epoch` (`1 → 2`), records in idempotency cache |
| `ActionGateway` Idempotent Replay (`idempotency_key` hit) | **1.35 µs** | **6,274× faster** than re-execution; prevents duplicate form/payment submits |
| `ActionGateway` Stale Epoch Fence (`expected_epoch` mismatch) | **0.24 µs** (240 ns) | Refuses stale agent actions before touching the V8 actor |
| Causal `trace` Bundle (`action → ΔDOM → net → console`) | **4.57 ms** | Propagates `caused_by = act-<id>` to `NetworkLedger` and `console_logs` |
| Live `heap` Inspection (Resident V8 Actor) | **34.4 µs** | Reports real `used_heap_bytes` (2,144 KB), envelope band, pressure epoch, Parena & AB pool |
| `canvas2digest` (64×64 Canvas 2D Palette & Geometry) | **2.83 ms** | Extracts `non_blank_ratio` and dominant hex color (`#00aa88`) without vision tokens |
| `frame.eval` in Isolated Child Realm (`frame-1`) | **15.81 ms** cold / **27.0 µs** warm | Evaluates inside child `<iframe>` V8 actor (`WidgetFrame:777`) |
| Shadow DOM Piercing (`#host >>> #inner-target`) | **2.42 µs / query** | Traverses open `ShadowRoot` boundaries deterministically |
| NativeCrypto (`crypto.subtle` HMAC-SHA256 + AES-256-GCM) | **28.18 µs / op** | 100 sign + encrypt + decrypt roundtrips (300 ops) in **8.46 ms** via `ring` |
| ACD v2 Wikipedia Fixture (`include_references=false`) | **1.63 ms** (44 blocks) | Includes `<iframe srcdoc>` block + JSON-LD `Article` blocks |
| ACD v2 Wikipedia Fixture (`include_references=true`) | **4.25 ms** (244 blocks) | Recovers all **200** `ol.mw-references` citation blocks on demand |

---

## 3. Suite Pass Counts

- `doshgentv8-core --lib`: **48 passed, 0 failed** (0.20s)
- `doshgentv8-core --test sigtrap_regression`: **3 passed, 0 failed** (0.16s)
- `dosh-browser-core --lib`: **258 passed, 0 failed** (0.91s)
- `tests/test_acd_coverage.py` + `test_stealth_cloaking.py` + `test_shell_spine.py` + `test_loom.py`: **80 passed, 0 failed**
