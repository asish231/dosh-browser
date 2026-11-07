# Dosh Loom — Agent-Native Session Daemon

## The problem

dosh-browser's MCP stdio server is a **child process** of the client (opencode).
When the client disconnects, restarts, or crashes, the MCP process dies — and all
in-memory browser state (page, DOM, cookies, storage, live JS context) is gone.

This is the exact problem tmux solves for shells: a persistent server owns the
session; clients attach/detach without killing it.

But tmux/dtach/shpool all manage **terminal/PTY sessions**. dosh-browser manages
**browser/agent sessions** (DOM, cookies, storage, JS context, action refs).
That's a different problem — and the AGENTS.md directive says: *invent it, implement
it natively, test it. Do not fake anything.*

## What exists already

| Component | Status | Location |
|---|---|---|
| WebSocket attach bridge (`/v1/attach`) | ✅ Built (Rust) | `dosh-browser-core/src/server.rs` |
| Session registry (create/list/attach) | ✅ Built (Rust) | `dosh-browser-core/src/session.rs` |
| Checkpoint/restore (DOM/storage/cookies) | ✅ Built (Python) | `webresearch/shell.py` |
| `WR_SESSION_DB` persistence | ✅ Built (this session) | `webresearch/shell.py`, `webresearch/mcp.py` |
| MCP auto-checkpoint-on-nav + restore-on-startup | ✅ Built (this session) | `webresearch/mcp.py` |
| Daemon mode (persistent background process) | ❌ Missing | — |
| Unix socket transport | ❌ Missing (uses TCP) | — |
| TTL/LRU session eviction | ❌ Missing | — |
| Event journal per session (detached activity) | ❌ Missing | — |
| Python MCP thin-client adapter | ❌ Missing (MCP is the server, not a client) | — |

## The invention: Dosh Loom

A **persistent background daemon** that owns browser sessions and lets clients
(MCP, CLI, REST, agent swarms) attach/detach without killing them.

```
┌──────────────────────────────────────────────┐
│  Dosh Loom Daemon (persistent, background)     │
│                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │ Session A │ │ Session B │ │ Session C │      │
│  │ (live)    │ │ (live)    │ │ (evicted) │      │
│  │ page+DOM  │ │ page+DOM  │ │ checkpoint│      │
│  │ cookies   │ │ cookies   │ │ only      │      │
│  │ JS ctx    │ │ JS ctx    │ │           │      │
│  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘      │
│        │             │             │             │
│  ┌─────▼─────────────▼─────────────▼───────────┐│
│  │  Session Registry                            ││
│  │  • LRU + TTL eviction (low memory)          ││
│  │  • Checkpoint store (SQLite)                 ││
│  │  • Event journal (causal log)               ││
│  └─────────────────────────────────────────────┘│
│        ▲                                       │
│        │ Unix Socket (~/.dosh/loom.sock)       │
└────────┼───────────────────────────────────────┘
         │
    ┌────┴──────┬──────────┬──────────┬──────────┐
    │           │          │          │          │
 ┌──▼───┐ ┌────▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐
 │ MCP  │ │ CLI     │ │ REST   │ │ Agent  │ │ Agent  │
 │ thin │ │ client  │ │ client │ │ swarm  │ │ swarm  │
 │client│ │         │ │        │ │  #1    │ │  #2    │
 └──────┘ └─────────┘ └────────┘ └────────┘ └────────┘
```

### How it differs from tmux/dtach/shpool

| Aspect | tmux/dtach/shpool | Dosh Loom |
|---|---|---|
| What persists | Terminal/PTY state (vt100) | Browser state (DOM/cookies/storage/JS ctx) |
| State replay on reattach | vt100 terminal snapshot | Browser checkpoint (DOM tree + storage + cookies + URL) |
| Live state survives detach | ✅ (process stays alive) | ✅ (QuickJS context stays alive in daemon) |
| Process death recovery | ❌ (server crash = lost) | ✅ (checkpoint to SQLite → restore on daemon restart) |
| Memory management | Fixed buffers | LRU + TTL eviction (idle sessions checkpointed + evicted) |
| IPC | Unix socket | Unix socket (same, but framed JSON not imsg) |
| Multi-client | ✅ (tmux) / ❌ (shpool) | ✅ (multiple agents attach to same session) |
| Event journal | ❌ | ✅ (causal: action → DOM delta → network → console) |

