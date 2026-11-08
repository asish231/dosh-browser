# Audit Remediation, Deep-Fault Discovery & New Architecture Directions

**Date:** 2026-11-08
**Scope:** Rust product (`dosh-browser-core`), Rust engine (`doshgentv8/core`), shared JS/Python companion (`minidom.js`, `runtime.py`, `acd.py`, `content.py`, `datadome.py`).
**Method:** Section-2 plans + prior audit docs (`DOSH-CAPABILITY-REALITY-AUDIT.md`, `DOSH-BROWSER-REPO-RESEARCH.md`, `DOSH-SESSION-FINDINGS-EXACT.md`, `AGENTIC-VERIFY-CLASSIFY.md`, `SITE-SWEEP-LIMITATIONS.md`) re-read against the live tree, every gap fixed natively with hermetic tests. No Chromium/CDP anywhere; secrets stay memory-only.

---

## 1. Partially-implemented items completed (all with tests + benchmarks)

| Item | Prior state | Now |
|---|---|---|
| DCP raw-pointer lifetime defects (`Audit P0-04`) | `protocol.rs` returned `DoshResponse` pointing into dropped `String`/`Vec<u8>`; `Eval` result discarded; `BatchRead` dangled; `PollTelemetryRing` returned only a count | `dispatch_owned() -> DoshOwnedResponse` (owned, thread-safe); `dcp_scratch` engine-owned buffer backs every `DoshResponse`; opcode coverage whole (eval string, errors, batch bytes, telemetry JSON events). |
| MEASURED shell values (`Audit P0-05`) | `heap` returned hardcoded constants; `wasm` returned configured fuel + fake instances | `heap` reads the resident V8 actor (`get_heap_report`): used/total heap, envelope floor/ceil/granted, pressure epoch, external bytes, shrink fires, Parena RAM/spill/text stats, AB-pool stats. `wasm` reads live `fuel_remaining`. |
| Live actor config (`Audit P0-06`) | `clock`/`rng`/`gate` mutated session fields only | `sync_live_runtime_config()` applies/errors transactionally on the live actor; gates propagate immediately; `configure_runtime` reset PRNG properly. |
| Plaintext temp-script login (`Audit P0-07`) | Rust `login` wrote credentials to `$TMP/dosh-login/login.py` and invoked Python | `Session::login` / `Session::submit_otp` — native in-memory: wall detect → type → submit → verdict, OTP checkpoints (max 3 attempts, 600s TTL, never guess). Credentials never persisted nor echoed in results. |
| Unified `BrowserControl` registry (`Audit P1-01`) | `dispatch.rs` / `tools.rs` / `mcp.rs` divergent | All reach `shell::execute_command` / `Session::login` through a canonical `dispatch::invoke`; consistency assertions added. |
| ActionGateway (`State Weave §8` / `Audit P1-04`) | — | `act`/`action.start` support `expected_epoch` fencing and `idempotency_key` replay via `document_epoch` + `action_idempotency_cache`. |
| Causal `trace` (`Audit P1-08`) | Rust trace returned empty `network`; no `caused_by` | `trace` propagates `caused_by = act-<id>` into every network record and console entry via seam `active_cause`, computes identity-keyed DOM `added`/`removed`, bundles dialogs. |
| `net.rule` enforcement (`Audit P1-06`) | rules stored but never consulted during transport | `NetworkEngine::fetch` evaluates `match_rule` first: block (honest error + ledger record), mock (synchronous FetchResult + sealed evidence), redirect. |
| Resident-realm `layers`/`screenshot`/`restore` | fresh throwaway runtime lost live state | All now use the live resident runtime (document rehydrated on `restore`). |
| `frame.eval` (`Audit P1-07` / P2-02) | created fresh top-level runtime, ignored frame | `Session::ensure_frame_docs` + `get_or_create_frame_runtime` load real child-frame documents (incl. `srcdoc`) into isolated V8 actors; `frame.eval <frame> expr` runs inside the child realm. |
| External scripts / ESM loader (`Audit P2-01`) | `host_load_module` returned `{"url":"", "code":""}`; external `<script src>` never fetched | `modules.rs::translate_module` (import/export/import.meta → engine ESM loader) + NetworkEngine-backed `fetch_script_source` (rule mock/block/redirect; external network opt-in via `WR_FETCH_EXTERNAL_SCRIPTS`); injected `src` scripts fetch too. |
| NativeCrypto (`AGENTIC-VERIFY-CLASSIFY §1`) | `importKey/exportKey/encrypt/decrypt` resolved `{}`/empty silently; `sign/verify/deriveBits` absent | `crypto.subtle` implements HMAC (SHA-1/256/384/512), Ed25519, AES-128/256-GCM, PBKDF2, SHA digests over `host_crypto_subtle`/`host_crypto_digest` (Rust `ring`; Python `hashlib`/`hmac`), plus pure-JS SHA-256/HMAC fallbacks; `navigator.credentials` rejects honestly; unsupported algorithms raise `NotSupportedError`. |
| Shadow DOM `>>>` piercing (`SITE-SWEEP Tier 3`) | `cssSelect` ignored shadow roots | `>>>` combinator enters open shadow roots; `ShadowRoot.getElementById` added. |
| ACD v2 (`SITE-SWEEP Tier 2`) | references skipped as chrome; iframe content invisible; 4 JSON-LD types | `include_references` toggle (Wikipedia reference lists), child-frame `srcdoc`/FrameInfo blocks project, JSON-LD expanded to Recipe/Article-family/Event/JobPosting/LocalBusiness/BreadcrumbList (Rust + Python). |
| DataDome (`SITE-SWEEP Tier 1`) | `datadome.py` drove the disabled Chromium stub | Native protocol: `extract_dd_params` (cid/hsh/t/bv/fe + `challenge_url`), frame-realm slider drag via `ctx.eval`, honest `ok | needs_human | needs_new_ip` verdicts. |
| `find` / `page_query` / `canvas2digest` (`AGENTIC-VERIFY §3–§4`) | — | New shell commands: in-page find with block-id/snippet/action-ref, structured ACD+action query, non-pixel canvas digest (dims, drawn, non-blank ratio, dominant palette). |
| `wait` / `diff` / `checkpoint`/`restore` (`Audit P1-08/P0-09`) | instant check; URL-only diff; headers-as-cookies; no runtime rehydrate | `wait` polls the live signal through `__drainTimers`; `diff` compares action-surface delta; `checkpoint` stores the live cookie jar; `restore` rehydrates the live runtime DOM + cookies and bumps `document_epoch`. |
| Parena Future A (spill depth) | `text_arena` only | Blanked:** `attr_data` column spill tier (`spill_attr_tail`/`un_spill_attr`/stitched `attr_bytes`), `cold_watermark_from_dirty_log` liveness-aware watermark, `warm_checkpoint` with `spill_path`/`spilled_from`. |
| Engine telemetry & JS surface | — | `dosh.parenaNode(id)` / `dosh.parenaStats()` borrowed-view helpers (no V8 DOM object graph for reads). |

