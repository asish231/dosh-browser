from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, urlunparse

import httpx

from webresearch.acd import query_acd, slice_acd
from webresearch.actions import Action, ActionSurface, collect
from webresearch.engine.captcha import (
    CaptchaInspection,
    CaptchaSolveResult,
    dump_captcha_code,
    inspect_captcha,
    solve_captcha,
)
from webresearch.engine.frames import FrameDocument, frame_infos, load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import MAX_UPLOAD_B64_CHARS, run_scripts
from webresearch.fetch import close_impersonated, fetch, make_client
from webresearch.parse import parse
from webresearch.read import _decorate, read_live
from webresearch.search import search as web_search
from webresearch.snapshot import snapshot
from webresearch.types import (
    CaptchaCodeDump,
    CaptchaReport,
    CaptchaSolution,
    Challenge,
    FetchResult,
    FrameInfo,
    Page,
    SearchResult,
    Target,
)


from webresearch.profile import Profile
from webresearch.engine.login import MAX_OTP_ATTEMPTS, OtpCheckpoint

DOWNLOAD_CONTENT_TYPES = (
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/gzip",
    "application/x-tar",
    "application/octet-stream",
    "application/msword",
    "application/vnd.",
    "application/vnd.openxmlformats",
    "image/",
    "video/",
    "audio/",
    "font/",
    "application/wasm",
)


def _is_download_response(kind: str, content_type: str, disposition: str) -> bool:
    """True when a navigation response is a file, not a document."""
    if kind != "other":
        return False
    disposition = (disposition or "").lower()
    if "attachment" in disposition:
        return True
    ctype = (content_type or "").split(";")[0].strip().lower()
    if not ctype:
        return False
    if ctype.startswith(DOWNLOAD_CONTENT_TYPES):
        return True
    if ctype.startswith("text/"):
        return False
    # Unknown non-text types are treated as files rather than broken pages.
    return True


class Tab:
    """An open tab in a Session with its own navigation history and DOM state."""

    def __init__(self, tab_id: str) -> None:
        self.id = tab_id
        self.history: list[Page] = []
        # Parallel snapshot per history entry: the DOM/headers/frames state that
        # entry represented, so back()/forward() can restore a live document
        # instead of leaving the engine on the navigated-away page.
        self.snapshots: list[dict] = []
        # Absolute scroll position to re-apply after the next fresh engine pass
        # (history restore replays page scripts, then wheels back to where the
        # entry was left).
        self.pending_scroll_y: int = 0
        self.index: int = -1
        self.html: str = ""
        self.url: str = ""
        self.headers: dict = {}
        self.frame_documents: list[FrameDocument] = []
        self.captcha_inspection: CaptchaInspection | None = None
        self.ctx: Any | None = None

    @property
    def page(self) -> Page | None:
        if 0 <= self.index < len(self.history):
            return self.history[self.index]
        return None

    def push(
        self, page: Page, html: str | None = None, headers: dict | None = None, replace: bool = False
    ) -> Page:
        if html is not None:
            self.html = html
            self.url = page.url
        if headers is not None:
            self.headers = dict(headers)
        self.history = self.history[: self.index + 1]
        del self.snapshots[self.index + 1 :]
        if replace:
            # An in-page action (scroll/type/mutation) that stayed on this
            # document rewrites the current entry, like a real browser: these
            # never create history entries.
            if self.history:
                self.history[self.index] = page
                pushed_page = self.history[self.index]
            else:
                self.history.append(page)
                self.index = len(self.history) - 1
                pushed_page = self.history[self.index]
        else:
            self.history.append(page)
            self.index = len(self.history) - 1
            pushed_page = page
        # Capture the state this entry represents. Entries pushed without an
        # HTML payload (synthetic API pages) record None: there is no live
        # document behind them, and restoring one honestly clears the DOM.
        # scroll_y is read at push time — the ctx may be further mutated by
        # later entries, but the entry's position is fixed now.
        scroll_y = 0
        if html is not None and self.ctx is not None:
            try:
                scroll_y = int(float(self.ctx.eval("scrollY || 0") or 0))
            except Exception:
                scroll_y = 0
        snap = {
            "html": self.html if html is not None else None,
            "url": page.url,
            "headers": dict(self.headers),
            "frames": list(self.frame_documents) if html is not None else [],
            "captcha": self.captcha_inspection if html is not None else None,
            "scroll_y": scroll_y,
        }
        if replace and self.snapshots:
            self.snapshots[self.index] = snap
        else:
            self.snapshots.append(snap)
        return pushed_page


