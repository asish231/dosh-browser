# Open Issues — dosh-browser engine

> Work register of verified gaps. Every entry was reproduced through the real
> engine and cross-checked by three subagent audits instructed to disprove
> each claim. **Status pass completed 2026-10-27**: all P0, P1, P2 (except the
> three disclosed substrate-execution items), DOC, and the five MCP-layer
> findings are fixed with hermetic regressions. Commits: 6a6c493, eb40aa8,
> 3a6effe, 555a35e, 7d5aacd, 0967508, c9cd30a, 91bb561, plus the docs pass.
> Suite at the final verification: 420 passed, 8 skipped. Full history in git.
>
> Rule from AGENTS.md still applies: a capability gap is a bug report against
> `minidom.js`, never a reason to reach for a browser binary.

---

## Fixed — P0 form and storage fidelity

### P0-1. URL.searchParams detached from the URL → GET submits lost the query — **FIXED** (6a6c493)
href/search/origin are live accessors over searchParams; `_replace` keeps
object identity. Live: `session.submit(1, {plan:pro, q:engines})` on a GET
form fires `GET /result?q=engines&plan=pro`. 7 regressions
(tests/test_p0_url_fixes.py).

### P0-2. `<select>` had no element semantics — **FIXED** (eb40aa8)
value from the selected option (first-option default per HTML spec), exclusive
value/selectedIndex setters, options/selectedOptions, option.selected,
typing matches by value then text (never appends). 7 regressions.

### P0-3. Radio clicks were complete no-ops; wire ignored checked state — **FIXED** (eb40aa8)
Click checks the radio, unchecks the name-group, fires input+change; wire
payloads consult live DOM state (clicked radio wins, unchecked boxes omitted,
`on` default per spec). 5 regressions.

### P0-4. Disabled controls were actuated — **FIXED** (3a6effe)
`act()` guards disabled before every lane (navigate/submit/activate raise
with zero HTTP); engine click/type/pressKey refuse disabled form controls;
`el.disabled` is a real property so Tab-skip works. 12 regressions.

### P0-5. localStorage/sessionStorage died on navigation — **FIXED** (555a35e)
Host-side per-origin stores seeded into each fresh ctx (`__WR_STORAGE_SEED`)
and drained back every pass (`__drainStorage`); sessionStorage is per-Tab;
the previously dead `profile.load_storage/save_storage` sqlite lane is wired
(cross-session persistence verified); `delete_storage` propagates removals.
7 regressions.

### P0-6. Payload family — **FIXED** (eb40aa8)
Unchecked checkboxes omitted from the wire; submit() consumes the engine's
live `__WR_FORM_SUBMIT` (typed values, toggles, selections, files) instead of
re-parsing stale HTML; multi-select submits one pair per selection; `<option>`
no longer surfaced as a do-nothing activate action (the select is the control;
engine option-click selects in the parent).

---

## Fixed — P1 missing APIs (0967508)

All previously `typeof undefined` with zero stubs; each now has a regression:

- **scrollIntoView / scrollIntoViewIfNeeded** — real-geometry scroll
  (block start/center/end/nearest), driving the same scroll lane.
- **HTMLDialogElement** — showModal/close/returnValue/open + close event;
  registry hoisted out of `__load` (it ran AFTER hydration and re-created
  constructors per pass), so `instanceof` holds for parsed AND created
  elements.
- **History API** — pushState/replaceState create/update real session entries
  (same DOM, new URL — SPA semantics); back/forward/go restore the target
  entry, rebuild the ctx, and dispatch popstate with the destination's pushed
  state. Live: entries 1→2 at /page2; engine-driven back() lands on /home with
  `popstate s:null|path:/home`.
- **DragEvent + DataTransfer** — native DnD in `__executeDrag` for draggable
  sources: dragstart (cancelable), drag per step, dragover/drop on the
  hit-tested target, dragend. Live payload round-trip:
  `dragstart,dragover,drop:payload-1,dragend`.
