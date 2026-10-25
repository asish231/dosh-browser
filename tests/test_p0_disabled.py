"""P0-4 regressions: disabled controls are never actuated.

Before these fixes: Session.act() checked `disabled` only in the activate
branch, so disabled submit buttons POSTed and disabled links navigated; the
engine click path had no disabled concept (listeners ran on disabled
buttons); and el.disabled was undefined so the Tab-skip guard passed
disabled controls. All probes run through the real session/engine paths;
only the HTTP transport is a mock.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

PAGE = """<html><body>
<form action="/submitted" method="get">
  <button type="submit" id="go" disabled onclick="window.__clicked=true">Go</button>
  <input name="q" id="field" disabled>
  <select id="sel" name="plan" disabled>
    <option value="a">Alpha</option><option value="b">Beta</option>
  </select>
  <input type="checkbox" id="cb" disabled>
</form>
<a href="/other" id="nav" disabled>Nav away</a>
<button id="alive" onclick="window.__alive=true">Alive</button>
<script>window.__clicked=false; window.__alive=false; window.__ready=1;</script>
</body></html>"""


def _session_and_log() -> tuple[Session, list[str]]:
    requests: list[str] = []

    def handler(req: httpx.Request):
        requests.append(f"{req.method} {req.url.path}")
        if req.url.path in ("/submitted", "/other"):
            return httpx.Response(
                200, text="<html><body><h1>SHOULD NOT HAPPEN</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_disabled")
    return session, requests


async def _live():
    session, requests = _session_and_log()
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    requests.clear()
    return session, requests, ctx


# ---------------------------------------------------------------------------
# Engine-level guards


@pytest.mark.asyncio
async def test_disabled_property_reflects_and_sets_attribute():
    _, _, ctx = await _live()
    got = ctx.eval(
        "[document.getElementById('go').disabled,"
        " document.getElementById('alive').disabled].join(',')"
    )
    assert got == "true,false"
    got = ctx.eval(
        "(function(){var b=document.getElementById('alive');"
        "b.disabled = true; var on = b.disabled;"
        "b.disabled = false; return [on, b.disabled, b.hasAttribute('disabled')].join(',');})()"
    )
    assert got == "true,false,false"


@pytest.mark.asyncio
async def test_engine_click_on_disabled_button_is_refused():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#go', null, false, false)") is False
    assert ctx.eval("window.__clicked") is False, "disabled click listener must not run"


@pytest.mark.asyncio
async def test_programmatic_click_on_disabled_is_noop():
    _, _, ctx = await _live()
    assert ctx.eval("document.getElementById('go').click()") is False
    assert ctx.eval("window.__clicked") is False


@pytest.mark.asyncio
async def test_engine_typing_into_disabled_field_is_refused():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#field','hello',false,false)") is False
    assert ctx.eval("document.getElementById('field').value") == ""


@pytest.mark.asyncio
async def test_engine_typing_into_disabled_select_is_refused():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#sel','b',false,false)") is False
    assert ctx.eval("document.getElementById('sel').value") == "a"


@pytest.mark.asyncio
async def test_engine_press_key_on_disabled_is_refused():
    _, _, ctx = await _live()
    assert ctx.eval("__pressKey('#field','enter')") is False


@pytest.mark.asyncio
async def test_enabled_controls_still_work():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#alive', null, false, false)")
    assert ctx.eval("window.__alive") is True


# ---------------------------------------------------------------------------
# Session lanes: every act() kind refuses a disabled control, no HTTP fires


@pytest.mark.asyncio
async def test_act_refuses_disabled_submit_button_without_http():
    session, requests, _ = await _live()
    surf = session.action_surface()
    go = next(a for a in surf.actions if a.label == "Go")
    assert go.disabled
    with pytest.raises(ValueError, match="disabled"):
        await session.act(go.ref, max_chars=20_000)
    assert requests == [], f"disabled submit must not fire HTTP: {requests}"


@pytest.mark.asyncio
async def test_act_refuses_disabled_link_without_http():
    session, requests, _ = await _live()
    surf = session.action_surface()
    nav = next(a for a in surf.actions if a.label == "Nav away")
    assert nav.disabled
    with pytest.raises(ValueError, match="disabled"):
        await session.act(nav.ref, max_chars=20_000)
    assert requests == [], f"disabled link must not navigate: {requests}"


@pytest.mark.asyncio
async def test_act_refuses_disabled_textbox_without_typing():
    session, _, ctx = await _live()
    surf = session.action_surface()
    field = next(a for a in surf.actions if a.label == "field")
    assert field.disabled
    with pytest.raises(ValueError, match="disabled"):
        await session.act(field.ref, text="hello", max_chars=20_000)
    assert ctx.eval("document.getElementById('field').value") == ""


@pytest.mark.asyncio
async def test_disabled_controls_are_surfaced_with_flag():
    session, _requests, _ = await _live()
    surf = session.action_surface()
    labels = {a.label: a.disabled for a in surf.actions}
    assert labels.get("Go") is True
    assert labels.get("Nav away") is True
    assert labels.get("Alive") is False
