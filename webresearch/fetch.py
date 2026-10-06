from __future__ import annotations

import contextlib
import inspect
import json
import os
from urllib.parse import urlparse

import httpx

from webresearch.types import FetchResult, Kind

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_BYTES = 8_000_000
DEFAULT_IMPERSONATE = os.environ.get("WR_IMPERSONATE", "chrome124")

HTML_TYPES = {"text/html", "application/xhtml+xml"}
XML_TYPES = {"text/xml", "application/xml", "application/rss+xml", "application/atom+xml"}
JSON_TYPES = {"application/json", "application/ld+json", "text/json"}

try:  # optional TLS impersonation
    from curl_cffi import requests as _curl_requests
except Exception:  # pragma: no cover - optional dependency
    _curl_requests = None


def browser_client_hints(ua: str = DEFAULT_UA, platform: str = "macOS") -> dict[str, str]:
    """Client hints consistent with a Chrome desktop User-Agent."""
    version = "128"
    for token in ua.split():
        if token.startswith("Chrome/"):
            version = token.split("/", 1)[1].split(".", 1)[0]
            break
    mobile = "?1" if "Mobile" in ua else "?0"
    return {
        "sec-ch-ua": f'"Chromium";v="{version}", "Not;A=Brand";v="24", "Google Chrome";v="{version}"',
        "sec-ch-ua-mobile": mobile,
        "sec-ch-ua-platform": f'"{platform}"',
        "upgrade-insecure-requests": "1",
    }


def navigate_headers(url: str) -> dict[str, str]:
    """Sec-Fetch-* headers for a top-level document load."""
    return {
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
    }


def classify(content_type: str) -> Kind:
    mime = content_type.split(";")[0].strip().lower()
    if mime in HTML_TYPES or mime.endswith("+html"):
        return "html"
    if mime in JSON_TYPES or mime.endswith("+json"):
        return "json"
    if mime in XML_TYPES or mime.endswith("+xml"):
        return "xml"
    if mime.startswith("text/"):
        return "text"
    return "other"


