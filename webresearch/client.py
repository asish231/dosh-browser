"""HTTP client for a running webresearch server."""

from __future__ import annotations

from typing import Any

import httpx


class Client:
    def __init__(self, base_url: str = "http://127.0.0.1:4747", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def read(self, url: str, *, max_chars: int = 8000, js: str = "auto") -> dict:
        return await self.invoke("browser_read", url=url, max_chars=max_chars, js=js)

    async def search(self, q: str, *, max_hits: int = 8, engine: str = "auto") -> dict:
        return await self.invoke("browser_search", q=q, max_hits=max_hits, engine=engine)

    async def images(self, q: str, *, max_hits: int = 8, engine: str = "auto") -> dict:
        return await self.invoke("browser_images", q=q, max_hits=max_hits, engine=engine)

    async def click(self, session_id: str, ref: str) -> dict:
        return await self.invoke("browser_click", session_id=session_id, ref=ref)

    async def captcha(self, *, session_id: str | None = None, url: str | None = None) -> dict:
        arguments = {}
        if session_id:
            arguments["session_id"] = session_id
        if url:
            arguments["url"] = url
        return await self.invoke("browser_captcha", **arguments)

    async def captcha_code(self, *, session_id: str | None = None, url: str | None = None) -> dict:
        arguments = {}
        if session_id:
            arguments["session_id"] = session_id
        if url:
            arguments["url"] = url
        return await self.invoke("browser_captcha_code", **arguments)

    async def frames(self, session_id: str) -> list[dict]:
        return await self.invoke("browser_frames", session_id=session_id)

    async def frame_source(
        self,
        session_id: str,
        ref: str,
        *,
        max_chars: int = 20_000,
    ) -> dict:
        return await self.invoke(
            "browser_frame_source",
            session_id=session_id,
            ref=ref,
            max_chars=max_chars,
        )

    async def tools(self) -> list[dict]:
        r = await self._http.get("/v1/tools")
        r.raise_for_status()
        return r.json()["tools"]

    async def invoke(self, name: str, **arguments: Any) -> dict:
        r = await self._http.post("/v1/invoke", json={"name": name, "arguments": arguments})
        r.raise_for_status()
        return r.json()
