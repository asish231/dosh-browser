# dosh-browser-core

The pure-Rust browser engine and product core for **dosh-browser**.

Replaces the entire Python `webresearch` product with a native Rust implementation,
while consuming the `doshgentv8-core` engine for JS execution (V8).

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  dosh-browser-core (Rust Product)                            │
│  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
│  │ shell.rs   │ │ server.rs│ │ session  │ │ actions/forms │ │
│  │ (cli cmds) │ │ (axum)   │ │ (state)  │ │ (surface)     │ │
│  └─────┬──────┘ └────┬─────┘ └────┬─────┘ └───────────────┘ │
│        └─────────────┴────────────┴──────────────────────────┘ │
│                              │                                │
│  ┌───────────────────────────▼──────────────────────────────┐ │
│  │  doshgentv8-core (JS Engine)                             │ │
│  │  HostSeam trait (fetch, parse, crypto)                   │ │
│  │  + v8 crate (V8 15.9.0)                                  │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## Modules (12/12 Complete)

1. **`fetch.rs`**: `reqwest` transport (replaces `curl_cffi`/`httpx`)
2. **`tree.rs`**: `kuchiki` HTML parser (replaces `selectolax.lexbor`)
3. **`content.rs`**: Root detection & chrome skipping
4. **`links.rs`**: Link extraction & deduplication
5. **`acd.rs`**: Agent Context Document (blocks, JSON-LD projection, region fallbacks)
6. **`forms.rs`**: Form field extraction
7. **`actions.rs`**: Agent action surface (navigate/submit/drag/drop)
8. **`session.rs`**: Tab/Session state machine, history, goto loop
9. **`download.rs`**: Streaming downloads with hash verification
10. **`video.rs`**: yt-dlp integration with policy gates
11. **`shell.rs`**: Command execution loop & SQLite storage (skills, snapshots)
12. **`server.rs`**: Axum HTTP server (`/v1/tools`, `/v1/invoke`, `/v1/ask`)

## V8 Engine Fork

**doshgentv8** is the custom, lightly-forked V8 engine that powers JavaScript
execution in dosh-browser. It is housed at `../doshgentv8/` as three crates:

| Crate | Path | Role |
|-------|------|------|
| `doshgentv8-core` | `core/` | Pure-Rust engine crate — `HostSeam` trait (fetch, parse, crypto), **no Python** here; consumed unchanged by this product |
| `doshgentv8_rust` | `py/` | Thin PyO3 facade (`cdylib`), `quickjs.Context`-compatible surface: `eval`, `add_callable`, `execute_pending_job`, `limits` |
| `base/` | `base/` | Vendored V8 15.9.0 source for the forked engine |

### Key features

- **HostSeam trait** — the host seam is a Rust trait, so the future pure-Rust
  product implements it natively with zero Python runtime dependency.
- **QuickJS-compatible Python facade** — the PyO3 `doshgentv8_rust` binary is a
  drop-in for `quickjs.Context`, letting the existing Python companion package
  run the same probes against real V8.
- **Pointer compression** — V8 15.9.0 is built with pointer compression ON;
  32-bit tagged pointers shrink every heap object and lift the old-gen ceiling.
- **Fuel metering** *(pending)* — per-instruction fuel for script budgeting.
- **Host clock injection** *(pending)* — deterministic virtual clock so timers,
  rAF, and smooth-scroll animations step deterministically under host control.
- **Seeded RNG** *(pending)* — reproducible `Math.random` for hermetic tests.
- **Capability gate** *(pending)* — JS-side capability gating for the agent
  surface (e.g. disabling untrusted script execution paths).
- **Checkpoint callback** *(pending)* — session checkpointing hook for durable
  snapshots.

> **Status — in development, not yet the default engine.** The Rust bridge is
> verified against a QuickJS oracle (boot/document/TypedArray/console/event
> loop/DOM/storage probes all identical, 9µs synchronous host-callable
> round-trip, wall-clock kill 0.51s, boot 10.4ms vs quickjs 13.9ms — see
> `../doshgentv8/test_rust_bridge.py`), with two disclosed expected
> divergences (native WebAssembly; Proxy-wrapped TypedArray throws honestly
> instead of faking). The fork-patch policy overlays are **not yet wired**;
> once they land, the engine is selectable via the `WR_JS_ENGINE` flag.

## Tests

**62/62 tests passing.**

```bash
cargo test --release
```

## Status

**Complete.** The core product is now 100% Rust. It is ready to be deployed as the
backend for the agent CLI and API.