- **navigator.sendBeacon** — real POST through the owned host lane.
- **Element.checkVisibility** — consults the real cascade (stylesheet
  visibility:hidden counts; visibility/opacity added to the cascade template).
- **document.caretRangeFromPoint** — box hit-test (disclosed: element-level,
  no caret geometry).
- **Notification** — permission denied (no invented notifications).
- **BroadcastChannel** — same-context pub/sub (disclosed: no cross-frame
  delivery).
- **requestFullscreen / exitFullscreen / fullscreenchange** — state + events
  (disclosed: no top layer/paint).
- **Popover API** — showPopover/hidePopover/togglePopover with beforetoggle
  (cancelable) + toggle events.
- **window.print** — beforeprint/afterprint pair.
- **navigator.geolocation** — PERMISSION_DENIED, never invented coordinates.
- **navigator.serviceWorker** — register() resolves a registration
  (controller null; SW execution stays host-side, see P2-3).
- **WheelEvent / InputEvent / SubmitEvent** classes — instanceof passes.

---

## Fixed — P2 substrate gaps

### P2-4. Horizontal scroll broken twice — **FIXED** (7d5aacd)
`_clampScroll` clamps each axis against its own extent; layout computes
`doc.__scrollExtent` (furthest laid-out box edge) and Element/Document expose
scrollWidth/scrollHeight (the stale clientWidth stub that silently overrode
them is removed). Live: 5000px page `scrollTo(4000,0)` → 3728 (=5008−1280).
7 regressions.

### P2-5. CSS-hidden controls still actionable — **FIXED** (c9cd30a)
`action_surface()` asks the engine cascade (`__hiddenCheck`) — inline
display:none, stylesheet rules, visibility:hidden, hidden ancestors all count;
hidden controls are excluded but counted and reported on the surface (never
silently dropped; unresolvable selectors treated as visible). 4 regressions.

### P2-7. WebSocket gaps — **FIXED** (c9cd30a)
Outgoing binary is real opcode-2 frames (byte-exact via base64);
fragmented-binary continuation frames are skipped and counted instead of
decoded as mojibake text into the inbox; client pings idle connections every
25s (keepalive). 4 regressions including a local RFC 6455 frame server.

### P2-8. Parent→frame postMessage dead — **FIXED** (7d5aacd)
`runtime.py` passed the frame URL as `__receiveMessage`'s targetOrigin (an
origin was expected), so the child's check always failed. Delivery now passes
`msg.target_origin`; frames.py's Python-side origin check was already
correct. Live: child listener receives `GOT:hello-child|from:<parent>`.

### P2-6. Overlay occlusion — **DISCLOSED, UNCHANGED**
`__activateSelector` is selector-resolved `el.click()` semantics; a real
browser's programmatic click also bypasses occlusion, and this engine has no
paint order. The nuance is recorded here: agents driving pages with full-screen
overlays act on the underlying control directly.

---

## Disclosed — substrate execution depth (P2-1/P2-2/P2-3)

**Decision: NOT implemented; honestly represented.** These require running
arbitrary guest code in parallel/isolated runtimes — a design project, not a
patch. They stay feature-detection-plausible (constructors resolve, calls do
not throw), and their limits are disclosed rather than faked:

- **P2-1 Worker**: constructor + inert methods; no execution lane exists.
  Blob URLs resolve LOCALLY now (fetch/XHR against `blob:` serve the
  registered content or reject; they never leak to the network lane as
  malformed requests — fixed in the docs pass).
- **P2-2 WASM**: `validate` still returns false, `instantiate` yields empty
  exports, no Module/Memory/Table constructors. No bytecode interpreter here.
- **P2-3 Service workers**: regex analysis + Python strategy simulation for
  in-page fetch/XHR (CacheFirst/SWR/NetworkFirst honored from the shared
  cache); the SW's own JavaScript never executes and navigation is never
  intercepted.

