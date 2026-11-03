# Open Issues — dosh-browser engine

> Register of verified open issues and engine capability gaps.
>
> Rule from AGENTS.md applies: a capability gap is a bug report against
> `minidom.js`, never a reason to reach for a browser binary.
>
> Round 5 (2026-11-01): issue 1 (nested scroll) fixed. scrollIntoView now
> walks every scrolling ancestor nearest-first and offsets the document
> target by the containers' travel; element scrollTop/scrollLeft/scrollTo/
> scrollBy gained real clamping + non-bubbling scroll events; per-element
> scrollWidth/scrollHeight come from subtree content extents;
> IntersectionObserver honors container clipping; hidden subtrees lose
> their stale layout boxes (display:none targets are no longer scroll
> targets); and the kebab→camel cascade mapping was restored — an untested
> e2ffd5a regression that made getComputedStyle longhands (font-weight,
> overflow-y, …) read as undefined, which the scroll lane itself needed.
> 20 regressions in tests/test_nested_scroll.py; suite at 538 passed.
>
> Round 6 (2026-11-01): issue 2 (history fidelity) fixed. Load-time history
> traversals (`history.back()` in a `setTimeout` at page load) are consumed
> by goto()/submit() instead of silently deferred to an action pass; the
> runtime drains+resets the engine's move counter so a traversal is never
> consumed twice; same-document traversal (back/forward between pushState
> entries) is a LIVE navigation — the ctx, JS heap, and DOM persist, popstate
> dispatches into the live context with the entry's URL/state, no script
> re-runs (a page that pushState'd on load no longer re-pushes after back()),
> and scroll offsets save/restore per entry (honoring scrollRestoration
> "manual"). popstate state now crosses the engine→host→engine boundary
> through a structured-clone subset codec: undefined-valued keys, Dates,
> NaN/±Infinity/-0, Maps, Sets, RegExps, Errors, and cyclic references
> survive; functions/symbols throw DataCloneError like a real browser.
> 15 regressions in tests/test_history_fidelity.py; suite at 553 passed.
> Remaining disclosed: cross-document restores still rebuild the stored
> document and re-run its scripts (parity with a real browser without
> bfcache); ArrayBuffer/typed-array state values are not cloneable.
>
> Round 7 (2026-11-01): issue 3 (cross-tab storage events) fixed. localStorage
> mutations now broadcast `storage` StorageEvents to every other same-origin
> tab with a live ctx — the receiving area syncs first, then the event carries
> key/oldValue/newValue/url/storageArea (isTrusted, instanceof both hold);
> originating tab never hears its own change, sessionStorage never broadcasts,
> cross-origin siblings get nothing, and quiet applies never echo. 11
> regressions in tests/test_storage_events.py; suite at 564 passed.
>
> Round 8 (2026-11-01): issue 4 (DnD negotiation) fixed. dropEffect/effectAllowed
> are validated phase-aware accessors; dragenter/dragover initialize dropEffect to
> the effectAllowed default, listeners may override within the allowed set,
> drop+dragend carry the negotiated operation, and no drop is delivered when the
> operation is "none". The phantom native DnD on non-draggable elements (the old
> dragstart-listener heuristic) is removed — only draggable=true initiates.
> 14 regressions in tests/test_dnd_negotiation.py; suite at 578 passed.
>
> Round 9 (2026-11-01): issue 5 (content_root two-sibling heuristic) fixed — the
> last open issue. Co-equal same-tag siblings now keep the wrapper's union
> (3+ at any size mix, exactly 2 when balanced within 8x); mixed-tag landmarks
> and grossly unbalanced pairs still subtract so the dominant landmark wins;
> nested duplicates are always subtracted from the union. En route: the first
> cut's nesting test was quadratic on wide sibling lists (27s on 300 rows —
> lexbor `==` against an ancestor-scale node costs ~90us); an ancestor-TAG
> prefilter keeps it linear (0.38s at 300 rows). 10 regressions in
> tests/test_content_siblings.py; suite at 588 passed. ALL 5 OPEN ISSUES NOW
> FIXED; only the 4 disclosed substrate boundaries remain.

---

## Open Issues

None of the five registered engine issues remain open. One test-interaction failure
was root-caused and fixed (2026-11-03):

