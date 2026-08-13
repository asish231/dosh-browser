from __future__ import annotations

from urllib.parse import urljoin, urlparse

from webresearch.content import content_root, inside_chrome
from webresearch.parse import Document
from webresearch.types import Link

SKIP_SCHEMES = {"javascript", "mailto", "tel", "data"}


def _abs(base: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith("#"):
        return None
    url = urljoin(base, href)
    parsed = urlparse(url)
    if parsed.scheme.lower() in SKIP_SCHEMES:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return url


def links(doc: Document, *, max_links: int = 50) -> list[Link]:
    """Extract unique http(s) links in document order."""
    if doc.tree is None:
        return []
    root = content_root(doc.tree) or doc.tree
    seen: dict[str, str] = {}
    ordered: list[str] = []
    for a in root.css("a[href]"):
        if inside_chrome(a, stop=root):
            continue
        href = a.attributes.get("href") or ""
        if href.startswith("#cite") or "cite_note" in href or "cite_ref" in href:
            continue
        url = _abs(doc.url, href)
        if not url:
            continue
        text = a.text(separator=" ", strip=True)
        if url not in seen:
            seen[url] = text
            ordered.append(url)
        elif text and len(text) > len(seen[url]):
            seen[url] = text
        if len(ordered) >= max_links:
            break
    return [Link(n=i, text=seen[url] or url, url=url) for i, url in enumerate(ordered, start=1)]
