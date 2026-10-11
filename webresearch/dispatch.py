"""Run a named browser tool. Shared by the HTTP API and the LLM agent loop."""

from __future__ import annotations

from typing import Any

import httpx

from webresearch.extract import extract as extract_fields
from webresearch.fetch import fetch as http_fetch
from webresearch.parse import parse as parse_doc
from webresearch.read import captcha_code as dump_url_captcha_code
from webresearch.read import read as read_url
from webresearch.search import search as web_search
from webresearch.search import search_images as web_search_images
from webresearch.session import Browser
from webresearch.types import CaptchaReport


async def invoke(http: httpx.AsyncClient, browser: Browser, name: str, args: dict[str, Any]):
    if name == "browser_search":
        engine = args.get("engine") or "auto"
        if engine not in {"auto", "google", "ddg"}:
            engine = "auto"
        return await web_search(args["q"], client=http, max_hits=int(args.get("max_hits", 8)), engine=engine)
    if name == "browser_images":
        return await web_search_images(
            args.get("q") or args.get("query") or "",
            client=http,
            max_hits=int(args.get("max_hits", 8)),
            engine=args.get("engine") or "auto",
        )
    if name == "browser_read":
        js = args.get("js") or "auto"
        if js not in {"auto", "always", "never"}:
            js = "auto"
        return await read_url(
            args["url"],
            client=http,
            max_chars=int(args.get("max_chars", 8000)),
            js=js,
        )
    if name == "browser_extract":
        result = await http_fetch(args["url"], client=http)
        return extract_fields(parse_doc(result), args.get("fields") or {"title": "title"})
    if name == "browser_session_create":
        session = await browser.create()
        return {"id": session.id}
    if name == "browser_goto":
        js = args.get("js") or "auto"
        return await browser.get(args["session_id"]).goto(
            args["url"], max_chars=int(args.get("max_chars", 8000)), js=js
        )
    if name == "browser_open":
        return await browser.get(args["session_id"]).open(int(args["n"]), max_chars=int(args.get("max_chars", 8000)))
    if name == "browser_submit":
        return await browser.get(args["session_id"]).submit(
            int(args["n"]), args.get("fields"), max_chars=int(args.get("max_chars", 8000))
        )
    if name == "browser_inspect":
        targets, challenges = browser.get(args["session_id"]).inspect()
        return {"targets": targets, "challenges": challenges}
    if name == "browser_actions":
        surface = browser.get(args["session_id"]).action_surface(
            max_actions=int(args.get("max_actions", 200))
        )
        return {
            "coverage": surface.coverage_line(),
            "complete": surface.complete,
            "total_found": surface.total_found,
            "returned": len(surface.actions),
            "truncated": surface.truncated,
            "engine_only": surface.engine_only,
            "actions": [
                {
                    "ref": a.ref,
                    "kind": a.kind,
                    "role": a.role,
                    "label": a.label,
                    "target": a.target,
                    "field_name": a.field_name,
                    "checked": a.checked,
                    "disabled": a.disabled,
                    "notes": a.notes,
                    "http_callable": a.callable_without_engine,
                }
                for a in surface.actions
            ],
        }
    if name == "browser_act":
        return await browser.get(args["session_id"]).act(
            args["ref"],
            text=args.get("text") or "",
            dx=args.get("dx"),
            dy=args.get("dy"),
            files=args.get("files"),
            max_chars=int(args.get("max_chars", 8000)),
        )
    if name == "browser_upload":
        payload = args.get("files") or args.get("file_path") or []
        return await browser.get(args["session_id"]).upload(
            args["ref"],
            payload,
            max_chars=int(args.get("max_chars", 8000)),
        )
    if name == "browser_press_key":
        return await browser.get(args["session_id"]).press_key(
            args["ref"],
            args.get("key") or "enter",
            max_chars=int(args.get("max_chars", 8000)),
        )
    if name == "browser_dialogs":
        session = browser.get(args["session_id"])
        return {"dialogs": session.dialogs()}
    if name == "browser_respond_dialog":
        session = browser.get(args["session_id"])
        if args.get("index") is None and not args.get("accept") and not args.get("dismiss"):
            return {"dialogs": session.dialogs()}
        accept = bool(args.get("accept")) if args.get("accept") is not None else not bool(args.get("dismiss"))
        return session.respond_dialog(accept=accept, text=args.get("text") or "")
    if name == "browser_download":
        session = browser.get(args["session_id"])
        rec = await session.download(
            args["url"], expected=args.get("expected"), dest_dir=args.get("dest_dir")
        )
        return {
            "url": rec.url,
            "path": rec.path,
            "bytes": rec.bytes,
            "sha256": rec.sha256,
            "content_type": rec.content_type,
            "verified": rec.verified,
            "scanned": rec.scanned,
            "error": rec.error,
        }
    if name == "browser_query":
        return browser.get(args["session_id"]).query(
            args.get("term") or "", limit=int(args.get("limit", 40))
        )
    if name == "browser_slice":
        return browser.get(args["session_id"]).slice(
            args.get("start") or "",
            args.get("end") or "",
            limit=int(args.get("limit", 120)),
        )
    if name == "browser_frames":
        return browser.get(args["session_id"]).frames()
    if name == "browser_frame_source":
        frame = browser.get(args["session_id"]).frame_source(args["ref"])
        max_chars = int(args.get("max_chars", 20_000))
        html = frame.html
        return {
            "ref": frame.ref,
            "url": frame.url,
            "status": frame.status,
            "html": html[:max_chars],
            "truncated": len(html) > max_chars,
        }
    if name == "browser_captcha":
        session_id = args.get("session_id")
        if session_id:
            return browser.get(session_id).captcha()
        url = args.get("url")
        if not url:
            raise ValueError("browser_captcha requires session_id or url")
        page = await read_url(
            url,
            client=http,
            max_chars=int(args.get("max_chars", 8000)),
            js=args.get("js") or "auto",
        )
        return page.captcha or CaptchaReport(
            present=False,
            note="No supported CAPTCHA or bot challenge was detected.",
            view="CAPTCHA: not detected",
        )
    if name == "browser_captcha_code":
        session_id = args.get("session_id")
        if session_id:
            return browser.get(session_id).captcha_code()
        url = args.get("url")
        if not url:
            raise ValueError("browser_captcha_code requires session_id or url")
        return await dump_url_captcha_code(
            url,
            client=http,
            js=args.get("js") or "auto",
        )
    if name == "browser_captcha_solve":
        session_id = args.get("session_id")
        if not session_id:
            raise ValueError("browser_captcha_solve requires session_id")
        return await browser.get(session_id).solve_captcha(
            model=args.get("model") or "auto",
            max_attempts=int(args.get("max_attempts", 1)),
        )
    if name == "browser_click":
        return await browser.get(args["session_id"]).click(args["ref"], max_chars=int(args.get("max_chars", 8000)))
    if name == "browser_type":
        return await browser.get(args["session_id"]).type(
            args["ref"], args["text"], max_chars=int(args.get("max_chars", 8000))
        )
    if name == "browser_back":
        return browser.get(args["session_id"]).back()
    if name == "browser_scroll":
        return await browser.get(args["session_id"]).scroll(
            y=int(args.get("y", 800)),
            x=int(args.get("x", 0)),
            max_chars=int(args.get("max_chars", 8000)),
        )
    raise ValueError(f"unknown tool: {name}")
