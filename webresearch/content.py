from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

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
SKIP_IDS = {
    "toc",
    "mw-navigation",
    "mw-panel",
    "p-lang",
    "siteSub",
    "jump-to-nav",
    "google_ads",
    "ad-root",
    "advertisement",
    "ad_unit",
    "ad-container",
    "sponsor-block",
}
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
    "ad",
    "ads",
    "advertisement",
    "ad-container",
    "ad-slot",
    "banner-ad",
    "google-ad",
    "taboola",
    "outbrain",
    "sponsored",
    "promoted",
    "sponsor",
    "commercial",
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
    tokens: set[str] = set()
    for tok in str(raw).split():
        low = tok.lower()
        tokens.add(low)
        for sub in low.replace("_", "-").split("-"):
            if sub:
                tokens.add(sub)
    return tokens


def _same_node(a: Node | None, b: Node | None) -> bool:
    """True when both wrappers address the same underlying DOM node.

    selectolax/lexbor builds a *fresh* Python wrapper on every ``.parent`` and
    ``.css()`` call, so ``a is b`` is False even when both wrappers wrap the
    same node. ``==`` compares the underlying node, which is what a ``stop``
    boundary has to mean.
    """
    if a is None or b is None:
        return a is None and b is None
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:  # pragma: no cover - defensive
        return False


# ``<html>`` and ``<body>`` carry theme and feature-flag classes, never
# content-chrome markers. Wikipedia's root element is
# ``vector-toc-available`` and its body is ``skin-vector mediawiki``; testing
# those against the chrome tokens vetoed every article on the site. Once the
# walk reaches either of them it has already left the content root.
NEVER_CHROME_TAGS = {"html", "body"}


def inside_chrome(node: Node, stop: Node | None = None) -> bool:
    """True if node sits in nav/aside/toc/infobox/ads, stopping at the article root."""
    current: Node | None = node
    while current is not None and not _same_node(current, stop):
        tag = (current.tag or "").lower()
        if tag in NEVER_CHROME_TAGS:
            return False
        if tag in SKIP_TAGS:
            return True
        attrs = current.attributes or {}
        role = (attrs.get("role") or "").lower()
        if role in SKIP_ROLES:
            return True
        nid = (attrs.get("id") or "").lower()
        if nid in SKIP_IDS or any(sub in SKIP_IDS for sub in nid.replace("_", "-").split("-") if sub):
            return True
        if _class_tokens(current) & SKIP_CLASS_TOKENS:
            return True
        current = current.parent
    return False
