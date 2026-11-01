# Open Issues — dosh-browser engine

> Register of verified open issues and engine capability gaps.
>
> Rule from AGENTS.md applies: a capability gap is a bug report against
> `minidom.js`, never a reason to reach for a browser binary.

---

## Open Issues

### 1. Scroll: Nested container `scrollIntoView`
- `el.scrollIntoView()` computes block start/center/end/nearest geometry and scrolls the document window, but it does not yet scroll nested scrollable ancestor containers (`overflow: auto | scroll`).
- `behavior: 'smooth'` executes synchronously without animation timing frames.
- `ifNeeded` performs nearest instead of centering.
- `display: none` targets scroll to phantom coordinates.

### 2. History: Page-load timers and script re-execution
- `history.back()` within a `setTimeout()` during initial page load is deferred until an action pass (moves are consumed only on action passes).
- History restore re-executes destination scripts against the restored DOM: pages that run `pushState()` unconditionally on initial load will re-push upon navigation back.
- Popstate state is JSON-serialized across engine passes (structured-clone subset: `undefined` object properties, functions, and symbols are dropped rather than preserved via binary structured clone).

### 3. Storage: Cross-tab `storage` event broadcast
- `localStorage` and `sessionStorage` persist correctly with exact insertion order across navigations and sessions, but cross-tab `storage` events are not dispatched to sibling same-origin tabs in the session.

### 4. Drag & Drop: `dropEffect` / `effectAllowed` negotiation
- Drag & drop dispatches `dragstart`, `drag`, `dragenter`, `dragover`, `drop`, `dragleave`, and `dragend` with drop-target hit-testing, but does not negotiate `dataTransfer.dropEffect` vs `effectAllowed` cursors.
- Non-draggable elements that attach a `dragstart` listener trigger a native DnD sequence (in a real browser, only `draggable="true"` initiates DnD unless text selection is dragged).

### 5. Content Root: Two-sibling no-main extraction heuristic
- `content_root` heuristic on pages without `<main>` containing exactly 2 sibling candidate containers drops one sibling (below the 3+ container threshold).

---

## Disclosed Substrate Boundaries (By Design)

**Decision: NOT implemented; honestly represented.** These require running arbitrary guest code in parallel/isolated runtimes — an external engine architecture, not this lightweight browser. They remain feature-detection-plausible (constructors resolve, calls do not throw), and their limits are disclosed per `AGENTS.md`:

- **Web Workers**: Constructor + inert methods resolve; no background OS execution thread exists. Blob URLs resolve locally.
- **WebAssembly (WASM)**: `validate` returns false, `instantiate` yields empty exports, no Module/Memory/Table constructors. No bytecode interpreter.
- **Service Workers**: Regex analysis + Python strategy simulation for in-page fetch/XHR (CacheFirst/SWR/NetworkFirst honored from shared cache); the SW's own JavaScript never executes in a worker thread and navigation is never intercepted.
- **Overlay Occlusion**: Programmatic clicks via `__activateSelector` act on the selector-matched control directly without paint-order collision testing.
