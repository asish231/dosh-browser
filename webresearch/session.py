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
from urllib.parse import urlencode, urljoin, urlparse, urlsplit, urlunparse

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
        # Monotonic document identity: every real navigation (goto, link,
        # submit) allocates a new id; pushState/replaceState entries inherit
        # the current one because they are the SAME document under a new URL.
        # back()/forward() between same-doc entries is a live traversal (the
        # ctx survives, like a real browser's same-document navigation);
        # cross-doc entries restore the stored document instead.
        self._doc_seq = 0
        # sessionStorage is per-tab per-ORIGIN lifetime (spec): owned by this
        # tab, scoped per origin, gone when it closes. localStorage lives on
        # the Session (shared per origin across tabs).
        self.session_storage: dict[str, dict[str, str]] = {}
        # Parallel snapshot per history entry: the DOM/headers/frames state that
        # entry represented, so back()/forward() can restore a live document
        # instead of leaving the engine on the navigated-away page.
        self.snapshots: list[dict] = []
        # Absolute scroll position to re-apply after the next fresh engine pass
        # (history restore replays page scripts, then wheels back to where the
        # entry was left).
        self.pending_scroll_y: int = 0
        # popstate contract for history restores: fired (with the entry's
        # pushed state) when the restored document is next rebuilt.
        self.pending_popstate: bool = False
        self.pending_popstate_state: object = None
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
        self,
        page: Page,
        html: str | None = None,
        headers: dict | None = None,
        replace: bool = False,
        doc_id: int | None = None,
    ) -> Page:
        if html is not None:
            self.html = html
            self.url = page.url
        if headers is not None:
            self.headers = dict(headers)
        # Only a genuinely NEW entry truncates the forward stack. An in-place
        # replace (scroll/type/mutation/history-move rebuild) must keep it:
        # the old code truncated unconditionally, so back() + one action
        # destroyed the forward entry and forward() then failed (re-audit
        # finding).
        if not replace:
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
        # Document identity: an explicit doc_id (pushState inheriting the
        # current document, a history-move rebuild keeping the restored
        # entry's) wins; an in-place replace keeps the entry's existing id;
        # a genuinely new navigation allocates a fresh one.
        if doc_id is None:
            if replace and 0 <= self.index < len(self.snapshots):
                doc_id = self.snapshots[self.index].get("doc_id")
            else:
                self._doc_seq += 1
                doc_id = self._doc_seq
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
        # An in-place replace (scroll/type/SPA mutation) must not lose the
        # entry's pushed history state — a later restore dispatches popstate
        # with it.
        prev_state = None
        if replace and 0 <= self.index < len(self.snapshots):
            prev_state = self.snapshots[self.index].get("history_state")
        snap = {
            "html": self.html if html is not None else None,
            "url": page.url,
            "headers": dict(self.headers),
            "frames": list(self.frame_documents) if html is not None else [],
            "captcha": self.captcha_inspection if html is not None else None,
            "scroll_y": scroll_y,
            "history_state": prev_state,
            "doc_id": doc_id,
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
        # Web storage, host side: localStorage per origin (optionally backed
        # by the profile's sqlite storage table), sessionStorage per tab.
        # Seeded into each fresh engine context and drained back after every
        # JS pass — the engine context itself dies on navigation.
        self._web_storage: dict[str, dict[str, str]] = {}
        self._shell = None

    @property
    def shell(self):
        if self._shell is None:
            from webresearch.shell import DoshShell

            self._shell = DoshShell(self)
        return self._shell

    def _origin_of(self, url: str) -> str:
        parts = urlsplit(url or "")
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
        return "about:blank"

    def _storage_seed(self, url: str | None = None) -> dict[str, dict[str, str]]:
        """Seed payload for a fresh engine context at `url`."""
        origin = self._origin_of(url or self.url)
        if origin not in self._web_storage:
            loaded: dict[str, str] = {}
            if self.profile is not None:
                try:
                    loaded = {str(k): str(v) for k, v in self.profile.load_storage(origin).items()}
                except Exception:
                    loaded = {}
            self._web_storage[origin] = loaded
        return {
            "local": dict(self._web_storage[origin]),
            # Per-origin slice only: a cross-origin navigation in this tab
            # starts with an empty session store (the old flat dict leaked
            # a.test's values to b.test — re-audit finding).
            "session": dict(self.current_tab.session_storage.get(origin, {})),
        }

    def _absorb_storage(self, js_info: dict | None, url: str | None = None) -> None:
        """Persist drained web storage from an engine pass at session level."""
        drained = (js_info or {}).get("storage")
        if not isinstance(drained, dict):
            return
        origin = self._origin_of(url or self.url)
        local = drained.get("local")
        if isinstance(local, dict):
            if origin not in self._web_storage:
                self._storage_seed(url or self.url)  # ensures origin entry + profile load
            before = self._web_storage.get(origin, {})
            for k, v in local.items():
                k = str(k)
                v = str(v)
                if before.get(k) != v:
                    self._web_storage[origin][k] = v
                    if self.profile is not None:
                        try:
                            self.profile.save_storage(origin, k, v)
                        except Exception:
                            pass
            for k in [k for k in before if k not in local]:
                self._web_storage[origin].pop(k, None)
                if self.profile is not None:
                    try:
                        self.profile.delete_storage(origin, k)
                    except Exception:
                        pass
        session = drained.get("session")
        if isinstance(session, dict):
            origin_s = self._origin_of(url or self.url)
            self.current_tab.session_storage[origin_s] = {
                str(k): str(v) for k, v in session.items()
            }
        self._broadcast_storage_events(js_info, url or self.url)

    def _broadcast_storage_events(self, js_info: dict | None, event_url: str) -> None:
        """Dispatch `storage` events for localStorage mutations to OTHER
        same-origin tabs (spec contract).

        The pass that made the change is the ACTIVE tab's; every other tab
        with a live engine context whose current origin matches receives,
        in order: the value applied into its own localStorage copy (a real
        browser's storage area is shared — the listener reads the new value
        already), then the StorageEvent with key/oldValue/newValue/url and
        storageArea. The originating tab gets no event; the receiving store's
        apply is quiet (no re-record) so the change cannot echo back.
        sessionStorage is per-tab and never broadcasts.
        """
        changes = (js_info or {}).get("storage_changes") or {}
        local_changes = [c for c in (changes.get("local") or []) if isinstance(c, dict)]
        if not local_changes:
            return
        origin = self._origin_of(event_url)
        src_id = self.active_tab_id
        for tab in self._tabs.values():
            if tab.id == src_id or tab.ctx is None:
                continue
            if self._origin_of(tab.url) != origin:
                continue
            try:
                url_js = json.dumps(event_url)
            except Exception:
                url_js = '""'
            for ch in local_changes:
                key = ch.get("key")
                if key is None:  # clear(): one event, all-null payload
                    js = (
                        "__applyRemoteStorageChange(null, null, true);"
                        f"__dispatchStorageEvent(null, null, null, {url_js}); true"
                    )
                else:
                    try:
                        k_js = json.dumps(str(key))
                    except Exception:
                        k_js = '""'
                    old_js = json.dumps(ch.get("oldValue"))
                    new_js = json.dumps(ch.get("newValue"))
                    js = (
                        f"__applyRemoteStorageChange({k_js}, {new_js}, false);"
                        f"__dispatchStorageEvent({k_js}, {old_js}, {new_js}, {url_js}); true"
                    )
                try:
                    tab.ctx.eval(js)
                except Exception:
                    pass

    def _absorb_history(self, js_info: dict | None) -> None:
        """Record engine pushState/replaceState at session level.

        Each pushState adds a real history entry carrying the CURRENT document
        (same DOM, new URL) — exactly like an SPA router in a real browser:
        no reload, the entry becomes current, the live ctx is preserved.
        Multiple pushes in one script pass each create their entry. A
        replaceState rewrites the current entry's URL. The state object is
        stored on the entry snapshot so a later restore can dispatch popstate
        with it. back/forward/go are NOT handled here (they need a document
        restore; handled by _engine_history_move in action passes).
        """
        pushes = (js_info or {}).get("history_pushes") or []
        # Back-compat: single-push shape from earlier revisions.
        single = (js_info or {}).get("history_push")
        if isinstance(single, dict) and single.get("url"):
            pushes = [*pushes, single]
        # pushState entries are the SAME document under a new URL: they inherit
        # the current document identity so a later back() to one of them is a
        # live same-document traversal (no script re-run, no re-push).
        tab = self.current_tab
        cur_doc = (
            tab.snapshots[tab.index].get("doc_id")
            if 0 <= tab.index < len(tab.snapshots)
            else None
        )
        for pushed in pushes:
            if not (isinstance(pushed, dict) and pushed.get("url")):
                continue
            page = self.page
            if page is None:
                # No document to carry yet: skip this push, do not abort the
                # remaining ones.
                continue
            new_page = page.model_copy(update={"url": str(pushed["url"])})
            self._push(new_page, self.html, doc_id=cur_doc)
            if 0 <= self.current_tab.index < len(self.current_tab.snapshots):
                self.current_tab.snapshots[self.current_tab.index]["history_state"] = pushed.get("state")
        replaced = (js_info or {}).get("history_replace")
        if isinstance(replaced, dict) and replaced.get("url"):
            tab = self.current_tab
            url = str(replaced["url"])
            self.url = url
            if 0 <= tab.index < len(tab.history) and tab.history[tab.index] is not None:
                tab.history[tab.index] = tab.history[tab.index].model_copy(update={"url": url})
            if 0 <= tab.index < len(tab.snapshots):
                tab.snapshots[tab.index]["url"] = url
                tab.snapshots[tab.index]["history_state"] = replaced.get("state")

    def _dispatch_popstate(self, state: object) -> None:
        """Fire popstate (with the entry's pushed state) into the live ctx.

        The stored state is a structured-clone ENVELOPE (see the engine's
        __structuredCloneEncode): decode it back into live values first, so
        undefined-valued keys, Dates, NaN/Infinity, Maps/Sets and cyclic
        references survive the engine→host→engine round trip — the same
        subset structured clone preserves in a real browser.
        """
        ctx = self.current_tab.ctx
        if ctx is None:
            return
        try:
            payload = json.dumps(state)
        except Exception:
            payload = "null"
        try:
            ctx.eval(
                "history.state = (typeof __structuredCloneDecode === 'function')"
                f" ? __structuredCloneDecode({payload}) : {payload}; true"
            )
        except Exception:
            pass
        try:
            js = (
                "(function(){var ev = new Event('popstate'); ev.state = history.state; "
                "window.dispatchEvent(ev);})()"
            )
            ctx.eval(js)
        except Exception:
            pass

    async def _engine_history_move(self, delta: int, *, max_chars: int, _depth: int = 0) -> Page:
        """history.back()/forward()/go(delta) from page JS: move the tab's
        history to the target entry and honor its document identity.

        Same-document target (pushState entry of the live document): a live
        traversal — no script re-run (so a page that pushState'd on load does
        NOT re-push), popstate into the live ctx, entry page refreshed from
        the live DOM.

        Cross-document target: restore its stored document, rebuild the live
        engine context, and dispatch popstate with the entry's pushed state.
        Chained moves (a popstate listener or restored page calling back()
        again) are consumed up to a bounded depth — a runaway history loop
        must not hang the session.
        """
        if _depth >= 4:
            return self.page  # type: ignore[return-value]
        tab = self.current_tab
        target = tab.index + int(delta)
        if not 0 <= target < len(tab.history):
            # Out-of-range is a no-op in a real browser, not an error.
            return self.page  # type: ignore[return-value]
        same_doc = self._goto_history_index(target)
        if same_doc:
            # popstate already dispatched into the live ctx (and a listener
            # may have requested another move): drain it, bounded.
            nxt = self._pending_engine_move()
            if nxt:
                return await self._engine_history_move(nxt, max_chars=max_chars, _depth=_depth + 1)
            page = self.page
            assert page is not None
            return page
        state = (tab.snapshots[target] or {}).get("history_state") if target < len(tab.snapshots) else None
        new_html, js_info = await run_scripts(
            self.html, self.url, self.client, force=True, storage_seed=self._storage_seed(),
            ctx=None,
            history_length=max(1, len(self.history)),
        )
        self.current_tab.ctx = js_info.get("ctx")
        self._absorb_storage(js_info)
        self._absorb_history(js_info)
        self._dispatch_popstate(state)
        result = FetchResult(
            url=self.url,
            final_url=self.url,
            status=200,
            content_type="text/html",
            kind="html",
            body=new_html,
        )
        captcha = await inspect_captcha(
            new_html,
            self.url,
            self.client,
            self.frame_documents,
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
        )
        page = snapshot(parse(result), max_chars=max_chars)
        page = _decorate(page, new_html, bool(js_info.get("ran")), js_info, captcha=captcha.report)
        pushed = self._push(page, new_html, replace=True)
        # Chained traversals: the restored page's own load may request a move
        # (drained into js_info), and a popstate listener (dispatched above)
        # may request another — both are consumed, bounded.
        nxt = int((js_info or {}).get("history_move") or 0) or self._pending_engine_move()
        if nxt:
            return await self._engine_history_move(nxt, max_chars=max_chars, _depth=_depth + 1)
        return pushed

    def _pending_engine_move(self) -> int:
        """Read and reset the live ctx's pending history move counter."""
        ctx = self.current_tab.ctx
        if ctx is None:
            return 0
        try:
            move = int(float(ctx.eval("Number(globalThis.__WR_HISTORY_MOVE || 0)") or 0))
            if move:
                ctx.eval("globalThis.__WR_HISTORY_MOVE = 0; true")
            return move
        except Exception:
            return 0

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

    def _push(self, page: Page, html: str | None = None, replace: bool = False, doc_id: int | None = None) -> Page:
        return self.current_tab.push(
            page, html, headers=getattr(page, "headers", None), replace=replace, doc_id=doc_id
        )

    async def goto(self, url: str, *, max_chars: int = 8000, js: str = "auto") -> Page:
        page, html, frames, captcha, js_info = await read_live(
            url,
            client=self.client,
            max_chars=max_chars,
            js=js,  # type: ignore[arg-type]
            sprite_endpoint=f"/v1/sessions/{self.id}/captcha/sprite",
            return_js_info=True,
            storage_seed=self._storage_seed(url),
            history_length=max(1, len(self.history) + 1),
        )
        self.frame_documents = frames
        self.captcha_inspection = captcha
        self.current_tab.ctx = js_info.get("ctx") if js_info else None
        self._absorb_storage(js_info, url)
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
            # pushState during page load carries the NEWLY loaded document —
            # absorb after the push so the entries have a page to carry.
            self._absorb_history(js_info)
            await self._load_popup_tabs((js_info or {}).get("popups") or [])
            return pushed

        pushed = self._push(page, html)
        self._absorb_history(js_info)
        # A load-time history.back()/go() (e.g. inside a setTimeout during page
        # load) is a real traversal in a browser: consume it here instead of
        # silently dropping the move until some later action pass.
        move = int((js_info or {}).get("history_move") or 0)
        if move:
            return await self._engine_history_move(move, max_chars=max_chars)
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

    async def get_video(
        self,
        url: str,
        *,
        dest_dir: str | None = None,
        format: str | None = None,
        use_session: bool = True,
        max_bytes: int | None = None,
        timeout: float | None = None,
    ):
        """Download a video through the video lane (direct file or yt-dlp).

        The session's cookies reach the extractor only when use_session is
        set (default True here: the session's login state is the point of
        going through the session). Returns video.py's VideoDownload record:
        path, bytes, sha256, extractor metadata, and honest errors — policy
        refusals included.
        """
        from webresearch.video import DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT
        from webresearch.video import get_video as _get_video

        absolute = url if url.startswith(("http://", "https://")) or not self.url else urljoin(self.url, url)
        return await _get_video(
            absolute,
            self.client,
            dest_dir=dest_dir,
            format=format,
            use_session=use_session,
            max_bytes=max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )

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
        if js_outcome in ("prevented", "dialog") or form.method.upper() == "DIALOG":
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
                storage_seed=self._storage_seed(result.final_url or form.action),
                history_length=max(1, len(self.history) + 1),
            )
            js_ran = bool(js_info.get("ran"))
            self._absorb_storage(js_info, result.final_url or form.action)
            self._absorb_history(js_info)
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
        pushed = self._push(page, html)
        # The POST response document is also a load: a load-time history
        # traversal request from its scripts is consumed here (same contract
        # as goto()).
        move = int((js_info or {}).get("history_move") or 0)
        if move:
            return await self._engine_history_move(move, max_chars=max_chars)
        return pushed

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
            self.html, self.url, self.client, force=True, actions=actions, ctx=self.current_tab.ctx,
            storage_seed=self._storage_seed(),
            history_length=max(1, len(self.history)),
        )
        self.current_tab.ctx = js_info.get("ctx") or self.current_tab.ctx
        self._absorb_storage(js_info)
        self._absorb_history(js_info)
        # submit() reads the engine's live form state (fields/files the page
        # JS actually collected) from this snapshot.
        self._last_form_js_info = js_info
        outcome = str(js_info.get("submit_outcome") or "submitted")
        if js_info.get("navigated"):
            nav_url = urljoin(self.url, js_info["navigated"])
            await self.goto(nav_url, max_chars=max_chars)
            return "prevented"
        if outcome in ("prevented", "dialog"):
            # JS or dialog handled submission (validation, SPA, or method=dialog).
            # Rebuild page from JS state.
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
                    cached=self.frame_documents,
                    parent_ctx=self.current_tab.ctx,
                )
                if new_html
                else self.frame_documents
            )
            # Same-document pass: reuse live frames; deliver each frame's
            # messages to the parent exactly once (cursor).
            from webresearch.engine.frames import (
                _route_port_msgs,
                _resolver,
                drain_frame_posts,
                frame_message_roundtrip,
                is_port_message,
            )

            child_msgs: list[dict] = []
            for f in frames:
                drain_frame_posts(f)
                pms = f.post_messages
                if len(pms) > f.delivered_count:
                    for pm in pms[f.delivered_count :]:
                        if pm.get("target") in {"parent", "top", "reply", "*", ""} or is_port_message(pm):
                            pm = dict(pm)
                            pm["source_window"] = {"window": "frame", "src": f.seed_src or f.url}
                            child_msgs.append(pm)
                    f.delivered_count = len(pms)
            if child_msgs and js_ran:
                # MessagePort traffic (split handshake channels) routes
                # directly into the owning realm; only window messages ride
                # the parent lane.
                port_msgs = [pm for pm in child_msgs if is_port_message(pm)]
                window_msgs = [pm for pm in child_msgs if not is_port_message(pm)]
                if port_msgs:
                    await _route_port_msgs(
                        port_msgs,
                        parent_ctx=self.current_tab.ctx,
                        parent_html=new_html,
                        parent_url=self.url,
                        client=self.client,
                        frames=frames,
                    )
                # Replies (e.source.postMessage — the standard cross-frame
                # reply form) carry the child's origin in msg["origin"] (the
                # engine records the sender), so the parent's delivery
                # verifies them like any other message.
                # Deliver into the LIVE parent context: a fresh context here
                # rebuilt the page, so every widget reply was recorded into a
                # throwaway info dict and destroyed — the frame waited forever.
                if window_msgs:
                    pumped_html, extra_js = await run_scripts(
                        new_html, self.url, self.client, force=True, messages=window_msgs,
                        ctx=self.current_tab.ctx,
                        storage_seed=self._storage_seed(),
                        frames=frames,
                    )
                    self.current_tab.ctx = extra_js.get("ctx") or self.current_tab.ctx
                    self._absorb_storage(extra_js)
                    self._absorb_history(extra_js)
                    if extra_js.get("navigated"):
                        js_info["navigated"] = extra_js["navigated"]
                    if extra_js.get("form_submit"):
                        js_info["form_submit"] = extra_js["form_submit"]
                    new_html = pumped_html
                    result.body = new_html
                    from webresearch.engine.frames import _wire_received_ports

                    _wire_received_ports(
                        window_msgs,
                        receiving_ctx=self.current_tab.ctx,
                        resolve_ctx_by_nonce=_resolver(self.current_tab.ctx, frames),
                    )
                await frame_message_roundtrip(
                    frames,
                    parent_ctx=self.current_tab.ctx,
                    parent_url=self.url,
                    parent_html=new_html,
                    client=self.client,
                    initial_parent_messages=(extra_js.get("post_messages") if window_msgs else []) or [],
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
                f"no target [{ref}] for key press. Re-inspect or query the action surface to obtain a valid ref."
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
                    res_html, dest, self.client, headers=result.headers,
                    storage_seed=self._storage_seed(dest),
                )
                result = result.model_copy(update={"body": res_html})
                self.current_tab.ctx = js_info_post.get("ctx") or None
                self._absorb_storage(js_info_post, dest)
                self._absorb_history(js_info_post)
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
        surface = collect(self.html, self.url, max_actions=max_actions)
        # CSS-hidden controls are not actionable in a real browser; the
        # engine's cascade (stylesheet rules, hidden ancestors — not just
        # the inline styles collect() can see) decides. When an engine
        # context exists it arbitrates EVERY control, including the ones
        # flagged inline-hidden: author CSS can override the UA [hidden]
        # rule (#x[hidden] { display: block }), so the flag alone must not
        # drop a control the cascade says is visible. Without a ctx the
        # inline flag is the honest fallback. Hidden controls are excluded
        # from the list but counted on the surface: never silently dropped.
        candidates = surface.actions
        ctx = self.current_tab.ctx
        if ctx is not None and candidates:
            import json as _json

            try:
                selectors = [a.selector for a in candidates]
                raw = ctx.eval(f"__hiddenCheck({_json.dumps(selectors)})")
                flags = _json.loads(str(raw)) if raw else []
            except Exception:
                flags = []
            if len(flags) == len(candidates):
                kept: list[Action] = []
                engine_hidden = 0
                for action, is_hidden in zip(candidates, flags):
                    if is_hidden:
                        engine_hidden += 1
                    else:
                        kept.append(action)
                surface.actions = kept
                surface.hidden_controls = engine_hidden
                return surface
        # No engine (or the check could not run): the inline/ancestor flag
        # is the fallback truth. If a <style> block exists, stylesheet rules
        # could not be verified — say so on the coverage line instead of
        # silently claiming the list is clean.
        kept2 = [a for a in candidates if not a.hidden]
        surface.hidden_controls = len(candidates) - len(kept2)
        surface.stylesheet_unverified = "<style" in (self.html or "").lower()
        surface.actions = kept2
        return surface

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
                        "occurrence": action.occurrence,
                    }
                ],
                self.html,
                max_chars,
            )
        elif action.kind in {"activate", "submit"}:
            if text:
                return await self._apply(
                    [{"kind": "type", "selector": selector, "text": text, "stealth": stealth,
                      "occurrence": action.occurrence}],
                    self.html,
                    max_chars,
                )
            return await self._apply(
                [{"kind": "activate", "selector": selector, "stealth": stealth,
                  "occurrence": action.occurrence}],
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
        frame_contexts = {
            f.ref: f.js_info.get("ctx")
            for f in self.frame_documents
            if f.js_info.get("ctx") is not None
        }
        new_html, js_info = await run_scripts(
            html, self.url, self.client, force=True, actions=actions, ctx=self.current_tab.ctx,
            storage_seed=self._storage_seed(),
            history_length=max(1, len(self.history)),
            frame_contexts=frame_contexts,
        )
        self.current_tab.ctx = js_info.get("ctx") or self.current_tab.ctx
        self._absorb_storage(js_info)
        self._absorb_history(js_info)
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
        if not had_ctx and self.current_tab.pending_popstate:
            # The restored document is live again: popstate fires with the
            # entry's pushed state (real back/forward contract).
            state = self.current_tab.pending_popstate_state
            self.current_tab.pending_popstate = False
            self.current_tab.pending_popstate_state = None
            self._dispatch_popstate(state)
        # history.back()/forward()/go(delta) from page JS: restore the target
        # entry's document (fresh ctx + popstate) and return that page.
        if js_info.get("history_move"):
            return await self._engine_history_move(int(js_info["history_move"]), max_chars=max_chars)
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
                cached=self.frame_documents,
                parent_ctx=self.current_tab.ctx,
            )
            if new_html
            else self.frame_documents
        )
        # Frames stay alive across action passes now; each frame's messages
        # are delivered to the parent exactly once (the per-frame cursor),
        # instead of the whole frame tree re-running and re-sending its
        # init messages on every click/scroll/type.
        from webresearch.engine.frames import (
            _route_port_msgs,
            _resolver,
            drain_frame_posts,
            frame_message_roundtrip,
            is_port_message,
        )

        child_msgs: list[dict] = []
        for f in frames:
            drain_frame_posts(f)
            pms = f.post_messages
            if len(pms) > f.delivered_count:
                for pm in pms[f.delivered_count :]:
                    if pm.get("target") in {"parent", "top", "reply", "*", ""} or is_port_message(pm):
                        # Sender identity: the parent verifies event.source
                        # against its own iframe contentWindow — an anonymous
                        # proxy made the recaptcha-setup handshake die silently.
                        pm = dict(pm)
                        pm["source_window"] = {"window": "frame", "src": f.seed_src or f.url}
                        child_msgs.append(pm)
                f.delivered_count = len(pms)
        if child_msgs and js_ran:
            # Split MessagePort traffic from the window lane: split handshake
            # channels route directly into their owning realm.
            port_msgs = [pm for pm in child_msgs if is_port_message(pm)]
            window_msgs = [pm for pm in child_msgs if not is_port_message(pm)]
            if port_msgs:
                await _route_port_msgs(
                    port_msgs,
                    parent_ctx=self.current_tab.ctx,
                    parent_html=new_html,
                    parent_url=self.url,
                    client=self.client,
                    frames=frames,
                )
            # Replies (e.source.postMessage — the standard cross-frame reply
            # form) must reach the parent like parent/top-targeted messages;
            # the old filter dropped them.
            # Inject frame content during this run so iframe clicks work
            if window_msgs:
                pumped_html, extra_js = await run_scripts(
                    new_html, self.url, self.client, force=True, messages=window_msgs, ctx=self.current_tab.ctx,
                    storage_seed=self._storage_seed(),
                    frames=frames,
                )
                self.current_tab.ctx = extra_js.get("ctx") or self.current_tab.ctx
                self._absorb_storage(extra_js)
                self._absorb_history(extra_js)
                if extra_js.get("navigated"):
                    js_info["navigated"] = extra_js["navigated"]
                if extra_js.get("form_submit"):
                    js_info["form_submit"] = extra_js["form_submit"]
                new_html = pumped_html
                result.body = new_html
                from webresearch.engine.frames import _wire_received_ports

                _wire_received_ports(
                    window_msgs,
                    receiving_ctx=self.current_tab.ctx,
                    resolve_ctx_by_nonce=_resolver(self.current_tab.ctx, frames),
                )
            # The parent's replies to those child messages (and any further
            # exchanges they trigger) go back into the frames' live contexts:
            # stopping here left an in-flight widget handshake (recaptcha-setup)
            # waiting for a reply that was collected but never routed.
            await frame_message_roundtrip(
                frames,
                parent_ctx=self.current_tab.ctx,
                parent_url=self.url,
                parent_html=new_html,
                client=self.client,
                initial_parent_messages=(extra_js.get("post_messages") if window_msgs else []) or [],
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
                        res_html, dest_url, self.client, headers=result.headers,
                        storage_seed=self._storage_seed(dest_url),
                    )
                    result = result.model_copy(update={"body": res_html})
                    # Same as submit(): the POST response is a new document —
                    # its JS context (or None for a script-less page) replaces
                    # the previous one, so the next action drives THIS page.
                    self.current_tab.ctx = js_info_post.get("ctx") or None
                    self._absorb_storage(js_info_post, dest_url)
                    self._absorb_history(js_info_post)
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

    async def click_captcha_checkbox(self, *, max_chars: int = 8000) -> Page:
        """Click the reCAPTCHA 'I'm not a robot' checkbox inside the captcha iframe.
        
        This finds the reCAPTCHA anchor frame and clicks the checkbox element.
        Returns the page after the click is processed.
        """
        if not self.html:
            raise ValueError("no current page")
        if not self.frame_documents:
            raise ValueError("no frames loaded - navigate first")
        
        # Find the reCAPTCHA anchor frame
        captcha_frame = None
        for frame in self.frame_documents:
            if frame.challenge or "recaptcha" in frame.url.lower() or "recaptcha" in frame.html.lower():
                captcha_frame = frame
                break
        
        if not captcha_frame:
            raise ValueError("no reCAPTCHA frame found")
        
        # Build path by finding the checkbox element in the frame's tree
        from webresearch.engine.tree import html_to_tree
        tree = html_to_tree(captcha_frame.html)
        
        def find_checkbox_path(node, path=None):
            """Recursively find the checkbox element and return its path."""
            if path is None:
                path = []
            attrs = node.get("a") or {}
            class_names = (attrs.get("class") or "").lower()
            role = (attrs.get("role") or "").lower()
            tag = node.get("t") or ""
            
            # reCAPTCHA checkbox is typically a span/table-cell with checkbox role
            # or has "recaptcha-checkbox" in class name
            if role == "checkbox" or "recaptcha-checkbox" in class_names:
                return path[:]
            
            # Also check for the checkbox container
            if "recaptcha-checkbox" in class_names and "table" in tag:
                # Look for first non-text child
                for i, child in enumerate(node.get("c") or []):
                    if isinstance(child, dict) and child.get("t") != "#text":
                        return path + [i]
            
            # Recurse into children
            for i, child in enumerate(node.get("c") or []):
                if isinstance(child, dict):
                    result = find_checkbox_path(child, path + [i])
                    if result is not None:
                        return result
            return None
        
        checkbox_path = find_checkbox_path(tree) or []
        # Click the document that ran the frame's scripts. The selector hits
        # the live checkbox in that realm.
        return await self._apply(
            [{
                "kind": "click_frame",
                "frame_ref": captcha_frame.ref,
                "frame_path": checkbox_path,
                "frame_selector": "[role='checkbox'], .recaptcha-checkbox",
            }],
            self.html,
            max_chars,
        )

    async def solve_recaptcha_v2(self, *, max_rounds: int = 8, models: list[str] | None = None) -> str:
        """Solve the current page's reCAPTCHA v2 challenge; return a token.

        Drives the challenge protocol (anchor -> reload -> payload ->
        userverify) over this session's HTTP client with the session's
        fingerprint and cookies, classifying tiles with the configured
        vision models (ensemble majority vote). The returned token is a
        verifiable ``g-recaptcha-response``; whether the SITE accepts it
        is the site's server-side decision, reported honestly.
        """
        if not self.html:
            raise ValueError("no current page")
        report = self.captcha()
        if not report.present:
            raise ValueError("no CAPTCHA on the current page")
        site_key = report.site_key
        if not site_key:
            raise ValueError("no reCAPTCHA site key on the current page")

        from webresearch.settings import llm_config

        cfg = llm_config("auto")
        vision_models = models or [
            m.strip()
            for m in os.environ.get(
                "WR_VISION_MODELS",
                "gemini-3.6-flash, qwen3.5-omni-plus, qwen3-vl-plus, qwen-vl-max, gemini-3.5-flash",
            ).split(",")
            if m.strip()
        ]

        from webresearch.engine.captcha import (
            RecaptchaV2Error,
            captcha_view_with_token,
            solve_recaptcha_v2 as _solve,
            vision_grid_select,
            vision_tile_contains,
        )

        async def tile_contains(tile: bytes, goal: str) -> bool:
            return await vision_tile_contains(
                tile,
                goal,
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                models=vision_models,
            )

        async def grid_select(grid: bytes, goal: str, rows: int, cols: int) -> list[int]:
            return await vision_grid_select(
                grid,
                goal,
                rows,
                cols,
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                models=vision_models,
            )

        try:
            token = await _solve(
                self.client,
                site_key=site_key,
                site_url=self.url,
                grid_select=grid_select,
                tile_contains=tile_contains,
                max_rounds=max_rounds,
            )
        except RecaptchaV2Error as exc:
            raise ValueError(f"reCAPTCHA v2 solve failed: {exc}") from exc
        # Surface the token in the current inspection so the action surface
        # reflects reality: the page's g-recaptcha-response is now filled.
        if self.captcha_inspection is not None and report is not None:
            report.token = token
            report.view = captcha_view_with_token(report, token)
        return token


    async def click_captcha_tile(self, tile_index: int | str, *, max_chars: int = 8000) -> Page:
        """Click a specific tile in the reCAPTCHA image challenge frame."""
        if not self.html:
            raise ValueError("no current page")
        if not self.frame_documents:
            raise ValueError("no frames loaded - navigate first")
        
        # Find the challenge frame (bframe)
        challenge_frame = None
        for frame in self.frame_documents:
            if "bframe" in frame.url.lower() or "imageselect" in frame.html.lower() or "rc-imageselect" in frame.html.lower():
                challenge_frame = frame
                break
        if not challenge_frame and self.captcha_inspection:
            # Fallback to any frame that's not the anchor
            for frame in self.frame_documents:
                if frame.challenge and "anchor" not in frame.html.lower() and getattr(frame, 'ref', '') != "f1":
                    challenge_frame = frame
                    break
        if not challenge_frame and len(self.frame_documents) >= 2:
            challenge_frame = self.frame_documents[-1]  # Usually the last frame is the challenge
        
        if not challenge_frame:
            raise ValueError("no CAPTCHA challenge frame found")
        
        from webresearch.engine.tree import html_to_tree
        tree = html_to_tree(challenge_frame.html)
        
        target_idx = tile_index
        # Normalize - if it's 1-based from solve results, also try to match
        try:
            t = int(str(tile_index))
            # Try both as-is and t-1 (vision returns 1-based as per our parse)
            target_candidates = [t, t-1]
        except Exception:
            target_candidates = [tile_index]
            # Try to convert string numbers
            try:
                target_candidates = [int(str(tile_index)), int(str(tile_index))-1]
            except Exception:
                pass
        
        def find_tile_path(node, path=None, candidates=None):
            if path is None:
                path = []
            if candidates is None:
                candidates = []
            attrs = node.get("a") or {}
            class_names = (attrs.get("class") or "").lower()
            node_id = str(attrs.get("id") or "")
            
            # Look for tile elements
            if "rc-imageselect-tile" in class_names or ("tile" in class_names and "imageselect" in class_names):
                try:
                    tid = int(node_id) if node_id.isdigit() else None
                    if tid is not None and tid in candidates:
                        return path[:]
                except Exception:
                    pass
                if not candidates and "rc-imageselect-tile" in class_names:
                    # If no specific target, could return first found - but we need specific
                    pass
            
            for i, child in enumerate(node.get("c") or []):
                if isinstance(child, dict):
                    result = find_tile_path(child, path + [i], candidates)
                    if result is not None:
                        return result
            return None
        
        tile_path = find_tile_path(tree, candidates=target_candidates)
        # Build selector
        try:
            t = int(str(tile_index))
            selector = f'[id="{t}"].rc-imageselect-tile, [id="{t}"], .rc-imageselect-tile#{t}, td#{t}.rc-imageselect-tile'
        except Exception:
            selector = '.rc-imageselect-tile'
        return await self._apply(
            [{
                "kind": "click_frame",
                "frame_ref": challenge_frame.ref,
                "frame_path": tile_path or [],
                "frame_selector": selector,
            }],
            self.html,
            max_chars,
        )

    async def click_captcha_tiles(self, tile_indices: list[int] | list[str], *, max_chars: int = 8000) -> Page:
        """Click multiple tiles in the reCAPTCHA image challenge."""
        page = None
        for idx in tile_indices:
            page = await self.click_captcha_tile(idx, max_chars=max_chars)
        return page or self.page

    async def click_captcha_verify(self, *, max_chars: int = 8000) -> Page:
        """Click the verify/submit button in the reCAPTCHA challenge frame."""
        if not self.html:
            raise ValueError("no current page")
        if not self.frame_documents:
            raise ValueError("no frames loaded - navigate first")
        
        challenge_frame = None
        for frame in self.frame_documents:
            if "bframe" in frame.url.lower() or "imageselect" in frame.html.lower() or "rc-imageselect" in frame.html.lower() or "verify" in frame.html.lower():
                challenge_frame = frame
                break
        if not challenge_frame and len(self.frame_documents) >= 2:
            challenge_frame = self.frame_documents[-1]
        if not challenge_frame:
            raise ValueError("no CAPTCHA challenge frame found")
        
        from webresearch.engine.tree import html_to_tree
        tree = html_to_tree(challenge_frame.html)
        
        def find_verify_path(node, path=None):
            if path is None:
                path = []
            attrs = node.get("a") or {}
            node_id = str(attrs.get("id") or "").lower()
            class_names = (attrs.get("class") or "").lower()
            tag = node.get("t") or ""
            text = ""
            # Get text
            try:
                from webresearch.engine.tree import walk
                texts = []
                for n in walk(node):
                    if n.get("t") == "#text":
                        texts.append(n.get("x") or "")
                text = " ".join(texts).lower()
            except Exception:
                pass
            
            if "verify" in node_id or "verify" in class_names or tag == "button" and "verify" in text:
                return path[:]
            
            for i, child in enumerate(node.get("c") or []):
                if isinstance(child, dict):
                    result = find_verify_path(child, path + [i])
                    if result is not None:
                        return result
            return None
        
        verify_path = find_verify_path(tree)
        return await self._apply(
            [{
                "kind": "click_frame",
                "frame_ref": challenge_frame.ref,
                "frame_path": verify_path or [],
                "frame_selector": '#recaptcha-verify-button, button:contains("Verify"), .rc-button-default',
            }],
            self.html,
            max_chars,
        )

    async def click_captcha_solution(self, solution: dict | object = None, *, max_chars: int = 8000) -> Page:
        """Click tiles from a captcha solution and submit."""
        if solution is None:
            raise ValueError("solution required")
        # Extract tile indices
        tiles = []
        if isinstance(solution, dict):
            tiles = solution.get("tiles_selected") or solution.get("tiles") or []
        else:
            tiles = getattr(solution, "tiles_selected", []) or []
        indices = []
        for t in tiles:
            if isinstance(t, dict):
                # Try different keys
                if "tile_id" in t:
                    # tile_id might be string like "0"
                    try:
                        idx = int(t["tile_id"])
                        # If from our vision model mapping, they store original 1-based? Wait - in our code:
                        # we take tile_indices from vision (1-based) and map to report.tiles[tile_zero] where tile_zero = idx-1
                        # So selections store tile_id from report.tiles. The tile_id comes from attrs.get('id') or index.
                        # But in our test case, tiles are created with id=str(i), so tile_id would be "0","1" etc matching index.
                        # Also in the solve_captcha code: we add selection with tile_id=tile.id (from report.tiles). 
                        # And in the test "selected_ids = [s.tile_id for s in result.solution.tiles_selected]; self.assertEqual(selected_ids, ["0", "2", "6"])" - correct, because vision returns [1,3,7] meaning first/third/seventh tile, and we map them back to report.tiles at index (idx-1), so tile_id becomes report.tiles[idx-1].id which is str(idx-1) = "0","2","6". Correct.
                        indices.append(idx)
                        continue
                    except Exception:
                        pass
                if "index" in t:
                    indices.append(int(t["index"]))
                    continue
                if "row" in t and "column" in t:
                    # Can't directly map without tile list, skip
                    continue
            elif isinstance(t, (int, str)):
                try:
                    indices.append(int(t))
                    continue
                except Exception:
                    pass
        if indices:
            page = await self.click_captcha_tiles(indices, max_chars=max_chars)
            await self.click_captcha_verify(max_chars=max_chars)
            return page
        raise ValueError("no tile indices found in solution")


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

    def _goto_history_index(self, i: int) -> bool:
        """Move the tab's history pointer to entry i.

        Returns True when this was a SAME-DOCUMENT traversal: the entry was
        created by pushState on the live document (same doc_id) and the engine
        ctx is alive, so — exactly like a real browser's same-document history
        navigation — the document and its JS heap persist, no script re-runs,
        and popstate dispatches into the live ctx. Returns False for a
        cross-document restore: the ctx belongs to the document being left
        and is dropped, to be rebuilt from the stored HTML on the next action
        pass (disclosed approximation: a browser with bfcache would restore
        the whole heap; ours re-runs the destination's scripts).
        """
        tab = self.current_tab
        if not 0 <= i < len(tab.history):
            raise ValueError(f"no page at history index {i}")
        snap = tab.snapshots[i] if i < len(tab.snapshots) else None
        cur_snap = tab.snapshots[tab.index] if 0 <= tab.index < len(tab.snapshots) else None
        target_doc = (snap or {}).get("doc_id")
        cur_doc = (cur_snap or {}).get("doc_id")
        same_doc = (
            tab.ctx is not None
            and target_doc is not None
            and target_doc == cur_doc
            and (snap or {}).get("html") is not None
        )
        if same_doc:
            # Save the position we are leaving (a real browser updates the
            # leaving entry's scroll offset on traversal).
            try:
                leaving_y = int(float(tab.ctx.eval("scrollY || 0") or 0))
            except Exception:
                leaving_y = (cur_snap or {}).get("scroll_y") or 0
            if cur_snap is not None:
                cur_snap["scroll_y"] = leaving_y
            tab.index = i
            self.html = snap["html"]
            self.url = snap["url"]
            tab.headers = dict(snap.get("headers") or {})
            self.frame_documents = list(snap.get("frames") or [])
            tab.captcha_inspection = snap.get("captcha")
            # The engine's location must reflect the traversal BEFORE popstate
            # (a real browser's popstate listener reads the new URL).
            try:
                ctx_js_url = json.dumps(snap.get("url") or self.url)
                tab.ctx.eval(
                    "if (typeof _updateLocationQuietly === 'function') "
                    f"_updateLocationQuietly({ctx_js_url}); true"
                )
            except Exception:
                pass
            # popstate dispatches into the LIVE context first — a real
            # browser fires popstate before scroll restoration, and SPA
            # routers mutate the DOM from that listener.
            self._dispatch_popstate((snap or {}).get("history_state"))
            # Restore the entry's scroll offset unless the page opted out
            # (history.scrollRestoration = "manual"). Zero is a position too:
            # an entry left at the top must scroll back to 0, not keep the
            # leaving entry's offset (the old falsy check did exactly that).
            try:
                restoration = str(tab.ctx.eval("history.scrollRestoration") or "auto")
            except Exception:
                restoration = "auto"
            if restoration != "manual":
                try:
                    tab.ctx.eval(f"scrollTo(0, {int((snap or {}).get('scroll_y') or 0)}); true")
                except Exception:
                    pass
            # The live DOM (post-popstate-listeners) is the truth for a
            # shared document: refresh the target entry's page model from it.
            self._refresh_entry_page_from_ctx(i)
            return True
        # Cross-document restore: the entry's document is not the live one.
        tab.index = i
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
        # popstate fires when the restored document is next rebuilt (the
        # engine ctx was dropped above), carrying the entry's pushed state —
        # the same event contract as a real browser's back/forward.
        tab.pending_popstate = True
        tab.pending_popstate_state = (snap or {}).get("history_state")
        tab.ctx = None
        return False

    def _refresh_entry_page_from_ctx(self, i: int) -> None:
        """Rebuild entry i's Page model from the live engine DOM."""
        tab = self.current_tab
        ctx = tab.ctx
        if ctx is None or not 0 <= i < len(tab.history):
            return
        try:
            live_html = str(ctx.eval("__serialize()") or self.html)
        except Exception:
            live_html = self.html
        if not live_html:
            return
        from webresearch.fetch import FetchResult as _FR

        result = _FR(
            url=self.url,
            final_url=self.url,
            status=200,
            content_type="text/html",
            kind="html",
            body=live_html,
        )
        page = snapshot(parse(result), max_chars=8000)
        # Keep the snapshot's html as-is (the stored document is the restore
        # source); only the agent-facing page model follows the live DOM.
        tab.history[i] = page

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
        if self._shell is not None:
            self._shell.close()
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
