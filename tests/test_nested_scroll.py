"""Nested scroll-container regressions (round 5).

The gap: `scrollIntoView` drove the top-level document only. An element inside an
`overflow:auto|scroll|hidden` pane could not be brought into view at all, element
`scrollTop`/`scrollLeft` were plain expando fields (no clamp, no scroll event, no
effect on scrollHeight), and a `display:none` target scrolled the window to a
phantom position because the hidden subtree kept the previous layout pass's box.

Every assertion reads the specific element, never a page substring.
"""

from __future__ import annotations

import json

import httpx
import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.session import Session

BASE = "https://scroll.test"

# Layout-critical blocks carry explicit heights; the pane is 200px tall with
# 840px of content, so its scroll range is deterministic.
PANE_PAGE = """<html><body style="margin:0">
<div style="height:50px"></div>
<div id="pane" style="height:200px;overflow:auto">
  <div style="height:600px"></div>
  <div id="target" style="height:40px"></div>
  <div style="height:200px"></div>
</div>
<div style="height:600px"></div>
</body></html>"""

HIDDEN_PAGE = """<html><body style="margin:0">
<div id="pane" style="height:200px;overflow:auto">
  <div style="height:600px"></div>
  <div id="target" style="height:40px"></div>
  <div id="ghost" style="display:none;height:40px"></div>
</div>
</body></html>"""


def _ctx(html: str) -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    ctx.eval(f"var __html = {json.dumps(html)};")
    ctx.eval('var __tree = host_parse_html(__html); __load(JSON.parse(__tree), "https://scroll.test/"); true;')
    return ctx


def _boxes(ctx: Context) -> dict:
    raw = ctx.eval(
        "JSON.stringify((function(){"
        "var p=document.getElementById('pane'), t=document.getElementById('target');"
        "var pb=p.getBoundingClientRect(), tb=t.getBoundingClientRect();"
        "return {top:pb.top,height:pb.height,ttop:tb.top,theight:tb.height,"
        "docMax:document.documentElement.scrollHeight-innerHeight};})())"
    )
    return json.loads(raw)


def _scroll(ctx: Context, which: str = "target", opts: str = "") -> None:
    ctx.eval(f"document.getElementById('{which}').scrollIntoView({opts}); true;")


def _state(ctx: Context) -> dict:
    raw = ctx.eval(
        "JSON.stringify({pane:document.getElementById('pane').scrollTop,"
        "y:scrollY,x:scrollX})"
    )
    return json.loads(raw)


# ---------------------------------------------------------------------------
# 1. scrollIntoView drives the nested pane, per alignment mode
# ---------------------------------------------------------------------------


def test_scroll_into_view_start_scrolls_the_nested_pane():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    assert b["height"] == pytest.approx(200, abs=1)
    _scroll(ctx, "target", '{block:"start"}')
    st = _state(ctx)
    assert st["pane"] == pytest.approx(b["ttop"] - b["top"], abs=2), st


def test_scroll_into_view_end_aligns_bottom_edge():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    _scroll(ctx, "target", '{block:"end"}')
    st = _state(ctx)
    expected = b["ttop"] + b["theight"] - b["top"] - b["height"]
    assert st["pane"] == pytest.approx(expected, abs=2), st


def test_scroll_into_view_center_and_nearest():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    _scroll(ctx, "target", '{block:"center"}')
    centered = _state(ctx)["pane"]
    expected_center = (b["ttop"] - b["top"]) - (b["height"] - b["theight"]) / 2
    assert centered == pytest.approx(expected_center, abs=2)

    ctx.eval("document.getElementById('pane').scrollTop = 0; true;")
    _scroll(ctx, "target", '{block:"nearest"}')
    nearest = _state(ctx)["pane"]
    expected_nearest = b["ttop"] + b["theight"] - b["top"] - b["height"]
    assert nearest == pytest.approx(expected_nearest, abs=2)


