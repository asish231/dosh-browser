# WASM Scope — what we add, what we refuse, and why

> Scope document for adding a WebAssembly implementation to `webresearch`.
>
> Governing rules (AGENTS.md): this project builds a browser from scratch. No
> Chromium, no CDP. A capability gap is a bug report against our own engine,
> never a reason to reach for someone else's. **Complexity never justifies a
> shortcut**, so the interpreter is hand-rolled and inspectable — exactly like
> `engine/ws.py` (hand-rolled RFC 6455, 349 lines, no third-party library).
>
> Explicitly forbidden: `wasmi`, `wasmtime`, `wasm3`, `wasmer`, or any vendored
> WASM engine. Using one would be the same category of mistake as embedding
> Chromium.

Status: **scoping** — nothing in this document is implemented yet.

---

## 1. Why we are doing this

The engine currently **fakes** WebAssembly. `webresearch/engine/minidom.js:4294-4298`:

```js
global.WebAssembly = global.WebAssembly || {
  instantiate: function () { return Promise.resolve({ module: {}, instance: { exports: {} } }); },
  compile: function () { return Promise.resolve({}); },
  validate: function () { return false; }
};
```

Measured behaviour (probed live, not inferred):

| Call | Actual result |
| --- | --- |
| `typeof WebAssembly` | `"object"` — feature detection **passes** |
| `WebAssembly.validate(bytes)` | always `false` |
| `WebAssembly.compile(bytes)` | resolves `{}` — an empty module object |
| `WebAssembly.instantiate(bytes)` | resolves `{module:{}, instance:{exports:{}}}` — **fake success** |
| `exports.solve()` after that | `TypeError: not a function` |
| `WebAssembly.Module` / `Memory` / `Table` / `Global` | `undefined` |
| `compileStreaming` / `instantiateStreaming` | `undefined` |

The failure mode is the worst possible one: a page's `.then()` runs, the UI
paints **"challenge ready"**, and the crash arrives later at the first export
call. That is an unavailable capability **appearing successful** — the one thing
AGENTS.md forbids outright.

### What a real implementation buys

Ranked by value to an agent browser:

| # | Class | Today | With WASM |
| --- | --- | --- | --- |
| 1 | **Anti-bot proof-of-work** — Cloudflare Turnstile/challenge, AWS WAF, Kasada, DataDome, Imperva, PerimeterX, Akamai | Token never computed → wall stays | PoW computes the real token → page unblocks |
| 2 | **WASM-hashed logins** — Argon2, SRP/PAKE, wallet key derivation | Derive call throws → login dies mid-flow | Hash computes → `browser_login` can reach `verified` |
| 3 | **Request-token derivation** — per-request `X-*` signature | Token throws → fetches go out unsigned → 403 wall | Header computed → navigation works |
| 4 | **WASM-rendered DOM** — Yew/Leptos, Blazor, SQLite-WASM, DuckDB-WASM, protobuf decoders | Renders empty; failure visible only in JS error log | Module hydrates → real content |
| 5 | **Honesty floor** (Tier H alone) | Fake success | Blocked pages fail **loudly and attributably** |

---

## 2. The critical framing: three layers, only one is WASM

Anti-bot is often described as one problem. It is three separate subsystems, and
conflating them is how projects conclude "wasm didn't help."

| Layer | Owner | WASM share |
| --- | --- | --- |
| **1. Human-like behaviour** — pointer trajectories, keystroke cadence, scroll velocity, dwell, click error | **Engine** (`trajectory`, stealth mode, `__advanceClock`) | **0%.** WASM cannot make input look human; that is geometry and timing |
| **2. Signal hashing / token derivation** — the opaque bundle | **WASM interpreter** | **100%.** `token = wasm(signals)` |
| **3. Token storage & sharing** — cookies, localStorage, cross-tab | **Session** (cookie jar; localStorage broadcast, round 7) | **0%.** Already solved |

