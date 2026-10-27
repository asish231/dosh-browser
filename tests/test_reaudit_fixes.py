"""Re-audit regressions (2026-10-27 adversarial verification round).

Every fix here was confirmed broken or incomplete by three adversarial
subagent audits that tried to disprove the original fix batch, then
re-verified live after the repair. Covers: the dead injected-script lane,
the selector-engine gap that defeated the hidden-control filter, wire
entry-list fidelity (repeated pairs, disabled exclusion, submitter pair,
GET query replacement), SPA history (multi-push, cross-origin guard,
relative-URL resolution, forward-entry preservation), sessionStorage origin
scoping, WebSocket close delivery, BroadcastChannel close, caret hit-testing,
captcha evidence classes, session_id schema completeness, and the content_root
quadratic blowup.
"""

import socket
import base64
import hashlib
import struct
import threading
import time

import httpx
import pytest

from selectolax.lexbor import LexborHTMLParser

from webresearch.content import content_root
from webresearch.engine.inspect import inspect_html
from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str] | None = None) -> httpx.AsyncClient:
    pages = pages or {}

    def handler(req: httpx.Request):
        ctype = "text/javascript" if req.url.path.endswith(".js") else "text/html"
        body = pages.get(req.url.path, pages.get("*", "<html><body>404</body></html>"))
        return httpx.Response(200, text=body, headers={"content-type": ctype})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Injected-script execution lane (was entirely dead: the detector compared
# nodeName === "SCRIPT" against lowercase 'script')


@pytest.mark.asyncio
async def test_injected_script_src_executes():
    pages = {
        "*": """<html><body><div id="v">x</div>
<script>
  var s = document.createElement('script');
  s.setAttribute('src', '/inj.js');
  document.body.appendChild(s);
</script></body></html>""",
        "/inj.js": "window.__injRan = 1;",
    }
    session = Session(_client(pages), "s_ra_inj")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("!!window.__injRan") is True, "appended src script must execute"


@pytest.mark.asyncio
async def test_injected_script_via_fragment_executes():
    pages = {
        "*": """<html><body>
<script>
  var f = document.createDocumentFragment();
  var s = document.createElement('script');
  s.textContent = 'window.__fragRan = 1;';
  f.appendChild(s);
  document.body.appendChild(f);
</script></body></html>""",
    }
    session = Session(_client(pages), "s_ra_frag")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("!!window.__fragRan") is True, "fragment-appended script must execute"


@pytest.mark.asyncio
async def test_text_nodes_have_data_and_nodevalue():
    pages = {"*": "<html><body><script>window.__t = (function(){var t = document.createTextNode('hello'); return [t.data, t.nodeValue, t.textContent].join('|');})();</script></body></html>"}
    session = Session(_client(pages), "s_ra_text")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__t") == "hello|hello|hello"


# ---------------------------------------------------------------------------
# Selector engine: child combinator and multi-class compounds (the gap made
# the hidden-control filter miss every id-less nested control)


@pytest.mark.asyncio
async def test_child_combinator_and_multiclass_selectors():
    page = """<html><body>
<div class="menu"><button class="s2 save" id="t">Q</button></div>
<script>window.__r = [
  !!document.querySelector('div.menu > button.s2.save'),
  !!document.querySelector('button.s2.save'),
  !!document.querySelector('div.menu > button'),
  !!document.querySelector('div.menu button')
].join(',');</script></body></html>"""
    session = Session(_client({"*": page}), "s_ra_sel")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx.eval("window.__r") == "true,true,true,true"


@pytest.mark.asyncio
async def test_hidden_filter_catches_idless_combinator_controls():
    """The exact case the re-audit broke: stylesheet-hidden, id-less, nested
    in classed wrappers — its _css_path selector must now resolve."""
    page = """<html><head><style>.hide{display:none}</style></head><body>
<div class="menu"><button class="hide s2 save">SheetHiddenNoId</button></div>
<div class="hidwrap" style="display:none"><button class="act">AncestorHiddenNoId</button></div>
<button id="v1">VisibleOne</button>
<script>window.__ready=1;</script>
</body></html>"""
    session = Session(_client({"*": page}), "s_ra_hidden")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert labels == ["VisibleOne"], labels
    assert surf.hidden_controls == 2, f"both hidden controls counted, got {surf.hidden_controls}"


# ---------------------------------------------------------------------------
# Wire entry-list fidelity


WIRE_PAGE = """<html><body>
<form action="/search?lang=en" method="get">
<input type="checkbox" name="top" value="bacon" checked>
<input type="checkbox" name="top" value="cheese" checked>
<input name="off" value="secret" disabled>
<input name="q" value="x">
<select name="tags" multiple>
  <option value="a" selected>A</option><option value="b" selected>B</option>
</select>
<button type="submit" name="btn" value="go">Go</button>
</form>
<fieldset disabled><input name="fs_child" value="fsval"></fieldset>
<script>window.__ready=1;</script></body></html>"""


@pytest.mark.asyncio
async def test_get_submit_replaces_action_query_and_repeats_pairs():
    requests: list[str] = []

    def handler(req: httpx.Request):
        requests.append(str(req.url))
        return httpx.Response(200, text="<html><body>ok</body></html>", headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_ra_get")
    await session.goto(f"{BASE}/", max_chars=20_000)
    session.current_tab.ctx  # page loaded
    # Re-goto with the wire page via the shared handler
    session2 = Session(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text=WIRE_PAGE, headers={"content-type": "text/html"})
                if req.url.path != "/search"
                else handler(req)
            )
        ),
        "s_ra_get2",
    )
    await session2.goto(f"{BASE}/", max_chars=20_000)
    await session2.submit(1, max_chars=20_000)
    assert requests, "GET submit never fired"
    url = requests[-1]
    assert "top=bacon" in url and "top=cheese" in url, url
    assert "tags=a" in url and "tags=b" in url and "tags=a%2Cb" not in url, url
    assert "lang=en" not in url, "form data must REPLACE the action query per spec"
    assert "off=secret" not in url, "disabled input must not submit"
    assert "fs_child" not in url, "fieldset-disabled input must not submit"
    assert "btn=go" not in url, "form.submit() has no submitter; the pair must not appear"


