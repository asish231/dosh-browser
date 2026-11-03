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

## Tests

**62/62 tests passing.**

```bash
cargo test --release
```

## Status

**Complete.** The core product is now 100% Rust. It is ready to be deployed as the
backend for the agent CLI and API.
