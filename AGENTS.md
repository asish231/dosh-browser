# AGENTS.md

Guidance for AI agents and humans working in this repository.

## Lane status (one source of truth: `webresearch/config.py`)

**Rust (`dosh-browser-core/`) is the PRIMARY product.** The standalone agent
browser binary (`dosh`) and engine core — pure Rust pipeline (fetch → parse → project →
session → shell → server/MCP, 189/189 tests green) powered by our custom `doshgentv8`
V8 engine, ACD 2.0, SIMD INT8 neural model, unified network pipeline (SSRF IpFilter,
SingleFlight, RateLimiter, Robots, CORS, WebBotAuth, RFC 9111 SQLite cache),
36 shell commands, Site-as-SDK API discovery, diff-first reading, and durable SQLite
session checkpoints. Zero Python runtime dependency for browser operations.

**Python (`webresearch/`) is an OPTIONAL companion package.** The Python SDK
and agent integration package — provides PyO3 native bindings via `doshgentv8_rust`,
QuickJS fallback, reCAPTCHA v2 protocol solver, login/OTP, frame messaging,
video lane, FastAPI REST server, and MCP tools for Python agentic frameworks.

Every surface answers "which version is primary?" from
`webresearch/config.py` (`shell product` / `project_status()`), never by
inference from commit messages.

## The rule that overrides everything else

**This project builds a browser from scratch. It must not use an existing one.**

No Chromium. No Playwright, no Puppeteer, no Selenium, no `rebrowser`,
`patchright`, or CDP of any kind. Not as a fallback, not as an escalation lane,
not "just for this hard page". `webresearch/engine/chromium.py` is a disabled
stub that exists only so imports and old call sites fail loudly.

Everything must work through:

- **HTTP fetch** (`webresearch/fetch.py`) — the transport
- **Our own DOM** (`webresearch/engine/minidom.js`) + **our JS engine**
- **Our own DOM event model** — `dispatchEvent`, listeners, bubbling

The JS execution layer may swap between engines: QuickJS is the default for
the main product; `doshgentv8/` is our own lightly-forked V8 with agentic
patches (fuel metering, clock injection, capability gates). Both run the
same `minidom.js` unchanged. Select via `WR_JS_ENGINE` flag. See
`doshgentv8/README.md` for the fork plan.

If a page cannot be handled this way, the answer is to *implement more of the
engine*, not to reach for a browser binary. A capability gap is a bug report
against `minidom.js`.

`research_repos/` holds third-party projects (`browser-use`, `nodriver`,
`rebrowser-patches`) as **reading material only**. Their conventions do not
apply here. In particular `browser-use/AGENTS.md` describes a Chromium/CDP
architecture — the opposite of this one. Do not follow it.

## Why it matters

The engine exists to be small, inspectable, and free of a 150MB browser
dependency. A single `import playwright` undoes the point of the project.

## No shortcuts, no "too hard"

There is no token budget and no time budget. Build anything from scratch when
the task requires it — new subsystems, new algorithms, new files are expected
work, not a last resort. Never assume a task is too hard, never look for the
easy way around, and never substitute an approximation for the real
implementation. The hard path is the job: invent it, implement it natively, test
it hermetically from real captured markup. Stating a limitation honestly is
acceptable; faking around it is not.

## Research, reference code, and native compilation

This is research toward an LLM-native browser. External browser source may be
studied to understand loading, parsing, scripting, layout, events, security,
storage, and frame architecture, but it must never become part of this browser.

- External projects stay under `research_repos/` and are **reference material
  only**. Never import, execute, link, vendor, build, or depend on them from
  `webresearch/`, tests, scripts, or the server.
- Prefer architecture documentation, specifications, and narrowly relevant source
  excerpts over whole engine checkouts. Do not paste large foreign subsystems
  into this runtime.
- Attach agent compilation to the normal document lifecycle without changing it:
  owned fetch → owned parse → owned script execution → owned DOM/events → derived
  reading and action projections. The agent representation is an additional
  compiled output, not a replacement document.
- Complexity never justifies a bypass or shortcut. No spoofed trusted events,
  forged tokens, hidden unverifiable behavior, copied engine shortcuts, fake
  layout coordinates, or controls reported as actionable when they are not.
- If a capability is missing, name it explicitly, implement it natively, and add
  a hermetic regression test from real captured markup.
- `webresearch/engine/chromium.py` remains a permanently disabled stub. Do not
  revive it, reintroduce its behavior elsewhere, or create another external
  browser lane.

## Any-website conversion protocol

