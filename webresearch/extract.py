from __future__ import annotations

from typing import Any

from webresearch.content import content_root
from webresearch.parse import Document, meta_from
from webresearch.types import ExtractResult

BUILTIN = {"title", "description", "url", "headings", "text"}


def extract(doc: Document, fields: dict[str, str]) -> ExtractResult:
    """Pull fields by CSS selector, or builtins: title, description, url, headings, text."""
    out: dict[str, Any] = {}
    meta = meta_from(doc)
    for name, spec in fields.items():
        key = spec.strip()
        if key in BUILTIN or key == "auto":
            out[name] = _builtin(doc, meta, name if key == "auto" else key)
            continue
        if doc.tree is None:
            out[name] = None
            continue
        nodes = doc.tree.css(key)
        values = [_clean(n.text(separator=" ", strip=True)) for n in nodes]
        values = [v for v in values if v]
        if not values:
            out[name] = None
        elif len(values) == 1:
            out[name] = values[0]
        else:
            out[name] = values
    return ExtractResult(url=doc.url, fields=out)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _builtin(doc: Document, meta, key: str) -> Any:
    if key == "title":
        return meta.title
    if key == "description":
        return meta.description
    if key == "url":
        return doc.url
    if key == "headings" and doc.tree is not None:
        root = content_root(doc.tree) or doc.tree
        return [_clean(n.text(strip=True)) for n in root.css("h1, h2, h3") if n.text(strip=True)]
    if key == "text":
        if doc.tree is not None:
            root = content_root(doc.tree) or doc.tree.css_first("body") or doc.tree.root
            return _clean(root.text(separator=" ", strip=True))[:4000]
        return doc.body[:4000]
    return None
