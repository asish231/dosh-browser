# Open Issues — dosh-browser engine

> Work register of verified open gaps. Every issue here was reproduced through the
> real engine (`webresearch.session` + QuickJS mini-DOM, mock or local HTTP) and
> cross-checked against source, then independently re-verified by three subagent
> audits that were instructed to try to disprove each claim. Verified 2026-10-24.
>
> Rule from AGENTS.md still applies: a capability gap is a bug report against
> `minidom.js`, never a reason to reach for a browser binary.

Priorities: **P0** = the browser's core job is wrong (forms/storage/navigation).
**P1** = APIs real sites call, currently throwing. **P2** = substrate limits,
disclosed but blocking whole site classes. **DOC** = stale claims in docs/comments.

---

## P0 — Form and storage fidelity

### P0-1. `URL.searchParams` is detached from the URL → every GET form submits empty

- Engine: `u.searchParams.set("q","hi")` updates the params object but `u.href`
  stays without `?q=hi`; `u.search = "..."` mutates the property, never the href
  (`minidom.js:1834` — `href` is a plain data property computed at construction;
  `searchParams` attached separately with no write-back).
- Consequence, full trace: engine GET submit (`minidom.js:681-693`, `searchParams.set`
  at 686) navigates via `location.assign(u.href)` → `__WR_NAVIGATED` with no query
  (`runtime.py:1082-1084`) → `session.submit` treats the navigation as "prevented"
  and returns early (`session.py:504-507`, `402-403`) — so the correct raw Python
  GET lane (`session.py:432-435`, builds `urlencode(payload)` properly) is
  **unreachable for every GET form**. POST forms are correct.
- Repro: `session.submit(1, {"plan": "Beta"})` on a GET form fires
  `GET /result` with no query string. Search boxes and GET filters are dead.
- Fix direction: make `href`/`search` live accessors over `searchParams`, or build
  the GET query in `Element.prototype.submit` without going through `URL`.

### P0-2. `<select>` has no element semantics

- `options`, `selectedIndex`, `selectedOptions` all `undefined`; `.value` is the
  generic Element getter — concatenated option **text** (`minidom.js:996-997`),
  e.g. `"AlphaBeta"`; with indented markup, whitespace included.
- Typing via `__activateSelector('#sel','Beta',...)` appends: `"AlphaBetaBeta"`
  (typing lane `minidom.js:4969-5039` treats a select as a text field).
- `session.submit` raw payload parses the original HTML where a select's value is
  `""` (`forms.py:45`), so selects submit **empty** over HTTP unless the caller
  passes the value explicitly.
- Partial compensation (verified working): ArrowUp/ArrowDown on a focused select
  genuinely change selection and fire input/change (`minidom.js:5176-5198`);
  `__setFormFields` with an explicit value works for engine-side submits.
- Action surface advertises selects as `role=select` with label = concatenated
  option text (`"Alpha Beta"`) — an agent is told it can fill something it cannot.
- Fix direction: real `HTMLSelectElement` value/options/selectedIndex semantics;
  option `selected` attribute → `.value`; block the typing lane on selects.

### P0-3. Radio clicks are a complete no-op; raw HTTP lane ignores checked state

- `_dispatchClickSequence` special-cases checkbox only (`minidom.js:568-576`); a
  radio click checks **nothing** — the clicked radio stays `unchecked`, the
  previously checked one stays checked.
- Raw HTTP lane is worse: it builds the payload from Python-parsed static HTML
  where every radio is a plain field, dict comprehension last-wins — the
  **last same-named radio in the DOM** is submitted regardless of checked state
  (`session.py:405`, `forms.py:35-57`).
- Verified working: `__setFormFields` with an explicit value does enforce group
  exclusivity (`minidom.js:5416-5418`).
- Fix direction: radio case in the click sequence (uncheck name-group, set
  checked + attribute, fire input/change); payload build must consult checked
  state for radio/checkbox.

### P0-4. Disabled controls are still actuated

- `Session.act()` checks `action.disabled` at `session.py:1174`, **after** the
  navigate (`1160`) and submit (`1163-1172`) branches already returned. Verified:
  disabled submit button → real `GET /result`; disabled navigate link → real
  fetch; disabled textbox → typed successfully.
- Engine click path has no disabled concept at all
  (`_dispatchClickSequence`, `__activateSelector`): click listeners on a disabled
  button still run. `kind=activate` is the only guarded lane (raises `ValueError`).
- `el.disabled` is `undefined` in the engine, so the Tab-skip guard
  `if (el.disabled) continue` (`minidom.js:5114`) is dead code.
- Fix direction: hoist the disabled guard above all branches in `act()`; engine
  click/typing paths refuse disabled elements; define the `disabled` property.

### P0-5. `localStorage` / `sessionStorage` do not survive navigation

