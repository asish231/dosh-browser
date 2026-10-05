"""ACD — Agent Context Document.

A structured, retrievable projection of a page for agents. It carries text
blocks with stable IDs, actions, links, observed API traffic, frames and
framework hydration data, so an agent can slice/query instead of re-reading a
flat markdown blob.
"""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from webresearch.content import content_root, inside_chrome
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
    "[itemprop='description'], [itemprop='reviewBody']"
)


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
    if root is not None:
        for node in root.css(BLOCK_SEL):
            if inside_chrome(node, stop=root):
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
                blocks.append(
                    AcdBlock(
                        id=f"b{counter}",
                        type="table",
                        rows=rows,
                        locator=_locator(node),
                    )
                )
                continue
            text = _text(node)
            if not text:
                continue
            counter += 1
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                blocks.append(
                    AcdBlock(
                        id=f"b{counter}",
                        type="heading",
                        level=int(tag[1]),
                        text=text,
                        locator=_locator(node),
                    )
                )
            elif tag == "pre":
                blocks.append(AcdBlock(id=f"b{counter}", type="code", text=text))
            elif tag == "li":
                blocks.append(AcdBlock(id=f"b{counter}", type="list", text=text))
            elif tag == "blockquote":
                blocks.append(AcdBlock(id=f"b{counter}", type="quote", text=text))
            elif tag == "table":
                continue
            else:
                blocks.append(AcdBlock(id=f"b{counter}", type="text", text=text))
            if len(blocks) >= max_blocks:
                break

    actions: list[AcdAction] = []
    for target in targets:
        if target.challenge:
            kind = "click"
        elif target.role in {"textbox", "select"}:
            kind = "type"
        else:
            kind = "click"
        actions.append(
            AcdAction(
                ref=target.ref,
                kind=kind,
                role=target.role,
                label=(target.text or target.name or target.href)[:160],
                name=target.name,
            )
        )

    api = [
        AcdApi(
            index=i + 1,
            url=str(item.get("url") or ""),
            status=int(item.get("status") or 0),
            content_type=str(item.get("content_type") or ""),
        )
        for i, item in enumerate(js_info.get("resource_requests") or [])
    ]

    framework = js_info.get("framework_data") or {}
    errors = list(js_info.get("error_messages") or [])

    counts = {
        "blocks": len(blocks),
        "actions": len(actions),
        "links": len(links),
        "api": len(api),
        "frames": len(frames),
        "framework_keys": len(framework),
        "errors": len(errors),
    }
    return Acd(
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
    )


def acd_view(acd: Acd, *, limit: int = 60) -> str:
    lines = [
        f"ACD v{acd.version} — {len(acd.blocks)} blocks, {len(acd.actions)} actions, "
        f"{len(acd.api)} api, {len(acd.frames)} frames"
    ]
    if acd.framework:
        lines.append("FRAMEWORK_DATA: " + ", ".join(sorted(acd.framework.keys())))
    if acd.errors:
        lines.append(f"JS_ERRORS: {len(acd.errors)} (see acd.errors)")
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
    ids = [b.id for b in acd.blocks]
    i = ids.index(start) if start and start in ids else 0
    j = ids.index(end) + 1 if end and end in ids else i + limit
    return acd.blocks[i:j]