@pytest.mark.asyncio
async def test_clicking_submit_button_includes_its_pair():
    page = """<html><body>
<form action="/r" method="post">
<input name="q" value="x">
<button type="submit" name="btn" value="go">Go</button>
</form><script>window.__ready=1;</script></body></html>"""
    bodies: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/r":
            bodies.append(req.read().decode())
            return httpx.Response(200, text="<html><body>ok</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_ra_submitter")
    await session.goto(f"{BASE}/", max_chars=20_000)
    go = next(a.ref for a in session.action_surface().actions if a.label == "Go")
    await session.act(go, max_chars=20_000)
    assert bodies, "clicking the submit button must POST"
    assert "q=x" in bodies[-1] and "btn=go" in bodies[-1], bodies[-1]


@pytest.mark.asyncio
async def test_post_repeats_same_name_pairs():
    page = """<html><body>
<form action="/r" method="post">
<input type="checkbox" name="top" value="bacon" checked>
<input type="checkbox" name="top" value="cheese" checked>
</form><script>window.__ready=1;</script></body></html>"""
    bodies: list[str] = []

    def handler(req: httpx.Request):
        if req.url.path == "/r":
            bodies.append(req.read().decode())
            return httpx.Response(200, text="<html><body>ok</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_ra_pairs")
    await session.goto(f"{BASE}/", max_chars=20_000)
    await session.submit(1, max_chars=20_000)
    assert bodies
    assert "top=bacon" in bodies[-1] and "top=cheese" in bodies[-1], bodies[-1]


# ---------------------------------------------------------------------------
# SPA history: multi-push, cross-origin guard, relative resolution, forward


HISTORY_PAGE = """<html><body><script>
try { history.pushState({a:1}, '', 'https://evil.example/x'); window.__cross = 'NO-THROW'; }
catch (e) { window.__cross = e.message.slice(0, 24); }
history.pushState({p:1}, '', 'sub/page');
history.pushState({p:2}, '', '/two');
</script></body></html>"""


