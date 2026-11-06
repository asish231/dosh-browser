# DOSH Full-Access Remediation Plan

**Goal:** give the DoshShell terminal — and therefore any agent driving the
dosh browser — *full, deep, inspectable access* to every browser subsystem, plus
the ability to **manipulate** each one (not just read it). Close every remaining
honestly-disclosed capability gap so the agent can see and drive live requests,
streams, storage, cookies, CORS, the DOM, layout, media, workers, and challenge
surfaces.

This document is the single source of truth for the remaining work. It lists
**what is already done**, **everything still missing** (grounded in the code and
`AGENTS.md`), and a **phased plan** with concrete commands, files, and tests.

> Ground rules carried from `AGENTS.md`: build natively, no Chromium/CDP, no
> shortcuts, no faked behavior. Every new capability ships with a hermetic test
> from real captured markup, and secrets stay redacted unless an explicit,
> audited authorization flag is set.

---

## 0. Status legend

- ✅ **DONE** — implemented and tested this session or earlier.
- 🟡 **PARTIAL** — exists but incomplete / read-only / not wired to the shell.
- ❌ **MISSING** — not implemented.
- 🚫 **OUT OF SCOPE** — intentionally never built (kept here for completeness).

Each planned item names the **shell command(s)** the agent will use, the
**files** to change, and the **test** that proves it.

---

## 1. Already done (this session)

| Capability | Shell surface | Files | Tests |
|---|---|---|---|
| ✅ Resident-runtime `eval` (JS state persists across turns) | `eval`, `skill.run` | `shell.rs`, `session.rs` | `shell::test_eval_state_persists_across_turns` |
| ✅ Live cookie jar read (not stale headers) | `cookies` | `shell.rs`, `session.rs` | `shell::test_cookies_reads_live_jar` |
| ✅ localStorage + sessionStorage reporting | `storage` | `shell.rs` | covered by assist tests |
| ✅ Session registry (isolated, keyed sessions) | REST `/v1/sessions` | `registry.rs`, `server.rs` | `registry::*` (5) |
| ✅ SSH-like persistent attach bridge | WS `/v1/attach` | `server.rs` | `server::test_bridge_*` (2) |
| ✅ In-session DevTools walkthrough + unified cURL synthesis | `assist`, MCP `browser_assist`, CLI `dosh assist` | `assist.rs`, `mcp.rs`, `bin/dosh.rs` | `assist::*`, `shell::test_assist_*`, `mcp::test_mcp_assist_*` |
| ✅ Per-session capability gate independence | `gate` | `shell.rs`, `session.rs` | `shell::test_gate_is_per_session` |
| ✅ Python lane parity for `assist` | `assist` | `webresearch/shell.py` | `tests/test_shell.py::test_assist_*` |

Baseline after this session: **Rust 213 tests, engine 29, Python 928** — all green.

---

## 2. Everything still missing / partial (the work)

Grouped by subsystem. Network group first since it is the user's priority.

### 2.1 Network — live requests, streams, formats, manipulation

Status as of 2026-11-06: **Phase 1 complete** — N1–N9 all implemented and
tested in both lanes (see §9 execution log). The "State" column now records the
shipped behavior, not the gap.

| # | Capability | State | Why it matters |
|---|---|---|---|
| N1 | **SSE / `text/event-stream` capture** | ✅ ordered `stream_events` + `net.stream` drain | Chat APIs stream tokens; chunks now arrive in order with arrival stamps. |
| N2 | **WebSocket frame capture (text + binary)** | ✅ `ws.rs` RFC 6455 client; text+binary (b64) frames, direction+opcode, `net.ws` | Realtime apps visible; Python mirror records both directions. |
| N3 | **CORS verdict inspection** | ✅ `cors_verdict` + `net.cors` (allowed/blocked + why) | The verdict the engine computed is now inspectable per request. |
| N4 | **Per-request timing / waterfall** | ✅ ttfb/duration via chunk streaming; DNS/connect disclosed as unmeasured | `net.timing` reports the real measured phases only. |
| N5 | **Request replay from the ledger** | ✅ sealed in-memory evidence + `net.replay` behind the reveal gate | The exact recorded request contract is replayable; secrets gated + audited. |
| N6 | **Binary/protobuf/msgpack body decode** | ✅ gzip/zlib/deflate/brotli + structural protobuf wire decoder, `net.decode` | Challenge bodies are inspectable, schema-free, depth-capped. |
| N7 | **Guaranteed full body capture** | ✅ parameterized `max_bytes` cap + honest `truncated` flag | Caps are operator-set; truncation is reported, never silent. |
| N8 | **Authorized secret reveal** | ✅ `gate secret_reveal` + `cookies values=raw` + `RevealAudit` log | Redaction stays default; the escape hatch is explicit and audited. |
| N9 | **Initiator call stack** | ✅ `host_fetch` stack hint (arg 6) + sanitized `initiator_stack` | The JS call site that fired a fetch is recorded (query strings stripped). |