**Corollary that governs the build order:** a perfect interpreter still fails if
the *signals* are implausible. The module hashes **our own fingerprint**, and the
server checks whether it looks like a real browser. WASM is **necessary but not
sufficient** — see §9.

---

## 3. Scope: what goes in

### 3.1 Binary format (decoder + validator)

All sections of the core spec, plus `custom` (read and ignore safely):

| Id | Section | Notes |
| --- | --- | --- |
| 0 | `custom` | Name/producers sections — parse and ignore, never error |
| 1 | `type` | Function types, multi-value result vectors |
| 2 | `import` | func / table / memory / global, from any module name |
| 3 | `function` | Function ↔ type index |
| 4 | `table` | `funcref` (+ `externref` if cheap) |
| 5 | `memory` | limits: `min ≤ max`, pages ≤ 65536 |
| 6 | `global` | const/mut, init expressions |
| 7 | `export` | name → func/table/memory/global |
| 8 | `start` | runs during instantiation |
| 9 | `element` | active/passive segments, `funcref` |
| 10 | `code` | locals + expression bodies; count must match §3 |
| 11 | `data` | active/passive segments |
| 12 | `datacount` | required by bulk-memory validation |

Primitives: LEB128 (`u32`/`s32`/`s64`), IEEE-754 little-endian, UTF-8 names,
section-order validation, duplicate-section rejection, trailing-byte rejection.

### 3.2 Instruction set

The **full MVP core** — ~172 opcodes. Grouped for reference:

| Group | Content |
| --- | --- |
| Control | `unreachable`, `nop`, `block`, `loop`, `if`/`else`, `end`, `br`, `br_if`, `br_table`, `return`, `call`, `call_indirect` |
| Parametric | `drop`, `select` |
| Variables | `local.get/set/tee`, `global.get/set` |
| Memory | `i32/i64/f32/f64.load`, `load8_s/u`, `load16_s/u`, `i64.load32_s/u`, matching stores, `memory.size`, `memory.grow` |
| Constants | `i32.const`, `i64.const`, `f32.const`, `f64.const` |
| Comparisons | `eqz`, `eq`, `ne`, `lt/gt/le/ge` in `_s`/`_u` for integers, full set for floats |
| Integer arithmetic | `clz`, `ctz`, `popcnt`, `add`, `sub`, `mul`, `div_s/u`, `rem_s/u`, `and`, `or`, `xor`, `shl`, `shr_s/u`, `rotl`, `rotr` |
| Float arithmetic | `abs`, `neg`, `ceil`, `floor`, `trunc`, `nearest`, `sqrt`, `add`, `sub`, `mul`, `div`, `min`, `max`, `copysign` |
| Conversions | `wrap`, `extend_s/u`, `trunc_f*_s/u`, `convert_*`, `demote`, `promote`, `reinterpret` |

### 3.3 Post-MVP features we **must** accept (LLVM emits them by default)

This is the single most important scope correction. "MVP only" would **reject
real Rust and clang output at validation**, because modern LLVM emits these
unconditionally:

| Feature | Opcodes | Why required |
| --- | --- | --- |
| **Sign-extension** | `i32.extend8_s`, `i32.extend16_s`, `i64.extend8_s/16_s/32_s` | LLVM emits for narrow integer loads |
| **Non-trapping float→int** | `i32/i64.trunc_sat_f32/f64_s/u` | LLVM emits saturating conversions by default |
| **Bulk memory** | `memory.copy`, `memory.fill`, `memory.init`, `data.drop`, `table.copy/init/grow/size/fill`, `elem.drop` | Struct copies, initialization |
| **Multi-value** | blocks and functions with multiple results | Rust `()`-returning closures, LLVM tuples |
| **Mutable globals** | exported/imported mutable globals | Stack pointer, allocator state |
| **Reference types** | `ref.null`, `ref.is_null`, `ref.func`, `funcref` tables | wasm-bindgen glue, indirect calls |

All six are small additions — no parallelism, no new type system.

---

## 4. Scope: what is explicitly **out**

