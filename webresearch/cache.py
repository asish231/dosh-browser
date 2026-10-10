"""Native HTTP cache: freshness, revalidation, Vary. Mirrors RFC 9111 semantics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from email.utils import formatdate, parsedate_to_datetime
from typing import Any


MAX_ENTRIES = 200
MAX_BODY_BYTES = 2 * 1024 * 1024


def _parse_cache_control(value: str) -> dict[str, str | bool]:
    out: dict[str, str | bool] = {}
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip().lower()] = v.strip().strip('"')
        else:
            out[part.lower()] = True
    return out


def _http_date(value: str) -> float | None:
    try:
        return parsedate_to_datetime(value).timestamp()
    except Exception:
        return None


@dataclass
class CacheEntry:
    url: str
    status: int
    resp_headers: dict[str, str]
    body: bytes
    stored_at: float
    vary: dict[str, str] = field(default_factory=dict)

    def age(self, now: float) -> float:
        return max(0.0, now - self.stored_at)

    def freshness_lifetime(self) -> float | None:
        cc = _parse_cache_control(self.resp_headers.get("cache-control", ""))
        if "no-store" in cc or "no-cache" in cc:
            return 0.0
        if "max-age" in cc:
            try:
                return max(0.0, float(cc["max-age"]))
            except ValueError:
                pass
        if "expires" in self.resp_headers:
            exp = _http_date(self.resp_headers["expires"])
            date = _http_date(self.resp_headers.get("date", ""))
            if exp is not None:
                base = date if date is not None else self.stored_at
                return max(0.0, exp - base)
        if "last-modified" in self.resp_headers:
            lm = _http_date(self.resp_headers["last-modified"])
            date = _http_date(self.resp_headers.get("date", ""))
            if lm is not None:
                base = date if date is not None else self.stored_at
                if base > lm:
                    return (base - lm) * 0.1
        return None

    def is_fresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        lifetime = self.freshness_lifetime()
        if lifetime is None:
            return False
        return self.age(now) < lifetime

    def has_validator(self) -> bool:
        return "etag" in self.resp_headers or "last-modified" in self.resp_headers

    def conditional_headers(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if "etag" in self.resp_headers:
            out["if-none-match"] = self.resp_headers["etag"]
        elif "last-modified" in self.resp_headers:
            out["if-modified-since"] = self.resp_headers["last-modified"]
        return out


class HttpCache:
    """In-memory private cache keyed by (method, url, vary-values)."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: dict[str, list[CacheEntry]] = {}
        self.max_entries = max_entries

    def _vary_key(self, entry: CacheEntry, req_headers: dict[str, str]) -> bool:
        lowered = {k.lower(): v for k, v in req_headers.items()}
        for name, value in entry.vary.items():
            if lowered.get(name, "") != value:
                return False
        return True

    def lookup(self, method: str, url: str, req_headers: dict[str, str]) -> CacheEntry | None:
        if method.upper() != "GET":
            return None
        req_cc = _parse_cache_control(req_headers.get("cache-control", ""))
        if "no-store" in req_cc or "no-cache" in req_cc:
            return None
        for entry in self._entries.get(url, []):
            if self._vary_key(entry, req_headers):
                return entry
        return None

    def store(
        self,
        method: str,
        url: str,
        req_headers: dict[str, str],
        status: int,
        resp_headers: dict[str, str],
        body: bytes,
    ) -> bool:
        if method.upper() != "GET":
            return False
        if status not in {200, 203, 204, 300, 301, 308, 404, 405, 414, 501}:
            return False
        lowered_resp = {k.lower(): v for k, v in resp_headers.items()}
        cc = _parse_cache_control(lowered_resp.get("cache-control", ""))
        if "no-store" in cc:
            return False
        if len(body) > MAX_BODY_BYTES:
            return False
        vary_names = [v.strip().lower() for v in lowered_resp.get("vary", "").split(",") if v.strip()]
        if "*" in vary_names:
            return False
        lowered_req = {k.lower(): v for k, v in req_headers.items()}
        vary = {name: lowered_req.get(name, "") for name in vary_names}
        entry = CacheEntry(
            url=url,
            status=status,
            resp_headers=lowered_resp,
            body=body,
            stored_at=time.time(),
            vary=vary,
        )
        bucket = self._entries.setdefault(url, [])
        bucket[:] = [e for e in bucket if e.vary != vary]
        bucket.append(entry)
        total = sum(len(v) for v in self._entries.values())
        if total > self.max_entries:
            oldest_url = next(iter(self._entries))
            self._entries[oldest_url].pop(0)
            if not self._entries[oldest_url]:
                del self._entries[oldest_url]
        return True

    def update_on_304(self, entry: CacheEntry, resp_headers: dict[str, str]) -> None:
        lowered = {k.lower(): v for k, v in resp_headers.items()}
        for k, v in lowered.items():
            if k not in {"content-length", "content-encoding", "transfer-encoding"}:
                entry.resp_headers[k] = v
        entry.stored_at = time.time()

    def clear(self) -> None:
        self._entries.clear()


CACHE = HttpCache()


def cache_lookup(method: str, url: str, req_headers: dict[str, str]) -> CacheEntry | None:
    return CACHE.lookup(method, url, req_headers or {})


def cache_store(
    method: str,
    url: str,
    req_headers: dict[str, str],
    status: int,
    resp_headers: dict[str, Any],
    body: bytes,
) -> bool:
    return CACHE.store(method, url, req_headers or {}, status, dict(resp_headers or {}), body or b"")
