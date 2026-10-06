"""Chromium lane is strictly disabled.

webresearch is an independent from-scratch browser engine built with HTTP fetch
and the QuickJS mini-DOM. External Chromium / Playwright engines are strictly
disallowed.
"""

from __future__ import annotations

import contextlib
from typing import Any

WAF_CHALLENGE_MARKERS: tuple[str, ...] = ()


def available() -> bool:
    """Chromium is strictly disabled. All browsing runs on the native QuickJS engine."""
    return False


def should_escalate(html: str, *, js_info: dict | None = None) -> bool:
    """Never escalate to external browser engines."""
    return False


async def render(*args: Any, **kwargs: Any) -> None:
    """No-op render: Chromium is disabled."""
    return None


def sync_cookies_to_client(cookies: list[dict], client: Any) -> None:
    """Sync cookie dicts into an httpx client cookie jar."""
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain", "").lstrip(".")
        path = c.get("path", "/")
        if name and value:
            with contextlib.suppress(Exception):
                client.cookies.set(name, value, domain=domain or None, path=path)


def extract_cookies_for_chromium(client: Any) -> list[dict]:
    """Extract cookies from an httpx client into list of dicts."""
    out: list[dict] = []
    if hasattr(client, "cookies"):
        for cookie in client.cookies.jar:
            out.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            })
    return out


class ChromiumSession:
    """Disabled stub: external Chromium processes are strictly disallowed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "Chromium usage is strictly disallowed in dosh-browser. "
            "All navigation and JS execution runs in the from-scratch QuickJS engine."
        )