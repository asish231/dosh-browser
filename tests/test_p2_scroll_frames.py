"""P2-4/P2-8 regressions: per-axis scroll clamping with real document extents,
and parent-to-frame postMessage delivery.

Before these fixes: _clampScroll clamped BOTH scroll axes with the vertical
max (docHeight - viewport), and documentElement's layout box never reflected
overflow content, so wide pages could not scroll at all; and runtime.py passed
the frame URL as __receiveMessage's targetOrigin, so every parent-to-frame
and reply message failed the child's origin check and was silently dropped.
All probes run the real session/engine paths; only the HTTP transport is a
mock.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

WIDE_PAGE = """<html><body>
<div style="width:5000px;height:1500px">wide</div>
<script>window.__ready=1;</script>
</body></html>"""

TALL_PAGE = """<html><body>
<div style="height:3000px">tall</div>
<script>window.__ready=1;</script>
</body></html>"""

NORMAL_PAGE = """<html><body>
<div>Normal content</div>
<script>window.__ready=1;</script>
</body></html>"""

MARGIN0_PAGE = """<html><body style="margin: 0;">
<div style="width: 100%;">Full width content</div>
<script>window.__ready=1;</script>
</body></html>"""


def _client() -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        if req.url.path == "/tall":
            body = TALL_PAGE
        elif req.url.path == "/normal":
            body = NORMAL_PAGE
        elif req.url.path == "/margin0":
            body = MARGIN0_PAGE
        else:
            body = WIDE_PAGE
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _ctx(path: str = "/wide"):
    session = Session(_client(), "s_scroll")
    await session.goto(f"{BASE}{path}", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    return ctx


# ---------------------------------------------------------------------------
# Document scroll extents


@pytest.mark.asyncio
async def test_documentelement_scroll_extent_reflects_overflow():
    ctx = await _ctx()
    sw = ctx.eval("document.documentElement.scrollWidth")
    sh = ctx.eval("document.documentElement.scrollHeight")
    # 5000px child + 8px body margin; height 1500 + margins.
    assert sw > 4900, f"scrollWidth must reflect the 5000px child, got {sw}"
    assert sh > 1400, f"scrollHeight must reflect the 1500px child, got {sh}"


@pytest.mark.asyncio
async def test_document_scroll_width_aliases_documentelement():
    ctx = await _ctx()
    assert ctx.eval(
        "document.scrollWidth === document.documentElement.scrollWidth"
    )


@pytest.mark.asyncio
async def test_body_scroll_extent_reflects_content_not_box_width():
    ctx = await _ctx("/wide")
    body_sw = ctx.eval("document.body.scrollWidth")
    de_sw = ctx.eval("document.documentElement.scrollWidth")
    assert body_sw > 4900, f"body.scrollWidth must reflect 5000px content extent, got {body_sw}"
    assert body_sw == de_sw

    ctx_tall = await _ctx("/tall")
    body_sh = ctx_tall.eval("document.body.scrollHeight")
    de_sh = ctx_tall.eval("document.documentElement.scrollHeight")
    assert body_sh > 2500, f"body.scrollHeight must reflect 3000px content extent, got {body_sh}"
    assert body_sh == de_sh


@pytest.mark.asyncio
async def test_clean_page_has_zero_phantom_horizontal_scroll():
    ctx = await _ctx("/normal")
    de_sw = ctx.eval("document.documentElement.scrollWidth")
    body_sw = ctx.eval("document.body.scrollWidth")
    assert de_sw == 1280, f"documentElement.scrollWidth must be 1280 (no phantom 8px), got {de_sw}"
    assert body_sw == 1280, f"body.scrollWidth must be 1280 (no phantom 8px), got {body_sw}"

    # Verify horizontal scroll clamps strictly to 0
    got = ctx.eval(
        "(function(){scrollTo(100, 0); return [scrollX, scrollY].join(',');})()"
    )
    assert got == "0,0", f"normal page must clamp horizontal scroll to 0, got {got}"


@pytest.mark.asyncio
async def test_styled_body_margin0_has_zero_phantom_horizontal_scroll():
    ctx = await _ctx("/margin0")
    de_sw = ctx.eval("document.documentElement.scrollWidth")
    body_sw = ctx.eval("document.body.scrollWidth")
    assert de_sw == 1280, f"documentElement.scrollWidth must be 1280, got {de_sw}"
    assert body_sw == 1280, f"body.scrollWidth must be 1280, got {body_sw}"

    got = ctx.eval(
        "(function(){scrollTo(50, 0); return [scrollX, scrollY].join(',');})()"
    )
    assert got == "0,0", f"margin:0 page must clamp horizontal scroll to 0, got {got}"


# ---------------------------------------------------------------------------
# Per-axis clamping


@pytest.mark.asyncio
async def test_horizontal_scroll_clamps_to_its_own_extent():
    ctx = await _ctx()
    got = ctx.eval(
        "(function(){scrollTo(4000, 0); return [scrollX, scrollY].join(',');})()"
    )
    # maxX = scrollWidth - innerWidth; the old code clamped x by the vertical
    # max (~826), which made wide pages unscrollable.
    assert got.startswith("372") or got.startswith("373"), (
        f"horizontal clamp should be ~scrollWidth - innerWidth (3728), got {got}"
    )
    assert got.endswith(",0")


@pytest.mark.asyncio
async def test_vertical_scroll_clamps_to_its_own_extent():
    ctx = await _ctx("/tall")
    got = ctx.eval(
        "(function(){scrollTo(0, 5000); return [scrollX, scrollY].join(',');})()"
    )
    x, y = got.split(",")
    assert x == "0"
    # maxY = scrollHeight - innerHeight (~2326 for a 3000px page).
    assert 2200 < float(y) < 2500, f"vertical clamp out of range: {y}"


@pytest.mark.asyncio
async def test_smooth_and_scrollby_use_the_same_clamp():
    ctx = await _ctx()
    smooth = ctx.eval("(function(){__scrollSmooth(4000, 0, 4); return scrollX;})()")
    assert 3700 < float(smooth) < 3800, f"smooth scroll clamp wrong: {smooth}"
    ctx.eval("scrollTo(0, 0); true")
    # First wheel adds 3000 (under the max, no clamp); the second crosses it.
    first = ctx.eval("(function(){scrollBy(3000, 0); return scrollX;})()")
    assert float(first) == 3000, f"scrollBy must add a delta, got {first}"
    second = ctx.eval("(function(){scrollBy(3000, 0); return scrollX;})()")
    assert 3700 < float(second) < 3800, f"scrollBy clamp wrong: {second}"


@pytest.mark.asyncio
async def test_negative_scroll_clamps_to_zero():
    ctx = await _ctx()
    got = ctx.eval(
        "(function(){scrollTo(-500, -500); return [scrollX, scrollY].join(',');})()"
    )
    assert got == "0,0"


# ---------------------------------------------------------------------------
# Parent -> frame postMessage


PARENT = """<html><body>
<h1>parent</h1>
<iframe id="f" src="https://child.local/child"></iframe>
<script>
  try {
    document.getElementById('f').contentWindow.postMessage('hello-child', 'https://child.local');
    window.__sent = true;
  } catch (e) { window.__sent = 'ERR:' + e.message; }
</script>
</body></html>"""

CHILD = """<html><body>
<h1>child</h1>
<div id="got">nothing</div>
<script>
  window.addEventListener('message', function (e) {
    document.getElementById('got').textContent = 'GOT:' + e.data + '|from:' + e.origin;
  });
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_parent_to_frame_postmessage_delivers():
    def handler(req: httpx.Request):
        if req.url.host == "child.local":
            return httpx.Response(200, text=CHILD, headers={"content-type": "text/html"})
        return httpx.Response(200, text=PARENT, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_frame")
    await session.goto("https://parent.local/", max_chars=20_000)

    assert session.current_tab.ctx.eval("window.__sent") is True
    frames = session.frame_documents
    assert frames, "iframe must load as a frame document"
    child = frames[0]
    assert child.url == "https://child.local/child"
    assert "GOT:hello-child" in child.html, (
        "parent-to-frame message was dropped: the child's listener never ran"
    )
    assert "from:https://parent.local" in child.html
