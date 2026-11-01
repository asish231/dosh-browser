"""HTMLDialogElement and form[method=dialog] regressions:
- showModal() throws InvalidStateError when the dialog is already open
- show() on an already-open dialog is a silent no-op (does not throw)
- form[method=dialog] closes the nearest dialog ancestor with the submitter's value
- button[formmethod=dialog] overrides a POST form, closing the dialog without network traffic
- requestSubmit(submitter) threads the submitter into dialog submission
- Escape key fires a cancelable 'cancel' event and closes the dialog when not prevented
- Escape key with preventDefault() keeps the dialog open
- Closed <dialog> contents are hidden to checkVisibility() and excluded from action_surface
- session.submit() on a dialog form updates the page without making an HTTP request
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str], requests_log: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests_log is not None:
            requests_log.append(request)
        path = request.url.path
        if path in pages:
            return httpx.Response(200, text=pages[path], headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_showmodal_throws_invalid_state_when_already_open():
    """showModal() on an already-open dialog throws InvalidStateError; show() is a no-op."""
    page = """<html><body>
<dialog id="d1" open><p>Modal 1</p></dialog>
<dialog id="d2"><p>Modal 2</p></dialog>
<script>
window.__results = {};
var d1 = document.getElementById('d1');
var d2 = document.getElementById('d2');

// 1. showModal on open dialog throws InvalidStateError
try {
  d1.showModal();
  window.__results.openThrows = false;
} catch (e) {
  window.__results.openThrows = true;
  window.__results.errorName = e.name;
}

// 2. show() on open dialog does NOT throw
try {
  d1.show();
  window.__results.showOnOpenNoThrow = true;
} catch (e) {
  window.__results.showOnOpenNoThrow = false;
}

// 3. showModal on closed dialog succeeds and opens it
d2.showModal();
window.__results.d2Open = d2.open;
</script></body></html>"""

    session = Session(_client({"/": page}), "s_dlg_showmodal")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__results.openThrows") is True
    assert ctx.eval("window.__results.errorName") == "InvalidStateError"
    assert ctx.eval("window.__results.showOnOpenNoThrow") is True
    assert ctx.eval("window.__results.d2Open") is True


@pytest.mark.asyncio
async def test_form_method_dialog_closes_dialog_and_sets_return_value():
    """Submitting form[method=dialog] closes the dialog, sets returnValue, and avoids network traffic."""
    page = """<html><body>
<dialog id="dlg" open>
  <form id="f" method="dialog">
    <p>Confirm action</p>
    <button id="cancel-btn" type="submit" value="cancel">Cancel</button>
    <button id="confirm-btn" type="submit" value="confirmed">Confirm</button>
  </form>
</dialog>
<script>
window.__ev = [];
var dlg = document.getElementById('dlg');
dlg.addEventListener('close', function () {
  window.__ev.push('closed:' + dlg.returnValue);
});
</script></body></html>"""

    session = Session(_client({"/": page}), "s_dlg_method")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx

    # Click the confirm button via native click
    assert ctx.eval("document.getElementById('confirm-btn').click()") is True
    assert ctx.eval("document.getElementById('dlg').open") is False
    assert ctx.eval("document.getElementById('dlg').returnValue") == "confirmed"
    assert ctx.eval("window.__ev.join(',')") == "closed:confirmed"
    assert ctx.eval("typeof window.__WR_FORM_SUBMIT") == "undefined"


@pytest.mark.asyncio
async def test_button_formmethod_dialog_overrides_post_form():
    """button[formmethod=dialog] in a POST form closes the dialog without triggering HTTP POST."""
    reqs = []
    page = """<html><body>
<dialog id="dlg" open>
  <form id="post-form" method="post" action="/submit-post">
    <button id="dismiss-btn" type="submit" formmethod="dialog" value="dismissed">Dismiss</button>
    <button id="save-btn" type="submit" value="saved">Save</button>
  </form>
</dialog>
<script>
window.__ev = [];
var dlg = document.getElementById('dlg');
dlg.addEventListener('close', function () {
  window.__ev.push('closed:' + dlg.returnValue);
});
</script></body></html>"""

    session = Session(_client({"/": page}, reqs), "s_dlg_formmethod")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx

    assert ctx.eval("document.getElementById('dismiss-btn').click()") is True
    assert ctx.eval("document.getElementById('dlg').open") is False
    assert ctx.eval("document.getElementById('dlg').returnValue") == "dismissed"
    assert ctx.eval("window.__ev.join(',')") == "closed:dismissed"
    assert ctx.eval("typeof window.__WR_FORM_SUBMIT") == "undefined"
    # Only initial page GET was issued; no POST /submit-post
    assert len(reqs) == 1
    assert reqs[0].method == "GET"


@pytest.mark.asyncio
async def test_request_submit_threads_submitter_to_dialog():
    """form.requestSubmit(submitter) honors method=dialog and submitter value."""
    page = """<html><body>
<dialog id="dlg" open>
  <form id="f" method="dialog">
    <button id="btn1" type="submit" value="one">One</button>
    <button id="btn2" type="submit" value="two">Two</button>
  </form>
</dialog>
<script>
var dlg = document.getElementById('dlg');
var f = document.getElementById('f');
var btn2 = document.getElementById('btn2');
f.requestSubmit(btn2);
window.__val = dlg.returnValue;
window.__isOpen = dlg.open;
</script></body></html>"""

    session = Session(_client({"/": page}), "s_dlg_reqsubmit")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__isOpen") is False
    assert ctx.eval("window.__val") == "two"


@pytest.mark.asyncio
async def test_escape_key_fires_cancel_and_closes_dialog():
    """Pressing Escape dispatches cancel event on open dialog and closes it."""
    page = """<html><body>
