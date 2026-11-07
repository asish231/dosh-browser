"""Tests for the Dosh Loom session daemon (Phase 1).

Tests prove: daemon binds Unix socket, accepts connections, manages sessions
with LRU+TTL eviction, checkpoints on evict, restores on reattach, and survives
client disconnect with state intact.
"""

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.loom import LoomDaemon, connect_and_send, SessionRegistry


@pytest.fixture
def daemon_factory(tmp_path):
    """Returns an async context manager that yields a running daemon."""
    # macOS AF_UNIX path limit is 104 bytes; tmp_path can be very long.
    # Use a short unique path in /tmp.
    import os, secrets
    short_id = secrets.token_hex(4)
    sock = f"/tmp/dosh_test_{short_id}.sock"
    db = str(tmp_path / "loom.sqlite")

    @asynccontextmanager
    async def make(**kwargs):
        defaults = {"socket_path": sock, "db_path": db, "max_live": 4, "ttl_seconds": 300}
        defaults.update(kwargs)
        daemon = LoomDaemon(**defaults)
        await daemon.start()
        try:
            yield daemon
        finally:
            await daemon.stop()

    return make


async def _send(sock: str, frame: dict) -> dict:
    return await connect_and_send(sock, frame)


async def _session(sock: str, *frames: dict) -> list[dict]:
    reader, writer = await asyncio.open_unix_connection(sock)
    responses = []
    try:
        for frame in frames:
            writer.write((json.dumps(frame) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
            responses.append(json.loads(line.decode("utf-8")))
    finally:
        writer.close()
        await writer.wait_closed()
    return responses


# --- Basic protocol ---


@pytest.mark.asyncio
async def test_ping(daemon_factory):
    async with daemon_factory() as daemon:
        r = await _send(daemon.socket_path, {"op": "ping"})
        assert r["ok"] is True
        assert r["pong"] is True


@pytest.mark.asyncio
async def test_attach_creates_session(daemon_factory):
    async with daemon_factory() as daemon:
        r = await _send(daemon.socket_path, {"op": "attach"})
        assert r["ok"] is True
        assert r["session_id"].startswith("s_")
        assert r["restored"] is False


@pytest.mark.asyncio
async def test_list_shows_sessions(daemon_factory):
    async with daemon_factory() as daemon:
        await _send(daemon.socket_path, {"op": "attach"})
        r = await _send(daemon.socket_path, {"op": "list"})
        assert r["ok"] is True
        assert len(r["sessions"]) >= 1
        assert "session_id" in r["sessions"][0]


@pytest.mark.asyncio
async def test_command_without_attach_fails(daemon_factory):
    async with daemon_factory() as daemon:
        r = await _send(daemon.socket_path, {"op": "command", "command": "status"})
        assert r["ok"] is False
        assert "attach" in r["error"]


@pytest.mark.asyncio
async def test_attach_and_command_in_same_connection(daemon_factory):
    async with daemon_factory() as daemon:
        responses = await _session(
            daemon.socket_path,
            {"op": "attach"},
            {"op": "command", "command": "status"},
            {"op": "detach"},
        )
        assert responses[0]["ok"] is True
        assert responses[1]["ok"] is True
        assert responses[1]["result"]["session_id"].startswith("s_")
        assert responses[2]["ok"] is True


@pytest.mark.asyncio
async def test_unknown_op_returns_error(daemon_factory):
    async with daemon_factory() as daemon:
        r = await _send(daemon.socket_path, {"op": "frobnicate"})
        assert r["ok"] is False
        assert "unknown op" in r["error"]


# --- Session persistence across disconnect ---


@pytest.mark.asyncio
async def test_session_survives_client_disconnect(daemon_factory):
    """Core test: client attaches → opens page → disconnects → reconnects → state intact."""
    BASE = "https://loom.test"
    HTML = '<html><head><title>Loom Test</title></head><body><p>hello loom</p></body></html>'
    routes = {str(httpx.URL(BASE + "/")): HTML}

    def handler(request):
        return httpx.Response(200, text=routes[str(request.url)],
                              headers={"content-type": "text/html"})

    async with daemon_factory() as daemon:
        # 1. Attach to get a session, inject mock transport into its client
        r = await _send(daemon.socket_path, {"op": "attach"})
        sid = r["session_id"]
        entry = daemon.registry._sessions.get(sid)
        assert entry is not None
        await entry.session.client.aclose()
        entry.session.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )

        # 2. Open page via the daemon protocol (persistent connection)
        responses = await _session(
            daemon.socket_path,
            {"op": "attach", "session_id": sid},
            {"op": "command", "command": "open", "arguments": {"url": BASE + "/"}},
        )
        assert responses[0]["ok"] is True
        assert responses[1]["ok"] is True
        assert "Loom Test" in json.dumps(responses[1])

        # 3. Reconnect to same session and verify state persisted
        responses2 = await _session(
            daemon.socket_path,
            {"op": "attach", "session_id": sid},
            {"op": "command", "command": "status"},
        )
        assert responses2[0]["ok"] is True
        assert responses2[1]["ok"] is True
        assert BASE in responses2[1]["result"].get("url", "")


# --- LRU eviction ---


@pytest.mark.asyncio
async def test_lru_evicts_oldest(daemon_factory):
    """When max_live is exceeded, oldest session is evicted (checkpointed)."""
    async with daemon_factory(max_live=4) as daemon:
        sids = []
        for _ in range(5):
            r = await _send(daemon.socket_path, {"op": "attach"})
            sids.append(r["session_id"])

        r = await _send(daemon.socket_path, {"op": "list"})
        live_ids = {s["session_id"] for s in r["sessions"]}
        assert len(r["sessions"]) <= 4
        assert sids[-1] in live_ids


# --- Registry unit tests ---


@pytest.mark.asyncio
async def test_registry_create_and_list(tmp_path):
    reg = SessionRegistry(max_live=8, ttl_seconds=300, db_path=str(tmp_path / "test.sqlite"))
    sid = await reg.create()
    assert sid.startswith("s_")
    sessions = await reg.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == sid


@pytest.mark.asyncio
async def test_registry_attach_creates_new(tmp_path):
    reg = SessionRegistry(max_live=8, ttl_seconds=300, db_path=str(tmp_path / "test.sqlite"))
    result = await reg.attach(None)
    assert result["session_id"].startswith("s_")
    assert result["restored"] is False


@pytest.mark.asyncio
async def test_registry_evict_expired(tmp_path):
    """Sessions with no attached clients and expired TTL are evicted."""
    reg = SessionRegistry(max_live=8, ttl_seconds=0.1, db_path=str(tmp_path / "test.sqlite"))
    await reg.create()
    assert len(reg._sessions) == 1
    await asyncio.sleep(0.2)
    await reg.evict_expired()
    assert len(reg._sessions) == 0
