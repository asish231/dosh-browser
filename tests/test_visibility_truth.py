"""Action-surface visibility truth: the surface must match what a user can
actually see and click — in both directions.

Before this bundle the surface lied both ways: page JS revealing a control
with el.style.display = '...' (the React/jQuery pattern) never reached the
style ATTRIBUTE the cascade reads, so JS-revealed controls stayed invisible
to checkVisibility and the action surface while the serialized HTML kept
claiming hidden; and the [hidden] attribute plus collapsed <details> content
were never consulted at all, so controls a real browser would never show
were offered as actionable.
"""

import httpx
import pytest

from webresearch.actions import collect
from webresearch.parse import parse
from webresearch.session import Session
from webresearch.types import FetchResult

BASE = "https://test.local"


def _client(page: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )
    )


REVEAL_PAGE = """<html><body>
<div style="display:none"><button id="jsRevealed">JS-revealed</button></div>
<div id="hid" hidden><button id="hb">HiddenAttr</button></div>
<details id="det"><summary id="sum">More</summary><button id="detBtn">InsideDetails</button></details>
<details id="detOpen" open><summary>Open</summary><button id="odBtn">OpenDetails</button></details>
<button id="v1">VisibleOne</button>
<script>
  document.querySelector('#jsRevealed').parentNode.style.display = 'block';
  window.__ready = 1;
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_js_style_reveal_reaches_visibility():
    """el.style.display = 'block' on a hidden parent reveals the child to
    checkVisibility — the exact React/jQuery reveal pattern."""
    session = Session(_client(REVEAL_PAGE), "s_vis_reveal")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    got = ctx.eval(
        "(function(){return document.querySelector('#jsRevealed').parentNode.getAttribute('style');})()"
    )
    assert got == "display: block", f"style property write must sync the attribute, got {got!r}"
    assert ctx.eval("document.getElementById('jsRevealed').checkVisibility()") is True


@pytest.mark.asyncio
async def test_js_revealed_control_is_actionable():
    session = Session(_client(REVEAL_PAGE), "s_vis_reveal2")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert "JS-revealed" in labels, f"revealed control must be listed: {labels}"


@pytest.mark.asyncio
async def test_hidden_attribute_control_not_actionable():
    session = Session(_client(REVEAL_PAGE), "s_vis_hidden")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("document.getElementById('hb').checkVisibility()") is False
    surf = session.action_surface()
    assert "HiddenAttr" not in [a.label for a in surf.actions]


@pytest.mark.asyncio
async def test_closed_details_content_hidden_summary_visible():
    session = Session(_client(REVEAL_PAGE), "s_vis_details")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("document.getElementById('detBtn').checkVisibility()") is False, (
        "closed-details content must be hidden"
    )
    assert ctx.eval("document.getElementById('sum').checkVisibility()") is True
    assert ctx.eval("document.getElementById('odBtn').checkVisibility()") is True, (
        "open-details content stays visible"
    )
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert "InsideDetails" not in labels and "More" in labels and "OpenDetails" in labels


@pytest.mark.asyncio
async def test_full_surface_truth():
    """One page, every hiding/revealing method: only what a user could click
    is listed, and the hidden count is honest."""
    session = Session(_client(REVEAL_PAGE), "s_vis_full")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert labels == ["JS-revealed", "More", "Open", "OpenDetails", "VisibleOne"], labels
    assert surf.hidden_controls == 2, "HiddenAttr + InsideDetails counted, never dropped"


@pytest.mark.asyncio
async def test_empty_string_style_unhides():
    """el.style.display = '' removes the declaration (real-browser un-hide)."""
    page = """<html><body>
<button id="b" style="display:none">B</button>
<script>window.__ready=1;</script></body></html>"""
    session = Session(_client(page), "s_vis_empty")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    ctx.eval("document.getElementById('b').style.display = ''; true")
    assert ctx.eval("document.getElementById('b').checkVisibility()") is True


@pytest.mark.asyncio
async def test_style_attribute_and_object_are_one_state():
    page = """<html><body><button id="b">B</button>
<script>window.__ready=1;</script></body></html>"""
    session = Session(_client(page), "s_vis_roundtrip")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    # setAttribute -> object (spec)
    ctx.eval("document.getElementById('b').setAttribute('style', 'color: red'); true")
    assert ctx.eval("document.getElementById('b').style.color") == "red"
    # object -> setAttribute (the sync this bundle adds)
    ctx.eval("document.getElementById('b').style.fontWeight = 'bold'; true")
    attr = ctx.eval("document.getElementById('b').getAttribute('style')")
    assert "color: red" in attr and "font-weight: bold" in attr, attr
    # a REPLACING setAttribute drops prior declarations (spec)
    ctx.eval("document.getElementById('b').setAttribute('style', 'margin: 4px'); true")
    attr2 = ctx.eval("document.getElementById('b').getAttribute('style')")
    assert "color" not in attr2 and "margin: 4px" in attr2, attr2
    assert ctx.eval("document.getElementById('b').style.color") == "", (
        "replaced declaration must be gone from the object too"
    )


@pytest.mark.asyncio
async def test_author_css_overrides_hidden_attribute():
    """Author display beats the UA [hidden] rule, like a real cascade."""
    page = """<html><head><style>#keep[hidden] { display: block; }</style></head><body>
