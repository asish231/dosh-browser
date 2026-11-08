"""Dosh Loom — agent-native session daemon.

A persistent background process that owns browser sessions and lets clients
(MCP, CLI, REST, agent swarms) attach/detach without killing them.

This is the agent-native equivalent of tmux's server-client model: instead of
managing terminal/PTY sessions, it manages browser sessions (DOM, cookies,
storage, JS context). Sessions survive client disconnects; on TTL expiry they
are checkpointed to SQLite and their QuickJS context is dropped; on reattach
the checkpoint is restored.

Protocol: newline-delimited JSON frames over a Unix socket.

    {"op":"attach","session_id":"s_xxx"}   → {"ok":true,"session_id":"s_xxx","url":"...","title":"..."}
    {"op":"command","command":"open","arguments":{"url":"..."}}  → {"ok":true,"result":{...}}
    {"op":"detach"}                        → {"ok":true}
    {"op":"list"}                          → {"ok":true,"sessions":[...]}
    {"op":"ping"}                          → {"ok":true,"pong":true}
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import secrets
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx

from webresearch.session import Session


def _default_socket_path() -> str:
    base = Path.home() / ".dosh"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "loom.sock")


def _default_db_path() -> str:
    base = Path.home() / ".dosh"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "loom.sqlite")


class SessionEntry:
    """One live browser session in the registry."""

    __slots__ = ("session_id", "session", "shell", "last_attach", "attached_clients", "store", "pressure_mark")

    def __init__(self, session_id: str, session: Session, store: Any = None):
        self.session_id = session_id
        self.session = session
        self.shell = session.shell  # lazily creates DoshShell
        self.last_attach = time.monotonic()
        self.attached_clients: int = 0
        self.store = store  # ShellStore for checkpoint/restore
        self.pressure_mark: int = 0  # >=1 = pressured (eviction-first)


class SessionRegistry:
    """LRU + TTL session registry with checkpoint-on-evict and restore-on-reattach.

    Sessions are kept live while attached. When a client detaches, a TTL countdown
    starts. On expiry, the session is checkpointed to SQLite and its QuickJS context
    is dropped (memory freed). On reattach, the checkpoint is restored.
    """

    def __init__(
        self,
        *,
        max_live: int = 8,
        ttl_seconds: float = 1800.0,
        db_path: str | None = None,
    ):
        self._sessions: OrderedDict[str, SessionEntry] = OrderedDict()
        self._max_live = max_live
        self._ttl = ttl_seconds
        self._db_path = db_path or os.environ.get("WR_LOOM_DB", _default_db_path())
        self._store: Any = None
        self._lock = asyncio.Lock()
        self._eviction_task: asyncio.Task | None = None

    def _get_store(self):
        if self._store is None:
            from webresearch.shell import ShellStore
            self._store = ShellStore(self._db_path)
        return self._store

    async def create(self) -> str:
        """Create a new session, return its id."""
        async with self._lock:
            sid = "s_" + secrets.token_hex(4)
            client = httpx.AsyncClient(follow_redirects=True)
            session = Session(client, sid)
            entry = SessionEntry(sid, session, self._get_store())
            self._sessions[sid] = entry
            self._evict_lru_locked()
            return sid

    async def attach(self, session_id: str | None = None) -> dict[str, Any]:
        """Attach to a session by id. If None or unknown, create new.
        If the session was evicted (checkpoint exists), restore it."""
        async with self._lock:
            if session_id and session_id in self._sessions:
                entry = self._sessions[session_id]
                self._sessions.move_to_end(session_id)  # LRU
                entry.last_attach = time.monotonic()
                entry.attached_clients += 1
                return {
                    "session_id": entry.session_id,
                    "url": entry.session.url or "",
                    "title": entry.session.page.title if entry.session.page else "",
                    "restored": False,
                }

            # Check for evicted checkpoint
            if session_id:
                restored = await self._restore_locked(session_id)
                if restored:
                    return restored

            # Create new
            sid = session_id or ("s_" + secrets.token_hex(4))
            client = httpx.AsyncClient(follow_redirects=True)
            session = Session(client, sid)
            entry = SessionEntry(sid, session, self._get_store())
            self._sessions[sid] = entry
            self._evict_lru_locked()
            entry.attached_clients += 1
            return {
                "session_id": sid,
                "url": "",
                "title": "",
                "restored": False,
            }

    async def detach(self, session_id: str) -> None:
        """Detach from a session. Starts TTL countdown."""
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry:
                entry.attached_clients = max(0, entry.attached_clients - 1)
                entry.last_attach = time.monotonic()

    async def execute(self, session_id: str, command: str, arguments: dict | None = None) -> dict[str, Any]:
        """Execute a shell command against a session."""
        async with self._lock:
            entry = self._sessions.get(session_id)
        if not entry:
            return {"ok": False, "error": f"session '{session_id}' not found"}
        result = await entry.shell.execute(command, arguments or {})
        return result

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all live sessions with metadata (+ engine pressure)."""
        async with self._lock:
            rows = []
            for e in self._sessions.values():
                row = {
                    "session_id": e.session_id,
                    "url": e.session.url or "",
                    "title": e.session.page.title if e.session.page else "",
                    "attached_clients": e.attached_clients,
                    "idle_seconds": int(time.monotonic() - e.last_attach),
                }
                # Stage-5: expose the live context's pressure so eviction
                # policy (and agents) can see which sessions run hot. All
                # probes are optional — a closed realm simply reports none.
                ctx = getattr(getattr(e.session, "current_tab", None), "ctx", None)
                pressure = {}
                if ctx is not None:
                    if hasattr(ctx, "pressure_epoch"):
                        epoch = ctx.pressure_epoch()
                        pressure["epoch"] = epoch
                        # Auto-mark: observed pressure drives eviction policy.
                        if epoch >= 1:
                            e.pressure_mark = max(e.pressure_mark, epoch)
                    if hasattr(ctx, "heap_band"):
                        floor, ceil, granted = ctx.heap_band()
                        pressure["heap_granted_bytes"] = granted
                        pressure["heap_ceil_bytes"] = ceil
                    if hasattr(ctx, "external_reported"):
                        pressure["external_bytes"] = ctx.external_reported()
                    if hasattr(ctx, "parena_stats"):
                        _ram, spill, _text = ctx.parena_stats()
                        pressure["parena_spill_bytes"] = spill
                if pressure:
                    row["pressure"] = pressure
                rows.append(row)
            return rows

    async def _restore_locked(self, session_id: str) -> dict[str, Any] | None:
        """Restore a session from its SQLite checkpoint."""
        store = self._get_store()
        try:
            rows = store.list_checkpoints()
        except Exception:
            return None
        for row in rows:
            if row.get("id", "").startswith(session_id) or row.get("id") == session_id:
                # Found a checkpoint — restore
                client = httpx.AsyncClient(follow_redirects=True)
                session = Session(client, session_id)
                entry = SessionEntry(session_id, session, store)
                self._sessions[session_id] = entry
                self._evict_lru_locked()
                # Restore state via shell
                try:
                    await entry.shell.execute("restore", {"id": row["id"]})
                except Exception:
                    pass
                entry.attached_clients += 1
                return {
                    "session_id": session_id,
                    "url": session.url or "",
                    "title": session.page.title if session.page else "",
                    "restored": True,
                }
        return None

    def _evict_lru_locked(self):
        """Evict least-recently-used sessions if over capacity. Called under lock.

        Pressure-driven policy (Future C): sessions marked under pressure
        (epoch >= 1) are evicted FIRST — a hot session is the one burning
        process memory, so reclaiming it is the highest-value eviction;
        calm sessions outlive them. Within each class, LRU order holds.
        """
        while len(self._sessions) > self._max_live:
            # Pressured-first: scan for any entry with a pressure mark.
            pressured = next(
                (sid for sid, e in self._sessions.items() if e.pressure_mark >= 1),
                None,
            )
            if pressured is not None:
                entry = self._sessions.pop(pressured)
                self._checkpoint_and_close_locked(entry)
                continue
            sid, entry = self._sessions.popitem(last=False)  # oldest (LRU)
            self._checkpoint_and_close_locked(entry)

    def mark_pressure(self, session_id: str, epoch: int) -> None:
        """Mark (or clear) a session's pressure level. epoch >= 1 makes the
        session eviction-first; 0 clears the mark. Hosts (Loom TTL loop,
        agents) call this from observed pressure signals."""
        entry = self._sessions.get(session_id)
        if entry is not None:
            entry.pressure_mark = max(0, epoch)

    def pressured_sessions(self) -> list[str]:
        """IDs of sessions currently marked under pressure."""
        return [sid for sid, e in self._sessions.items() if e.pressure_mark >= 1]

    def _checkpoint_and_close_locked(self, entry: SessionEntry):
        """Checkpoint a session to SQLite and close its client. Sync, under lock."""
        try:
            # Use the shell's checkpoint command via the store directly
            import json as _json
            store = entry.store or self._get_store()
            cid = f"loom_{entry.session_id}_{int(time.time())}"
            s = entry.session
            cookies_data = _json.dumps(
                [{"name": k, "value": v} for k, v in getattr(s.client, "cookies", {}).items()]
            )
            storage_data = _json.dumps(getattr(s, "_web_storage", {}))
            tab = getattr(s, "current_tab", None)
            history_data = _json.dumps(
                [{"url": p.url, "title": p.title} for p in (tab.history if tab else [])]
            )
            html = s.html or ""
            title = s.page.title if s.page else ""
            store.save_checkpoint(cid, s.active_tab_id, s.url or "", title, html,
                                  cookies_data, storage_data, history_data)
        except Exception:
            pass  # checkpoint failure is non-fatal; session is still dropped
        # Close the httpx client
        try:
            asyncio.ensure_future(entry.session.client.aclose())
        except Exception:
            pass

    async def evict_expired(self):
        """Evict sessions whose TTL has expired and have no attached clients."""
        async with self._lock:
            now = time.monotonic()
            to_evict = [
                sid for sid, e in self._sessions.items()
                if e.attached_clients == 0 and (now - e.last_attach) > self._ttl
            ]
            for sid in to_evict:
                entry = self._sessions.pop(sid)
                self._checkpoint_and_close_locked(entry)

    def start_eviction_loop(self, interval: float = 60.0):
        """Start a background task that periodically evicts expired sessions."""
        async def _loop():
            while True:
                await asyncio.sleep(interval)
                await self.evict_expired()
        self._eviction_task = asyncio.ensure_future(_loop())

    def stop_eviction_loop(self):
        if self._eviction_task:
            self._eviction_task.cancel()
            self._eviction_task = None

    async def close_all(self):
        """Checkpoint all live sessions and close them (graceful shutdown)."""
        async with self._lock:
            for entry in list(self._sessions.values()):
                self._checkpoint_and_close_locked(entry)
            self._sessions.clear()
        self.stop_eviction_loop()


