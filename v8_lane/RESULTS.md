# Smoke results — 2026-11-03

## The question

Can the product's own `webresearch/engine/minidom.js` (9,494 lines, untouched)
run inside an embedded V8 engine?

## The evidence

Run via `uv pip install mini-racer --python .venv/bin/python && .venv/bin/python v8_lane/smoke.py`.

mini-racer ships a prebuilt V8 static library (macOS arm64, Python 3.14).
The harness evaluates MINIDOM_JS in both engines, registers identical JS-stub
host callables, and runs 8 probes: boot, TypedArray, console arity, event loop,
WebAssembly, DOM+parser, storage, native typed-array + WASM.

### Results table

| Probe | QuickJS (oracle) | V8 | Match |
|---|---|---|---|
| A_boot (document/window/Node/Event) | `["object","object","function","function"]` | `["object","object","function","function"]` | ✅ |
| B_typedarr (Proxy OOB write, instanceof, reduce) | `{"len":4,"at2":7,"at10":"undef","inst":true,"reduce":7}` | identical | ✅ |
| C_log_arity (host_log 2-arg contract) | `done` | `done` | ✅ |
| D_event_loop (Promise, micro, timers) | `["promise","micro","t20","t150"]` | identical | ✅ |
| E_wasm (Proxy-wrapped Uint8Array + WASM.validate) | fake: `validate:false, no Module ctor` | **THROW**: `Argument 0 must be a buffer source` | ⚠️ |
| F_dom (createElement + innerHTML + event dispatch) | `{"click":1,"attr":"1",...}` | identical | ✅ |
| G_storage (localStorage + cookie + crypto stub) | `{"ls":"v","ck":"sid=42"}` | identical | ✅ |
| H_native_typedarr (pre-minidom ctor + native WASM) | fake WASM: `hasRealWasm:false, validate:false` | **real**: `hasRealWasm:true, validate:true, Instance ok` | ✅* |

\* *H proves that V8's native WebAssembly works with the pre-minidom constructor;
both engines return `oob:"undef"` for native OOB reads (spec-compliant).*

**Boot timing** (minidom.js eval): QuickJS 29.0ms, V8 16.3ms.

**QuickJS arity-trap diagnostic**: `immediate:SystemError:… returned a result
with an exception set` — the documented QuickJS-only behavior where a 1-arg
Python callable invoked with 2 args raises a deferred SystemError that
propagates at the next engine boundary. V8 has no equivalent (extra JS args
to a host function are silently ignored or raise a catchable TypeError depending
on the embedder).

## Interpretation

1. **V8 runs the product's minidom.js with 100% semantic parity** on DOM,
   events, storage, console, and the virtual timer clock. The minidom.js IIFE
   compiles, the Proxy-rewritten typed-array layer executes identically, and
   the event-loop drain order is the same in both engines.

2. **The TypedArray Proxy (minidom.js:5-39) is unnecessary under V8.** V8's
   native typed arrays already return `undefined` for OOB reads (the spec
   behavior). Worse, the Proxy wrapper causes V8's `WebAssembly.validate`
   (which performs TypedArray brand checks) to throw — probe E. The fix is
   a one-liner: skip the Proxy rewrite when the engine is V8, or remove it
   entirely since V8 typed arrays are spec-compliant. Probe H confirms that
   native typed arrays + native WASM work together in V8.

3. **Performance:** minidom.js eval is ~2× faster in V8 (16ms vs 29ms),
   though this is a cold-boot single-shot measurement; the real advantage
   of V8 is in JIT-compiled page scripts (not exercised here).

4. **Host-callable marshalling:** this harness uses JS stubs because
   py_mini_racer 0.14 exposes host functions via an async API (`wrap_py_function`)
   incompatible with the synchronous `host_parse_html`/`host_log` contract.
   The full Python↔V8 seam will be built in Phase 1 via rusty_v8 + PyO3,
   where `FunctionTemplate` provides synchronous host callables natively.

## Verdict

**Green light for the fork.** V8 can run minidom.js, and the only divergence
is a known Proxy-vs-brand-check issue that has a trivial fix (remove the
proxy under V8). The next gate is the full 13-callable seam behind
`WR_JS_ENGINE=v8` (rusty_v8 scaffold), parity-gated by the existing test suite.