This is the central ongoing work: every loaded website must be transformed into
our own browser document and then compiled into an agent-native representation.
The browser is not finished when HTML arrives; it is finished when an agent can
read, locate, identify, and manipulate everything important.

Apply this protocol to every website, current and future:

1. **Load our own document first.**
   Fetch the source through our HTTP transport, preserve the final URL and
   response status, run its scripts through QuickJS, and keep the mutated
   document. The loaded page—not the raw byte stream—is the conversion source.

2. **Preserve the full control inventory.**
   Do not discard buttons, inputs, sliders, selects, links, frames, canvas
   challenges, or repeated controls before the agent plans. Noise reduction may
   shape a reading projection, but not the control inventory.

3. **Derive an agent-native action surface.**
   Every operable control must have:
   - a stable, content-addressed reference;
   - a human-readable label, preferring visible text;
   - an explicit action kind: `navigate`, `submit`, `activate`, `drag`,
     `dropzone`, or `embed`;
   - enough routing information to execute it deterministically;
   - a plain statement when only a richer engine capability is required.

4. **Separate reading from driving.**
   Text extraction may omit navigation chrome. The action surface must not.
   Never reuse an article-reading filter for controls.

5. **Report coverage honestly.**
   A complete page can still exceed a response budget. In that case, report the
   total, what was returned, what was omitted, and how to retrieve the rest.
   Silent truncation is a conversion failure.

6. **Represent—not pretend—unsupported controls.**
   If mini-DOM cannot move a control, expose its structure, label, action
   requirements, and the missing capability. Never let an unavailable action
   appear successful.

7. **Keep extending the compiler.**
   New websites will expose unsupported controls. Treat each newly discovered
   control as a requirement against the conversion protocol, implement the
   missing mini-DOM/parser/event behavior natively, and add a regression test
   from real captured markup.

A website is successfully converted only when its agent projection can answer:

- What can I read?
- What can I do?
- Where does each action go?
- What is still unavailable, and why?

## Current capabilities and known gaps

Be honest about these rather than working around them silently.

