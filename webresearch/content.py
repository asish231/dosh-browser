from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

SITE_ROOT_SELECTORS = (
    "#mw-content-text .mw-parser-output",
    "#mw-content-text",
)

GENERIC_ROOT_SELECTORS = (
    "main",
    "[role='main']",
    "article",
    ".post-content",
    ".entry-content",
    ".post-body",
    "#content",
    "body",
)

CONTENT_SELECTORS = SITE_ROOT_SELECTORS + GENERIC_ROOT_SELECTORS

# Selectors in the order used to break ties when two containers carry the same
# projected reading content. ``main`` is a whole-document landmark; an ``article``
# can be one card inside it, so prefer the landmark when their scores tie.
ROOT_SELECTOR_PRIORITY = {
    "main": 6,
    "[role='main']": 5,
    "#content": 4,
    ".post-content": 3,
    ".entry-content": 3,
    ".post-body": 3,
    "article": 2,
    "body": 1,
}

_SCORABLE_BLOCK_TAGS = (
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "pre",
    "blockquote",
    "table",
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


def _candidate_score(candidate: Node) -> tuple[int, int, int]:
    """Score a reading container by its projected non-chrome content."""
    words = 0
    blocks = 0
    headings = 0
    for node in candidate.css(",".join(_SCORABLE_BLOCK_TAGS)):
        if inside_chrome(node, stop=candidate):
            continue
        text = " ".join(node.text(separator=" ", strip=True).split())
        if not text:
            continue
        words += len(text.split())
        blocks += 1
        if (node.tag or "").lower().startswith("h"):
            headings += 1
    return (words, headings, blocks)


def _ancestor_of(node: Node, ancestor: Node) -> bool:
    """True when ``ancestor`` is a strict ancestor of ``node``."""
    current: Node | None = node.parent
    while current is not None:
        if _same_node(current, ancestor):
            return True
        current = current.parent
    return False


def content_root(tree: HTMLParser) -> Node | None:
    """Return the container most likely to hold the page's main reading content.

    A page can contain several plausible landmarks: DeepMind's model-card page,
    for example, places a small promotional ``article`` for another model before
    the document's much larger ``main`` element. Returning the first ``article``
    therefore yields only the card. Site-specific selectors still win outright;
    otherwise, choose whichever generic landmark projects the most non-chrome
    reading content.

    Containment is subtracted first. ``<body>`` wraps ``<main>`` and every card
    in it, so on raw wrapped text it always outranks the article that actually
    holds the content; scoring a landmark on what is *its own* — its content
    minus the content of the landmarks nested inside it — is what makes the
    distinction real.
    """
    for selector in SITE_ROOT_SELECTORS:
        node = tree.css_first(selector)
        if node is not None:
            return node

    candidates: list[tuple[int, Node, tuple[int, int, int]]] = []
    for selector in GENERIC_ROOT_SELECTORS:
        for candidate in tree.css(selector):
            candidates.append(
                (ROOT_SELECTOR_PRIORITY[selector], candidate, _candidate_score(candidate))
            )

    best: Node | None = None
    best_key: tuple[int, int, int, int] | None = None
    for priority, candidate, (words, headings, blocks) in candidates:
        # List pages (GitHub trending, HN, product grids) repeat the SAME
        # landmark tag many times as siblings: a candidate whose candidate
        # descendants number three or more is their CONTAINER, not a wrapper
        # around one duplicate. Subtracting every descendant emptied <main>
        # on such pages and crowned a single <article> row, so ACD read one
        # repo card and dropped its nine siblings. Nested-duplicate pages
        # (a promo card inside the real content) still subtract normally.
        descendant_candidates = 0
        for other_priority, other, other_score in candidates:
            if _same_node(other, candidate):
                continue
            if _ancestor_of(other, candidate):
                if descendant_candidates >= 2:
                    # Three or more candidate descendants: this is a list
                    # container. Keep the full score.
                    descendant_candidates = 99
                    break
                descendant_candidates += 1
        if descendant_candidates < 99:
            for other_priority, other, other_score in candidates:
                if _same_node(other, candidate):
                    continue
                if _ancestor_of(other, candidate):
                    words = max(0, words - other_score[0])
                    headings = max(0, headings - other_score[1])
                    blocks = max(0, blocks - other_score[2])
        key = (words, headings, blocks, priority)
        if best is None or (best_key is not None and key > best_key):
            best = candidate
            best_key = key
    if best is not None:
        return best
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
