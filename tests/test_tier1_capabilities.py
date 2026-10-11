"""Tier-1 agent capabilities: dialogs, keys, popups, downloads, editors, WebSocket.

Hermetic regressions, one per capability, all through the real session/engine
paths (no mocks of our own code except the HTTP transport).
"""

import base64
import contextlib
import hashlib
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS
from webresearch.session import Session


def _session(client_for):
    return Session(client_for, "s_tier1")


# ---------------------------------------------------------------------------
# 1. Dialogs (alert/confirm/prompt)


DIALOG_PAGE = """<!DOCTYPE html>
<html><body>
<button id="go">Delete</button>
<span id="out">idle</span>
<script>
  document.getElementById("go").addEventListener("click", function () {
    if (confirm("Delete everything?")) {
      document.getElementById("out").textContent = "confirmed:yes";
    } else {
      document.getElementById("out").textContent = "confirmed:no";
    }
  });
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_dialog_recorded_and_answer_staged():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=DIALOG_PAGE, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)

    await session.goto("https://example.com/delete-page")

    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.role == "button")
    page = await session.act(btn.ref)

    # The dialog is visible: in the view and in session.dialogs(), honestly
    # marked as default-answered (no staged answer at the time it fired).
    assert "DIALOGS" in page.view
    assert "Delete everything?" in page.view
    dialogs = session.dialogs()
    assert len(dialogs) == 1
    assert dialogs[0]["type"] == "confirm"
    assert dialogs[0]["answered"] == "default"
    assert "confirmed:no" in session.html

    # Stage the answer, re-run the same action: confirm() now returns true.
    session.respond_dialog(accept=True)
    await session.act(btn.ref)
    assert "confirmed:yes" in session.html
    dialogs = session.dialogs()
    assert dialogs[0]["answered"] == "accepted"


@pytest.mark.asyncio
async def test_prompt_dialog_returns_staged_text():
    page_html = """<!DOCTYPE html>
    <html><body>
    <button id="p">Name</button>
    <span id="out">idle</span>
    <script>
      document.getElementById("p").addEventListener("click", function () {
        var name = prompt("Your name?");
        document.getElementById("out").textContent = "name:" + name;
      });
    </script>
    </body></html>"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=page_html, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/prompt-page")

    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.role == "button")
    await session.act(btn.ref)
    assert "name:null" in session.html  # unanswered prompt defaults to null

    session.respond_dialog(accept=True, text="Agent")
    await session.act(btn.ref)
    assert "name:Agent" in session.html


# ---------------------------------------------------------------------------
# 2. Named keys: Enter submits, Tab focuses, arrows drive selects


@pytest.mark.asyncio
async def test_enter_in_input_submits_form():
    received = []

    def handler(req: httpx.Request):
        if req.url.path == "/do-search" and req.method == "POST":
            received.append(req.read())
            return httpx.Response(
                200, text="<html><body><h1>Results for query</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            text="""<!DOCTYPE html><html><body>
            <form action="/do-search" method="POST">
              <input id="q" name="q" type="text" />
            </form>
            </body></html>""",
            headers={"content-type": "text/html"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/search")

    targets, _ = session.inspect()
    box = next(t for t in targets if t.role == "textbox")
    await session.type(box.ref, "paris hotels")

    # Refs are content-addressed; the typed value is part of identity, so
    # re-inspect before pressing the key on the same box.
    targets, _ = session.inspect()
    filled = next(t for t in targets if t.name == "q")
    await session.press_key(filled.ref, "enter")

    assert len(received) == 1
    assert b"q=paris+hotels" in received[0] or b"paris hotels" in received[0]
    assert "Results for query" in session.html


@pytest.mark.asyncio
async def test_tab_moves_focus_and_arrows_drive_select():
    page_html = """<!DOCTYPE html>
    <html><body>
    <input id="one" type="text" />
    <input id="two" type="text" />
    <select id="pick"><option value="a">A</option><option value="b">B</option></select>
    <span id="focus-log">none</span>
    <span id="pick-log">a</span>
    <script>
      document.getElementById("two").addEventListener("focus", function () {
        document.getElementById("focus-log").textContent = "focus:two";
      });
      document.getElementById("pick").addEventListener("change", function (e) {
        document.getElementById("pick-log").textContent = "picked:" + e.target.value;
      });
    </script>
    </body></html>"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=page_html, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/keys")

    targets, _ = session.inspect()
    one = next(t for t in targets if t.name == "one")
    await session.press_key(one.ref, "tab")
    assert "focus:two" in session.html

    pick = next(t for t in targets if t.role == "select")
    await session.press_key(pick.ref, "arrowdown")
    assert "picked:b" in session.html


