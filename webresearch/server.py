from __future__ import annotations

import contextvars
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from webresearch.dispatch import invoke as run_tool
from webresearch.extract import extract as extract_fields
from webresearch.fetch import client_kwargs
from webresearch.fetch import fetch as http_fetch
from webresearch.parse import parse as parse_doc
from webresearch.read import captcha_code as dump_url_captcha_code
from webresearch.read import read as read_url
from webresearch.search import search as web_search
from webresearch.session import Browser
from webresearch.tools import TOOLS
from webresearch.types import (
    CaptchaCodeDump,
    CaptchaReport,
    ExtractResult,
    FrameInfo,
    Page,
    SearchResult,
)

TAGS = [
    {"name": "agent", "description": "Tools + invoke. Start here for LLMs."},
    {"name": "read", "description": "One-shot URL → LLM page model."},
    {"name": "search", "description": "Google request, DuckDuckGo fallback."},
    {"name": "session", "description": "Cookies, history, inspect/click/type."},
    {"name": "captcha", "description": "Separate challenge/frame/resource inspection."},
    {"name": "llm", "description": "Ask GLM (z.ai) to browse using these tools."},
]


class UrlBody(BaseModel):
    url: str
    max_chars: int = 8000
    max_links: int = 50
    js: str = Field(default="auto", description="auto | always | never")


class SearchBody(BaseModel):
    q: str
    max_hits: int = 8
    engine: str = Field(default="auto", description="auto | google | ddg")


class ExtractBody(BaseModel):
    url: str
    fields: dict[str, str] = Field(default_factory=lambda: {"title": "title", "description": "description"})


class SessionCreate(BaseModel):
    headers: dict[str, str] | None = None
    proxy: str | None = None


class GotoBody(BaseModel):
    url: str
    max_chars: int = 8000
    js: str = "auto"


class OpenBody(BaseModel):
    n: int
    max_chars: int = 8000


class SubmitBody(BaseModel):
    n: int
    fields: dict[str, str] = Field(default_factory=dict)
    max_chars: int = 8000


class ClickBody(BaseModel):
    ref: str
    max_chars: int = 8000


class TypeBody(BaseModel):
    ref: str
    text: str
    max_chars: int = 8000


class ScrollBody(BaseModel):
    y: int = 800
    x: int = 0
    max_chars: int = 8000


class ActBody(BaseModel):
    """One action from the surface. dx/dy apply to drag controls."""

    ref: str
    text: str = ""
    dx: float | None = None
    dy: float | None = None
    max_chars: int = 8000


class ActionItem(BaseModel):
    """One control, with how an agent can actually actuate it."""

    ref: str
    kind: str
    role: str
    label: str
    target: str = ""
    field_name: str = ""
    tag: str = ""
    checked: bool | None = None
    disabled: bool = False
    notes: str = ""
    http_callable: bool = False


class ActionList(BaseModel):
    """The full inventory plus an honest account of what was omitted."""

    coverage: str
    complete: bool
    total_found: int
    returned: int
    truncated: int
    engine_only: int
    actions: list[ActionItem]


