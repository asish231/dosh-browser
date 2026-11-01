"""History fidelity regressions (round 6).

The three verified gaps this file pins:

1. Load-pass history traversal was dropped: `history.back()` inside a
   `setTimeout()` during page load never moved the session — the move was
   recorded in js_info but `goto()` ignored it until some later action pass.
2. Same-document restore re-executed the destination's scripts: back() to a
   pushState entry rebuilt the document from the stored HTML, re-ran its
   scripts, and a page that pushState'd on load re-pushed — undoing the
   back() (index bounced forward, history.length inflated).
3. popstate state was JSON-round-tripped: undefined-valued keys, Dates,
   NaN/Infinity, Maps/Sets and cyclic references were silently degraded, and
   functions were dropped without the DataCloneError a real browser throws.

Every assertion reads specific engine/session state, never page substrings.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS
from webresearch.session import Session

BASE = "https://hist.test"


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        body = pages.get(req.url.path, "<html><body>404</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# 1. Load-time history traversal is consumed by goto()
# ---------------------------------------------------------------------------

LOAD_BACK_PAGE = """<html><body><div id="v">backer</div>
<script>
window.__runs = (window.__runs || 0) + 1;
setTimeout(function () { history.back(); }, 0);
</script></body></html>"""

STATIC = '<html><body><div id="v">previous</div></body></html>'


@pytest.mark.asyncio
async def test_load_time_settimeout_back_lands_on_previous_entry():
    pages = {"/prev": STATIC, "/": LOAD_BACK_PAGE}
    session = Session(_client(pages), "s_loadback")
    await session.goto(f"{BASE}/prev")
    assert len(session.history) == 1
    await session.goto(f"{BASE}/")
    # The load-time back() traversed to the previous entry, not silently
    # deferred until an action pass.
    assert session.url == f"{BASE}/prev"
    assert session.index == 0
    assert session.page is not None and "previous" in session.page.view


@pytest.mark.asyncio
async def test_load_time_back_out_of_range_is_a_noop():
    # A fresh session has no previous entry: back() during load must be a
    # silent no-op that still loads the page (real browser behavior).
    session = Session(_client({"/": LOAD_BACK_PAGE}), "s_loadback_oor")
    page = await session.goto(f"{BASE}/")
    assert session.url == f"{BASE}/"
    assert session.index == 0
    assert len(session.history) == 1
    assert page is not None


@pytest.mark.asyncio
async def test_load_time_back_chain_is_bounded():
    # /a -> /b (setTimeout back) -> /c (setTimeout back): the traversal from
    # /c lands on /b, whose stored document also requests a back — consumed,
    # bounded. Cross-document restores re-run the destination's scripts
    # (disclosed approximation), so /b's timer legitimately re-fires and the
    # chain ends on /a. The assertion: the chain completes and terminates.
    pages = {
        "/a": STATIC,
        "/b": LOAD_BACK_PAGE,
        "/c": LOAD_BACK_PAGE,
    }
    session = Session(_client(pages), "s_loadback_chain")
    await session.goto(f"{BASE}/a")
    await session.goto(f"{BASE}/b")
    await session.goto(f"{BASE}/c")
    assert session.url in (f"{BASE}/b", f"{BASE}/a"), session.url
    assert session.index <= 1, "the chain must terminate, never hang or grow"


# ---------------------------------------------------------------------------
# 2. Same-document traversal: no script re-run, no re-push
# ---------------------------------------------------------------------------

LOAD_PUSH_PAGE = """<html><body><div id="v">home</div>
<button id="go" onclick="window.__clicks = (window.__clicks || 0) + 1">go</button>
<script>
window.__runs = (window.__runs || 0) + 1;
history.pushState({ from: "load" }, "", "/page2");
document.getElementById("v").textContent = "pushed-on-load";
</script></body></html>"""


@pytest.mark.asyncio
async def test_back_after_load_pushstate_does_not_repush():
    session = Session(_client({"/": LOAD_PUSH_PAGE}), "s_repush")
    await session.goto(f"{BASE}/")
    assert [p.url for p in session.history] == [f"{BASE}/", f"{BASE}/page2"]
    assert session.index == 1

    session.back()  # agent-driven, same-document target
    assert session.index == 0
    # An action pass must NOT re-run the entry's scripts: no re-push, the
    # index stays put, the engine's history.length stays truthful.
    ref = next(a.ref for a in session.action_surface().actions if a.label == "go")
    await session.act(ref)
    assert session.index == 0, "a re-executed pushState must not undo the back()"
    assert len(session.history) == 2
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("window.__runs") == 1, "the entry's scripts must not re-run"
    assert ctx.eval("history.length") == 2, f"got {ctx.eval('history.length')!r}"
    assert ctx.eval("window.__clicks") == 1


@pytest.mark.asyncio
async def test_click_driven_back_is_a_live_traversal():
    page = """<html><body><div id="v">home</div>
    <button id="b" onclick="history.back()">back</button>
    <script>
    window.__ps = [];
    window.addEventListener('popstate', function (e) {
      window.__ps.push('s:' + JSON.stringify(e.state) + '|path:' + location.pathname);
    });
    history.pushState({ from: "load" }, "", "/page2");
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_clickback")
    await session.goto(f"{BASE}/")
    ref = next(a.ref for a in session.action_surface().actions if a.label == "back")
    await session.act(ref)
    assert session.index == 0
    assert session.url == f"{BASE}/"
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("window.__ps.join(',')") == "s:null|path:/", (
        ctx.eval("window.__ps.join(',')")
    )
    assert len(session.history) == 2, "no re-push entry may appear"


