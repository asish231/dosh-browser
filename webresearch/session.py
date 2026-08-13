from __future__ import annotations

import secrets
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from webresearch.engine.captcha import CaptchaInspection, dump_captcha_code, inspect_captcha
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import client_kwargs, fetch
from webresearch.parse import parse
from webresearch.read import page_from_html, read_live
from webresearch.search import search as web_search
from webresearch.snapshot import snapshot
from webresearch.types import CaptchaCodeDump, CaptchaReport, Challenge, FrameInfo, Page, SearchResult, Target


class Session:
    """Agent session: cookies, history, live HTML, inspect/click/type."""

    def __init__(self, client: httpx.AsyncClient, session_id: str) -> None:
        self.id = session_id
        self.client = client
        self.history: list[Page] = []
        self.index = -1
        self.html = ""
        self.url = ""
        self.frame_documents: list[FrameDocument] = []
        self.captcha_inspection: CaptchaInspection | None = None

    @property
    def page(self) -> Page | None:
        if 0 <= self.index < len(self.history):
            return self.history[self.index]
        return None

    def _push(self, page: Page, html: str | None = None) -> Page:
        if html is not None:
            self.html = html
            self.url = page.url
        self.history = self.history[: self.index + 1]
        self.history.append(page)
        self.index = len(self.history) - 1
        return page

    async def goto(self, url: str, *, max_chars: int = 8000, js: str = "auto") -> Page:
        page, html, frames, captcha = await read_live(
            url,
            client=self.client,
            max_chars=max_chars,
            js=js,  # type: ignore[arg-type]
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        return self._push(page, html)

    async def open(self, n: int, *, max_chars: int = 8000) -> Page:
        current = self.page
        if current is None:
            raise ValueError("no current page")
        match = next((link for link in current.links if link.n == n), None)
        if match is None:
            raise ValueError(f"no link [{n}] on this page")
        return await self.goto(match.url, max_chars=max_chars)

    async def submit(self, n: int, fields: dict[str, str] | None = None, *, max_chars: int = 8000) -> Page:
        current = self.page
        if current is None:
            raise ValueError("no current page")
        form = next((item for item in current.forms if item.n == n), None)
        if form is None:
            raise ValueError(f"no form [{n}] on this page")
        payload = {item.name: item.value for item in form.fields}
        if fields:
            payload.update(fields)
        payload = {k: v for k, v in payload.items() if k}
        if form.method == "GET":
            parsed = urlparse(form.action)
            url = urlunparse(parsed._replace(query=urlencode(payload)))
            result = await fetch(url, client=self.client)
        else:
            result = await fetch(form.action, client=self.client, method="POST", data=payload)
        html = result.body
        js_info: dict | None = None
        js_ran = False
        if result.kind == "html":
            html, js_info = await run_scripts(
                html,
                result.final_url or form.action,
                self.client,
            )
            js_ran = bool(js_info.get("ran"))
            result = result.model_copy(update={"body": html})
        frames = (
            await load_frames(html, result.final_url or form.action, self.client)
            if result.kind == "html"
            else []
        )
        captcha = await inspect_captcha(
            html,
            result.final_url or form.action,
            self.client,
            frames,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        page = snapshot(parse(result), max_chars=max_chars)
        from webresearch.read import _decorate

        page = _decorate(
            page,
            html,
            js_ran,
            js_info,
            frames=frames,
            captcha=captcha.report,
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        return self._push(page, html)

    async def click(self, ref: str, *, max_chars: int = 8000) -> Page:
        if not self.html:
            raise ValueError("no current page")
        html, nav = click_html(self.html, self.url, ref)
        if nav:
            return await self.goto(nav, max_chars=max_chars)
        captcha = await inspect_captcha(
            html,
            self.url,
            self.client,
            self.frame_documents,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        self.captcha_inspection = captcha
        page = await page_from_html(
            html,
            url=self.url,
            max_chars=max_chars,
            js_ran=bool(self.page and self.page.js_ran),
            frames=self.frame_documents,
            captcha=captcha.report,
        )
        return self._push(page, html)

    async def type(self, ref: str, text: str, *, max_chars: int = 8000) -> Page:
        if not self.html:
            raise ValueError("no current page")
        html = type_html(self.html, ref, text)
        captcha = await inspect_captcha(
            html,
            self.url,
            self.client,
            self.frame_documents,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        self.captcha_inspection = captcha
        page = await page_from_html(
            html,
            url=self.url,
            max_chars=max_chars,
            js_ran=bool(self.page and self.page.js_ran),
            frames=self.frame_documents,
            captcha=captcha.report,
        )
        return self._push(page, html)

    def inspect(self) -> tuple[list[Target], list[Challenge]]:
        if not self.html:
            raise ValueError("no current page")
        return inspect_html(self.html, self.url)

    def frames(self) -> list[FrameInfo]:
        if not self.html:
            raise ValueError("no current page")
        return frame_infos(self.frame_documents)

    def frame_source(self, ref: str) -> FrameDocument:
        if not self.html:
            raise ValueError("no current page")
        frame = next((item for item in self.frame_documents if item.ref == ref), None)
        if frame is None:
            raise ValueError(f"no frame [{ref}]")
        return frame

    def captcha(self) -> CaptchaReport:
        if not self.html:
            raise ValueError("no current page")
        if self.captcha_inspection is None:
            return CaptchaReport(
                present=False,
                note="No CAPTCHA inspection has run for this page.",
                view="CAPTCHA: not inspected",
            )
        return self.captcha_inspection.report

    def captcha_sprite(self) -> tuple[bytes, str]:
        if self.captcha_inspection is None or self.captcha_inspection.sprite is None:
            raise ValueError("no CAPTCHA sprite is available")
        content_type = self.captcha_inspection.report.sprite_content_type or "application/octet-stream"
        return self.captcha_inspection.sprite, content_type

    def captcha_code(self) -> CaptchaCodeDump:
        if not self.html:
            raise ValueError("no current page")
        if self.captcha_inspection is None:
            return CaptchaCodeDump(
                present=False,
                note="No CAPTCHA inspection has run for this page.",
                code="CAPTCHA CODE: not inspected",
            )
        return dump_captcha_code(self.captcha_inspection)

    def back(self) -> Page:
        if self.index <= 0:
            raise ValueError("no previous page")
        self.index -= 1
        page = self.page
        assert page is not None
        return page

    async def search(self, query: str, *, engine: str = "auto") -> SearchResult:
        return await web_search(query, client=self.client, engine=engine)  # type: ignore[arg-type]

    async def close(self) -> None:
        await self.client.aclose()


class Browser:
    """Isolated agent sessions (cookie jars)."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(self, *, headers: dict[str, str] | None = None, proxy: str | None = None) -> Session:
        sid = "s_" + secrets.token_hex(4)
        client = httpx.AsyncClient(**client_kwargs(headers=headers, proxy=proxy))
        session = Session(client, sid)
        self.sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            await self.close(sid)
