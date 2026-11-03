# v8_lane — V8 feasibility test lane (NOT the product engine)

Companion experiment for the planned QuickJS → doshgentv8 runtime swap.

Boundary (per AGENTS.md): `webresearch/`, `tests/`, server, CLI **never import
this lane or any V8 binding**. The product default engine stays QuickJS.
Everything here answers one question with evidence: *can the untouched
`webresearch/engine/minidom.js` run inside embedded V8, and what diverges?*

- `smoke.py` — hour-zero smoke harness. Boots real V8 via `mini-racer`
  (a prebuilt-V8 proxy, not our fork), evaluates the product's `minidom.js`
  **verbatim**, and probes the HIGH-risk seams the QuickJS coupling audit
  flagged: TypedArray-Proxy rewrite, host-callable arity, synthetic event
  loop, WASM, storage, DOM+parser. QuickJS runs the identical probes as the
  baseline oracle in the same process.
- `RESULTS.md` — dated results of harness runs (the claim log).

## How to run

```bash
uv pip install mini-racer --python .venv/bin/python   # once
.venv/bin/python v8_lane/smoke.py
```

## What "green" means and what it does not

Green = minidom.js boots in V8, all probes match QuickJS semantics where they
must, divergences are classified. It does NOT mean the swap is done: the real
port is the planned `rusty_v8` + PyO3 companion scaffold with the full 13-callable
seam behind `WR_JS_ENGINE=v8`, parity-gated by the existing test suite.