- `tests/test_port_transfer.py::test_port_transfer_roundtrip` — passed in isolation
  (6/6) but failed deterministically in full-suite runs (2/2, signature
  `port_unrouted: 1, port_wires: 0`). Root cause: `frames.py`'s `_ctx_nonce()`
  cached per-context nonces in a dict keyed by `id(ctx)`; CPython reuses `id()`s
  after GC, so a fresh QuickJS context could inherit a DEAD context's nonce and
  every cross-realm port transfer addressed `<nonce>:<id>` unrouted. The heap
  layout that produces the address collision only occurs under full-suite
  allocation churn, which is why isolated runs never reproduced it. Fix: the nonce
  is read fresh from the live context on every call (no cache), and
  `runtime.py`'s durable per-context postMessage queues are keyed by the
  context nonce (`_post_queue_key`) instead of `id(ctx)` — the same reuse class
  could have delivered a dead context's pending posts into a fresh realm.
  Regression-pinned by `tests/test_ctx_identity_hygiene.py` without relying on
  allocator luck (nonce mutation must be followed by fresh reads).

The four disclosed substrate boundaries below remain by design.

---

## Fixed

### 6. Frames: MessagePort string payloads and the iframe `load` lifecycle — **FIXED** (round 10)
- Found while driving the reCAPTCHA v2 widget on a real login page
  (erp.aktu.ac.in): the anchor's postMessage channel went permanently
  silent. Three engine defects, each pinned by a hermetic regression test:
- **Port string contract** (`__wrParsePortData`): the port delivery lane
  eagerly `JSON.parse`d recorded strings, so a page posting a JSON-text
  string over a MessagePort (reCAPTCHA's anchor channel posts
  `["<b64>","z","<id>"]` as text) received a *different type* than it
  sent — an array where a real browser delivers the exact string. Every
  handler on the far side silently ignored the malformed traffic and the
  "o"/open handshake reply never came. Strings now cross the port lane
  untouched; only the `\u0001wrsc` structured-clone prefix decodes.
  (tests/test_port_string_contract.py)
- **Port listener `this`/target**: MessagePort listeners now fire with
  `this` and `event.target` = the port itself (spec). Called bare, a
  handler reading port state through `this` broke silently.
- **Frame-load lifecycle**: a frame document finishing its load now fires
  `load` on its iframe element in the owning realm (both the `onload`
  property and `addEventListener('load')`, each exactly once). Widget
  parents gate channel processing on this event; an engine that never
  dispatched it left handshakes "waiting for the anchor" forever.
  (tests/test_frame_load_event.py)
- **Console `_emit` host arity**: the JS console bridge passed
  (message, level) to every host callable, but QuickJS raises a DEFERRED
  SystemError (uninterceptable by any JS try/catch) when a 1-argument
  Python callable receives two arguments — which made `console.error`
  crash raw contexts. The runtime now declares its 2-arg callable via
  `__WR_HOST_LOG_TAKES_LEVEL`; single-arg hosts get the message alone.
- Outcome note, reported honestly: with all fixes the engine's widget
  lane is structurally complete — the anchor's handshake, botguard, and
  parent client all run — but Google's obfuscated channel protocol still
  declines in-VM (its parent handler consumes the correctly-typed string
  and declines to reply; the keys are derived inside the VM). The v2
  challenge is instead solved through the documented HTTP protocol lane
  (anchor → reload protobuf → payload → userverify), implemented in the
  session's captcha solver, which yields a verified
  `g-recaptcha-response` token. Tile classification crops each grid tile,
  upscales 2x (LANCZOS), and takes a 5-model vision ensemble with a
  strict-above-60% supermajority (a 3/5 gray-box-as-hydrant
  hallucination stays unselected — verified on a live dynamic grid with
  independently established ground truth). The widget UI lane remains
  disclosed as partial, not hidden.
- **Multicaptcha goal map** (same session): multicaptcha metas carry only
  Google class IDs, and a guessed ID table had `/m/04_sv` as "bus" when
  the bundle says MOTORCYCLES, and `/m/01bjv` as "fire hydrant" when the
  bundle says BUS — every multicaptcha round searched for the wrong
  object (all-empty selections on live rounds) until the goal names began
  being extracted from the live JS bundle's string table at solve time
  (each ID sits near its "Select all squares with X" description; cached
  per version). The multicaptcha first payload also needed `p=` and the
  replacement id chain starts at "2" (librecaptcha semantics).
  (tests/test_recaptcha_protocol.py::test_goal_map_extraction_from_bundle)