def test_scroll_into_view_offsets_the_document_by_container_travel():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    _scroll(ctx, "target", '{block:"start"}')
    st = _state(ctx)
    # The pane's travel moves the target up relative to the viewport, so the
    # window target is the element's position MINUS that travel, clamped to the
    # document's scroll range.
    want_y = min(max(b["ttop"] - st["pane"], 0), b["docMax"])
    assert st["y"] == pytest.approx(want_y, abs=3), (st, b)


def test_scroll_into_view_if_needed_noop_when_already_visible():
    ctx = _ctx(PANE_PAGE)
    ctx.eval("document.getElementById('pane').scrollTop = 600; scrollTo(0, 0); true;")
    before = _state(ctx)
    ctx.eval("document.getElementById('target').scrollIntoViewIfNeeded(); true;")
    after = _state(ctx)
    assert after == before


def test_scroll_into_view_if_needed_reveals_hidden_target():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    ctx.eval("document.getElementById('target').scrollIntoViewIfNeeded(); true;")
    st = _state(ctx)
    # WebKit default: centerIfNeeded=true centers the not-fully-visible element
    # inside the pane: contentPos - (paneHeight - targetHeight)/2.
    expected = (b["ttop"] - b["top"]) - (b["height"] - b["theight"]) / 2
    assert st["pane"] == pytest.approx(expected, abs=2), st


def test_scroll_into_view_if_needed_false_scrolls_minimum():
    ctx = _ctx(PANE_PAGE)
    b = _boxes(ctx)
    ctx.eval("document.getElementById('target').scrollIntoViewIfNeeded(false); true;")
    st = _state(ctx)
    # centerIfNeeded=false: nearest — the minimal scroll that reveals the
    # element (bottom edge aligned to the pane's bottom).
    expected = b["ttop"] + b["theight"] - b["top"] - b["height"]
    assert st["pane"] == pytest.approx(expected, abs=2), st


# ---------------------------------------------------------------------------
# 2. Element scroll state: clamp, event, no bubbling
# ---------------------------------------------------------------------------


def test_element_scroll_top_clamps_to_content_extent():
    ctx = _ctx(PANE_PAGE)
    sh = ctx.eval("document.getElementById('pane').scrollHeight")
    ch = ctx.eval("document.getElementById('pane').clientHeight")
    assert sh >= 800, sh
    ctx.eval("document.getElementById('pane').scrollTop = 99999; true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") == pytest.approx(sh - ch, abs=1)
    ctx.eval("document.getElementById('pane').scrollTop = -500; true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") == 0


def test_element_scroll_fires_scroll_event_without_bubbling():
    ctx = _ctx(PANE_PAGE)
    ctx.eval(
        """
        window.__paneScrolls = 0;
        window.__docScrolls = 0;
        document.getElementById('pane').addEventListener('scroll', function () { window.__paneScrolls += 1; });
        document.addEventListener('scroll', function () { window.__docScrolls += 1; });
        true;
        """
    )
    ctx.eval("document.getElementById('pane').scrollTop = 100; true;")
    assert ctx.eval("window.__paneScrolls") == 1
    assert ctx.eval("window.__docScrolls") == 0, "element scroll events must not bubble"
    ctx.eval("document.getElementById('pane').scrollTop = 100; true;")
    assert ctx.eval("window.__paneScrolls") == 1, "unchanged offset must not re-fire"


def test_element_scrollto_and_scrollby_drive_the_pane():
    ctx = _ctx(PANE_PAGE)
    assert ctx.eval("typeof document.getElementById('pane').scrollTo") == "function"
    ctx.eval("document.getElementById('pane').scrollTo({top: 50, left: 0}); true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") == 50
    ctx.eval("document.getElementById('pane').scrollBy(0, 25); true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") == 75


def test_overflow_visible_is_not_a_scroll_container():
    ctx = _ctx(
        """<html><body style="margin:0">
        <div id="plain" style="height:100px">
          <div style="height:500px"></div>
        </div></body></html>"""
    )
    ctx.eval("document.getElementById('plain').scrollTop = 120; true;")
    assert ctx.eval("document.getElementById('plain').scrollTop") == 0