class LoomDaemon:
    """The Unix socket server that accepts client connections and dispatches
    frames to the session registry."""

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        max_live: int = 8,
        ttl_seconds: float = 1800.0,
        db_path: str | None = None,
    ):
        self.socket_path = socket_path or os.environ.get("WR_LOOM_SOCKET", _default_socket_path())
        self.registry = SessionRegistry(max_live=max_live, ttl_seconds=ttl_seconds, db_path=db_path)
        self._server: asyncio.AbstractServer | None = None
        self._current_session: dict[int, str] = {}  # client id → attached session_id

    async def start(self):
        """Bind the Unix socket and start accepting connections."""
        # Clean up stale socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = await asyncio.start_unix_server(self._handle_client, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)  # owner-only access
        self.registry.start_eviction_loop()
        return self._server

    async def serve_forever(self):
        """Start and run until interrupted."""
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Graceful shutdown: checkpoint all sessions, close socket."""
        await self.registry.close_all()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle one client connection: read JSON frames, dispatch to registry."""
        client_id = id(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    frame = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    await self._send(writer, {"ok": False, "error": "invalid JSON frame"})
                    continue

                response = await self._dispatch(frame, client_id)
                await self._send(writer, response)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            # Detach on disconnect
            sid = self._current_session.pop(client_id, None)
            if sid:
                await self.registry.detach(sid)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, frame: dict, client_id: int) -> dict[str, Any]:
        """Dispatch a JSON frame to the appropriate registry operation."""
        op = frame.get("op", "")

        if op == "ping":
            return {"ok": True, "pong": True}

        if op == "list":
            sessions = await self.registry.list_sessions()
            return {"ok": True, "sessions": sessions}

        if op == "attach":
            session_id = frame.get("session_id")
            result = await self.registry.attach(session_id)
            self._current_session[client_id] = result["session_id"]
            # Record journal entry for attach
            try:
                from webresearch.loom_journal import record_journal_entry
                payload = json.dumps({"session_id": result["session_id"], "restored": result.get("restored", False)}, ensure_ascii=False)
                record_journal_entry(result["session_id"], "attach", payload)
            except Exception:
                pass  # journal recording is best-effort
            return {"ok": True, **result}

        if op == "detach":
            sid = self._current_session.pop(client_id, None)
            if sid:
                # Record journal entry for detach
                try:
                    from webresearch.loom_journal import record_journal_entry
                    payload = json.dumps({"session_id": sid}, ensure_ascii=False)
                    record_journal_entry(sid, "detach", payload)
                except Exception:
                    pass  # journal recording is best-effort
                await self.registry.detach(sid)
            return {"ok": True}

        if op == "command":
            sid = self._current_session.get(client_id)
            if not sid:
                return {"ok": False, "error": "not attached; send {\"op\":\"attach\"} first"}
            command = frame.get("command", "")
            arguments = frame.get("arguments", {})
            result = await self.registry.execute(sid, command, arguments)
            # Record journal entry for the command
            try:
                from webresearch.loom_journal import record_journal_entry
                payload = json.dumps({"command": command, "arguments": arguments, "result_ok": result.get("ok", False)}, ensure_ascii=False)
                record_journal_entry(sid, "command", payload)
            except Exception:
                pass  # journal recording is best-effort
            return result

        return {"ok": False, "error": f"unknown op '{op}'; use: attach, command, detach, list, ping"}

    async def _send(self, writer: asyncio.StreamWriter, obj: dict):
        """Send a JSON response frame (newline-delimited)."""
        writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()


async def connect_and_send(socket_path: str, frame: dict) -> dict:
    """Thin client: connect to the daemon, send one frame, get response, disconnect."""
    reader, writer = await asyncio.open_unix_connection(socket_path)
    try:
        writer.write((json.dumps(frame) + "\n").encode("utf-8"))
        await writer.drain()
        line = await reader.readline()
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        await writer.wait_closed()
