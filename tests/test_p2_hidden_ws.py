"""P2-5/P2-7 regressions: CSS-hidden controls are not actionable, and the
WebSocket fixes (binary send, fragmented-binary continuation leak, ping
keepalive).

Before these fixes: collect() parsed serialized HTML with no cascade, so
controls hidden by inline display:none, stylesheet rules, visibility:hidden,
or a hidden ancestor all appeared actionable — and acting on them "worked",
which a real browser would never do. On the socket side, sending binary was
impossible, continuation frames of a fragmented binary message were decoded
as UTF-8 text into the inbox, and the client never pinged, so idle
connections died at proxies.
"""

import base64
import contextlib
import hashlib
import socket
import struct
import threading
import time

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

HIDDEN_PAGE = """<html><head><style>
.hide{display:none} .invis{visibility:hidden}
</style></head><body>
<button id="h1" style="display:none">HiddenInline</button>
<button id="h2" class="hide">HiddenByStylesheet</button>
<button id="h3" class="invis">InvisibleVisibility</button>
<div style="display:none"><button id="h4">HiddenAncestor</button></div>
<button id="v1">VisibleOne</button>
<a href="/x" id="h5" style="visibility:hidden">HiddenLink</a>
<script>window.__ready=1;</script>
</body></html>"""


def _client(page: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=page, headers={"content-type": "text/html"})
        )
    )


# ---------------------------------------------------------------------------
# Hidden controls


@pytest.mark.asyncio
async def test_css_hidden_controls_not_actionable():
    session = Session(_client(HIDDEN_PAGE), "s_p2_hidden")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    labels = [a.label for a in surf.actions]
    assert labels == ["VisibleOne"], labels
    assert surf.hidden_controls == 5, (
        f"all five hiding methods must be counted, got {surf.hidden_controls}"
    )


@pytest.mark.asyncio
async def test_hidden_count_reported_in_coverage_line():
    session = Session(_client(HIDDEN_PAGE), "s_p2_hidden2")
    await session.goto(f"{BASE}/", max_chars=20_000)
    surf = session.action_surface()
    line = surf.coverage_line()
    assert "hidden by CSS" in line and "5" in line, line


@pytest.mark.asyncio
async def test_inline_hidden_filtered_without_engine():
    """Scriptless page (no ctx): the inline-style fallback still applies."""
    page = """<html><body>
<button id="h1" style="display:none">HiddenInline</button>
<button id="v1">VisibleOne</button>
</body></html>"""
    session = Session(_client(page), "s_p2_hidden3")
    await session.goto(f"{BASE}/", max_chars=20_000)
    assert session.current_tab.ctx is None
    surf = session.action_surface()
    assert [a.label for a in surf.actions] == ["VisibleOne"]
    assert surf.hidden_controls == 1


@pytest.mark.asyncio
async def test_act_on_hidden_control_refuses():
    """A hidden control's ref is gone from the surface: act() says so."""
    session = Session(_client(HIDDEN_PAGE), "s_p2_hidden4")
    await session.goto(f"{BASE}/", max_chars=20_000)
    from webresearch.actions import collect

    raw = collect(session.html, session.url, max_actions=100)
    hidden_ref = next(a.ref for a in raw.actions if a.label == "HiddenAncestor")
    with pytest.raises(ValueError, match="no action"):
        await session.act(hidden_ref, max_chars=20_000)


# ---------------------------------------------------------------------------
# WebSocket: binary send, fragmented-binary continuation, ping keepalive


