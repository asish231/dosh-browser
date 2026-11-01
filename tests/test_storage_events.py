"""Cross-tab `storage` event regressions (round 7).

The gap: localStorage mutations in one tab were never broadcast — sibling
same-origin tabs had no `storage` event and no live value sync. Per spec the
event fires in every OTHER Document sharing the storage area, with
key/oldValue/newValue/url/storageArea; the originating document never
receives it; sessionStorage is per-tab and never broadcasts.

Every assertion reads specific engine/session state, never page substrings.
"""

from __future__ import annotations

import httpx
import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.session import Session

BASE = "https://stor.test"
OTHER = "https://other.test"

A_PAGE = """<html><body>
<button id="set1" onclick="localStorage.setItem('k','v1')">set1</button>
<button id="set2" onclick="localStorage.setItem('k','v2')">set2</button>
<button id="rm" onclick="localStorage.removeItem('k')">rm</button>
<button id="clr" onclick="localStorage.clear()">clr</button>
<button id="ss" onclick="sessionStorage.setItem('sk','sv')">ss</button>
<button id="multi" onclick="localStorage.setItem('a','1');localStorage.setItem('b','2')">multi</button>
<script>
window.__own = [];
window.addEventListener('storage', function (e) { window.__own.push(e.key); });
</script>
</body></html>"""

LISTENER_PAGE = """<html><body>
<script>
window.__ev = [];
window.addEventListener('storage', function (e) {
  window.__ev.push(JSON.stringify({
    key: e.key, oldValue: e.oldValue, newValue: e.newValue,
    url: e.url, area: e.storageArea === localStorage,
    trusted: e.isTrusted === true,
    isSE: e instanceof StorageEvent
  }));
});
</script>
</body></html>"""


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        body = pages.get(req.url.path, "<html><body>404</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _two_tabs(session: Session, b_url: str = f"{BASE}/b") -> str:
    """Open tab A on the writer page and tab B on the listener page.

    Returns tab A's id (the session's active tab is B; tests switch back).
    """
    await session.goto(f"{BASE}/a")
    a_id = session.active_tab_id
    session.new_tab("B")
    await session.goto(b_url)
    return a_id


def _act(session: Session, label: str) -> None:
    ref = next(a.ref for a in session.action_surface().actions if a.label == label)
    asyncio_run_act(session, ref)


def asyncio_run_act(session: Session, ref: str) -> None:
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(session.act(ref))


# ---------------------------------------------------------------------------
# Broadcast contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_broadcasts_full_payload_to_other_tab():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev1")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "set1")
    await session.act(ref)
    session.switch_tab("B")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ev.length") == 1
    ev = ctx.eval("window.__ev[0]")
    assert '"key":"k"' in ev
    assert '"oldValue":null' in ev
    assert '"newValue":"v1"' in ev
    assert f'"url":"{BASE}/a"' in ev
    assert '"area":true' in ev
    assert '"trusted":true' in ev, "a cross-tab storage event is browser-initiated"
    assert '"isSE":true' in ev
    # The other tab's localStorage copy is synced (shared storage area):
    assert ctx.eval("localStorage.getItem('k')") == "v1"


@pytest.mark.asyncio
async def test_second_change_carries_old_value():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev2")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    for label in ("set1", "set2"):
        ref = next(a.ref for a in session.action_surface().actions if a.label == label)
        await session.act(ref)
    session.switch_tab("B")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ev.length") == 2
    assert '"oldValue":"v1"' in ctx.eval("window.__ev[1]")
    assert '"newValue":"v2"' in ctx.eval("window.__ev[1]")
    assert ctx.eval("localStorage.getItem('k')") == "v2"


@pytest.mark.asyncio
async def test_remove_and_clear_broadcast_null_payloads():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev3")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    for label in ("set1", "rm", "clr"):
        ref = next(a.ref for a in session.action_surface().actions if a.label == label)
        await session.act(ref)
    session.switch_tab("B")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ev.length") == 3
    remove_ev = ctx.eval("window.__ev[1]")
    assert '"newValue":null' in remove_ev and '"oldValue":"v1"' in remove_ev
    clear_ev = ctx.eval("window.__ev[2]")
    # clear(): one event, key/oldValue/newValue all null (browser behavior)
    assert '"key":null' in clear_ev and '"newValue":null' in clear_ev
    assert ctx.eval("localStorage.getItem('k')") is None
    assert ctx.eval("localStorage.length") == 0


@pytest.mark.asyncio
async def test_multi_key_pass_broadcasts_in_order():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev4")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "multi")
    await session.act(ref)
    session.switch_tab("B")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ev.length") == 2
    assert '"key":"a"' in ctx.eval("window.__ev[0]")
    assert '"key":"b"' in ctx.eval("window.__ev[1]")
    assert ctx.eval("localStorage.getItem('a')") == "1"
    assert ctx.eval("localStorage.getItem('b')") == "2"


# ---------------------------------------------------------------------------
# Isolation contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_originating_tab_receives_no_event():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev5")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "set1")
    await session.act(ref)
    ctx = session.current_tab.ctx
    # The spec: the event fires in OTHER documents sharing the area — never
    # in the one that made the change (tab A registered its own listener).
    assert ctx.eval("window.__own.length") == 0
    assert ctx.eval("localStorage.getItem('k')") == "v1", "the writer sees its own write"


@pytest.mark.asyncio
async def test_session_storage_does_not_broadcast():
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev6")
    a_id = await _two_tabs(session)
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "ss")
    await session.act(ref)
    session.switch_tab("B")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ev.length") == 0, "sessionStorage is per-tab: no broadcast"
    assert ctx.eval("sessionStorage.getItem('sk')") is None
    # And the writer's own tab keeps its per-tab value:
    session.switch_tab(a_id)
    assert session.current_tab.ctx.eval("sessionStorage.getItem('sk')") == "sv"