| Area | State |
| --- | --- |
| JS engine | **QuickJS** = default (`webresearch/engine/runtime.py`, `from quickjs import Context`). **doshgentv8** = our own lightly-forked V8 at `doshgentv8/`: `core/` (pure-Rust engine crate, `HostSeam` trait, NO Python — consumed unchanged by the future pure-Rust product) + `py/` (thin PyO3 facade, cdylib `doshgentv8_rust`, quickjs.Context-compatible: eval/add_callable/execute_pending_job/limits) + `base/` (vendored V8 15.9.0 source). Rust bridge ACCEPTED vs QuickJS oracle: all probes identical (boot/document/TypedArray/console/event-loop/DOM/storage) with REAL synchronous Python host callables (9µs/call round-trip, product parser + hashlib), wall-clock kill 0.51s, host errors catchable in JS; two documented expected divergences (native WebAssembly; Proxy-wrapped TypedArray throws honestly instead of faking). Boot 10.4ms vs quickjs 13.9ms. Evidence: `doshgentv8/test_rust_bridge.py`. Fork patches pending: fuel metering, host-clock injection, seeded RNG, capability gate, checkpoint callback (all policy overlays, never alter byte-level semantics). Select via `WR_JS_ENGINE` (Phase 2 wiring); plan: `docs/DOSHGENTV8-MASTER-PLAN.md`. |
| Layout / geometry | Real block-flow engine in minidom.js: CSS cascade (tag<class<id<inline), vertical stacking with additive margins, flex (row/column + gap + flex-wrap + justify-content space-*/center/flex-end + align-items center/flex-end/stretch), grid (repeat(N) columns, wrap), position absolute/fixed/relative/sticky (top/bottom/left/right), CSS floats (left/right regions, clear), text wrap by proportional per-character advance + Helvetica-class kern pairs (shaping-lite), CSS-spec 300x150 replaced defaults; units px/%/em/rem/pt. Per-element overflow scrolling: `overflow:auto\|scroll\|hidden` boxes have real scrollTop/scrollLeft/scrollTo/scrollBy/scrollIntoView (clamped, non-bubbling scroll events), smooth scroll via `behavior:"smooth"` (ease-out, rAF frame clock), scrollWidth/scrollHeight from subtree extents, and `scrollIntoView` walks every scrolling ancestor before the document; IntersectionObserver clips to each scrolling box's visible window; hidden subtrees lose their stale boxes. Bidi: `direction:rtl` reorders inline elements and anchors lines right (inherited through the DOM); disclosed: no Unicode bidi algorithm, no glyph shaping. CSS timing engine: `@keyframes` animations (iteration-count, direction, fill-mode, easing) and `transition` shorthand both run on the frame clock with numeric/color interpolation, exposed via `getComputedStyle` mid-flight; disclosed: no animation events dispatched. Reported via `document.__layoutInfo`. |
| File upload | Native end to end: `browser_upload`/`browser_act(files=)` sets `el.files` and fires trusted input/change; multipart submits carry the exact bytes. Binary/video/any files travel as base64 (`content_b64`) so payloads are byte-exact; payload cap ~12 MB raw. |
| Dialogs (alert/confirm/prompt) | Recorded per script pass, reported in the view and via `browser_respond_dialog`. QuickJS is synchronous, so a dialog cannot block: an agent stages an answer, then re-runs the triggering action. Unanswered dialogs honestly report `answered: default`. |
| Named keys | `browser_press_key` (enter/tab/escape/backspace/delete/space/arrows/home/end/pageup/pagedown) with real key/code/keyCode. Enter performs implicit form submission, Tab moves focus, arrows change selects, Backspace deletes. |
| History API | pushState/replaceState create real session entries (same document, new URL); load-time `history.back()` in a `setTimeout` is consumed by goto/submit; back/forward between pushState entries is a LIVE same-document traversal (ctx/JS heap/DOM persist, popstate into the live ctx, no script re-run, per-entry scroll restore honoring `scrollRestoration`); state round-trips through a structured-clone subset codec (undefined keys, Date, NaN/±Infinity, Map/Set/RegExp, cycles preserved; functions/symbols throw DataCloneError). Disclosed: cross-document restore rebuilds the stored document and re-runs its scripts (no-bfcache parity); traversals chain-bounded at depth 4. |
| window.open / popups | Recorded and loaded as new session tabs (recursion depth-limited); page scripts receive an inert stub window so they do not crash. The agent stays on the opening page and can `browser_tabs` switch. |
| File downloads | `browser_download` streams to disk with sha256 and optional hash verification (hex or base64); a navigation that lands on an attachment/binary is saved and reported (path/bytes/hash) instead of parsed as a page. No malware verdict engine — that absence is reported, not hidden. |
| Video downloads | Two real lanes (`video.py`): direct media files stream through the session transport (cookies, sha256, byte cap); pages/HLS/DASH/platform URLs extract via yt-dlp run as `python -m yt_dlp` pinned to this interpreter (`webresearch[video]` extra), with metadata captured via `--print after_move:%(field)j` and the produced file hashed + size-checked post-hoc. Session cookies reach the extractor (Netscape export) for login-gated content. Policy enforced before any network work: DRM subscription services and known piracy mirrors are refused; the denylists are known-entries gates, not guarantees, and the tool contract says so. Absent yt-dlp is reported as a missing capability, never faked; no malware verdict — sha256 provided. Wired: `browser_get_video` (MCP/dispatch/HTTP) and the shell `video` command. |
| contenteditable | Detected as `editable` in both inspect and action surfaces; typing lands in textContent with input events carrying `data`/`inputType` so MutationObserver-based editors (Quill/ProseMirror) observe it. Approximation: no caret, plain text only. |
| Login / OTP | `browser_login` detects the wall (password role in both surfaces), fills+submits via the normal lanes, and returns an honest verdict: verified / otp_required / failed / account_locked / human_only. OTP pages (single input or split digit boxes) become checkpoints: `browser_submit_otp` fills, submits, verifies; max 3 attempts, ~10 min expiry, never guesses codes. `browser_login_state` probes without credentials. Secrets are memory-only: never persisted, never echoed in views/results. Engine never ticks "trust this device". |
| WebSocket | Hand-rolled RFC 6455 client (`engine/ws.py`, no third-party lib): sync handshake, masked client frames, text frames both ways, ping/pong and close handled; frames deliver on runtime pump passes. Binary frames are counted and skipped (disclosed gap). |
| Web Storage events | localStorage mutations broadcast `storage` StorageEvents to other same-origin tabs in the session: receiving area syncs first, then the event (key/oldValue/newValue/url/storageArea, isTrusted true). Originating tab gets no event; sessionStorage (per-tab) and cross-origin tabs never broadcast; quiet applies never echo. |
| Drag & Drop negotiation | `effectAllowed` (dragstart-only, case-insensitive, canonicalized) and `dropEffect` (negotiation-phase-only, validated against effectAllowed) drive the spec processing model: dragenter/dragover initialize dropEffect to the effectAllowed default, listeners override within the allowed set, drop+dragend carry the negotiated operation, and no drop fires when the operation is "none". Only `draggable="true"` initiates native DnD (non-draggable elements get the mouse lane). Disclosed: no text-selection drag lane; getData stays readable during dragover (no protected mode). |
| Trusted events | `Event` carries `isTrusted`; agent-driven dispatches (click/type/activate/drag/submit/press) are trusted, programmatic `el.dispatchEvent` is not. |
| Inline `onclick=` attributes | Wired: constructor compiles `on*` attributes into listeners (minidom.js Element). |
| Canvas / iframe pixels | Never reach the agent. Both are decomposed at parse time (`parse.py`). |
| SVG layout diagram | `screenshot` shell command paints the engine's computed boxes/colors/text as an SVG file (`__screenshotSVG()` in `minidom.js`; triggers its own layout pass). Honest limits: approximate diagram, not pixel-faithful — Latin-only text metrics, no canvas pixels, images referenced by URL not inlined. Secret hygiene: `type=hidden` inputs paint nothing; passwords paint dots only. |
| Trusted CDP input | Does not exist and must not be added. |
| Cross-origin frame messaging | Frames are fetched as separate documents; `postMessage` traffic is routed between live frame contexts and the parent (`engine/frames.py`): window messages and split MessagePort channels both directions, with a per-frame delivery cursor, port proxy materialization, and sender-identity resolution (`event.source` matches the parent's own iframe `contentWindow`). A posted STRING over a port/window lane arrives as that exact string (structured-clone contract; only the `\u0001wrsc` prefix decodes). A loaded frame fires `load` on its iframe element in the owning realm. Disclosed: obfuscated widget channel keys derived inside their own VM (e.g. reCAPTCHA's botguard-keyed anchor channel) can still decline in-VM; the reCAPTCHA v2 challenge is solved through the documented HTTP protocol lane instead. |
| reCAPTCHA v2 challenge solving | Native protocol solver (`engine/captcha.py`, `session.solve_recaptcha_v2`, MCP `browser_solve_recaptcha`): anchor → `api2/reload` (protobuf body, reason=fi) → `payload` (grid image) → per-tile crop classification by an ensemble majority of vision models → `replaceimage` for dynamic tiles → `userverify`; an embedded rresp means rejected and the loop replays with the next challenge until a passing token is issued. Verified end-to-end against a live widget: token accepted by the site's server (the AKTU ERP login redirected to the student portal). Token is never echoed, only its presence and length; site-side acceptance is the site's decision, reported honestly. |
| Fingerprint parity | TLS JA3/JA4 matches Chrome 124 via `curl_cffi` (both page fetch and subresource `host_fetch` lanes); Sec-Fetch-* headers computed from origin relationship; Sec-CH-UA client hints; enriched `navigator.*` device block (hardwareConcurrency, deviceMemory, screen, timezone, platform); inter-submit delay in login flows mimics human timing. |
| Injected script execution | Scripts added to the DOM at runtime via `appendChild`/`prepend` (SPA chunk injection) are detected and executed: `src` scripts are fetched and evaled, inline scripts evaled directly, ESM auto-detected for both; `DocumentFragment` children flatten per spec. (Was dead until the 2026-10-27 re-audit: the detector compared `nodeName === "SCRIPT"` against lowercase engine names.) Failures are surfaced as JS console errors. |
| ESM module translator | Real production bundles parse: regex-after-keyword detection (`return/^\d:/` no longer swallows 681 chars), line-comment cap bounds URL-string desyncs, default-only imports (`import X from "..."`) rewrite, `import.meta` replaced in real code and template interpolations via a template-aware string mask, and QuickJS Annex-B regex compatibility (`\-` → `\x2d`, `\;` `\,` dropped — `\\` preserved) for u-flagged classes. Verified against LinkedIn's 66-module graph: 0 parse failures (was 5). |
| Login submit fallback | When a page's submit control is inert in the engine (form-less React SPAs), `browser_login(submit_url=..., username_field=..., password_field=...)` POSTs credentials directly with page cookies + CSRF. Honest verdicts either way. |