<dialog id="dlg" open>
  <p>Inside dialog</p>
  <button id="focused-btn">Click me</button>
</dialog>
<script>
window.__ev = [];
var dlg = document.getElementById('dlg');
dlg.addEventListener('cancel', function (e) {
  window.__ev.push('cancel:cancelable=' + e.cancelable);
});
dlg.addEventListener('close', function () {
  window.__ev.push('close');
});
</script></body></html>"""

    session = Session(_client({"/": page}), "s_dlg_escape")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx

    # Press escape targeting the button inside the open dialog
    assert ctx.eval("__pressKey('#focused-btn', 'escape')") is True
    assert ctx.eval("window.__ev.join(',')") == "cancel:cancelable=true,close"
    assert ctx.eval("document.getElementById('dlg').open") is False


@pytest.mark.asyncio
async def test_escape_key_cancel_prevent_default_keeps_dialog_open():
    """If cancel event calls preventDefault(), dialog remains open on Escape."""
    page = """<html><body>
<dialog id="dlg" open>
  <button id="trapped">Stay open</button>
</dialog>
<script>
window.__ev = [];
var dlg = document.getElementById('dlg');
dlg.addEventListener('cancel', function (e) {
  e.preventDefault();
  window.__ev.push('prevented');
});
dlg.addEventListener('close', function () {
  window.__ev.push('close');
});
</script></body></html>"""

    session = Session(_client({"/": page}), "s_dlg_escape_prevent")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx

    assert ctx.eval("__pressKey('#trapped', 'escape')") is True
    assert ctx.eval("window.__ev.join(',')") == "prevented"
    assert ctx.eval("document.getElementById('dlg').open") is True


@pytest.mark.asyncio
async def test_closed_dialog_hidden_from_check_visibility_and_surface():
    """Closed <dialog> is display:none via UA rule; open makes it visible; closing hides it again."""
    page = """<html><body>
<button id="outside-btn">Outside</button>
<dialog id="dlg">
  <button id="inside-btn">Inside</button>
</dialog>
<script>window.__ready = true;</script>
</body></html>"""

    session = Session(_client({"/": page}), "s_dlg_vis")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx

    # 1. Closed: checkVisibility is false
    assert ctx.eval("document.getElementById('dlg').checkVisibility()") is False
    assert ctx.eval("document.getElementById('inside-btn').checkVisibility()") is False
    assert ctx.eval("document.getElementById('outside-btn').checkVisibility()") is True

    # Action surface only shows outside-btn
    surf1 = session.action_surface()
    labels1 = [a.label for a in surf1.actions]
    assert "Outside" in labels1
    assert "Inside" not in labels1

    # 2. Open dialog: checkVisibility becomes true
    ctx.eval("document.getElementById('dlg').showModal()")
    assert ctx.eval("document.getElementById('inside-btn').checkVisibility()") is True


@pytest.mark.asyncio
async def test_session_submit_on_dialog_form_closes_dialog_without_network():
    """Session.submit() on a dialog form updates the page with dialog closed and zero HTTP requests."""
    reqs = []
    page = """<html><body>
<dialog id="dlg" open>
  <form id="dlg-form" method="dialog">
    <input type="text" name="user" value="alice">
    <button id="done" type="submit" value="ok">Done</button>
  </form>
</dialog>
</body></html>"""

    session = Session(_client({"/": page}, reqs), "s_session_dlg_submit")
    p1 = await session.goto(f"{BASE}/", max_chars=20_000)
    assert len(p1.forms) == 1
    assert p1.forms[0].method == "DIALOG"

    # Submit form 1
    p2 = await session.submit(1, {"user": "bob"})
    # Dialog is now closed: open attribute removed
    assert "<dialog id=\"dlg\">" in session.html or "<dialog id=\"dlg\"" in session.html
    assert 'open' not in session.html.split("<dialog")[1].split(">")[0]
    # No HTTP submit was made
    assert len(reqs) == 1
    assert reqs[0].method == "GET"


@pytest.mark.asyncio
async def test_session_act_on_dialog_button_closes_dialog():
    """session.act() clicking a submit button in form[method=dialog] closes the dialog."""
    reqs = []
    page = """<html><body>
<dialog id="dlg" open>
  <form id="dlg-form" method="dialog">
    <button id="close-modal" type="submit" value="dismiss">Close Modal</button>
  </form>
</dialog>
<script>window.__ready = true;</script>
</body></html>"""

    session = Session(_client({"/": page}, reqs), "s_session_act_dlg")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    close_action = next(a for a in surf.actions if a.label == "Close Modal")

    # Act on the Close Modal button
    await session.act(close_action.ref)

    # Dialog is closed and button disappears from the action surface
    surf2 = session.action_surface()
    labels2 = [a.label for a in surf2.actions]
    assert "Close Modal" not in labels2
    assert len(reqs) == 1  # No HTTP POST/GET submit


@pytest.mark.asyncio
async def test_scriptless_closed_dialog_hidden_from_action_surface():
    """Scriptless page with closed <dialog>: contents are hidden by UA rule in actions.py fallback."""
    page = """<html><body>
<button id="outside-btn">Outside Only</button>
<dialog id="dlg">
  <button id="inside-btn">Hidden Inside</button>
</dialog>
</body></html>"""

    session = Session(_client({"/": page}), "s_dlg_scriptless")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx is None

    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert "Outside Only" in labels
    assert "Hidden Inside" not in labels

