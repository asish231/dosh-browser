"""Opt-in telemetry for the owned HTTPX client and QuickJS runtime reports.

No integration is installed on import. Call ``attach()`` and explicitly feed each
runtime resource batch once to ``record_runtime``. The two sources are observations,
not deduplicated wire requests. Curl impersonation, other clients, cached responses,
and runtime failures absent from resource_requests are outside hook coverage.

HTTPX has no failure hook: a request without response headers stays ``unresolved``
(it may be in flight, cancelled, or failed). A response only proves headers arrived,
not that a body completed. Stream wrappers observe consumption, never read ahead.
Cursors enumerate request starts, not updates; query from an earlier cursor to
refresh unfinished records. The ledger is intended for one client/event loop.

Bodies are disabled by default. Opt-in captures complete JSON/form bodies only,
redacting secret-like keys recursively, with a byte cap. Unknown formats, encoded
streams, oversized and partial bodies are omitted rather than exposing an unsafe
prefix. Request bodies and exception messages are never retained. Redaction is
key-based, not a guarantee that arbitrary application data contains no secrets.
"""

from __future__ import annotations

import base64
import binascii
import struct
import time
import zlib
import gzip
import json
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

try:  # optional; decode(encoding="brotli") discloses when absent
    import brotli as _brotli
except ImportError:
    _brotli = None

SEALED_MAX_BYTES = 262144
WS_FRAME_RECORD_MAX = 4096

_REDACTED = "[REDACTED]"
_SECRET = re.compile(r"authorization|cookie|token|password|passwd|secret|api.?key|credential|session|csrf", re.I)
_RULE_SCOPE = "runtime host_fetch fetch/XHR only; no HTTPX/navigation interception"
_COVERAGE = {
    "httpx": "attached client event hooks only; excludes curl impersonation and other clients",
    "failures": "pre-header failures/cancellation remain unresolved; consumed stream failures are recorded",
    "runtime": "explicit resource_requests batches only; caller must submit each batch once",
    "correlation": "sources are not deduplicated; runtime records may overlap HTTPX observations",
    "cursor": "request-start IDs, not an update feed; re-query earlier IDs for changed records",
    "rules": _RULE_SCOPE,
}


def _url(value: str) -> str:
    try:
        parts = urlsplit(value)
        # Discard userinfo and fragments; even innocently named query values may be credentials.
        host = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(key, _REDACTED) for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))[:2048]
    except ValueError:
        return "[INVALID URL]"


def _headers(headers: Any) -> dict[str, str]:
    result = {}
    for key, value in list(headers.items())[:64]:
        name = str(key).lower()
        if _SECRET.search(name):
            value = _REDACTED
        elif name in {"location", "referer", "content-location"}:
            value = _url(str(value))
        result[name[:128]] = str(value)[:1024]
    return result


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _REDACTED if _SECRET.search(key) else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


# ============================ Phase 1 helpers ============================
# Mirror of the Rust lane's telemetry.rs free functions (N1/N6/N8/N9).


def _epoch_ms() -> float:
    return time.time() * 1000.0


def _sanitize_initiator_stack(stack: str) -> list[str]:
    """Sanitized JS call-site hint (N9): strip query strings, cap lines."""
    out = []
    for line in str(stack or "").splitlines():
        line = line.strip()
        if not line:
            continue
        q = line.find("?")
        if q >= 0:
            line = line[:q] + "?<REDACTED>"
        out.append(line[:200])
        if len(out) >= 12:
            break
    return out


def _redact_header_value(name: str, value: str) -> str:
    """Redacted header value for net.replay output (secrets stay hidden)."""
    lname = str(name).lower()
    sensitive = (
        lname in {"authorization", "cookie", "set-cookie", "proxy-authorization"}
        or "token" in lname
    )
    if not sensitive:
        return str(value)
    parts = str(value).split(" ", 1)
    if lname.endswith("authorization") and len(parts) == 2:
        return f"{parts[0]} [REDACTED]"
    return _REDACTED


def _parse_sse_with_arrivals(body: str, arrivals: list[tuple[int, float]]) -> list[dict]:
    """Parse a text/event-stream body into ordered events (N1).

    ``arrivals`` are (byte_end_offset, arrival_ms_after_first_byte) pairs from
    the chunked read; empty when no chunk timing was captured (all events get
    ``t: 0`` — absent, not invented).
    """
    events: list[dict] = []
    data_lines: list[str] = []
    event_type = ""
    last_id = ""
    offset = 0
    end_for_event = 0
    lines = str(body).split("\n")
    for line in lines:
        raw_line = line
        end_for_event = offset + len((raw_line + "\n").encode("utf-8"))
        line = line.rstrip("\r")
        offset = end_for_event
        if line == "":
            if data_lines:
                t = 0.0
                for off, ms in arrivals:
                    if off >= end_for_event:
                        t = ms
                        break
                else:
                    if arrivals:
                        t = arrivals[-1][1]
                events.append(
                    {
                        "t": t,
                        "event": event_type or "message",
                        "id": last_id,
                        "data": "\n".join(data_lines),
                    }
                )
                data_lines = []
                event_type = ""
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        elif line.startswith("event:"):
            event_type = line[6:].lstrip(" ")
        elif line.startswith("id:"):
            last_id = line[3:].lstrip(" ")
    return events


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    start = i
    while True:
        if i >= len(data):
            raise ValueError("truncated varint")
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if i - start > 10 or shift > 63:
            raise ValueError("varint too long")


