from __future__ import annotations

from typing import Literal

import httpx

from webresearch.acd import build_acd, error_summary
from webresearch.engine.captcha import (
    CaptchaInspection,
    dump_captcha_code,
    inspect_captcha,
)
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.inspect import agent_view_extras, inspect_html
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import _impersonation_allowed, fetch
from webresearch.parse import parse
from webresearch.snapshot import snapshot
from webresearch.types import CaptchaCodeDump, CaptchaReport, FetchResult, Page

JsMode = Literal["auto", "always", "never"]


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

    if result.kind == "html":
        from webresearch.consent import dismiss_consent_dom

        clean_html, dismissed = dismiss_consent_dom(html)
        if dismissed:
            html = clean_html
            result = result.model_copy(update={"body": html})

    frames = (
        await load_frames(
            html,
            result.final_url or url,
            client,
            js=js,
            parent_headers=result.headers,
            parent_messages=(js_info.get("post_messages") if js_info else None),
        )
        if result.kind == "html"
        else []
    )
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