---

## 2. Deep faults found during the remediation pass

1. **Two different `Document` objects existed in `minidom.js` boot order** — `global.Document` was assigned inside the `__load` subscope while the outer `Document.prototype.title` getter was only reachable on the outer constructor; resolved pending after the pass and verified with `document.title` in an isolated child frame (`frame.eval` returns `WidgetFrame:777`).
2. **`document.title` returned `undefined` in child realms** (thrown away by `frame.eval`'s fresh-runtime bug in the earlier tree) — no longer reproducible: fixed by the resident frame runtime + real title getter.
3. **`fetch` from the synchronous script path deadlocked single-threaded host runtimes** (`can call blocking only when running on the multi-threaded runtime`) — `run_async_host_call_inner` now checks `RuntimeFlavor`, and rule-served mock script sources resolve synchronously without leaving the failing thread. External CDN script fetch is opt-in so a blocked reqwest driver task can never hang the page actor.
4. **`net.rule` was decorative** — rules were appended to `NetworkLedger` but `NetworkEngine` never consulted them; every audit-listed “rule engine” claim was false for real traffic. Now enforced at the top of `fetch`, with ledger+sealed evidence for mock and error records for block.
5. **`caused_by` was dropped at three network write-points** (cache hit, chunked real dispatch, rule mock) — `telemetry.rs` writes now all carry `req.caused_by`, making `trace` truly causal.

---

## 3. Verification

Rust suites (fresh, all green):

```bash
cargo test --manifest-path doshgentv8/Cargo.toml -p doshgentv8-core --lib -q      # 48 passed / 0 failed
cargo test --manifest-path doshgentv8/Cargo.toml -p doshgentv8-core --test sigtrap_regression -q  # 3 passed
cargo test --manifest-path dosh-browser-core/Cargo.toml --lib -q                  # 258 passed / 0 failed
```

Python slices (fresh, all green):

```bash
.venv/bin/python -m pytest tests/test_acd_coverage.py tests/test_stealth_cloaking.py tests/test_shell_spine.py tests/test_loom.py -q  # 80 passed
```

Benchmarks (release): `benchmarks/bench-audit-remediation-20261108.md`,
`doshgentv8/core/examples/remediation_bench.rs`, `dosh-browser-core/examples/control_plane_bench.rs`.
Highlights: warm snapshot boot **0.655 ms (14.3× cold)**, DCP set/get fuel **3 ns**, owned batch-read 64 nodes **4.9 µs**, Parena Future A RAM drop **94.5%**, idempotent replay **1.35 µs**, stale-epoch fence **0.24 µs**, causal trace bundle **4.57 ms**, crypto.subtle (RSA-none) **28.2 µs/op**, ACD v2 `references=true` recovers **200/200** Wikipedia citation blocks.

---

## 4. New architecture directions (proposal doc, not yet built)

The next planned inventions, in priority order:

1. **Skillion Manifold** — kernel + per-capabilit broker/verifier/audit ledger for JS/WASM/subprocess extensions (`DOSH-SKILLION-ARCHITECTURE.md`), starting with the WAASM `ArrayBuffer` marshalling fix.
2. **Site-as-SDK Compiler** — catalog endpoint↔schema pairs from `net.apis` traffic and compile typed callable tools (mode 3 in `AGENTIC-NATIVE-CAPABILITY-MAP.md`).
3. **Live DOM ↔ Parena mutation mirror** — advanced the initial-document projection into a real-time delta mirror so spilled/checkpointed state matches the lived DOM.
4. **GCRA+Session lease** — replace the global `Arc<Mutex<Session>>` with per-session-id isolation (`SessionRegistry`).