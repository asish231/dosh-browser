from __future__ import annotations

import json as _json
import re as _re
from typing import Literal
from urllib.parse import quote as _quote, urljoin as _urljoin

import httpx

from webresearch.acd import build_acd, error_summary
from webresearch.engine.captcha import (
    CaptchaInspection,
    dump_captcha_code,
    inspect_captcha,
)
from webresearch.engine.datadome import detect as _datadome_detect
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.frames import extract_origin as _extract_origin
from webresearch.engine.inspect import agent_view_extras, inspect_html
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import _impersonation_allowed, fetch
from webresearch.parse import parse
from webresearch.snapshot import snapshot
from webresearch.types import CaptchaCodeDump, CaptchaReport, FetchResult, Page

JsMode = Literal["auto", "always", "never"]


async def _try_resolve_datadome(
    url: str,
    client: httpx.AsyncClient,
    html: str,
    result: FetchResult,
) -> FetchResult | None:
    """Attempt to resolve DataDome challenge and return re-fetched result, or None."""
    detection = _datadome_detect(html)
    if not detection.present:
        return None

    # Extract dd object from the challenge page
    dd_match = _re.search(r"dd\s*=\s*\{([^}]+)\}", html)
    if not dd_match:
        return None

    dd_raw = dd_match.group(0)
    dd_cid = _re.search(r"'cid'\s*:\s*'([^']+)'", dd_raw)
    dd_hsh = _re.search(r"'hsh'\s*:\s*'([^']+)'", dd_raw)
    dd_host = _re.search(r"'host'\s*:\s*'([^']+)'", dd_raw)
    dd_s = _re.search(r"'s'\s*:\s*(\d+)", dd_raw)
    dd_b = _re.search(r"'b'\s*:\s*(\d+)", dd_raw)
    dd_cookie = _re.search(r"'cookie'\s*:\s*'([^']+)'", dd_raw)
    dd_e = _re.search(r"'e'\s*:\s*'([^']+)'", dd_raw)

    if not all([dd_cid, dd_hsh, dd_host, dd_s, dd_b, dd_cookie]):
        return None

    base_url = result.final_url or url
    iframe_url = (
        f'https://{dd_host.group(1)}/interstitial/'
        f'?initialCid={_quote(dd_cid.group(1))}'
        f'&hash={_quote(dd_hsh.group(1))}'
        f'&cid={_quote(dd_cookie.group(1))}'
        f'&referer={_quote(base_url)}'
        f'&s={dd_s.group(1)}'
        f'&e={dd_e.group(1) if dd_e else ""}'
        f'&b={dd_b.group(1)}'
        f'&dm=cd'
    )

    # Fetch and run the interstitial frame
    try:
        frame_resp = await client.get(iframe_url)
        if frame_resp.status_code != 200:
            return None
        frame_html = frame_resp.text
        frame_html, frame_info = await run_scripts(
            frame_html, iframe_url, client, force=True
        )

        # Check if a new datadome cookie was set by the frame's postMessage
        post_msgs = frame_info.get("post_messages", [])
        for pm in post_msgs:
            data = pm.get("data", "")
            if '"cookie"' in data:
                try:
                    parsed = _json.loads(data)
                    if isinstance(parsed, dict) and parsed.get("cookie"):
                        # Cookie was set by the host_post_message_handler
                        # which extracts and sets the datadome cookie
                        pass
                except Exception:
                    pass

        # Try to re-fetch the original URL with the cookie
        new_result = await fetch(url, client=client)
        if new_result.status_code == 200:
            return new_result
    except Exception:
        pass

    return None


def _decorate(
    page: Page,
    html: str,
    js_ran: bool,
    js_info: dict | None,
    *,
    frames: list[FrameDocument] | None = None,
    captcha: CaptchaReport | None = None,
) -> Page:
    targets, challenges = inspect_html(html, page.url)
    extra = agent_view_extras(targets=targets, challenges=challenges, js_ran=js_ran, js_info=js_info)
    infos = frame_infos(frames or [])
    acd = build_acd(
        url=page.url,
        title=page.title,
        html=html,
        targets=targets,
        links=page.links,
        frames=infos,
        js_info=js_info,
    )
    if acd.counts.get("blocks"):
        extra += (
            f"\nACD: {acd.counts['blocks']} blocks, {acd.counts['actions']} actions, "
            f"{acd.counts['api']} api calls"
        )
    info = js_info or {}
    cors_blocked = [r for r in (info.get("resource_requests") or []) if r.get("content_type") == "cors-blocked"]
    if cors_blocked:
        extra += f"\nCORS-BLOCKED: {len(cors_blocked)} cross-origin read(s) without ACAO; data not loaded (faithful, not retried): "
        extra += ", ".join(r.get("url", "")[:120] for r in cors_blocked[:5])
    if info.get("framework_data"):
        extra += "\nFRAMEWORK_DATA: " + ", ".join(sorted(info["framework_data"].keys()))
    if info.get("error_messages"):
        extra += "\n" + error_summary(list(info["error_messages"]))
    if infos:
        challenge_count = sum(1 for frame in infos if frame.challenge)
        extra += f"\nFRAMES: {len(infos)} loaded, {challenge_count} challenge frame(s)"
        for frame in infos[:12]:
            extra += (
                f"\n[{frame.ref}] depth={frame.depth} status={frame.status} "
                f"{frame.title or frame.url}"
            )
    if captcha and captcha.present:
        extra += (
            f"\nCAPTCHA OBJECT: kind={captcha.kind}; use browser_captcha for structure "
            "and browser_captcha_code to dump widget/iframe HTML."
        )
    dialogs = info.get("dialogs") or []
    if dialogs:
        extra += "\nDIALOGS (page called alert/confirm/prompt; answer with browser_respond_dialog, then re-run):"
        for i, d in enumerate(dialogs[:6]):
            answered = d.get("answered", "default")
            extra += f"\n  [{i}] {d.get('type')}: {(d.get('message') or '')[:120]} (answered: {answered})"
    popups = info.get("popups") or []
    if popups:
        extra += "\nPOPUPS (window.open; loaded as new tabs, switch with browser_tabs):"
        for p in popups[:4]:
            extra += f"\n  {p.get('url', '')[:160]}"
    view = f"{page.view}\n\n{extra}".strip()
    engine = info.get("engine") or ("quickjs" if js_ran else "html")
    return page.model_copy(
        update={
            "view": view,
            "chars": len(view),
            "js_ran": js_ran,
            "targets": targets,
            "challenges": challenges,
            "frames": infos,
            "captcha": captcha,
            "acd": acd,
            "engine": engine,
        }
    )