---

## Fixed — DOC stale claims (docs pass)

- minidom.js layout comment: now states flex/grid/position implemented, units
  px/%/em/rem/pt, margins additive; remaining: no floats/sticky/flex-wrap/
  justify/align/bidi.
- `__layoutInfo.textMetric`: `"proportional-advance-table"` (the engine uses
  __CHAR_ADVANCES, a per-character ratio table).
- AGENTS.md capabilities table: rewritten to match (flex/grid/position,
  additive margins, proportional advance, disclosed approximations updated).

---

## Fixed — MCP-layer findings (91bb561, 2026-10-24 exercise)

1. **False captcha banner — FIXED.** `detect_challenges` matched needles
   against the raw document; Wikipedia's inline
   `wgConfirmEditHCaptchaSiteKey` fired "CHALLENGE: hcaptcha" on every
   article. Now uses captcha.py's evidence model (script contents never
   count; tags/attributes do). Inspect and browser_captcha lanes agree.
2. **Twin-form Enter-to-submit — FIXED.** Inspect refs hashed the input's
   VALUE, so typing changed the typed field's ref while its empty twin kept
   the stale one; Enter resolved to the twin (Wikipedia → Main_Page).
   Form-field refs now hash value-independent identity. Type+Enter submits
   the typed element's own form with its query.
3. **Script-heavy list extraction — FIXED.** `content_root` subtracted ALL
   descendant candidates, emptying `<main>` on list pages (10 sibling
   `<article class="Box-row">`) and crowning a single row: ACD read one repo
   card of ten. Candidates with 3+ candidate descendants are list containers
   and keep their score. GitHub trending: 2 → 22 ACD blocks, all 10 rows.
4. **Injected JS throws — root-caused precisely.**
   - example.com: its production s.js calls `document.body.prepend()`; the
     ParentNode insertion methods (prepend/append/before/after/
     replaceChildren, strings→Text) were missing. Implemented; example.com
     runs with zero errors and its injected content renders.
   - GitHub: rspack chunk `hk` cross-chunk require reads `.length` of
     undefined during module eval (registry live-binding approximation on
     webpack chunk requires). Non-fatal; the page renders (10 rows). Errors
     now carry provenance: `(in module <url>)`.
   - The performance resource timeline is now REAL (one entry per
     subresource actually fetched, real URLs + byte sizes; timing values 0 —
     no fabricated timings), so stats reducers over
     `getEntriesByType("resource")` no longer read `undefined.entries`.
5. **Token counter — FIXED.** Accounting moved to the TOP of every result
   (the old tail footer vanished under client truncation for exactly the
   biggest calls, so summed per-call numbers undercounted the session
   total). browser_token_usage documents the reconciliation.
6. **session_id — FIXED.** Uniformly optional across all tools, one shared
   session, documented in each schema.

---

## Known remaining (honest)

- GitHub rspack chunk-require `hk` live-binding error (above): non-fatal,
  module-provenance reported; full webpack cross-chunk emulation is part of
  the ESM registry approximation, tracked with the module system.
- Worker/WASM/SW execution depth: disclosed above, by decision.
- Overlay occlusion: disclosed above (P2-6).
- Binary WebSocket frames INCOMING are counted and skipped (disclosed gap);
  outgoing binary is real opcode-2.
- No pixel rendering: canvas/video frames never reach the agent (decomposed
  at parse); captcha stays inspection-only by policy.

## Verified working — do not re-file

- ArrowUp/ArrowDown on a focused select changes selection and fires events.
- Checkbox click toggling (no double-toggle artifact).
- Frame→parent postMessage replay at load time (correct origins).
- SW strategy simulation for in-page fetch/XHR honors declared strategies.
- Storage across navigation/restore/tabs/profiles (P0-5).
- All P1 APIs above (each has a live probe + regression).
