"""Cross-process session retention tests (flaw #1).

The MCP stdio server owns one in-memory session that dies on process restart.
The fix: when WR_SESSION_DB is set, DoshShell backs its store with that SQLite
file; the MCP server auto-checkpoints on navigate and auto-restores the latest
checkpoint on startup. These tests exercise the retention mechanism at the
Session+shell level (the same primitives the MCP wiring uses), including the
restore command reapplying the cookie jar so logged-in sessions survive.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session

BASE = "https://persist.test"
HTML = '<html><head><title>Persist</title></head><body><p>hello persist</p></body></html>'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())
    db = tmp_path / "session.sqlite"
    monkeypatch.setenv("WR_SESSION_DB", str(db))

    @asynccontextmanager
    async def make():
        routes = {str(httpx.URL(BASE + "/")): HTML}

        def handler(request):
            return httpx.Response(200, text=routes[str(request.url)],
                                  headers={"content-type": "text/html", "cache-control": "no-store"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            yield client

    return make


@pytest.mark.asyncio
async def test_shell_default_is_in_memory(monkeypatch):
    monkeypatch.delenv("WR_SESSION_DB", raising=False)
    async with httpx.AsyncClient() as client:
        s = Session(client, "t")
        assert s.shell.store.persistent is False


@pytest.mark.asyncio
async def test_shell_uses_wr_session_db_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WR_SESSION_DB", str(tmp_path / "x.sqlite"))
    async with httpx.AsyncClient() as client:
        s = Session(client, "t")
        assert s.shell.store.persistent is True


@pytest.mark.asyncio
async def test_cross_process_session_retention(workspace):
    # --- Process A: open page, set cookie + storage, checkpoint ---
    async with workspace() as client:
        sA = Session(client, "A")
        await sA.goto(BASE + "/")
        sA.client.cookies.set("sid", "SECRET_SESSION_VALUE", domain="persist.test")
        sA._web_storage["https://persist.test"] = {"theme": "dark"}
        res = await sA.shell.execute("checkpoint", {})
        assert res["ok"] is True
        rows = sA.shell.store.list_checkpoints()
        assert len(rows) == 1
        await sA.close()

    # --- Process B: new Session, same WR_SESSION_DB; restore latest ---
    async with workspace() as client:
        sB = Session(client, "B")
        # The checkpoint from A survived the process boundary.
        rows = sB.shell.store.list_checkpoints()
        assert len(rows) == 1
        r = await sB.shell.execute("restore", {"id": rows[0]["id"]})
        assert r["ok"] is True
        # URL retained
        assert "persist.test" in (sB.url or "")
        # Web storage retained
        assert sB._web_storage.get("https://persist.test", {}).get("theme") == "dark"
        # Cookie jar retained (the high-value auth-retention case)
        assert sB.client.cookies.get("sid") == "SECRET_SESSION_VALUE"
        await sB.close()