@pytest.mark.asyncio
async def test_backspace_deletes_character():
    page_html = """<!DOCTYPE html>
    <html><body>
    <input id="q" type="text" />
    <span id="mirror">-</span>
    <script>
      document.getElementById("q").addEventListener("input", function (e) {
        document.getElementById("mirror").textContent = e.target.value + ":" + e.inputType;
      });
    </script>
    </body></html>"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=page_html, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/bs")

    targets, _ = session.inspect()
    box = next(t for t in targets if t.name == "q")
    await session.type(box.ref, "hello")
    targets, _ = session.inspect()
    filled = next(t for t in targets if t.name == "q")
    await session.press_key(filled.ref, "backspace")
    assert "hell:deleteContentBackward" in session.html


# ---------------------------------------------------------------------------
# 3. window.open popups become real tabs


@pytest.mark.asyncio
async def test_window_open_becomes_tab():
    def handler(req: httpx.Request):
        if req.url.path == "/helper":
            return httpx.Response(
                200,
                text="<html><head><title>Helper Window</title></head><body><h1>helper content</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            text="""<!DOCTYPE html><html><body>
            <button id="open">Open helper</button>
            <script>
              document.getElementById("open").addEventListener("click", function () {
                window.open("/helper", "helper");
              });
            </script>
            </body></html>""",
            headers={"content-type": "text/html"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/main")

    assert len(session.tabs) == 1
    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.label and "Open helper" in a.label)
    await session.act(btn.ref)

    tabs = session.tabs
    assert len(tabs) == 2
    helper = next(t for t in tabs if t["id"] != "tab_1")
    assert helper["url"].endswith("/helper")
    assert helper["title"] == "Helper Window"
    # The agent stays on the page that opened the popup.
    assert session.active_tab_id == "tab_1"

    popup_session = session.switch_tab(helper["id"])
    assert "helper content" in popup_session.view


# ---------------------------------------------------------------------------
# 4. Downloads: explicit tool and navigation interception


@pytest.mark.asyncio
async def test_download_tool_saves_bytes_and_hash(tmp_path):
    payload = b"PDFDATA-" + bytes(range(256)) * 8
    sha = hashlib.sha256(payload).hexdigest()

    def handler(req: httpx.Request):
        if req.url.path == "/files/report.pdf":
            return httpx.Response(
                200,
                content=payload,
                headers={"content-type": "application/pdf"},
            )
        return httpx.Response(200, text="<html><body>home</body></html>", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/")

    rec = await session.download(
        "https://example.com/files/report.pdf", expected=sha, dest_dir=str(tmp_path)
    )
    assert rec.error == ""
    assert rec.path and Path(rec.path).is_file()
    assert Path(rec.path).read_bytes() == payload
    assert rec.sha256 == sha
    assert rec.verified is True
    assert rec.scanned is False  # no verdict engine: disclosed, not faked


@pytest.mark.asyncio
async def test_goto_attachment_intercepts_as_download(tmp_path):
    payload = b"%PDF-1.4 fake pdf bytes for the regression"

    def handler(req: httpx.Request):
        if req.url.path == "/report.pdf":
            return httpx.Response(
                200,
                content=payload,
                headers={
                    "content-type": "application/pdf",
                    "content-disposition": 'attachment; filename="report.pdf"',
                },
            )
        return httpx.Response(
            200,
            text="""<!DOCTYPE html><html><body>
            <a id="dl" href="/report.pdf">Download report</a>
            </body></html>""",
            headers={"content-type": "text/html"},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/docs")

    surface = session.action_surface()
    link = next(a for a in surface.actions if a.kind == "navigate" and "report.pdf" in a.target)
    page = await session.act(link.ref)

    assert page.view.startswith("DOWNLOAD:")
    assert str(len(payload)) in page.view
    assert "sha256" in page.view
    assert "not scanned" in page.view
    saved_match = [line for line in page.view.splitlines() if "saved to" in line]
    assert saved_match, page.view
    saved_path = Path(saved_match[0].split("saved to", 1)[1].strip())
    assert saved_path.is_file()
    assert saved_path.read_bytes() == payload


# ---------------------------------------------------------------------------
# 5. contenteditable editors


@pytest.mark.asyncio
async def test_contenteditable_typing_via_action_surface():
    page_html = """<!DOCTYPE html>
    <html><body>
    <div id="editor" contenteditable="true">start</div>
    <span id="mirror">-</span>
    <script>
      var ed = document.getElementById("editor");
      ed.addEventListener("input", function (e) {
        document.getElementById("mirror").textContent = ed.textContent + "|" + e.inputType;
      });
    </script>
    </body></html>"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=page_html, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    await session.goto("https://example.com/editor")

    # Visible in the reading surface with an honest role.
    targets, _ = session.inspect()
    editable = next(t for t in targets if t.role == "editable")
    assert editable.tag == "div"

    # Visible in the action surface as an engine-actuated control.
    surface = session.action_surface()
    action = next(a for a in surface.actions if a.role == "editable")
    assert action.kind == "activate"

    await session.act(action.ref, text=" and more")
    assert "start and more" in session.html
    assert "insertText" in session.html