### 2.2 JS engine

| # | Capability | State |
|---|---|---|
| J1 | Native WebAssembly | ❌ (documented divergence) |
| J2 | Proxy-wrapped TypedArray | ❌ (throws honestly) |
| J3 | WeakRef / FinalizationRegistry real GC semantics | 🟡 strong-retention shim, no finalizers |
| J4 | Full Intl/ICU (non-en locales) | 🟡 en-US subset only |
| J5 | Fork patches fully wired (fuel, clock, seeded RNG, checkpoint) | 🟡 Phase-2 pending |

### 2.3 Layout / geometry / text

| # | Capability | State |
|---|---|---|
| L1 | CSS floats | ✅ |
| L2 | `position: sticky` | ✅ |
| L3 | flex-wrap + justify/align distribution | ✅ |
| L4 | Bidi + text shaping | ✅ (direction:rtl reorder; no Unicode bidi/shaping) |
| L5 | Real font metrics (shaping-lite) | ✅ proportional advance + kern pairs |
| L6 | Smooth scroll animation | ✅ rAF frame-clock ease-out |
| L7 | Caret + text selection model | ✅ |
| L8 | Real rAF frame clock | ✅ (rAF queue + drain batch) |
| L9 | CSS transitions / @keyframes timing | ✅ |
| L10 | Occlusion-gated clicks | ✅ (inspector + gate + honest refusal) |

### 2.4 Rendering / pixels

| # | Capability | State |
|---|---|---|
| R1 | Canvas 2D raster | 🟡 no-op stub + seeded noise |
| R2 | WebGL raster | 🟡 fingerprint stub |
| R3 | Pixel-faithful screenshot | 🟡 approximate SVG only |
| R4 | Paint stack / top-layer / `::backdrop` | ❌ |

### 2.5 Events / input

| # | Capability | State |
|---|---|---|
| E1 | Blocking alert/confirm/prompt | 🟡 record + default, re-run model |
| E2 | Text-selection drag lane | ❌ |
| E3 | Mid-flight `fetch` abort | 🟡 pre-abort only |
| E4 | Full Popover API semantics | 🟡 subset |
| E5 | Trusted CDP input | 🚫 out of scope |

### 2.6 Storage / state

| # | Capability | State |
|---|---|---|
| S1 | Unified SQL state DB (history+cookies+storage+ledger, snapshot/diff/rollback) | ✅ `state_snapshots` + `state.snapshot/diff/rollback` both lanes (ledger append-only on rollback) |
| S2 | Element identity tracker (stable refs across re-renders) | ✅ `identity.rs` (Rust) + content-identity inventory (Python); `ref` re-resolves across re-renders |
| S3 | Mutation subscription stream (deltas, not re-reads) | ✅ `watch` = host-side MutationObserver drain (`__wrWatch*`), after/limit/next_after, both lanes |
| S4 | Same-origin frame storage sharing | 🟡 unseeded, not shared |
| S5 | Cross-tab/cross-origin storage events | 🟡 same-origin-other-tab only |
| S6 | Cookie/site-data partitioning (3rd-party isolation) | ❌ |
| S7 | Deterministic replay engine (ledger+snapshots+seeds) | ❌ |
| S8 | bfcache live-heap restore | 🟡 rebuild + re-run |
| S9 | Capability manifest per page (formalized) | ✅ `caps` both lanes: live-realm counts (static fallback labeled), tabs/frames/dialogs, unavailable+why, absolute-path export |
| S10 | IndexedDB full semantics | 🟡 per-pass drain partial |

### 2.7 Workers / threads / WASM substrate

| # | Capability | State |
|---|---|---|
| W1 | Web Workers / SharedWorker real execution | ❌ inert constructors |
| W2 | Service Worker real execution + interception | 🟡 simulated |
| W3 | Scoped WASM float/extended opcodes | 🟡 integer subset; floats trap |
| W4 | MessagePort across realms | 🟡 inert in receiving realm |
| W5 | structuredClone of class instances | 🟡 subset codec |

### 2.8 Media / video / audio

