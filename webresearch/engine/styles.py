"""External stylesheet pipeline: gated fetch, fed into the layout cascade."""

from __future__ import annotations

from urllib.parse import urljoin

from webresearch.engine.tree import html_to_tree, walk


def collect_stylesheet_links(html: str, base_url: str) -> list[dict[str, str]]:
    tree = html_to_tree(html or "")
    out: list[dict[str, str]] = []
    for node in walk(tree):
        if node.get("t") != "link":
            continue
        attrs = node.get("a") or {}
        rel = (attrs.get("rel") or "").lower()
        if "stylesheet" not in rel:
            continue
        href = attrs.get("href") or ""
        if not href or href.startswith(("data:", "blob:", "javascript:")):
            continue
        out.append(
            {
                "href": urljoin(base_url, href),
                "integrity": attrs.get("integrity") or "",
                "media": (attrs.get("media") or "all").lower(),
                "disabled": attrs.get("disabled") is not None,
            }
        )
    return out


def applies_to_screen(media: str) -> bool:
    parts = [p.strip() for p in (media or "all").lower().split(",")]
    if "all" in parts or "screen" in parts:
        return True
    return False


async def load_stylesheets(
    links: list[dict[str, str]],
    page_url: str,
    client,
    headers: dict | None = None,
    *,
    max_sheets: int = 12,
    max_bytes: int = 1_000_000,
) -> tuple[list[dict[str, str]], list[dict]]:
    """Fetch stylesheets through every gate scripts pass through.

    Returns (applied, blocked): applied entries carry href+css for cascade
    injection; blocked entries carry href+reason for honest reporting.
    """
    from webresearch.engine.csp import style_source_allowed
    from webresearch.engine.security import mixed_content_blocked, verify_integrity
    from webresearch.engine.trackers import GRAPH as _TRACKER_GRAPH

    csp = (headers or {}).get("content-security-policy") or (headers or {}).get("Content-Security-Policy")
    applied: list[dict[str, str]] = []
    blocked: list[dict] = []
    for link in links[:max_sheets]:
        href = link["href"]
        if link.get("disabled"):
            blocked.append({"href": href, "reason": "disabled-stylesheet"})
            continue
        if not applies_to_screen(link.get("media", "all")):
            blocked.append({"href": href, "reason": f"media-mismatch:{link.get('media')}"})
            continue
        if mixed_content_blocked(page_url, href):
            blocked.append({"href": href, "reason": "mixed-content"})
            continue
        decision = _TRACKER_GRAPH.evaluate(href, page_url)
        if decision.status == "BLOCKED":
            blocked.append({"href": href, "reason": f"tracker:{decision.entity}"})
            continue
        if csp and not style_source_allowed(csp, external_url=href, page_url=page_url):
            blocked.append({"href": href, "reason": "csp-style-src"})
            continue
        try:
            resp = await client.get(href)
            if resp.status_code >= 400:
                blocked.append({"href": href, "reason": f"http-{resp.status_code}"})
                continue
            from webresearch.engine.cross_origin import check_subresource as _check_xo
            xo_reason, _ = _check_xo(resp.headers, href, page_url, headers)
            if xo_reason is not None:
                blocked.append({"href": href, "reason": xo_reason})
                continue
            css = resp.text
            if len(css.encode("utf-8", "ignore")) > max_bytes:
                blocked.append({"href": href, "reason": "too-large"})
                continue
            if not verify_integrity(css, link.get("integrity")):
                blocked.append({"href": href, "reason": "sri-mismatch"})
                continue
            applied.append({"href": href, "css": css})
        except Exception as exc:
            blocked.append({"href": href, "reason": f"{type(exc).__name__}"})
    return applied, blocked


def inject_stylesheets(tree: dict, applied: list[dict[str, str]]) -> None:
    """Append fetched CSS as <style data-wr-external> nodes under <head>,
    so the layout cascade treats external and inline CSS uniformly."""
    if not applied:
        return
    head = None
    for node in walk(tree):
        if node.get("t") == "head":
            head = node
            break
    parent = head if head is not None else tree
    children = parent.setdefault("c", [])
    for item in applied:
        children.append(
            {
                "t": "style",
                "a": {"data-wr-external": item["href"]},
                "c": [{"t": "#text", "x": item["css"]}],
            }
        )
