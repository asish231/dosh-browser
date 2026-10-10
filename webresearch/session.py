from __future__ import annotations

import os
import re
import secrets
import time
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx

from webresearch.acd import query_acd, slice_acd
from webresearch.actions import Action, ActionSurface, collect
from webresearch.engine.captcha import (
    CaptchaInspection,
    dump_captcha_code,
    inspect_captcha,
)
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import close_impersonated, fetch, make_client
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


from webresearch.profile import Profile


class Tab:
    """An open tab in a Session with its own navigation history and DOM state."""

    def __init__(self, tab_id: str) -> None:
        self.id = tab_id
        self.history: list[Page] = []
        self.index: int = -1
        self.html: str = ""
        self.url: str = ""
        self.headers: dict = {}
        self.frame_documents: list[FrameDocument] = []
        self.captcha_inspection: CaptchaInspection | None = None

    @property
    def page(self) -> Page | None:
        if 0 <= self.index < len(self.history):
            return self.history[self.index]
        return None

    def push(self, page: Page, html: str | None = None, headers: dict | None = None) -> Page:
        if html is not None:
            self.html = html
            self.url = page.url
        if headers is not None:
            self.headers = dict(headers)
        self.history = self.history[: self.index + 1]
        self.history.append(page)
        self.index = len(self.history) - 1
        return page