### The agent-native innovation

1. **Checkpoint + live hybrid**: if the daemon is alive, reattach gives you the
   live JS context (nothing lost). If the daemon restarted, reattach restores
   from the last checkpoint (DOM/storage/cookies — live JS context honestly
   NOT restored, documented). This is the shpool "in-memory render + replay"
   pattern translated to browser state.

2. **TTL + LRU eviction**: idle sessions (no client attached for N minutes) are
   checkpointed to SQLite and their QuickJS context is dropped. Memory stays
   low. On reattach, the checkpoint is restored and a fresh context is built.
   Default TTL: 30 min. Default max live sessions: 8 (matches the Rust
   `SessionPool` LRU cap in STATE-WEAVE-PLAN).

3. **Event journal**: each session has a causal event log (action → DOM delta
   → network request → console message). On reattach, the agent can query
   "what happened while I was detached?" — the journal survives in SQLite.

4. **Unix socket transport**: `~/.dosh/loom.sock` — zero-latency local IPC,
   no TCP overhead, no port conflicts. The MCP stdio adapter connects to this
   socket as a thin client; the CLI does the same.

5. **MCP becomes a thin client**: instead of the MCP server owning the session,
   it connects to the daemon. The MCP process can die/restart freely — the
   session lives in the daemon. This is the core fix for the "kill and restart"
   problem.

## Review updates based on codebase audit

### Verified path conventions
- `~/.dosh` already used in `device.py:32` — consistent with using `~/.dosh/loom.sock`
- `~/.dosh-browser/profiles` for profile data — separate namespace

### Verified concurrency patterns
- `webresearch/shell.py:410` uses `asyncio.Lock()` — consistent approach
- Rust `Mutex<T>` used in `session.rs` — Rust side covered

### Secret hygiene already strong
- Cookies values never echoed in output (shell.py:70)
- Already using `_redact` utility (shell.py:24)
- Will maintain these practices

### Gaps identified in review:
1. **No existing daemonize/auto-spawn patterns** in Python — must introduce first time
2. **No Unix socket support** in existing server (only TCP via FastAPI/Uvicorn)
3. **Rust session.rs needs explicit SessionRegistry** — current code has Session/Page but no global registry with LRU/TTL
4. **Event journal table schema** needs design before implementation
5. **MCP thin-client needs new module** (not rewrite of existing mcp.py)

## Phased implementation plan

### Phase 1 — Unix socket daemon (Python, low-risk) ✅ DONE
- Add `webresearch serve --daemon` mode: binds Unix socket at `~/.dosh/loom.sock`
- Session registry: `dict[session_id → Session]` with LRU + TTL
- Thin protocol: JSON frames over the socket (`attach`, `command`, `detach`, `list`)
- On attach: restore from checkpoint if session was evicted
- On detach: start TTL countdown; on expiry, checkpoint + drop context
- **Test**: daemon survives client disconnect; reattach restores state

### Phase 2 — MCP thin-client adapter ✅ DONE
- Create `webresearch/mcp_daemon_client.py` (thin client connecting to daemon socket)
- Rewrite `webresearch mcp` command to use thin client instead of owning session
- MCP process becomes stateless: every tool call → `attach(session_id) → command → detach`
- Session persists in daemon across MCP restarts
- **Test**: kill MCP process; restart; session state intact

### Phase 3 — CLI thin-client ✅ DONE
- Add `webresearch attach <session_id>` — interactive attach to named session
- Add `webresearch sessions` — list live + evicted sessions
- Existing commands connect to daemon socket if running
- **Test**: CLI attaches to same session as MCP; state shared

### Phase 4 — Event journal ✅ DONE
- Add `sessions_journal` SQLite table: `(session_id TEXT, timestamp REAL, kind TEXT, payload TEXT)`
- Journal entries on: navigation, click/type/actions, DOM changes, network events, errors
- **Test**: detach; trigger network activity; reattach; journal shows events

### Phase 5 — Rust lane integration ✅ DONE
- Wire `dosh-browser-core/src/server.rs` attach bridge to same Unix socket
- Rust sessions and Python sessions share registry via socket protocol
- **Test**: Rust client + Python client attach to same session

