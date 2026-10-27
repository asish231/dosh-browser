# Open Issues — dosh-browser engine

> Work register of verified gaps. Round 1 (2026-10-24): every entry reproduced
> through the real engine and cross-checked by three subagent audits. Round 2
> (2026-10-27): three ADVERSARIAL audits tried to disprove every fix from
> round 1; they confirmed the core fixes but found real defects the author's
> own tests didn't encode — several introduced by the fixes themselves. The
> confirmed high-impact findings were then fixed and re-verified (20 new
> regressions in tests/test_reaudit_fixes.py). Round 3 (2026-10-27/28):
> action-surface visibility truth, the frame reply lane + URL-form
> targetOrigin, and the option-element API (36 regressions across
> tests/test_visibility_truth.py, test_frame_reply.py, test_option_api.py;
> suite at 471 passed). Commits: 6a6c493..0a342a3 + the round-3 commits.
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

## Round 2 — adversarial re-audit: confirmed defects and their fixes

Three adversarial audits tried to disprove the round-1 fixes. The CORE claims
all held (searchParams liveness, select/radio/disabled lanes, storage
persistence, scroll clamp, frame messaging, tab-surf, MCP findings). The
defects below were confirmed live, fixed, and re-verified:

### Fixed in round 2

1. **Injected-script execution lane was entirely dead** — the detector
   compared `nodeName === "SCRIPT"` but engine nodeNames are lowercase, so
   runtime `<script>` injection never executed (AGENTS.md had been claiming
   this worked). Also missing: `createDocumentFragment` (undefined),
   fragment flattening, Text `.data`/`.nodeValue`. Fixed: lowercase match,
   DocumentFragment + spec flattening, Text data/nodeValue; 4 regressions.
   AGENTS.md row restored to truth.
2. **Wire entry-list infidelity** — same-name checkboxes/text inputs
   collapsed to one pair; disabled inputs and fieldset-disabled descendants
   were submitted; the clicked submitter's name/value pair was missing;
   button/reset inputs were submitted; GET forms APPENDED to the action
   query instead of replacing it; GET multi-select serialized as one
   comma-joined pair. Fixed: __addField array promotion, __entryDisabled
   (fieldset + legend exemption), submitter threading through
   __submitPath/click, button/reset exclusion, GET query replacement with
   repeated pairs. 3 regressions.
3. **Forward entries destroyed by any action pass** — `Tab.push(replace=True)`
   truncated the forward stack unconditionally, so back()+scroll made
   forward() fail. Fixed: only new entries truncate. 1 regression.
4. **sessionStorage leaked across origins in a tab** — b.test read a.test's
   session store (per-origin scoping was never implemented). Fixed:
   per-origin slice per tab. 1 regression.
5. **WebSocket close never reached JS** — `_parse_frames` popped the conn
   before `poll_all` could report it, so server-initiated close never fired
   onclose and readyState stayed OPEN. Fixed: conn stays in the registry
   until poll_all reports it. 1 regression (local RFC 6455 close server).
6. **BroadcastChannel.close() was a no-op** — the filter callback compared
   against unbound `this`; closed channels kept receiving. Fixed: self-bound
   filter + closed guard on postMessage. 1 regression.
7. **Selector engine could not resolve `A > B.c.d`** — no child combinator,
   no multi-class compounds — which silently defeated the round-1 hidden-
   control filter for every id-less nested control (its `__hiddenCheck`
   selectors never resolved, and "unresolvable → visible" kicked in). Fixed:
   child-combinator + multi-class selector support; hidden filter now
   catches the id-less case. 2 regressions.
8. **History API holes** — two pushStates in one pass collapsed to one
   entry; relative pushState URLs resolved twice (`sub/page` →
   `.../sub/sub/page`); cross-origin pushState did not throw SecurityError;
   load-pass pushStates were dropped when the page had no entry yet. Fixed:
   __WR_PUSHED as a list, resolve-then-update ordering, origin check,
   absorb-after-push in goto. 2 regressions.
9. **caretRangeFromPoint returned the OUTERMOST box** (its own comment said
   innermost) and returned an empty Range outside every box instead of
   null. Fixed: smallest-box wins; null outside. 1 regression.
10. **Captcha false positives remained** — HTML comments, data-attributes,
    and provider names in visible prose ("an article about datadome") still
    fired the inspect banner while browser_captcha stayed quiet (lanes
    disagreed). Fixed: provider rules match structural evidence only
    (class/id/src/href/action attribute values); interstitial rules keep
    matching visible text. 2 regressions.
11. **14/22 MCP tools had no session_id in their schema at all** — the
    round-1 "uniform" claim only normalized the 8 that already had it.
    Fixed: declared on every tool, never required. 1 regression.
12. **Resource timeline reset every action pass** — page-load subresource
    entries vanished after the first scroll. Fixed: merge-by-URL instead of
    replace (fresh navigation still starts a fresh timeline, like a real
    page load); marks/measures now appear in getEntries()/getEntriesByType.
    1 regression.