class _FrameServer:
    """Minimal RFC 6455 server: handshake, then scripted raw frames out and
    recording of raw client frames in."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, send_after_handshake: bytes = b""):
        self.client_frames: list[bytes] = []
        self.ready = threading.Event()
        self.port = 0
        self._send_after = send_after_handshake
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
            if self._send_after:
                conn.sendall(self._send_after)
            # Record raw client frames until the peer goes quiet.
            conn.settimeout(3)
            buffer = b""
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buffer += chunk
            # Best-effort: keep raw bytes; the test parses frames itself.
            self.client_frames.append(buffer)
            conn.close()
        except Exception:
            pass


def _parse_client_frames(raw: bytes) -> list[tuple[int, bytes]]:
    """Parse masked client frames from a raw byte stream."""
    frames = []
    i = 0
    while i + 2 <= len(raw):
        b0, b1 = raw[i], raw[i + 1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            length = struct.unpack("!H", raw[i + 2 : i + 4])[0]
            offset = 4
        elif length == 127:
            length = struct.unpack("!Q", raw[i + 2 : i + 10])[0]
            offset = 10
        mask = raw[i + offset : i + offset + 4]
        offset += 4
        payload = raw[i + offset : i + offset + length]
        if len(payload) < length:
            break
        unmasked = bytes(b ^ mask[j % 4] for j, b in enumerate(payload))
        frames.append((opcode if fin else 0x100 + opcode, unmasked))
        i += offset + length
    return frames


@pytest.mark.asyncio
async def test_websocket_binary_send_is_opcode_2_and_byte_exact():
    server = _FrameServer()
    page_html = f"""<!DOCTYPE html>
    <html><body><div id="out">idle</div>
    <script>
      var ws = new WebSocket("ws://127.0.0.1:{server.port}/");
      ws.onopen = function () {{
        var buf = new Uint8Array([0, 1, 250, 255, 128, 7]);
        ws.send(buf);
        document.getElementById("out").textContent = "sent";
      }};
    </script>
    <script>/* second script: _wants_js gates single-script pages */</script>
    </body></html>"""
    session = Session(_client(page_html), "s_p2_wsbin")
    await session.goto(f"{BASE}/", max_chars=20_000)
    deadline = time.time() + 5
    raw = b""
    while time.time() < deadline:
        if server.client_frames:
            raw = server.client_frames[0]
            break
        time.sleep(0.05)
    frames = _parse_client_frames(raw)
    binaries = [p for op, p in frames if op == 0x2]
    assert binaries, f"no binary frame sent; frames={frames}"
    assert binaries[0] == bytes([0, 1, 250, 255, 128, 7]), binaries[0]


@pytest.mark.asyncio
async def test_fragmented_binary_continuation_does_not_leak_into_text():
    # A fragmented binary message (opcode 2, FIN=0; continuation opcode 0,
    # FIN=1) followed by a clean text frame.
    frag = bytes([0x02, 0x03]) + b"\x00\xff\x9c" + bytes([0x80, 0x03]) + b"\x88\x72\x00"
    text = bytes([0x81, 5]) + b"hello"
    server = _FrameServer(send_after_handshake=frag + text)
    page_html = f"""<!DOCTYPE html>
    <html><body><div id="out">idle</div>
    <script>
      var ws = new WebSocket("ws://127.0.0.1:{server.port}/");
      var got = [];
      ws.onmessage = function (e) {{ got.push(e.data); document.getElementById("out").textContent = got.join(','); }};
      window.__wsGot = got;
    </script>
    <script>/* second script: _wants_js gates single-script pages */</script>
    </body></html>"""
    session = Session(_client(page_html), "s_p2_wsfrag")
    await session.goto(f"{BASE}/", max_chars=20_000)
    # Pump the runtime a few times so the socket drains and messages deliver.
    for _ in range(6):
        await session.scroll(y=1, max_chars=20_000)
        if "hello" in session.html:
            break
        time.sleep(0.1)
    out = [l for l in session.html.splitlines() if 'id="out"' in l]
    assert out and "hello" in out[0], f"text frame never delivered: {out}"
    # The continuation bytes (0x88 0x72 0x00 etc.) must NOT appear as a
    # delivered text message — the old leak decoded them as mojibake.
    assert "\\x88" not in out[0] and "hello" in out[0]
    ctx = session.current_tab.ctx
    got = ctx.eval("window.__wsGot.join('|')") if ctx else ""
    assert got == "hello", f"binary continuation leaked into the text inbox: {got!r}"


def test_client_ping_keepalive_on_idle():
    """poll_all sends a ping when the connection has been idle past the
    interval; a fake socket records the written frame."""
    from webresearch.engine import ws as ws_mod

    class FakeSock:
        def __init__(self):
            self.written = b""
            self.sent_frames: list[bytes] = []

        def setblocking(self, _b):
            pass

        def settimeout(self, _t):
            pass

        def sendall(self, data):
            self.written += data
            self.sent_frames.append(bytes(data))

        def recv(self, _n):
            raise BlockingIOError

        def close(self):
            pass

    sock = FakeSock()
    conn = ws_mod._Conn(999_001, "ws://127.0.0.1/x")
    conn.sock = sock
    conn.last_ping = time.time() - (ws_mod._PING_INTERVAL + 10)
    ws_mod._REGISTRY[999_001] = conn
    try:
        ws_mod.poll_all()
        assert sock.sent_frames, "no keepalive ping was sent on an idle connection"
        frame = sock.sent_frames[0]
        assert frame[0] & 0x0F == 0x9, f"expected a ping frame, got opcode {frame[0] & 0x0F}"
        # A freshly pinged connection must not ping again on the next poll.
    finally:
        ws_mod._REGISTRY.pop(999_001, None)
    # After the ping, last_ping is recent: the next poll sends nothing.
    sock.sent_frames.clear()
    ws_mod._REGISTRY[999_002] = conn
    conn.id = 999_002
    try:
        ws_mod.poll_all()
        assert not sock.sent_frames, "pinged again immediately after a ping"
    finally:
        ws_mod._REGISTRY.pop(999_002, None)


def test_fragmented_binary_frames_all_counted():
    """Unit-level: a fragmented binary message counts every frame and never
    touches the text inbox."""
    from webresearch.engine import ws as ws_mod

    conn = ws_mod._Conn(999_003, "ws://127.0.0.1/x")
    # Server->client frames are unmasked.
    fin0_bin = bytes([0x02, 0x02]) + b"\x00\x01"
    cont_fin = bytes([0x80, 0x02]) + b"\x02\x03"
    conn.buffer = fin0_bin + cont_fin
    ws_mod._parse_frames(conn)
    assert conn.binary_skipped == 2, conn.binary_skipped
    assert conn.inbox == [], "binary continuation must never reach the text inbox"
