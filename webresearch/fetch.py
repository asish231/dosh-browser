from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from webresearch.types import FetchResult, Kind

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_BYTES = 2_000_000

HTML_TYPES = {"text/html", "application/xhtml+xml"}
XML_TYPES = {"text/xml", "application/xml", "application/rss+xml", "application/atom+xml"}
JSON_TYPES = {"application/json", "application/ld+json", "text/json"}


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
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        merged.update(headers)
    kwargs: dict = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": merged,
        "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
    }
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


async def fetch(
    url: str,
    *,
    client: httpx.AsyncClient,
    method: str = "GET",
    data: dict[str, str] | None = None,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FetchResult:
    """HTTP fetch. No browser. No JS. Returns the document bytes as text."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported url scheme: {parsed.scheme or 'missing'}")

    req_headers = headers or None
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
        content_type = response.headers.get("content-type", "")
        kind = classify(content_type)
        body = decode_body(raw, content_type)
        if kind == "json":
            body = pretty_json(body)

        return FetchResult(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            content_type=content_type,
            headers={k.lower(): v for k, v in response.headers.items()},
            kind=kind,
            body=body,
            truncated=truncated,
        )