Say "this needs a real browser" plainly when it is true. Do not paper over it.

## Current implementation status

### Completed & wired (uncommitted as of this audit)

| Component | Location | Tests | Status |
|---|---|---|---|
| DoshShell (terminal command surface) | `webresearch/shell.py` | `tests/test_shell.py` (738 lines, 111 dedicated passes) | ✅ Implemented, HTTP/MCP/CLI entry points connected |
| NetworkLedger + causal attachment | `telemetry.py`, session hooks | `tests/test_telemetry.py` (472 lines) | ✅ Implemented, observes every `resource_request` from pass |
| 5-stratum occlusion inspector (`__inspectLayers`) | `minidom.js:8936` | See `test_engine_apis.py` | ✅ Implemented, wired to `browser_layers` |
| Console log capture (per-realm) | `runtime.py:292,327` | In shell tests | ✅ Implemented, available via `browser_console` |
| Frame/postMessage data pipeline | `engine/frames.py`, `runtime.py` | In frame tests | ✅ Live traffic recorded & filtered by to/sender |
| Bounded control-realm `eval` | `shell.py:run_script` | In shell tests | ✅ Run JS with action/reading projection, strict limits |
| Skills registry | `shell.py:ShellStore` + SQLite | In shell tests | ✅ Save/run/delete skills with SHA-256 pinning |
| Snapshot & diff | `shell.py:snapshot/diff` | In shell tests | ✅ Evidence snapshot of controls, not VM checkpoint |
| `open`, `actions`, `act`, `net.*`, `console`, `eval`, `skill.*`, `db`, `storage`, `snapshot`/`diff`, `layers`, `frames`, `tabs`, `video`, `read`, `history`, `cookies`, `wait`, `caps`, `screenshot`, `frame.eval`, `ref`, `trace`, `watch` | `shell.py` | All commands tested | ✅ 55 shell commands fully connected (46 baseline + 6 net.* depth + 3 state.*; count asserted in `test_shell_45_commands_completeness`) |
| Binary WebSockets (count & skip) | `engine/ws.py` | Disclosed | ❌ Implementation exists, flagged as unimplemented |
| Scoped WASM Interpreter & In-DOM Engine | `webresearch/engine/wasm.py`, `minidom.js` | `tests/test_wasm_engine.py`, `tests/test_wasm_minidom.py` (13 tests) | ✅ Implemented, fuel-metered anti-tarpit, linear memory, stack/table ops, causal export telemetry |
| Native Function Cloaking | `minidom.js` (`__makeNative`, `Function.prototype.toString`) | `tests/test_stealth_cloaking.py` (6 tests) | ✅ Implemented, `[native code]` cloaking, spec-compliant name/length, CreepJS/Turnstile defense |
| **Unified SQL state DB** (history+cookies+storage+ledger) | `state_snapshots` in `shell.py` (`state_snapshots` in Rust `shell.rs`) | `tests/test_shell.py::test_state_*` + Rust shell tests | ✅ Implemented, both lanes, with `state.snapshot`/`state.diff`/`state.rollback`; diff is value-free, rollback rehydrates live jar + DOM, ledger stays append-only (disclosed). |
| **Element identity tracker** (stable refs across re-renders) | Rust `identity.rs`; Python content-identity inventory | Rust shell test, `tests/test_shell.py::test_ref_stable_survives_rerender_shift` | ✅ Implemented, both lanes. Refs are content-addressed; `ref {stable}` re-resolves across re-rendering. |
| **Causal trace stream** (action→Δ→net→console) | Python `trace` (full bundle) | Python shell tests | 🟡 Python complete (identity-keyed DOM delta + network + console + dialogs); Rust `trace` is wired but the `caused_by` link is still absent. |
| **Mutation subscription stream** (deltas, not re-reads) | host-side MutationObserver in `minidom.js` (`__wrWatch*`) + `watch` drain | Rust `test_watch_real_mutation_stream`; Python `test_watch_*` | ✅ Implemented, both lanes: after/limit/next_after, disabled retains. |
| **Capability manifest per page** ("unavailable — and why") | `caps` (Rust + Python) | Rust caps test + Python caps tests | ✅ Implemented, both lanes: live-realm counts with labeled static fallback, tabs/frames/dialogs, unavailable+why, absolute-path export. |
| rAF frame clock | `minidom.js` | tests/test_minidom_layout_upgrades.py::test_raf_* | ✅ Done (Phase 3: L8) |
| CSS transitions & keyframes | ✅ | tests/test_minidom_layout_upgrades.py | ✅ Done (Phase 4) |
| Sticky/flex-wrap/floats | ✅ | tests/test_minidom_layout_upgrades.py | ✅ Done (Phase 4) |
| **In-session DevTools agent (`assist`)** + **SSH-like attach bridge** | `dosh-browser-core/src/assist.rs`, `registry.rs`, `server.rs` (`/v1/attach` WS, `/v1/sessions`); Python `webresearch/shell.py:_assist` | Rust: `assist::*`, `registry::*`, `server::test_bridge_*`, `shell::test_assist_*`; Python: `tests/test_shell.py::test_assist_*` | ✅ Implemented & wired. `assist` runs a walkthrough over the live session (cookies/storage/`net.apis`) and synthesizes a unified cURL referencing secrets by name only. The bridge adds a session registry + a persistent WebSocket that attaches a client to one isolated live session so context persists across turns and connections do not clobber each other. Exposed via shell `assist`, MCP `browser_assist`, CLI `dosh assist`. |
| **Resident-runtime eval + live cookie jar** | `shell.rs` `eval`/`skill.run` now use `session.get_or_create_runtime`; `session.rs:current_cookies` | `shell::test_eval_state_persists_across_turns`, `shell::test_cookies_reads_live_jar` | ✅ Fixed. `eval`/`skill.run` previously rebuilt a throwaway runtime per call (empty cookie jar, no persisted JS state); they now evaluate against the tab's resident runtime so console state and `document.cookie` writes survive across turns. The `cookies` command reads the live jar (was reading stale HTTP response headers). `storage` now also reports localStorage. |
| **Phase 1 network depth (N1–N9)** | `telemetry.rs` (SSE/timing/sealed/protobuf/WS), `ws.rs` (RFC 6455), `cors.rs` (`cors_verdict`), `network.rs` (chunked stream), `shell.rs` (`net.stream/ws/cors/timing/replay/decode`, `cookies values=raw`, `gate secret_reveal`); Python mirror in `webresearch/{telemetry,shell}.py`, `engine/{runtime,ws}.py`, `minidom.js` (stack hint) | Rust: `telemetry::*`, `shell::test_watch*`-adjacent net tests; Python: `tests/test_telemetry.py`, `tests/test_shell.py` | ✅ Implemented, both lanes. Live streams (SSE with arrival stamps), WS frames (text+b64 binary, direction+opcode), CORS verdicts, measured ttfb/duration (DNS/connect disclosed unmeasured), audited replay + secret reveal (sealed in-memory evidence, 256 KiB cap, `RevealAudit` log), structural protobuf decode, parameterized body caps with honest truncation, sanitized initiator stacks. |
| **Phase 2 state manipulation (S1–S3, S9)** | `shell.rs`/`webresearch/shell.py` (`state_snapshots` table + `state.snapshot/diff/rollback`), `identity.rs` + `Target.selector` + `actions.rs::_css_path`, `minidom.js` (`__wrWatch*`, `__lastActionError` honesty), `session.rs` (`click`/`type_text` selector resolution, `fork` document materialization) | `shell::test_state_snapshot_diff_rollback_roundtrip`, `shell::test_watch_real_mutation_stream`, `shell::test_stable_identity_survives_rerender_shift`, `shell::test_act_resolves_css_selector_not_content_hash`, `shell::test_caps_manifest_live_counts_and_honest_unavailable`; Python: `test_state_*`, `test_ref_stable_survives_rerender_shift` | ✅ Implemented, both lanes. Unified state DB (DOM + live cookie jar + storage + history + ledger view); value-free structural diff; rollback rehydrates the live runtime and jar (ledger append-only, disclosed). Stable content-addressed refs survive re-renders in both lanes. `watch` is a real host-side MutationObserver drain (after/limit/next_after). `caps` is a live per-page available/unavailable+why manifest. |

