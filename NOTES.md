# Dosh Loom Implementation Notes

## Gate 2 - Reconnaissance

### Existing infrastructure to reuse
- `webresearch/shell.py:ShellStore` — SQLite-backed checkpoint store (checkpoints table: id, url, title, html, cookies, storage, history). Already has save_checkpoint/get_checkpoint/list_checkpoints.
- `webresearch/session.py:Session` — the browser session (cookies, tabs, DOM, shell). Constructor: `Session(client, session_id, profile, tenant_id)`.
- `webresearch/session.py:Browser` — session factory with TTL eviction (ttl_seconds=1800, max_sessions=64). Already has `_evict()`.
- `webresearch/shell.py:DoshShell` — shell with `execute(command, args)` async method. Has `_lock = asyncio.Lock()`.
- `webresearch/config.py` — env helpers (js_memory_limit_mb, js_stack_limit_mb, js_engine).
- `~/.dosh` path convention from `webresearch/device.py:32`.

### Python Unix socket patterns
- `asyncio.start_server` doesn't support Unix sockets directly in all versions.
- Use `asyncio.open_unix_connection` (client) + `asyncio.start_unix_server` (server) — available in Python 3.12+.
- The project requires Python >=3.11 (pyproject.toml). `asyncio.start_unix_server` is available since 3.12. Need to check: does `asyncio.start_server` with `sock=` parameter work for AF_UNIX?
- Alternative: use `socket.socket(AF_UNIX, SOCK_STREAM)` + `asyncio.loop.create_unix_server`.

### Key API to verify
- `asyncio.start_unix_server` — check if available in Python 3.14 (the venv uses 3.14.7).
- Frame protocol: newline-delimited JSON is simplest and robust.

### Session lifecycle design
1. Client sends `{"op":"attach","session_id":"s_xxx"}` (or omit session_id for auto-create)
2. Daemon looks up session in registry:
   - If live: return attached, current url/title
   - If evicted (checkpoint exists): restore from SQLite, return restored
   - If new: create fresh Session, return new session_id
3. Client sends `{"op":"command","command":"open","arguments":{"url":"..."}}` frames
4. Daemon executes via `session.shell.execute(command, args)`, returns result
5. Client sends `{"op":"detach"}` or just disconnects
6. Daemon starts TTL countdown; on expiry, checkpoint + drop

### Concurrency
- Each session has its own `asyncio.Lock` (via DoshShell._lock) — commands serialize per session.
- The daemon's event loop handles multiple clients concurrently.
- Multiple clients can attach to the same session (shared mode) — commands queue on the session lock.
