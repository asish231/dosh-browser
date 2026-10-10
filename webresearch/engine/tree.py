"""HTML ↔ JSON tree for the JS runtime. No browser engine involved."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"t": "document", "a": {}, "c": []}
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = {"t": tag.lower(), "a": {k: (v or "") for k, v in attrs}, "c": []}
        self.stack[-1]["c"].append(node)
        if tag.lower() not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["t"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        self.stack[-1]["c"].append({"t": "#text", "x": data})


def html_to_tree(html: str) -> dict[str, Any]:
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def tree_to_html(node: dict[str, Any], parent_tag: str = "") -> str:
    tag = node.get("t") or ""
    if tag == "#text":
        if parent_tag in ("script", "style"):
            return node.get("x") or ""
        return _escape(node.get("x") or "")
    if tag == "document":
        return "".join(tree_to_html(c, tag) for c in node.get("c") or [])
    attrs = "".join(
        f' {k}="{_escape(str(v))}"' for k, v in (node.get("a") or {}).items() if k
    )
    children = "".join(tree_to_html(c, tag) for c in node.get("c") or [])
    if tag in VOID:
        return f"<{tag}{attrs}>"
    return f"<{tag}{attrs}>{children}</{tag}>"


def walk(node: dict[str, Any]):
    yield node
    for child in node.get("c") or []:
        if isinstance(child, dict):
            yield from walk(child)


def collect_iframes(html: str) -> list[dict[str, str]]:
    tree = html_to_tree(html)
    frames: list[dict[str, str]] = []
    for node in walk(tree):
        if node.get("t") != "iframe":
            continue
        attrs = node.get("a") or {}
        frames.append(
            {
                "src": attrs.get("src") or "",
                "srcdoc": attrs.get("srcdoc") or "",
                "title": attrs.get("title") or "",
                "name": attrs.get("name") or "",
                "width": attrs.get("width") or "",
                "height": attrs.get("height") or "",
                "sandbox": attrs.get("sandbox") or "",
            }
        )
    return frames


def extract_scripts(html: str) -> tuple[list[dict[str, str]], str]:
    """Return (scripts, html_without_external_noise). Inline bodies + src urls."""
    tree = html_to_tree(html)
    scripts: list[dict[str, str]] = []

    def walk(node: dict[str, Any]) -> None:
        if node.get("t") == "script":
            attrs = node.get("a") or {}
            src = attrs.get("src") or ""
            body = "".join(
                c.get("x") or "" for c in (node.get("c") or []) if c.get("t") == "#text"
            )
            defer = ("defer" in attrs) or ("async" in attrs)
            scripts.append({
                "src": src,
                "code": body,
                "type": attrs.get("type") or "",
                "integrity": attrs.get("integrity") or "",
                "defer": defer,
            })
        for child in node.get("c") or []:
            if isinstance(child, dict):
                walk(child)

    walk(tree)
    return scripts, html


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
