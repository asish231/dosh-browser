"""P1 regressions: previously-missing Web APIs, all through the real
session/engine paths (only the HTTP transport is a mock).

Before this batch every one of these threw TypeError on real pages:
scrollIntoView, HTMLDialogElement.showModal/close, history back/forward/go
with popstate and session-level pushState entries, DragEvent/DataTransfer
native drag & drop, navigator.sendBeacon, Element.checkVisibility,
document.caretRangeFromPoint, Notification, BroadcastChannel,
requestFullscreen, the popover API, window.print, navigator.geolocation,
navigator.serviceWorker, and the WheelEvent/InputEvent/SubmitEvent classes.
Parsed and created elements also now carry their real HTMLElement subclass
(dialog instanceof HTMLDialogElement).
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str] | None = None) -> httpx.AsyncClient:
    pages = pages or {}

    def handler(req: httpx.Request):
        body = pages.get(req.url.path, pages.get("*", "<html><body>404</body></html>"))
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Dialog


@pytest.mark.asyncio
async def test_dialog_show_close_and_events():
    page = """<html><body>
<dialog id="d"><p>body</p></dialog>
<script>
window.__ev = [];
var d = document.getElementById('d');
d.addEventListener('close', function(){ window.__ev.push('closed:' + d.returnValue); });
d.showModal();
window.__open = d.open;
d.close('bye');
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_dialog")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__open") is True
    assert ctx.eval("d_open_after_close") if False else ctx.eval(
        "document.getElementById('d').open"
    ) is False
    assert ctx.eval("window.__ev.join(',')") == "closed:bye"


@pytest.mark.asyncio
async def test_dialog_instanceof_for_created_and_parsed():
    page = """<html><body><dialog id="d"></dialog>
<script>window.__r = [
  document.createElement('dialog') instanceof HTMLDialogElement,
  document.getElementById('d') instanceof HTMLDialogElement
].join(',');</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_dlginst")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__r") == "true,true"


# ---------------------------------------------------------------------------
# scrollIntoView family


@pytest.mark.asyncio
async def test_scroll_into_view_scrolls_the_window():
    page = """<html><body>
<div style="height:2500px">spacer</div>
<div id="far">far</div>
<script>
var el = document.getElementById('far');
window.__pre = scrollY;
el.scrollIntoView();
window.__post = Math.round(scrollY);
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_siv")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__pre") == 0
    assert ctx.eval("window.__post") > 1700, "scrollIntoView must scroll to the element"


@pytest.mark.asyncio
async def test_scroll_into_view_if_needed_noop_when_visible():
    page = """<html><body>
<div id="top">top</div>
<script>
window.__pre = scrollY;
document.getElementById('top').scrollIntoViewIfNeeded();
window.__post = scrollY;
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_sivn")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__pre") == ctx.eval("window.__post") == 0


# ---------------------------------------------------------------------------
# History: pushState entries + engine-driven back + popstate


HISTORY_PAGE = """<html><body>
<div id="v">home</div>
<button id="push" onclick="history.pushState({page:2}, '', '/page2'); document.getElementById('v').textContent='pushed';">Push</button>
<button id="back" onclick="history.back();">Back</button>
<script>
window.__ps = [];
window.addEventListener('popstate', function(e){
  window.__ps.push('s:' + JSON.stringify(e.state) + '|path:' + location.pathname + '|v:' + document.getElementById('v').textContent);
});
window.__ready = 1;
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_pushstate_creates_a_real_history_entry():
    session = Session(_client({"*": HISTORY_PAGE}), "s_p1_push")
    await session.goto(f"{BASE}/home", max_chars=20_000)
    assert len(session.history) == 1
    push = next(a.ref for a in session.action_surface().actions if a.label == "Push")
    await session.act(push, max_chars=20_000)
    assert session.url == f"{BASE}/page2"
    assert len(session.history) == 2, "pushState must add an entry, not navigate"
    assert "pushed" in session.html, "the live DOM is kept (SPA semantics: no reload)"


@pytest.mark.asyncio
async def test_engine_back_restores_and_fires_popstate():
    session = Session(_client({"*": HISTORY_PAGE}), "s_p1_back")
    await session.goto(f"{BASE}/home", max_chars=20_000)
    push = next(a.ref for a in session.action_surface().actions if a.label == "Push")
    await session.act(push, max_chars=20_000)
    back = next(a.ref for a in session.action_surface().actions if a.label == "Back")
    await session.act(back, max_chars=20_000)

    assert session.url == f"{BASE}/home", "history.back() must land on the previous entry"
    pop = session.current_tab.ctx.eval("window.__ps.join(' ;; ')")
    assert pop, "popstate must fire after an engine-driven back()"
    assert "path:/home" in pop, pop
    assert "v:home" in pop, pop
    line = [l for l in session.html.splitlines() if 'id="v"' in l]
    assert "home" in (line[0] if line else ""), "the restored DOM is the previous entry's"


# ---------------------------------------------------------------------------
# Native drag & drop


@pytest.mark.asyncio
async def test_native_drag_and_drop_with_datatransfer():
    page = """<html><body>
<div id="src" draggable="true" style="width:100px;height:40px">drag me</div>
<div id="tgt" style="width:300px;height:120px">drop zone</div>
<script>
window.__dd = [];
var src = document.getElementById('src'), tgt = document.getElementById('tgt');
src.addEventListener('dragstart', function(e){ e.dataTransfer.setData('text/plain', 'payload-1'); window.__dd.push('dragstart'); });
tgt.addEventListener('dragover', function(e){ e.preventDefault(); window.__dd.push('dragover'); });
tgt.addEventListener('drop', function(e){ window.__dd.push('drop:' + e.dataTransfer.getData('text/plain')); });
src.addEventListener('dragend', function(){ window.__dd.push('dragend'); });
window.__ready = 1;
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_dnd")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("__dragSelector('#src', 0, 100, 3, null, false)")
    assert ctx.eval("window.__dd.join(',')") == "dragstart,dragover,drop:payload-1,dragend"


@pytest.mark.asyncio
async def test_drag_event_classes_exist():
    page = """<html><body><script>