13. **content_root was O(candidates²)** — 400 sibling rows measured ~63s
    (fresh parent-chain walks per candidate pair, with expensive selectolax
    node comparisons). Fixed: chains precomputed once, container-tag gate;
    300 rows now well under the test budget. 1 regression.
14. **sendBeacon returned true for unresolvable URLs** — fixed: URL parse
    failure returns false per spec.
15. **insertBefore("string") corrupted the tree** and the serializer
    fallback then silently discarded the whole pass's DOM mutations with
    errors=0. Fixed: TypeError per spec; the serializer fallback now records
    an error naming the discarded pass. 1 regression.

### Corrections to round-1 claims (now accurate in this register)

- Round-1 commit 0967508's message claimed dialog "close/cancel events" —
  the cancel event (ESC) was never implemented (see remaining).
- Round-1 claimed "session_id uniformly optional across all tools" — was
  true for 8/22; now all 22.
- Round-1 claimed multi-select "submits one pair per selection" — was true
  only for POST; GET serialized one comma-joined pair (now both repeat).
- Round-1's OPEN_ISSUES claimed "inspect and browser_captcha lanes agree" —
  three evidence classes disagreed (comments, data-attrs, prose); now the
  evidence model is shared and the remaining known disagreement class is
  listed below.

## Round 3 — action-surface visibility truth (fixed)

The conversion protocol's core promise — never report a control as actionable
when it is not — was broken in BOTH directions, verified live before fixing:

1. **JS style-property writes never reached the cascade** (false negatives):
   `el.style.display = 'block'` (the React/jQuery reveal pattern) updated
   only the style object, never the style ATTRIBUTE that `__computedStyle`,
   the serializer, and the surface parser all read — JS-revealed controls
   stayed invisible to checkVisibility and the action surface. Fixed: the
   style object is a Proxy whose set/delete traps sync every write back to
   the attribute (through setAttribute, so MutationObserver records fire),
   `style.x = ''` removes the declaration (un-hide), `setAttribute('style')`
   re-parses into the object (guarded against loops), a replacing attribute
   drops prior declarations, and unset properties read as `""` (spec).
2. **`[hidden]` attribute ignored** (false positives): UA rule
   `[hidden] { display: none }` added to `__computedStyle` (author CSS that
   sets display explicitly still wins, matching cascade order — and the
   cascade's selector matcher `__matchesSel` now supports attribute
   selectors so `#x[hidden] { display: block }` overrides can apply);
   ancestor `[hidden]` hides subtrees via the existing checkVisibility walk;
   the no-engine fallback walks ancestors too.
