"""Optional real-Chromium render lane.

The QuickJS mini-DOM is the fast lane. When a page is a genuine SPA, ships ES
module bundles, or throws a bot challenge, a real Blink engine gives correct
layout, canvas/WebGL/Audio fingerprints, and trusted input events.

This module is optional: if no anti-detect Chromium driver is installed
(`patchright` preferred, then `playwright`), ``render`` returns ``None`` and
the caller keeps the QuickJS result.
"""

from __future__ import annotations

import os

DEFAULT_ENABLED = os.environ.get("WR_USE_CHROMIUM", "auto")

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _load_driver():
    """Return an async_playwright-compatible entrypoint, preferring patchright."""
    if DEFAULT_ENABLED == "0":
        return None
    try:  # anti-detect fork first
        from patchright.async_api import async_playwright  # type: ignore

        return async_playwright
    except Exception:
        pass
    try:
        from playwright.async_api import async_playwright  # type: ignore

        return async_playwright
    except Exception:
        return None


def available() -> bool:
    if DEFAULT_ENABLED == "0":
        return False
    return _load_driver() is not None


async def render(
    url: str,
    *,
    timeout_ms: int = 20000,
    wait_until: str = "networkidle",
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    """Render a URL in Chromium. Returns (html, final_url) or None."""
    driver = _load_driver()
    if driver is None:
        return None
    launch_kwargs: dict = {"headless": True}
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}
    try:
        async with driver() as pw:
            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=_CHROME_UA,
                locale="en-US",
                viewport={"width": 1280, "height": 800},
                extra_http_headers=headers or {},
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            except Exception:
                # networkidle can time out on long-polling sites; fall back.
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            html = await page.content()
            final_url = page.url
            await context.close()
            await browser.close()
            return html, final_url
    except Exception:
        return None


def should_escalate(html: str, *, js_info: dict | None = None) -> bool:
    """Heuristic: is the QuickJS result suspiciously empty / module-heavy?"""
    if not available():
        return False
    info = js_info or {}
    if info.get("skipped_modules"):
        return True
    low = html.lower()
    spa_markers = ('id="root"', 'id="app"', "data-reactroot", "__next")
    if any(marker in low for marker in spa_markers):
        return True
    return False


class ChromiumSession:
    """Persistent interactive Chromium page for one agent session.

    Keeps the browser/context/page alive across goto/click/type so SPA state
    and cookies persist, and input goes through real CDP (trusted events).
    """

    def __init__(self, *, proxy: str | None = None, headers: dict[str, str] | None = None) -> None:
        self.proxy = proxy
        self.headers = headers or {}
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None
        self.url = ""

    async def _ensure(self) -> None:
        if self.page is not None:
            return
        driver = _load_driver()
        if driver is None:
            raise RuntimeError("chromium driver not installed")
        self._pw = await driver().__aenter__()
        launch_kwargs: dict = {"headless": True}
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}
        self._browser = await self._pw.chromium.launch(**launch_kwargs)
        self._context = await self._browser.new_context(
            user_agent=_CHROME_UA,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
            extra_http_headers=self.headers,
        )
        self.page = await self._context.new_page()

    async def goto(self, url: str, *, timeout_ms: int = 20000) -> tuple[str, str]:
        await self._ensure()
        try:
            await self.page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        self.url = self.page.url
        return await self.page.content(), self.page.url

    async def content(self) -> str:
        await self._ensure()
        return await self.page.content()

    async def _element_box(self, selector: str) -> dict | None:
        handle = await self.page.query_selector(selector)
        if handle is None:
            return None
        try:
            return await handle.bounding_box()
        except Exception:
            return None

    async def click(self, selector: str) -> bool:
        """Real CDP mouse click at the element center (trusted event)."""
        await self._ensure()
        box = await self._element_box(selector)
        if box is None:
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await self.page.mouse.click(x, y)
        await self.page.wait_for_timeout(150)
        self.url = self.page.url
        return True

    async def type(self, selector: str, text: str) -> bool:
        """Focus element and type via CDP keyboard (trusted events)."""
        await self._ensure()
        handle = await self.page.query_selector(selector)
        if handle is None:
            return False
        await handle.click()
        await self.page.keyboard.type(text)
        await self.page.wait_for_timeout(100)
        self.url = self.page.url
        return True

    async def press(self, key: str) -> None:
        await self._ensure()
        await self.page.keyboard.press(key)

    async def scroll(self, y: int = 800) -> None:
        await self._ensure()
        await self.page.mouse.wheel(0, y)
        await self.page.wait_for_timeout(200)

    async def screenshot(self) -> bytes:
        await self._ensure()
        return await self.page.screenshot()

    _PATH_JS = """
    (path) => {
      let n = document;
      for (const i of path) {
        if (!n || !n.children) return null;
        n = n.children[i] || null;
        if (!n) return null;
      }
      if (!n) return null;
      const r = n.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, tag: n.tagName };
    }
    """

    async def _box_by_path(self, path: list[int]) -> dict | None:
        await self._ensure()
        try:
            box = await self.page.evaluate(self._PATH_JS, path)
        except Exception:
            return None
        if not box or (box.get("width") == 0 and box.get("height") == 0):
            return None
        return box

    async def click_path(self, path: list[int]) -> bool:
        """CDP mouse click at the center of the element addressed by DOM index path."""
        box = await self._box_by_path(path)
        if box is None:
            return False
        x = box["x"] + max(box["width"], 1) / 2
        y = box["y"] + max(box["height"], 1) / 2
        await self.page.mouse.move(x - 4, y - 2)
        await self.page.mouse.click(x, y)
        await self.page.wait_for_timeout(200)
        self.url = self.page.url
        return True

    async def type_path(self, path: list[int], text: str) -> bool:
        """Click element by path to focus, then type via CDP keyboard."""
        box = await self._box_by_path(path)
        if box is None:
            return False
        x = box["x"] + max(box["width"], 1) / 2
        y = box["y"] + max(box["height"], 1) / 2
        await self.page.mouse.click(x, y)
        await self.page.keyboard.type(text)
        await self.page.wait_for_timeout(120)
        self.url = self.page.url
        return True

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.__aexit__(None, None, None)
        except Exception:
            pass
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None