window.__r = [
  typeof DragEvent, typeof DataTransfer,
  new DragEvent('dragstart', {dataTransfer: new DataTransfer()}) instanceof DragEvent
].join(',');</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_dragev")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__r") == "function,function,true"


# ---------------------------------------------------------------------------
# Small API batch


@pytest.mark.asyncio
async def test_send_beacon_posts_to_the_wire():
    requests: list[tuple[str, str]] = []

    def handler(req: httpx.Request):
        requests.append((req.method, req.url.path))
        if req.url.path == "/beacon":
            return httpx.Response(204)
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    page = """<html><body><script>window.__ok = navigator.sendBeacon('/beacon', 'ping');</script></body></html>"""
    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_p1_beacon")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__ok") is True
    assert ("POST", "/beacon") in requests

@pytest.mark.asyncio
async def test_check_visibility_sees_stylesheet_hiding():
    page = """<html><head><style>.hide{display:none}</style></head><body>
<div class="hide" id="h">x</div><div id="v">y</div>
<script>window.__r = [document.getElementById('h').checkVisibility(),
 document.getElementById('v').checkVisibility()].join(',');</script>
</body></html>"""
    session = Session(_client({"/": page}), "s_p1_vis")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__r") == "false,true"


@pytest.mark.asyncio
async def test_misc_api_surface():
    page = """<html><body><script>
window.__r = [
  typeof Notification, Notification.permission,
  typeof BroadcastChannel,
  typeof Element.prototype.requestFullscreen,
  typeof Element.prototype.showPopover,
  typeof window.print,
  typeof navigator.geolocation,
  typeof navigator.serviceWorker,
  typeof document.caretRangeFromPoint,
  typeof WheelEvent, typeof InputEvent, typeof SubmitEvent
].join(',');</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_misc")
    await session.goto(f"{BASE}/", max_chars=20_000)
    got = session.current_tab.ctx.eval("window.__r").split(",")
    assert got[0] == "function" and got[1] == "denied", got  # Notification + permission
    assert got[2] == "function", got   # BroadcastChannel
    assert got[3] == "function", got   # requestFullscreen
    assert got[4] == "function", got   # showPopover
    assert got[5] == "function", got   # window.print
    assert got[6] == "object", got     # navigator.geolocation (object in real browsers too)
    assert got[7] == "object", got     # navigator.serviceWorker (likewise)
    assert got[8] == "function", got   # caretRangeFromPoint
    assert got[9] == "function" and got[10] == "function" and got[11] == "function", got


@pytest.mark.asyncio
async def test_broadcast_channel_delivers_in_context():
    page = """<html><body><script>
window.__got = [];
var a = new BroadcastChannel('ch'), b = new BroadcastChannel('ch');
b.onmessage = function(e){ window.__got.push(e.data); };
a.postMessage('hello');
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_bc")
    await session.goto(f"{BASE}/", max_chars=20_000)
    got = session.current_tab.ctx.eval("window.__got.join(',')")
    # Message delivery rides a microtask; pump via a trivial action pass if empty.
    if not got:
        await session.scroll(y=1, max_chars=20_000)
        got = session.current_tab.ctx.eval("window.__got.join(',')")
    assert got == "hello"


@pytest.mark.asyncio
async def test_geolocation_denies_honestly():
    page = """<html><body><script>
window.__code = -1;
navigator.geolocation.getCurrentPosition(
  function(){ window.__code = 0; },
  function(e){ window.__code = e.code; });
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_geo")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__code") == 1, "PERMISSION_DENIED, never invented coordinates"


@pytest.mark.asyncio
async def test_service_worker_register_resolves():
    page = """<html><body><script>
window.__sw = '';
navigator.serviceWorker.register('/sw.js').then(function(r){ window.__sw = 'ok:' + r.scope; })
  .catch(function(e){ window.__sw = 'err:' + e.message; });
</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_sw")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    got = ctx.eval("window.__sw")
    if not got:
        await session.scroll(y=1, max_chars=20_000)
        got = ctx.eval("window.__sw")
    assert got == "ok:/"


@pytest.mark.asyncio
async def test_event_class_instanceof():
    page = """<html><body><script>
window.__r = [
  new WheelEvent('wheel', {deltaY: 120}) instanceof WheelEvent,
  new InputEvent('input', {data: 'x'}) instanceof InputEvent,
  new SubmitEvent('submit') instanceof SubmitEvent
].join(',');</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_evcls")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__r") == "true,true,true"


@pytest.mark.asyncio
async def test_caret_range_from_point_hit_tests_boxes():
    page = """<html><body>
<div id="a" style="height:100px">alpha</div>
<script>window.__t = (function(){
  var r = document.caretRangeFromPoint(20, 40);
  return r && r.startContainer ? r.startContainer.id || r.startContainer.nodeName : 'null';
})();</script></body></html>"""
    session = Session(_client({"/": page}), "s_p1_caret")
    await session.goto(f"{BASE}/", max_chars=20_000)
    got = session.current_tab.ctx.eval("window.__t")
    assert got in ("a", "DIV", "div", "BODY", "body"), got
