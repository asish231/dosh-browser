"""DOM Tag Provenance, Feature Vector Extraction, and Sibling Clustering for Python ACD 2.0.

Provides parity with Rust `provenance.rs`, `features.rs`, `classifier.rs`, and `cluster.rs`.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from typing import Any

from webresearch.types import AcdCollection, TagProvenance


POS_KEYWORDS = ("content", "article", "post", "body", "main", "entry", "story", "text", "summary")
NEG_KEYWORDS = ("sidebar", "ad", "banner", "nav", "footer", "menu", "promo", "cookie", "social", "widget")


def compute_css_path(node) -> str:
    """Compute a deterministic CSS path for a node up to html or an ID anchor."""
    parts: list[str] = []
    current = node

    while current is not None:
        tag = current.tag or ""
        if not tag or tag in {"-document", "#document"}:
            break

        attrs = current.attributes or {}
        nid = attrs.get("id")
        if nid and nid.strip():
            parts.append(f"{tag}#{nid.strip()}")
            break

        # Count nth-of-type among previous siblings
        nth = 1
        prev = current.prev
        while prev is not None:
            if getattr(prev, "tag", None) == tag:
                nth += 1
            prev = prev.prev

        cls_val = attrs.get("class") or ""
        first_cls = cls_val.split()[0] if cls_val.split() else ""
        part = tag
        if first_cls and ":" not in first_cls and "[" not in first_cls:
            part = f"{tag}.{first_cls}"

        if nth > 1:
            part = f"{part}:nth-of-type({nth})"

        parts.append(part)
        if tag == "html":
            break
        current = current.parent

    return " > ".join(reversed(parts))


def compute_xpath(node) -> str:
    """Compute standard 1-based XPath for a node."""
    parts: list[str] = []
    current = node

    while current is not None:
        tag = current.tag or ""
        if not tag or tag in {"-document", "#document"}:
            break

        nth = 1
        prev = current.prev
        while prev is not None:
            if getattr(prev, "tag", None) == tag:
                nth += 1
            prev = prev.prev

        parts.append(f"{tag}[{nth}]")
        if tag == "html":
            break
        current = current.parent

    return "/" + "/".join(reversed(parts))


def extract_provenance(node) -> TagProvenance:
    attrs = {k: str(v) for k, v in (node.attributes or {}).items() if v is not None}
    return TagProvenance(
        tag=node.tag or "",
        css_path=compute_css_path(node),
        xpath=compute_xpath(node),
        attributes=attrs,
    )


@dataclass
class NodeFeatures:
    text_len: float = 0.0
    word_count: float = 0.0
    link_density: float = 0.0
    punctuation_ratio: float = 0.0
    depth: float = 0.0
    child_count: float = 0.0
    descendant_count: float = 0.0
    p_count: float = 0.0
    heading_count: float = 0.0
    list_count: float = 0.0
    table_count: float = 0.0
    form_count: float = 0.0
    img_count: float = 0.0
    is_article: float = 0.0
    is_main: float = 0.0
    is_nav: float = 0.0
    is_footer: float = 0.0
    is_aside: float = 0.0
    class_pos_score: float = 0.0
    class_neg_score: float = 0.0


def extract_features(node) -> NodeFeatures:
    text = " ".join(node.text(separator=" ", strip=True).split()) if hasattr(node, "text") else ""
    text_len = float(len(text))
    word_count = float(len(text.split()))

    punct_count = float(sum(1 for c in text if c in string.punctuation))
    punctuation_ratio = (punct_count / text_len) if text_len > 0 else 0.0

    tag = (node.tag or "").lower()
    p_count = 1.0 if tag == "p" else 0.0
    heading_count = 1.0 if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} else 0.0

    # Ancestor walk for depth and container tags
    depth = 0.0
    is_article = 1.0 if tag == "article" else 0.0
    is_main = 1.0 if tag == "main" else 0.0
    is_nav = 1.0 if tag == "nav" else 0.0
    is_footer = 1.0 if tag == "footer" else 0.0
    is_aside = 1.0 if tag == "aside" else 0.0
    class_pos_score = 0.0
    class_neg_score = 0.0

    cur = node
    walk_depth = 0
    while cur is not None:
        depth += 1.0
        if walk_depth < 6:
            t = (cur.tag or "").lower()
            if t == "article":
                is_article = 1.0
            elif t == "main":
                is_main = 1.0
            elif t == "nav":
                is_nav = 1.0
            elif t == "footer":
                is_footer = 1.0
            elif t == "aside":
                is_aside = 1.0

            attrs = cur.attributes or {}
            c_str = (attrs.get("class") or "").lower()
            id_str = (attrs.get("id") or "").lower()
            haystack = f"{c_str} {id_str}"
            for kw in POS_KEYWORDS:
                if kw in haystack:
                    class_pos_score += 1.0
            for kw in NEG_KEYWORDS:
                if kw in haystack:
                    class_neg_score += 1.0
        cur = cur.parent
        walk_depth += 1

    # Descendants inspect
    anchor_text_len = 0.0
    child_count = 0.0
    descendant_count = 0.0
    list_count = 0.0
    table_count = 0.0
    form_count = 0.0
    img_count = 0.0

    # Direct children count
    cur_c = node.child if hasattr(node, "child") else None
    while cur_c is not None:
        if cur_c.tag and cur_c.tag not in {"-text", "#text"}:
            child_count += 1.0
        cur_c = cur_c.next

    for child in getattr(node, "iter", lambda: [])():
        if child == node:
            continue
        descendant_count += 1.0
        dtag = (child.tag or "").lower()
        if dtag == "a":
            atext = child.text(separator=" ", strip=True) if hasattr(child, "text") else ""
            anchor_text_len += float(len(atext.strip()))
        elif dtag == "p":
            p_count += 1.0
        elif dtag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading_count += 1.0
        elif dtag in {"ul", "ol", "li"}:
            list_count += 1.0
        elif dtag in {"table", "tr", "td", "th"}:
            table_count += 1.0
        elif dtag in {"form", "input", "button", "select", "textarea"}:
            form_count += 1.0
        elif dtag in {"img", "svg", "picture"}:
            img_count += 1.0

    link_density = min(1.0, anchor_text_len / text_len) if text_len > 0 else 0.0

    return NodeFeatures(
        text_len=text_len,
        word_count=word_count,
        link_density=link_density,
        punctuation_ratio=punctuation_ratio,
        depth=depth,
        child_count=child_count,
        descendant_count=descendant_count,
        p_count=p_count,
        heading_count=heading_count,
        list_count=list_count,
        table_count=table_count,
        form_count=form_count,
        img_count=img_count,
        is_article=is_article,
        is_main=is_main,
        is_nav=is_nav,
        is_footer=is_footer,
        is_aside=is_aside,
        class_pos_score=class_pos_score,
        class_neg_score=class_neg_score,
    )


def classify_node(f: NodeFeatures) -> tuple[str, float]:
    """Pure-Python decision logic matching Rust classifier.rs."""
    # 1. Navigation
    if f.is_nav > 0.0 or (f.link_density > 0.65 and f.child_count > 2.0 and f.text_len < 300.0):
        conf = 0.95 if f.is_nav > 0.0 else 0.85
        return "Navigation", conf

    # 2. Boilerplate / Footer
    if f.is_footer > 0.0 or f.is_aside > 0.0 or (f.class_neg_score >= 2.0 and f.class_pos_score == 0.0):
        conf = 0.90 if f.is_footer > 0.0 else 0.75
        return "Boilerplate", conf

    # 3. Form Block
    if f.form_count >= 2.0 and f.text_len < 400.0:
        return "FormBlock", 0.90

    # 4. Product Card / Catalog Snippet (image, compact text, no prose paragraphs)
    if f.img_count >= 1.0 and f.p_count == 0.0 and 15.0 < f.text_len < 450.0 and f.child_count >= 2.0:
        return "ProductCard", 0.85

    # 5. ArticleBody
    if (f.is_article > 0.0 or (f.is_main > 0.0 and f.p_count >= 1.0) or f.p_count >= 1.0 or f.class_pos_score > 0.0) and f.link_density < 0.35 and f.text_len > 40.0:
        score = 0.65
        if f.is_article > 0.0 or f.is_main > 0.0:
            score += 0.20
        if f.p_count >= 1.0:
            score += 0.10
        if f.class_pos_score > 0.0:
            score += 0.05
        if f.link_density < 0.15:
            score += 0.05
        return "ArticleBody", min(0.99, score)

    if f.text_len > 80.0:
        return "ArticleBody", 0.50
    return "Unknown", 0.40


def child_tag_signature(node) -> str:
    tags: list[str] = []
    # inspect immediate children
    cur = node.child
    while cur is not None:
        t = getattr(cur, "tag", None)
        if t and t not in {"-text", "#text"}:
            tags.append(t)
        cur = cur.next
    return "+".join(tags)


def cluster_siblings(parent, collection_id_start: int = 1) -> list[AcdCollection]:
    collections: list[AcdCollection] = []
    signature_groups: dict[str, list[Any]] = {}

    cur = parent.child if parent is not None else None
    while cur is not None:
        sig = child_tag_signature(cur)
        if sig and "+" in sig:
            signature_groups.setdefault(sig, []).append(cur)
        cur = cur.next

    curr_id = collection_id_start
    for sig, nodes in signature_groups.items():
        if len(nodes) >= 2:
            sample_text = nodes[0].text(separator=" ", strip=True) if hasattr(nodes[0], "text") else ""
            if "img" in sig and any(sym in sample_text for sym in ("$", "₹", "€")):
                cat = "ProductCatalog"
            elif "h3" in sig or "h2" in sig:
                cat = "SearchResults"
            else:
                cat = "CardGrid"

            collections.append(
                AcdCollection(
                    id=f"col_{curr_id}",
                    category=cat,
                    name=f"Collection ({sig})",
                    count=len(nodes),
                    block_ids=[],
                )
            )
            curr_id += 1

    return collections