| # | Capability | State |
|---|---|---|
| M1 | Web Audio processing | 🟡 presence stub |
| M2 | MediaStream / capture | 🟡 presence stub |
| M3 | DRM video | 🚫 refused by policy |
| M4 | Piracy mirrors | 🚫 refused by policy |
| M5 | HLS/DASH extraction without external yt-dlp | 🟡 needs yt-dlp |
| M6 | Malware verdict on downloads | ❌ sha256 only |

### 2.9 Security / adversarial

| # | Capability | State |
|---|---|---|
| A1 | In-VM obfuscated widget channels (botguard MessagePort) | 🟡 HTTP protocol lane instead |
| A2 | reCAPTCHA challenge-type coverage | 🟡 vision tiles, can reject-loop |
| A3 | Non-http(s) frame schemes | 🚫 rejected |
| A4 | `data:` module imports | 🚫 rejected |
| A5 | Login/OTP beyond honest refusals | 🟡 capped, never guesses |

### 2.10 Explicit never-build (kept for completeness)

🚫 Any real browser engine (Chromium/Playwright/Puppeteer/Selenium/CDP) ·
🚫 OS/POSIX shell · 🚫 Full general WASM engine · 🚫 Real OS worker threads.
These stay out of scope; the plan below works *around* them with native emulation.

---

## 3. Phased implementation plan

Ordered by **agent value × independence**. Each phase compiles and tests green
before the next. Every command is added to both the Rust lane (primary) and the
Python companion, mirrored.

### Phase 1 — Live network depth (the priority) `[N1–N9]`

**Outcome:** the agent sees and manipulates *everything* on the wire, including streams.

1. **SSE/event-stream capture (N1).** Teach `NetworkLedger` + the fetch
   transport to recognize `text/event-stream` and accumulate chunks into an
   ordered `stream_events: Vec<{t, data}>` on the `NetworkRecord`. New shell
   command `net.stream id=<n> after=<k>` drains new chunks incrementally.
   - Files: `telemetry.rs`, `network.rs`/`fetch.rs`, `shell.rs`; Python
     `telemetry.py`, `fetch.py`, `shell.py`.
   - Test: mock an SSE endpoint, assert chunks arrive in order via `net.stream`.
2. **WebSocket frame capture (N2).** Record text **and** binary frames
   (base64 for binary) with direction + opcode on a `ws_frames` buffer.
   New `net.ws id=<n>` command.
   - Files: `engine/ws.py`, Rust WS client (new, small), `telemetry.*`, `shell.*`.
   - Test: echo WS server fixture, assert both directions + binary captured.
3. **CORS verdict (N3).** `cors.rs` already computes the decision; surface it.
   New `net.cors pattern=` command reporting `{allowed, reason, allow_origin,
   allow_credentials, method_ok, preflight}` per request.
   - Test: cross-origin fixture with/without correct headers → verdict matches.
4. **Timing/waterfall (N4).** Add `timing: {queued, dns, connect, ttfb, end}`
   to `NetworkRecord` from the transport. Surface via `net` and `net.timing`.
5. **Request replay (N5).** `net.replay id=<n>` re-issues a recorded request
   with its real method/headers/body (authorized), returns the fresh response.
6. **Body decoders (N6).** Add a decode step: gzip/deflate/br inflate, and a
   protobuf wire-format *structural* decoder (field numbers + wire types, not
   schema) so challenge bodies become inspectable. `net.decode id=<n>`.
7. **Full-body + caps (N7).** `net.capture enabled=true max_bytes=<n>` already
   toggles; raise/parameterize the cap and record truncation honestly.
8. **Authorized secret reveal (N8).** New audited flag
   `cookies values=raw --authorize` / `net ... reveal=true` guarded by a
   session-level `allow_secret_reveal` gate (default off, logged when used).
   Keeps the redaction-by-default rule; adds an explicit, audited escape hatch.
9. **Initiator stack (N9).** Capture the JS call site when a fetch/XHR is fired
   from page script (wrap the host fetch bridge to record a stack hint).

### Phase 2 — Live state manipulation `[S1–S3, S9]`

**Outcome:** the agent can snapshot, diff, roll back, and subscribe to changes.

1. **Unified SQL state DB (S1).** One SQLite with history+cookies+storage+ledger
   and `state.snapshot` / `state.diff` / `state.rollback` commands.
2. **Element identity tracker (S2).** Content-addressed stable refs that survive
   SPA re-renders; wire into `actions`/`ref`. Files: `identity.rs`/`identity.py`.
