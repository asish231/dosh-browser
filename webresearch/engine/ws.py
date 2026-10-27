"""Hand-rolled RFC 6455 WebSocket client. Own transport: raw sockets + TLS.

No third-party websocket library. The handshake, frame masking, and
fragmentation rules are implemented directly from RFC 6455 so page scripts get
a real socket, not a stub. Text frames are delivered to the JS context; binary
frames are counted and skipped (disclosed gap, not silently mangled) —
including every continuation frame of a fragmented binary message, which used
to leak into the text inbox as mojibake. Outgoing binary sends travel as real
opcode-2 frames, and the client answers server pings and sends its own
keepalive pings on idle connections.

Connections live in a module-level registry because a QuickJS context is
reused across action passes and a page's socket should stay open across them.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import itertools
import json
import os
import socket
import ssl
import struct
import time
from urllib.parse import unquote, urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_CONNECT_TIMEOUT = 6.0
_IO_TIMEOUT = 4.0
_MAX_HANDSHAKE_BYTES = 16 * 1024
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_PING_INTERVAL = 25.0

_IDS = itertools.count(1)


class _Conn:
    def __init__(self, conn_id: int, url: str) -> None:
        self.id = conn_id
        self.url = url
        self.sock: socket.socket | None = None
        self.protocol = ""
        self.buffer = b""
        self.inbox: list[str] = []
        self.closed = False
        self.close_code = 0
        self.close_reason = ""
        self.binary_skipped = 0
        self.frag_buffer = b""
        self.frag_opcode = 0
        self.last_ping = time.time()


_REGISTRY: dict[int, _Conn] = {}


def _fail(error: str) -> str:
    return json.dumps({"ok": False, "error": str(error)[:200], "code": 1006})


def connect(url: str, protocols: str = "") -> str:
    """Synchronous handshake. Returns a JSON result string for the JS host call."""
    try:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("ws", "wss"):
            return _fail(f"scheme must be ws:// or wss://, got {scheme or 'none'}")
        secure = scheme == "wss"
        host = parts.hostname or ""
        port = parts.port or (443 if secure else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        if not host:
            return _fail("missing host")

        raw = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        if secure:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        raw.settimeout(_IO_TIMEOUT)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}" + ("" if port in (80, 443) else f":{port}"),
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        wanted = [p.strip().lower() for p in protocols.split(",") if p and p.strip()]
        if wanted:
            lines.append(f"Sec-WebSocket-Protocol: {', '.join(p.strip() for p in protocols.split(',') if p.strip())}")
        raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = raw.recv(4096)
            if not chunk:
                return _fail("connection closed during handshake")
            response += chunk
            if len(response) > _MAX_HANDSHAKE_BYTES:
                return _fail("handshake response too large")
        head, rest = response.split(b"\r\n\r\n", 1)
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101 " not in f" {status_line} ":
            code = status_line.split(" ")[1] if " " in status_line else "?"
            return _fail(f"handshake rejected: HTTP {code}")
        headers: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept", "") != expected:
            return _fail("Sec-WebSocket-Accept mismatch")
        protocol = ""
        offered = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
        for p in offered:
            if p.lower() in wanted:
                protocol = p
                break

        conn = _Conn(next(_IDS), url)
        conn.sock = raw
        conn.protocol = protocol
        conn.buffer = rest
        raw.setblocking(False)
        _REGISTRY[conn.id] = conn
        return json.dumps({"ok": True, "id": conn.id, "protocol": protocol})
    except Exception as exc:
        return _fail(exc)


def _send_frame(conn: _Conn, opcode: int, payload: bytes) -> None:
    if conn.sock is None:
        raise OSError("socket closed")
    header = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + struct.pack("!H", n)
    else:
        header += bytes([0x80 | 127]) + struct.pack("!Q", n)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    conn.sock.setblocking(True)
    conn.sock.settimeout(_IO_TIMEOUT)
    try:
        conn.sock.sendall(header + mask + masked)
    finally:
        conn.sock.setblocking(False)


def send(conn_id: int, data: str) -> int:
    conn = _REGISTRY.get(int(conn_id))
    if conn is None or conn.closed:
        return 0
    try:
        _send_frame(conn, 0x1, str(data).encode("utf-8"))
        return 1
    except Exception:
        return 0


def send_binary(conn_id: int, data_b64: str) -> int:
    """Send an opcode-2 binary frame. The JS side hands us base64 so the
    payload is byte-exact (text frames are the only thing String() can carry)."""
    conn = _REGISTRY.get(int(conn_id))
    if conn is None or conn.closed:
        return 0
    try:
        payload = base64.b64decode(str(data_b64 or ""))
    except Exception:
        return 0
    try:
        _send_frame(conn, 0x2, payload)
        return 1
    except Exception:
        return 0


def close(conn_id: int, code: int = 1000) -> int:
    conn = _REGISTRY.pop(int(conn_id), None)
    if conn is None:
        return 0
    try:
        if not conn.closed and conn.sock is not None:
            _send_frame(conn, 0x8, struct.pack("!H", int(code)))
    except Exception:
        pass
    try:
        if conn.sock is not None:
            conn.sock.close()
    except Exception:
        pass
    conn.closed = True
    conn.sock = None
    return 1


def _parse_frames(conn: _Conn) -> None:
    while True:
        if len(conn.buffer) < 2:
            return
        b0, b1 = conn.buffer[0], conn.buffer[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if len(conn.buffer) < 4:
                return
            length = struct.unpack("!H", conn.buffer[2:4])[0]
            offset = 4
        elif length == 127:
            if len(conn.buffer) < 10:
                return
            length = struct.unpack("!Q", conn.buffer[2:10])[0]
            offset = 10
        if length > _MAX_FRAME_BYTES:
            close(conn.id)
            return
        if masked:
            if len(conn.buffer) < offset + 4:
                return
            mask = conn.buffer[offset : offset + 4]
            offset += 4
        if len(conn.buffer) < offset + length:
            return
        payload = conn.buffer[offset : offset + length]
        conn.buffer = conn.buffer[offset + length :]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        if opcode == 0x8:  # close
            code = 1005
            if len(payload) >= 2:
                code = struct.unpack("!H", payload[:2])[0]
            with contextlib.suppress(Exception):
                _send_frame(conn, 0x8, payload[:2])
            conn.close_code = code
            conn.close_reason = unquote(payload[2:].decode("utf-8", "replace")[:120])
            with contextlib.suppress(Exception):
                if conn.sock is not None:
                    conn.sock.close()
            conn.sock = None
            conn.closed = True
            _REGISTRY.pop(conn.id, None)
            return
        if opcode == 0x9:  # ping -> pong
            with contextlib.suppress(Exception):
                _send_frame(conn, 0xA, payload)
            continue
        if opcode == 0xA:  # pong
            continue
        if opcode == 0x2:  # binary: counted, not delivered (disclosed gap)
            conn.binary_skipped += 1
            # A fragmented binary message's continuation frames belong to the
            # binary lane: track the state so opcode-0 frames that follow are
            # skipped too instead of being decoded as UTF-8 text into the
            # inbox (the old leak delivered mojibake as a chat message).
            if not fin:
                conn.frag_opcode = 0x2
                conn.frag_buffer = b""
            else:
                conn.frag_opcode = 0
                conn.frag_buffer = b""
            continue
        if opcode == 0x0 and conn.frag_opcode == 0x2:
            # Continuation of a skipped binary message: skip, count, and stay
            # in binary-fragment state until FIN.
            conn.binary_skipped += 1
            if fin:
                conn.frag_opcode = 0
                conn.frag_buffer = b""
            continue
        if opcode in (0x1, 0x0):  # text / continuation
            if opcode == 0x1:
                conn.frag_buffer = payload
                conn.frag_opcode = 0x1
            else:
                conn.frag_buffer += payload
            if fin:
                conn.inbox.append(conn.frag_buffer.decode("utf-8", "replace"))
                conn.frag_buffer = b""
                conn.frag_opcode = 0


def poll_all() -> str:
    """Drain every open socket. Returns {messages:[{id,data}], closed:[{id,code,reason}]}."""
    messages: list[dict] = []
    closed: list[dict] = []
    now = time.time()
    for conn in list(_REGISTRY.values()):
        try:
            # Client-initiated ping keepalive: real browsers ping idle
            # connections so proxies keep them open; we do the same on a
            # fixed interval. Server pongs are consumed silently above.
            if conn.sock is not None and (now - conn.last_ping) > _PING_INTERVAL:
                with contextlib.suppress(Exception):
                    _send_frame(conn, 0x9, b"keepalive")
                conn.last_ping = now
            if conn.sock is not None:
                conn.sock.setblocking(False)
                while True:
                    try:
                        chunk = conn.sock.recv(65536)
                    except BlockingIOError:
                        break
                    except (ConnectionResetError, OSError):
                        conn.closed = True
                        break
                    if not chunk:
                        conn.closed = True
                        break
                    conn.buffer += chunk
                    if len(conn.buffer) > _MAX_FRAME_BYTES:
                        break
            _parse_frames(conn)
            while conn.inbox:
                messages.append({"id": conn.id, "data": conn.inbox.pop(0)})
        except Exception:
            continue
    for conn_id, conn in list(_REGISTRY.items()):
        if conn.closed:
            closed.append({"id": conn_id, "code": conn.close_code or 1006, "reason": conn.close_reason})
            _REGISTRY.pop(conn_id, None)
    return json.dumps({"messages": messages, "closed": closed})


def active_count() -> int:
    return len(_REGISTRY)


def shutdown_all() -> int:
    """Close every registered socket. Best-effort cleanup."""
    count = 0
    for conn_id in list(_REGISTRY.keys()):
        count += close(conn_id)
    return count
