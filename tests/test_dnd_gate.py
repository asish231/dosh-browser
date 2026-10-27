"""DnD drop-gate regressions: drop is delivered only when the target
cancels dragover (preventDefault = acceptance per spec), dragging to empty
space delivers dragend only (no phantom drop), and DataTransfer types are
ASCII-lowercased with case-insensitive setData/getData.

Before this fix the engine reported drops a real browser would never deliver:
drop fired unconditionally after dragover (no gate), a miss in the hit-test
fell back to dropping on the source itself, and 'Text/Plain' never matched
'text/plain'.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

PAGE_TMPL = """<html><body>
<div id="src" draggable="true" style="width:100px;height:40px">drag me</div>
<div style="height:50px"></div>
<div id="zone" style="width:200px;height:100px">zone</div>
<script>
window.__ev = [];
var src = document.getElementById('src'), zone = document.getElementById('zone');
src.addEventListener('dragstart', function (e) {{ e.dataTransfer.setData('Text/Plain', 'P1'); }});
zone.addEventListener('dragover', function (e) {{ {dragover} }});
zone.addEventListener('drop', function (e) {{ window.__ev.push('DROP:' + e.dataTransfer.getData('text/plain')); }});
src.addEventListener('drag', function () {{ window.__ev.push('drag'); }});
src.addEventListener('dragend', function () {{ window.__ev.push('dragend'); }});
window.__ready = 1;
</script></body></html>"""

ACCEPT = "e.preventDefault(); window.__ev.push('dragover');"
NO_ACCEPT = "window.__ev.push('dragover');"


def _client(page: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )
    )


async def _drag(page: str, dy: int = 200) -> str:
    session = Session(_client(page), "s_dnd")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("__dragSelector('#src', 0, %d, 3, null, false)" % dy)
    return ctx.eval("window.__ev.join(',')")


@pytest.mark.asyncio
async def test_drop_requires_dragover_acceptance():
    """Target does NOT preventDefault dragover: no drop may be reported."""
    ev = await _drag(PAGE_TMPL.format(dragover=NO_ACCEPT))
    assert "dragover" in ev, ev
    assert "DROP:" not in ev, f"drop fired without the dragover gate: {ev}"
    assert "dragend" in ev, "dragend must always fire on the source"


@pytest.mark.asyncio
async def test_drop_fires_when_target_accepts():
    ev = await _drag(PAGE_TMPL.format(dragover=ACCEPT))
    assert "dragover" in ev and "dragend" in ev, ev
    assert "DROP:P1" in ev, (
        f"accepted drop must fire with the payload: {ev}"
    )


@pytest.mark.asyncio
async def test_drag_to_empty_space_delivers_nothing():
    """Source dragged far outside any element: dragend only — no phantom
    drop on the source's own position."""
    ev = await _drag(PAGE_TMPL.format(dragover=ACCEPT), dy=5000)
    assert ev == "drag,drag,drag,dragend", (
        f"empty-space drag must end without dragover/drop: {ev}"
    )


@pytest.mark.asyncio
async def test_datatransfer_types_lowercase_and_case_insensitive():
    page = """<html><body><script>
window.__r = [];
var dt = new DataTransfer();
dt.setData('Text/Plain', 'A');
window.__r.push(JSON.stringify(dt.types));
window.__r.push(dt.getData('text/plain'));
window.__r.push(dt.getData('TEXT/PLAIN'));
dt.setData('x-custom', 'B');
window.__r.push(dt.getData('X-Custom'));
window.__ready = 1;
</script></body></html>"""
    session = Session(_client(page), "s_dt")
    await session.goto(f"{BASE}/", max_chars=20_000)
    got = session.current_tab.ctx.eval("window.__r.join('|')")
    parts = got.split("|")
    assert parts[0] == '["text/plain"]', f"types must be lowercased: {parts[0]}"
    assert parts[1] == "A" and parts[2] == "A", "getData folds case per spec"
    assert parts[3] == "B", "setData/getData must fold case both ways"
