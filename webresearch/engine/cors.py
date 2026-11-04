"""cors.py — W3C Cross-Origin Resource Sharing (CORS) Gate and Preflight Store.

Enforces CORS rules for subresource fetches, script XHR/fetch calls, and iframes:
- Identifies simple vs preflight-required requests
- Maintains an in-memory cache of preflight OPTIONS grants (CorsStore)
- Validates response headers (Access-Control-Allow-Origin, Access-Control-Allow-Credentials)
"""

from __future__ import annotations

import time
from urllib.parse import urlparse


class CorsError(Exception):
    """Raised on CORS policy violations."""


def get_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


CORS_UNSAFE_BYTES = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    ord('"'), ord('('), ord(')'), ord(':'), ord('<'), ord('>'), ord('?'), ord('@'),
    ord('['), ord('\\'), ord(']'), ord('{'), ord('}'), 127
}

SAFELISTED_LANG_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz *,-.;=")


def is_cors_unsafe_byte(b: int) -> bool:
    return b in CORS_UNSAFE_BYTES


def has_no_cors_unsafe_bytes(value: str) -> bool:
    return not any(b in CORS_UNSAFE_BYTES for b in value.encode("latin-1", errors="replace"))


def is_safelisted_language_value(value: str) -> bool:
    return all(c in SAFELISTED_LANG_CHARS for c in value)


def is_safelisted_content_type(value: str) -> bool:
    mime = value.split(";", 1)[0].strip().lower()
    return mime in {
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
    } and has_no_cors_unsafe_bytes(value)


def is_safelisted_header(name: str, value: str) -> bool:
    if len(value) > 128:
        return False
    lk = name.lower()
    if lk == "accept":
        return has_no_cors_unsafe_bytes(value)
    elif lk in {"accept-language", "content-language"}:
        return is_safelisted_language_value(value)
    elif lk == "content-type":
        return is_safelisted_content_type(value)
    return False


def is_simple_request(method: str, headers: dict[str, str]) -> bool:
    upper = method.upper()
    if upper not in {"GET", "HEAD", "POST"}:
        return False

    for k, v in headers.items():
        lk = k.lower()
        if lk in {"user-agent", "host", "origin", "referer"} or lk.startswith("sec-fetch-") or lk.startswith("sec-ch-ua"):
            continue
        if not is_safelisted_header(lk, v):
            return False

    return True


class PreflightEntry:
    def __init__(
        self,
        allowed_methods: set[str],
        allowed_headers: set[str],
        allow_credentials: bool,
        max_age_secs: float,
    ) -> None:
        self.allowed_methods = allowed_methods
        self.allowed_headers = allowed_headers
        self.allow_credentials = allow_credentials
        self.expires_at = time.time() + min(max_age_secs, 7200.0)

    def is_fresh(self) -> bool:
        return time.time() < self.expires_at


class CorsStore:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], PreflightEntry] = {}

    def record_preflight(
        self,
        client_origin: str,
        target_origin: str,
        headers: dict[str, str],
    ) -> None:
        raw_age = headers.get("access-control-max-age", "5")
        try:
            max_age = float(raw_age)
        except ValueError:
            max_age = 5.0

        methods_hdr = headers.get("access-control-allow-methods", "")
        allowed_methods = {m.strip().upper() for m in methods_hdr.split(",") if m.strip()}

        headers_hdr = headers.get("access-control-allow-headers", "")
        allowed_headers = {h.strip().lower() for h in headers_hdr.split(",") if h.strip()}

        allow_cred = headers.get("access-control-allow-credentials", "").lower() == "true"

        entry = PreflightEntry(allowed_methods, allowed_headers, allow_cred, max_age)
        self._entries[(client_origin.rstrip("/"), target_origin.rstrip("/"))] = entry

    def get(self, client_origin: str, target_origin: str) -> PreflightEntry | None:
        key = (client_origin.rstrip("/"), target_origin.rstrip("/"))
        entry = self._entries.get(key)
        if entry and entry.is_fresh():
            return entry
        elif entry:
            self._entries.pop(key, None)
        return None


class CorsGate:
    def __init__(self) -> None:
        self.store = CorsStore()

    @staticmethod
    def is_same_origin(client_origin: str, target_url: str) -> bool:
        return client_origin.rstrip("/").lower() == get_origin(target_url).lower()

    def evaluate_request(
        self,
        client_origin: str,
        target_url: str,
        method: str,
        headers: dict[str, str],
    ) -> str:
        """Returns: 'same_origin' | 'simple' | 'preflight_cached' | 'preflight_required'."""
        if self.is_same_origin(client_origin, target_url):
            return "same_origin"

        target_origin = get_origin(target_url)
        upper = method.upper()

        preflight = self.store.get(client_origin, target_origin)
        if preflight and ("*" in preflight.allowed_methods or upper in preflight.allowed_methods):
            return "preflight_cached"

        if is_simple_request(upper, headers):
            return "simple"

        return "preflight_required"

    @staticmethod
    def validate_response(
        client_origin: str,
        with_credentials: bool,
        response_headers: dict[str, str],
    ) -> None:
        # Case-insensitive header lookup
        allow_origin = None
        for k, v in response_headers.items():
            if k.lower() == "access-control-allow-origin":
                allow_origin = v.strip()
                break

        if not allow_origin:
            raise CorsError("CORS error: Missing Access-Control-Allow-Origin header")

        if allow_origin == "*":
            if with_credentials:
                raise CorsError(
                    "CORS error: Cannot use wildcard '*' Access-Control-Allow-Origin with credentials"
                )
            return

        if allow_origin.rstrip("/").lower() != client_origin.rstrip("/").lower():
            raise CorsError(
                f"CORS error: Origin mismatch (expected {client_origin}, got {allow_origin})"
            )

