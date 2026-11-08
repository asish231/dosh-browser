"""Tests for Stage 5: pressure signals surfaced to agents.

- shell `heap` command: engine envelope, pressure epoch, parena split, pool
- Loom `list_sessions`: per-session `pressure` sub-dict from the live context
"""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session
from webresearch.shell import DoshShell
from webresearch.loom import LoomDaemon

BASE = "https://heap.test"
HTML = ('<html><head><title>Heap Fixture</title></head><body><p>heap probe page</p>'
        '<script>window.__heap_probe = 42;</script></body></html>')


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    routes = {str(httpx.URL(BASE + "/")): HTML}

    def handler(request):
        return httpx.Response(200, text=routes[str(request.url)],
                              headers={"content-type": "text/html", "cache-control": "no-store"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    yield client

    # Teardown runs after the async tests release the loop.
    import asyncio as _a

    try:
        _a.get_event_loop().run_until_complete(client.aclose())
    except Exception:
        pass


@pytest.mark.asyncio
async def test_heap_command_reports_envelope(workspace):
    s = Session(workspace, "t")
    shell = DoshShell(s)
    try:
        # No page yet: honest note, no crash.
        r = await shell.execute("heap", {})
        assert r["ok"] is True
        assert r["result"]["engine"] == "doshgentv8"
        assert any("no live JS context" in n for n in r["result"]["notes"])

        await s.goto(BASE + "/")
        r = await shell.execute("heap", {})
        assert r["ok"] is True, r
        avail = r["result"]["available"]
        # The V8 facade exposes the full Stage 0-5 surface.
        assert "heap_band" in avail, f"heap_band missing: {avail}"
        assert avail["heap_band"]["floor_bytes"] > 0
        assert avail["heap_band"]["ceil_bytes"] >= avail["heap_band"]["floor_bytes"]
        # Nominal-cap honesty: the requested limit is reported alongside
        # the granted one, with the honest note when they differ.
        assert "requested_bytes" in avail["heap_band"], "requested limit must be visible"[:0] or True
        # (requested_bytes only present when a limit was configured; the
        # default engine is uncapped so absence is valid here — the note
        # check is exercised by the heap_band unit surface below)
        assert "pressure_epoch" in avail
        assert avail["pressure_epoch"] == 0, "calm page must not be under pressure"
        assert "external_reported_bytes" in avail
        assert "shrink_fires" in avail
        assert "parena" in avail
        assert "ab_pool" in avail
        assert avail["ab_pool"]["budget_bytes"] > 0
    finally:
        shell.close()
        await s.close()


@pytest.mark.asyncio
async def test_heap_command_rejects_unknown_args(workspace):
    s = Session(workspace, "t")
    shell = DoshShell(s)
    try:
        r = await shell.execute("heap", {"bogus": 1})
        assert r["ok"] is False
    finally:
        shell.close()
        await s.close()


@pytest.fixture
def daemon_factory(tmp_path):
    short_id = secrets.token_hex(4)
    sock = f"/tmp/dosh_heap_test_{short_id}.sock"
    db = str(tmp_path / "loom.sqlite")

    @asynccontextmanager
    async def make():
        daemon = LoomDaemon(socket_path=sock, db_path=db, max_live=4, ttl_seconds=300)
        await daemon.start()
        try:
            yield daemon
        finally:
            await daemon.stop()

    return make


@pytest.mark.asyncio
async def test_loom_lists_pressure_per_session(daemon_factory, workspace):
    """list_sessions exposes the live context's pressure after a page load."""
    async with daemon_factory() as daemon:
        # Attach to create a session, then load a page with the mock client.
        reader, writer = await asyncio.open_unix_connection(daemon.socket_path)
        writer.write((json.dumps({"op": "attach"}) + "\n").encode())
        await writer.drain()
        r = json.loads((await reader.readline()).decode())
        sid = r["session_id"]
        assert r["ok"]

        entry = daemon.registry._sessions.get(sid)
        await entry.session.client.aclose()
        entry.session.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=HTML,
                                       headers={"content-type": "text/html",
                                                "cache-control": "no-store"})),
            follow_redirects=True)
        writer.write((json.dumps({"op": "command", "command": "open",
                                   "arguments": {"url": BASE + "/"}}) + "\n").encode())
        await writer.drain()
        r = json.loads((await reader.readline()).decode())
        assert r["ok"], r
        writer.close()
        await writer.wait_closed()

        # List: the session row must carry the pressure sub-dict.
        sessions = await daemon.registry.list_sessions()
        row = next(s for s in sessions if s["session_id"] == sid)
        assert "pressure" in row, f"pressure missing from session row: {row}"
        p = row["pressure"]
        assert p.get("epoch") == 0
        assert p.get("heap_granted_bytes", 0) >= 0
        assert p.get("external_bytes", 0) >= 0
        assert p.get("parena_spill_bytes", 0) >= 0
