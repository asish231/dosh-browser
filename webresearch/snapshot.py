from __future__ import annotations

import re

from selectolax.lexbor import LexborNode as Node

from webresearch.content import content_root, inside_chrome
from webresearch.forms import forms as extract_forms
from webresearch.links import count_all_links, links as extract_links
from webresearch.parse import Document, meta_from
from webresearch.types import Page

CONTENT_SEL = (
    "h1, h2, h3, h4, h5, h6, p, li, pre, blockquote, table, "
    ".quote, .text, figcaption, [itemprop='reviewBody'], [itemprop='description']"
)


def _text(node: Node) -> str:
    return " ".join(node.text(separator=" ", strip=True).split())


def _cell_text(node: Node) -> str:
    return " ".join(node.text(separator=" ", strip=True).split()).replace("|", "\\|")


def _table_md(table: Node) -> str:
    rows: list[list[str]] = []
    header: list[str] = []
    for tr in table.css("tr"):
        cells = tr.css("th, td")
        if not cells:
            continue
        values = [_cell_text(cell) for cell in cells]
        if not any(values):
            continue
        if not header and (tr.css_first("th") is not None or not rows):
            header = values
        else:
            rows.append(values)
    if not header and not rows:
        return ""
    width = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if width == 0:
        return ""

    def norm(row: list[str]) -> list[str]:
        return (row + [""] * width)[:width]

    lines: list[str] = []
    if header:
        lines.append("| " + " | ".join(norm(header)) + " |")
        lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows:
        lines.append("| " + " | ".join(norm(row)) + " |")
    return "\n".join(lines)


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
        if tag == "table":
            if node.css_first("table") is not None and node.parent is not None and node.parent.tag == "table":
                continue
            md = _table_md(node)
            if md:
                lines.append(md)
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
    dense = len("\n\n".join(compact)) < 280
    if dense or len("\n\n".join(compact)) < 700:
        seen_text = set(compact)
        min_len = 2 if dense else 40
        for node in root.css("div, span, article, section, ul, ol, dl, figure"):
            if inside_chrome(node, stop=root):
                continue
            if node.css_first("p, li, h1, h2, h3, table, blockquote") is not None:
                continue
            text = _text(node)
            if min_len <= len(text) <= 800 and text not in seen_text:
                seen_text.add(text)
                compact.append(text)
            if len("\n\n".join(compact)) > max_chars:
                break
    view = "\n\n".join(compact)
    if len(view) > max_chars:
        view = view[: max_chars - 20].rstrip() + "\n\n[truncated]"
    return view, headings


_SCRIPT_BODY_RE = re.compile(r"(?is)<script\b[^>]*>(.*?)</script\s*>")
_SPA_MOUNT_SELECTORS = ("#app", "#root", "#__next", "#__nuxt", "[data-reactroot]")


def _spa_hint(doc: Document, view: str) -> bool:
    """True when the page is likely client-rendered rather than server-rendered.

    A short view is necessary but not sufficient: a short *article* is still an
    article. The extra signals are that script mass dominates the response and
    the body carries an empty mount point, which is what a SPA shell looks
    like. Wikipedia carries five inline scripts and a 15 KB article, and was
    previously labelled script-heavy purely because its article had been
    dropped by the chrome filter upstream — a bug surfaced as a note.
    """
    body = doc.body.lower()
    if body.count("<script") < 5:
        return False
    script_bytes = sum(len(m.group(1)) for m in _SCRIPT_BODY_RE.finditer(doc.body))
    if script_bytes < 2000:
        return False
    view_len = len(view.strip())
    if view_len > 4000:
        return False
    tree = doc.tree
    if tree is None:
        return False
    for selector in _SPA_MOUNT_SELECTORS:
        mount = tree.css_first(selector)
        if mount is not None and not " ".join(mount.text(strip=True).split()):
            return True
    return script_bytes > view_len * 3


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
        # Say plainly when the list is a subset. An agent that sees 47 links and
        # assumes that is all of them will conclude the page has no other exits.
        total_links = count_all_links(doc)
        if total_links > len(page_links):
            link_lines.append(
                f"[{len(page_links) + 1}] ... {total_links - len(page_links)} more link(s) "
                "not shown; use browser_inspect or the action surface to reach them"
            )
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
        final_url=doc.url,
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