3. **Collapsed `<details>` content treated as visible** (false positives):
   UA rule — a closed `<details>` hides everything except its summary (and
   the summary's own contents); opening it (JS or a click) reveals the
   content to checkVisibility and the surface.
4. **Engine arbitration for every control**: `action_surface()` previously
   dropped inline-flagged controls without consulting the cascade, so an
   author-override (`#x[hidden] { display: block }`) could never surface.
   The engine now arbitrates ALL controls when a ctx exists; the inline flag
   is the fallback only.
5. **Honest disclosure for scriptless pages**: without an engine ctx the
   fallback filters inline/[hidden]/details/ancestor hiding, and if a
   `<style>` block exists the coverage line says stylesheet-hidden controls
   could not be verified (never silently claims a clean list).

Verified live: JS-revealed button surfaces (attr `display: block`,
checkVisibility true, listed actionable); [hidden] and closed-details
controls filtered with honest counts; author CSS overrides [hidden];
`<details>` opening live-surfaces its content; style writes produce
MutationObserver records. 12 hermetic regressions in
tests/test_visibility_truth.py; full suite 455 passed; tab_surf_check ALL
CHECKS PASSED. Known cost: the per-element style Proxy adds ~5-8% to
engine-heavy passes (suite 254s -> ~263-275s).

## Known remaining (honest, post round 2)

- **P0-area spec edges**: **option API FIXED in round 3** — `option.text`/
  `.label`/`.index` exist per spec (label attribute wins, index is the
  position in the select's list); `<option value="">` reports `""` (the old
  `attr || text` path treated the empty attribute as absent); the option
  value setter writes the text when no value attribute exists; multiple
  selects have no implicit first-option selection (value ""/selectedIndex
  -1 with nothing chosen, first selected wins otherwise); out-of-range
  selectedIndex deselects per spec (was: snap back to the first option).
  10 regressions in tests/test_option_api.py. Still open in this area:
  the engine lane does not disable fieldset-disabled descendants for
  page-JS flows (act() catches them via the surface; wire excludes them
  since round 2 — the engine click/type lanes still act on them);
  parse-time radio exclusivity not applied (two `checked` attrs in one
  group both read checked — wire is safe by collection order).
- **URL serializer/parsing edges**: `%20` instead of `+` for spaces in the
  application/x-www-form-urlencoded serializer; href getter drops
  username/password and non-special schemes (mailto:); default port kept
  in href; `?a` re-serializes as `?a=`; naive base resolution for
  base-with-query cases.
- **History**: `history.length` is engine-local (always 1) even when the
  session owns several entries; `scrollRestoration` undefined; popstate
  state is JSON-round-tripped (structured-clone subset: undefined keys and
  functions silently dropped); load-pass `history.back()` (in a setTimeout
  during page load) is dropped — moves are consumed only on action passes;
  history restore re-executes the destination's scripts, so a page that
  pushStates on load re-pushes after back().
- **Dialog**: no cancel event (ESC), no InvalidStateError on double-show,
  `form[method=dialog]` submit does not close the dialog; focus trap and
  top layer disclosed-absent.
- **scrollIntoView**: `behavior:'smooth'` is synchronous (no animation);
  no scrollable-ancestor support (scrolls the document); `ifNeeded` does
  nearest instead of centering; `display:none` target scrolls to phantom.
- **DnD**: drop fires without the dragover-preventDefault gate;
  drag-to-nothing still drops on the source's own position; DataTransfer
  types not ASCII-lowercased (setData/getData case-sensitive); no
  dragenter/dragleave; dropEffect/effectAllowed not negotiated; the
  dragstart-listener heuristic (a non-draggable element with a dragstart
  listener gets a native DnD sequence — a real browser fires none) is a
  disclosed deviation.
- **Small APIs**: geolocation watchPosition ids not unique; serviceWorker
  register scriptURL not resolved absolute; checkVisibility always consults
  visibility (spec: only with checkVisibilityCSS:true); popover missing
  InvalidStateError/NotSupportedError cases and auto-close-of-others.
- **Hidden controls**: **FIXED in round 3** (see below).
- **Frames**: **FIXED in round 3** — `e.source.postMessage` replies
  (target 'reply') now pass the session's message pumps and reach the parent
  with the child's origin, and URL-form targetOrigin
  ('https://host/path') is parsed down to its origin instead of naively
  string-compared (wrong origins still dropped). Verified end to end:
  parent → child delivery, reply, direct parent message, and URL-form
  targeting all work through a real act() pass; 6 regressions in
  tests/test_frame_reply.py. Remaining in this area: frames re-fetch and
  re-run their scripts on every action pass (init messages repeat).
- **MCP**: error-path results (unknown tool, bad args) bypass token
  accounting; the legacy `_iter_refs` fallback hash is still value-inclusive
  (it can never resolve inspect-issued form-field refs — it errors honestly
  instead); `press_key`'s stale-ref error text still describes the old
  value-sensitive refs.
- **Scroll**: every page gets ~8px of phantom horizontal scroll (the body
  box is shifted by its margin and counted in the extent); body.scrollWidth
  returns the box width, not the content extent.
- **Perf**: content_root's 2-sibling no-main pages still drop a sibling
  (below the 3+ container threshold — pre-existing subtraction model).
- **Storage**: no `storage` events in other same-origin tabs (spec fires
  them); key() ordering after cross-navigation re-seed follows host seed
  order, not original set order.
- Substrate execution depth (Worker/WASM/SW-JS) remains disclosed-not-
  implemented per the round-1 decision; blob: URLs resolve locally.

## Verified working — do not re-file

- ArrowUp/ArrowDown on a focused select changes selection and fires events.
- Checkbox click toggling (no double-toggle artifact).
- Frame→parent postMessage replay at load time (correct origins);
  parent→frame delivery (round 2 re-verified).
- SW strategy simulation for in-page fetch/XHR honors declared strategies.
- Storage across navigation/restore/tabs/profiles (P0-5), now including
  sessionStorage per-origin scoping (round 2).
- All P1 APIs above (each has a live probe + regression).
- Injected-script execution (round 2): appendChild/prepend src + inline,
  fragment flattening, Text.data/nodeValue.
- Wire entry-list: repeated pairs (GET and POST), disabled/fieldset
  exclusion, submitter pair, GET query replacement.
- Forward() survives action passes between back/forward (round 2).
- WS server-initiated close reaches JS onclose with code + readyState 3
  (round 2).
- BroadcastChannel close() stops delivery (round 2).
- Selector engine: `A > B.c.d` child combinator + multi-class (round 2).
- history: multi-push entries, relative single-resolution, cross-origin
  SecurityError (round 2).
- caretRangeFromPoint: innermost box; null outside (round 2).
- Captcha evidence: comments/data-attrs/prose quiet in both lanes; real
  widgets still fire (round 2).
- session_id declared on all 22 tools (round 2).
- Resource timeline persists across action passes; marks/measures in
  getEntries (round 2).
- content_root: 300-row lists under test budget (round 2).