<button id="keep" hidden>Kept</button>
<button id="gone" hidden>Gone</button>
<script>window.__ready=1;</script></body></html>"""
    session = Session(_client(page), "s_vis_author")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("document.getElementById('keep').checkVisibility()") is True, (
        "author rule must override the UA [hidden] rule"
    )
    assert ctx.eval("document.getElementById('gone').checkVisibility()") is False
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert "Kept" in labels and "Gone" not in labels, labels


@pytest.mark.asyncio
async def test_details_opening_reveals_content():
    """Clicking the summary (or JS setting open) reveals the content live."""
    page = """<html><body>
<details id="det"><summary>More</summary><button id="detBtn">Inside</button></details>
<script>document.getElementById('det').addEventListener('toggle', function(){ window.__toggled = true; });
window.__ready=1;</script></body></html>"""
    session = Session(_client(page), "s_vis_toggle")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    assert "Inside" not in [a.label for a in surf.actions]
    # The page's own JS opens it (setAttribute path); an action pass then
    # re-serializes the mutated document so the surface sees it.
    ctx = session.current_tab.ctx
    ctx.eval("document.getElementById('det').setAttribute('open', ''); true")
    assert ctx.eval("document.getElementById('detBtn').checkVisibility()") is True
    await session.scroll(y=1, max_chars=20_000)
    surf = session.action_surface()
    assert "Inside" in [a.label for a in surf.actions], "opened details content must surface"


@pytest.mark.asyncio
async def test_scriptless_fallback_catches_hidden_and_details():
    """No engine ctx (scriptless page): the inline/ancestor flag is the
    fallback truth and Session.action_surface filters on it."""
    html = """<html><body>
<button id="h" hidden>HiddenAttr</button>
<details><summary>More</summary><button id="d">InsideDetails</button></details>
<div hidden><button id="ha">HiddenAncestor</button></div>
<button id="v">VisibleOne</button>
</body></html>"""
    session = Session(_client(html), "s_vis_scriptless")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx is None, "scriptless page must have no engine ctx"
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert "VisibleOne" in labels and "More" in labels, labels
    assert "HiddenAttr" not in labels, "[hidden] must be filtered without an engine"
    assert "HiddenAncestor" not in labels, "hidden ancestor must be filtered without an engine"
    assert "InsideDetails" not in labels, "closed-details content must be filtered without an engine"
    assert surf.hidden_controls == 3
    assert not surf.stylesheet_unverified, "no <style> block on this page"


@pytest.mark.asyncio
async def test_scriptless_with_stylesheet_discloses_unverified():
    """No engine + a <style> block: the surface must say stylesheet hiding
    could not be verified instead of silently claiming a clean list."""
    html = """<html><head><style>.hide { display: none; }</style></head><body>
<button id="h" class="hide">SheetHidden</button>
<button id="v">VisibleOne</button>
</body></html>"""
    session = Session(_client(html), "s_vis_unverified")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    assert surf.stylesheet_unverified is True
    line = surf.coverage_line()
    assert "stylesheet-hidden controls cannot be verified" in line, line
    # The inline/hidden-attr filtering still applied where it can:
    assert "VisibleOne" in [a.label for a in surf.actions]


@pytest.mark.asyncio
async def test_mutation_observer_sees_style_writes():
    """Style mutations now produce attribute records (they silently didn't
    before the sync)."""
    page = """<html><body><button id="b">B</button>
<script>
window.__recs = [];
var mo = new MutationObserver(function(muts){ muts.forEach(function(m){ window.__recs.push(m.attributeName); }); });
mo.observe(document.getElementById('b'), { attributes: true });
window.__ready = 1;
</script></body></html>"""
    session = Session(_client(page), "s_vis_mo")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    ctx.eval("document.getElementById('b').style.display = 'none'; true")
    # Observer records deliver on microtask drain: an action pass pumps.
    await session.scroll(y=1, max_chars=20_000)
    recs = ctx.eval("(window.__recs||[]).join(',')")
    assert "style" in recs, f"style write must produce an attribute record, got {recs!r}"
