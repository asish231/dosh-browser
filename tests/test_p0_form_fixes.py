"""P0-2/P0-3/P0-6 regressions: select semantics, radio exclusivity, and
payload correctness (checked state, live DOM values, multi-select pairs).

Before these fixes: select.value concatenated option text ("AlphaBeta"),
typing appended to it, radio clicks were complete no-ops, unchecked
checkboxes were submitted, the raw HTTP payload re-parsed stale HTML, and
multi-select selections collapsed to one value. All probes run through the
real session/engine paths; only the HTTP transport is a mock.
"""

import httpx
import pytest

from webresearch.forms import forms
from webresearch.parse import parse
from webresearch.session import Session
from webresearch.types import FetchResult

BASE = "https://test.local"

FORM_PAGE = """<html><body>
<form action="/result" method="post">
  <input type="radio" name="tier" id="r1" value="free" checked>
  <input type="radio" name="tier" id="r2" value="paid">
  <input type="radio" name="tier" id="r3" value="team">
  <input type="checkbox" name="ok" id="cb" checked>
  <select id="sel" name="plan">
    <option value="a">Alpha</option><option value="b">Beta</option>
  </select>
  <select id="multi" name="tags" multiple>
    <option value="x" selected>X</option>
    <option value="y">Y</option>
    <option value="z" selected>Z</option>
  </select>
  <input name="note" id="note" value="">
  <input type="hidden" name="csrf" value="tok-123">
</form><script>window.__ready=1;</script></body></html>"""


def _session() -> tuple[Session, list[str]]:
    bodies: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/result":
            bodies.append(req.read().decode() if req.method == "POST" else "")
            return httpx.Response(
                200, text="<html><body><h1>RESULT</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, text=FORM_PAGE, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_forms")
    return session, bodies