- Both are closures inside each engine context (`minidom.js:3067-3084`); every
  `goto` builds a fresh context (`runtime.py:245-247`); history restore drops
  `ctx` (`session.py:1585`). Token stored on `/a` reads `null` on `/b`, same
  origin, same session; same on `back()`/`forward()` restore.
- Dead code found: `profile.py:409/433` `load_storage`/`save_storage` + a sqlite
  `storage` table exist with **zero call sites** — persistence was scaffolded and
  never wired. (Cookies, by contrast, persist: `document.cookie` is bridged to the
  httpx jar, `runtime.py:292-308`.)
- `sessionStorage` has the same lifetime as `localStorage` here — no
  same-tab-survives-navigation distinction.
- Fix direction: per-origin storage on the Python side (the profile table is
  already there), injected into each fresh context; `sessionStorage` scoped to
  the tab lifetime.

### P0-6. Same-family payload bugs (found during verification)

- **Unchecked checkboxes are submitted** as `ok=` on the raw HTTP lane
  (`forms.py:49-56`, `session.py:405`); real browsers omit them.
- **The raw HTTP payload ignores all live DOM state**: it re-parses the original
  stored HTML, so anything the engine mutated (typing, toggles,
  `set_form_fields` in the same dispatch pass) never reaches the wire — only
  `__WR_FORM_SUBMIT` carries it, which `session.submit` ignores (the `_apply`
  act-lane consumes it, `session.py:1373-1406`).
- **`<option>` elements are surfaced as `activate` actions that do nothing**:
  clicking an option leaves the select unchanged — a silent false success, which
  the conversion protocol explicitly forbids.

---

## P1 — Missing APIs real pages call

All verified live: `typeof` → `undefined`, call → `TypeError: not a function`,
zero stubs/polyfills/compensating lanes anywhere in `webresearch/`.

| API | Consequence |
| --- | --- |
| `Element.prototype.scrollIntoView` | "scroll to top"/anchor flows throw |
| `HTMLDialogElement.showModal`/`close` (also `open`) | `<dialog>` modals never open; constructor is a hollow name-list shell (`minidom.js:4601-4630`) and `createElement('dialog') instanceof HTMLDialogElement` is `false` |
| `history.back/forward/go` (empty functions) + `popstate` | SPA back button dead; `pushState` rewrites location quietly (`_updateLocationQuietly`, `minidom.js:4576-4581`) but is recorded nowhere — `session.back()` skips pushed URLs entirely |
| `DragEvent`, `DataTransfer` | native HTML5 drag & drop cannot work; `__executeDrag` (`minidom.js:5227-5363`) dispatches only pointer/mouse events, never `dragstart`/`drop` |
| `navigator.sendBeacon` | analytics/unload beacons throw |
| `Element.checkVisibility` | visibility feature-detects throw |
| `document.caretRangeFromPoint` | caret positioning (consistent with the disclosed no-caret contenteditable approximation) |
| `Notification` | throws on `new Notification(...)` |
| `BroadcastChannel` | — |
| `Element.requestFullscreen` | — |
| `Element.showPopover` / popover API | — |
| `Element.scrollIntoViewIfNeeded` | — |
| `window.print` | — |
| `WheelEvent`, `InputEvent`, `SubmitEvent` classes | events are delivered as generic `Event` with init data; `instanceof` checks fail |
| `navigator.geolocation` | — |
| `navigator.serviceWorker` | `register()` throws at runtime — yet regex detection still "installs" the SW (see P2-3) |

No Python-side code depends on any of these (grep-verified), so nothing currently
breaks internally — pages do.

---

## P2 — Substrate gaps (disclosed, blocking site classes)

### P2-1. Worker is inert; blob URLs never materialize
Constructor stores the URL, all methods no-op (`minidom.js:2630-2638`); the blob
registry is write/delete-only bookkeeping (`minidom.js:1932-1947`). No execution
lane exists in `runtime.py`. Also: `fetch('blob:...')` leaks to the network lane
as a malformed URL instead of being rejected or served. Worker-heavy apps stall.

### P2-2. WebAssembly is a stub
`validate` returns `false` even for a valid module magic prefix
(`minidom.js:2639-2643`); `instantiate` yields empty exports; no
`Module`/`Instance`/`Memory`/`Table`/`Global` constructors, so every feature-detect
fails, not just validate. WASM libraries (codecs, in-browser SQLite, some WAF SDKs)
silently degrade.

### P2-3. Service workers: analysis + strategy simulation, no JS execution (PARTIAL — better than it looks)
What exists: regex analysis of registration scripts + precache priming
(`sw.py:53-101`), and a **Python-level strategy simulation for in-page
fetch/XHR** (`runtime.py:496-525`): CacheFirst/CacheOnly/StaleWhileRevalidate/
NetworkFirst are honored from the shared HTTP cache, with background
revalidation. What does not exist: the SW's JavaScript never runs, navigation and
subresource loads never consult SW strategy, `navigator.serviceWorker` is
undefined so runtime registration throws (while static-script regex detection
still reports the SW installed — a page whose `register()` throws is credited
with a worker).