3. **Mutation subscription stream (S3).** Promote the existing `watch` into a
   durable delta stream the agent drains instead of re-reading. `engine/delta.py`.
4. **Capability manifest (S9).** Formalize `caps` into a per-page
   "available / unavailable + why" manifest the agent can query before planning.

### Phase 3 — Interaction fidelity `[L7, L8, L10, E2, E3, E4, S4, S5]`

**Outcome:** clicks, typing, scrolling, and storage behave like a real browser.

1. **Occlusion-gated clicks (L10).** Consult `__inspectLayers` before a click;
   refuse/annotate clicks on occluded targets.
2. **Caret + selection (L7).** Minimal caret offset + Selection/Range so editors
   and selection-drag work.
3. **Real rAF clock (L8).** Replace the `setTimeout(16)` shim with a driven frame
   clock tied to the virtual clock, so animation callbacks step deterministically.
4. **Selection drag (E2), fetch abort (E3), full Popover (E4).** Close the input
   subset gaps.
5. **Frame storage sharing (S4) + storage events (S5).** Share same-origin
   storage across frames; broaden storage-event delivery.

### Phase 4 — Layout/geometry completeness `[L1–L6, L9]`

Implement in the order `AGENTS.md` prescribes: **sticky → flex-wrap → floats →
bidi**, then text-metrics upgrade, smooth-scroll animation, and the CSS
transition/keyframe timing engine. Each lands with a captured-markup regression.

### Phase 5 — Workers / WASM substrate `[W1–W4]`

1. **Single-threaded Worker emulation (W1).** Run worker scripts in a child realm
   with a real message queue pumped between turns (no OS threads, per scope).
2. **Service Worker execution (W2).** Actually run SW JS in a realm and route
   intercepted fetches through it.
3. **WASM float + extended opcodes (W3).** Extend `wasm.py` beyond the integer
   subset as real challenge modules demand; keep fuel metering.
4. **Cross-realm MessagePort (W4).** Make transferred ports route in the receiver.

### Phase 6 — Rendering & media depth `[R1–R4, M1, M2, M6]`

1. **Canvas 2D software raster (R1)** + **pixel-accurate screenshot (R3).**
   A real (small) software rasterizer so `screenshot` is pixel-faithful and
   `getImageData` returns true pixels.
2. **WebGL software path (R2)** — best-effort; may stay represented-not-faked.
3. **Paint stack / top-layer (R4)** for dialogs/popovers `::backdrop`.
4. **Web Audio / MediaStream processing (M1, M2)** — real buffers where feasible.
5. **Download malware verdict (M6)** — pluggable scanner hook (hash + optional
   external engine), reported honestly.

### Phase 7 — Engine semantics & adversarial `[J1–J5, A1, A2, A5]`

1. **doshgentv8 fork patches fully wired (J5)** — fuel, host-clock, seeded RNG,
   checkpoint callback promoted from Phase-2-pending to selectable/default.
2. **Native WASM decision (J1)** — either wire V8's WASM or keep the documented
   divergence explicit; decide and record.
3. **WeakRef/Intl upgrades (J3, J4)** as needed by real pages.
4. **In-VM widget channels (A1), reCAPTCHA coverage (A2), login/OTP (A5)** —
   keep upgrading the protocol lanes; never fake tokens.

---

## 4. New shell commands introduced by this plan

| Command | Phase | Purpose |
|---|---|---|
| `net.stream` | 1 | Drain SSE/event-stream chunks for a request |
| `net.ws` | 1 | Inspect WebSocket frames (text + binary) |
| `net.cors` | 1 | Per-request CORS verdict + reason |
| `net.timing` | 1 | Per-request timing breakdown |
| `net.replay` | 1 | Re-issue a recorded request with its real body |
| `net.decode` | 1 | Inflate + structurally decode binary/protobuf bodies |
| `state.snapshot` / `state.diff` / `state.rollback` | 2 | Unified state checkpointing |
| `identity` | 2 | Stable element refs across re-renders |
| `delta` | 2 | Subscribe to and drain DOM mutation deltas |
| `caps` (upgraded) | 2 | Formal per-page capability manifest |

All also exposed as MCP tools (`browser_net_stream`, `browser_net_ws`, …) and
over the attach bridge, so a remote model gets the same depth as the local shell.

---

## 5. Secret-handling policy (unchanged default, new audited escape hatch)

