"""Tests for Fix 3: bounded session history + pressure-driven Loom eviction."""

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session, Tab, _history_limit
from webresearch.loom import LoomDaemon

BASE = "https://hist.test"


@pytest.fixture
def hist_client(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    def handler(request):
        n = request.url.path.strip("/") or "0"
        return httpx.Response(200, text=f"<html><head><title>Page {n}</title></head><body><p>p{n}</p></body></html>",
                              headers={"content-type": "text/html", "cache-control": "no-store"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_history_limit_default():
    assert _history_limit() == 50


def test_history_limit_env(monkeypatch):
    monkeypatch.setenv("WR_HISTORY_LIMIT", "3")
    assert _history_limit() == 3
    monkeypatch.setenv("WR_HISTORY_LIMIT", "0")
    assert _history_limit() == 0


@pytest.mark.asyncio
async def test_history_is_bounded(hist_client, monkeypatch):
    """The ~1MB/visit drift fix: history must cap at WR_HISTORY_LIMIT entries
    and always keep the CURRENT page as the active entry."""
    monkeypatch.setenv("WR_HISTORY_LIMIT", "5")
    s = Session(hist_client, "t")
    try:
        for i in range(1, 13):  # 12 visits, cap 5
            await s.goto(f"{BASE}/{i}", js="never")
        # History is bounded at the cap.
        assert len(s.current_tab.history) == 5, f"history must be capped, got {len(s.current_tab.history)}"
        assert len(s.current_tab.snapshots) == 5, "snapshots must stay entry-parallel"
        assert len(s.current_tab.bf_ctx) == 5, "bf_ctx must stay entry-parallel"
        # The CURRENT entry is the newest page, and index points at it.
        assert s.current_tab.index == 4
        assert "Page 12" in (s.page.title or "")
        # Back traversal still works within the bounded window.
        page = s.back()
        assert "Page 11" in (page.title or ""), f"back must reach the prior entry: {page.title}"
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_history_zero_is_unbounded(hist_client, monkeypatch):
    monkeypatch.setenv("WR_HISTORY_LIMIT", "0")
    s = Session(hist_client, "t")
    try:
        for i in range(1, 9):
            await s.goto(f"{BASE}/{i}", js="never")
        assert len(s.current_tab.history) == 8, "0 must mean unbounded"
    finally:
        await s.close()


@pytest.fixture
def daemon_factory(tmp_path):
    short_id = secrets.token_hex(4)
    sock = f"/tmp/dosh_evict_test_{short_id}.sock"
    db = str(tmp_path / "loom.sqlite")

    @asynccontextmanager
    async def make(**kwargs):
        daemon = LoomDaemon(socket_path=sock, db_path=db, **kwargs)
        await daemon.start()
        try:
            yield daemon
        finally:
            await daemon.stop()

    return make


@pytest.mark.asyncio
async def test_loom_eviction_prioritizes_pressured_sessions(daemon_factory, hist_client, monkeypatch):
    """Pressure-driven eviction (Future C): when over capacity, pressured
    sessions must be evicted (checkpointed) FIRST — even when a CALMER
    session is the LRU-oldest. Recency must not shield a hot session."""
    monkeypatch.setenv("WR_HISTORY_LIMIT", "5")

    async with daemon_factory(max_live=3, ttl_seconds=300) as daemon:
        # Attach three sessions in order: calm_a (oldest), hot_b, calm_c.
        for name in ["calm_a", "hot_b", "calm_c"]:
            reader, writer = await asyncio.open_unix_connection(daemon.socket_path)
            writer.write((json.dumps({"op": "attach", "session_id": name}) + "\n").encode())
            await writer.drain()
            r = json.loads((await reader.readline()).decode())
            assert r["ok"]
            writer.close()
            await writer.wait_closed()

        # Mark the NEWEST-but-one session (hot_b) as pressured. Note: calm_a
        # is the LRU-oldest, so plain LRU would evict calm_a — the policy
        # must instead evict hot_b (pressured-first overrides recency).
        daemon.registry.mark_pressure("hot_b", 2)
        assert daemon.registry.pressured_sessions() == ["hot_b"]

        # Exceed capacity: attach a 4th session -> one eviction round.
        reader, writer = await asyncio.open_unix_connection(daemon.socket_path)
        writer.write((json.dumps({"op": "attach", "session_id": "new_d"}) + "\n").encode())
        await writer.drain()
        r = json.loads((await reader.readline()).decode())
        assert r["ok"]
        writer.close()
        await writer.wait_closed()

        sessions = await daemon.registry.list_sessions()
        live = {s["session_id"] for s in sessions}
        # The PRESSURED session must be gone even though it was newer than
        # calm_a; the calm LRU-oldest must have outlived it.
        assert "hot_b" not in live, f"pressured session must be evicted first; live: {live}"
        assert "calm_a" in live, f"calm LRU-oldest must outlive the pressured session; live: {live}"
        assert "calm_c" in live
        assert "new_d" in live