| Feature | Verdict | Reason |
| --- | --- | --- |
| **Threads / atomics / `SharedArrayBuffer`** | **OUT** | Real parallelism; QuickJS is synchronous by design. Nothing in our target set needs it |
| **SIMD (128-bit)** | **OUT** | ~236 extra opcodes; not enabled by default in rustc/clang |
| **GC / `stringref` / `structref`** | **OUT** | New type system; no target page ships it |
| **Exception handling (tags)** | **OUT** | Wasm EH proposal; C++ targets that need it are behind other walls |
| **JSPI / tail calls** | **OUT** | Not default-emitted |
| **Component model** | **OUT** | Not a browser-page thing |
| **WASI filesystem / sockets** | **OUT** (tiny subset maybe) | Pages don't have a filesystem. If a Tier-R target demands it, implement *only* `random_get`, `clock_time_get`, `fd_write` |
| **`WebAssembly.Module` serialization (`postMessage` transfer)** | OUT | No cross-realm wasm in this engine |
| **Streaming compile from a real `Response` body stream** | Partial | `instantiateStreaming(url)` fetches and (optionally) checks `application/wasm`, then uses the buffer path |
| **Debugging (name-section-driven traces)** | OUT | Name section is stored for error messages only |

### Why not the whole spec — the area under the curve

| Tier | Content | Est. lines | Unlocks |
| --- | --- | --- | --- |
| **H** — honesty only | Real `validate()`; `instantiate` throws instead of fake-succeeding | **~50** | Nothing functionally; kills the lie |
| **U** — the unlock subset | §§3.1–3.3 | **~1,300** | Anti-bot PoW, WASM-hashed logins, request-token derivation |
| **R** — DOM-producing | U + WASI micro-subset, larger memory, more glue imports | **~2,200** | Yew/Leptos/Blazor, SQLite-WASM, DuckDB-WASM |
| **F** — full 3.0 | Threads, SIMD, GC, EH, components | 15,000+ | **Nothing readable** — canvas wall |

We build **H then U**. **R** only if a concrete page demands it. **F** never.

---

## 5. The JS API surface we must provide

Currently absent or stubbed — all of these must become real:

| API | Requirement |
| --- | --- |
| `WebAssembly.validate(bytes)` | Honest parse+validate verdict. `false` for malformed; `true` for a real module |
| `WebAssembly.compile(bytes)` | `Promise<Module>`; `CompileError` on bad bytes |
| `WebAssembly.instantiate(bytesOrModule, imports)` | `Promise<{module, instance}>`; `LinkError` on missing/mistyped imports; runs `start` |
| `WebAssembly.compileStreaming(src)` / `instantiateStreaming(src, imports)` | Fetch → MIME check → same paths |
| `WebAssembly.Module` | `.exports`, `.imports`, `.customSections()` (name section at minimum) |
| `WebAssembly.Instance` | `.exports` with callable functions, live `Memory`/`Table`/`Global` |
| `WebAssembly.Memory({initial, maximum})` | `.buffer` (a real, JS-readable `ArrayBuffer`), `.grow(delta)` → new size or `RangeError` |
| `WebAssembly.Table({element, initial, maximum})` | `.length`, `.get(i)`, `.set(i, fn)`, `.grow(n)` |
| `WebAssembly.Global({value, mutable}, v)` | `.value`, `.valueOf()`; writes to immutable throw `TypeError` |
| `WebAssembly.RuntimeError` | Trap class — division by zero, OOB memory, `unreachable`, indirect-call type mismatch |
| `WebAssembly.CompileError` / `LinkError` | Distinct from `RuntimeError`, per spec |

**Marshaling rules:** `i32` ↔ JS `Number` (int32 truncation), `i64` ↔ **`BigInt`**,
`f32`/`f64` ↔ `Number`, memory/globals exported as the class instances above, JS
functions importable as wasm function pointers.

---

## 6. Semantics that must be exactly right

