from __future__ import annotations

import os
import secrets
import time
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from webresearch.acd import query_acd, slice_acd
from webresearch.engine import chromium
from webresearch.engine.captcha import (
    CaptchaInspection,
    dump_captcha_code,
    inspect_captcha,
)
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import client_kwargs, fetch
from webresearch.parse import parse
from webresearch.read import _decorate, read_live
from webresearch.search import search as web_search
from webresearch.snapshot import snapshot
from webresearch.types import (
    CaptchaCodeDump,
    CaptchaReport,
    Challenge,
    FetchResult,
    FrameInfo,
    Page,
    SearchResult,
    Target,
)


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
        self.created_at = time.time()
        self.last_used = self.created_at
        self.proxy: str | None = None
        self.chromium_session: chromium.ChromiumSession | None = None

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
        # Lazily create a persistent Chromium session so an escalated SPA keeps
        # one live browser across goto/click/type/scroll.
        if self.chromium_session is None and chromium.available():
            self.chromium_session = chromium.ChromiumSession(
                proxy=self.proxy, headers=dict(self.client.headers)
            )
        page, html, frames, captcha = await read_live(
            url,
            client=self.client,
            max_chars=max_chars,
            js=js,  # type: ignore[arg-type]
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
            chromium_session=self.chromium_session,
        )
        # If the page did NOT escalate, the chromium session is unused; drop it.
        if page.engine != "chromium" and self.chromium_session is not None:
            await self.chromium_session.close()
            self.chromium_session = None
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

        # Dispatch the submit event into the live JS first. If page JS calls
        # preventDefault (validation / SPA-handled), respect it and do NOT do
        # the raw HTTP submit; return the JS-mutated page instead.
        js_outcome = await self._dispatch_form_event(n, fields, max_chars)
        if js_outcome == "prevented":
            return self.page

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

    async def _dispatch_form_event(
        self, n: int, fields: dict[str, str] | None, max_chars: int
    ) -> str:
        """Run page JS: set fields + dispatch submit on form index n-1.

        Returns 'submitted' | 'prevented' | 'missing'. If JS prevented default,
        the session page is updated to the JS-mutated result (and no raw submit
        should follow).
        """
        if not self.html:
            return "missing"
        index = int(n) - 1
        actions: list[dict] = []
        if fields:
            actions.append({"kind": "set_form_fields", "index": index, "fields": fields})
        actions.append({"kind": "submit_form", "index": index})
        new_html, js_info = await run_scripts(
            self.html, self.url, self.client, force=True, actions=actions
        )
        outcome = str(js_info.get("submit_outcome") or "submitted")
        if outcome == "prevented":
            # JS handled submission (validation or SPA). Rebuild page from JS state.
            js_ran = bool(js_info.get("ran"))
            result = FetchResult(
                url=self.url,
                final_url=self.url,
                status=200,
                content_type="text/html",
                kind="html",
                body=new_html,
            )
            frames = (
                await load_frames(new_html, self.url, self.client)
                if new_html
                else self.frame_documents
            )
            captcha = await inspect_captcha(
                new_html,
                self.url,
                self.client,
                frames,
                sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
            )
            page = snapshot(parse(result), max_chars=max_chars)
            page = _decorate(page, new_html, js_ran, js_info, frames=frames, captcha=captcha.report)
            self.frame_documents = frames
            self.captcha_inspection = captcha
            self._push(page, new_html)
        return outcome

    async def click(self, ref: str, *, max_chars: int = 8000) -> Page:
        if not self.html:
            raise ValueError("no current page")
        targets, _ = inspect_html(self.html, self.url)
        target = next((item for item in targets if item.ref == ref), None)
        if target is None:
            # keep legacy fallback for challenge widgets not in inspect list
            html, nav = click_html(self.html, self.url, ref)
            if nav:
                return await self.goto(nav, max_chars=max_chars)
            return await self._apply([], html, max_chars)
        if (
            target.tag == "a"
            and target.href
            and not target.href.startswith("#")
            and not target.href.lower().startswith("javascript:")
        ):
            return await self.goto(target.href, max_chars=max_chars)
        # Chromium lane: real CDP click on the live page, keep browser state.
        if self._use_cdp(target):
            return await self._cdp_click(target, max_chars)
        return await self._apply(
            [{"kind": "click", "path": target.path}], self.html, max_chars
        )

    async def type(self, ref: str, text: str, *, max_chars: int = 8000) -> Page:
        if not self.html:
            raise ValueError("no current page")
        targets, _ = inspect_html(self.html, self.url)
        target = next((item for item in targets if item.ref == ref), None)
        if target is None:
            html = type_html(self.html, ref, text)
            return await self._apply([], html, max_chars)
        if self._use_cdp(target):
            return await self._cdp_type(target, text, max_chars)
        return await self._apply(
            [{"kind": "type", "path": target.path, "text": text}],
            self.html,
            max_chars,
        )

    async def scroll(self, y: int = 800, x: int = 0, *, max_chars: int = 8000) -> Page:
        """Scroll the page; fires scroll event + IntersectionObserver notifications."""
        if not self.html:
            raise ValueError("no current page")
        if self.chromium_session is not None and self.page and self.page.engine == "chromium":
            await self.chromium_session.scroll(y)
            return await self._rebuild_from_chromium(max_chars)
        return await self._apply(
            [{"kind": "scroll", "x": x, "y": y}], self.html, max_chars
        )

    def _use_cdp(self, target: Target) -> bool:
        return (
            self.chromium_session is not None
            and self.page is not None
            and self.page.engine == "chromium"
            and bool(target.path)
        )

    async def _cdp_click(self, target: Target, max_chars: int) -> Page:
        ok = await self.chromium_session.click_path(target.path)  # type: ignore[union-attr]
        if not ok:
            return await self._apply([{"kind": "click", "path": target.path}], self.html, max_chars)
        return await self._rebuild_from_chromium(max_chars)

    async def _cdp_type(self, target: Target, text: str, max_chars: int) -> Page:
        ok = await self.chromium_session.type_path(target.path, text)  # type: ignore[union-attr]
        if not ok:
            return await self._apply(
                [{"kind": "type", "path": target.path, "text": text}], self.html, max_chars
            )
        return await self._rebuild_from_chromium(max_chars)

    async def _rebuild_from_chromium(self, max_chars: int) -> Page:
        """Re-read the live Chromium DOM and rebuild the page model."""
        assert self.chromium_session is not None
        html = await self.chromium_session.content()
        url = self.chromium_session.url or self.url
        self.url = url
        result = FetchResult(
            url=url,
            final_url=url,
            status=200,
            content_type="text/html",
            kind="html",
            body=html,
        )
        frames = (
            await load_frames(html, url, self.client) if html else self.frame_documents
        )
        captcha = await inspect_captcha(
            html,
            url,
            self.client,
            frames,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        page = snapshot(parse(result), max_chars=max_chars)
        page = _decorate(
            page, html, True, {"engine": "chromium", "ran": True, "scripts": 1},
            frames=frames, captcha=captcha.report,
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        return self._push(page, html)

    async def _apply(
        self, actions: list[dict], html: str, max_chars: int
    ) -> Page:
        """Re-run page JS with agent interactions, then rebuild the page model."""
        new_html, js_info = await run_scripts(
            html, self.url, self.client, force=True, actions=actions
        )
        js_ran = bool(js_info.get("ran"))
        result = FetchResult(
            url=self.url,
            final_url=self.url,
            status=200,
            content_type="text/html",
            kind="html",
            body=new_html,
        )
        frames = (
            await load_frames(new_html, self.url, self.client)
            if new_html
            else self.frame_documents
        )
        captcha = await inspect_captcha(
            new_html,
            self.url,
            self.client,
            frames,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        page = snapshot(parse(result), max_chars=max_chars)
        page = _decorate(
            page,
            new_html,
            js_ran,
            js_info,
            frames=frames,
            captcha=captcha.report,
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        return self._push(page, new_html)

    def inspect(self) -> tuple[list[Target], list[Challenge]]:
        if not self.html:
            raise ValueError("no current page")
        return inspect_html(self.html, self.url)

    def query(self, term: str, *, limit: int = 40) -> list[dict]:
        page = self.page
        if page is None or page.acd is None:
            raise ValueError("no ACD for the current page")
        return [block.model_dump() for block in query_acd(page.acd, term, limit=limit)]

    def slice(self, start: str = "", end: str = "", *, limit: int = 120) -> list[dict]:
        page = self.page
        if page is None or page.acd is None:
            raise ValueError("no ACD for the current page")
        return [block.model_dump() for block in slice_acd(page.acd, start, end, limit=limit)]

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
        if self.chromium_session is not None:
            import contextlib

            with contextlib.suppress(Exception):
                await self.chromium_session.close()
            self.chromium_session = None
        await self.client.aclose()


class Browser:
    """Isolated agent sessions (cookie jars). Bounded + TTL-evicted."""

    def __init__(
        self,
        *,
        max_sessions: int = 64,
        ttl_seconds: float = 1800.0,
        proxies: list[str] | None = None,
    ) -> None:
        self.sessions: dict[str, Session] = {}
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        if proxies is None:
            env = os.environ.get("WR_PROXIES", "")
            proxies = [p.strip() for p in env.split(",") if p.strip()]
        self.proxies = proxies
        self._proxy_index = 0

    def _next_proxy(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._proxy_index % len(self.proxies)]
        self._proxy_index += 1
        return proxy

    async def create(self, *, headers: dict[str, str] | None = None, proxy: str | None = None) -> Session:
        await self._evict()
        sid = "s_" + secrets.token_hex(4)
        # Explicit proxy wins; otherwise rotate through the pool for isolation.
        chosen = proxy if proxy is not None else self._next_proxy()
        client = httpx.AsyncClient(**client_kwargs(headers=headers, proxy=chosen))
        session = Session(client, sid)
        session.proxy = chosen
        self.sessions[sid] = session
        return session

    async def _evict(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, session in self.sessions.items()
            if now - session.last_used > self.ttl_seconds
        ]
        for sid in expired:
            await self.close(sid)
        overflow = len(self.sessions) - self.max_sessions + 1
        if overflow > 0:
            oldest = sorted(self.sessions, key=lambda s: self.sessions[s].last_used)
            for sid in oldest[:overflow]:
                await self.close(sid)

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        session.last_used = time.time()
        return session

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            await self.close(sid)