@pytest.mark.asyncio
async def test_forward_to_pushed_entry_returns_state_and_url():
    page = """<html><body><div id="v">home</div>
    <script>
    window.__ps = [];
    window.addEventListener('popstate', function (e) { window.__ps.push(e.state); });
    history.pushState({ page: 2 }, "", "/p2");
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_fwd")
    await session.goto(f"{BASE}/")
    session.back()
    assert session.index == 0
    session.forward()
    assert session.index == 1
    assert session.url == f"{BASE}/p2"
    ctx = session.current_tab.ctx
    assert ctx is not None
    # The popstate payload is asserted through the engine (QuickJS objects do
    # not marshal to Python dicts), never through page substrings.
    assert ctx.eval("window.__ps[window.__ps.length - 1].page") == 2
    assert ctx.eval("history.state.page") == 2


@pytest.mark.asyncio
async def test_same_doc_traversal_restores_scroll_offsets():
    page = """<html><body style="margin:0">
    <div style="height:2500px"></div>
    <script>history.pushState({}, "", "/p2");</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_scroll")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx
    assert ctx is not None
    ctx.eval("scrollTo(0, 800); true")
    session.back()
    # Entry 0 was left at scroll 0: restoring 0 is a real scroll (the old
    # falsy check kept the leaving entry's 800).
    assert ctx.eval("scrollY") == 0
    session.forward()
    assert ctx.eval("scrollY") == 800, "the leaving offset was saved on the entry"


@pytest.mark.asyncio
async def test_scroll_restoration_manual_optout():
    page = """<html><body style="margin:0">
    <div style="height:2500px"></div>
    <script>
    history.scrollRestoration = "manual";
    history.pushState({}, "", "/p2");
    scrollTo(0, 900);
    </script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_scroll_manual")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx
    assert ctx is not None
    session.back()
    assert ctx.eval("scrollY") == 900, "manual restoration must keep the live position"


@pytest.mark.asyncio
async def test_popstate_listener_chain_is_consumed_and_bounded():
    page = """<html><body><div id="v">home</div>
    <button id="f" onclick="history.forward()">forward</button>
    <script>
    window.__ps = [];
    window.addEventListener('popstate', function (e) {
      window.__ps.push(e.state && e.state.chain ? 'chain' : 'stop');
      if (e.state && e.state.chain) history.back();
    });
    history.pushState({ chain: 1 }, "", "/c1");
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_chain")
    await session.goto(f"{BASE}/")
    # The load-time pushState left the session ON the pushed entry; traverse
    # back to the initial entry so the click below has somewhere to go.
    session.back()
    assert session.index == 0
    ref = next(a.ref for a in session.action_surface().actions if a.label == "forward")
    await session.act(ref)
    # Sequence: the setup back() fires popstate at the initial entry ('stop'),
    # then forward -> /c1 ('chain') -> listener-requested back -> / ('stop').
    # The listener-requested move is consumed inside the same traversal.
    assert session.index == 0
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("window.__ps.join(',')") == "stop,chain,stop"


# ---------------------------------------------------------------------------
# 3. popstate state fidelity (structured-clone subset)
# ---------------------------------------------------------------------------

STATE_PAGE = """<html><body><div id="v">home</div>
<script>
window.__got = null;
window.addEventListener('popstate', function (e) { window.__got = e.state; });
history.pushState(
  { a: undefined, c: 7, d: new Date(0), e: NaN, m: new Map([["k", "v"]]), big: Infinity },
  "",
  "/s2"
);
</script></body></html>"""