def test_stylesheet_overflow_longhand_makes_the_pane_scrollable():
    ctx = _ctx(
        """<html><head><style>
        #pane { height: 100px; overflow-y: auto; }
        </style></head><body style="margin:0">
        <div id="pane"><div style="height:500px"></div><div id="target" style="height:20px"></div></div>
        </body></html>"""
    )
    assert ctx.eval('getComputedStyle(document.getElementById("pane")).overflowY') == "auto"
    ctx.eval("document.getElementById('pane').scrollTop = 120; true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") == 120
    ctx.eval("document.getElementById('target').scrollIntoView({block:'end'}); true;")
    assert ctx.eval("document.getElementById('pane').scrollTop") > 0


# ---------------------------------------------------------------------------
# 3. Content extent feeds scrollHeight/scrollWidth and the clamp
# ---------------------------------------------------------------------------


def test_scroll_height_and_width_reflect_overflowing_children():
    ctx = _ctx(
        """<html><body style="margin:0">
        <div id="pane" style="height:150px;width:200px;overflow:auto">
          <div style="height:900px;width:700px"></div>
        </div></body></html>"""
    )
    assert ctx.eval("document.getElementById('pane').scrollHeight") >= 900
    assert ctx.eval("document.getElementById('pane').scrollWidth") >= 700
    ctx.eval("document.getElementById('pane').scrollLeft = 9999; true;")
    left = ctx.eval("document.getElementById('pane').scrollLeft")
    assert left == pytest.approx(ctx.eval("document.getElementById('pane').scrollWidth") - 200, abs=2)


# ---------------------------------------------------------------------------
# 4. Hidden targets are not scroll targets (no phantom coordinates)
# ---------------------------------------------------------------------------


def test_display_none_target_does_not_scroll_anything():
    ctx = _ctx(HIDDEN_PAGE)
    before = _state(ctx)
    _scroll(ctx, "ghost")
    after = _state(ctx)
    assert after == before == {"pane": 0, "y": 0, "x": 0}


def test_ancestor_hidden_after_layout_drops_the_stale_box():
    ctx = _ctx(PANE_PAGE)
    assert ctx.eval("document.getElementById('target').getBoundingClientRect().top") > 0
    tall_extent = ctx.eval("document.documentElement.scrollHeight")
    ctx.eval("document.getElementById('pane').style.display = 'none'; true;")
    # The hidden subtree must lose its stale box: the target reports the
    # spec's all-zero rect, not its previous-layout position.
    r = ctx.eval("JSON.stringify(document.getElementById('target').getBoundingClientRect())")
    rect = json.loads(r)
    assert rect["x"] == rect["y"] == rect["top"] == rect["left"] == rect["bottom"] == rect["right"] == rect["width"] == rect["height"] == 0, r
    # And the pane's 840px of content leaves the document extent, so the
    # window can no longer scroll into the space it used to occupy.
    assert ctx.eval("document.documentElement.scrollHeight") < tall_extent
    ctx.eval("scrollTo(0, 40); true;")
    assert ctx.eval("scrollY") == 0
    _scroll(ctx, "target")
    assert ctx.eval("scrollY") == 0, "a hidden target must not move the viewport"


def test_detached_element_is_not_a_scroll_target():
    ctx = _ctx(PANE_PAGE)
    ctx.eval(
        """
        window.__victim = document.getElementById('target');
        window.__victim.parentNode.removeChild(window.__victim);
        true;
        """
    )
    ctx.eval("window.__victim.scrollIntoView(); true;")
    assert ctx.eval("scrollY") == 0


# ---------------------------------------------------------------------------
# 5. IntersectionObserver sees the container's clip
# ---------------------------------------------------------------------------


