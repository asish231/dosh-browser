"""ACD — Agent Context Document.

A structured, retrievable projection of a page for agents. It carries text
blocks with stable IDs, actions, links, observed API traffic, frames and
framework hydration data, so an agent can slice/query instead of re-reading a
flat markdown blob.
"""

from __future__ import annotations

import json

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from webresearch.content import content_root, inside_chrome
from webresearch.provenance import (
    classify_node,
    cluster_siblings,
    extract_features,
    extract_provenance,
)
from webresearch.types import (
    Acd,
    AcdAction,
    AcdApi,
    AcdBlock,
    FrameInfo,
    Link,
    Target,
)

BLOCK_SEL = (
    "h1, h2, h3, h4, h5, h6, p, li, pre, blockquote, table, "
    "[itemprop='description'], [itemprop='reviewBody'], "
    "[itemprop='text'], [itemprop='articleBody'], [itemprop='headline'], "
    "[itemprop='caption'], [itemprop='abstract']"
)

# Structural content regions. Modern pages lay content out in div/span cards
# with no p/h*/li anywhere; a purely tag-driven selector sees nothing there.
# These heuristic container matches are a FALLBACK pass, not the primary one:
# a region is projected only when it contains no already-matched semantic
# content (BLOCK_SEL) inside it — the de-duplicate, don't double-count
# discipline the action surface already applies. `article`/`[role='article']`
# are handled here for the same reason: putting them in BLOCK_SEL would
# double-project every article whose inner p/h* already matched.
REGION_SEL = (
    "article, [role='article'], "
    "[class*='card'], [class*='item'], [class*='post'], [class*='quote'], "
    "[data-testid*='card']"
)
# Region matches are class-attribute heuristics, so a junk floor applies:
# below this, a match is more likely a crumb/button/label than content.
REGION_MIN_CHARS = 40


def _text(node) -> str:
    return " ".join(node.text(separator=" ", strip=True).split())


def _locator(node) -> str:
    parts: list[str] = []
    current = node
    depth = 0
    while current is not None and depth < 4:
        tag = current.tag or ""
        if not tag or tag in {"-document"}:
            break
        attrs = current.attributes or {}
        nid = attrs.get("id")
        if nid:
            parts.append(f"#{nid}")
            break
        cls = (attrs.get("class") or "").split()
        cls = [c for c in cls if c][:2]
        parts.append(tag + ("." + ".".join(cls) if cls else ""))
        current = current.parent
        depth += 1
    return " > ".join(reversed(parts))


