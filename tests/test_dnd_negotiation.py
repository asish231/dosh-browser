"""DnD dropEffect/effectAllowed negotiation regressions (round 8).

The two verified gaps this file pins:

1. No operation negotiation: dragenter/dragover never initialized dropEffect
   to the default for the source's effectAllowed, and any value (including
   garbage like "banana") was accepted on both attributes. Per the spec
   processing model the user agent initializes dropEffect before dragenter
   and dragover, the listener may override it (validated against
   effectAllowed), a canceled dragover sets the drag operation to the
   resulting dropEffect, and drop/dragend carry the negotiated operation.
2. Phantom DnD: a NON-draggable element with a dragstart listener got a full
   native drag sequence (dragstart/drag/dragend) — a real browser fires none
   of those (only draggable=true initiates DnD; text-selection drags are a
   separate lane this engine does not model).

Every assertion reads specific engine state, never page substrings.
"""

from __future__ import annotations

import httpx
import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.session import Session

BASE = "https://dnd.test"

# Source declares effectAllowed; zone accepts dragover and (optionally)
# overrides dropEffect. Every handler logs the operation it observed.
PAGE_TMPL = """<html><body>
<div id="src" draggable="true" style="width:100px;height:40px">s</div>
<div style="height:50px"></div>
<div id="zone" style="width:200px;height:100px">z</div>
<script>
window.__ev = [];
var src = document.getElementById('src'), zone = document.getElementById('zone');
src.addEventListener('dragstart', function (e) {{
  {dragstart}
  window.__ev.push('start');
}});
zone.addEventListener('dragenter', function (e) {{
  window.__ev.push('enter:' + e.dataTransfer.dropEffect);
}});
zone.addEventListener('dragover', function (e) {{
  e.preventDefault();
  {dragover}
  window.__ev.push('over:' + e.dataTransfer.dropEffect);
}});
zone.addEventListener('drop', function (e) {{
  window.__ev.push('drop:' + e.dataTransfer.dropEffect);
}});
zone.addEventListener('dragleave', function (e) {{
  window.__ev.push('leave:' + e.dataTransfer.dropEffect);
}});
src.addEventListener('dragend', function (e) {{
  window.__ev.push('end:' + e.dataTransfer.dropEffect);
}});
</script></body></html>"""

DS_MOVE = "e.dataTransfer.effectAllowed = 'move';"
DS_COPY = "e.dataTransfer.effectAllowed = 'copy';"
DS_NONE = "e.dataTransfer.effectAllowed = 'none';"
DS_LINK = "e.dataTransfer.effectAllowed = 'link';"
DS_NOTHING = ""
OV_NOTHING = ""
OV_SET_MOVE = "e.dataTransfer.dropEffect = 'move';"
OV_SET_COPY = "e.dataTransfer.dropEffect = 'copy';"
OV_SET_LINK = "e.dataTransfer.dropEffect = 'link';"


def _client(page: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )
    )


async def _drag(page: str, dy: int = 190) -> str:
    session = Session(_client(page), "s_dnd_neg")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("__dragSelector('#src', 0, %d, 3, null, false)" % dy)
    return ctx.eval("window.__ev.join(',')")


# ---------------------------------------------------------------------------
# 1. Default initialization per effectAllowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dragenter_sees_default_for_effect_allowed():
    # effectAllowed="move" → the default dropEffect is "move" (a "move"
    # cursor), not "none".
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_MOVE, dragover=OV_NOTHING))
    assert "enter:move" in ev, ev
    assert "over:move" in ev, ev
    assert "drop:move" in ev, ev
    assert "end:move" in ev, ev


@pytest.mark.asyncio
async def test_default_without_effect_allowed_is_copy():
    # No effectAllowed declared ("uninitialized") → default "copy".
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_NOTHING, dragover=OV_NOTHING))
    assert "enter:copy" in ev, ev
    assert "drop:copy" in ev, ev


@pytest.mark.asyncio
async def test_link_default_for_link_effects():
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_LINK, dragover=OV_NOTHING))
    assert "enter:link" in ev, ev
    assert "drop:link" in ev, ev


# ---------------------------------------------------------------------------
# 2. Listener override + compatibility validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listener_override_within_allowed_set():
    # effectAllowed="copy" and the zone asks for "copy" → the operation is
    # copy at drop and dragend.
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_COPY, dragover=OV_SET_COPY))
    assert "over:copy" in ev, ev
    assert "drop:copy" in ev, ev
    assert "end:copy" in ev, ev


@pytest.mark.asyncio
async def test_incompatible_override_is_ignored():
    # effectAllowed="copy" but the zone sets dropEffect="move": the set is
    # IGNORED (outside the allowed set), the negotiated operation stays the
    # default "copy", and drop/dragend report "copy".
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_COPY, dragover=OV_SET_MOVE))
    assert "over:copy" in ev, f"an incompatible set must not change dropEffect: {ev}"
    assert "drop:copy" in ev, ev
    assert "end:copy" in ev, ev


@pytest.mark.asyncio
async def test_effect_allowed_none_blocks_drop_despite_acceptance():
    # The source allows nothing; the target preventDefaults dragover anyway.
    # The negotiated operation is "none" → NO drop, dragleave fires, and
    # dragend reports "none". (A real browser shows a "no" cursor and never
    # delivers drop.)
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_NONE, dragover=OV_NOTHING))
    assert "enter:none" in ev, ev
    assert "over:none" in ev, ev
    assert "drop:" not in ev, f"no drop may be delivered when the operation is none: {ev}"
    assert "leave:" in ev, ev
    assert "end:none" in ev, ev