- Default stays **redact everything secret-named** in bodies, headers, URLs.
- New per-session gate `allow_secret_reveal` (off by default). When the operator
  explicitly turns it on, `reveal=true` on `cookies`/`net` prints real values and
  the action is recorded in the ledger as an audited reveal. This gives the agent
  "full access when authorized" without weakening the safe default.

---

## 6. Testing & verification contract

- Every item ships a **hermetic test** from real captured markup or a mock
  transport — no substring-on-whole-page assertions (per `AGENTS.md`).
- Both lanes stay green: `cargo test --workspace` and `uv run pytest tests/`.
- Each phase updates the `AGENTS.md` capability + roadmap tables to flip the
  item's status honestly (❌/🟡 → ✅), with the file:test citation.
- No capability is reported as working until its test passes and a live smoke
  run confirms it.

---

## 7. Out of scope (will not be built)

Chromium/Playwright/Puppeteer/Selenium/CDP of any kind · an OS/POSIX shell ·
a full general-purpose WASM engine beyond challenge needs · real OS worker
threads. These remain intentionally excluded; the plan emulates their observable
behavior natively instead.

---

## 8. Suggested execution order (shortest path to the user's goal)

1. **Phase 1** (network depth) — directly answers "see live requests, streams,
   cookies, CORS, replay, decode". Highest value, mostly self-contained.
2. **Phase 2** (state manipulation) — snapshot/diff/rollback + stable refs +
   mutation stream make manipulation safe and repeatable.
3. **Phase 3** (interaction fidelity) — makes driving pages reliable.
4. Phases 4–7 as capacity allows, in the listed order.

> Recommendation: start with **Phase 1, items N1 (SSE) and N3 (CORS verdict)** —
> they map exactly to the Xiaomi chat page in the audit and are small, testable
> wins that unlock the rest of the network depth.

---

## 9. Execution log

### Phase 1 — DONE (2026-11-06)

Implemented by the freebuff (Codebuff) session `2026-11-05T20-16-12.124Z`, which
expired mid-summary (410 `session_expired`) after both suites were green.
Re-verified in a fresh session: `cargo test -p dosh-browser-core` → **231 passed,
0 failed**; `uv run pytest tests/` → **928 passed, 8 skipped**. N1–N9 all shipped
in both lanes (SSE capture, WS frames, CORS verdict, timing, replay, decoders,
caps, audited secret reveal, initiator stacks). Remaining: doc status updates
(landed with Phase 2 close-out below).

### Phase 2 — execution (started 2026-11-06)

Gap audit against the working tree before coding:

| Item | Rust lane | Python lane |
|---|---|---|
| S1 state DB + `state.*` | 🟡 `checkpoint`/`restore` + `db` exist; no `state.*` surface; ledger not in state store | 🟡 same |
| S2 element identity | ✅ `identity.rs` + `ref` (drift detection) | ✅ `ref` via content-identity inventory (stable field) |
| S3 mutation stream | ❌ **stub** (`watch` returned `records: []`) | ✅ real (`__wrWatch*` MutationObserver drain) |
| S9 capability manifest | 🟡 basic (counts + fixed gaps) | ✅ real (per-page unavailable + why, export) |

Todos (status: pending → done as they land, both lanes, hermetic tests):

- [x] **S3-Rust** — replace stub `watch` with the real MutationObserver delta
      stream over the resident runtime (shared minidom.js `__wrWatchInstall/
      Drain/Disable`); pump microtasks before drain. Test: mutate via `act`,
      drain, assert records/seq/next_after; disable retains.
      → `test_watch_real_mutation_stream` (shell.rs).
- [x] **S1-Rust** — unified `state_snapshots` SQLite table (html, url, title,
      live cookie jar, storage, history, ledger view) + `state.snapshot` /
      `state.diff` / `state.rollback` commands (ledger is append-only —
      rollback restores document/storage/history/cookies and says so). Tests.
      → `test_state_snapshot_diff_rollback_roundtrip` (shell.rs).
- [x] **S1-Python** — mirror `state.*` in `webresearch/shell.py` (store +
      handlers + value-free storage diff). Tests.
      → `test_state_snapshot_diff_rollback`, `test_state_diff_reports_value_change_without_the_value`,
      `test_state_snapshot_unknown_id_is_honest` (tests/test_shell.py).
- [x] **S9-Rust** — bring Rust `caps` to the "available / unavailable + why"
      level: live-realm counts with honest static fallback, tabs/frames/dialogs,
      absolute-path-only export. Test.
      → `test_caps_manifest_live_counts_and_honest_unavailable` (shell.rs).