def build_acd(
    *,
    url: str,
    title: str,
    html: str,
    targets: list[Target] | None = None,
    links: list[Link] | None = None,
    frames: list[FrameInfo] | None = None,
    js_info: dict | None = None,
    max_blocks: int = 400,
) -> Acd:
    js_info = js_info or {}
    targets = targets or []
    links = links or []
    frames = frames or []

    blocks: list[AcdBlock] = []
    tree = HTMLParser(html or "")
    root = content_root(tree) or tree.root
    counter = 0
    blocks_total = 0
    chrome_skipped = 0
    budget_hit = False
    if root is not None:
        # Candidate census first: blocks_total is the candidate ceiling, so
        # the consumer can tell "empty page" from "projection missed it".
        candidates = root.css(BLOCK_SEL)
        blocks_total = len(candidates)

        def _emit(block: AcdBlock, node=None):
            if not block.content_hash:
                import hashlib
                h = hashlib.sha256()
                h.update(f"{block.type}:{block.locator}:{block.text}".encode("utf-8", errors="replace"))
                if block.rows:
                    for row in block.rows:
                        h.update(":".join(row).encode("utf-8", errors="replace"))
                block.content_hash = h.hexdigest()[:16]
            if node is not None:
                try:
                    block.provenance = extract_provenance(node)
                    feats = extract_features(node)
                    cat, conf = classify_node(feats)
                    block.semantic_category = cat
                    block.confidence = conf
                except Exception:
                    pass
            blocks.append(block)

        for node in candidates:
            if inside_chrome(node, stop=root):
                chrome_skipped += 1
                continue
            tag = node.tag or ""
            if tag == "table":
                parent = node.parent
                if parent is not None and parent.tag == "table":
                    continue
                rows = [
                    [_text(c) for c in tr.css("th, td")]
                    for tr in node.css("tr")
                ]
                rows = [r for r in rows if any(r)]
                if not rows:
                    continue
                counter += 1
                _emit(
                    AcdBlock(
                        id=f"b{counter}",
                        type="table",
                        rows=rows,
                        locator=_locator(node),
                    ),
                    node=node,
                )
                if len(blocks) >= max_blocks:
                    budget_hit = True
                    break
                continue
            text = _text(node)
            if not text:
                continue
            counter += 1
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                _emit(
                    AcdBlock(
                        id=f"b{counter}",
                        type="heading",
                        level=int(tag[1]),
                        text=text,
                        locator=_locator(node),
                    ),
                    node=node,
                )
            elif tag == "pre":
                _emit(AcdBlock(id=f"b{counter}", type="code", text=text), node=node)
            elif tag == "li":
                _emit(AcdBlock(id=f"b{counter}", type="list", text=text), node=node)
            elif tag == "blockquote":
                _emit(AcdBlock(id=f"b{counter}", type="quote", text=text), node=node)
            else:
                _emit(AcdBlock(id=f"b{counter}", type="text", text=text), node=node)
            if len(blocks) >= max_blocks:
                budget_hit = True
                break

        # Region fallback pass: div/span card content the tag-driven selector
        # cannot see. Innermost region wins; regions whose inner semantic
        # nodes already matched are skipped (their content is already in
        # blocks, and re-projecting the wrapper text would double-count).
        # NOTE: selectolax css() includes the node ITSELF when it matches
        # the selector (p.css('p') -> [p]), so "contains another region"
        # must exclude the self-hit; a self-hit's html equals the node's
        # own html, a descendant's is always a strict substring.
        if not budget_hit:
            regions = [
                node for node in root.css(REGION_SEL)
                if not inside_chrome(node, stop=root)
            ]

            def _other_regions(node):
                return [h for h in node.css(REGION_SEL) if h.html != node.html]

            qualifying = [
                node for node in regions
                if not _other_regions(node) and not node.css(BLOCK_SEL)
                and len(_text(node)) >= REGION_MIN_CHARS
            ]
            blocks_total += len(qualifying)
            for node in qualifying:
                if len(blocks) >= max_blocks:
                    budget_hit = True
                    break
                text = _text(node)
                counter += 1
                _emit(
                    AcdBlock(
                        id=f"b{counter}",
                        type="text",
                        text=text,
                        locator=_locator(node),
                        role="region",
                    ),
                    node=node,
                )

    # Extract JSON-LD structured data (FAQPage, HowTo, Product, etc.)
    json_ld_data: list[dict] = []
    if tree is not None:
        for script_node in tree.css("script[type='application/ld+json']"):
            raw = script_node.text(strip=True)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    json_ld_data.extend(item for item in parsed if isinstance(item, dict))
                elif isinstance(parsed, dict):
                    # Check for schema.org @graph
                    if isinstance(parsed.get("@graph"), list):
                        json_ld_data.extend(item for item in parsed["@graph"] if isinstance(item, dict))
                    json_ld_data.append(parsed)
            except Exception:
                continue

    # Project high-value JSON-LD schemas (FAQPage, HowTo, Product) into
    # structured ACD blocks. Projectables are counted FIRST so blocks_total
    # stays exact even when the budget truncates the projection.
    pending: list[dict] = []

    def _pending(**kwargs) -> None:
        pending.append(kwargs)

    for entity in json_ld_data:
        etype = entity.get("@type")
        if etype == "FAQPage" and isinstance(entity.get("mainEntity"), list):
            for faq in entity["mainEntity"]:
                if not isinstance(faq, dict):
                    continue
                q = str(faq.get("name") or "").strip()
                ans_obj = faq.get("acceptedAnswer") or {}
                a = str(ans_obj.get("text") if isinstance(ans_obj, dict) else ans_obj or "").strip()
                if q and a:
                    clean_a = " ".join(HTMLParser(a).text(separator=" ", strip=True).split()) if "<" in a else a
                    _pending(type="heading", level=4, text=f"Q: {q}")
                    _pending(type="text", text=clean_a)
        elif etype == "HowTo" and isinstance(entity.get("step"), list):
            howto_name = str(entity.get("name") or "").strip()
            if howto_name:
                _pending(type="heading", level=3, text=howto_name)
            for step in entity["step"]:
                if not isinstance(step, dict):
                    continue
                s_name = str(step.get("name") or "").strip()
                s_text = ""
                direction = step.get("itemListElement")
                if isinstance(direction, dict):
                    s_text = str(direction.get("text") or "").strip()
                elif isinstance(step.get("text"), str):
                    s_text = step["text"].strip()
                combined = f"{s_name}: {s_text}" if s_name and s_text else (s_name or s_text)
                if combined:
                    _pending(type="list", text=combined)
        elif etype == "Product":
            name = str(entity.get("name") or "").strip()
            description = str(entity.get("description") or "").strip()
            if name:
                _pending(type="heading", level=4, text=f"Product: {name}")
            if description:
                clean_d = " ".join(HTMLParser(description).text(separator=" ", strip=True).split()) if "<" in description else description
                _pending(type="text", text=clean_d)
            offers = entity.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
            for offer in offer_list:
                if not isinstance(offer, dict):
                    continue
                price = str(offer.get("price") or "").strip()
                currency = str(offer.get("priceCurrency") or "").strip()
                availability = str(offer.get("availability") or "").strip()
                if price:
                    bits = [f"Price: {price} {currency}".strip()]
                    if availability:
                        bits.append(availability.rsplit("/", 1)[-1])
                    _pending(type="text", text=" — ".join(bits))
    blocks_total += len(pending)
    for kwargs in pending:
        if len(blocks) >= max_blocks:
            budget_hit = True
            break
        counter += 1
        b = AcdBlock(id=f"b{counter}", **kwargs)
        import hashlib
        h = hashlib.sha256()
        h.update(f"{b.type}:{b.locator}:{b.text}".encode("utf-8", errors="replace"))
        b.content_hash = h.hexdigest()[:16]
        blocks.append(b)

    actions: list[AcdAction] = []
    for target in targets:
        if target.challenge:
            kind = "click"
        elif target.role == "link":
            kind = "navigate"
        elif target.role in {"textbox", "select"}:
            kind = "type"
        elif target.role == "slider":
            kind = "drag"
        elif target.role == "iframe":
            kind = "embed"
        elif target.role == "button" and target.name.strip().lower() in {"submit", "search"}:
            kind = "submit"
        elif target.role == "button" or target.role in {"checkbox", "label"}:
            kind = "activate"
        else:
            kind = "activate"
        routing = ""
        if kind == "navigate" and target.href:
            routing = target.href
        elif kind == "submit":
            routing = target.name or target.ref
        elif kind == "drag":
            routing = "drag:path=" + ",".join(str(i) for i in target.path)
        elif kind == "embed":
            routing = target.href
        actions.append(
            AcdAction(
                ref=target.ref,
                kind=kind,
                role=target.role,
                label=(target.text or target.name or target.href)[:160],
                name=target.name,
                href=target.href,
                run=routing or target.ref,
            )
        )

    api = [
        AcdApi(
            index=i + 1,
            url=str(item.get("url") or ""),
            status=int(item.get("status") or 0),
            content_type=str(item.get("content_type") or ""),
            method=str(item.get("method") or "GET"),
            data=item.get("data"),
        )
        for i, item in enumerate(js_info.get("resource_requests") or [])
    ]

    framework = dict(js_info.get("framework_data") or {})
    if json_ld_data:
        framework["json_ld"] = json_ld_data
    errors = list(js_info.get("error_messages") or [])

    try:
        from webresearch.engine.inspect import count_interactive
        controls_total = count_interactive(html or "")
    except Exception:
        controls_total = len(targets)

    counts = {
        "blocks": len(blocks),
        "blocks_total": blocks_total,
        "actions": len(actions),
        "actions_total": controls_total,
        "links": len(links),
        "api": len(api),
        "frames": len(frames),
        "framework_keys": len(framework),
        "errors": len(errors),
    }
    unavailable: list[str] = []
    if any(a.kind == "drag" for a in actions):
        unavailable.append("drag: numeric deltas come from the mini-DOM block-flow layout engine (approximate, not Blink-exact)")
    if any(a.kind == "embed" for a in actions):
        unavailable.append("embed: iframe pixels are structurally decomposed, never rastered; frame postMessage lanes are live")
    if controls_total > len(actions):
        unavailable.append(
            f"actions: {controls_total - len(actions)} interactive controls beyond the 80-ref budget did not receive refs"
        )
    if chrome_skipped:
        unavailable.append(
            f"blocks: {chrome_skipped} candidate content region(s) inside nav/header/footer chrome were skipped"
        )
    if budget_hit and blocks_total > len(blocks):
        unavailable.append(
            f"blocks: {blocks_total - len(blocks)} candidate content region(s) beyond the {max_blocks}-block budget were not projected"
        )

    # Subtree clustering for Collections (ACD 2.0)
    collections = []
    search_root = tree.body if tree is not None and tree.body is not None else root
    if search_root is not None:
        collections = cluster_siblings(search_root, 1)
        for cont in search_root.css("div, section, main, ul")[:50]:
            sub_cols = cluster_siblings(cont, len(collections) + 1)
            for c in sub_cols:
                if not any(existing.name == c.name for existing in collections):
                    collections.append(c)
            if len(collections) >= 10:
                break

    return Acd(
        version="2",
        url=url,
        title=title,
        blocks=blocks,
        actions=actions,
        links=links,
        api=api,
        frames=frames,
        framework=framework,
        errors=errors,
        counts=counts,
        collections=collections,
        unavailable=unavailable,
    )