- **Labeled-grid classification**: vision models MIS-COUNT cells on a
  plain photo grid. The engine now draws cell lines and 1-based numbers
  on a 3x-upscaled grid image (``_label_grid`` +
  ``vision_grid_select``) and asks one ensemble call for cell numbers —
  verified on a live dynamic challenge with independently established
  ground truth: per-tile crops gave 4/5 and 4/5 on the two true positives
  with a dangerous 3/5 hallucination; the labeled grid gave 5/5 and 5/5
  unanimity with the hallucination collapsing to 2/5. One call per grid
  is also ~9x fewer requests, which keeps the challenge server's
  throttle at bay. Static `imageselect` challenges (select + verify, no
  replacements) are now handled too, and every response parse is
  shape-defensive (a degenerate dresp salvages the round instead of
  crashing the solve).
- **Verified end-to-end (2026-11-02, live site)**: the full chain —
  solve reCAPTCHA v2 → AES-CBC-encrypted credential POST → server-side
  acceptance — succeeded on erp.aktu.ac.in twice: 298s and 407s solves
  produced tokens the site's server ACCEPTED, both redirecting to
  exams.aktu.ac.in/studentlogin.aspx (the student portal SSO handoff).
  The student portal itself demands a SECOND captcha (different site
  key); after ~300 challenge rounds from one IP in one night Google's
  risk engine escalated every attempt (frequent multicaptcha + payload
  throttling), so stage 2 remained unsolved at session end. Honest
  state: stage 1 repeatedly verified; stage 2 needs a fresh IP or a
  multi-hour cooldown, not more code.


### 5. Content Root: Two-sibling no-main extraction heuristic — **FIXED** (round 9)
- `content_root` now recognizes **co-equal repeated siblings**: the same
  landmark tag appearing as siblings under a wrapper keeps the wrapper's
  UNION — three or more at any size mix (list pages, the round-2 case) and
  exactly two when their scores are balanced within 8x (two posts of a
  no-`<main>` blog index, the registered bug: the second post used to be
  dropped from every reading projection).
- The distinctions that motivated the old subtraction still hold: mixed-tag
  landmarks (a promo `<article>` beside the document `<main>` — `<main>`
  wins) and grossly unbalanced pairs (one real article plus a stub promo
  card, > 8x word gap — the real article wins) subtract normally, and
  NESTED duplicates (a promo card inside one of the siblings) are always
  subtracted from the wrapper's union.
- Performance stays linear: the nesting classification pre-filters on
  ancestor TAGS before any node comparison — lexbor `==` against an
  ancestor-scale node costs ~90µs, and the first cut compared every row
  against the body/html wrappers (27s on 300 rows; caught by the perf
  regression test). With the prefilter: 0.38s at 300 rows, 0.68s at 400.
- 10 regressions in `tests/test_content_siblings.py` (union cases, the
  promo/main and stub-promo distinctions, the nested-duplicate subtraction,
  the list-page union, and the 300-row performance bound); full suite 588
  passed, 8 skipped.

### 4. Drag & Drop: `dropEffect` / `effectAllowed` negotiation — **FIXED** (round 8)
- **Operation negotiation** per the spec processing model: the user agent
  initializes `dropEffect` to the default for the source's declared
  `effectAllowed` before each dragenter/dragover (uninitialized/copy/copyLink/
  copyMove/all → "copy", link/linkMove → "link", move → "move", none →
  "none"); the listener may override it; a canceled dragover sets the drag
  operation to the resulting dropEffect; `drop` and `dragend` carry the
  negotiated operation (the source learns "move" → remove the original).
- **drop delivered only when the operation is not "none"**: a target that
  preventDefaults dragover while the source declared `effectAllowed="none"`
  gets dragleave, not a drop (real browsers show a "no" cursor).
- **Validated setters (Blink parity)**: `effectAllowed` accepts only the nine
  spec values (case-insensitive, canonicalized: "COPYMOVE" → "copyMove") and
  is settable only in dragstart; `dropEffect` accepts only the four values
  and is settable only while negotiating (dragstart/dragend sets are
  ignored — the source does not choose the operation); during negotiation a
  set outside effectAllowed's allowed set is IGNORED; invalid values never
  change the attribute. Garbage like `dropEffect="banana"` no longer sticks.