@pytest.mark.asyncio
async def test_two_pushes_create_two_entries_and_back_lands_between():
    session = Session(_client({"*": HISTORY_PAGE}), "s_ra_push")
    await session.goto(f"{BASE}/deep/dir/page", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert str(ctx.eval("window.__cross")).startswith("SecurityError"), ctx.eval("window.__cross")
    assert len(session.history) == 3, "load + two pushes = three entries"
    assert session.url == f"{BASE}/two"
    session.back()
    assert session.url == f"{BASE}/deep/dir/sub/page", "back must land on the FIRST push (relative single-resolved)"


@pytest.mark.asyncio
async def test_forward_survives_action_pass():
    pages = {
        "/a": "<html><body><h1>A</h1><script>window.__ready=1;</script></body></html>",
        "/b": "<html><body><h1>B</h1><script>window.__ready=1;</script></body></html>",
    }
    session = Session(_client(pages), "s_ra_fwd")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.goto(f"{BASE}/b", max_chars=20_000)
    session.back()
    await session.scroll(y=1, max_chars=20_000)  # an action pass between
    session.forward()
    assert "B" in session.html, "forward() must work after back() + an action pass"


# ---------------------------------------------------------------------------
# sessionStorage origin scoping


@pytest.mark.asyncio
async def test_session_storage_is_origin_scoped():
    pages = {
        "a.test": "<html><body><script>sessionStorage.setItem('sv', 'from-a');</script></body></html>",
        "b.test": (
            "<html><body><div id=\"v\">unset</div><script>"
            "document.getElementById('v').textContent = 'sv=' + sessionStorage.getItem('sv');"
            "</script></body></html>"
        ),
    }

    def handler(req: httpx.Request):
        return httpx.Response(200, text=pages.get(req.url.host, "<html><body>x</body></html>"),
                              headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_ra_ss")
    await session.goto("https://a.test/", max_chars=20_000)
    await session.goto("https://b.test/", max_chars=20_000)
    line = [l for l in session.html.splitlines() if 'id="v"' in l]
    assert "sv=null" in (line[0] if line else ""), "cross-origin must start empty"

    await session.goto("https://a.test/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("sessionStorage.getItem('sv')") == "from-a", "same-origin session storage must persist"


# ---------------------------------------------------------------------------
# WebSocket: server-initiated close reaches JS


class _CloseServer:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self):
        self.ready = threading.Event()
        self.port = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self.ready.wait(5)

    def _run(self):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(10)
        self.port = srv.getsockname()[1]
        self.ready.set()
        try:
            conn, _ = srv.accept()
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            key = ""
            for line in data.split(b"\r\n"):
                if line.lower().startswith(b"sec-websocket-key:"):
                    key = line.split(b":", 1)[1].strip().decode()
            accept = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode()
            )
            # Give the page a moment to wire its handlers, then close.
            time.sleep(0.3)
            payload = struct.pack("!H", 1000) + b"bye"
            conn.sendall(bytes([0x88, len(payload)]) + payload)
            with __import__("contextlib").suppress(Exception):
                conn.recv(64)
            conn.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_server_close_fires_onclose_in_js():
    server = _CloseServer()
    page = f"""<html><body><div id="v">connecting</div>
<script>
  var ws = new WebSocket("ws://127.0.0.1:{server.port}/");
  ws.onopen = function () {{ document.getElementById('v').textContent = 'open'; }};
  ws.onclose = function (e) {{
    document.getElementById('v').textContent = 'closed:' + e.code + ':rs' + ws.readyState;
  }};
</script>
<script>/* second script: _wants_js gates single-script pages */</script>
</body></html>"""
    session = Session(_client({"*": page}), "s_ra_wsclose")
    await session.goto(f"{BASE}/", max_chars=20_000)
    for _ in range(10):
        await session.scroll(y=1, max_chars=20_000)
        line = [l for l in session.html.splitlines() if 'id="v"' in l]
        if line and "closed:1000" in line[0]:
            break
        time.sleep(0.15)
    line = [l for l in session.html.splitlines() if 'id="v"' in l]
    assert line and "closed:1000" in line[0], (
        f"onclose must fire with the close code (div says: {line[:1]})"
    )
    assert ":rs3" in line[0], "readyState must be CLOSED (3) after onclose"


# ---------------------------------------------------------------------------
# BroadcastChannel close()


@pytest.mark.asyncio
async def test_broadcast_channel_close_stops_delivery():
    page = """<html><body><script>
  var a = new BroadcastChannel('ch'); var b = new BroadcastChannel('ch');
  window.__got = [];
  b.onmessage = function (e) { window.__got.push(e.data); };
  a.postMessage('m1');
  a.close();
  a.postMessage('m2');
</script></body></html>"""
    session = Session(_client({"*": page}), "s_ra_bc")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    got = ctx.eval("(window.__got||[]).join(',')")
    if not got:
        await session.scroll(y=1, max_chars=20_000)
        got = ctx.eval("(window.__got||[]).join(',')")
    assert got == "m1", f"closed channel must not deliver: {got!r}"


# ---------------------------------------------------------------------------
# caretRangeFromPoint: innermost box, null outside


@pytest.mark.asyncio
async def test_caret_range_innermost_and_null_outside():
    page = """<html><body>
<div style="width:200px;height:200px"><div id="inner" style="width:50px;height:50px">i</div></div>
<script>window.__c = (function(){var r = document.caretRangeFromPoint(20, 40); return r ? (r.startContainer.id || r.startContainer.nodeName) : 'null';})();
window.__out = document.caretRangeFromPoint(99999, 99999) === null ? 'null' : 'range';</script>
</body></html>"""
    session = Session(_client({"*": page}), "s_ra_caret")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__c") == "inner"
    assert ctx.eval("window.__out") == "null"


# ---------------------------------------------------------------------------
# Captcha evidence: comments, data attributes, and prose are not challenges


@pytest.mark.asyncio
async def test_captcha_false_positive_classes_quiet():
    """HTML comments, data attributes, and prose mentioning providers are not
    challenge evidence; a Wikipedia-style config blob stays quiet too."""
    page = """<html><body>
<!-- recaptcha discussion -->
<p>This article explains how datadome and grecaptcha work.</p>
<div data-note="recaptcha">x</div>
<script>mw.config.set({"wgConfirmEditHCaptchaSiteKey":"abc"});</script>
</body></html>"""
    targets, challenges = inspect_html(page, f"{BASE}/article")
    assert challenges == [], (
        f"comments/prose/data-attrs are not challenge evidence: {[c.kind for c in challenges]}"
    )


def test_real_widget_still_fires_after_evidence_tightening():
    page = """<html><body>
<div class="h-captcha" data-sitekey="xxx"></div>
<script src="https://js.hcaptcha.com/1/api.js"></script>
</body></html>"""
    targets, challenges = inspect_html(page, f"{BASE}/hc")
    assert "hcaptcha" in [c.kind for c in challenges]


# ---------------------------------------------------------------------------
# session_id declared on EVERY tool


def test_session_id_on_all_tools():
    from webresearch.mcp import TOOLS

    assert TOOLS, "TOOLS list must not be empty"
    missing = [t["name"] for t in TOOLS if "session_id" not in (t.get("inputSchema") or {}).get("properties", {})]
    assert not missing, f"tools without session_id: {missing}"
    for tool in TOOLS:
        req = (tool.get("inputSchema") or {}).get("required") or []
        assert "session_id" not in req, tool["name"]


# ---------------------------------------------------------------------------
# Resource timeline persists across action passes


@pytest.mark.asyncio
async def test_resource_entries_persist_across_action_pass():
    pages = {
        "*": '<html><body><div id="v">x</div><script src="/lib.js"></script></body></html>',
        "/lib.js": "window.__lib = 1;",
    }
    session = Session(_client(pages), "s_ra_res")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    n0 = ctx.eval("performance.getEntriesByType('resource').length")
    assert n0 >= 1
    await session.scroll(y=100, max_chars=20_000)
    n1 = session.current_tab.ctx.eval("performance.getEntriesByType('resource').length")
    assert n1 >= n0, "an action pass must not wipe the page's resource timeline"


# ---------------------------------------------------------------------------
# content_root performance: the old pair-scan was quadratic


def test_content_root_list_performance():
    rows = "\n".join(
        f'<article class="Box-row"><h2>repo {i}</h2><p>desc {i}</p></article>'
        for i in range(300)
    )
    page = f"<html><body><main>{rows}</main></body></html>"
    start = time.time()
    root = content_root(LexborHTMLParser(page))
    elapsed = time.time() - start
    assert root is not None and root.tag == "main"
    assert elapsed < 5.0, f"content_root took {elapsed:.1f}s on 300 rows (regression: quadratic scan)"


# ---------------------------------------------------------------------------
# insertBefore with a string throws (spec) instead of corrupting the tree


@pytest.mark.asyncio
async def test_insert_before_string_throws():
    page = """<html><body><div id="d"><span>a</span></div>
<script>window.__threw = '';
try { document.getElementById('d').insertBefore('raw', document.getElementById('d').firstChild); }
catch (e) { window.__threw = e.name + ':' + e.message.slice(0, 20); }</script></body></html>"""
    session = Session(_client({"*": page}), "s_ra_insbefore")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    got = ctx.eval("window.__threw")
    assert got.startswith("TypeError"), f"insertBefore(string) must throw TypeError, got {got!r}"