## Implementation status (all phases complete)

| Phase | Delivered | Evidence |
|---|---|---|
| 1 — Unix socket daemon | `webresearch/loom.py` (`SessionRegistry`, `LoomDaemon`), `serve --daemon --socket-path --max-live --ttl` | `tests/test_loom.py` — 11 passed |
| 2 — MCP thin-client | `webresearch/mcp_daemon_client.py`, `_handle_via_loom` in `webresearch/mcp.py` | `tests/test_mcp_thin_client.py` — 6 passed |
| 3 — CLI thin-client | `webresearch attach <id>`, `webresearch sessions` in `webresearch/cli.py` | `tests/test_cli_thin_client.py`; live E2E attach/sessions verified |
| 4 — Event journal | `webresearch/loom_journal.py` (`sessions_journal`), recording in daemon dispatch, `webresearch journal [--session]` | `tests/test_cli_thin_client.py` (journal record/retrieve/filter/in-daemon); live `journal` verified |
| 5 — Rust lane integration | `dosh-browser-core/src/daemon_client.rs` (persistent `Session` + `read_journal`), `dosh attach/sessions/journal` | `cargo test -p dosh-browser-core` — 251 passed; live cross-lane: Rust `sessions`/`journal`/`attach` see Python session |

Verification runs:
- Python: `pytest` across loom/mcp/cli/shell/engine/deadline suites → **194 passed**
- Rust: `cargo test -p dosh-browser-core` → **251 passed**
- Cross-lane (live): Python creates `py_session_1` → Rust `dosh sessions` lists it, `dosh journal` shows its journal, `dosh attach` runs `status` in it.

Known limitation: heavy-JS sites (e.g. `build.nvidia.com` and the `nvidia.com` homepage) exceed the JS memory/time budget and time out; a lighter NVIDIA page (`nvidia.com/en-us/about-nvidia/`) loads in ~70 s at a 256 MB cap. This is a page-cost limit, not a daemon defect.

## Memory budget

| Component | Memory per session | Notes |
|---|---|---|
| QuickJS context (live) | ~2-10 MB | Depends on page complexity; memory cap at 512 MB (env: `WR_JS_MEMORY_LIMIT_MB`) |
| DOM tree (parsed) | ~100 KB - 1 MB | selectolax tree |
| Cookies + storage | ~10-50 KB | JSON in SQLite |
| Event journal | ~10-100 KB/turn | Bounded ring buffer in SQLite |
| Checkpoint (evicted) | ~100 KB - 1 MB | DOM HTML + JSON state in SQLite |

**8 live sessions × ~10 MB = ~80 MB** — well within agentic memory budget.
Evicted sessions cost only SQLite disk space (~1 MB each).

## Speed budget

| Operation | Target | Notes |
|---|---|---|
| Attach (live session) | < 5 ms | Unix socket + session lookup |
| Attach (evicted session) | < 50 ms | SQLite restore + fresh QuickJS context (14 ms boot) |
| Command round-trip | < 1 ms overhead | Unix socket + JSON frame |
| Detach | < 1 ms | Start TTL timer |

## What this is NOT

- ❌ Not a PTY multiplexer (no terminal rendering, no vt100)
- ❌ Not a process manager (doesn't manage shells or subprocesses)
- ❌ Not a network service (Unix socket, local only — like tmux)
- ❌ Not wrapping tmux/dtach externally (native implementation, per AGENTS.md "invent")

## Relationship to existing work

- **STATE-WEAVE-PLAN Phase A** (`SessionPool`, `watchdog`): the LRU cap (8) and
  watchdog deadline are the daemon's eviction + safety mechanisms.
- **`/v1/attach` WebSocket bridge** (Rust `server.rs`): the daemon's Python
  implementation mirrors this protocol; Phase 5 unifies them.
- **`WR_SESSION_DB` checkpoint/restore** (this session): the daemon's
  checkpoint store uses the same SQLite schema.
- **`DOSH-SESSION-FINDINGS-EXACT.md` finding #4**: "REST and stdio MCP each
  own one global session" — the daemon fixes this by being the single session
  authority that all adapters connect to.