class Session:
    """Agent session: cookies, multi-tab history, live HTML, inspect/click/type."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        session_id: str,
        profile: str | Profile | None = None,
        tenant_id: str = "default",
    ) -> None:
        self.id = session_id
        self.tenant_id = tenant_id
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
        self.stealth: bool = False
        # Per-run agent-visible state from the last JS pass.
        self._last_dialogs: list[dict] = []
        self._last_popups: list[dict] = []
        self._popup_depth = 0
        # Login/OTP checkpoints: memory only. The secret never touches disk.
        self._otp_checkpoints: dict[str, OtpCheckpoint] = {}
        self._last_login_wall: dict[str, Any] | None = None
        self._last_login_verdict: str = ""

    def set_stealth(self, enabled: bool) -> bool:
        """Enable or disable humanised stealth mode (biological kinematics, typing pauses, smooth scroll)."""
        self.stealth = bool(enabled)
        return self.stealth

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
        self._tabs[tab_id].ctx = None
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
        # Numeric history moves restore the live document (back/forward).
        self._goto_history_index(val)

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

    def _push(self, page: Page, html: str | None = None, replace: bool = False) -> Page:
        return self.current_tab.push(
            page, html, headers=getattr(page, "headers", None), replace=replace
        )

    async def goto(self, url: str, *, max_chars: int = 8000, js: str = "auto") -> Page:
        page, html, frames, captcha, js_info = await read_live(
            url,
            client=self.client,
            max_chars=max_chars,
            js=js,  # type: ignore[arg-type]
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
            return_js_info=True,
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        self.current_tab.ctx = js_info.get("ctx") if js_info else None
        self._last_dialogs = list((js_info or {}).get("dialogs") or [])
        self._last_popups = list((js_info or {}).get("popups") or [])
        if self.profile is not None:
            self.profile.save_from_client(self.client)

        # A navigation that lands on a file (attachment/binary) is a download,
        # not a broken page: stream it to disk and say where it went.
        disposition = page.headers.get("content-disposition", "")
        if _is_download_response(page.kind, page.content_type, disposition):
            rec = await self.download(url, expected=None)
            if rec.path:
                note = (
                    f"DOWNLOAD: {rec.bytes} bytes saved to {rec.path}\n"
                    f"  url: {url}\n"
                    f"  content_type: {rec.content_type}\n"
                    f"  sha256: {rec.sha256}\n"
                    "  malware verdict: not scanned (no verdict engine; hash is provided)"
                )
                page = page.model_copy(update={"view": note, "chars": len(note)})
            elif rec.error:
                note = f"DOWNLOAD FAILED: {rec.error}\n  url: {url}"
                page = page.model_copy(update={"view": note, "chars": len(note)})
            pushed = self._push(page, html)
            await self._load_popup_tabs((js_info or {}).get("popups") or [])
            return pushed

        pushed = self._push(page, html)
        await self._load_popup_tabs((js_info or {}).get("popups") or [])
        return pushed

    async def download(self, url: str, *, expected: str | None = None, dest_dir: str | None = None):
        """Fetch a file with the session's cookies and save it to disk.

        Returns download.py's Download record: path, byte count, sha256,
        content type, and hash verification when ``expected`` is provided.
        """
        from webresearch.download import download as _download

        absolute = url if url.startswith(("http://", "https://")) or not self.url else urljoin(self.url, url)
        return await _download(absolute, self.client, expected=expected, dest_dir=dest_dir)

    async def _load_popup_tabs(self, popups: list[dict]) -> None:
        """window.open targets become real tabs; the agent stays on this page."""
        if not popups or self._popup_depth >= 2:
            return
        self._popup_depth += 1
        try:
            for pop in popups[:4]:
                target = urljoin(self.url, str(pop.get("url") or ""))
                if not target.startswith(("http://", "https://")):
                    continue
                prev_active = self.active_tab_id
                tid = f"tab_{len(self._tabs) + 1}"
                while tid in self._tabs:
                    tid += "_"
                self.new_tab(tid)
                with contextlib.suppress(Exception):
                    await self.goto(target, max_chars=2000)
                self.active_tab_id = prev_active
        finally:
            self._popup_depth -= 1

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

        # The engine's form.submit() records the LIVE form state in
        # __WR_FORM_SUBMIT: typed values, toggled checkboxes, selected
        # options, checked radios, file inputs — everything page JS and the
        # agent's set_form_fields pass mutated. The old code ignored it and
        # re-parsed the ORIGINAL html, so any engine-side change was silently
        # lost on the wire. Use the engine state when present; caller-passed
        # fields always win. Payload is a pair list so repeated names
        # (multiple selects) survive to the wire.
        engine_fs: dict | None = None
        if js_outcome == "submitted":
            info = getattr(self, "_last_form_js_info", None) or {}
            fs = info.get("form_submit")
            if isinstance(fs, dict) and fs.get("fields"):
                engine_fs = fs

        upload_files = None
        if engine_fs is not None:
            method = str(engine_fs.get("method") or form.method or "POST").upper()
            action = urljoin(self.url, str(engine_fs.get("action") or form.action or ""))
            # Engine multi-select values arrive as arrays (a JS object cannot
            # repeat keys); expand them back into one pair per value.
            pairs: list[tuple[str, str]] = []
            for k, v in (engine_fs.get("fields") or {}).items():
                if not k:
                    continue
                if isinstance(v, list):
                    pairs.extend((str(k), str(item)) for item in v)
                else:
                    pairs.append((str(k), str(v)))
            if fields:
                override = set(fields)
                pairs = [(k, v) for k, v in pairs if k not in override]
                pairs.extend((str(k), str(v)) for k, v in fields.items() if k)
            files_map = engine_fs.get("files") or {}
            if files_map:
                upload_files = []
                for f_name, f_list in files_map.items():
                    for f_item in f_list:
                        b64_c = f_item.get("contentB64") or f_item.get("content_b64")
                        if b64_c:
                            # Binary files round-trip as base64; decode to the
                            # exact original bytes for the multipart POST.
                            raw_bytes = base64.b64decode(b64_c)
                        else:
                            raw_c = f_item.get("content", "")
                            raw_bytes = raw_c.encode("utf-8") if isinstance(raw_c, str) else bytes(raw_c)
                        upload_files.append(
                            (
                                f_name,
                                (
                                    f_item.get("name", "file"),
                                    raw_bytes,
                                    f_item.get("type", "application/octet-stream"),
                                ),
                            )
                        )
        else:
            method = form.method
            action = form.action
            pairs = [(item.name, item.value) for item in form.fields if item.name]
            if fields:
                override = set(fields)
                pairs = [(k, v) for k, v in pairs if k not in override]
                pairs.extend((str(k), str(v)) for k, v in fields.items() if k)
            # Hidden fields are reported by name without their value so the
            # model never sees a session blob it might try to invent or echo.
            # A real submit still needs them, so pull the live values from the
            # document.
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
                present = {k for k, _ in pairs}
                for key, value in live.items():
                    if key and key not in present:
                        pairs.append((key, value))

        if method == "GET":
            parsed = urlparse(action)
            url = urlunparse(parsed._replace(query=urlencode(pairs)))
            result = await fetch(url, client=self.client)
        else:
            result = await fetch(action, client=self.client, method="POST", data=pairs, files=upload_files)
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
            # The response is a NEW document: its JS context replaces the
            # previous page's. A script-less page has no live context at all
            # (run_scripts returns early without one) — ctx becomes None so
            # the next action builds fresh from this page, never driving the
            # stale previous document (a real bug: an OTP submit re-sent
            # the old login form's credentials).
            if result.kind == "html":
                self.current_tab.ctx = js_info.get("ctx") if js_info else None
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
            self.html, self.url, self.client, force=True, actions=actions, ctx=self.current_tab.ctx
        )
        self.current_tab.ctx = js_info.get("ctx") or self.current_tab.ctx
        # submit() reads the engine's live form state (fields/files the page
        # JS actually collected) from this snapshot.
        self._last_form_js_info = js_info
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
            child_msgs = [
                pm
                for f in frames
                for pm in f.post_messages
                if pm.get("target") in {"parent", "top", "*"}
            ]
            if child_msgs and js_ran:
                pumped_html, extra_js = await run_scripts(
                    new_html, self.url, self.client, force=True, messages=child_msgs
                )
                if extra_js.get("navigated"):
                    js_info["navigated"] = extra_js["navigated"]
                if extra_js.get("form_submit"):
                    js_info["form_submit"] = extra_js["form_submit"]
                new_html = pumped_html
                result.body = new_html
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
            if js_info.get("navigated"):
                nav_url = urljoin(self.url, js_info["navigated"])
                return await self.goto(nav_url, max_chars=max_chars)
        return outcome

    async def click(
        self,
        ref: str,
        *,
        max_chars: int = 8000,
        stealth: bool | None = None,
    ) -> Page:
        if not self.html:
            raise ValueError("no current page")
        is_stealth = self.stealth if stealth is None else bool(stealth)
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
        # Native mini-DOM actuation with optional humanised kinematics
        return await self._apply(
            [{"kind": "click", "path": target.path, "stealth": is_stealth}], self.html, max_chars
        )

    async def type(
        self,
        ref: str,
        text: str,
        *,
        max_chars: int = 8000,
        stealth: bool | None = None,
    ) -> Page:
        if not self.html:
            raise ValueError("no current page")
        is_stealth = self.stealth if stealth is None else bool(stealth)
        targets, _ = inspect_html(self.html, self.url)
        target = next((item for item in targets if item.ref == ref), None)
        if target is None:
            html = type_html(self.html, ref, text)
            return await self._apply([], html, max_chars)
        return await self._apply(
            [{"kind": "type", "path": target.path, "text": text, "stealth": is_stealth}],
            self.html,
            max_chars,
        )

    async def upload(
        self,
        ref: str,
        files: list[dict] | str | Path | dict,
        *,
        max_chars: int = 8000,
    ) -> Page:
        """Upload one or more files to a file input control (<input type="file">).

        Binary files (video, images, PDFs, any bytes) are transported as
        ``contentB64`` so the payload stays byte-exact end to end; plain
        ``content`` strings are still accepted as text.
        """
        if not self.html:
            raise ValueError("no current page")

        def _b64_of_file(p: Path) -> str:
            return base64.b64encode(p.read_bytes()).decode("ascii")

        file_list: list[dict] = []
        raw_items = files if isinstance(files, list) else [files]
        for item in raw_items:
            if isinstance(item, (str, Path)):
                p = Path(item)
                if p.is_file():
                    fname = p.name
                    mime, _ = mimetypes.guess_type(str(p))
                    file_list.append({
                        "name": fname,
                        "contentB64": _b64_of_file(p),
                        "type": mime or "application/octet-stream",
                    })
                else:
                    file_list.append({
                        "name": str(item),
                        "content": "",
                        "type": "text/plain",
                    })
            elif isinstance(item, dict):
                b64 = item.get("contentB64") or item.get("content_b64")
                if b64:
                    file_list.append({
                        "name": item.get("name") or "file",
                        "contentB64": b64,
                        "type": item.get("type") or "application/octet-stream",
                    })
                    continue
                fname = item.get("name") or "file"
                content = item.get("content")
                if content is None and item.get("path"):
                    p = Path(item["path"])
                    if p.is_file():
                        fname = item.get("name") or p.name
                        mime, _ = mimetypes.guess_type(str(p))
                        item.setdefault("type", mime)
                        file_list.append({
                            "name": fname,
                            "contentB64": _b64_of_file(p),
                            "type": item.get("type") or "application/octet-stream",
                        })
                        continue
                file_list.append({
                    "name": fname,
                    "content": content if content is not None else "",
                    "type": item.get("type") or "application/octet-stream",
                })

        payload_chars = sum(
            len(f.get("contentB64") or f.get("content") or "") for f in file_list
        )
        if payload_chars > MAX_UPLOAD_B64_CHARS:
            raise ValueError(
                f"upload payload {payload_chars} chars exceeds engine limit "
                f"{MAX_UPLOAD_B64_CHARS} (approx {MAX_UPLOAD_B64_CHARS * 3 // 4} raw bytes)"
            )

        targets, _ = inspect_html(self.html, self.url)
        target = next((item for item in targets if item.ref == ref), None)
        if target is not None:
            return await self._apply(
                [{"kind": "upload", "path": target.path, "files": file_list}],
                self.html,
                max_chars,
            )

        surface = self.action_surface(max_actions=100_000)
        action = next((a for a in surface.actions if a.ref == ref), None)
        selector = (action.selector or action.target) if action else ref
        return await self._apply(
            [{"kind": "upload", "selector": selector, "files": file_list}],
            self.html,
            max_chars,
        )

    async def press_key(
        self,
        ref: str,
        key: str,
        *,
        max_chars: int = 8000,
        stealth: bool | None = None,
    ) -> Page:
        """Press a named key (enter/tab/escape/arrows/backspace/space/...) on a control.

        Enter in a text input performs the form's implicit submission, Tab
        moves focus to the next focusable element, arrows change select
        options, Backspace deletes a character.
        """
        if not self.html:
            raise ValueError("no current page")
        key_norm = key.strip().lower()
        if not key_norm:
            raise ValueError(
                "key must be one of: enter, tab, escape, backspace, delete, space, "
                "arrowup, arrowdown, arrowleft, arrowright, home, end, pageup, pagedown"
            )
        is_stealth = self.stealth if stealth is None else bool(stealth)
        targets, _ = inspect_html(self.html, self.url)
        target = next((item for item in targets if item.ref == ref), None)
        if target is not None:
            return await self._apply(
                [{"kind": "press", "path": target.path, "key": key_norm, "stealth": is_stealth}],
                self.html,
                max_chars,
            )
        surface = self.action_surface(max_actions=100_000)
        action = next((a for a in surface.actions if a.ref == ref), None)
        if action is None:
            raise ValueError(
                f"no target [{ref}] for key press. Refs are content-addressed and "
                "change when a field's value changes; re-inspect and use the new ref."
            )
        return await self._apply(
            [{"kind": "press", "selector": action.selector or action.target, "key": key_norm, "stealth": is_stealth}],
            self.html,
            max_chars,
        )

    def dialogs(self) -> list[dict]:
        """Dialogs (alert/confirm/prompt) fired by the most recent script pass.

        Each entry says how it was answered at the time the page called it:
        'accepted'/'dismissed' (the agent had staged an answer) or 'default'
        (no staged answer, so the engine's default return value applied).
        """
        return list(self._last_dialogs)

    def respond_dialog(self, accept: bool, text: str = "") -> dict:
        """Stage the answer for the next confirm/prompt the page fires.

        The engine is synchronous, so the answer cannot travel back in time:
        stage it, then re-run the action that triggers the dialog. Returns the
        staged state plus the current dialog list.
        """
        ctx = self.current_tab.ctx
        if ctx is not None:
            ctx.eval(f"globalThis.__stageDialogAnswer({str(bool(accept)).lower()}, {json.dumps(text)})")
        return {
            "staged": {"accept": bool(accept), "text": text},
            "note": "answer staged; re-run the action that triggers the dialog",
            "dialogs": self.dialogs(),
        }

    # -- login / OTP ------------------------------------------------------
    # Secrets are memory-only: never persisted, never echoed in views or
    # results. The verdict is computed from page evidence, never assumed.

    def login_wall(self) -> dict | None:
        """Login wall detected on the current page, if any (dict for the agent)."""
        wall = self._detect_wall()
        if wall is None:
            return None
        out = {
            "kind": wall.kind,
            "url": wall.url,
            "confidence": wall.confidence,
            "detail": wall.detail,
        }
        if wall.kind == "form":
            out.update({
                "username_ref": wall.username_ref,
                "password_ref": wall.password_ref,
                "submit_ref": wall.submit_ref,
            })
        if wall.kind == "otp":
            out.update({
                "refs": wall.refs,
                "expected_length": wall.expected_length,
                "split_boxes": wall.split_boxes,
            })
        return out

    def _detect_wall(self):
        from webresearch.engine.login import detect_login_wall, detect_otp_page

        if not self.html:
            return None
        targets, _ = inspect_html(self.html, self.url)
        status = self.page.status if self.page else 200
        wall = detect_login_wall(self.html, self.url, targets, status)
        if wall is not None:
            return wall
        return detect_otp_page(self.html, self.url, targets)

    async def login(
        self,
        url: str,
        username: str,
        password: str,
        *,
        max_chars: int = 4000,
        submit_url: str | None = None,
        username_field: str | None = None,
        password_field: str | None = None,
    ) -> dict:
        """Log in to a site using credentials the agent got from the user.

        Fills the login form through the normal type/submit lanes, then
        verifies the outcome from page evidence. Returns an honest verdict:
        verified / otp_required (with a checkpoint) / failed / human_only /
        account_locked / unknown. The password itself is never part of the
        result.

        When the page's submit control is inert in the engine (form-less React
        SPAs whose click handler never attaches — LinkedIn is the real case),
        a direct-POST fallback lane runs: POST ``submit_url`` with
        ``username_field``/``password_field`` form data, carrying the session
        cookies + CSRF from the page. The response page is loaded and verified
        with the same honest verdicts.
        """
        from webresearch.engine.login import verify_logged_in

        page = await self.goto(url, max_chars=max_chars)
        pre_url = url
        wall = self._detect_wall()

        if wall is None or wall.kind != "form":
            # Maybe already logged in.
            verdict = verify_logged_in(
                self.html, self.url, pre_url, dict(self.client.cookies),
                page.status if page else 200,
            )
            if verdict == "verified":
                self._last_login_verdict = "verified"
                return {
                    "verdict": "verified",
                    "message": "already logged in; no credentials used",
                    "url": self.url,
                }
            return {
                "verdict": "failed",
                "message": "no login form detected on this page",
                "url": self.url,
            }

        # Fill through the engine lanes so page JS sees real typing.
        if wall.username_ref:
            await self.type(wall.username_ref, username, max_chars=max_chars)
        targets, _ = inspect_html(self.html, self.url)
        pwd = next((t for t in targets if t.ref == wall.password_ref), None)
        if pwd is None:
            return {"verdict": "failed", "message": "password field vanished after typing username", "url": self.url}
        await self.type(wall.password_ref, password, max_chars=max_chars)

        # Small human-mimicking delay between typing and clicking submit.
        # Sites that score keystroke-to-click intervals see a bot when it is 0.
        await asyncio.sleep(0.3 + (hash(username + password) % 500) / 1000.0)

        # Submit: prefer the detected submit button; fall back to form submit.
        post: Page
        if wall.submit_ref:
            targets, _ = inspect_html(self.html, self.url)
            btn = next((t for t in targets if t.ref == wall.submit_ref), None)
            if btn is not None:
                post = await self.click(wall.submit_ref, max_chars=max_chars)
            else:
                post = await self._submit_first_login_form(max_chars)
        else:
            post = await self._submit_first_login_form(max_chars)

        # Inert-click fallback: if the engine click did nothing (still the
        # same URL, password field still present) the page's React handler
        # never attached. When a submit_url is provided, POST the credentials
        # directly — the same request the handler would have sent.
        still_login = (
            "password" in (self.html or "").lower()
            and 'type="password"' in (self.html or "")
        )
        if still_login and submit_url:
            csrf = self._extract_login_csrf()
            data: dict[str, str] = {
                username_field or "session_key": username,
                password_field or "session_password": password,
            }
            if csrf:
                data.setdefault("loginCsrfParam", csrf)
            result = await fetch(
                submit_url,
                client=self.client,
                method="POST",
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": url,
                    "Origin": urlparse(url).scheme + "://" + urlparse(url).netloc,
                },
            )
            dest = result.final_url or submit_url
            res_html = result.body
            js_info_post: dict = {}
            if result.kind == "html":
                res_html, js_info_post = await run_scripts(
                    res_html, dest, self.client, headers=result.headers
                )
                result = result.model_copy(update={"body": res_html})
                self.current_tab.ctx = js_info_post.get("ctx") or None
            page = snapshot(parse(result), max_chars=max_chars)
            page = _decorate(page, res_html, bool(js_info_post.get("ran")), js_info_post)
            post = self._push(page, res_html)

        verdict = verify_logged_in(
            self.html, self.url, pre_url, dict(self.client.cookies),
            post.status if post else 200,
        )
        self._last_login_verdict = verdict

        if verdict == "otp_required":
            checkpoint = self._create_otp_checkpoint()
            self._last_login_wall = self.login_wall()
            return {
                "verdict": "otp_required",
                "message": "credentials accepted; a verification code is required",
                "url": self.url,
                "checkpoint": checkpoint.id,
                "expected_length": (self._last_login_wall or {}).get("expected_length"),
            }
        if verdict == "verified":
            return {"verdict": "verified", "message": "logged in", "url": self.url}
        if verdict in {"human_only", "account_locked"}:
            return {
                "verdict": verdict,
                "message": "the page says this step needs a human (lock/identity check)",
                "url": self.url,
            }
        return {
            "verdict": "failed",
            "message": "credentials did not lead to a logged-in page; check them and retry",
            "url": self.url,
        }

    def _extract_login_csrf(self) -> str:
        """Best-effort CSRF token for the direct-POST fallback lane.

        Reads data-csrf attributes and csrf-ish meta/hidden fields from the
        current page. Never invents a value; empty string when absent.
        """
        if not self.html:
            return ""
        m = re.search(r'data-csrf="([^"]+)"', self.html)
        if m:
            return m.group(1)
        m = re.search(r'name="loginCsrfParam"\s+value="([^"]+)"', self.html)
        if m:
            return m.group(1)
        return ""

    async def _submit_first_login_form(self, max_chars: int) -> Page:
        current = self.page
        if current and current.forms:
            return await self.submit(1, max_chars=max_chars)
        # No extracted form: click any submit-ish button.
        targets, _ = inspect_html(self.html, self.url)
        btn = next((t for t in targets if t.role == "button"), None)
        if btn is not None:
            return await self.click(btn.ref, max_chars=max_chars)
        raise ValueError("login form has no submit control")

    def _create_otp_checkpoint(self) -> OtpCheckpoint:

        wall = self._detect_wall()
        cid = secrets.token_hex(4)
        refs = list(wall.refs) if wall and wall.kind == "otp" else []
        checkpoint = OtpCheckpoint(
            id=cid,
            url=self.url,
            refs=refs,
            expected_length=wall.expected_length if wall else None,
            split_boxes=wall.split_boxes if wall else False,
        )
        self._otp_checkpoints[cid] = checkpoint
        return checkpoint

    async def submit_otp(self, checkpoint_id: str, code: str, *, max_chars: int = 4000) -> dict:
        """Submit a verification code the human received.

        One call fills, submits, and verifies. Max 3 attempts per checkpoint,
        ~10 minute expiry; the code is never echoed back.
        """
        from webresearch.engine.login import verify_logged_in

        checkpoint = self._otp_checkpoints.get(checkpoint_id)
        if checkpoint is None:
            known = ", ".join(self._otp_checkpoints) or "none"
            return {"verdict": "failed", "message": f"no checkpoint [{checkpoint_id}] (open: {known})"}
        if checkpoint.expired:
            self._otp_checkpoints.pop(checkpoint_id, None)
            return {
                "verdict": "failed",
                "message": "checkpoint expired (codes live ~10 minutes); restart the login",
            }
        if checkpoint.attempts >= MAX_OTP_ATTEMPTS:
            return {
                "verdict": "failed",
                "message": "too many wrong codes; the site may lock the account — ask the user before retrying",
            }

        code = (code or "").strip().replace(" ", "")
        if checkpoint.expected_length and len(code) != checkpoint.expected_length:
            checkpoint.attempts += 1
            return {
                "verdict": "failed",
                "message": f"code is {len(code)} digit(s); the input expects {checkpoint.expected_length}",
                "attempts_left": checkpoint.attempts_left,
            }

        # Fill: digit-per-box for split inputs, whole code otherwise.
        try:
            if checkpoint.split_boxes and len(checkpoint.refs) == len(code):
                for ref, digit in zip(checkpoint.refs, code, strict=False):
                    await self.type(ref, digit, max_chars=max_chars)
            elif checkpoint.refs:
                await self.type(checkpoint.refs[0], code, max_chars=max_chars)
            else:
                return {"verdict": "failed", "message": "no code input found on the checkpoint page"}
        except ValueError as exc:
            return {"verdict": "failed", "message": f"could not fill the code field: {exc}"}

        post = await self._submit_first_login_form(max_chars)
        verdict = verify_logged_in(
            self.html, self.url, checkpoint.url, dict(self.client.cookies),
            post.status if post else 200,
        )

        if verdict == "verified":
            self._otp_checkpoints.pop(checkpoint_id, None)
            self._last_login_verdict = "verified"
            return {"verdict": "verified", "message": "logged in", "url": self.url}
        if verdict == "otp_required":
            # Same page after submit: the code was rejected. Count the attempt.
            checkpoint.attempts += 1
            if checkpoint.attempts >= MAX_OTP_ATTEMPTS:
                self._otp_checkpoints.pop(checkpoint_id, None)
                return {
                    "verdict": "failed",
                    "message": "code rejected 3 times; stopping before the account locks",
                }
            return {
                "verdict": "rejected",
                "message": "code rejected; ask the user for the newest code",
                "attempts_left": checkpoint.attempts_left,
            }
        if verdict in {"human_only", "account_locked"}:
            self._otp_checkpoints.pop(checkpoint_id, None)
            return {"verdict": verdict, "message": "site requires a human step (lock/identity)", "url": self.url}
        # Unknown post-state: assume rejection but keep the checkpoint.
        checkpoint.attempts += 1
        return {
            "verdict": "unknown",
            "message": "submit did not clearly verify or reject; ask the user before retrying",
            "attempts_left": checkpoint.attempts_left,
        }

    async def login_state(self, url: str, *, max_chars: int = 2000) -> dict:
        """Probe whether the current session is logged in at url.

        No credentials involved: loads the page and reads the evidence.
        """
        from webresearch.engine.login import verify_logged_in

        page = await self.goto(url, max_chars=max_chars)
        wall = self._detect_wall()
        verdict = verify_logged_in(
            self.html, self.url, url, dict(self.client.cookies),
            page.status if page else 200,
        )
        out = {"url": self.url, "verdict": verdict}
        if wall is not None and wall.kind == "form":
            out["login_wall"] = True
        return out

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
        files: list[dict] | str | Path | dict | None = None,
        max_chars: int = 8000,
        stealth: bool | None = None,
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

        # Disabled is checked before every lane: a disabled link must not
        # navigate, a disabled submit must not POST, a disabled field must
        # not type. The old code only guarded the activate branch, so
        # disabled submit buttons and links drove real HTTP requests.
        if action.disabled:
            raise ValueError(f"[{ref}] {action.label!r} is disabled")

        if action.role == "file" or files is not None:
            upload_payload = files if files is not None else (text or "uploaded_file.txt")
            return await self.upload(ref, upload_payload, max_chars=max_chars)

        is_stealth = self.stealth if stealth is None else bool(stealth)

        if action.kind == "navigate":
            return await self.goto(action.target, max_chars=max_chars)

        if action.kind == "submit":
            if not text:
                if action.role == "button" or action.tag == "button" or (action.tag == "input" and not action.field_name):
                    return await self._act_engine(action, text="", dx=dx, dy=dy, max_chars=max_chars, stealth=is_stealth)
                raise ValueError(
                    f"[{ref}] {action.role!r} is a form field and needs text"
                )
            if action.selector and (action.role in {"textbox", "searchbox"} or action.tag in {"input", "textarea"}):
                return await self._act_engine(action, text=text, dx=dx, dy=dy, max_chars=max_chars, stealth=is_stealth)
            return await self._act_submit_field(action, text, max_chars)

        if action.kind == "dropzone":
            raise ValueError(
                f"[{ref}] is a drag goal, not a control; drag a handle onto it"
            )

        if action.kind in {"activate", "drag", "embed"}:
            return await self._act_engine(action, text=text, dx=dx, dy=dy, max_chars=max_chars, stealth=is_stealth)

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
        stealth: bool = False,
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
                        "stealth": stealth,
                    }
                ],
                self.html,
                max_chars,
            )
        elif action.kind in {"activate", "submit"}:
            if text:
                return await self._apply(
                    [{"kind": "type", "selector": selector, "text": text, "stealth": stealth}],
                    self.html,
                    max_chars,
                )
            return await self._apply(
                [{"kind": "activate", "selector": selector, "stealth": stealth}],
                self.html,
                max_chars,
            )
        else:  # embed
            raise ValueError(
                f"[{action.ref}] is embedded content at {selector!r}; "
                "read its own document instead of acting on it"
            )

    async def scroll(
        self, y: int = 800, x: int = 0, *, max_chars: int = 8000, stealth: bool | None = None
    ) -> Page:
        """Wheel the page by a delta (x, y add onto the current position, like a
        real mouse wheel); fires wheel + scroll events and IntersectionObserver
        notifications."""
        if not self.html:
            raise ValueError("no current page")
        is_stealth = self.stealth if stealth is None else bool(stealth)
        return await self._apply(
            [{"kind": "scroll", "x": x, "y": y, "stealth": is_stealth}], self.html, max_chars
        )

    async def _apply(
        self, actions: list[dict], html: str, max_chars: int
    ) -> Page:
        """Re-run page JS with agent interactions, then rebuild the page model."""
        had_ctx = self.current_tab.ctx is not None
        new_html, js_info = await run_scripts(
            html, self.url, self.client, force=True, actions=actions, ctx=self.current_tab.ctx
        )
        self.current_tab.ctx = js_info.get("ctx") or self.current_tab.ctx
        # A fresh engine build may be a history restore replaying this entry's
        # document: re-apply the stored scroll position once, before the page
        # snapshot is taken, so IntersectionObserver-driven feeds load the
        # same content a real browser would show at that position.
        if not had_ctx and self.current_tab.pending_scroll_y:
            re_y = self.current_tab.pending_scroll_y
            self.current_tab.pending_scroll_y = 0
            ctx = self.current_tab.ctx
            if ctx is not None:
                try:
                    ctx.eval(f"scrollTo(0, {re_y}); true")
                except Exception:
                    pass
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
        child_msgs = [
            pm
            for f in frames
            for pm in f.post_messages
            if pm.get("target") in {"parent", "top", "*"}
        ]
        if child_msgs and js_ran:
            pumped_html, extra_js = await run_scripts(
                new_html, self.url, self.client, force=True, messages=child_msgs, ctx=self.current_tab.ctx
            )
            self.current_tab.ctx = extra_js.get("ctx") or self.current_tab.ctx
            if extra_js.get("navigated"):
                js_info["navigated"] = extra_js["navigated"]
            if extra_js.get("form_submit"):
                js_info["form_submit"] = extra_js["form_submit"]
            new_html = pumped_html
            result.body = new_html
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
        self._last_dialogs = list(js_info.get("dialogs") or [])
        popups = list(js_info.get("popups") or [])
        self._last_popups = popups
        if self.profile is not None:
            self.profile.save_from_client(self.client)
        await self._load_popup_tabs(popups)
        if js_info.get("navigated"):
            nav_url = urljoin(self.url, js_info["navigated"])
            return await self.goto(nav_url, max_chars=max_chars)
        if js_info.get("form_submit"):
            fs = js_info["form_submit"]
            act_url = urljoin(self.url, fs.get("action") or "")
            method = fs.get("method", "POST").upper()
            fields = fs.get("fields", {})
            files_map = fs.get("files", {})
            if method == "GET":
                parsed = urlparse(act_url)
                target = urlunparse(parsed._replace(query=urlencode(fields)))
                return await self.goto(target, max_chars=max_chars)
            else:
                upload_files = None
                if files_map:
                    upload_files = []
                    for f_name, f_list in files_map.items():
                        for f_item in f_list:
                            b64_c = f_item.get("contentB64") or f_item.get("content_b64")
                            if b64_c:
                                # Binary files round-trip as base64; decode to the
                                # exact original bytes for the multipart POST.
                                raw_bytes = base64.b64decode(b64_c)
                            else:
                                raw_c = f_item.get("content", "")
                                raw_bytes = raw_c.encode("utf-8") if isinstance(raw_c, str) else bytes(raw_c)
                            upload_files.append(
                                (f_name, (f_item.get("name", "file"), raw_bytes, f_item.get("type", "application/octet-stream")))
                            )
                result = await fetch(
                    act_url,
                    client=self.client,
                    method="POST",
                    data=fields,
                    files=upload_files,
                )
                dest_url = result.final_url or act_url
                res_html = result.body
                js_info_post: dict = {}
                if result.kind == "html":
                    res_html, js_info_post = await run_scripts(
                        res_html, dest_url, self.client, headers=result.headers
                    )
                    result = result.model_copy(update={"body": res_html})
                    # Same as submit(): the POST response is a new document —
                    # its JS context (or None for a script-less page) replaces
                    # the previous one, so the next action drives THIS page.
                    self.current_tab.ctx = js_info_post.get("ctx") or None
                page = snapshot(parse(result), max_chars=max_chars)
                page = _decorate(
                    page,
                    res_html,
                    bool(js_info_post.get("ran")) if result.kind == "html" else False,
                    js_info_post if result.kind == "html" else None,
                )
                return self._push(page, res_html)
        # The action stayed on this document (scroll/type/inline JS mutation):
        # rewrite the current history entry instead of creating a new one.
        return self._push(page, new_html, replace=True)

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

    async def solve_captcha(
        self,
        *,
        model: str = "auto",
        max_attempts: int = 1,
    ) -> dict:
        """Use a vision model to solve a tile-based CAPTCHA.

        Returns the solution dict with tile selections. The caller should then
        click the tiles and hit verify.
        """
        from webresearch.settings import llm_config

        if not self.html:
            raise ValueError("no current page")
        if self.captcha_inspection is None:
            raise ValueError(
                "no CAPTCHA inspection available. Call browser_captcha first."
            )

        cfg = llm_config(model)
        result: CaptchaSolveResult = await solve_captcha(
            self.captcha_inspection,
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=cfg["model"],
            client=self.client,
            max_attempts=max_attempts,
        )

        return {
            "solved": result.solution.solved,
            "kind": result.solution.kind,
            "prompt": result.solution.prompt,
            "model_used": result.solution.model_used,
            "attempt": result.solution.attempt,
            "tiles_selected": [
                {
                    "tile_id": s.tile_id,
                    "row": s.row,
                    "column": s.column,
                    "confidence": s.confidence,
                    "reason": s.reason,
                }
                for s in result.solution.tiles_selected
            ],
            "error": result.solution.error or (result.error or ""),
            "view": result.solution.view,
        }

    def _goto_history_index(self, i: int) -> None:
        """Move the tab's history pointer and restore that entry's live DOM.

        The engine context belongs to the entry we are leaving, so it is
        dropped and rebuilt from the stored HTML on the next action pass —
        the same code path as a fresh load of that document. The stored
        scroll position is re-applied after the rebuild.
        """
        tab = self.current_tab
        if not 0 <= i < len(tab.history):
            raise ValueError(f"no page at history index {i}")
        tab.index = i
        snap = tab.snapshots[i] if i < len(tab.snapshots) else None
        if snap is None or snap.get("html") is None:
            # Synthetic entry (no stored document): clear the live state.
            self.html = ""
            self.url = snap.get("url", "") if snap else ""
            tab.headers = {}
            self.frame_documents = []
            tab.captcha_inspection = None
        else:
            self.html = snap["html"]
            self.url = snap["url"]
            tab.headers = dict(snap.get("headers") or {})
            self.frame_documents = list(snap.get("frames") or [])
            tab.captcha_inspection = snap.get("captcha")
        tab.pending_scroll_y = int((snap or {}).get("scroll_y") or 0)
        tab.ctx = None

    def back(self) -> Page:
        if self.index <= 0:
            raise ValueError("no previous page")
        self._goto_history_index(self.index - 1)
        page = self.page
        assert page is not None
        return page

    def forward(self) -> Page:
        tab = self.current_tab
        if tab.index >= len(tab.history) - 1:
            raise ValueError("no forward page")
        self._goto_history_index(tab.index + 1)
        page = self.page
        assert page is not None
        return page

    async def search(self, query: str, *, engine: str = "auto") -> SearchResult:
        return await web_search(query, client=self.client, engine=engine)  # type: ignore[arg-type]

    async def search_images(self, query: str, *, engine: str = "auto", max_hits: int = 8) -> SearchResult:
        from webresearch.search import search_images as web_search_images

        return await web_search_images(query, client=self.client, max_hits=max_hits, engine=engine)

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
        tenant_id: str = "default",
    ) -> Session:
        await self._evict()
        sid = "s_" + secrets.token_hex(4)
        # Explicit proxy wins; otherwise rotate through the pool for isolation.
        chosen = proxy if proxy is not None else self._next_proxy()
        client = make_client(headers=headers, proxy=chosen)
        session = Session(client, sid, profile=profile, tenant_id=tenant_id)
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

    def get(self, session_id: str, tenant_id: str | None = None) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if tenant_id is not None and session.tenant_id != tenant_id:
            raise PermissionError(f"session {session_id} belongs to another tenant")
        session.last_used = time.time()
        return session

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            await self.close(sid)
