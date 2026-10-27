"""Frame reply lane regressions: e.source.postMessage replies (the standard
cross-frame reply form) must reach the parent, alongside parent/top/direct
messages, through real action passes.

The session's message pumps filtered targets to {parent, top, *}: a child's
reply to the parent's postMessage — the OAuth-popup/payment/embed handshake
pattern — was recorded by the engine but dropped by the pump, so the parent
never learned the answer.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://parent.test"

PARENT = """<html><body>
<iframe id="f" src="https://child.test/child"></iframe>
<div id="out">idle</div>
<button id="poke" onclick="document.getElementById('f').contentWindow.postMessage('hello-child', '*'); window.__poked=1;">Poke</button>
<script>
window.__log = [];
window.addEventListener('message', function (e) {
  window.__log.push(e.data + '|' + e.origin);
  document.getElementById('out').textContent = window.__log.join(',');
});
</script>
</body></html>"""

CHILD = """<html><body>
<div id="got">nothing</div>
<script>
  window.addEventListener('message', function (e) {
    document.getElementById('got').textContent = 'GOT:' + e.data;
    e.source.postMessage('reply-to:' + e.data, '*');
    window.parent.postMessage('direct-from-child', '*');
  });
</script>
</body></html>"""


def _client() -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        if req.url.host == "child.test":
            return httpx.Response(200, text=CHILD, headers={"content-type": "text/html"})
        return httpx.Response(200, text=PARENT, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _clicked_session():
    session = Session(_client(), "s_frame_reply")
    await session.goto(f"{BASE}/", max_chars=30_000)
    poke = next(a.ref for a in session.action_surface().actions if a.label == "Poke")
    await session.act(poke, max_chars=30_000)
    return session


@pytest.mark.asyncio
async def test_child_receives_parent_postmessage_via_action_pass():
    session = await _clicked_session()
    child = next(f for f in session.frame_documents if "child" in f.url)
    line = [l for l in child.html.splitlines() if 'id="got"' in l]
    assert line and "GOT:hello-child" in line[0], (
        f"parent->child message never delivered: {line}"
    )


@pytest.mark.asyncio
async def test_reply_reaches_parent_with_child_origin():
    session = await _clicked_session()
    log = session.current_tab.ctx.eval("(window.__log||[]).join(' ;; ')")
    assert "reply-to:hello-child|https://child.test" in log, (
        f"e.source.postMessage reply missing from the parent: {log!r}"
    )


@pytest.mark.asyncio
async def test_direct_parent_message_still_delivered():
    session = await _clicked_session()
    log = session.current_tab.ctx.eval("(window.__log||[]).join(' ;; ')")
    assert "direct-from-child|https://child.test" in log, log


@pytest.mark.asyncio
async def test_parent_dom_reflects_child_messages():
    """The parent's own listener mutates its DOM on delivery; the serialized
    page must show it (the pump result is the page the agent reads)."""
    session = await _clicked_session()
    line = [l for l in session.html.splitlines() if 'id="out"' in l]
    assert line and "reply-to:hello-child" in line[0], (
        f"parent DOM must reflect the reply: {line}"
    )


# ---------------------------------------------------------------------------
# URL-form targetOrigin: the spec compares origins, not strings


PARENT_URLFORM = """<html><body>
<iframe id="f" src="https://child.test/child"></iframe>
<button id="poke" onclick="document.getElementById('f').contentWindow.postMessage('hello', 'https://child.test/child'); window.__poked=1;">Poke</button>
</body></html>"""


@pytest.mark.asyncio
async def test_url_form_target_origin_delivers():
    """postMessage(data, 'https://host/path') — a URL, not a bare origin —
    must deliver when the child's origin matches the URL's origin."""
    def handler(req: httpx.Request):
        if req.url.host == "child.test":
            return httpx.Response(200, text=CHILD, headers={"content-type": "text/html"})
        return httpx.Response(200, text=PARENT_URLFORM, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_urlform")
    await session.goto(f"{BASE}/", max_chars=30_000)
    poke = next(a.ref for a in session.action_surface().actions if a.label == "Poke")
    await session.act(poke, max_chars=30_000)
    child = next(f for f in session.frame_documents if "child" in f.url)
    line = [l for l in child.html.splitlines() if 'id="got"' in l]
    assert line and "GOT:hello" in line[0], f"URL-form targetOrigin dropped: {line}"


@pytest.mark.asyncio
async def test_wrong_origin_still_dropped():
    def handler(req: httpx.Request):
        if req.url.host == "child.test":
            return httpx.Response(200, text=CHILD, headers={"content-type": "text/html"})
        page = PARENT_URLFORM.replace("'https://child.test/child'", "'https://other.test/x'")
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_urlform_bad")
    await session.goto(f"{BASE}/", max_chars=30_000)
    poke = next(a.ref for a in session.action_surface().actions if a.label == "Poke")
    await session.act(poke, max_chars=30_000)
    child = next(f for f in session.frame_documents if "child" in f.url)
    line = [l for l in child.html.splitlines() if 'id="got"' in l]
    assert line and "GOT:" not in line[0], f"wrong-origin message must be dropped: {line}"