Breadth is not the risk. **These edges are the risk** — each one gets a
hand-encoded regression binary:

| Rule | Correct behaviour |
| --- | --- |
| Integer wrap | `i32`/`i64` arithmetic wraps modulo 2³²/2⁶⁴ |
| `div_s` overflow | `INT_MIN / -1 == INT_MIN`, **must not trap** |
| `rem_s` overflow | `INT_MIN % -1 == 0`, **must not trap** |
| Division by zero | `div_s/u`, `rem_s/u` by 0 → **trap** |
| Shift masking | `i32.shl` masks count by 31; `i64` by 63; rotates likewise |
| Bit counts | `clz(0) = 32/64`, `ctz(0) = 32/64`, `popcnt(0) = 0` |
| Float→int trunc | NaN / ±Inf / out-of-range → **trap**; saturating variant clamps instead |
| `f32` precision | Every intermediate `f32` op rounds to single precision, not double |
| `min`/`max` | NaN propagation; `min(-0, +0) = -0`, `max(-0, +0) = +0` |
| `nearest` | Round-half-to-**even** |
| Memory access | Out-of-bounds load/store → **trap**, never a silent zero. Alignment hints ignored |
| `memory.grow` | Returns previous page count, `-1` on failure; never exceeds declared max |
| `call_indirect` | Type mismatch → **trap**; uninitialised table slot → **trap** |
| `br_table` | Out-of-range index takes the **default** label |
| `unreachable` | **Trap** |
| `start` | Runs during instantiation, before `instantiate` resolves |
| Validator | Full stack-typed checking, including **stack-polymorphic** state after `unreachable`/`br` |

---

## 7. Architecture

```
webresearch/engine/wasm/
  __init__.py      # public API + backend selection
  py_backend.py    # hand-rolled decoder, validator, stack-machine interpreter
  rs_backend.py    # OPTIONAL: native accelerator, loaded only if importable
tests/test_wasm_*.py   # backend-parametrized conformance corpus
```

### We write Python. We never write WebAssembly.

This is the single most-misread part of the plan, so state it plainly:

| Thing | Who makes it | In this project |
| --- | --- | --- |
| **`token.wasm` bytes** | the *page's* author (rustc, emscripten, AssemblyScript) | **input only** — we receive it over HTTP and parse it |
| **The interpreter** — decoder, validator, stack machine | **us** | **Python** (`py_backend.py`) |
| **The optional accelerator** | us, for the closed build | **Rust → a native Python extension** (`.so`/`.dylib` via PyO3), **not** compiled to wasm |

There is **zero WebAssembly emitted anywhere in this repo.** We are not a wasm
toolchain, we have no `wat` parser, no `wasm-opt`, no `wasm-bindgen`. The
relationship is exactly the one CPython has with Python: CPython is written in C
and *runs* Python source; `py_backend.py` is written in Python and *runs* wasm
bytecode. An interpreter is written in some other language by definition — if a
file in this project ever ended in `.wasm`, that would be a bug.

Practically: a page contains `await WebAssembly.instantiateStreaming(fetch("/w.wasm"), env)`.
Today the stub hands it `{exports:{}}`. After this work, the bytes arrive, `minidom.js`
calls `host_wasm_instantiate`, and Python decodes and executes them. The page
never learns the difference.

### Backend-neutral interface (built from day one)

Two lanes, one contract — and the open lane needs **no Rust, ever**:

| | Open release | Closed binary |
| --- | --- | --- |
| Interpreter | Pure Python, always present | Native, prebuilt |
| User needs | `pip install webresearch` | Nothing installed — shipped inside the binary |
| Rust toolchain | **never** | build-time only |

Precedent already in the tree — the optional native accelerator pattern from
`webresearch/fetch.py:27-29`:

```python
try:
    from curl_cffi import requests as _curl_requests   # optional compiled native module
except ImportError:
    _curl_requests = None                               # graceful pure-Python fallback
```