def _pb_nested_or_text(chunk: bytes, depth: int) -> Any:
    # Plain text wins for ASCII-looking chunks; otherwise attempt structural.
    non_printable = any(b < 0x20 and b not in (9, 10, 13) for b in chunk)
    if not chunk or not non_printable:
        try:
            return chunk.decode("utf-8")
        except UnicodeDecodeError:
            pass
    try:
        fields = _decode_protobuf_structural(chunk, depth + 1)
        if fields:
            return fields
    except ValueError:
        pass
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return {"hex": chunk.hex().upper()}


def _decode_protobuf_structural_prefix(data: bytes, depth: int) -> list[dict]:
    """Wire-format walk; raises ValueError on non-protobuf shapes."""
    if depth > 8:
        raise ValueError("protobuf nested message exceeds depth 8")
    out: list[dict] = []
    i = 0
    n = len(data)
    while i < n:
        key, i = _read_varint(data, i)
        field, wire_type = key >> 3, key & 7
        if field == 0:
            raise ValueError("field number 0 is invalid")
        if wire_type == 0:
            value, i = _read_varint(data, i)
        elif wire_type == 1:
            if i + 8 > n:
                raise ValueError("truncated 64-bit value")
            value = struct.unpack("<d", data[i : i + 8])[0]
            i += 8
        elif wire_type == 2:
            length, i = _read_varint(data, i)
            if i + length > n:
                raise ValueError("truncated length-delimited field")
            value = _pb_nested_or_text(data[i : i + length], depth)
            i += length
        elif wire_type == 5:
            if i + 4 > n:
                raise ValueError("truncated 32-bit value")
            value = struct.unpack("<f", data[i : i + 4])[0]
            i += 4
        elif wire_type in (3, 4):
            raise ValueError(f"unsupported wire type {wire_type} (deprecated groups)")
        else:
            raise ValueError(f"unknown wire type {wire_type}")
        out.append({"field": field, "wire_type": wire_type, "value": value})
    return out


def decode_protobuf_structural(data: bytes, depth: int = 0) -> list[dict]:
    """Structural protobuf wire-format decode (N6): field numbers + wire types
    without any schema file. Nested length-delimited chunks that parse as valid
    messages become nested field lists. Depth-capped at 8."""
    fields = _decode_protobuf_structural_prefix(bytes(data), depth)
    if not fields:
        raise ValueError("no fields decoded")
    return fields


def decode_protobuf(data: bytes) -> dict:
    try:
        return {"fields": decode_protobuf_structural(data), "ok": True}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def decode_payload_bytes(raw: bytes, encoding: str = "auto") -> dict:
    """Inflate/analyze a captured body (N6). Honest failure reporting."""
    raw = bytes(raw)
    enc = (encoding or "auto").lower()
    if not raw:
        return {"empty": True, "encoding": enc}
    decoded: bytes
    if enc in ("identity", "none", ""):
        decoded = raw
    elif enc == "gzip":
        try:
            decoded = gzip.decompress(raw)
        except OSError as exc:
            return {"error": f"gzip decompress failed: {exc}", "encoding": enc}
    elif enc in ("zlib", "deflate"):
        try:
            decoded = zlib.decompress(raw)
        except zlib.error as exc:
            return {"error": f"zlib decompress failed: {exc}", "encoding": enc}
    elif enc == "raw-deflate":
        try:
            decoded = zlib.decompress(raw, -15)
        except zlib.error as exc:
            return {"error": f"raw deflate decompress failed: {exc}", "encoding": enc}
    elif enc == "brotli":
        if _brotli is None:
            return {"error": "brotli module not installed (pip install brotli)"}
        try:
            decoded = _brotli.decompress(raw)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"brotli decompress failed: {exc}", "encoding": enc}
    elif enc == "auto":
        if raw[:2] == b"\\x1f\\x8b":
            try:
                decoded = gzip.decompress(raw)
            except OSError as exc:
                return {"error": f"gzip decompress failed: {exc}", "encoding": enc}
        elif raw[:1] == b"\\x78":
            try:
                decoded = zlib.decompress(raw)
            except zlib.error as exc:
                return {"error": f"zlib decompress failed: {exc}", "encoding": enc}
        else:
            try:
                decoded = zlib.decompress(raw, -15)
            except zlib.error as exc:
                return {
                    "error": f"auto decompress failed: not gzip/zlib header, raw deflate failed: {exc};"
                    " available: gzip|zlib|raw-deflate|brotli|identity",
                    "encoding": enc,
                }
    else:
        return {
            "error": f"unknown encoding '{enc}'; available: auto|gzip|zlib|deflate|raw-deflate|brotli|identity",
            "encoding": enc,
        }

    out = {"encoding": enc, "compressed_len": len(raw), "decoded_len": len(decoded)}
    # Structure analysis: protobuf, JSON, text, binary hex — explicit branches.
    non_printable = any(b < 0x20 and b not in (9, 10, 13) for b in decoded[:4096])
    if non_printable:
        pb = decode_protobuf(decoded)
        if pb.get("ok"):
            out.update({"format": "protobuf-structural", "fields": pb["fields"],
                        "note": "field numbers + wire types only; no schema file"})
            return out
    text = decoded.decode("utf-8", errors="strict")
    trimmed = text.lstrip()
    if trimmed.startswith(("{", "[")):
        try:
            out.update({"format": "json", "redacted_data": _redact(json.loads(trimmed))})
            return out
        except ValueError:
            pass
    out.update({"format": "text", "text": text[:200_000]})
    return out