### P2-4. Horizontal scroll is broken twice
- `_clampScroll` (`minidom.js:2897-2913`) clamps `cx` with the **vertical** max
  (`docHeight − viewportHeight`): probe on a 5000px-wide/1615px-tall page →
  `scrollTo(4000,0)` lands at `scrollX = 895.2`. `__scrollSmooth` inherits it.
- `documentElement`'s layout box never reflects overflow content — no
  `scrollWidth` concept (5000px child → docWidth 1280), so a corrected clamp
  formula alone would still yield max 0.

### P2-5. Hidden controls are still actionable
`collect()` parses serialized HTML and never consults the cascade
(`actions.py:420-563`): buttons hidden by inline `display:none`, stylesheet
rules, or `visibility:hidden` all appear in the action surface, and
`__activateSelector` fires their listeners. The engine has the data to fix this —
layout gives `display:none` elements zero-size boxes — but `actions.py` never
asks. Compounding: page-facing `getComputedStyle` is an inline-style shim
(`minidom.js:2046-2051`), so stylesheet hiding is invisible in-page too.

### P2-6. No overlay occlusion on clicks
`__activateSelector` resolves the selector and dispatches directly
(`minidom.js:5047-5068`); no hit-testing, no z-order anywhere in the engine. A
full-viewport fixed overlay does not intercept a click on the button beneath it.
(Fair nuance: this matches programmatic `el.click()` semantics; the divergence
is with coordinate/automation clicks.)

### P2-7. WebSocket limits
Incoming binary frames counted and skipped (`ws.py:237-239`); outgoing binary has
no API at all (`send()` is text-only, `ws.py:151-159`); edge bug — continuation
frames of a *fragmented binary* message fall into the text branch and can be
delivered as garbled text (`ws.py:240`); client never sends pings (no keepalive);
permessage-deflate neither implemented nor negotiated; 16 MB frame cap.

### P2-8. Cross-frame messaging is one-way and half-broken
- Frame→parent **works**: load-time replay delivers child messages targeted
  `parent`/`top` to the parent's listener with correct origins
  (`read.py:252-282`).
- Parent→frame **is dead code in practice**: routed correctly
  (`frames.py:303-323`) but `runtime.py:925` passes the frame's **URL** as
  `__receiveMessage`'s targetOrigin argument — the origin comparison
  (`".../c1.html" !== "https://child.local"`) always fails and the message is
  silently dropped. One-line-class fix.
- Frame→frame does not exist; no live channel, ports, or synchronous frame access.

---

## DOC — Stale claims that no longer match the engine

| Location | Says | Reality |
| --- | --- | --- |
| `minidom.js:1037` comment | "no floats/positioning/flex/grid, px + % + em only" | flex, grid, absolute/fixed/relative ARE implemented (`minidom.js:1126-1533`); units include rem/pt; only floats/sticky missing |
| `__layoutInfo.textMetric` (`minidom.js:1532`) | "monospace-advance-0.6em" | engine uses a proportional per-character advance table (`minidom.js:1192-1219`) |
| AGENTS.md capabilities table | "margin collapsing", "no floats/position/flex/grid" | margins stack additively (no collapsing); flex/grid/position exist |

---

## Verified working — do not re-file

Recorded so nobody burns time rediscovering these:

- ArrowUp/ArrowDown on a focused select changes selection and fires input/change.
- `__setFormFields` with explicit values works and enforces radio-group exclusivity.
- Checkbox click toggling is correct (no double-toggle artifact).
- POST form submits with explicitly-passed field values land correctly on the wire.
- Frame→parent `postMessage` replay at load time delivers with correct origins.
- SW strategy simulation for in-page fetch/XHR honors declared strategies.
- `kind=activate` disabled guard raises a clean `ValueError`.
- `URLSearchParams` standalone (toString, set) is spec-correct.

---

## Suggested order

1. **P0-1** (URL searchParams write-back — unblocks all GET forms, smallest fix, biggest unlock)
2. **P0-3 + P0-2** (radio/select/disabled — one form-fidelity pass; consult checked state in payload builds)
3. **P0-6** (raw payload from live DOM state; checkbox omission; option actions)
4. **P0-5** (wire the existing `profile.py` storage table; sessionStorage per tab)
5. **P2-8** parent→frame targetOrigin fix (one line) — can ride along with any commit
6. **P1 batch**: dialog/showModal, scrollIntoView, history+popstate session wiring, DragEvent/DataTransfer
7. **P2-4** horizontal clamp + scrollWidth concept
8. DOC pass: fix the three stale claims + AGENTS.md table
9. P2-1/P2-2/P2-3 (Workers, WASM, SW execution) — larger design work, not patches