`WebAssembly` in `minidom.js` is replaced by a **bridge**, not an interpreter.
Host callables, shaped like the existing `host_fetch`:

`host_wasm_validate`, `host_wasm_compile`, `host_wasm_instantiate`,
`host_wasm_call`, `host_wasm_memory_grow`, `host_wasm_global_get/set`

### The one design rule that decides performance

**One host crossing per exported call — never per instruction.** A module is
decoded once into a host-side instruction array; `exports.solve(a, b)` marshals
arguments, runs the whole loop host-side, returns the result. Imports that reach
back into page JS use **re-entrant evaluation** — verified working in this engine
(`JS → host → JS → host`, nested, no failure).

### Lifetime (the trap that already bit us once)

An SPA instantiates a module **once at load** and calls exports on every
interaction afterwards. Instances therefore must survive across action passes,
keyed per tab/context. This is the same lifetime discipline as history state —
and `runtime.py` has already had one stale-counter bug of exactly this family.

---

## 8. Phases and test gates

Each phase lands verified and tested before the next — the discipline used for
the five issue rounds.

| Phase | Deliverable | Gate |
| --- | --- | --- |
| **H** | Real `validate()`; `instantiate` throws `wasm_blocked` instead of fake success; keep fake-success **only** behind an explicit flag if any legacy test needs it | ~50 lines; stub behaviours removed from the API surface |
| **A** | Decoder + validator (§3.1), accepting §3.3 features | Accepts a real Rust-emitted module; rejects truncated/bad-LEB/misordered sections |
| **B** | Interpreter core: i32/i64, control flow, locals, globals, memory, direct calls, traps | fib/fact recursion; memory round-trips; every §6 edge |
| **C** | `minidom.js` bridge: real classes, `instantiate`/`instantiateStreaming`, export marshaling, instance registry | Session-level: page instantiates and calls an export; result reaches the DOM |
| **D** | f32/f64, `br_table`, `call_indirect`, remaining edges | Full §6 conformance corpus green |
| **E** | Imports: native `env.*`, then re-entrant JS callbacks (wasm-bindgen shape) | Imported-function round trip; JS callback invoked from inside wasm |
| **F** | Docs: P2-2 → implemented-with-disclosures; AGENTS.md capability row; suite + commit | Full suite green, zero regressions |

### Test corpus (the real asset)

**Hand-encoded module binaries** — we emit opcodes directly in Python, no `wat`
tooling, mirroring the RFC 6455 frame tests:

- header/section corruptions, bad LEB128, trailing bytes, duplicate sections
- every §6 edge as its own binary with an exact expected value or trap type
- memory widths and bounds; `grow` semantics; `ArrayBuffer` visibility from JS
- control-flow matrices: nested `block`/`loop`/`if`, `br_table` defaults
- `call_indirect` typing and null-slot traps
- import round-trips, including a JS callback invoked from the interpreter
- backend-parametrized, so the Rust accelerator must pass the **same** corpus

---

## 9. Companion work — WASM alone is not enough for anti-bot

The token is `wasm(our_signals)`. If the signals are missing or impossible, the
hash is consistent but **implausible**, and modern detection flags exactly that.
Gaps found by probing the engine today:

| Signal | State | Impact |
| --- | --- | --- |
| `crypto.getRandomValues` | **`Math.random()`** | Predictable entropy; a fingerprint tell no real browser emits |
| `crypto.subtle.digest` unknown algorithm | **silently returns SHA-256** | Wrong hash, no error — a correctness bug of the fake-success family |
| `crypto.subtle.encrypt/decrypt/exportKey` | **empty `ArrayBuffer`** | Page believes it encrypted; produced nothing |
| `crypto.subtle.sign/verify/deriveKey/deriveBits/generateKey` | **absent** | HMAC, PBKDF2, ECDSA, Ed25519 flows die |
| `OfflineAudioContext` | **absent** | Audio fingerprint probe throws |
| `RTCPeerConnection` | **absent** | Its absence is itself a signal |
| WebGL renderer/vendor strings | stub only | Read by Cloudflare/DataDome sensor |
| `navigator.credentials` | **absent** | Passkeys fail (not a WASM problem) |