def test_intersection_observer_fires_when_pane_scrolls_target_out():
    ctx = _ctx(PANE_PAGE)
    ctx.eval(
        """
        window.__io = [];
        var obs = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) { window.__io.push(e.isIntersecting); });
        });
        obs.observe(document.getElementById('target'));
        true;
        """
    )
    # The target starts below the pane's fold: clipped by the container, it is
    # NOT intersecting even though the pane itself is inside the viewport.
    ctx.eval("document.getElementById('target').scrollIntoView({block:'start'}); true;")
    assert ctx.eval("window.__io[window.__io.length - 1]") is True
    ctx.eval("document.getElementById('pane').scrollTop = 0; true;")  # scrolled out again
    assert ctx.eval("window.__io[window.__io.length - 1]") is False, (
        "an element scrolled out of its overflow pane must not intersect"
    )


# ---------------------------------------------------------------------------
# 6. Smooth behavior steps instead of teleporting
# ---------------------------------------------------------------------------


def test_smooth_scroll_reports_intermediate_positions():
    ctx = _ctx(PANE_PAGE)
    ctx.eval(
        """
        window.__panePositions = [];
        window.__winPositions = [];
        document.getElementById('pane').addEventListener('scroll', function () {
          window.__panePositions.push(document.getElementById('pane').scrollTop);
        });
        window.addEventListener('scroll', function () { window.__winPositions.push(Math.round(scrollY)); });
        true;
        """
    )
    b = _boxes(ctx)
    _scroll(ctx, "target", '{block:"start", behavior:"smooth"}')
    pane_positions = json.loads(ctx.eval("JSON.stringify(window.__panePositions)"))
    assert len(pane_positions) > 2, pane_positions
    assert pane_positions == sorted(pane_positions), "a smooth scroll must progress monotonically"
    assert pane_positions[-1] == pytest.approx(b["ttop"] - b["top"], abs=2)
    assert abs(ctx.eval("scrollY") - min(max(b["ttop"] - pane_positions[-1], 0), b["docMax"])) < 3


# ---------------------------------------------------------------------------
# 7. Cascade longhands (the scroll lane reads them; pages read them)
# ---------------------------------------------------------------------------


def test_stylesheet_multi_word_properties_reach_computed_style():
    ctx = _ctx(
        """<html><head><style>
        #s { font-weight: 700; line-height: 2; background-color: rgb(1, 2, 3); }
        </style></head><body><div id="s" style="font-style: italic">t</div></body></html>"""
    )
    assert ctx.eval('getComputedStyle(document.getElementById("s")).fontWeight') == "700"
    assert ctx.eval('getComputedStyle(document.getElementById("s")).lineHeight') == "2"
    assert ctx.eval('getComputedStyle(document.getElementById("s")).backgroundColor') == "rgb(1, 2, 3)"
    assert ctx.eval('getComputedStyle(document.getElementById("s")).fontStyle') == "italic"


# ---------------------------------------------------------------------------
# 8. End-to-end through a real session action pass
# ---------------------------------------------------------------------------


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        body = pages.get(req.url.path, "<html><body>404</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_nested_scroll_state_survives_an_action_pass():
    # The <script> tag is what makes goto() build the QuickJS ctx; without it
    # there is no engine context to carry element scroll state.
    page = """<html><body style="margin:0">
    <div id="pane" style="height:150px;overflow:auto">
      <div style="height:700px"></div>
      <button id="b" onclick="window.__clicked = (window.__clicked||0)+1">go</button>
    </div>
    <script>window.__ready = true;</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_nested_scroll")
    await session.goto(f"{BASE}/", max_chars=4000)
    ctx = session.current_tab.ctx
    assert ctx is not None, "page has a script; goto() must build the engine ctx"
    ctx.eval("document.getElementById('pane').scrollTop = 200; true;")
    surf = session.action_surface()
    ref = next(a.ref for a in surf.actions if a.label == "go")
    await session.act(ref)
    assert session.current_tab.ctx.eval("document.getElementById('pane').scrollTop") == 200
    assert session.current_tab.ctx.eval("window.__clicked") == 1