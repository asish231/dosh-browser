"""Tab-surfing regressions: in-page JS on single-script pages, wheel-delta
scrolling with IntersectionObserver feeds, live DOM serialization after runtime
mutations, and history restore semantics (back/forward/replace).

Hermetic regressions from the tab_surf_check captured scenario, all through the
real session/engine paths; only the HTTP transport is a mock.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _feed_page(name: str, heading: str, next_href: str | None = None) -> str:
    """A single-script page (the case _wants_js used to skip) with an
    IntersectionObserver infinite feed: each batch is 600px tall so the
    sentinel is pushed out of the 720px viewport, exactly like a real feed."""
    if next_href:
        next_link = f'<a href="{next_href}" id="next-link">{name} next</a>'
    else:
        next_link = ""
    return f"""<!DOCTYPE html>
<html><head><title>{name}</title></head>
<body style="margin:0">
<h1>{heading}</h1><a href="/other" id="nav-away">{name} away</a>
{next_link}
<div id="feed"><div class="item">item 1</div><div class="item">item 2</div></div>
<div id="sentinel" style="height:10px">sentinel</div>
<script>
  var batch = 2;
  var io = new IntersectionObserver(function (ents) {{
    for (var i = 0; i < ents.length; i++) {{
      if (!ents[i].isIntersecting) continue;
      var feed = document.getElementById("feed");
      for (var k = 0; k < 3; k++) {{
        batch++;
        var d = document.createElement("div");
        d.className = "item";
        var txt = "item " + batch;
        for (var w = 0; w < 60; w++) txt += " wrapwrapwrapwrapwrapwrapwrap";
        d.textContent = txt;
        feed.appendChild(d);
      }}
    }}
  }});
  io.observe(document.getElementById("sentinel"));
</script>
</body></html>"""


def _client():
    def handler(req: httpx.Request):
        path = req.url.path
        page = {
            "/a": (_feed_page("Alpha", "alpha heading", "/b"), 760),
            "/b": (_feed_page("Beta", "beta heading", None), 760),
            "/other": ("<html><head><title>Other</title></head><body><h1>other page</h1></body></html>", 400),
        }.get(path)
        if page is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        html, height = page
        return httpx.Response(
            200,
            text=html,
            headers={
                "content-type": "text/html",
                "x-page-height": str(height),
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _session():
    return Session(_client(), "s_tab_surf")


# ---------------------------------------------------------------------------
# 1. Single-script pages must run JS (the old _wants_js heuristic skipped them,
#    leaving ctx=None and no IntersectionObserver at all).


@pytest.mark.asyncio
async def test_single_script_page_runs_js():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    assert session.current_tab.ctx is not None, "single <script> page must build a JS context"


# ---------------------------------------------------------------------------
# 2. Browser_scroll is a wheel delta: repeated equal scrolls keep appending
#    feed items through the IntersectionObserver (the old absolute scrollTo
#    made the second and later calls no-ops).


@pytest.mark.asyncio
async def test_repeated_equal_scrolls_grow_feed():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    counts = []
    for _ in range(4):
        await session.scroll(y=600, max_chars=80_000)
        counts.append(session.html.count('class="item"'))
    assert counts[0] >= 5, f"first wheel scroll must load a batch, got {counts}"
    assert len(set(counts)) > 1, f"repeated equal wheel deltas must keep growing the feed: {counts}"
    assert counts[-1] >= 11, f"3 batches x 3 items + initial 2 = >=11, got {counts}"


# ---------------------------------------------------------------------------
# 3. Runtime-created elements must serialize with their className/id:
#    the className property setter must reach the attribute table that the
#    serializer, CSS matcher and action surface all read.


@pytest.mark.asyncio
async def test_runtime_appended_items_serialize_with_class():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    await session.scroll(y=600, max_chars=80_000)
    # Exact token match, not substring games: 'class="item"' must appear as the
    # serializer emits it for runtime-created nodes too.
    serialized = session.html.count('class="item"')
    live_attr = session.current_tab.ctx.eval(
        'document.querySelectorAll(".item").length'
    )
    assert serialized > 0, "runtime-appended items vanished from serialization"
    assert serialized == live_attr, f"serialized {serialized} != live {live_attr}"


# ---------------------------------------------------------------------------
# 4. history restore: back() returns the real live DOM of the previous entry
#    (the old back() only decremented an index and served stale html).


@pytest.mark.asyncio
async def test_back_restores_live_dom_of_previous_entry():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    surf = session.action_surface()
    link = next(a for a in surf.actions if a.label and "Alpha away" in a.label)
    await session.act(link.ref, max_chars=20_000)
    assert "other page" in session.html

    page = session.back()
    assert "alpha heading" in session.html, "back() must restore the previous entry's DOM"

    # The restored page is actionable through a freshly rebuilt surface.
    surf = session.action_surface()
    assert any(
        a.kind == "navigate" for a in surf.actions
    ), "restored page must expose its action surface again"

    # forward() round trip also restores.
    session.forward()
    assert "other page" in session.html


# ---------------------------------------------------------------------------
# 5. In-page actions never create history entries: back() after scrolling must
#    land on the previous *navigation*, not the last scroll.


@pytest.mark.asyncio
async def test_scroll_does_not_create_history_entries():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    await session.scroll(y=600, max_chars=80_000)
    scrolled_items = session.html.count('class="item"')
    await session.goto(f"{BASE}/other", max_chars=20_000)
    assert session.current_tab.index == 1, "one goto after the scroll = one entry"
    session.back()
    assert "alpha heading" in session.html, "back() must restore the pre-navigation entry"
    assert session.html.count('class="item"') == scrolled_items, (
        "the replaced entry must carry the post-scroll DOM, like a real browser"
    )


# ---------------------------------------------------------------------------
# 6. Back after repeated navigations lands on the genuinely previous document.


@pytest.mark.asyncio
async def test_back_after_navigation_flurry():
    session = _session()
    await session.goto(f"{BASE}/a", max_chars=80_000)
    for _ in range(3):
        await session.goto(f"{BASE}/b", max_chars=20_000)
        await session.goto(f"{BASE}/other", max_chars=20_000)
        await session.goto(f"{BASE}/a", max_chars=80_000)
    assert session.current_tab.index == 9, "each goto must push exactly one entry"
    session.back()
    assert "other page" in session.html, "back() after flurry must land on the previous document"
    seq = session.back()
    assert "beta heading" in (seq.view or session.html)
