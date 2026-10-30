"""Regression: fieldset-disabled descendants are not actuated by engine lanes.

The wire/act() lanes already exclude fieldset-disabled controls; the engine
click/type/pressKey/focus lanes did not. Fixed by __controlDisabled().
"""
import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

PAGE = """<html><body>
<fieldset disabled id="fs">
  <legend><button id="legbtn" onclick="window.__leg=true">Legend button</button></legend>
  <button id="fsbtn" onclick="window.__fs=true">FS button</button>
  <input id="fsfield" value="">
</fieldset>
<button id="alive" onclick="window.__alive=true">Alive</button>
<script>window.__fs=false; window.__leg=false; window.__alive=false;</script>
</body></html>"""


def _session():
    def handler(req):
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})
    return Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_fs")


async def _live():
    session = _session()
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    return session, ctx


@pytest.mark.asyncio
async def test_fieldset_disabled_button_click_refused():
    _, ctx = await _live()
    assert ctx.eval("__activateSelector('#fsbtn', null, false, false)") is False
    assert ctx.eval("window.__fs") is False
    assert ctx.eval("document.getElementById('fsbtn').click()") is False


@pytest.mark.asyncio
async def test_fieldset_disabled_typing_refused():
    _, ctx = await _live()
    assert ctx.eval("__activateSelector('#fsfield','hello',false,false)") is False
    assert ctx.eval("document.getElementById('fsfield').value") == ""


@pytest.mark.asyncio
async def test_fieldset_disabled_press_key_refused():
    _, ctx = await _live()
    assert ctx.eval("__pressKey('#fsfield','a')") is False


@pytest.mark.asyncio
async def test_legend_contents_are_not_disabled():
    _, ctx = await _live()
    # first legend's button should be actuated (exempt)
    assert ctx.eval("__activateSelector('#legbtn', null, false, false)") is True
    assert ctx.eval("window.__leg") is True


@pytest.mark.asyncio
async def test_enabled_control_still_works():
    _, ctx = await _live()
    assert ctx.eval("__activateSelector('#alive', null, false, false)") is True
    assert ctx.eval("window.__alive") is True