@pytest.mark.asyncio
async def test_cross_origin_tab_receives_no_event_or_value():
    session = Session(
        _client({"/a": A_PAGE, "/b": LISTENER_PAGE, "/x": LISTENER_PAGE}), "s_sev7"
    )
    a_id = await _two_tabs(session, b_url=f"{OTHER}/x")
    cross_ctx = session.current_tab.ctx
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "set1")
    await session.act(ref)
    assert cross_ctx.eval("window.__ev.length") == 0, "different origin: no broadcast"
    assert cross_ctx.eval("localStorage.getItem('k')") is None


@pytest.mark.asyncio
async def test_receiving_tab_does_not_echo_the_change():
    # The receiving store applies the change QUIETLY: a third tab must not
    # see an event when tab B's area was updated by tab A's change.
    session = Session(_client({"/a": A_PAGE, "/b": LISTENER_PAGE}), "s_sev8")
    a_id = await _two_tabs(session)
    session.new_tab("C")
    await session.goto(f"{BASE}/b")  # same listener page, third tab
    c_ctx = session.current_tab.ctx
    session.switch_tab(a_id)
    ref = next(a.ref for a in session.action_surface().actions if a.label == "set1")
    await session.act(ref)
    assert c_ctx.eval("window.__ev.length") == 1, "exactly one event from A — no echo from B"
    assert '"newValue":"v1"' in c_ctx.eval("window.__ev[0]")
    # The receiving ctx recorded nothing: its own drain broadcasts nothing
    # back (verified at the engine boundary).
    session.switch_tab("B")
    assert session.current_tab.ctx.eval("__takeStorageChanges()") == '{"local":[],"session":[]}'


# ---------------------------------------------------------------------------
# Engine-level contract
# ---------------------------------------------------------------------------


def _loaded_ctx() -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    ctx.eval(
        'var d = host_parse_html("<html><body>x</body></html>");'
        '__load(JSON.parse(d), "https://stor.test/a");'
    )
    return ctx


def test_storage_event_class_contract():
    ctx = _loaded_ctx()
    assert ctx.eval("typeof StorageEvent") == "function"
    ctx.eval(
        """
        window.__probe = null;
        window.addEventListener('storage', function (e) { window.__probe = e; });
        __dispatchStorageEvent("k", "old", "new", "https://stor.test/a");
        true;
        """
    )
    assert ctx.eval("window.__probe instanceof StorageEvent") is True
    assert ctx.eval("window.__probe instanceof Event") is True
    assert ctx.eval("window.__probe.key") == "k"
    assert ctx.eval("window.__probe.oldValue") == "old"
    assert ctx.eval("window.__probe.newValue") == "new"
    assert ctx.eval("window.__probe.url") == "https://stor.test/a"
    assert ctx.eval("window.__probe.storageArea === localStorage") is True
    assert ctx.eval("window.__probe.isTrusted === true") is True


def test_apply_remote_change_is_quiet_and_precise():
    ctx = _loaded_ctx()
    ctx.eval('localStorage.setItem("seed", "0"); true')  # recorded (writer)
    assert '"key":"seed"' in ctx.eval("__takeStorageChanges()")
    # Quiet apply: value visible, nothing recorded (no echo).
    ctx.eval('__applyRemoteStorageChange("k", "v", false); true')
    assert ctx.eval("localStorage.getItem('k')") == "v"
    ctx.eval('__applyRemoteStorageChange("k", null, false); true')
    assert ctx.eval("localStorage.getItem('k')") is None
    ctx.eval('__applyRemoteStorageChange("seed", "9", false); true')
    assert ctx.eval("localStorage.getItem('seed')") == "9"
    assert ctx.eval("__takeStorageChanges()") == '{"local":[],"session":[]}'
    ctx.eval('__applyRemoteStorageChange(null, null, true); true')
    assert ctx.eval("localStorage.length") == 0
    assert ctx.eval("__takeStorageChanges()") == '{"local":[],"session":[]}'


def test_change_records_carry_old_and_new_values():
    ctx = _loaded_ctx()
    ctx.eval('localStorage.setItem("a", "1"); localStorage.setItem("a", "2"); localStorage.removeItem("a"); true')
    recs = ctx.eval("__takeStorageChanges()")
    assert '"oldValue":null,"newValue":"1"' in recs
    assert '"oldValue":"1","newValue":"2"' in recs
    assert '"oldValue":"2","newValue":null' in recs
    # Drained once: the next take is empty (records do not accumulate).
    assert ctx.eval("__takeStorageChanges()") == '{"local":[],"session":[]}'
