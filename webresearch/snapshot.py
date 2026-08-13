from __future__ import annotations

from selectolax.parser import Node

from webresearch.content import content_root, inside_chrome
from webresearch.forms import forms as extract_forms
from webresearch.links import links as extract_links
from webresearch.parse import Document, meta_from
from webresearch.types import Page

CONTENT_SEL = (
    "h1, h2, h3, h4, h5, h6, p, li, pre, blockquote, "
    ".quote, .text, figcaption, [itemprop='reviewBody'], [itemprop='description']"
)


def _text(node: Node) -> str:
    return " ".join(node.text(separator=" ", strip=True).split())


def _html_view(doc: Document, max_chars: int) -> tuple[str, list[str]]:
    assert doc.tree is not None
    root = content_root(doc.tree)
    lines: list[str] = []
    headings: list[str] = []
    if root is None:
        return "", headings

    for node in root.css(CONTENT_SEL):
        if inside_chrome(node, stop=root):
            continue
        tag = node.tag or ""
        if tag == "pre":
            code = node.text(separator="\n", strip=False).strip("\n")
            if code.strip():
                lines.append(f"```\n{code}\n```")
            continue
        text = _text(node)
        if not text:
            continue
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            lines.append(f"{'#' * int(tag[1])} {text}")
            headings.append(text)
        elif tag == "li":
            lines.append(f"- {text}")
        elif tag == "blockquote":
            lines.append(f"> {text}")
        else:
            lines.append(text)

    compact: list[str] = []
    for line in lines:
        if compact and compact[-1] == line:
            continue
        compact.append(line)
    if len("\n\n".join(compact)) < 280:
        for node in root.css("div, span, article, section"):
            if inside_chrome(node, stop=root):
                continue
            text = _text(node)
            if 50 <= len(text) <= 600 and text not in compact:
                compact.append(text)
            if len("\n\n".join(compact)) > max_chars:
                break
    view = "\n\n".join(compact)
    if len(view) > max_chars:
        view = view[: max_chars - 20].rstrip() + "\n\n[truncated]"
    return view, headings


def _spa_hint(doc: Document, view: str) -> bool:
    scripts = doc.body.lower().count("<script")
    return len(view) < 240 and scripts >= 5


def snapshot(
    doc: Document,
    *,
    max_chars: int = 8000,
    max_links: int = 50,
) -> Page:
    """Compact page model for an LLM: text + numbered links + forms."""
    meta = meta_from(doc)
    page_links = extract_links(doc, max_links=max_links)
    page_forms = extract_forms(doc)

    if doc.kind in {"html", "xml"} and doc.tree is not None:
        view, headings = _html_view(doc, max_chars)
    else:
        view = doc.body[:max_chars]
        headings = []

    spa = _spa_hint(doc, view) if doc.kind == "html" else False

    header = [
        f"TITLE: {meta.title or '(untitled)'}",
        f"URL: {doc.url}",
        f"STATUS: {doc.status}",
    ]
    if spa:
        header.append("NOTE: Page looks script-heavy; visible HTML may be incomplete.")
    if doc.truncated:
        header.append("NOTE: Response was truncated at size limit.")

    parts = ["\n".join(header), view.strip()]
    if page_links:
        link_lines = ["Links:"]
        for link in page_links:
            label = link.text.replace("\n", " ").strip()[:80]
            link_lines.append(f"[{link.n}] {label} → {link.url}")
        parts.append("\n".join(link_lines))
    if page_forms:
        form_lines = ["Forms:"]
        for form in page_forms:
            form_lines.append(f"[{form.n}] {form.method} {form.action}")
            for field in form.fields:
                if field.type == "hidden":
                    continue
                req = " required" if field.required else ""
                opts = f" options={field.options[:8]}" if field.options else ""
                form_lines.append(f"    {field.name}: {field.type}{req}{opts}")
        parts.append("\n".join(form_lines))

    full_view = "\n\n".join(p for p in parts if p).strip()
    return Page(
        url=doc.url,
        status=doc.status,
        content_type=doc.content_type,
        kind=doc.kind,
        title=meta.title,
        view=full_view,
        links=page_links,
        forms=page_forms,
        headings=headings[:30],
        meta=meta,
        spa_hint=spa,
        chars=len(full_view),
    )
