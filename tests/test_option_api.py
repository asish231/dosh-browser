"""Option-element API regressions: option.text/.label/.index exist per spec,
value="" options report "" (not their text), multiple selects have no
implicit first-option selection, and out-of-range selectedIndex deselects.

Real form-driving JS reads these constantly (populating selects, finding a
choice by label); before this fix all of them lied or threw undefined.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

PAGE = """<html><body>
<select id="s1">
  <option value="a">Alpha</option>
  <option value="">Empty</option>
  <option>Bare</option>
</select>
<select id="m" multiple><option value="x">X</option><option value="y">Y</option></select>
<select id="m2" multiple><option value="x" selected>X</option><option value="y" selected>Y</option></select>
<script>window.__ready = 1;</script>
</body></html>"""


def _client(page: str = PAGE) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )
    )


async def _ctx():
    session = Session(_client(), "s_option")
    await session.goto(f"{BASE}/", max_chars=20_000)
    return session, session.current_tab.ctx


# ---------------------------------------------------------------------------
# option.text / .label / .index


@pytest.mark.asyncio
async def test_option_text_is_text_content():
    _, ctx = await _ctx()
    assert ctx.eval("document.querySelector('#s1 option').text") == "Alpha"
    # A value-less option's text is still its text (not its value)
    assert ctx.eval("document.querySelectorAll('#s1 option')[2].text") == "Bare"


@pytest.mark.asyncio
async def test_option_label_attribute_wins_else_text():
    _, ctx = await _ctx()
    assert ctx.eval(
        "(function(){var el = document.createElement('option');"
        "el.setAttribute('label', 'Lab'); el.textContent = 'Txt'; return el.label;})()"
    ) == "Lab"
    # No label attribute: label is the (trimmed) text
    assert ctx.eval("document.querySelector('#s1 option').label") == "Alpha"


@pytest.mark.asyncio
async def test_option_index_is_position_in_select():
    _, ctx = await _ctx()
    assert ctx.eval("document.querySelectorAll('#s1 option')[2].index") == 2
    assert ctx.eval("document.querySelector('#s1 option').index") == 0
    # A detached option (no select) reports 0 per spec
    assert ctx.eval("(function(){var o = document.createElement('option'); return o.index;})()") == 0


# ---------------------------------------------------------------------------
# option.value: the value attribute governs — even when empty


@pytest.mark.asyncio
async def test_empty_value_attribute_reports_empty():
    _, ctx = await _ctx()
    assert ctx.eval("document.querySelectorAll('#s1 option')[1].value") == "", (
        "value=\"\" must report '' — the old code fell through to the text"
    )


@pytest.mark.asyncio
async def test_bare_option_value_is_its_text():
    _, ctx = await _ctx()
    assert ctx.eval("document.querySelectorAll('#s1 option')[2].value") == "Bare"


@pytest.mark.asyncio
async def test_option_value_setter_with_no_value_attr_writes_text():
    _, ctx = await _ctx()
    ctx.eval("document.querySelectorAll('#s1 option')[2].value = 'Written'; true")
    assert ctx.eval("document.querySelectorAll('#s1 option')[2].text") == "Written"


# ---------------------------------------------------------------------------
# Multiple selects: no implicit first-option selection


@pytest.mark.asyncio
async def test_multiple_select_no_selection_value_empty():
    _, ctx = await _ctx()
    assert ctx.eval("document.getElementById('m').value") == ""
    assert ctx.eval("document.getElementById('m').selectedIndex") == -1
    assert ctx.eval("document.getElementById('m').options[0].selected") is False, (
        "the implicit first-option selection must not apply to multiple selects"
    )


@pytest.mark.asyncio
async def test_multiple_select_first_selected_wins():
    _, ctx = await _ctx()
    assert ctx.eval("document.getElementById('m2').value") == "x"
    assert ctx.eval("document.getElementById('m2').selectedIndex") == 0


# ---------------------------------------------------------------------------
# Out-of-range selectedIndex deselects


@pytest.mark.asyncio
async def test_out_of_range_selected_index_deselects():
    _, ctx = await _ctx()
    ctx.eval("document.getElementById('s1').selectedIndex = 99; true")
    assert ctx.eval("document.getElementById('s1').selectedIndex") == -1
    assert ctx.eval("document.getElementById('s1').value") == ""
    # Setting a real index back works
    ctx.eval("document.getElementById('s1').selectedIndex = 0; true")
    assert ctx.eval("document.getElementById('s1').value") == "a"


# ---------------------------------------------------------------------------
# End to end: the wire still carries the right payload through all this


@pytest.mark.asyncio
async def test_select_payload_still_correct_after_api_fix():
    page = """<html><body>
<form action="/r" method="post">
<select id="s1" name="s1">
  <option value="a">Alpha</option>
  <option value="">Empty</option>
  <option>Bare</option>
</select>
</form><script>window.__ready = 1;</script></body></html>"""
    bodies: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/r":
            bodies.append(req.read().decode())
            return httpx.Response(200, text="<html><body>ok</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_option_wire")
    await session.goto(f"{BASE}/", max_chars=20_000)
    # JS picks the empty-value option
    ctx = session.current_tab.ctx
    ctx.eval("document.getElementById('s1').value = ''; true")
    await session.submit(1, max_chars=20_000)
    assert bodies, "submit never fired"
    assert "s1=&" in bodies[-1] or bodies[-1].endswith("s1="), bodies[-1]
    assert "s1=Bare" not in bodies[-1], "the bare option's text must not leak in"