# ---------------------------------------------------------------------------
# 3. Attribute validation (spec setters)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_values_are_ignored():
    ev = await _drag(
        PAGE_TMPL.format(
            dragstart="e.dataTransfer.effectAllowed = 'banana';",
            dragover="e.dataTransfer.dropEffect = 'sideways';",
        )
    )
    # Garbage never changes the attributes: effectAllowed stays
    # "uninitialized" (default "copy"), dropEffect stays the initialized
    # default — the handler's 'sideways' set was ignored.
    assert "enter:copy" in ev, ev
    assert "over:copy" in ev, ev
    assert "drop:copy" in ev, ev


@pytest.mark.asyncio
async def test_effect_allowed_settable_only_in_dragstart():
    ev = await _drag(
        PAGE_TMPL.format(
            dragstart=DS_COPY,
            dragover="e.dataTransfer.effectAllowed = 'all';",  # must be ignored
        )
    )
    # The zone tried to widen the allowed effects mid-drag: ignored, so the
    # incompatibility gate still applies (dropEffect stays "copy").
    assert "over:copy" in ev, ev
    assert "drop:copy" in ev, ev


@pytest.mark.asyncio
async def test_drop_effect_set_during_dragstart_is_ignored():
    # The source cannot choose the operation: sets during dragstart are
    # ignored (Blink parity). The negotiated default still applies.
    ev = await _drag(
        PAGE_TMPL.format(
            dragstart="e.dataTransfer.effectAllowed = 'link'; e.dataTransfer.dropEffect = 'move';",
            dragover=OV_NOTHING,
        )
    )
    assert "enter:link" in ev, f"the dragstart dropEffect set must be ignored: {ev}"
    assert "drop:link" in ev, ev



def _loaded_ctx() -> Context:
    """DataTransfer is defined in the document-load block: a loaded DOM is
    required for the engine-level accessor tests."""
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    ctx.eval(
        'var d = host_parse_html("<html><body>x</body></html>");'
        '__load(JSON.parse(d), "https://dnd.test/");'
    )
    return ctx


def test_case_insensitive_canonicalization():
    ctx = _loaded_ctx()
    res = ctx.eval(
        """
        (function () {
          var d = new DataTransfer();
          d.effectAllowed = 'COPYMOVE';
          d.dropEffect = 'MoVe';
          return JSON.stringify([d.effectAllowed, d.dropEffect]);
        })()
        """
    )
    assert res == '["copyMove","move"]', res


def test_standalone_datatransfer_accessors():
    ctx = _loaded_ctx()
    res = ctx.eval(
        """
        (function () {
          var d = new DataTransfer();
          d.dropEffect = 'link';
          d.effectAllowed = 'linkMove';
          return JSON.stringify([d.dropEffect, d.effectAllowed]);
        })()
        """
    )
    assert res == '["link","linkMove"]', res
    # Invalid values leave the attributes unchanged:
    res2 = ctx.eval(
        """
        (function () {
          var d = new DataTransfer();
          d.dropEffect = 'garbage';
          d.effectAllowed = 'garbage';
          return JSON.stringify([d.dropEffect, d.effectAllowed]);
        })()
        """
    )
    assert res2 == '["none","uninitialized"]', res2


# ---------------------------------------------------------------------------
# 4. Only draggable=true initiates native DnD (phantom sequence removed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_draggable_source_never_initiates_native_dnd():
    page = """<html><body>
    <div id="fake" style="width:100px;height:40px">fake</div>
    <script>
    window.__ev = [];
    var fake = document.getElementById('fake');
    fake.addEventListener('dragstart', function () { window.__ev.push('dragstart'); });
    fake.addEventListener('drag', function () { window.__ev.push('drag'); });
    fake.addEventListener('dragend', function () { window.__ev.push('dragend'); });
    fake.addEventListener('mousedown', function () { window.__ev.push('mousedown'); });
    fake.addEventListener('mouseup', function () { window.__ev.push('mouseup'); });
    </script></body></html>"""
    session = Session(_client(page), "s_dnd_phantom")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("__dragSelector('#fake', 0, 50, 3, null, false)")
    ev = ctx.eval("window.__ev.join(',')").split(",")
    # A real browser fires ONLY pointer/mouse events for a non-draggable
    # element: no dragstart, no drag, no dragend — the old heuristic produced
    # a phantom native sequence.
    assert ev == ["mousedown", "mouseup"], ev


@pytest.mark.asyncio
async def test_draggable_true_keeps_full_sequence():
    # Control: the draggable=true lane still runs the complete native
    # sequence with the negotiated operation.
    ev = await _drag(PAGE_TMPL.format(dragstart=DS_MOVE, dragover=OV_SET_MOVE))
    assert ev.startswith("start"), ev
    assert "enter:move" in ev and "over:move" in ev and "drop:move" in ev, ev
    assert ev.endswith("end:move"), ev


@pytest.mark.asyncio
async def test_dragstart_prevent_default_aborts_native_drag():
    page = """<html><body>
    <div id="src" draggable="true" style="width:100px;height:40px">s</div>
    <div style="height:50px"></div>
    <div id="zone" style="width:200px;height:100px">z</div>
    <script>
    window.__ev = [];
    var src = document.getElementById('src'), zone = document.getElementById('zone');
    src.addEventListener('dragstart', function (e) { e.preventDefault(); window.__ev.push('start'); });
    zone.addEventListener('dragover', function (e) { e.preventDefault(); window.__ev.push('dragover'); });
    zone.addEventListener('drop', function () { window.__ev.push('drop'); });
    src.addEventListener('dragend', function () { window.__ev.push('dragend'); });
    </script></body></html>"""
    session = Session(_client(page), "s_dnd_abort")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("__dragSelector('#src', 0, 190, 3, null, false)")
    ev = ctx.eval("window.__ev.join(',')")
    assert ev == "start", f"a canceled dragstart must abort the whole sequence: {ev}"
