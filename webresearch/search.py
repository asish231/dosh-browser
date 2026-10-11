from __future__ import annotations

from typing import Literal
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from webresearch.fetch import fetch
from webresearch.parse import Document, parse
from webresearch.types import FetchResult, SearchHit, SearchResult

DDG_URL = "https://html.duckduckgo.com/html/"
GOOGLE_HTML = "https://www.google.com/search?q={q}&hl=en&gl=us&num=10"

Engine = Literal["google", "bing", "ddg", "yahoo", "lite", "auto", "brave", "bing-images", "ddg-images", "google-images"]

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


def _blocked_ddg_challenge(result: FetchResult) -> bool:
    """DuckDuckGo serves HTTP 200 with a botnet_challenge body instead of results."""
    body = result.body.lower()
    return "botnet_challenge" in body or ("challenge" in body and "proof" in body and "vqd" in body)


def _extract_vqd(html: str) -> str | None:
    import re

    m = re.search(r"""vqd\s*=\s*['"]([^'"]+)['"]""", html)
    if m:
        return m.group(1)
    m = re.search(r"vqd=([\d][\d\-]*)", html)
    return m.group(1) if m else None


def _jar_has(client: httpx.AsyncClient, name: str) -> bool:
    try:
        return any(c.name == name for c in client.cookies.jar)
    except Exception:
        return False


async def _warm_google(client: httpx.AsyncClient) -> bool:
    """Prime Google NID/AEC cookies. Returns True if the jar looks warmed."""
    try:
        await fetch("https://www.google.com/", client=client, headers={"Referer": "https://www.google.com/"})
    except Exception:
        return False
    return any(_jar_has(client, k) for k in ("NID", "AEC", "SOCS", "OTZ"))


