from __future__ import annotations

from dataclasses import dataclass

from selectolax.parser import HTMLParser

from webresearch.types import FetchResult, Kind, Meta

NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "template",
}


@dataclass
class Document:
    url: str
    status: int
    kind: Kind
    content_type: str
    body: str
    tree: HTMLParser | None
    truncated: bool = False


def parse(result: FetchResult) -> Document:
    """Turn a fetch result into a DOM (HTML/XML) or a raw document."""
    tree = None
    if result.kind in {"html", "xml"}:
        tree = HTMLParser(result.body)
        for tag in NOISE_TAGS:
            for node in tree.css(tag):
                node.decompose()
    return Document(
        url=result.final_url or result.url,
        status=result.status,
        kind=result.kind,
        content_type=result.content_type,
        body=result.body,
        tree=tree,
        truncated=result.truncated,
    )


def meta_from(doc: Document) -> Meta:
    if doc.tree is None:
        return Meta(title=_guess_title(doc))
    tree = doc.tree
    title = ""
    node = tree.css_first("title")
    if node:
        title = node.text(strip=True)
    if not title:
        h1 = tree.css_first("h1")
        title = h1.text(strip=True) if h1 else ""

    extra: dict[str, str] = {}
    description = ""
    language = ""
    canonical = ""

    html = tree.css_first("html")
    if html:
        language = html.attributes.get("lang", "") or ""

    for tag in tree.css("meta"):
        attrs = tag.attributes
        key = (attrs.get("name") or attrs.get("property") or "").lower()
        val = attrs.get("content") or ""
        if not key or not val:
            continue
        if key in {"description", "og:description", "twitter:description"} and not description:
            description = val
        elif key in {"og:title", "twitter:title"} and not title:
            title = val
        else:
            extra[key] = val

    link = tree.css_first("link[rel='canonical']")
    if link:
        canonical = link.attributes.get("href") or ""

    return Meta(
        title=title,
        description=description,
        language=language,
        canonical=canonical,
        extra=extra,
    )


def _guess_title(doc: Document) -> str:
    if doc.kind == "json":
        return doc.url.rsplit("/", 1)[-1] or "json"
    first = doc.body.strip().splitlines()[0] if doc.body.strip() else ""
    return first[:80]