- [x] **S2-tests** — hermetic proof in both lanes that content-addressed stable
      refs survive a re-render that shifts document order.
      → `test_stable_identity_survives_rerender_shift` (Rust),
      `test_ref_stable_survives_rerender_shift` (Python).
- [x] **Docs** — §2.1 (N1–N9) + Phase 2 statuses here and the `AGENTS.md`
      capability table (capability table, "Completed & wired" table, Layers
      1/3/5 roadmap rows all updated to the audited state).
- [x] **Green** — `cargo test -p dosh-browser-core` (**236 passed, 0 failed**)
      + full `uv run pytest tests/` (**932 passed, 8 skipped**; 4 new Phase-2
      tests included).

Committed 2026-11-06 as 32 detailed commits (Phase 1 carry-over from the
freebuff session, Phase 2 work, plus earlier-session carry-overs: assist/
attach bridge, README/installer, Skillion doc).

### Phase 3 — execution (started 2026-11-06)

Interaction fidelity `[L10, L7, L8, E2, E3, E4, S4, S5]`. Order per §3:
occlusion gate first (inspector exists), then caret/selection, then the frame
clock, then the input-subset gaps, then frame storage.

- [x] **L10** — occlusion-gated clicks: consult `__isElementOccluded` in the
      actuate path (`__activateSelector` gains the gate `__clickPath` already
      has); refused clicks surface `occluded_by:<desc>` in the lane's
      `__lastActionError`; both lanes report the refusal honestly (never a
      silent success). Tests: overlay-over-button fixture → refused; same
      button with overlay removed → click lands.
      → Rust `test_occlusion_gate_refuses_click_under_overlay`
      (`__activateSelector(…, 6th-arg gate)` + Rust `apply_action` gate,
      default on, `occlusion_gate:false` override; `check_action_error`
      surfaces it), Python `test_occlusion_gate_refuses_click_under_overlay`
      (runtime gate-pass + `_apply` raises on `actions_applied.error`).
      Rust 237/237; Python shell+action subsets green.
- [x] **L7** — minimal caret + Selection/Range in minidom.js:
      `selectionStart/End/setSelectionRange/select` on text inputs +
      textareas (spec model: unset caret defaults to end of value,
      IndexSizeError on start>end), `selectionchange` on the document,
      upgraded `Selection` (anchor/focus node+offset, `isCollapsed`,
      spec `addRange` replace semantics, `collapse(node,offset)`),
      `Range.toString/deleteContents`, focus links the caret to
      `window.getSelection()`, typing/backspace/enter/space insert at the
      caret (replacing an active selection first).
      → `tests/test_engine_apis.py::test_field_selection_model_and_selectionchange`,
      `test_typing_lands_at_the_caret`, `test_typing_replaces_an_active_selection`,
      `test_backspace_at_caret`, `test_contenteditable_caret_and_getselection`.
      Both engines green (V8 via Rust suite, QuickJS via Python suite).
- [x] **L8** — real rAF frame clock: replace the `setTimeout(fn,16)` shim with
      a frame queue driven by the virtual clock; `__drainTimers` steps frames;
      rAF callbacks receive the frame timestamp; nested rAF bounded per pass.
      Test: rAF ordering vs timers + timestamp monotonicity under the virtual
      clock.
      → `tests/test_minidom_layout_upgrades.py`: `test_raf_frame_clock_cadence`
      (frame not due until the 16.6 ms boundary passes),
      `test_raf_frame_clock_nested_next_frame` (inner waits for a LATER
      boundary, never fires in the same batch),
      `test_raf_shim_mode_fires_on_next_drain` (`__setRafFrameInterval(0)`
      restores the immediate shim). Frames scheduled on the deterministic
      `__timerNow` virtual clock (not wall-clock `performance.now()`, which
      has a 150 ms boot baseline). Existing monotonic-delivery + batch-
      isolation tests still green.
- [x] **E2** — text-selection drag lane: a drag over selectable text produces
      a real selection (select event), distinct from the element DnD lane.
      → engine `__selectText` (mousedown → selection extends →
      selectstart/select/selectionchange → mouseup; never a click),
      action kind `text_select` (both lanes), `Session.select_text` +
      shell `select ref start end` command (56th command; count test
      updated). Offsets clamped/swapped like a real drag; non-text
      targets refuse with `not_selectable_text`.
      Tests: `test_select_text_drag_lane` + `test_select_text_rejects_non_text_targets`
      (engine), `tests/test_shell.py::test_select_text_drag_lane` (Python),
      `shell::test_select_text_drag_lane_rust` (Rust). Rust 238/238.