# ========================= end Phase 1 free helpers =========================


class _ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream, ledger: NetworkLedger, request_id: int, content_type: str, encoded: bool):
        self.stream = stream
        self.ledger = ledger
        self.request_id = request_id
        self.content_type = content_type
        self.encoded = encoded
        self.buffer = bytearray()
        self.size = 0
        self.complete = False
        self.arrivals: list[tuple[int, float]] = []
        self.t0 = _epoch_ms()
        self.ttfb: float | None = None
        self.sealed_buffer = bytearray()
        self.sealed_response_headers: dict = {}

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                now = _epoch_ms()
                if self.ttfb is None:
                    self.ttfb = now - self.t0
                self.size += len(chunk)
                self.arrivals.append((self.size, now - self.t0))
                # Sealed raw evidence (never serialized in query output), capped.
                room = SEALED_MAX_BYTES - len(self.sealed_buffer)
                if room > 0:
                    self.sealed_buffer.extend(chunk[:room])
                if self.ledger.capture_bodies and not self.encoded:
                    if self.size <= self.ledger.body_max_bytes:
                        self.buffer.extend(chunk)
                    else:
                        self.buffer.clear()
                yield chunk
        except BaseException as exc:
            self.ledger._update(
                self.request_id, state="body_failed", error=type(exc).__name__, bytes=self.size,
                body_state="omitted_partial" if self.ledger.capture_bodies else "disabled",
            )
            raise
        else:
            self.complete = True
            updates = {"state": "complete", "bytes": self.size}
            if self.ttfb is not None:
                # N4 + N1: measured chunk arrivals; stored both raw and (for
                # SSE bodies) decoded into per-event arrival stamps.
                updates["arrivals"] = [(off, ms) for off, ms in self.arrivals]
                self.ledger._merge_timing(
                    self.request_id,
                    {"ttfb_ms": self.ttfb, "duration_ms": self.arrivals[-1][1] if self.arrivals else 0.0},
                )
            if self.ledger.capture_bodies:
                if self.encoded:
                    updates["body_state"] = "omitted_encoded_stream"
                elif self.size > self.ledger.body_max_bytes:
                    updates["body_state"] = "omitted_over_cap"
                else:
                    updates.update(self.ledger._body(bytes(self.buffer), self.content_type))
            if self.ledger.capture_bodies and (self.content_type or "").lower().startswith("text/event-stream"):
                events = _parse_sse_with_arrivals(bytes(self.buffer).decode("utf-8", errors="replace"), self.arrivals)
                for ev in events:
                    if ev["event"] == "message" and ev["data"]:
                        stripped = ev["data"].lstrip()
                        if stripped.startswith(("[", "{")):
                            try:
                                # Redacted here: JSON data payloads do not pre-empt secret hygiene.
                                ev["data"] = json.dumps(_redact(json.loads(stripped)), ensure_ascii=True)
                            except ValueError:
                                pass
                updates["stream_events"] = events
            # Store sealed raw evidence for authorized decode/replay/reveal.
            self.ledger._seal(self.request_id, None, bytes(self.sealed_buffer), self.content_type, self.size)
            self.ledger._update(self.request_id, **updates)
        finally:
            self.buffer.clear()
            self.sealed_buffer.clear()

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            if not self.complete:
                record = self.ledger._records.get(self.request_id)
                if record and record["state"] != "body_failed":
                    self.ledger._update(
                        self.request_id, state="body_not_completed", bytes=self.size,
                        body_state="omitted_partial" if self.ledger.capture_bodies else "disabled",
                    )
            self.buffer.clear()


