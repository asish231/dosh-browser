"""QuickJS page runtime. Own DOM, not Chromium."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin

import httpx
from quickjs import Context

from webresearch.engine.tree import extract_scripts, html_to_tree

MINIDOM_JS = Path(__file__).with_name("minidom.js").read_text(encoding="utf-8")

SKIP_SRC = (
    "google-analytics",
    "googletagmanager",
    "doubleclick.net",
    "hotjar",
    "segment.com",
    "facebook.net/signals",
    "scorecardresearch",
)

MAX_SCRIPTS = 30
MAX_SCRIPT_BYTES = 1_500_000


def _wants_js(html: str) -> bool:
    low = html.lower()
    if "<script" not in low:
        return False
    if 'id="root"' in low or 'id="app"' in low or "data-reactroot" in low:
        return True
    if low.count("<script") >= 2:
        return True
    return "react" in low or "vue" in low or "__next" in low


def _host_parse_html(html: str) -> str:
    tree = html_to_tree(f"<div id='__frag'>{html}</div>")
    for child in tree.get("c") or []:
        if child.get("t") == "div" and (child.get("a") or {}).get("id") == "__frag":
            return json.dumps(child.get("c") or [])
    return json.dumps(tree.get("c") or [])


async def run_scripts(
    html: str,
    url: str,
    client: httpx.AsyncClient,
    *,
    force: bool = False,
) -> tuple[str, dict]:
    """Execute page scripts against mini-DOM. Returns (html, info)."""
    info: dict = {
        "ran": False,
        "scripts": 0,
        "errors": 0,
        "skipped_modules": 0,
        "engine": "quickjs",
        "post_messages": [],
        "resource_requests": [],
    }
    if not force and not _wants_js(html):
        return html, info

    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.add_callable("host_log", lambda *_a: None)
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.add_callable(
        "host_post_message",
        lambda data, origin="*": info["post_messages"].append(
            {"data": str(data)[:4000], "origin": str(origin)[:500]}
        ),
    )

    sync = httpx.Client(
        timeout=8.0,
        follow_redirects=True,
        headers=dict(client.headers),
        cookies=client.cookies,
    )

    def host_fetch(req_url: str, method: str, body: str) -> str:
        try:
            abs_url = urljoin(url, req_url)
            resp = sync.request(method or "GET", abs_url, content=body or None)
            info["resource_requests"].append(
                {
                    "url": str(resp.url)[:2000],
                    "status": resp.status_code,
                    "content_type": resp.headers.get("content-type", "")[:200],
                }
            )
            return json.dumps(
                {
                    "status": resp.status_code,
                    "url": str(resp.url),
                    "content_type": resp.headers.get("content-type", ""),
                    "body": resp.text[:400_000],
                }
            )
        except Exception as exc:
            return json.dumps({"status": 599, "body": str(exc)})

    ctx.add_callable("host_fetch", host_fetch)

    tree = html_to_tree(html)
    payload = json.dumps(tree, ensure_ascii=False)
    ctx.eval(f"__load({payload}, {json.dumps(url)})")

    scripts, _ = extract_scripts(html)
    ran = 0
    for script in scripts:
        if ran >= MAX_SCRIPTS:
            break
        stype = (script.get("type") or "").lower()
        if stype in {"application/ld+json", "application/json", "text/html"}:
            continue
        if "module" in stype:
            info["skipped_modules"] += 1
            continue
        code = script.get("code") or ""
        src = script.get("src") or ""
        if src:
            low = src.lower()
            if any(skip in low for skip in SKIP_SRC):
                continue
            try:
                resp = await client.get(urljoin(url, src))
                code = resp.text
            except Exception:
                info["errors"] += 1
                continue
        code = code.strip()
        if not code or len(code.encode()) > MAX_SCRIPT_BYTES:
            continue
        try:
            ctx.eval(code)
            ran += 1
        except Exception:
            info["errors"] += 1

    for _ in range(10):
        try:
            left = ctx.eval("__drainTimers()")
        except Exception:
            break
        if not left:
            break

    try:
        out = str(ctx.eval("__serialize()") or html)
    except Exception:
        out = html

    client.cookies.update(sync.cookies)
    sync.close()
    info["ran"] = ran > 0
    info["scripts"] = ran
    return out, info