### Not yet invented / explicitly out of scope
- **OS shell** — never build one; this is a browser terminal, not a POSIX shell.
- **Full WASM engine** — only for opcodes required by real challenge modules.
- **Workers as threads** — single-threaded emulation is the intended substrate.
- **bfcache** — serviced via documented rebuild, not live heap.

---

## Required Inventions Roadmap

### Legend
- **Status**: `UNAVENGED` (not implemented), `WIRE` (built but not connected), `BUILD` (partial but needs hard work)
- **Priority**: `P0` (foundational), `P1` (major capability), `P2` (important), `P3` (polish)

### Layer 0 — Transport & Network
| Invention | Current State | Status |
|---|---|---|
| Fingerprint parity (JA3/JA4, Sec-Fetch, CH-UA, navigator block) | `fetch.py` implementation | ✅ HAVE |
| Causally-linked network ledger (action→pass→request) | `telemetry.py` ready, causal link missing | `WIRE` + invent `caused_by` |
| Request rule engine (mock/block/redirect) | Consumed at `runtime.py:611` | `WIRE` |
| Cookie/site-data partitioning model (third-party isolation) | Not implemented | `INVENT` |

### Layer 1 — Document & Runtime
| Invention | Current State | Status |
|---|---|---|
| HTML→DOM with script ordering, mutated-document truth | `minidom.js` | ✅ HAVE |
| ESM module graph translator (66-module LinkedIn graph verified) | `engine/modules.py` | ✅ HAVE |
| Runtime-injected script execution | `runtime.py` | ✅ HAVE |
| **Scoped WASM interpreter** (opcode subset real challenge modules use + hermetic tests) | `webresearch/engine/wasm.py` + `minidom.js` | ✅ HAVE |
| **WASM float + extended opcodes (W3)** — f32/f64 consts/arith/compare/memory/conversions at spec opcodes; i64 ops on the i32-approximated lane (disclosed); fuel metering unchanged | `minidom.js` WASM engine | ✅ DONE (Phase 5) — `tests/test_w3_wasm_float.py` |
| WebSocket binary frames | Counted & skipped | `INVENT` (currently counted & skipped) |
| Worker substrate decision: real threads vs honest single-thread emulation with real message queues | ✅ DECIDED + IMPLEMENTED: single-threaded child realms (Worker/SharedWorker) with real message queues pumped by `__drainTimers` (W1) | ✅ DONE (Phase 5) — `tests/test_minidom_layout_upgrades.py` -k worker |
| Service worker real execution (SW JS runs, navigation intercepted) | ✅ SW script runs in a ServiceWorkerGlobalScope realm; in-scope `fetch()` routes through `event.respondWith` (W2) | ✅ DONE (Phase 5) — `tests/test_w2_service_worker.py` |
| **Element identity tracker** — stable refs surviving re-renders | `identity.rs` (Rust) / content-identity inventory (Python) | ✅ HAVE |