class NetworkLedger:
    """Bounded, redacted observations; attachment is explicit and idempotent.

    ``body_max_bytes`` (default 16 KiB) caps both input and serialized redacted
    output. Metadata is limited to 64 headers, 128-character header names,
    1024-character values and 2048-character URLs. Raw bodies are never stored.
    ``network_sent=None`` means hooks cannot prove a network transmission.
    Runtime mocks, blocks and cache hits explicitly use ``False``.
    """

    def __init__(self, client: httpx.AsyncClient, max_records: int = 1000,
                 capture_bodies: bool = False, *, body_max_bytes: int = 16384):
        if not isinstance(client, httpx.AsyncClient):
            raise TypeError("client must be an httpx.AsyncClient")
        for name, value in (("max_records", max_records), ("body_max_bytes", body_max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.client = client
        self.max_records = max_records
        self.capture_bodies = capture_bodies
        self.body_max_bytes = body_max_bytes
        self._records: OrderedDict[int, dict] = OrderedDict()
        self._last_id = 0
        self._rule_id = 0
        self._extension = f"dosh_ledger_{id(self)}"
        self._request_hook = self._on_request
        self._response_hook = self._on_response
        # ---- Phase 1 state (mirrors telemetry.rs NetworkLedger) ------------
        self._sealed: dict[int, dict] = {}          # raw request evidence, never in query output
        self._reveal_allowed = False                # N8 gate: off by default
        self._reveal_audit: list[dict] = []
        self._ws_sessions: list[dict] = []
        self._ws_frames: list[dict] = []
        self._next_ws_id = 0
        self._t0: dict[int, float] = {}             # per-record request-start epoch ms

    def attach(self) -> NetworkLedger:
        """Append hooks without replacing existing hooks. Return self."""
        for kind, hook in (("request", self._request_hook), ("response", self._response_hook)):
            hooks = self.client.event_hooks.setdefault(kind, [])
            if hook not in hooks:
                hooks.append(hook)
        return self

    def detach(self) -> None:
        """Remove only this ledger's hooks; existing observed streams may finish."""
        for kind, hook in (("request", self._request_hook), ("response", self._response_hook)):
            hooks = self.client.event_hooks.get(kind, [])
            if hook in hooks:
                hooks.remove(hook)

    def _append(self, **record) -> int:
        self._last_id += 1
        self._records[self._last_id] = {"id": self._last_id, **record}
        while len(self._records) > self.max_records:
            self._records.popitem(last=False)
        return self._last_id

    def _update(self, request_id: int, **updates) -> None:
        if request_id in self._records:
            self._records[request_id].update(updates)

    async def _on_request(self, request: httpx.Request) -> None:
        request_id = self._append(
            source="httpx", url=_url(str(request.url)), method=request.method,
            request_headers=_headers(request.headers), response_headers={}, status=None,
            state="unresolved", network_sent=None, body_state="disabled" if not self.capture_bodies else "pending",
        )
        self._t0[request_id] = _epoch_ms()
        request.extensions[self._extension] = request_id

    async def _on_response(self, response: httpx.Response) -> None:
        request_id = response.request.extensions.get(self._extension)
        if request_id not in self._records:
            return
        t0 = self._t0.pop(request_id, None)
        if t0 is not None:
            # N4: measured wall-clock from dispatch to headers.
            self._update(request_id, timing={"started_at_ms": t0, "ttfb_ms": _epoch_ms() - t0,
                                            "duration_ms": None, "disclosed_gaps": ["dns", "connect", "tls"]})
        self._update(request_id, status=response.status_code, response_headers=_headers(response.headers), state="headers_received")
        content_type = response.headers.get("content-type", "")
        if response.is_stream_consumed:
            try:
                content = response.content
            except httpx.ResponseNotRead:
                self._update(request_id, state="consumed_before_ledger", body_state="unavailable_prior_hook")
                return
            updates = {"state": "complete", "bytes": len(content)}
            if self.capture_bodies:
                updates.update(self._body(content, content_type))
            self._update(request_id, **updates)
        else:
            response.stream = _ObservedStream(
                response.stream, self, request_id, content_type,
                response.headers.get("content-encoding", "identity").lower() != "identity",
            )

    def _body(self, raw: bytes, content_type: str) -> dict:
        if len(raw) > self.body_max_bytes:
            return {"body_state": "omitted_over_cap"}
        mime = content_type.split(";", 1)[0].strip().lower()
        try:
            text = raw.decode("utf-8")
            if mime == "application/json" or mime.endswith("+json"):
                value = json.loads(text)
                text = json.dumps(_redact(value), ensure_ascii=True)
            elif mime == "application/x-www-form-urlencoded":
                text = urlencode([(key, _REDACTED if _SECRET.search(key) else value)
                                  for key, value in parse_qsl(text, keep_blank_values=True)])
            else:
                return {"body_state": "omitted_unsupported_format"}
        except (ValueError, RecursionError):
            return {"body_state": "omitted_invalid_or_partial"}
        if len(text.encode("utf-8")) > self.body_max_bytes:
            return {"body_state": "omitted_over_cap"}
        return {"body_state": "captured_redacted", "response_body": text}

    def record_runtime(self, info: dict, tab_id: str, url: str) -> int:
        """Ingest one resource_requests batch, returning its observation count.

        Pass only newly reported entries. Original reports are not mutated or
        retained. Runtime ``body`` is ambiguous (often the *request* body), so it
        is used only for explicitly mocked responses. Existing upstream limits
        may already have truncated runtime metadata or response bodies.
        """
        count = 0
        for item in info.get("resource_requests", []):
            if not isinstance(item, dict):
                continue
            disposition = str(item.get("disposition", ""))
            headers = item.get("response_headers") or {}
            mocked = disposition.startswith("mocked:") or str(headers.get("x-dosh-mock", "")) == "1"
            blocked = disposition.startswith("blocked:") or item.get("content_type") == "blocked-by-rule"
            cached = bool(item.get("sw"))
            status = item.get("status")
            failed = status == 0 or bool(item.get("error"))
            record = {
                "source": "runtime", "tab_id": str(tab_id)[:128], "page_url": _url(url),
                "url": _url(urljoin(url, str(item.get("url", "")))),
                "method": str(item.get("method") or "GET").upper()[:32],
                "status": status if isinstance(status, int) else None,
                "request_headers": _headers(item.get("request_headers") or {}),
                "response_headers": _headers(headers),
                "state": "mocked" if mocked else "blocked" if blocked else "failed" if failed else "reported",
                "network_sent": False if mocked or blocked or cached else None,
                "body_state": "disabled" if not self.capture_bodies else "unavailable",
                "content_type": str(item.get("content_type") or headers.get("content-type", ""))[:200],
                "initiator_type": str(item.get("initiatorType") or "fetch")[:64],
                "caused_by": str(item.get("caused_by"))[:128] if item.get("caused_by") else None,
            }
            size = item.get("bytes")
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                record["bytes"] = size
            # ---- Phase 1 evidence propagation (N4/N5/N9) ----
            stack = item.get("initiator_stack")
            if isinstance(stack, list) and stack:
                record["initiator_stack"] = [str(line)[:200] for line in stack[:12]]
                record["initiator_stack_sanitized"] = True
            timing = item.get("timing")
            if isinstance(timing, dict) and timing:
                record["timing"] = {
                    k: timing.get(k) for k in ("started_at_ms", "ttfb_ms", "duration_ms") if k in timing
                }
            sealed = item.get("sealed")
            if isinstance(sealed, dict) and sealed:
                sealed_out: dict = {}
                body_b64 = sealed.get("request_body_b64")
                if isinstance(body_b64, str):
                    try:
                        sealed_out["body"] = base64.b64decode(body_b64)
                    except (binascii.Error, ValueError):
                        sealed_out["body"] = b""
                resp_b64 = sealed.get("response_body_b64")
                if isinstance(resp_b64, str):
                    try:
                        sealed_out["response_body"] = base64.b64decode(resp_b64)[:SEALED_MAX_BYTES]
                    except (binascii.Error, ValueError):
                        sealed_out["response_body"] = b""
                sealed_out["len"] = len(sealed_out.get("response_body", b""))
            else:
                sealed_out = None
            if self.capture_bodies:
                body = item.get("response_body")
                if body is None and mocked:
                    body = item.get("body")
                if isinstance(body, str):
                    content_type = str(item.get("content_type") or headers.get("content-type", ""))
                    record.update(self._body(body.encode("utf-8"), content_type))
            if isinstance(item.get("truncated"), bool):
                record["truncated"] = item["truncated"]
            self._append_with_sealed(record, sealed_out)
            if sealed_out:
                count += 0  # sealed evidence attached to the appended record
            count += 1
        return count

    def query(self, after: int = 0, limit: int = 100, pattern: str = "") -> dict:
        """Literal URL filtering with explicit retention and pagination omissions.

        ``next_after`` advances past scanned nonmatches; no regex is evaluated.
        ``omitted_retention`` counts evicted IDs after the supplied cursor (their
        pattern matches cannot be known). ``omitted_limit`` counts retained
        matches not returned. Returned records are independent copies.
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("after must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(pattern, str):
            raise TypeError("pattern must be a string")
        oldest = next(iter(self._records), self._last_id + 1)
        matches = [record for key, record in self._records.items() if key > after and pattern in record["url"]]
        returned = matches[:limit]
        omitted_limit = len(matches) - len(returned)
        omitted_retention = max(0, oldest - after - 1)
        next_after = returned[-1]["id"] if omitted_limit else max(after, self._last_id)
        return {
            "records": deepcopy(returned), "returned": len(returned), "matching_retained": len(matches),
            "retained": len(self._records), "total_observed": self._last_id, "oldest_id": oldest,
            "latest_id": self._last_id, "next_after": next_after,
            "omitted_retention": omitted_retention, "omitted_limit": omitted_limit,
            "complete": not (omitted_retention or omitted_limit),
            "coverage": dict(_COVERAGE),
            "metadata_limits": {"headers": 64, "header_name_chars": 128, "header_value_chars": 1024, "url_chars": 2048},
        }

    def discover_apis(self) -> list[dict]:
        """Catalog discovered background API endpoints (XHR/fetch/JSON) for Site-as-SDK direct replay."""
        endpoints: dict[tuple[str, str], dict] = {}
        for record in self._records.values():
            url = record.get("url", "")
            method = record.get("method", "GET").upper()
            content_type = record.get("content_type", "")
            status = record.get("status")

            path_lower = urlsplit(url).path.lower()
            if path_lower.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot")):
                continue

            is_api = (
                "/api/" in url
                or "json" in content_type.lower()
                or record.get("source") in ("runtime", "fetch", "xhr")
                or record.get("initiatorType") in ("fetch", "xmlhttprequest")
                or url.endswith((".json", ".graphql"))
            )
            if not is_api:
                continue

            try:
                parts = urlsplit(url)
                path = parts.path
                host = parts.netloc
                base_endpoint = f"{parts.scheme}://{host}{path}"
                query_keys = [k for k, _ in parse_qsl(parts.query)]
            except Exception:
                continue

            key = (method, base_endpoint)
            if key not in endpoints:
                endpoints[key] = {
                    "method": method,
                    "endpoint": base_endpoint,
                    "host": host,
                    "path": path,
                    "query_params": set(query_keys),
                    "content_type": content_type,
                    "sample_status": status,
                    "observations": 0,
                }
            else:
                endpoints[key]["query_params"].update(query_keys)
                if status:
                    endpoints[key]["sample_status"] = status
                if content_type:
                    endpoints[key]["content_type"] = content_type

            endpoints[key]["observations"] += 1

        result = []
        for ep in endpoints.values():
            ep_copy = dict(ep)
            ep_copy["query_params"] = sorted(list(ep["query_params"]))
            result.append(ep_copy)
        result.sort(key=lambda x: (-x["observations"], x["endpoint"]))
        return result

    def add_rule(self, action: str, pattern: str, method: str = "*", **kwargs) -> dict:
        """Store a runtime rule and return redacted metadata, never its payload.

        Patterns retain host_fetch's literal-substring OR regex semantics; even
        literal-looking patterns must be valid regexes. Empty matches everything.
        Use ``re.escape`` for literal metacharacters. Rules are not applied by
        telemetry hooks. Header/mock payloads remain raw in the client policy
        store because the runtime needs them, not in telemetry query results.
        """
        if action not in {"block", "header", "mock"}:
            raise ValueError("action must be block, header, or mock")
        if not isinstance(pattern, str):
            raise TypeError("pattern must be a string")
        re.compile(pattern)
        if not isinstance(method, str) or not re.fullmatch(r"\*|[!#$%&'*+.^_`|~0-9A-Za-z-]+", method):
            raise ValueError("method must be an HTTP method or *")
        allowed = {"enabled"} | ({"headers"} if action == "header" else {"status", "content_type", "body"} if action == "mock" else set())
        if kwargs.keys() - allowed:
            raise ValueError("unsupported rule options")
        if "enabled" in kwargs and not isinstance(kwargs["enabled"], bool):
            raise ValueError("enabled must be boolean")
        if "headers" in kwargs:
            headers = kwargs["headers"]
            if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
                raise ValueError("headers must map strings to strings")
        if action == "mock":
            status = kwargs.get("status", 200)
            if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
                raise ValueError("status must be an integer from 100 to 599")
            if any(not isinstance(kwargs[key], str) for key in ("body", "content_type") if key in kwargs):
                raise ValueError("mock body and content_type must be strings")
        rules = getattr(self.client, "_dosh_network_rules", None)
        if rules is None:
            rules = []
            setattr(self.client, "_dosh_network_rules", rules)  # noqa: B010
        if not isinstance(rules, list):
            raise TypeError("client._dosh_network_rules must be a list")
        existing = {rule.get("id") for rule in rules if isinstance(rule, dict)}
        while True:
            self._rule_id += 1
            rule_id = f"ledger-{id(self):x}-{self._rule_id}"
            if rule_id not in existing:
                break
        rules.append({"id": rule_id, "action": action, "pattern": pattern, "method": method.upper(), **deepcopy(kwargs)})
        return {"id": rule_id, "action": action, "method": method.upper(), "scope": _RULE_SCOPE}

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by exact ID, preserving the shared list and other rules."""
        rules = getattr(self.client, "_dosh_network_rules", [])
        for index, rule in enumerate(rules):
            if isinstance(rule, dict) and rule.get("id") == rule_id:
                del rules[index]
                return True
        return False

    # ================== Phase 1 ledger API (Rust mirror) ==================

    def _merge_timing(self, request_id: int, patch: dict) -> None:
        """Merge measured timing patch into a record's ``timing`` dict."""
        record = self._records.get(request_id)
        if record is None:
            return
        timing = dict(record.get("timing") or {})
        timing.update(patch)
        record["timing"] = timing

    def _seal(self, request_id: int, request_body: bytes | None, response_body: bytes,
              content_type: str, true_size: int) -> None:
        """Store raw, unrealyzed transport evidence (required by `net.replay`,
        `net.decode`, and the reveal gate). Sealed bodies are never serialized
        into `query()` output; only the reveal gate can surface them."""
        record = self._records.get(request_id)
        self._sealed[request_id] = {
            "record_id": request_id,
            "url": (record or {}).get("url", ""),
            "method": (record or {}).get("method", " GET").replace(" GET", "GET"),
            "body": bytes(request_body or b"")[:SEALED_MAX_BYTES],
            "response_body": bytes(response_body)[:SEALED_MAX_BYTES],
            "len": true_size,
            "content_type": str(content_type)[:200],
        }

    def _append_with_sealed(self, record: dict, sealed: dict | None = None) -> int:
        """Record one runtime batch entry and optionally its sealed evidence."""
        rid = self._append(**record)
        if sealed:
            self._seal(
                rid,
                sealed.get("body", b""),
                sealed.get("response_body", b""),
                sealed.get("content_type", ""),
                sealed.get("len", len(sealed.get("response_body", b""))),
            )
        return rid

    # ---- N8: audited secret-reveal gate ----------------------------------

    def set_secret_reveal(self, allowed: bool) -> None:
        self._reveal_allowed = bool(allowed)
        self._audit_reveal(
            "gate_set", "sealed-reveal gate set to " + ("ON" if allowed else "off"),
        )

    def reveal_allowed(self) -> bool:
        return self._reveal_allowed

    def _audit_reveal(self, action: str, detail: str) -> None:
        self._reveal_audit.append(
            {"t": _epoch_ms(), "action": action, "detail": str(detail)[:512]}
        )

    def audit_reveal(self, action: str = "", detail: str = "") -> None:
        """Public audit entry: recorded transitions and authorized uses."""
        self._audit_reveal(action or "use", detail or "(undisclosed use)")

    def reveal_audit(self) -> list[dict]:
        """Audit-log entries for every reveal-gate transition and use."""
        return deepcopy(self._reveal_audit)

    def reveal_record(self, record_id: int) -> dict:
        """Return the sealed record's raw evidence, refusing unless the gate is
        ON. Every use is audited. Refusals are audited too (attempts leave a
        trace, honest gate behavior)."""
        if not self._reveal_allowed:
            self._audit_reveal("reveal_denied", f"record_id={record_id}")
            raise PermissionError(
                "sealed reveal refused: secret-reveal gate is off (default)"
            )
        sealed = self._sealed.get(record_id)
        if sealed is None:
            raise KeyError(f"no sealed transport evidence for record_id {record_id}")
        self._audit_reveal("revealed", f"record_id={record_id}")
        return deepcopy(sealed)

    # ---- N1: SSE stream drain --------------------------------------------

    def stream_view(self, record_id: int, after: int = 0) -> dict:
        record = self._records.get(record_id)
        if record is None:
            return {"error": f"no record {record_id} in the ledger"}
        events = record.get("stream_events") or []
        if not isinstance(events, list):
            return {"error": "stream_events is not a drained event list"}
        page = events[int(after) :]
        return {"record_id": record_id, "total": len(events), "returned": len(page),
                "events": deepcopy(page), "after": int(after)}

    # ---- N2: WebSocket session + frame evidence --------------------------

    def ws_open(self, url: str, **extra) -> int:
        if self._next_ws_id == 0:
            # Rebuild the counter from history so restarts do not collide.
            self._next_ws_id = max((s["id"] for s in self._ws_sessions), default=0)
        self._next_ws_id += 1
        session = {"id": self._next_ws_id, "t": _epoch_ms(), "url": _url(url),
                   "state": "open", "frame_count": 0, **extra}
        self._ws_sessions.append(session)
        return self._next_ws_id

    def ws_frame(self, session_id: int, direction: str, opcode: int, payload: bytes, real_len: int | None = None) -> None:
        text, payload_b64 = "", ""
        try:
            decode_ips = payload.decode("utf-8")
            if direction == "in" and len(decode_ips) <= WS_FRAME_RECORD_MAX:
                text = decode_ips
            else:
                payload_b64 = base64.b64encode(payload).decode("ascii")
        except UnicodeDecodeError:
            payload_b64 = base64.b64encode(payload).decode("ascii")
        if opcode == 2:
            payload_b64 = base64.b64encode(payload).decode("ascii")
        for fs in self._ws_sessions:
            if fs["id"] == session_id and fs["state"] == "open":
                fs["frame_count"] += 1
                break
        self._ws_frames.append(
            {"session": session_id, "t": _epoch_ms(), "direction": direction,
             "opcode": opcode, "len": real_len if real_len is not None else len(payload),
             "text": text, "payload_b64": payload_b64}
        )

    def ws_close(self, session_id: int, code: int) -> None:
        for fs in self._ws_sessions:
            if fs["id"] == session_id:
                fs["state"], fs["closed"] = "closed", True
                fs["close_code"] = code
                return

    def ws_sessions(self) -> list[dict]:
        return deepcopy(self._ws_sessions)

    def ws_view(self, session_id: int, after: int = 0) -> dict:
        frames = [f for f in self._ws_frames if f["session"] == session_id]
        session = next((s for s in self._ws_sessions if s["id"] == session_id), None)
        if session is None:
            return {"error": f"no websocket session {session_id}"}
        page = frames[int(after):]
        return {"session_id": session_id, "url": session["url"], "state": session["state"],
                "closed": session["state"] == "closed", "close_code": session.get("close_code"),
                "total": len(frames), "returned": len(page), "frames": deepcopy(page),
                "after": int(after)}

    # ---- N3: CORS verdict mirror ------------------------------------------------

    def cors_verdict(self, client_origin: str, method: str, target_url: str,
                     request_headers: dict, response_headers: dict) -> dict:
        """Classify the CORS verdict from recorded evidence (Rust cosmos.rs mirror).

        Same-origin requests short-circuit; non-simple requests/preflight
        headers are required to be handled by the server; wildcards ACEO/ACAO
        are rejected with credentials; exposed-header lists only matter with
        credentials. Verdict strings match the Rust lane's wording.
        """
        target = urlsplit(str(target_url or ""))
        target_origin = f"{target.scheme}://{target.netloc}" if target.scheme and target.netloc else ""
        if not client_origin:
            return {"verdict": "no-origin", "same_origin": False}
        if target_origin == client_origin:
            return {"verdict": "same-origin", "same_origin": True}
        method_up = str(method or "GET").upper()
        simple_methods = {"GET", "HEAD", "POST"}
        simple_cts = ("application/x-www-form-urlencoded", "application/json", "text/plain", "multipart/form-data")
        req_ct = str(request_headers.get("content-type", "")).split(";", 1)[0].strip().lower()
        forbidden = {"authorization", "cookie", "x-requested-with", "x-api-key"}
        custom = [k for k in request_headers if str(k).lower() not in forbidden]
        is_simple = method_up in simple_methods and (not req_ct or req_ct in simple_cts) and not custom
        acao = str(response_headers.get("access-control-allow-origin", ""))
        is_credential = bool(str(request_headers.get("cookie", "")) or str(request_headers.get("authorization", "")))
        if acao == "*" and is_credential:
            return {"verdict": "blocked-wildcard-with-credentials", "same_origin": False,
                    "acao": acao, "simple": is_simple}
        if not acao:
            return {"verdict": "blocked-no-acao", "same_origin": False, "simple": is_simple}
        if acao != "*" and acao != client_origin:
            return {"verdict": "blocked-origin-mismatch", "same_origin": False, "acao": acao}
        verdict = "ok" if is_simple else "ok-after-preflight"
        return {"verdict": verdict, "same_origin": False, "acao": acao,
                "simple_request": is_simple,
                "note": "preflight outcome is not recorded (transport does not retain OPTIONS trips through this ledger)"}

    def cors_view(self, client_origin: str, record_id: int) -> dict:
        record = self._records.get(record_id)
        if record is None:
            return {"error": f"no record {record_id} in the ledger"}
        status = record.get("status")
        verdict = self.cors_verdict(
            client_origin, record.get("method") or "GET", record.get("url") or "",
            record.get("request_headers") or {}, record.get("response_headers") or {},
        )
        return {"record_id": record_id, "status": status, **verdict}

    # ---- N4: measured timing waterfall -----------------------------------

    def timing_view(self, pattern: str = "") -> dict:
        """Measured TTFB/duration waterfall. dNS/connect phases are NOT
        observable through this ledger and are absent, not invented."""
        rows = []
        if pattern:
            rows = [dict(r) for r in self._records.values() if pattern in r.get("url", "")]
        else:
            rows = [dict(r) for r in self._records.values()]
        return {"matches": len(rows), "records": rows, "note": "dns/connect/tls phases are not observable; only ttfb and duration are measured"}

    # ---- N6: sealed-body decode view ------------------------------------

    def decode_view(self, record_id: int, encoding: str = "auto") -> dict:
        """Decode the sealed raw body of one record. Requires capture to have
        stored sealed bytes; refuses honestly otherwise."""
        sealed = self._sealed.get(record_id)
        if sealed is None:
            raise KeyError(
                f"no sealed transport evidence for record_id {record_id}; capture_bodies must be on"
            )
        out = decode_payload_bytes(sealed["response_body"], encoding)
        out["record_id"] = record_id
        return out
