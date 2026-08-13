from __future__ import annotations

from selectolax.parser import HTMLParser, Node

CONTENT_SELECTORS = (
    "#mw-content-text .mw-parser-output",
    "#mw-content-text",
    "article",
    "main",
    "[role='main']",
    ".post-content",
    ".entry-content",
    ".post-body",
    "#content",
    "body",
)

# Only real page chrome inside the article. Do NOT skip <header>:
# Wikipedia / many sites wrap section titles in <header>.
SKIP_TAGS = {"nav", "aside", "form", "footer"}
SKIP_ROLES = {"navigation", "banner", "contentinfo", "complementary"}
SKIP_IDS = {"toc", "mw-navigation", "mw-panel", "p-lang", "siteSub", "jump-to-nav"}
SKIP_CLASS_TOKENS = {
    "toc",
    "vector-toc",
    "mw-portlet",
    "infobox",
    "navbox",
    "sidebar",
    "hatnote",
    "mw-editsection",
    "reference",
    "references",
    "mw-references-wrap",
    "navbox-styles",
}


def content_root(tree: HTMLParser) -> Node | None:
    for sel in CONTENT_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            return node
    return tree.root


def _class_tokens(node: Node) -> set[str]:
    raw = (node.attributes or {}).get("class") or ""
    if isinstance(raw, (list, tuple)):
        raw = " ".join(str(x) for x in raw)
    return {tok.lower() for tok in str(raw).split() if tok}


def inside_chrome(node: Node, stop: Node | None = None) -> bool:
    """True if node sits in nav/aside/toc/infobox, stopping at the article root."""
    current: Node | None = node
    while current is not None and current is not stop:
        tag = (current.tag or "").lower()
        if tag in SKIP_TAGS:
            return True
        attrs = current.attributes or {}
        role = (attrs.get("role") or "").lower()
        if role in SKIP_ROLES:
            return True
        nid = (attrs.get("id") or "").lower()
        if nid in SKIP_IDS:
            return True
        if _class_tokens(current) & SKIP_CLASS_TOKENS:
            return True
        current = current.parent
    return False