### Layer 2 — Layout, Geometry, Text
| Invention | Current State | Status |
|---|---|---|
| Block-flow + flex + grid + per-element overflow scrolling | `minidom.js` | ✅ HAVE |
| Sticky → flex-wrap distribution → floats → bidi | Layout engine | ✅ DONE (Phase 4) |
| Text metrics upgrade (real advance tables, shaping-lite) | Proportional advance + kern pairs | ✅ DONE (Phase 4: L5) |
| Caret + selection model (editors, selection DnD) | Plain text only | `INVENT` |
| Real rAF frame clock (today: `setTimeout(fn,16)` shim) | `minidom.js` | `INVENT` |
| CSS transitions/keyframes timing engine | Frame-clock timing engine | ✅ DONE (Phase 4: L9) |
| **Occlusion-gated clicks** — inspector exists, clicks bypass collision | `__inspectLayers` built, clicks unapproved | `WIRE + INVENT gate` |
| Canvas/media structural decomposition (never pixel raster) | None | `PARTIAL → INVENT` |

### Layer 3 — Session & State
| Invention | Current State | Status |
|---|---|---|
| Multi-tab history, live same-doc traversal, stored documents | `session.py` | ✅ HAVE |
| Dialogs, uploads, downloads, named keys, WS text | `shell.py` commands | ✅ HAVE |
| IndexedDB store + per-pass drain | `runtime.py` | `HAVE/PARTIAL` |
| localStorage/sessionStorage + cross-tab storage events | `runtime.py` | ✅ HAVE |
| **Unified SQL state DB** (history+cookies+storage+ledger) with snapshots/diffs/rollbacks | `state_snapshots` + `state.snapshot/diff/rollback` (both lanes) | ✅ HAVE |
| **Deterministic replay engine** (ledger + snapshots + seeds) | Not implemented | `INVENT` |
| bfcache parity | Documented rebuild, no live heap | `HAVE-as-disclosed` |