- [x] **E3** — mid-flight `fetch` abort: AbortSignal fired while the host
      fetch is outstanding rejects the promise with `AbortError` (currently
      only pre-dispatch abort works); both engine lanes.
      → `tests/test_engine_apis.py::test_fetch_in_flight_abort_rejects`,
      `test_fetch_in_flight_abort_wins_over_resolve`,
      `test_fetch_without_abort_resolves_after_pump`. `fetch` defers
      resolution one microtask: a synchronous `abort()` between dispatch and
      settle rejects with the signal's reason (documented window, not a
      faked delay — the engine itself is synchronous). Stale "honest shape"
      comment updated.
- [x] **E4** — full Popover semantics: `popover=auto|manual`, light dismiss,
      top-layer stacking order, beforetoggle/toggle events.
      → `tests/test_small_apis.py::test_popover_declarative_target_and_light_dismiss`:
      declarative `popovertarget`/`popovertargetaction` (toggle/show/hide)
      via a delegated document click listener; click-outside light dismiss
      closes open AUTO popovers and never a manual one; click inside keeps
      it open. Top-layer stacking stays with Phase 6 (R4) — no paint stack
      here yet, disclosed.
- [ ] **S4** — same-origin frame storage sharing: a loaded frame's realm is
      seeded from the session storage for its origin; writes flow back to the
      session (shared area, not per-frame copies).
- [x] **S4** — same-origin frame storage sharing: a loaded frame's realm is
      seeded from the session storage for its origin; writes flow back to the
      session (shared area, not per-frame copies).
      → `load_frames` gains `storage_seed_for: Callable[[str], dict] | None`;
      both `run_scripts` call sites in `load_frames` (new frames at line 849,
      reused frames at line 898) pass the per-frame-origin seed; `read_live`
      threads the callable through; `session.goto`, `_apply`, and
      `_act_submit_field` pass `self._storage_seed` and absorb each frame's
      drained storage back (order: parent first, frames second — fixes a
      regression where the parent's drained dict popped the frame's writes).
      → `tests/test_shell.py::test_same_origin_frame_storage_shared`:
      same-origin frame sees the session's seeded key and writes back;
      cross-origin frame sees nothing of the parent's storage and its
      writes stay isolated under its own origin.
- [x] **S5** — storage events broadened: same-origin frame realms receive
      `storage` events too (today: same-origin other tabs only); cross-origin
      never (per spec) and that stays disclosed.
      → `_broadcast_storage_events` now broadcasts to both other same-origin
      tabs AND same-origin frame realms (each frame has its own QuickJS
      context, separate from the parent's, but same-origin frames share the
      underlying storage area via `_web_storage`). The originating tab's
      frames receive the event (they're not the one that made the change);
      cross-origin frames never receive it (per spec).
      → `tests/test_shell.py::test_storage_events_reach_same_origin_frames`:
      parent writes via action pass (full drain + broadcast), same-origin
      frame receives the event and the value is applied; cross-origin frame
      receives nothing.
- [x] **Docs** — §2.3 rows (L7/L8/L10 ✅) + AGENTS.md tables.
- [x] **Green** — both full suites (943 passed/8 skipped; 238 passed/0 failed)

### Phase 2 — bugs the Phase-2 tests exposed (fixed in-lane, both lanes benefit)

- **`act` was a silent no-op in the Rust lane.** `Target` had no CSS selector;
  the action lane passed the content-hash `ref_id` as a selector, which matches
  neither an id nor a CSS selector, so every click/type no-op'd (it only
  worked by accident when a raw id was passed). Fix: `Target.selector`
  (kuchiki port of actions.py `_css_path`, with CSS identifier escaping and
  synthetic-root exclusion), `click`/`type_text` resolve via selector + the
  `~N` occurrence for repeated siblings, and an unresolvable ref is now an
  error, not a no-op. → `test_act_resolves_css_selector_not_content_hash`.
- **`minidom.js __activateSelector` failed silently.** `element_not_found` /
  `control_disabled` / `click_refused` are now set on `__lastActionError`
  (and cleared on a resolved element), so the action lane can surface them.
- **`Session::fork` produced an empty-branch runtime.** The forked session's
  resident runtime never got the document, so every action on a fork was a
  silent no-op. `fork` now materializes the document (same re-load `rewind`
  performs). → `test_session_fork_speculative_execution` now exercises a real
  branch action.