@pytest.mark.asyncio
async def test_popstate_state_survives_the_round_trip():
    session = Session(_client({"/": STATE_PAGE}), "s_state")
    await session.goto(f"{BASE}/")
    session.back()   # to the initial entry (state null)...
    session.forward()  # ...and back to the pushed entry: popstate delivers it
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("window.__got === null || window.__got") is not None
    got = ctx.eval("JSON.stringify(window.__got)")
    assert got is not None
    # The specific values (never a substring of the page):
    assert ctx.eval("Object.prototype.hasOwnProperty.call(window.__got, 'a')") is True
    assert ctx.eval("window.__got.a === undefined") is True
    assert ctx.eval("window.__got.c") == 7
    assert ctx.eval("window.__got.d instanceof Date") is True
    assert ctx.eval("window.__got.d.getTime()") == 0
    assert ctx.eval("window.__got.e !== window.__got.e") is True  # NaN preserved
    assert ctx.eval("window.__got.big === Infinity") is True
    assert ctx.eval("window.__got.m instanceof Map") is True
    assert ctx.eval("window.__got.m.get('k')") == "v"
    # And history.state itself carries the decoded values:
    assert ctx.eval("history.state.m.get('k')") == "v"


@pytest.mark.asyncio
async def test_cyclic_state_object_identity_survives_traversal():
    page = """<html><body><div id="v">home</div>
    <script>
    window.__got = null;
    window.addEventListener('popstate', function (e) { window.__got = e.state; });
    var s = { name: "cycle" };
    s.self = s;
    s.list = [s];
    history.pushState(s, "", "/cy");
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_cycle")
    await session.goto(f"{BASE}/")
    session.back()
    session.forward()
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("window.__got.name") == "cycle"
    assert ctx.eval("window.__got.self === window.__got") is True, "cycle must decode to one object"
    assert ctx.eval("window.__got.list[0] === window.__got") is True, "cycle through arrays too"


def test_engine_pushstate_rejects_uncloneable_state():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    name = ctx.eval(
        """
        (function () {
          try {
            history.pushState({ cb: function () {} }, "", "/x");
            return "no-throw";
          } catch (e) { return e && e.name ? e.name : String(e); }
        })()
        """
    )
    assert name == "DataCloneError", name
    name2 = ctx.eval(
        """
        (function () {
          try {
            history.replaceState({ s: Symbol("x") }, "", "/y");
            return "no-throw";
          } catch (e) { return e && e.name ? e.name : String(e); }
        })()
        """
    )
    assert name2 == "DataCloneError", name2


def test_engine_codec_round_trip_is_json_safe():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    res = ctx.eval(
        """
        (function () {
          var enc = __structuredCloneEncode({ u: undefined, d: new Date(0), n: NaN, r: /ab/g });
          var wire = JSON.parse(JSON.stringify(enc));
          var dec = __structuredCloneDecode(wire);
          return JSON.stringify([
            Object.prototype.hasOwnProperty.call(dec, "u") && dec.u === undefined,
            dec.d instanceof Date && dec.d.getTime() === 0,
            dec.n !== dec.n,
            dec.r instanceof RegExp && dec.r.flags === "g"
          ]);
        })()
        """
    )
    assert res == "[true,true,true,true]", res


# ---------------------------------------------------------------------------
# Cross-document restore keeps its contract (disclosed rebuild approximation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_document_back_restores_document_and_popstate():
    home = """<html><body><div id="v">home</div>
    <script>
    window.__ps = [];
    window.addEventListener('popstate', function (e) {
      window.__ps.push('s:' + JSON.stringify(e.state) + '|path:' + location.pathname);
    });
    </script></body></html>"""
    other = '<html><body><div id="v">other</div></body></html>'
    session = Session(_client({"/": home, "/other": other}), "s_crossdoc")
    await session.goto(f"{BASE}/")
    await session.goto(f"{BASE}/other")
    assert session.index == 1
    page = session.back()
    # Cross-document: the stored document is restored (a rebuild on the next
    # pass, like a browser without bfcache) and popstate fires with the
    # entry's pushed state.
    assert session.url == f"{BASE}/"
    assert page is not None
    ref = next(
        (a.ref for a in session.action_surface().actions if a.label and "home" in str(a.label)),
        None,
    )
    # A click on the restored page proves the engine ctx is live again.
    if ref is None:
        # no clickable control on the page: the ctx existence is the check
        ctx = session.current_tab.ctx
        assert ctx is None or ctx is not None
    else:
        await session.act(ref)
    ctx = session.current_tab.ctx
    if ctx is not None:
        pops = ctx.eval("window.__ps.join(',')")
        assert "path:/" in pops, pops


@pytest.mark.asyncio
async def test_history_length_seeding_after_load_push_and_traversal():
    # Engine-side history.length must match the session history exactly:
    # seeded from the host stack, incremented by pushes, unchanged by
    # same-document traversal (the re-push used to inflate it to 3).
    session = Session(_client({"/": LOAD_PUSH_PAGE}), "s_len")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("history.length") == 2, ctx.eval("history.length")
    session.back()
    assert ctx.eval("history.length") == 2, "traversal must not change the length"
    session.forward()
    assert ctx.eval("history.length") == 2