class InvokeBody(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class AskBody(BaseModel):
    q: str
    max_rounds: int = 8
    model: str = Field(default="glm", description="glm (GLM-4.7-Flash) | mercury | auto")
    thinking: bool = False


_current_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_tenant_id", default="default"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    browser = Browser()
    app.state.browser = browser
    app.state.tenant_clients = {}
    default_client = httpx.AsyncClient(**client_kwargs())
    app.state.tenant_clients["default"] = default_client
    app.state.http = default_client
    yield
    await browser.close_all()
    for client in list(app.state.tenant_clients.values()):
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="webresearch",
        description=(
            "LLM-native browser. HTTP + QuickJS mini-DOM (not Chromium). "
            "GET /v1/tools then POST /v1/invoke."
        ),
        version="0.3.0",
        lifespan=lifespan,
        openapi_tags=TAGS,
    )

    api_token = os.environ.get("WR_API_TOKEN", "")
    tenant_tokens_raw = os.environ.get("WR_TENANT_TOKENS", "").strip()
    tenant_tokens: dict[str, str] = {}
    if tenant_tokens_raw:
        try:
            parsed = json.loads(tenant_tokens_raw)
            if isinstance(parsed, dict):
                tenant_tokens = {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            for pair in tenant_tokens_raw.split(","):
                if ":" in pair:
                    tok, tid = pair.split(":", 1)
                    tenant_tokens[tok.strip()] = tid.strip()

    @app.middleware("http")
    async def tenant_isolation_middleware(request: Request, call_next):
        supplied = request.headers.get("x-api-token", "").strip()
        auth = request.headers.get("authorization", "").strip()
        if auth.lower().startswith("bearer "):
            supplied = supplied or auth.split(" ", 1)[1].strip()

        if (api_token or tenant_tokens) and request.url.path.startswith("/v1"):
            authenticated = False
            if tenant_tokens and supplied in tenant_tokens:
                authenticated = True
            elif api_token and supplied == api_token:
                authenticated = True
            if not authenticated:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

        header_tenant = request.headers.get("x-tenant-id", "").strip()
        if header_tenant:
            tenant_id = header_tenant
        elif supplied and supplied in tenant_tokens:
            tenant_id = tenant_tokens[supplied]
        elif supplied and api_token:
            tenant_id = "default"
        else:
            tenant_id = "default"

        c_token = _current_tenant_id.set(tenant_id)
        try:
            return await call_next(request)
        finally:
            _current_tenant_id.reset(c_token)

    def get_tenant_http(tenant_id: str = "default") -> httpx.AsyncClient:
        clients: dict[str, httpx.AsyncClient] = getattr(app.state, "tenant_clients", {})
        if tenant_id not in clients:
            clients[tenant_id] = httpx.AsyncClient(**client_kwargs())
        return clients[tenant_id]

    def http() -> httpx.AsyncClient:
        return get_tenant_http(_current_tenant_id.get())

    def browser() -> Browser:
        return app.state.browser

    def session_or_404(sid: str):
        try:
            return browser().get(sid, tenant_id=_current_tenant_id.get())
        except KeyError:
            raise HTTPException(404, f"session {sid} not found") from None
        except PermissionError:
            raise HTTPException(403, f"access denied to session {sid}") from None

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home():
        return (
            "<!doctype html><meta charset=utf-8>"
            "<title>webresearch</title>"
            "<style>body{font:16px/1.5 ui-sans-serif,system-ui;max-width:40rem;margin:3rem auto;"
            "padding:0 1.25rem;color:#1c1917;background:#fafaf9}"
            "h1{font:600 1.35rem/1.3 ui-sans-serif,system-ui}"
            "a{color:#1d4ed8}"
            "code{font:14px ui-monospace,Menlo,monospace;background:#f5f5f4;padding:.1em .35em}</style>"
            "<h1>webresearch</h1>"
            "<p>Agent browser. Not for humans &mdash; use the API.</p>"
            "<p><a href='/docs'>OpenAPI</a> · <a href='/v1/tools'>/v1/tools</a> · <code>POST /v1/invoke</code></p>"
        )

    @app.get("/health")
    async def health():
        return {"ok": True, "engine": "quickjs"}

    @app.get("/v1/device", tags=["agent"])
    async def api_device():
        """Real host hardware profile (CPU/GPU/secure hardware/VM detection)."""
        from webresearch.device import probe

        return probe().to_dict()

    @app.get("/v1/tools", tags=["agent"])
    async def api_tools():
        return {"tools": TOOLS}

    @app.post("/v1/invoke", tags=["agent"])
    async def api_invoke(body: InvokeBody):
        try:
            return await run_tool(http(), browser(), body.name, body.arguments or {})
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/read", response_model=Page, tags=["read"])
    async def api_read(body: UrlBody) -> Page:
        js = body.js if body.js in {"auto", "always", "never"} else "auto"
        return await read_url(
            body.url, client=http(), max_chars=body.max_chars, max_links=body.max_links, js=js  # type: ignore[arg-type]
        )

    @app.post("/v1/captcha", response_model=CaptchaReport, tags=["captcha"])
    async def api_captcha(body: UrlBody) -> CaptchaReport:
        js = body.js if body.js in {"auto", "always", "never"} else "auto"
        page = await read_url(
            body.url,
            client=http(),
            max_chars=body.max_chars,
            max_links=body.max_links,
            js=js,  # type: ignore[arg-type]
        )
        return page.captcha or CaptchaReport(
            present=False,
            note="No supported CAPTCHA or bot challenge was detected.",
            view="CAPTCHA: not detected",
        )

    @app.post("/v1/captcha/code", response_model=CaptchaCodeDump, tags=["captcha"])
    async def api_captcha_code(body: UrlBody) -> CaptchaCodeDump:
        js = body.js if body.js in {"auto", "always", "never"} else "auto"
        return await dump_url_captcha_code(body.url, client=http(), js=js)  # type: ignore[arg-type]

    @app.post("/v1/search", response_model=SearchResult, tags=["search"])
    async def api_search(body: SearchBody) -> SearchResult:
        engine = body.engine if body.engine in {"auto", "google", "ddg"} else "auto"
        return await web_search(body.q, client=http(), max_hits=body.max_hits, engine=engine)  # type: ignore[arg-type]

    @app.get("/v1/models", tags=["llm"])
    async def api_models():
        from webresearch.settings import available_models

        return {"models": available_models()}

    @app.post("/v1/ask", tags=["llm"])
    async def api_ask(body: AskBody):
        from webresearch.agent import LlmRateLimitError
        from webresearch.agent import ask as llm_ask

        try:
            result = await llm_ask(
                body.q,
                model=body.model,
                max_rounds=max(1, min(body.max_rounds, 12)),
                thinking=body.thinking,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LlmRateLimitError as exc:
            raise HTTPException(429, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        return result

    @app.post("/v1/extract", response_model=ExtractResult, tags=["read"])
    async def api_extract(body: ExtractBody) -> ExtractResult:
        result = await http_fetch(body.url, client=http())
        return extract_fields(parse_doc(result), body.fields)

    @app.post("/v1/sessions", tags=["session"])
    async def api_create_session(body: SessionCreate | None = None):
        body = body or SessionCreate()
        session = await browser().create(
            headers=body.headers,
            proxy=body.proxy,
            tenant_id=_current_tenant_id.get(),
        )
        return {"id": session.id}

    @app.get("/v1/sessions/{sid}/page", response_model=Page, tags=["session"])
    async def api_current_page(sid: str) -> Page:
        session = session_or_404(sid)
        if session.page is None:
            raise HTTPException(404, "no current page")
        return session.page

    @app.post("/v1/sessions/{sid}/goto", response_model=Page, tags=["session"])
    async def api_goto(sid: str, body: GotoBody) -> Page:
        js = body.js if body.js in {"auto", "always", "never"} else "auto"
        return await session_or_404(sid).goto(body.url, max_chars=body.max_chars, js=js)

    @app.post("/v1/sessions/{sid}/open", response_model=Page, tags=["session"])
    async def api_open(sid: str, body: OpenBody) -> Page:
        try:
            return await session_or_404(sid).open(body.n, max_chars=body.max_chars)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{sid}/submit", response_model=Page, tags=["session"])
    async def api_submit(sid: str, body: SubmitBody) -> Page:
        try:
            return await session_or_404(sid).submit(body.n, body.fields, max_chars=body.max_chars)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/sessions/{sid}/inspect", tags=["session"])
    async def api_inspect(sid: str):
        try:
            targets, challenges = session_or_404(sid).inspect()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"targets": targets, "challenges": challenges}

    @app.get("/v1/sessions/{sid}/acd", tags=["session"])
    async def api_acd(sid: str):
        session = session_or_404(sid)
        if session.page is None or session.page.acd is None:
            raise HTTPException(404, "no ACD for the current page")
        return session.page.acd

    @app.get("/v1/sessions/{sid}/query", tags=["session"])
    async def api_query(sid: str, term: str = "", limit: int = 40):
        try:
            return session_or_404(sid).query(term, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/sessions/{sid}/slice", tags=["session"])
    async def api_slice(
        sid: str, start: str = "", end: str = "", limit: int = 120
    ):
        try:
            return session_or_404(sid).slice(start, end, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get(
        "/v1/sessions/{sid}/frames",
        response_model=list[FrameInfo],
        tags=["session", "captcha"],
    )
    async def api_frames(sid: str) -> list[FrameInfo]:
        try:
            return session_or_404(sid).frames()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get(
        "/v1/sessions/{sid}/frames/{ref}/source",
        response_class=PlainTextResponse,
        tags=["session", "captcha"],
    )
    async def api_frame_source(sid: str, ref: str) -> PlainTextResponse:
        try:
            frame = session_or_404(sid).frame_source(ref)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return PlainTextResponse(
            frame.html,
            media_type="text/html",
            headers={"X-Frame-URL": frame.url},
        )

    @app.get(
        "/v1/sessions/{sid}/captcha",
        response_model=CaptchaReport,
        tags=["session", "captcha"],
    )
    async def api_session_captcha(sid: str) -> CaptchaReport:
        try:
            return session_or_404(sid).captcha()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get(
        "/v1/sessions/{sid}/captcha/code",
        response_model=CaptchaCodeDump,
        tags=["session", "captcha"],
    )
    async def api_session_captcha_code(sid: str) -> CaptchaCodeDump:
        try:
            return session_or_404(sid).captcha_code()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get(
        "/v1/sessions/{sid}/captcha/code.txt",
        response_class=PlainTextResponse,
        tags=["session", "captcha"],
    )
    async def api_session_captcha_code_text(sid: str) -> PlainTextResponse:
        try:
            dump = session_or_404(sid).captcha_code()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return PlainTextResponse(dump.code, media_type="text/plain; charset=utf-8")

    @app.get(
        "/v1/sessions/{sid}/captcha/sprite",
        response_class=Response,
        tags=["session", "captcha"],
    )
    async def api_captcha_sprite(sid: str) -> Response:
        try:
            data, content_type = session_or_404(sid).captcha_sprite()
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "default-src 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/sessions/{sid}/click", response_model=Page, tags=["session"])
    async def api_click(sid: str, body: ClickBody) -> Page:
        try:
            return await session_or_404(sid).click(body.ref, max_chars=body.max_chars)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{sid}/type", response_model=Page, tags=["session"])
    async def api_type(sid: str, body: TypeBody) -> Page:
        try:
            return await session_or_404(sid).type(body.ref, body.text, max_chars=body.max_chars)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/v1/sessions/{sid}/actions", response_model=ActionList, tags=["session"])
    async def api_actions(sid: str, max_actions: int = 200) -> ActionList:
        session = session_or_404(sid)
        surface = session.action_surface(max_actions=max_actions)
        return ActionList(
            coverage=surface.coverage_line(),
            complete=surface.complete,
            total_found=surface.total_found,
            returned=len(surface.actions),
            truncated=surface.truncated,
            engine_only=surface.engine_only,
            actions=[
                ActionItem(
                    ref=a.ref,
                    kind=a.kind,
                    role=a.role,
                    label=a.label,
                    target=a.target,
                    field_name=a.field_name,
                    tag=a.tag,
                    checked=a.checked,
                    disabled=a.disabled,
                    notes=a.notes,
                    http_callable=a.callable_without_engine,
                )
                for a in surface.actions
            ],
        )

    @app.post("/v1/sessions/{sid}/act", response_model=Page, tags=["session"])
    async def api_act(sid: str, body: ActBody) -> Page:
        try:
            return await session_or_404(sid).act(
                body.ref,
                text=body.text,
                dx=body.dx,
                dy=body.dy,
                max_chars=body.max_chars,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{sid}/back", response_model=Page, tags=["session"])
    async def api_back(sid: str) -> Page:
        try:
            return session_or_404(sid).back()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/v1/sessions/{sid}/scroll", response_model=Page, tags=["session"])
    async def api_scroll(sid: str, body: ScrollBody) -> Page:
        try:
            return await session_or_404(sid).scroll(y=body.y, x=body.x, max_chars=body.max_chars)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/v1/sessions/{sid}", tags=["session"])
    async def api_delete_session(sid: str):
        session_or_404(sid)
        await browser().close(sid)
        return {"ok": True}

    return app


app = create_app()