### Phase 4 — Layout/geometry completeness (execution; started 2026-11-06)

AGENTS.md order: **sticky → flex-wrap distribution → floats → bidi**, then
text-metrics upgrade, smooth-scroll animation, CSS transition/keyframe timing
engine. One captured-markup regression per item. Both suites green before the
phase is called done. Status legend: ✅ done+verified · 🟡 partial · [ ] todo.

- [x] **L2** `position: sticky` — sticky already in WIP (top/left/right); verify + regression test.
- [x] **L3** flex-wrap + justify/align distribution — rewrite WIP two-pass (double layout corrupted `__lines`/overcounted boxes) into single natural pass + subtree translation; regression tests for space-* distributions + align-items.
- [x] **L1** CSS floats — rewrite naive WIP (floats consumed block cursor; inline text never wrapped) into float regions + line-bounds exclusion; block/replaced/inline siblings wrap; `clear`; regression test incl. captured markup.
- [x] **L4** Bidi — thread inherited `direction` (html/body → descendants); remove debug prints; honest disclosure (RTL reorder + right-anchored lines; NO Unicode bidi algorithm/shaping); regression test.
- [x] **L5** Real font metrics (shaping-lite) — Helvetica/Arial AFM advance widths (1000u/em) + common kern pairs; honest `textMetric` in `__layoutInfo`; regression tests (i < W, kern reduces AV).
- [x] **L6** Smooth scroll animation — rAF-driven smooth scroll (ease-out) for scrollTo/scrollBy/scrollIntoView with `behavior:"smooth"`; `auto`/`instant` stay stepped; regression test with the rAF pump.
- [x] **L9** CSS transitions / @keyframes — real timing engine on the frame clock: property interpolation (px/unitless/rgb/transform), transition props parsed from the cascade, `@keyframes` harvested; computed values update per pumped frame; regression tests (transition mid/final values; keyframe progress).
- [x] **Docs** — §2.3 rows + AGENTS.md capability table + roadmap row.
- [x] **Green** — Python full suite + `cargo test -p dosh-browser-core --lib`. (954 passed/8 skipped; 238 passed/0 failed)

Pre-flight note: the Phase-4 WIP branch of work began with an uncommitted
flex rewrite, naive floats, bidi and sticky adds. An earlier misdiagnosis
(body margin-shift conditional) broke `scrollWidth` semantics and the
`test_css_grid_layout_columns_and_gap` expectation; reverted to HEAD behavior
(8px unconditional shift) and re-verified green before continuing.

### Phase 5 — Workers / WASM substrate (execution; started 2026-11-06)

Order per §3: W1 Worker emulation first (message queue + child realm), then
W2 Service Worker execution, W3 WASM float/extended opcodes, W4 cross-realm
MessagePort. Both lanes share minidom.js. One hermetic test per item. Both
suites green before the phase is called done.

- [ ] **W1** Web Worker emulation: `new Worker(url)` fetches the script (real
      URL via host_fetch; blob: from the blob store), runs it in a child realm
      (function scope with worker-only globals: `self`, `postMessage`,
      `onmessage`, `close`, `importScripts`), and wires a real message queue
      pumped by the existing `__drainTimers` pump — parent→worker and
      worker→parent messages deliver between turns, never synchronously.
      `terminate()` stops the pump. `SharedWorker` gets a shared port.
- [ ] **W2** Service Worker execution: `navigator.serviceWorker.register(url)`
      fetches and runs the SW script in a ServiceWorkerGlobalScope realm with
      `fetch` event handlers + `event.respondWith`; page fetches within the SW
      scope route through the SW's respondWith promise.
- [ ] **W3** WASM float + extended opcodes: f32/f64 types (const 0x43/0x44,
      comparisons 0x5B–0x66, arithmetic 0x7B–0x8F, min/max/copysign/abs/neg/
      ceil/floor/trunc/nearest/sqrt), f32/f64 memory load/store (0x2A/0x2B/
      0x38/0x39), i64 consts (0x42) where QuickJS supports them, wrap/extend/
      trunc conversions (0xA7–0xAC), sign extensions (0xA0–0xA4, 0xC0–0xC4).
      Fuel metering unchanged.
- [ ] **W4** Cross-realm MessagePort: transferred ports route in the receiving
      realm — `postMessage` on the received end queues to the sender, the
      buffer is live (not inert).
- [ ] **Docs** — §2.7 rows + AGENTS.md roadmap rows.
- [ ] **Green** — Python full suite + `cargo test -p dosh-browser-core --lib`.
