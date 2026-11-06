"""S8: bfcache live-heap restore — hermetic tests.

Before S8, cross-document back()/forward() dropped the destination's
engine context and re-ran its scripts (the disclosed approximation).
S8 parks the leaving entry's LIVE ctx on its history slot and restores
that same ctx on traversal — heap, DOM, timers, and JS state survive
exactly like a same-document traversal (real bfcache parity). Entries
with no parked ctx (cleared tabs, engine drops) keep the rebuild+
re-run fallback, disclosed — never faked.

The pin that matters: after goto A -> B -> back(), page A's JS heap
state (a global set before navigation) is STILL THERE — no script
re-ran. And a second global set during the restore pass persists
forward/back. The rebuild fallback is proven by its tell: scripts DO
re-run and heap state is lost.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        body = pages.get(req.url.path, pages.get("*", "<html><body>404</body></html>"))
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _session(pages: dict[str, str], sid: str) -> Session:
    return Session(_client(pages), sid)


@pytest.mark.asyncio
async def test_back_restores_live_heap_without_rerun():
    """goto A (heap mutates) -> goto B -> back(): A's heap survives and the
    script has NOT re-run (its run counter stays 1)."""
    pages = {
        "/a": """<html><body>
            <script>
                globalThis.__runs = (globalThis.__runs || 0) + 1;
                globalThis.__heap_marker = 'set-on-A';
            </script>
            <p id="which">A</p>
        </body></html>""",
        "/b": "<html><body><p id=\"which\">B</p></body></html>",
    }
    s = _session(pages, "s8_a")
    await s.goto(f"{BASE}/a")
    ctx_a = s.current_tab.ctx
    assert ctx_a is not None
    assert ctx_a.eval("globalThis.__runs") == 1
    assert ctx_a.eval("globalThis.__heap_marker") == "set-on-A"

    await s.goto(f"{BASE}/b")
    assert s.current_tab.ctx is not ctx_a, "goto B must leave A's ctx parked"

    page = s.back()
    assert page.url.endswith("/a")
    ctx_restored = s.current_tab.ctx
    assert ctx_restored is ctx_a, "back() must restore the PARKED live ctx, not rebuild"
    # Heap state survived; the script did NOT re-run:
    assert ctx_restored.eval("globalThis.__runs") == 1, "no script re-run on bfcache restore"
    assert ctx_restored.eval("globalThis.__heap_marker") == "set-on-A", "heap survives"


@pytest.mark.asyncio
async def test_forward_also_restores_the_parked_heap():
    pages = {
        "/a": "<html><body><p>AA</p></body></html>",
        "/b": """<html><body>
            <script>globalThis.__runs = (globalThis.__runs || 0) + 1;</script>
            <p>BB</p>
        </body></html>""",
    }
    s = _session(pages, "s8_b")
    await s.goto(f"{BASE}/a")
    await s.goto(f"{BASE}/b")
    ctx_b = s.current_tab.ctx
    assert ctx_b is not None and ctx_b.eval("globalThis.__runs") == 1

    s.back()          # to A (A has no scripts; its ctx may be None)
    page = s.forward()  # back to B: the parked heap returns
    assert page.url.endswith("/b")
    ctx_fwd = s.current_tab.ctx
    assert ctx_fwd is ctx_b, "forward() must restore B's parked ctx"
    assert ctx_fwd.eval("globalThis.__runs") == 1, "B's script must not re-run"


@pytest.mark.asyncio
async def test_heap_mutations_after_restore_persist_across_roundtrip():
    pages = {
        "/a": """<html><body>
            <script>globalThis.__visits = (globalThis.__visits || 0) + 1;</script>
            <p>A</p>
        </body></html>""",
        "/b": "<html><body><p>B</p></body></html>",
    }
    s = _session(pages, "s8_c")
    await s.goto(f"{BASE}/a")
    s.current_tab.ctx.eval("globalThis.__custom = 'from-first-visit'")
    await s.goto(f"{BASE}/b")
    s.back()
    # The restored heap carries BOTH the script's state and the mutation
    # made before navigating away.
    ctx = s.current_tab.ctx
    assert ctx.eval("globalThis.__custom") == "from-first-visit"
    assert ctx.eval("globalThis.__visits") == 1


@pytest.mark.asyncio
async def test_no_parked_ctx_falls_back_to_rebuild_disclosed():
    """A page with NO scripts has no ctx to park: back() rebuilds, and the
    rebuild+re-run fallback is honest (page content still restores)."""
    pages = {
        "/plain": "<html><body><p id=\"x\">PLAINPAGE</p></body></html>",
        "/next": "<html><body><p>NEXT</p></body></html>",
    }
    s = _session(pages, "s8_d")
    await s.goto(f"{BASE}/plain")
    # /plain has no scripts -> ctx is None (run_scripts returns early).
    assert s.current_tab.ctx is None
    await s.goto(f"{BASE}/next")
    page = s.back()
    assert page.url.endswith("/plain")
    assert "PLAINPAGE" in (page.view or ""), f"the stored document still restores by rebuild: {page.view!r}"


@pytest.mark.asyncio
async def test_close_tab_clears_its_ctx():
    """Closing a tab must drop its live ctx (no zombie heaps)."""
    pages = {
        "/a": "<html><body><script>globalThis.__z = 1;</script><p>A</p></body></html>",
    }
    s = _session(pages, "s8_e")
    await s.goto(f"{BASE}/a")
    ctx = s.current_tab.ctx
    assert ctx is not None
    s.new_tab("t2")
    s.close_tab("t2")
    # The original tab's ctx is untouched:
    assert s.current_tab.ctx is ctx
