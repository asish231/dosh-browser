from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from webresearch.fetch import fetch
from webresearch.parse import Document, parse
from webresearch.types import FetchResult, SearchHit, SearchResult

DDG_URL = "https://html.duckduckgo.com/html/"
GOOGLE_HTML = "https://www.google.com/search?q={q}&hl=en&gl=us&num=10"

Engine = Literal["google", "ddg", "auto"]

SKIP_GOOGLE = (
    "google.com/search",
    "google.com/sorry",
    "google.com/url?",
    "accounts.google.",
    "support.google.com",
    "maps.google.",
    "policies.google.",
    "webcache.googleusercontent.",
    "/aclk?",
    "google.com/imgres",
    "google.com/maps",
    "google.com/preferences",
)


def _unwrap_ddg(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return url


def _unwrap_google(href: str, base: str = "https://www.google.com") -> str | None:
    if not href or href.startswith("#"):
        return None
    url = urljoin(base, href.strip())
    parsed = urlparse(url)
    if parsed.path == "/url":
        qs = parse_qs(parsed.query)
        target = qs.get("q") or qs.get("url")
        if target:
            url = unquote(target[0])
            parsed = urlparse(url)
    low = url.lower()
    if any(skip in low for skip in SKIP_GOOGLE):
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if "google." in parsed.netloc and parsed.path in {"", "/"}:
        return None
    return url


def _blocked_google(result: FetchResult) -> bool:
    url = (result.final_url or result.url).lower()
    body = result.body.lower()
    if result.status in {429, 403, 503}:
        return True
    if "/sorry" in url or "unusual traffic" in body or "detected unusual" in body:
        return True
    if "our systems have detected" in body or "recaptcha" in body:
        return True
    if "enablejs" in body and "display:none" in body:
        return True
    return False


def _parse_google(doc: Document, *, max_hits: int) -> list[SearchHit]:
    if doc.tree is None:
        return []
    hits: list[SearchHit] = []
    seen: set[str] = set()

    cards = doc.tree.css("div.g, div.Gx5Zad, div.ezO2md, div[data-sokoban-container]")
    if not cards:
        cards = doc.tree.css("#search a, #ires a, a")

    for node in cards:
        a = node if node.tag == "a" else node.css_first("a[href]")
        if a is None:
            continue
        href = _unwrap_google(a.attributes.get("href") or "", doc.url)
        if not href or href in seen or "#:~:text=" in href:
            continue
        h3 = node.css_first("h3") if node.tag != "a" else a.css_first("h3")
        title = (h3.text(separator=" ", strip=True) if h3 else a.text(separator=" ", strip=True)).strip()
        if not title or len(title) < 3 or title.lower() in {"read more", "more", "cached"}:
            continue
        snippet = ""
        sn = (
            node.css_first("div.VwiC3b")
            or node.css_first("span.aCOpRe")
            or node.css_first("div.s")
            or node.css_first("div.FrI1Sb")
            or node.css_first("span.st")
        )
        if sn is not None:
            snippet = sn.text(separator=" ", strip=True)
        seen.add(href)
        hits.append(SearchHit(n=len(hits) + 1, title=title, url=href, snippet=snippet))
        if len(hits) >= max_hits:
            break
    return hits


def _parse_ddg(doc: Document, *, max_hits: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    if doc.tree is None:
        return hits
    nodes = doc.tree.css("a.result__a") or doc.tree.css(".result__title a")
    snippets = doc.tree.css("a.result__snippet") or doc.tree.css(".result__snippet")
    for i, a in enumerate(nodes):
        if i >= max_hits:
            break
        href = _unwrap_ddg(a.attributes.get("href") or "")
        if not href or "duckduckgo.com/y.js" in href or "ad_provider=" in href:
            continue
        title = a.text(separator=" ", strip=True)
        snippet = snippets[i].text(separator=" ", strip=True) if i < len(snippets) else ""
        if href and title:
            hits.append(SearchHit(n=len(hits) + 1, title=title, url=href, snippet=snippet))
    return hits


def _view(query: str, engine: str, hits: list[SearchHit], note: str = "") -> str:
    lines = [f"SEARCH: {query}", f"ENGINE: {engine}", ""]
    if note:
        lines.append(note)
        lines.append("")
    if not hits:
        lines.append("No results (search page may have blocked the request).")
    for hit in hits:
        lines.append(f"[{hit.n}] {hit.title}")
        lines.append(f"    {hit.url}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
        lines.append("")
    return "\n".join(lines).strip()


async def _ddg(query: str, *, client: httpx.AsyncClient, max_hits: int) -> SearchResult:
    result = await fetch(
        DDG_URL,
        client=client,
        method="POST",
        data={"q": query, "b": "", "kl": "us-en"},
        headers={"Referer": "https://html.duckduckgo.com/"},
    )
    hits = _parse_ddg(parse(result), max_hits=max_hits)
    return SearchResult(query=query, engine="ddg", hits=hits, view=_view(query, "ddg", hits))


async def _google_http(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    url = GOOGLE_HTML.format(q=quote_plus(query))
    headers = {
        "Referer": "https://www.google.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }
    result = await fetch(url, client=client, headers=headers)
    if _blocked_google(result):
        return None, True
    hits = _parse_google(parse(result), max_hits=max_hits)
    if not hits:
        return None, False
    return SearchResult(query=query, engine="google", hits=hits, view=_view(query, "google", hits)), False


async def search(
    query: str,
    *,
    client: httpx.AsyncClient,
    max_hits: int = 8,
    engine: Engine = "auto",
) -> SearchResult:
    """Search via HTTP. Google first, DuckDuckGo if Google blocks. No Chromium."""
    if engine == "ddg":
        return await _ddg(query, client=client, max_hits=max_hits)

    if engine in {"google", "auto"}:
        html, blocked = await _google_http(query, client=client, max_hits=max_hits)
        if html is not None:
            return html
        if blocked and engine == "google":
            return SearchResult(
                query=query,
                engine="google",
                hits=[],
                view=_view(query, "google", [], note="Google returned HTTP 429/403/503; IP rate-limited."),
            )
        if engine == "google":
            return SearchResult(
                query=query,
                engine="google",
                hits=[],
                view=_view(query, "google", [], note="Google blocked the request (captcha / 429)."),
            )

    ddg = await _ddg(query, client=client, max_hits=max_hits)
    note = "Google was blocked; used DuckDuckGo instead."
    return ddg.model_copy(update={"view": _view(query, "ddg", ddg.hits, note=note), "engine": "ddg"})