def client_kwargs(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> dict:
    merged = {
        "User-Agent": DEFAULT_UA,
        "Accept-Language": "en-US,en;q=0.9",
        **browser_client_hints(),
    }
    if headers:
        merged.update(headers)
    kwargs: dict = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": merged,
        "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    }
    try:  # HTTP/2 when the h2 package is present
        import h2  # noqa: F401

        kwargs["http2"] = True
    except Exception:
        pass
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def decode_body(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip("\"'")
            break
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def _build_result(
    *,
    url: str,
    final_url: str,
    status: int,
    content_type: str,
    headers: dict[str, str],
    raw: bytes,
    truncated: bool,
) -> FetchResult:
    kind = classify(content_type)
    body = decode_body(raw, content_type)
    if kind == "json":
        body = pretty_json(body)
    return FetchResult(
        url=url,
        final_url=final_url,
        status=status,
        content_type=content_type,
        headers={k.lower(): v for k, v in headers.items()},
        kind=kind,
        body=body,
        truncated=truncated,
    )


def make_client(
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
) -> httpx.AsyncClient:
    """Create a client tagged with its proxy so the impersonated lane can use it."""
    client = httpx.AsyncClient(**client_kwargs(timeout=timeout, headers=headers, proxy=proxy))
    client._wr_proxy = proxy
    return client


def _impersonated_session(
    client: httpx.AsyncClient,
    *,
    impersonate: str,
    timeout: float,
    headers: dict[str, str],
    proxy: str | None,
):
    """Reuse one impersonated session per client so cookies + TLS persist."""
    key = (impersonate, proxy)
    cached = getattr(client, "_wr_impersonated", None)
    if cached is not None and getattr(client, "_wr_impersonated_key", None) == key:
        return cached
    if cached is not None:
        client._wr_impersonated = None
        client._wr_impersonated_key = None
        with contextlib.suppress(Exception):
            res = cached.close()
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except Exception:
                    pass
    session = _curl_requests.AsyncSession(
        impersonate=impersonate,
        timeout=timeout,
        headers=headers,
        cookies={k: v for k, v in client.cookies.items()},
        proxy=proxy,
    )
    client._wr_impersonated = session
    client._wr_impersonated_key = key
    return session


async def _close_session(session) -> None:
    """Close a curl session. AsyncSession.close() returns a coroutine."""
    with contextlib.suppress(Exception):
        result = session.close()
        if inspect.isawaitable(result):
            await result


async def close_impersonated(client: httpx.AsyncClient) -> None:
    """Dispose the cached impersonated session attached to a client."""
    session = getattr(client, "_wr_impersonated", None)
    client._wr_impersonated = None
    client._wr_impersonated_key = None
    if session is not None:
        await _close_session(session)


async def _fetch_impersonated(
    url: str,
    *,
    client: httpx.AsyncClient,
    method: str,
    data: dict[str, str] | None,
    json_body: dict | None,
    headers: dict[str, str] | None,
    max_bytes: int,
    impersonate: str,
    timeout: float,
) -> FetchResult:
    merged = dict(client.headers)
    merged.update(navigate_headers(url))
    if headers:
        merged.update(headers)
    proxy = getattr(client, "_wr_proxy", None)
    session = _impersonated_session(
        client, impersonate=impersonate, timeout=timeout, headers=merged, proxy=proxy
    )
    response = await session.request(
        method.upper(),
        url,
        data=data,
        json=json_body,
        headers=merged,
        allow_redirects=True,
    )
    raw = response.content or b""
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
    for key, value in session.cookies.items():
        client.cookies.set(key, value)
    return _build_result(
        url=url,
        final_url=str(response.url),
        status=response.status_code,
        content_type=response.headers.get("content-type", ""),
        headers=dict(response.headers),
        raw=raw,
        truncated=truncated,
    )


async def _fetch_httpx(
    url: str,
    *,
    client: httpx.AsyncClient,
    method: str,
    data: dict[str, str] | None,
    json_body: dict | None,
    headers: dict[str, str] | None,
    max_bytes: int,
) -> FetchResult:
    req_headers = dict(navigate_headers(url))
    if headers:
        req_headers.update(headers)
    async with client.stream(
        method.upper(),
        url,
        data=data,
        json=json_body,
        headers=req_headers,
    ) as response:
        chunks: list[bytes] = []
        size = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                truncated = True
                leftover = max_bytes - (size - len(chunk))
                if leftover > 0:
                    chunks.append(chunk[:leftover])
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        return _build_result(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            content_type=response.headers.get("content-type", ""),
            headers=dict(response.headers),
            raw=raw,
            truncated=truncated,
        )


def _impersonation_allowed(client: httpx.AsyncClient) -> bool:
    """Skip impersonation for in-memory test transports."""
    transport = getattr(client, "_transport", None)
    if transport is None:
        return True
    name = type(transport).__name__
    return name not in {"MockTransport", "ASGITransport", "WSGITransport"}


async def fetch(
    url: str,
    *,
    client: httpx.AsyncClient,
    method: str = "GET",
    data: dict[str, str] | None = None,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    impersonate: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> FetchResult:
    """HTTP fetch with browser TLS impersonation when available.

    Keeps the httpx client as the cookie jar / identity so the rest of the
    engine can stay on one session, but performs the request through
    curl_cffi so JA3/JA4, HTTP/2 and header order match a real Chrome.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported url scheme: {parsed.scheme or 'missing'}")

    choice = DEFAULT_IMPERSONATE if impersonate is None else impersonate
    if choice and _curl_requests is not None and _impersonation_allowed(client):
        try:
            return await _fetch_impersonated(
                url,
                client=client,
                method=method,
                data=data,
                json_body=json_body,
                headers=headers,
                max_bytes=max_bytes,
                impersonate=choice,
                timeout=timeout,
            )
        except Exception:
            # Fall back to httpx rather than failing the whole navigation.
            pass
    return await _fetch_httpx(
        url,
        client=client,
        method=method,
        data=data,
        json_body=json_body,
        headers=headers,
        max_bytes=max_bytes,
    )