class Session:
    """Agent session: cookies, multi-tab history, live HTML, inspect/click/type."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        profile: str | Profile | None = None,
    ) -> None:
        self.id = session_id
        self.client = client
        self.profile = Profile(profile) if isinstance(profile, str) else profile
        if self.profile is not None:
            self.profile.apply_to_client(self.client)

        self._tabs: dict[str, Tab] = {}
        initial_tab = Tab("tab_1")
        self._tabs["tab_1"] = initial_tab
        self.active_tab_id = "tab_1"

        self.created_at = time.time()
        self.last_used = self.created_at
        self.proxy: str | None = None

    @property
    def current_tab(self) -> Tab:
        if self.active_tab_id not in self._tabs:
            self._tabs[self.active_tab_id] = Tab(self.active_tab_id)
        return self._tabs[self.active_tab_id]

    @property
    def tabs(self) -> list[dict[str, Any]]:
        """List all open tabs in this session."""
        return [
            {
                "id": tab.id,
                "title": tab.page.title if tab.page else "",
                "url": tab.url,
                "active": tab.id == self.active_tab_id,
            }
            for tab in self._tabs.values()
        ]

    def new_tab(self, tab_id: str | None = None) -> str:
        """Open a new blank tab and switch to it."""
        tid = tab_id or f"tab_{len(self._tabs) + 1}"
        self._tabs[tid] = Tab(tid)
        self.active_tab_id = tid
        return tid

    def switch_tab(self, tab_id: str) -> Page | None:
        """Switch active tab to tab_id."""
        if tab_id not in self._tabs:
            raise ValueError(f"no tab [{tab_id}]")
        self.active_tab_id = tab_id
        return self.page

    def close_tab(self, tab_id: str) -> None:
        """Close an open tab."""
        if len(self._tabs) <= 1:
            raise ValueError("cannot close the only open tab")
        if tab_id not in self._tabs:
            raise ValueError(f"no tab [{tab_id}]")
        del self._tabs[tab_id]
        if self.active_tab_id == tab_id:
            self.active_tab_id = next(iter(self._tabs.keys()))

    @property
    def history(self) -> list[Page]:
        return self.current_tab.history

    @history.setter
    def history(self, val: list[Page]) -> None:
        self.current_tab.history = val

    @property
    def index(self) -> int:
        return self.current_tab.index

    @index.setter
    def index(self, val: int) -> None:
        self.current_tab.index = val

    @property
    def html(self) -> str:
        return self.current_tab.html

    @html.setter
    def html(self, val: str) -> None:
        self.current_tab.html = val

    @property
    def url(self) -> str:
        return self.current_tab.url

    @url.setter
    def url(self, val: str) -> None:
        self.current_tab.url = val

    @property
    def frame_documents(self) -> list[FrameDocument]:
        return self.current_tab.frame_documents

    @frame_documents.setter
    def frame_documents(self, val: list[FrameDocument]) -> None:
        self.current_tab.frame_documents = val

    @property
    def captcha_inspection(self) -> CaptchaInspection | None:
        return self.current_tab.captcha_inspection

    @captcha_inspection.setter
    def captcha_inspection(self, val: CaptchaInspection | None) -> None:
        self.current_tab.captcha_inspection = val

    @property
    def page(self) -> Page | None:
        return self.current_tab.page

    def _push(self, page: Page, html: str | None = None) -> Page:
        return self.current_tab.push(page, html, headers=getattr(page, "headers", None))

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
        if self.profile is not None:
            self.profile.save_from_client(self.client)
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
        # Hidden fields are reported by name without their value so the model
        # never sees a session blob it might try to invent or echo. A real
        # submit still needs them, so pull the live values from the document.
        if self.html:
            from webresearch.parse import parse as parse_document
            from webresearch.forms import hidden_values as read_hidden_values
            from webresearch.types import FetchResult as _FetchResult

            live = read_hidden_values(
                parse_document(
                    _FetchResult(
                        url=self.url,
                        final_url=self.url,
                        status=current.status,
                        content_type=current.content_type,
                        kind=current.kind,
                        body=self.html,
                    )
                ),
                n,
            )
            for key, value in live.items():
                payload.setdefault(key, value)
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
                headers=result.headers,
            )
            js_ran = bool(js_info.get("ran"))
            result = result.model_copy(update={"body": html})
        frames = (
            await load_frames(html, result.final_url or form.action, self.client, parent_headers=result.headers)
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
        if js_info.get("navigated"):
            nav_url = urljoin(self.url, js_info["navigated"])
            await self.goto(nav_url, max_chars=max_chars)
            return "prevented"
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
                await load_frames(
                    new_html,
                    self.url,
                    self.client,
                    parent_headers=self.current_tab.headers,
                    parent_messages=js_info.get("post_messages"),
                )
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
        return await self._apply(
            [{"kind": "type", "path": target.path, "text": text}],
            self.html,
            max_chars,
        )

    # -- action-surface driven navigation -------------------------------
    # The `[eN]` refs above are numbered in document order and stop at 80, so
    # an agent cannot address the rest of a real page. The action surface below
    # uses content-addressed refs and discloses its own budget instead.

    def action_surface(self, *, max_actions: int = 200) -> ActionSurface:
        return collect(self.html, self.url, max_actions=max_actions)

    def actions(self, *, max_actions: int = 200) -> list[Action]:
        """Convenience alias returning the list of Action objects on the current surface."""
        return self.action_surface(max_actions=max_actions).actions

    def network_log(self) -> list[dict]:
        """Network Tab: Returns all API and subresource requests observed on current page."""
        if self.page and self.page.acd and self.page.acd.api:
            return [api.model_dump() for api in self.page.acd.api]
        return []

    def error_log(self) -> list[str]:
        """Console Tab: Returns all JavaScript errors and exceptions observed on current page."""
        if self.page and self.page.acd and self.page.acd.errors:
            return list(self.page.acd.errors)
        return []

    async def act(
        self,
        ref: str,
        *,
        text: str = "",
        dx: float | None = None,
        dy: float | None = None,
        max_chars: int = 8000,
    ) -> Page:
        """Carry out one action from the surface by ref.

        Dispatches on the action's kind, so the caller does not need to know
        whether a control is a link, a form field, a bare button, or a drag
        handle. Controls that only a real engine can actuate say so rather than
        failing obscurely.
        """
        surface = self.action_surface(max_actions=100_000)
        action = next((a for a in surface.actions if a.ref == ref), None)
        if action is None:
            known = ", ".join(sorted(a.ref for a in surface.actions[:8]))
            raise ValueError(
                f"no action [{ref}]. Surface has {len(surface.actions)} action(s); "
                f"first refs: {known}"
            )

        if action.kind == "navigate":
            return await self.goto(action.target, max_chars=max_chars)

        if action.kind == "submit":
            if not text:
                if action.role == "button" or action.tag == "button" or (action.tag == "input" and not action.field_name):
                    return await self._act_engine(action, text="", dx=dx, dy=dy, max_chars=max_chars)
                raise ValueError(
                    f"[{ref}] {action.role!r} is a form field and needs text"
                )
            return await self._act_submit_field(action, text, max_chars)

        if action.disabled:
            raise ValueError(f"[{ref}] {action.label!r} is disabled")

        if action.kind == "dropzone":
            raise ValueError(
                f"[{ref}] is a drag goal, not a control; drag a handle onto it"
            )

        if action.kind in {"activate", "drag", "embed"}:
            return await self._act_engine(action, text=text, dx=dx, dy=dy, max_chars=max_chars)

        raise ValueError(f"[{ref}] has unsupported kind {action.kind!r}")

    async def _act_submit_field(
        self, action: Action, text: str, max_chars: int
    ) -> Page:
        """Fill a named field and submit the form that owns it."""
        from webresearch.forms import forms as extract_forms
        from webresearch.parse import parse
        from webresearch.types import FetchResult

        match = re.match(r"form\[(\d+)\]", action.target)
        if not match:
            raise ValueError(f"malformed form target {action.target!r}")
        index = int(match.group(1))
        current = self.page
        forms = extract_forms(
            parse(
                FetchResult(
                    url=self.url,
                    final_url=self.url,
                    status=current.status if current else 200,
                    content_type=current.content_type if current else "text/html",
                    kind=current.kind if current else "html",
                    body=self.html,
                )
            )
        )
        if not 1 <= index <= len(forms):
            raise ValueError(f"form {index} is out of range")
        # A field with no name cannot be addressed over HTTP: the payload would
        # have no key to send it under. Say so rather than guessing.
        name = action.field_name
        if not name:
            raise ValueError(
                f"[{action.ref}] {action.label!r} has no name attribute, so an HTTP "
                "submit cannot target it; use the engine lane"
            )
        return await self.submit(index, {name: text}, max_chars=max_chars)

    async def _act_engine(
        self,
        action: Action,
        *,
        text: str,
        dx: float | None,
        dy: float | None,
        max_chars: int,
    ) -> Page:
        """Actuate a control natively in the QuickJS mini-DOM.