**Recommended ordering so progress is measurable at every step:**

1. **Crypto P0** — honest throws + `secrets`-backed RNG (~1 hour, removes silent lies)
2. **Crypto P1** — host primitives: HMAC, PBKDF2, scrypt, SHA3/BLAKE2, keccak, AES-GCM, RSA/ECDSA/Ed25519 (~1 day; covers ~80% of auth/crypto with **no WASM**)
3. **Tier H** — honest `validate`/`instantiate` (~50 lines)
4. **Fingerprint input completeness** — `OfflineAudioContext`, WebGL strings, `RTCPeerConnection`, permissions consistency
5. **Tier U (phases A–E)** — the interpreter
6. **Module lifetime + token rotation** — instances persist across passes

---

## 10. Honest disclosures this will carry

| Disclosure | Detail |
| --- | --- |
| **Speed** | A Python interpreter is ~20–100× slower than a native wasm JIT. Anti-bot PoW (10–100 ms native) becomes ~0.5–5 s — acceptable for an action-pass budget; heavy Tier-R modules may be too slow and will be reported as such |
| **No threads / SIMD / GC / exceptions / JSPI** | Out per §4; pages needing them fail with a clear `CompileError`, not silently |
| **Memory cap** | Engine-enforced ceiling (planned 256 MB) with an honest failure rather than OOM |
| **No `SharedArrayBuffer` memory** | `shared: true` in `Memory` descriptors is unsupported |
| **Not a JIT** | No tiering, no inlining; correctness first |
| **No wasm-side debugging** | Traps report function index/offset where available; no source maps |

---

## 11. Non-goals (reaffirmed)

- **No vendored WASM engine.** `wasmi`, `wasmtime`, `wasm3`, `wasmer` are
  reference material at most — never imported, linked, or shipped.
- **No Chromium/CDP lane** — ever, for any reason.
- **No canvas unlocking.** Games, Figma, Photopea, ffmpeg.wasm, WASM PDF
  renderers, WebGL charts, emulators all paint to pixels; the interpreter does
  not change what the agent can read.
- **No cryptominer execution.** Failing those is correct behaviour.

---

## 12. Decisions needed

| # | Decision | Options | Recommendation |
| --- | --- | --- | --- |
| 1 | Interpreter language | Pure Python / Rust extension | **Python**, per the `ws.py` precedent; Rust stays a closed-build accelerator behind the same interface |
| 2 | Tier target | H only / H+U / H+U+R | **H then U**; R on demand |
| 3 | Memory ceiling | 64 / 256 / 1024 MB | **256 MB**, disclosed |
| 4 | `instantiate` on unsupported feature | throw `CompileError` / `wasm_blocked` marker / stub-success | **`CompileError`**; the `wasm_blocked` marker survives only as an engine-level note the agent can read |
| 5 | Argon2 | WASM (Tier U) / `argon2-cffi` dependency | **WASM** — one interpreter beats a new native dependency |
| 6 | Streaming API depth | URL fetch only / full `Response` stream | **URL fetch**; reading `application/wasm` from a live stream is deferred |

---

## 13. Definition of done

- `WebAssembly.validate` returns honest verdicts on real and corrupt modules.
- A page that instantiates a module and calls an export **works end to end**
  through the session, with the result reaching the DOM.
- Every §6 semantic edge has a passing hand-encoded regression.
- Traps surface as `RuntimeError`; bad bytes as `CompileError`; bad imports as
  `LinkError`. **No path returns a fake success.**
- Instances survive across action passes (SPA contract).
- The optional native backend, when present, passes the identical corpus.
- `OPEN_ISSUES.md` P2-2 moves from *disclosed boundary* to *implemented, with
  disclosures*; `AGENTS.md` gains a WASM capability row stating exactly §10.
- Full suite green with zero regressions.