async def read_live(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_chars: int = 8000,
    max_links: int = 50,
    js: JsMode = "auto",
    sprite_endpoint: str = "",
    chromium_session: object | None = None,
    return_js_info: bool = False,
) -> tuple:
    """Fetch (+ QuickJS + frames) → page model and live agent state. Pure from-scratch engine, no Chromium."""
    result = await fetch(url, client=client)
    html = result.body
    js_info: dict | None = None
    js_ran = False
    if result.kind == "html" and js != "never":
        html, js_info = await run_scripts(html, result.final_url or url, client, force=(js == "always"), headers=result.headers)
        js_ran = bool(js_info.get("ran"))
        result = result.model_copy(update={"body": html, "kind": "html", "content_type": "text/html"})

    # DataDome auto-retry: if we got a 403 challenge, try to resolve it
    if result.status == 403 and result.kind == "html":
        resolved = await _try_resolve_datadome(url, client, html, result)
        if resolved is not None:
            result = resolved
            html = resolved.body
            js_info = None
            js_ran = False
            # Re-run scripts on the resolved page
            if result.kind == "html" and js != "never":
                html, js_info = await run_scripts(html, result.final_url or url, client, force=(js == "always"), headers=result.headers)
                js_ran = bool(js_info.get("ran"))
                result = result.model_copy(update={"body": html, "kind": "html", "content_type": "text/html"})

    frames = []
    if result.kind == "html":
        from webresearch.consent import dismiss_consent_dom

        clean_html, dismissed = dismiss_consent_dom(html)
        if dismissed:
            html = clean_html
            result = result.model_copy(update={"body": html})

        frames = await load_frames(
            html,
            result.final_url or url,
            client,
            js=js,
            parent_headers=result.headers,
            parent_messages=(js_info.get("post_messages") if js_info else None),
        )
        if js_info and js_info.get("ctx") and frames:
            parent_ctx = js_info["ctx"]
            child_replies = []
            for frame in frames:
                for msg in frame.post_messages:
                    tgt = msg.get("target", "")
                    if tgt in {"parent", "top", "reply", "*", ""}:
                        child_replies.append((msg, frame.url))
            if child_replies:
                import json as _json
                from webresearch.engine.frames import extract_origin
                from webresearch.engine.runtime import _pump_jobs

                for msg, frame_url in child_replies:
                    m_data = _json.dumps(msg.get("data", ""))
                    m_origin = _json.dumps(extract_origin(frame_url))
                    m_target = _json.dumps(msg.get("target_origin") or msg.get("target") or "*")
                    try:
                        parent_ctx.eval(
                            f"if (typeof window !== 'undefined' && typeof window.__receiveMessage === 'function') window.__receiveMessage({m_data}, {m_origin}, {m_target});"
                        )
                    except Exception:
                        pass
                _pump_jobs(parent_ctx)
                try:
                    updated_html = parent_ctx.eval("__serialize()")
                    if updated_html:
                        html = updated_html
                        result = result.model_copy(update={"body": html})
                except Exception:
                    pass
    captcha = await inspect_captcha(
        html,
        result.final_url or url,
        client,
        frames,
        sprite_endpoint=sprite_endpoint,
    )
    page = snapshot(parse(result), max_chars=max_chars, max_links=max_links)
    page = page.model_copy(update={"headers": dict(result.headers or {})})
    decorated = _decorate(
        page,
        html,
        js_ran,
        js_info,
        frames=frames,
        captcha=captcha.report,
    )
    if return_js_info:
        return (decorated, html, frames, captcha, js_info)
    return (
        decorated,
        html,
        frames,
        captcha,
    )


async def read(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_chars: int = 8000,
    max_links: int = 50,
    js: JsMode = "auto",
) -> Page:
    page, _, _, _ = await read_live(
        url, client=client, max_chars=max_chars, max_links=max_links, js=js
    )
    return page


async def captcha_code(
    url: str,
    *,
    client: httpx.AsyncClient,
    js: JsMode = "auto",
) -> CaptchaCodeDump:
    """One-shot: load URL and dump CAPTCHA widget/iframe HTML if present."""
    _, _, _, inspection = await read_live(url, client=client, max_chars=2000, js=js)
    return dump_captcha_code(inspection)


async def page_from_html(
    html: str,
    *,
    url: str,
    status: int = 200,
    max_chars: int = 8000,
    js_ran: bool = False,
    frames: list[FrameDocument] | None = None,
    captcha: CaptchaReport | None = None,
) -> Page:
    result = FetchResult(
        url=url,
        final_url=url,
        status=status,
        content_type="text/html",
        kind="html",
        body=html,
    )
    page = snapshot(parse(result), max_chars=max_chars)
    return _decorate(page, html, js_ran, None, frames=frames, captcha=captcha)
