"""Native subresource security checks, mirroring browser behavior."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlparse

_ALGOS = {"sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512}


def verify_integrity(body: str, integrity: str | None) -> bool:
    """Enforce the SRI `integrity` attribute. No integrity -> allowed."""
    if not integrity:
        return True
    data = body.encode("utf-8", errors="replace")
    for token in integrity.split():
        if "-" not in token:
            continue
        algo, _, expected = token.partition("-")
        factory = _ALGOS.get(algo.strip().lower())
        if factory is None or not expected:
            continue
        actual = base64.b64encode(factory(data).digest()).decode("ascii")
        if actual == expected.strip():
            return True
    return False


def mixed_content_blocked(page_url: str, resource_url: str) -> bool:
    page = urlparse(page_url)
    res = urlparse(resource_url)
    return page.scheme == "https" and res.scheme == "http"


def cookie_violates_policy(raw_set_cookie: str) -> bool:
    """Browser cookie prefixes + SameSite=None require Secure."""
    parts = [p.strip() for p in raw_set_cookie.split(";")]
    if not parts or "=" not in parts[0]:
        return False
    name, _, _ = parts[0].partition("=")
    name = name.strip()
    attrs = {p.split("=", 1)[0].strip().lower(): (p.split("=", 1)[1].strip() if "=" in p else "") for p in parts[1:]}
    secure = "secure" in attrs
    samesite = attrs.get("samesite", "").lower()
    if name.startswith("__Secure-") and not secure:
        return True
    if name.startswith("__Host-"):
        if not secure or attrs.get("path") != "/" or "domain" in attrs:
            return True
    if samesite == "none" and not secure:
        return True
    return False


def enforce_cookie_policy(client, page_url: str, set_cookies: list[str]) -> list[str]:
    rejected: list[str] = []
    for raw in set_cookies:
        if not cookie_violates_policy(raw):
            continue
        name = raw.split("=", 1)[0].strip()
        try:
            client.cookies.delete(name)
        except Exception:
            pass
        rejected.append(name)
    return rejected