Actions carry a CSS selector rather than a child-index path, because the action
surface addresses elements the way a page describes them and the mini-DOM can
resolve a selector directly.
"""
        selector = action.selector or action.target
        if action.kind == "drag":
            return await self._apply(
                [
                    {
                        "kind": "drag",
                        "selector": selector,
                        "dx": dx if dx is not None else 220.0,
                        "dy": dy if dy is not None else 0.0,
                    }
                ],
                self.html,
                max_chars,
            )
        elif action.kind in {"activate", "submit"}:
            if text:
                return await self._apply(
                    [{"kind": "type", "selector": selector, "text": text}],
                    self.html,
                    max_chars,
                )
            return await self._apply(
                [{"kind": "activate", "selector": selector}],
                self.html,
                max_chars,
            )
        else:  # embed
            raise ValueError(
                f"[{action.ref}] is embedded content at {selector!r}; "
                "read its own document instead of acting on it"
            )

    async def scroll(self, y: int = 800, x: int = 0, *, max_chars: int = 8000) -> Page:
        """Scroll the page; fires scroll event + IntersectionObserver notifications."""
        if not self.html:
            raise ValueError("no current page")
        return await self._apply(
            [{"kind": "scroll", "x": x, "y": y}], self.html, max_chars
        )

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
            await load_frames(
                new_html,
                self.url,
                self.client,
                parent_headers=self.current_tab.headers,
                parent_messages=js_info.get("post_messages"),
            )
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
        self._last_deltas = js_info.get("deltas", [])
        if self.profile is not None:
            self.profile.save_from_client(self.client)
        if js_info.get("navigated"):
            nav_url = urljoin(self.url, js_info["navigated"])
            return await self.goto(nav_url, max_chars=max_chars)
        if js_info.get("form_submit"):
            fs = js_info["form_submit"]
            act_url = urljoin(self.url, fs.get("action") or "")
            method = fs.get("method", "POST").upper()
            fields = fs.get("fields", {})
            if method == "GET":
                parsed = urlparse(act_url)
                target = urlunparse(parsed._replace(query=urlencode(fields)))
                return await self.goto(target, max_chars=max_chars)
            else:
                result = await fetch(act_url, client=self.client, method="POST", data=fields)
                dest_url = result.final_url or act_url
                res_html = result.body
                js_info_post: dict = {}
                if result.kind == "html":
                    res_html, js_info_post = await run_scripts(
                        res_html, dest_url, self.client, headers=result.headers
                    )
                    result = result.model_copy(update={"body": res_html})
                page = snapshot(parse(result), max_chars=max_chars)
                page = _decorate(
                    page,
                    res_html,
                    bool(js_info_post.get("ran")) if result.kind == "html" else False,
                    js_info_post if result.kind == "html" else None,
                )
                return self._push(page, res_html)
        return self._push(page, new_html)

    @property
    def last_deltas(self) -> list[dict]:
        """Return raw DOM mutation deltas captured during the most recent action."""
        return getattr(self, "_last_deltas", [])

    def get_last_delta_patch(self) -> bytes:
        """Encode the most recent mutation deltas as an ANBF binary delta patch."""
        from webresearch.engine.binary import AnbDelta, pack_delta_patch

        anb_deltas = [
            AnbDelta(
                kind=d.get("kind", "mutate"),
                target_id=d.get("target_id", "node"),
                content=d.get("content", ""),
            )
            for d in self.last_deltas
        ]
        return pack_delta_patch(anb_deltas)

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
        if self.profile is not None:
            self.profile.save_from_client(self.client)
        await close_impersonated(self.client)
        await self.client.aclose()


class Browser:
    """Isolated agent sessions (cookie jars). Bounded + TTL-evicted."""

    def __await__(self):
        """Allow ``session = await Browser()`` as shorthand for creating one session."""
        return self.create().__await__()

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

    async def create(
        self,
        *,
        headers: dict[str, str] | None = None,
        proxy: str | None = None,
        profile: str | Profile | None = None,
    ) -> Session:
        await self._evict()
        sid = "s_" + secrets.token_hex(4)
        # Explicit proxy wins; otherwise rotate through the pool for isolation.
        chosen = proxy if proxy is not None else self._next_proxy()
        client = make_client(headers=headers, proxy=chosen)
        session = Session(client, sid, profile=profile)
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