async def _ddg_plain_get(url: str, *, client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    """Plain httpx GET to DDG (bypasses curl_cffi impersonation).

    curl_cffi's TLS fingerprint actually triggers DDG's botnet challenge for
    /i.js.  Plain httpx is less detectable in this specific case.
    """
    async with client.stream("GET", url, headers=headers, timeout=20) as resp:
        body = await resp.aread()
    return body.decode("utf-8", errors="replace")


async def _warm_ddg(client: httpx.AsyncClient, query: str) -> str | None:
    """Prime DuckDuckGo and harvest the per-query vqd token."""
    from urllib.parse import quote_plus

    try:
        body = await _ddg_plain_get(
            f"https://duckduckgo.com/?q={quote_plus(query)}",
            client=client,
            headers={"Referer": "https://duckduckgo.com/"},
        )
    except Exception:
        return None
    return _extract_vqd(body)


def _set_ddg_session_cookies(client: httpx.AsyncClient) -> None:
    """Synthesise the session cookies that DDG's JS would set via document.cookie.

    DDG's main page is a Next.js SPA that never sends Set-Cookie headers.
    Cookies like ``ddgr``, ``cfuid``, ``td``, ``k`` are set only when client-side
    JavaScript runs.  DDG's images API (``/i.js``) checks for their presence as a
    signal that the caller is a JS-enabled browser session.  Without them the
    endpoint returns ``botnet_challenge`` (HTTP 202) regardless of vqd validity.

    We generate realistic values — random hex / URL-safe base64 — because DDG
    only checks cookie *presence*, not cryptographic validity, for this check.
    """
    import secrets

    domain = ".duckduckgo.com"
    client.cookies.set("ddgr", secrets.token_hex(16), domain=domain, path="/")
    client.cookies.set("cfuid", secrets.token_urlsafe(48), domain=domain, path="/")
    client.cookies.set("td", secrets.token_hex(8), domain=domain, path="/")
    client.cookies.set("k", secrets.token_hex(16), domain=domain, path="/")


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


def continuation_prompt(query: str, tried: list[str], next_step: str) -> str:
    """Keep-working instruction surfaced inside blocked views. Never solves challenges."""
    tried_s = ", ".join(tried) if tried else "none"
    return (
        f"NEXT: {next_step} (tried: {tried_s}). "
        f"Do not retry the blocked engine immediately; back off, keep the warmed cookie jar, "
        f"and continue with the next engine or a direct site read for '{query}'."
    )


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


def _parse_bing(doc: Document, *, max_hits: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    if doc.tree is None:
        return hits
    cards = doc.tree.css("li.b_algo")
    seen_urls: set[str] = set()
    for c in cards:
        if len(hits) >= max_hits:
            break
        h2 = c.css_first("h2 a")
        if not h2:
            continue
        href = h2.attributes.get("href") or ""
        title = h2.text(separator=" ", strip=True)
        if not href or not title or href in seen_urls:
            continue
        seen_urls.add(href)
        p = c.css_first("div.b_caption p, p.b_lineclamp, p")
        snippet = p.text(separator=" ", strip=True) if p else ""
        hits.append(SearchHit(n=len(hits) + 1, title=title, url=href, snippet=snippet))
    return hits


async def _bing(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    url = f"https://www.bing.com/search?q={quote_plus(query)}&setlang=en"
    headers = {
        "Referer": "https://www.bing.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    result = await fetch(url, client=client, headers=headers)
    if result.status in {429, 403, 503}:
        return None, True
    hits = _parse_bing(parse(result), max_hits=max_hits)
    if not hits:
        return None, False
    return SearchResult(query=query, engine="bing", hits=hits, view=_view(query, "bing", hits)), False


def _parse_yahoo(doc: Document, *, max_hits: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    if doc.tree is None:
        return hits
    cards = doc.tree.css("div.algo-sr, div.algo")
    seen_urls: set[str] = set()
    for c in cards:
        if len(hits) >= max_hits:
            break
        a = c.css_first("h3 a, div.compTitle a, a.d-ib, a[href]")
        if not a:
            continue
        href = a.attributes.get("href") or ""
        title = a.text(separator=" ", strip=True)
        if not href or not title or href in seen_urls:
            continue
        seen_urls.add(href)
        p = c.css_first("div.compText, p")
        snippet = p.text(separator=" ", strip=True) if p else ""
        hits.append(SearchHit(n=len(hits) + 1, title=title, url=href, snippet=snippet))
    return hits


async def _yahoo(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
    headers = {"Referer": "https://search.yahoo.com/"}
    result = await fetch(url, client=client, headers=headers)
    if result.status in {429, 403, 503}:
        return None, True
    hits = _parse_yahoo(parse(result), max_hits=max_hits)
    if not hits:
        return None, False
    return SearchResult(query=query, engine="yahoo", hits=hits, view=_view(query, "yahoo", hits)), False


def _parse_ddg_lite(doc: Document, *, max_hits: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    if doc.tree is None:
        return hits
    links = doc.tree.css("a.result-link")
    snippets = doc.tree.css("td.result-snippet")
    for i, a in enumerate(links):
        if len(hits) >= max_hits:
            break
        href = _unwrap_ddg(a.attributes.get("href") or "")
        title = a.text(separator=" ", strip=True)
        if not href or not title:
            continue
        snippet = snippets[i].text(separator=" ", strip=True) if i < len(snippets) else ""
        hits.append(SearchHit(n=len(hits) + 1, title=title, url=href, snippet=snippet))
    return hits


async def _ddg_lite(query: str, *, client: httpx.AsyncClient, max_hits: int) -> SearchResult:
    result = await fetch(
        "https://lite.duckduckgo.com/lite/",
        client=client,
        method="POST",
        data={"q": query, "kl": "us-en"},
        headers={"Referer": "https://lite.duckduckgo.com/"},
    )
    hits = _parse_ddg_lite(parse(result), max_hits=max_hits)
    return SearchResult(query=query, engine="lite", hits=hits, view=_view(query, "lite", hits))


async def _ddg(query: str, *, client: httpx.AsyncClient, max_hits: int) -> SearchResult:
    result = await fetch(
        DDG_URL,
        client=client,
        method="POST",
        data={"q": query, "b": "", "kl": "us-en"},
        headers={"Referer": "https://html.duckduckgo.com/"},
    )
    if _blocked_ddg_challenge(result):
        # Legitimate dodge: fall back to the lite endpoint on the same warmed jar.
        lite = await _ddg_lite(query, client=client, max_hits=max_hits)
        if lite.hits:
            return lite.model_copy(update={"view": _view(query, "lite", lite.hits, note="ENGINE: lite (DDG html challenged; used lite on warmed jar). " + continuation_prompt(query, ["ddg"], "read lite hits or try bing"))})
        return SearchResult(query=query, engine="ddg", hits=[], view=_view(query, "ddg", [], note="DuckDuckGo served botnet_challenge; warmed vqd session required. " + continuation_prompt(query, ["ddg"], "use engine lite or bing")))
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


async def _google_images(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    """Second-stage Google Images lane. Reuses the warmed cookie jar; inspection only."""
    warmed = await _warm_google(client)
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us&tbm=isch"
    headers = {
        "Referer": "https://www.google.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }
    result = await fetch(url, client=client, headers=headers)
    if _blocked_google(result):
        note = f"Google Images blocked (status={result.status}, warmed={warmed}); IP rate-limited or captcha."
        return None, True
    hits = _parse_google(parse(result), max_hits=max_hits)
    if not hits:
        return None, False
    return SearchResult(query=query, engine="google-images", hits=hits, view=_view(query, "google-images", hits)), False


async def _ddg_images(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    """DuckDuckGo images via primed vqd token. Returns (result, blocked)."""
    from urllib.parse import quote_plus as _qp

    _set_ddg_session_cookies(client)
    vqd = await _warm_ddg(client, query)
    if not vqd:
        return None, True
    url = f"https://duckduckgo.com/i.js?q={_qp(query)}&vqd={_qp(vqd)}&o=json&l=us-en"
    try:
        raw = await _ddg_plain_get(
            url,
            client=client,
            headers={"Referer": "https://duckduckgo.com/", "Accept": "application/json"},
        )
    except Exception:
        return None, True

    # Quick botnet check
    low = raw.lower()
    if "botnet_challenge" in low or ("challenge" in low and "proof" in low):
        return None, True

    try:
        import json as _json

        payload = _json.loads(raw)
        hits: list[SearchHit] = []
        for item in (payload.get("results") or [])[:max_hits]:
            image = item.get("image") or ""
            title = item.get("title") or ""
            page = item.get("url") or ""
            if image and title:
                hits.append(SearchHit(n=len(hits) + 1, title=title, url=page or image, snippet=image))
        if hits:
            return SearchResult(query=query, engine="ddg-images", hits=hits, view=_view(query, "ddg-images", hits)), False
    except Exception:
        pass
    return None, False


async def search_images(
    query: str,
    *,
    client: httpx.AsyncClient,
    max_hits: int = 8,
    engine: str = "auto",
) -> SearchResult:
    """Image search waterfall: brave-html -> bing-images -> ddg-images -> google-images.

    Pure HTTP, no Chromium. Brave HTML is server-rendered and least challenged,
    so it leads for agentic use.
    """
    if engine in {"brave", "auto"}:
        brave = await _brave_images(query, client=client, max_hits=max_hits)
        if brave is not None:
            if engine != "auto":
                return brave
            if brave.hits:
                return brave
    if engine == "auto" or engine == "bing-images":
        bing = await _bing_images(query, client=client, max_hits=max_hits)
        if bing[0] is not None:
            return bing[0]
        if engine != "auto":
            return SearchResult(query=query, engine="bing-images", hits=[], view=_view(query, "bing-images", [], note="Bing Images blocked or empty."))
    if engine == "auto" or engine == "ddg-images":
        ddg, _ = await _ddg_images(query, client=client, max_hits=max_hits)
        if ddg is not None:
            return ddg
        if engine != "auto":
            return SearchResult(query=query, engine="ddg-images", hits=[], view=_view(query, "ddg-images", [], note="DuckDuckGo Images challenged (botnet_challenge); warmed vqd required. " + continuation_prompt(query, ["ddg-images"], "use engine brave or bing-images")))
    if engine == "auto" or engine == "google-images":
        goog, _ = await _google_images(query, client=client, max_hits=max_hits)
        if goog is not None:
            return goog
        if engine != "auto":
            return SearchResult(query=query, engine="google-images", hits=[], view=_view(query, "google-images", [], note="Google Images blocked (429/sorry/captcha). " + continuation_prompt(query, ["google-images"], "use engine brave or bing-images")))
    brave = await _brave_images(query, client=client, max_hits=max_hits)
    if brave is not None and brave.hits:
        return brave
    return SearchResult(query=query, engine="images", hits=[], view=_view(query, "images", [], note="All image engines blocked or empty. " + continuation_prompt(query, ["brave", "bing-images", "ddg-images", "google-images"], "read the target site directly")))


async def _brave_images(query: str, *, client: httpx.AsyncClient, max_hits: int) -> SearchResult | None:
    import base64 as _b64

    url = f"https://search.brave.com/images?q={quote_plus(query)}&source=web"
    result = await fetch(url, client=client, headers={"Referer": "https://search.brave.com/"})
    if result.status in {429, 403, 503}:
        return None
    doc = parse(result)
    if doc.tree is None:
        return None
    hits: list[SearchHit] = []
    for btn in doc.tree.css("button.image-result"):
        if len(hits) >= max_hits:
            break
        img = btn.css_first("div.image-wrapper img")
        if img is None:
            continue
        src = img.attributes.get("src") or ""
        if "rs:fit:32" in src or not src:
            continue
        title_el = btn.css_first("span.image-metadata-title")
        src_el = btn.css_first("div.image-metadata-site span")
        title = title_el.text(strip=True) if title_el else img.attributes.get("alt", "")
        source = src_el.text(strip=True) if src_el else ""
        orig = src
        if "/g:ce/" in src:
            tail = src.split("/g:ce/")[-1].replace("/", "")
            tail += "=" * (-len(tail) % 4)
            try:
                orig = _b64.b64decode(tail).decode("utf-8", errors="replace")
            except Exception:
                pass
        if title:
            label = f"{title} [{source}]" if source else title
            hits.append(SearchHit(n=len(hits) + 1, title=label, url=orig, snippet=src))
    return SearchResult(query=query, engine="brave-images", hits=hits, view=_view(query, "brave-images", hits))


async def _bing_images(query: str, *, client: httpx.AsyncClient, max_hits: int) -> tuple[SearchResult | None, bool]:
    import re as _re

    url = f"https://www.bing.com/images/search?q={quote_plus(query)}"
    result = await fetch(url, client=client, headers={"Referer": "https://www.bing.com/"})
    if result.status in {429, 403, 503}:
        return None, True
    urls = _re.findall(r'"murl":"(.*?)"', result.body)
    titles = _re.findall(r'"t":"(.*?)"', result.body)
    hits: list[SearchHit] = []
    for i, u in enumerate(urls[:max_hits]):
        title = titles[i] if i < len(titles) else u
        hits.append(SearchHit(n=len(hits) + 1, title=title, url=u, snippet=""))
    if hits:
        return SearchResult(query=query, engine="bing-images", hits=hits, view=_view(query, "bing-images", hits)), False
    return None, False


async def search(
    query: str,
    *,
    client: httpx.AsyncClient,
    max_hits: int = 8,
    engine: Engine = "auto",
) -> SearchResult:
    """Search via HTTP across multiple search engines with automatic fallback. Pure HTTP, no Chromium."""
    if engine == "ddg":
        return await _ddg(query, client=client, max_hits=max_hits)
    if engine == "lite":
        return await _ddg_lite(query, client=client, max_hits=max_hits)
    if engine == "bing":
        b_res, blocked = await _bing(query, client=client, max_hits=max_hits)
        if b_res is not None:
            return b_res
        return SearchResult(query=query, engine="bing", hits=[], view=_view(query, "bing", [], note="Bing returned no results or blocked."))
    if engine == "yahoo":
        y_res, blocked = await _yahoo(query, client=client, max_hits=max_hits)
        if y_res is not None:
            return y_res
        return SearchResult(query=query, engine="yahoo", hits=[], view=_view(query, "yahoo", [], note="Yahoo returned no results or blocked."))

    if engine == "google":
        html, blocked = await _google_http(query, client=client, max_hits=max_hits)
        if html is not None:
            return html
        note = "Google returned HTTP 429/403/503; IP rate-limited." if blocked else "Google blocked the request (captcha / 429)."
        return SearchResult(query=query, engine="google", hits=[], view=_view(query, "google", [], note=note + " " + continuation_prompt(query, ["google"], "use engine bing or ddg")))

    # engine == "auto": waterfall across Google -> Bing -> DDG -> Yahoo -> DDG Lite
    warmed = any(_jar_has(client, k) for k in ("NID", "AEC", "dcm"))
    g_res, g_blocked = await _google_http(query, client=client, max_hits=max_hits)
    if g_res is not None and g_res.hits:
        return g_res.model_copy(update={"view": _view(query, "google", g_res.hits, note=f"ENGINE: google, warmed={warmed}.")})

    b_res, b_blocked = await _bing(query, client=client, max_hits=max_hits)
    if b_res is not None and b_res.hits:
        return b_res.model_copy(update={"view": _view(query, "bing", b_res.hits, note=f"ENGINE: bing, warmed={warmed}; Google blocked/rate-limited.")})

    ddg_res = await _ddg(query, client=client, max_hits=max_hits)
    if ddg_res.hits:
        return ddg_res.model_copy(update={"view": _view(query, "ddg", ddg_res.hits, note="Google/Bing was blocked; used DuckDuckGo instead.")})

    y_res, y_blocked = await _yahoo(query, client=client, max_hits=max_hits)
    if y_res is not None and y_res.hits:
        return y_res.model_copy(update={"view": _view(query, "yahoo", y_res.hits, note="Used Yahoo search fallback.")})

    lite_res = await _ddg_lite(query, client=client, max_hits=max_hits)
    return lite_res.model_copy(update={"view": _view(query, "lite", lite_res.hits, note="Used DuckDuckGo Lite search fallback.")})