def diff_acd(old_acd: Acd, new_acd: Acd) -> dict:
    """Diff-first reading: return only newly added or mutated blocks between visits."""
    old_hashes = {b.id: b.content_hash for b in old_acd.blocks if b.content_hash}
    old_locators = {b.locator: b.content_hash for b in old_acd.blocks if b.locator and b.content_hash}

    mutated_blocks: list[AcdBlock] = []
    unchanged_count = 0

    for b in new_acd.blocks:
        prior_hash = old_hashes.get(b.id) or (old_locators.get(b.locator) if b.locator else None)
        if prior_hash and prior_hash == b.content_hash:
            unchanged_count += 1
        else:
            mutated_blocks.append(b)

    new_ids = {b.id for b in new_acd.blocks}
    removed_ids = [bid for bid in old_hashes if bid not in new_ids]

    return {
        "mutated_blocks": mutated_blocks,
        "mutated_count": len(mutated_blocks),
        "removed_block_ids": removed_ids,
        "unchanged_count": unchanged_count,
        "total_blocks": len(new_acd.blocks),
    }


def error_summary(errors: list[str], *, limit: int = 3, width: int = 160) -> str:
    """Compact inline summary of distinct Mini-DOM error classes.

    A count alone tells an agent nothing: 25 errors on GitHub could be one
    missing API repeated or 25 unrelated breakages, and only the second means
    the rendered view is untrustworthy. Group by message so the agent can see
    which capability the Mini-DOM is missing without a second round trip.
    """
    if not errors:
        return ""
    counts: dict[str, int] = {}
    for err in errors:
        msg = " ".join(str(err).split())[:width]
        counts[msg] = counts.get(msg, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    parts = [f"{msg} (x{count})" if count > 1 else msg for msg, count in ranked[:limit]]
    hidden = len(ranked) - len(parts)
    suffix = f" ; +{hidden} more distinct" if hidden > 0 else ""
    distinct = f"{len(ranked)} distinct" if len(ranked) > 1 else "1 distinct"
    return f"JS_ERRORS: {len(errors)} error(s), {distinct} — " + "; ".join(parts) + suffix


def acd_view(acd: Acd, *, limit: int = 60) -> str:
    lines = [
        f"ACD v{acd.version} — {len(acd.blocks)} blocks, {len(acd.actions)} actions, "
        f"{len(acd.api)} api, {len(acd.frames)} frames"
    ]
    if acd.framework:
        lines.append("FRAMEWORK_DATA: " + ", ".join(sorted(acd.framework.keys())))
    if acd.errors:
        lines.append(error_summary(acd.errors))
    if acd.unavailable:
        lines.append("UNAVAILABLE: " + "; ".join(acd.unavailable[:5]))
    if acd.counts.get("actions_total", 0) > acd.counts.get("actions", 0):
        lines.append(f"ACTIONS: {acd.counts['actions']} of {acd.counts['actions_total']} (budget-truncated)")
    blocks_total = acd.counts.get("blocks_total", 0)
    if blocks_total > len(acd.blocks):
        lines.append(f"BLOCKS: {len(acd.blocks)} of {blocks_total} (some skipped or budget-truncated; see UNAVAILABLE)")
    for block in acd.blocks[:limit]:
        if block.type == "table":
            lines.append(f"[{block.id}] table {len(block.rows)}x{len(block.rows[0]) if block.rows else 0}")
            for row in block.rows[:4]:
                lines.append("    | " + " | ".join(row) + " |")
        elif block.type == "heading":
            lines.append(f"[{block.id}] h{block.level} {block.text[:120]}")
        elif block.type == "code":
            lines.append(f"[{block.id}] code {block.text[:80]}")
        else:
            lines.append(f"[{block.id}] {block.text[:160]}")
    return "\n".join(lines)


def query_acd(acd: Acd, term: str, *, limit: int = 40) -> list[AcdBlock]:
    needle = (term or "").lower().strip()
    if not needle:
        return acd.blocks[:limit]
    out: list[AcdBlock] = []
    for block in acd.blocks:
        hay = block.text.lower()
        if block.rows:
            hay += " " + " ".join(" ".join(r) for r in block.rows).lower()
        if needle in hay:
            out.append(block)
        if len(out) >= limit:
            break
    return out


def slice_acd(acd: Acd, start: str = "", end: str = "", *, limit: int = 120) -> list[AcdBlock]:
    """Slice blocks between start and end ids (inclusive).

    An unknown id is an ERROR, never a silent fallback: a retrieval API that
    answers an unknown address with "the first N blocks" returns a confident
    wrong answer, which is worse than no answer — the agent cannot tell it
    happened. Callers catch ValueError and surface it honestly.
    """
    ids = [b.id for b in acd.blocks]
    if start and start not in ids:
        raise ValueError(
            f"unknown block id: {start}; valid range is {ids[0]}..{ids[-1]}" if ids
            else f"unknown block id: {start}; the ACD has no blocks"
        )
    if end and end not in ids:
        raise ValueError(
            f"unknown block id: {end}; valid range is {ids[0]}..{ids[-1]}" if ids
            else f"unknown block id: {end}; the ACD has no blocks"
        )
    i = ids.index(start) if start else 0
    j = ids.index(end) + 1 if end else min(i + limit, len(ids))
    return acd.blocks[i:j]