async def _live():
    session, bodies = _session()
    await session.goto(f"{BASE}/form", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    return session, bodies, ctx


# ---------------------------------------------------------------------------
# Select element semantics


@pytest.mark.asyncio
async def test_select_value_is_selected_option_not_concatenated_text():
    _, _, ctx = await _live()
    assert ctx.eval("document.getElementById('sel').value") == "a"


@pytest.mark.asyncio
async def test_select_options_and_indices():
    _, _, ctx = await _live()
    assert ctx.eval("document.getElementById('sel').options.length") == 2
    assert ctx.eval("document.getElementById('sel').selectedIndex") == 0
    assert ctx.eval("document.getElementById('sel').selectedOptions.length") == 1


@pytest.mark.asyncio
async def test_select_value_setter_is_exclusive():
    _, _, ctx = await _live()
    got = ctx.eval(
        "(function(){var s=document.getElementById('sel'); s.value='b';"
        "return [s.value, s.selectedIndex, s.options[0].selected, s.options[1].selected].join('|');})()"
    )
    assert got == "b|1|false|true"


@pytest.mark.asyncio
async def test_typing_into_select_matches_by_value_then_text():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#sel','b',false,false)")
    assert ctx.eval("document.getElementById('sel').value") == "b"
    assert ctx.eval("__activateSelector('#sel','Alpha',false,false)")
    assert ctx.eval("document.getElementById('sel').value") == "a"


@pytest.mark.asyncio
async def test_typing_no_match_leaves_select_unchanged():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#sel','nope',false,false)") is False
    assert ctx.eval("document.getElementById('sel').value") == "a"


@pytest.mark.asyncio
async def test_option_click_selects_in_parent_select():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#sel option[value=b]', null, false, false)")
    assert ctx.eval("document.getElementById('sel').value") == "b"


@pytest.mark.asyncio
async def test_multi_select_selected_options_all_reported():
    _, _, ctx = await _live()
    got = ctx.eval(
        "(function(){var o=document.getElementById('multi').selectedOptions;"
        "var v=[]; for(var i=0;i<o.length;i++) v.push(o[i].value); return v.join(',');})()"
    )
    assert got == "x,z"


# ---------------------------------------------------------------------------
# Radio group exclusivity


@pytest.mark.asyncio
async def test_radio_click_checks_clicked_and_unchecks_siblings():
    _, _, ctx = await _live()
    assert ctx.eval("__activateSelector('#r2', null, false, false)")
    got = ctx.eval(
        "['r1','r2','r3'].map(function(id){return document.getElementById(id).checked;}).join(',')"
    )
    assert got == "false,true,false"


@pytest.mark.asyncio
async def test_radio_click_fires_input_and_change():
    _, _, ctx = await _live()
    got = ctx.eval(
        "(function(){window.__ev=[];"
        "var r3=document.getElementById('r3');"
        "r3.addEventListener('input',function(){window.__ev.push('input')});"
        "r3.addEventListener('change',function(){window.__ev.push('change')});"
        "__activateSelector('#r3', null, false, false);"
        "return window.__ev.join(',');})()"
    )
    assert got == "input,change"


@pytest.mark.asyncio
async def test_checkbox_click_toggles_and_fires_input_and_change():
    _, _, ctx = await _live()
    got = ctx.eval(
        "(function(){window.__ev=[];"
        "var cb=document.getElementById('cb');"
        "cb.addEventListener('input',function(){window.__ev.push('input')});"
        "cb.addEventListener('change',function(){window.__ev.push('change')});"
        "var before=cb.checked;"
        "__activateSelector('#cb', null, false, false);"
        "return [before, cb.checked, window.__ev.join(',')].join('|');})()"
    )
    assert got == "true|false|input,change"


# ---------------------------------------------------------------------------
# Wire payload correctness (live DOM state, checked state, defaults)


@pytest.mark.asyncio
async def test_submit_reflects_live_dom_state_on_the_wire():
    session, bodies, ctx = await _live()
    ctx.eval("__activateSelector('#r2', null, false, false)")      # radio -> paid
    ctx.eval("__activateSelector('#sel','Beta',false,false)")     # select -> b
    ctx.eval("__activateSelector('#cb', null, false, false)")     # uncheck ok
    ctx.eval("__activateSelector('#note','typed',false,false)")    # type note
    await session.submit(1, max_chars=20_000)
    wire = bodies[-1]
    assert "tier=paid" in wire and "tier=free" not in wire, wire
    assert "plan=b" in wire, wire
    assert "ok=" not in wire, "unchecked checkbox must not be submitted"
    assert "note=typed" in wire, "engine-typed value must reach the wire"
    assert "csrf=tok-123" in wire, "hidden token must ride along"


@pytest.mark.asyncio
async def test_submit_caller_fields_override_engine_state():
    session, bodies, ctx = await _live()
    ctx.eval("__activateSelector('#note','typed',false,false)")
    await session.submit(1, {"note": "override"}, max_chars=20_000)
    assert "note=override" in bodies[-1] and "note=typed" not in bodies[-1]


@pytest.mark.asyncio
async def test_multi_select_submits_one_pair_per_selection():
    session, bodies, _ = await _live()
    await session.submit(1, max_chars=20_000)
    wire = bodies[-1]
    assert "tags=x" in wire and "tags=z" in wire, wire


@pytest.mark.asyncio
async def test_checkbox_and_radio_default_value_is_on():
    session, bodies = await _session_pages_default()
    wire = bodies[-1]
    assert "ok=on" in wire, wire
    assert "off=" not in wire, wire
    assert "tier=paid" in wire and "tier=free" not in wire, wire


async def _session_pages_default():
    page = """<html><body>
<form action="/result" method="post">
  <input type="checkbox" name="ok" checked>
  <input type="checkbox" name="off">
  <input type="radio" name="tier" value="free">
  <input type="radio" name="tier" value="paid" checked>
</form><script>window.__ready=1;</script></body></html>"""
    bodies: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/result":
            bodies.append(req.read().decode() if req.method == "POST" else "")
            return httpx.Response(200, text="<html><body><h1>RESULT</h1></body></html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_defaults")
    await session.goto(f"{BASE}/form", max_chars=20_000)
    await session.submit(1, max_chars=20_000)
    return session, bodies


# ---------------------------------------------------------------------------
# Static extraction (forms.py): checked-state aware field list


def _extract(html: str):
    doc = parse(FetchResult(
        url=f"{BASE}/form", final_url=f"{BASE}/form", status=200,
        content_type="text/html", kind="html", body=html,
    ))
    return forms(doc)


def test_forms_radio_group_is_one_field_with_options():
    out = _extract(FORM_PAGE)
    tiers = [f for f in out[0].fields if f.name == "tier"]
    assert len(tiers) == 1, "a radio group is exclusive: one field, not one per radio"
    assert tiers[0].value == "free"  # the checked member
    assert tiers[0].options == ["free", "paid", "team"]


def test_forms_unchecked_checkbox_omitted():
    out = _extract(FORM_PAGE)
    assert not [f for f in out[0].fields if f.name == "off"]
    ok = [f for f in out[0].fields if f.name == "ok"]
    assert len(ok) == 1 and ok[0].value == "on"


def test_forms_select_defaults_to_first_option_and_selected_wins():
    out = _extract(FORM_PAGE)
    plan = [f for f in out[0].fields if f.name == "plan"]
    assert len(plan) == 1 and plan[0].value == "a"  # no selected attr -> first option
    marked = _extract(FORM_PAGE.replace(
        '<option value="b">Beta</option>',
        '<option value="b" selected>Beta</option>',
    ))
    plan2 = [f for f in marked[0].fields if f.name == "plan"]
    assert plan2[0].value == "b"


def test_forms_multi_select_yields_one_field_per_selection():
    out = _extract(FORM_PAGE)
    tags = [f for f in out[0].fields if f.name == "tags"]
    assert [f.value for f in tags] == ["x", "z"]


# ---------------------------------------------------------------------------
# Action surface: options are the select's state, not standalone controls


@pytest.mark.asyncio
async def test_options_not_surfaced_as_activate_actions():
    session, _, _ = await _live()
    surf = session.action_surface()
    assert all(a.tag != "option" for a in surf.actions), (
        "option elements must not appear as controls; clicking them used to "
        "silently change nothing"
    )
    assert any(a.role == "select" for a in surf.actions)