- **Phantom native DnD removed**: only `draggable="true"` initiates the
  native sequence now. The old dragstart-listener heuristic fired
  dragstart/drag/dragend on NON-draggable elements — events a real browser
  never fires; those pages keep the plain pointer/mouse lane. Disclosed
  remaining: text-selection drags (a separate real-browser lane) are not
  modeled at all; drag-data-store protected mode (getData during dragover
  returns the payload rather than "") remains a generous deviation.
- 14 regressions in `tests/test_dnd_negotiation.py`; full suite 578 passed,
  8 skipped. En route: the negotiation helpers were initially trapped in the
  event-class scope and the surrounding try/catch silently swallowed the
  ReferenceError (dragenter/dragover/drop stopped firing) — caught by the
  live probe, hoisted onto `global`.

### 3. Storage: Cross-tab `storage` event broadcast — **FIXED** (round 7)
- `localStorage` mutations broadcast to every OTHER same-origin tab with a
  live engine context: the receiving tab's area is updated first (a real
  browser's storage area is shared — listeners read the new value during the
  event), then a `storage` StorageEvent dispatches on its window with the
  full spec payload: `key`, `oldValue`, `newValue`, `url` (the changing
  document's URL), and `storageArea === localStorage`; `isTrusted` true
  (browser-initiated, host-broadcast), `instanceof StorageEvent/Event`.
- Change records are recorded at the mutation site (setItem/removeItem/clear
  with old+new values; clear records ONE all-null event like Chrome), drained
  per pass (`__takeStorageChanges`) — seeding writes and the receiving tab's
  quiet apply never record, so nothing echoes back (verified: a third tab sees
  exactly one event, and the receiving ctx's own drain is empty).
- Isolation verified live: the ORIGINATING tab's own listener never fires
  (spec); sessionStorage changes never broadcast (per-tab area) and the
  writer keeps its own value; a cross-origin sibling gets neither the event
  nor the value; a fresh same-origin tab seeds the shared value normally.
- `StorageEvent` is a real class defined with the other event constructors
  (prototype linked to `Event` — an earlier draft defined it before `Event`
  existed, which silently broke `instanceof`; caught by the engine-level
  regression).
- 11 regressions in `tests/test_storage_events.py`; full suite 564 passed,
  8 skipped.

### 1. Scroll: Nested container `scrollIntoView` — **FIXED** (round 5)
- `scrollIntoView` / `scrollIntoViewIfNeeded` now scroll **every** scrolling box
  between the element and the viewport, nearest container first, then the
  document; the document target is offset by the containers' final scrollTop
  travel (the element's *visible* position, verified: pane reaches target
  start/end/center/nearest and the window lands at `target.y − paneTravel`,
  clamped to the document range).
- A `display:none` (or hidden-ancestor, or detached) target has **no rendered
  box** and is not a scroll target: layout clears stale boxes for hidden
  subtrees (`__clearBoxes`), so the rect is all zeros, the shrunken document
  clamps, and nothing scrolls to phantom coordinates.
- `behavior:"smooth"` now runs an eased multi-step path for both containers and
  the document (scroll events + clock advance per step; verified monotonic
  intermediate positions). Disclosed: still synchronous — QuickJS has no
  animation-frame loop, so the steps run within the call.
- `ifNeeded` now follows WebKit semantics: default `centerIfNeeded=true`
  CENTERS a not-fully-visible element (previously nearest), `false` scrolls the
  minimum, and it is a strict no-op only when fully visible in every scrolling
  ancestor AND the viewport.
- Element `scrollTop`/`scrollLeft`/`scrollTo`/`scrollBy` are real: clamped to
  content extent, fire non-bubbling `scroll` events only on actual movement,
  and stay 0 on non-containers (`overflow: visible`). `overflow` shorthand
  resolves `overflowX`/`overflowY` longhands; `overflow-y: auto` from a
  stylesheet makes the pane scrollable.
- `scrollWidth`/`scrollHeight` on any element reflect its overflowing subtree
  (a 900px child in a 150px pane reports 900), which is also the clamp.
- IntersectionObserver clips observed targets to each scrolling ancestor's
  visible window: an element scrolled out of its pane is not intersecting.
- The round-4 e2ffd5a getComputedStyle regression is fixed en route
  (`__normalizeProp` kebab→camel): multi-word declarations reach the computed
  style again (`getComputedStyle(el).fontWeight === "700"` from a stylesheet,
  `overflowY` from inline/style-sheet), with a regression test.
- Verified through a real session action pass: pane scrollTop set from page
  JS survives a click-driven re-render (state persists across passes).
- 20 regressions in `tests/test_nested_scroll.py`; full suite 538 passed,
  8 skipped (Chromium lane + WR_LIVE_TESTS opt-ins).

### 2. History: Page-load timers and script re-execution — **FIXED** (round 6)
- **Load-time traversals consumed**: `goto()`/`submit()` now consume
  `history.back()/forward()/go()` requested during the load pass (the
  `setTimeout` redirect-back pattern) instead of silently deferring the move
  to some later action pass. Out-of-range moves are no-ops, like a real
  browser. Chained moves — the restored page or a popstate listener
  requesting another traversal — are consumed recursively, bounded at depth
  4 so a runaway history loop cannot hang the session.
- **Move counter consumed once**: the runtime drain now RESETS the engine's
  `__WR_HISTORY_MOVE` after reading it into js_info; previously a live
  traversal re-read the same counter and traversed twice.
- **Same-document traversal is live**: every history entry carries a
  document identity (`doc_id`); pushState entries inherit the current one.
  back()/forward() between same-doc entries keeps the ctx, JS heap, and DOM
  (real same-document navigation semantics): popstate dispatches into the
  LIVE context with the target entry's URL and state, the engine location
  updates before the listener runs, the entry's page model refreshes from
  the live DOM, and **no script re-runs** — a page that pushState'd
  unconditionally on load no longer re-pushes after back() (verified: index
  stays 0 across a post-back action, `history.length` stays truthful, the
  script-run counter does not advance).
- **Per-entry scroll restoration**: same-doc traversal saves the leaving
  entry's scrollY and restores the target's (zero included — the old falsy
  check kept the leaving offset), honoring `history.scrollRestoration =
  "manual"`.
- **popstate state fidelity**: state crosses the engine→host→engine boundary
  through a structured-clone subset codec (`__structuredCloneEncode`/
  `Decode`): `undefined`-valued keys, Dates, NaN/±Infinity/-0, Maps, Sets,
  RegExps, Errors, and CYCLIC references all survive the round trip
  (verified end-to-end through a real traversal: `e.state.d instanceof
  Date`, `e.state.m.get("k")`, cycle identity `s.self === s`); functions,
  symbols, and DOM nodes throw `DataCloneError` exactly like a real browser
  (previously they were silently dropped).
- **Disclosed remaining**: a CROSS-document restore still rebuilds the
  stored document and re-runs its scripts — parity with a real browser
  without bfcache (which reloads the destination too, re-firing load-time
  pushState and truncating forward history). ArrayBuffer/typed-array state
  values are not cloneable in this subset.
- 15 regressions in `tests/test_history_fidelity.py`; full suite 553 passed,
  8 skipped. One pre-existing assertion in `tests/test_p1_apis.py` was
  corrected: it demanded the DOM text of the REBUILT pre-push document after
  same-doc back() — the old rebuild artifact, not real-browser behavior
  (a live same-document traversal keeps the mutated DOM; the popstate
  listener owns reverting it).

---

## Disclosed Substrate Boundaries (By Design)

**Decision: NOT implemented; honestly represented.** These require running arbitrary guest code in parallel/isolated runtimes — an external engine architecture, not this lightweight browser. They remain feature-detection-plausible (constructors resolve, calls do not throw), and their limits are disclosed per `AGENTS.md`:

- **Web Workers**: Constructor + inert methods resolve; no background OS execution thread exists. Blob URLs resolve locally.
- **WebAssembly (WASM)**: `validate` returns false, `instantiate` yields empty exports, no Module/Memory/Table constructors. No bytecode interpreter.
- **Service Workers**: Regex analysis + Python strategy simulation for in-page fetch/XHR (CacheFirst/SWR/NetworkFirst honored from shared cache); the SW's own JavaScript never executes in a worker thread and navigation is never intercepted.
- **Overlay Occlusion**: Programmatic clicks via `__activateSelector` act on the selector-matched control directly without paint-order collision testing.