def test_minidom_contenteditable_property_and_enter():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        __load({t: 'html', c: [{t: 'body', c: []}]}, 'https://example.com');
        var ed = document.createElement('div');
        ed.setAttribute('contenteditable', 'true');
        ed.textContent = 'hello';
        document.body.appendChild(ed);
        var plain = document.createElement('div');
        document.body.appendChild(plain);
        ed.addEventListener('input', function (e) { window.__lastInput = e.inputType + ':' + e.data; });
    """)
    assert ctx.eval("ed.isContentEditable") is True
    assert ctx.eval("plain.isContentEditable") is False
    # Document-relative path: [html, body, ed]
    ctx.eval("__typePath([0, 0, 0], '!', null, false)")
    assert ctx.eval("ed.textContent") == "hello!"
    assert ctx.eval("globalThis.__lastInput") == "insertText:!"


# ---------------------------------------------------------------------------
# 6. WebSocket (own RFC 6455 transport, local echo server)


class _WsRecorder:
    """Minimal RFC 6455 server: handshake, one greeting, record one client frame."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self):
        self.received: list[str] = []
        self.ready = threading.Event()
        self.port = 0
        self._srv: socket.socket | None = None
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
        self._srv = srv
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
            # Greeting frame (server->client, unmasked) right after the handshake.
            greeting = b"connected"
            conn.sendall(bytes([0x81, len(greeting)]) + greeting)
            # Read one masked client frame.
            hdr = b""
            while len(hdr) < 2:
                hdr += conn.recv(2 - len(hdr))
            ln = hdr[1] & 0x7F
            mask = conn.recv(4)
            payload = b""
            while len(payload) < ln:
                payload += conn.recv(ln - len(payload))
            unmasked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            self.received.append(unmasked.decode("utf-8", "replace"))
            # Stay open until the test shuts down.
            conn.settimeout(5)
            with contextlib.suppress(Exception):
                conn.recv(64)
            conn.close()
        except Exception:
            pass

    def wait_received(self, timeout=5.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.received:
                return self.received[0]
            time.sleep(0.05)
        return ""


@pytest.mark.asyncio
async def test_websocket_connect_send_receive():
    from webresearch.engine import ws as ws_mod

    server = _WsRecorder()
    page_html = f"""<!DOCTYPE html>
    <html><body>
    <span id="status">connecting</span>
    <script>
      var ws = new WebSocket("ws://127.0.0.1:{server.port}/");
      ws.onopen = function () {{ ws.send("hello-from-page"); }};
      ws.onmessage = function (e) {{ document.getElementById("status").textContent = "msg:" + e.data; }};
    </script>
    <script>/* second script: _wants_js gates single-script pages */</script>
    </body></html>"""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=page_html, headers={"content-type": "text/html"})
    )
    client = httpx.AsyncClient(transport=transport)
    session = _session(client)
    try:
        await session.goto("https://example.com/ws-page")
        assert "msg:connected" in session.html
        assert server.wait_received() == "hello-from-page"
    finally:
        ws_mod.shutdown_all()


def test_ws_rejects_non_ws_scheme():
    from webresearch.engine import ws as ws_mod

    result = json.loads(ws_mod.connect("https://example.com/socket"))
    assert result["ok"] is False
    assert "scheme" in result["error"]
