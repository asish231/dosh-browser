"""Safe-browsing style host denylist (exact + suffix match)."""

from __future__ import annotations

import os

from webresearch.types import FetchResult

_ENV = "WEBRESEARCH_BLOCKED_HOSTS"


def _denylist() -> set[str]:
    raw = os.environ.get(_ENV, "")
    out = set()
    for part in raw.replace("\n", ",").split(","):
        host = part.strip().lower().lstrip(".")
        if host:
            out.add(host)
    return out


def is_blocked_host(hostname: str) -> bool:
    host = (hostname or "").lower().strip(".")
    if not host:
        return False
    for blocked in _denylist():
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def blocked_result(url: str) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status=403,
        content_type="text/plain",
        kind="other",
        body="blocked by WEBRESEARCH_BLOCKED_HOSTS",
        headers={"x-webresearch-blocked": "1"},
    )
