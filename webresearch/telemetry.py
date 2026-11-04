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

import json
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

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

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.size += len(chunk)
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
            if self.ledger.capture_bodies:
                if self.encoded:
                    updates["body_state"] = "omitted_encoded_stream"
                elif self.size > self.ledger.body_max_bytes:
                    updates["body_state"] = "omitted_over_cap"
                else:
                    updates.update(self.ledger._body(bytes(self.buffer), self.content_type))
            self.ledger._update(self.request_id, **updates)
        finally:
            self.buffer.clear()

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
        request.extensions[self._extension] = request_id

    async def _on_response(self, response: httpx.Response) -> None:
        request_id = response.request.extensions.get(self._extension)
        if request_id not in self._records:
            return
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
            if self.capture_bodies:
                body = item.get("response_body")
                if body is None and mocked:
                    body = item.get("body")
                if isinstance(body, str):
                    content_type = str(item.get("content_type") or headers.get("content-type", ""))
                    record.update(self._body(body.encode("utf-8"), content_type))
            self._append(**record)
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