### Layer 4 — Adversarial Robustness
| Invention | Current State | Status |
|---|---|---|
| Tracker graph + surrogates + hash-pinned remote lists | `engine/trackers.py` | ✅ HAVE |
| Challenge solvers (captcha family, DataDome, CT, OTP) | `engine/captcha.py`, `session.solve_*` | ✅ HAVE, keep upgrading |
| Bot-guard handshake (postMessage port lane) | Verified in tests | ✅ HAVE |
| Honeypot/decoy detection | `__inspectLayers` | `BUILT, WIRE` |
| Human timing model, generalized beyond login | `runtime.py` mimics delays | `HAVE → EXTEND` |

### Layer 5 — Agent-Native Projections *(the differentiator — nobody else can build these)*
| Invention | Current State | Status |
|---|---|---|
| Reading projection (layout-aware) | `content.py` | ✅ HAVE, upgrade |
| Action surface: complete inventory, labels, honest coverage | `shell.py` inventory | ✅ HAVE, upgrade w/ identity refs |
| Per-realm console tool (parent + every frame) | `runtime.py:console`, `shell.console` | `CAPTURED, WIRE` |
| **Multi-realm eval** (run JS in a chosen frame's context) | Not implemented | `INVENT` |
| Frame/postMessage debugger projection (lanes, addressing, bodies) | `frames.py`, `runtime.py` | `DATA FLOWS, WIRE` |
| **Causal trace**: action → DOM delta → network → console | Partial data | `INVENT` — flagship |
| **Mutation subscription stream** (deltas instead of re-reads) | `watch` over host-side `__wrWatch*` MutationObserver (both lanes) | ✅ HAVE |
| Capability manifest per page ("unavailable — and why", formalized) | `caps` (both lanes) | ✅ HAVE |
| **Skills registry** (persistent site procedures across sessions) | Partial: `shell.skill_*` | `INVENT` |
| DoshShell terminal as thin projection over all of the above | `shell.py` | `INVENT` (last, thinnest) |

### Layer 6 — Interfaces
MCP server, CLI, HTTP API: `tools.py`, `dispatch.py`, `mcp.py`, `cli.py` - **HAVE**. Keep tool contract stable across upgrades.

---

## Engineering rules learned the hard way

These cost real debugging time. Please don't rediscover them.

**Never assert on a substring of a whole page.** `assert "clicked" in html`
passes when the string occurs in a script or an attribute, whether or not the
code ran. Read the specific element's content. This produced a genuinely false
passing test here.

**Match CSS class tokens exactly, never by substring.** `slider` is a prefix of
`sliderContainer`, `sliderMask`, `sliderbg` and `sliderIcon`. Substring matching
turns one slider handle into six phantom controls.

**Prefer visible text over machine identifiers when labelling a control.**
`<button id="go">Go</button>` is labelled `Go`, not `go`. Check `aria-label`,
`title`, `alt`, `placeholder`, then text; only then `name`/`value`/`id`.

**Never drop controls silently.** If a budget hides part of the action surface,
say how many. The agent cannot plan against a surface whose shape it cannot see.
`actions.py` reports `ACTIONS: 244 of 245 (complete)` for this reason.

**Reading a page and driving a page need different filters.**
`content.py`'s `inside_chrome()` correctly drops nav/footer when you want an
article, and would be wrong for driving a page — "Open menu" and breadcrumbs are
exactly what an agent needs. Do not reuse the reading filter for the action
surface.

**Escape CSS identifiers.** Tailwind emits `lg:tw-flex` and `tw-flex[2]`; unescaped
they produce selectors the engine rejects.

**Copy every field from an event's `init` object.** `Event` dropping `clientX`
made every drag arrive as a click at `(0,0)`.

**Don't toggle twice.** `Element.click()` already flips checkbox state. Setting
`checked` in addition lands you back where you started.

**Make tests hermetic.** `settings.load_dotenv()` uses `setdefault`, so a test
that clears an env var gets it repopulated from `.env` on the next call. Patch
`load_dotenv` in tests that assert on configuration.

## Secrets

Secrets live in `.env` only, which is gitignored. Never write a key into source,
a test, a doc, or a commit. `.env.example` documents the variable names with
empty values.

## Secrets and providers

`settings.py` holds the model registry. `FAMILIES` maps a family to an
`api_key_env` and `base_url_env`; `MODELS` maps a registry `id` to a wire
`model`. These differ whenever an endpoint names its own deployments — `--model
pool-auto` sends `auto`. Keep them separate.

For an OpenAI-compatible pool, both `OPENAI_COMPAT_BASE_URL` and
`OPENAI_COMPAT_API_KEY` are required. The registry deliberately refuses to guess
a host from a key, because a wrong guess surfaces as a 401 that looks like a bad
key.

## Verifying a change

```bash
.venv/bin/python -m pytest tests/ -q     # full suite
.venv/bin/python -m webresearch models   # what is configured
```

Some tests self-skip when an optional dependency is absent; read the skip
reason before assuming coverage.

Test against **real captured markup**, not only hand-written HTML. Hand-written
fixtures only prove the assumptions you already had. `testhtml/test1.html` is a
captured page (205 links, 14 buttons, Nuxt/Vue) and found several real bugs that
invented HTML never would have.

## Reporting results

State what was verified and how. If something did not work, say so plainly and
show the evidence. Distinguish "the drag executed" from "the captcha passed" —
they are very different claims, and conflating them is how a false success gets
reported.