"""Drive webresearch tools with an OpenAI-compatible LLM (Mercury 2 or GLM-4.7)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field

from webresearch.dispatch import invoke
from webresearch.fetch import client_kwargs
from webresearch.session import Browser
from webresearch.settings import fallback_chain, llm_config
from webresearch.tools import TOOLS

SYSTEM = (
    "You are a web research agent driving a real browser through tools.\n"
    "Rules:\n"
    "- Use browser_search for questions about the live web. Never guess a URL.\n"
    "- Open a result with browser_read, or use a session (browser_session_create, "
    "browser_goto, browser_open) when you need cookies or multiple steps.\n"
    "- Each page comes back as compact text with [n] links and [eN] inspect refs. "
    "Use those refs with browser_open, browser_click and browser_type.\n"
    "- If a page reports a CAPTCHA, call browser_captcha to inspect it and report "
    "what blocked you. Never attempt to solve or bypass a challenge.\n"
    "- Ground every claim in tool output. If the tools cannot reach the page, say so.\n"
    "- Stop calling tools once you can answer. Be concise and specific."
)

MAX_TOOL_CHARS = 12_000
DEFAULT_ROUNDS = 8
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.3
RATE_LIMIT_CODES = {"1302", "1305"}
BILLING_CODES = {"1113"}


class LlmError(RuntimeError):
    """User-facing LLM provider failure."""


class LlmBillingError(LlmError):
    """The account cannot use this model. Retrying will not help."""

    def __init__(self, model: str = "", detail: str = "") -> None:
        self.model = model
        self.detail = detail
        super().__init__(
            f"{model or 'This model'} needs a paid balance on the provider account "
            "(z.ai code 1113). Use --model glm for the free GLM-4.7-Flash tier."
        )


class LlmRateLimitError(LlmError):
    def __init__(self, code: str = "", detail: str = "", model: str = "") -> None:
        self.code = code
        self.detail = detail
        self.model = model
        switch = "Use --model auto to fail over automatically, or --model mercury."
        if code == "1305":
            hint = f"{model or 'z.ai'} is overloaded (1305). Wait about a minute. {switch}"
        elif code == "1302":
            hint = (
                f"{model or 'z.ai'} hit its request quota (1302). The free GLM-4.7-Flash tier "
                f"allows roughly one request at a time, and every tool round is a new request. {switch}"
            )
        else:
            hint = f"{model or 'The provider'} rate-limited this request. {switch}"
        super().__init__(hint)


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, raw: dict[str, Any] | None) -> None:
        if not isinstance(raw, dict):
            return
        self.prompt_tokens += int(raw.get("prompt_tokens") or 0)
        self.completion_tokens += int(raw.get("completion_tokens") or 0)
        self.total_tokens += int(raw.get("total_tokens") or 0)


class AgentResult(BaseModel):
    answer: str
    model: str
    provider: str = ""
    rounds: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    models_used: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    truncated: bool = False


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def serialize_tool_result(value: Any, *, limit: int = MAX_TOOL_CHARS) -> str:
    """Trim a tool result to the fields a model actually needs."""
    data = _jsonable(value)
    if isinstance(data, dict) and data.get("view"):
        compact = {
            key: data[key]
            for key in (
                "url",
                "status",
                "title",
                "engine",
                "query",
                "present",
                "kind",
                "view",
            )
            if key in data
        }
        if "hits" in data:
            compact["hits"] = data["hits"][:8]
        if data.get("captcha"):
            captcha = data["captcha"]
            compact["captcha"] = {
                "present": captcha.get("present"),
                "kind": captcha.get("kind"),
                "note": captcha.get("note"),
            }
        data = compact
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[: limit - 32] + '... [truncated by webresearch]"}'
    return text


def message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return str(content)


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _strip_vendor_fields(messages: list[dict[str, Any]]) -> None:
    """Vendor-specific keys do not transfer when we fail over to another model."""
    for message in messages:
        message.pop("reasoning_content", None)


async def chat_llm(
    messages: list[dict[str, Any]],
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider: str = "glm",
    tools: list[dict] | None = TOOLS,
    client: httpx.AsyncClient | None = None,
    retries: int = 3,
    thinking: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """One OpenAI-compatible chat completion, with bounded retry on 429."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=120.0)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider == "glm":
        # Round-level reasoning: off keeps tool loops fast and cheap.
        payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
    elif provider == "mercury":
        payload["reasoning_effort"] = "medium" if thinking else "low"
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    last_code = ""
    last_detail = f"{model} request failed"
    try:
        for attempt in range(max(1, retries)):
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept-Language": "en-US,en",
                    },
                    json=payload,
                )
            except httpx.TimeoutException as exc:
                last_detail = f"{model} timed out: {exc}"
                await asyncio.sleep(3 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                raise LlmError(f"{model} connection failed: {exc}") from exc

            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text[:500]}
            err = body.get("error") if isinstance(body, dict) else None
            code = str(err.get("code") or "") if isinstance(err, dict) else ""

            if code in BILLING_CODES:
                raise LlmBillingError(model, str(err or body))
            if response.status_code == 429 or code in RATE_LIMIT_CODES:
                last_code = code or "429"
                last_detail = str(err or body)
                if attempt + 1 < max(1, retries):
                    await asyncio.sleep(5 * (2**attempt))
                continue
            if response.status_code == 401:
                raise LlmError(f"{model} rejected the API key (401). Check .env.")
            if response.status_code >= 400:
                raise LlmError(f"{model} HTTP {response.status_code}: {err or body}")
            return body
        raise LlmRateLimitError(last_code, last_detail, model=model)
    finally:
        if own:
            await client.aclose()


async def chat_zai(*args: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("provider", "glm")
    return await chat_llm(*args, **kwargs)


async def ask(
    prompt: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_rounds: int = DEFAULT_ROUNDS,
    thinking: bool = False,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentResult:
    """Run an LLM against the browser tools until it answers or runs out of rounds.

    With model='auto' the configured models are tried in order, so a rate-limited
    provider fails over instead of killing the run.
    """
    choice = model or provider
    if api_key or base_url:
        cfg = llm_config(choice)
        chain = [
            {
                **cfg,
                "api_key": api_key or cfg["api_key"],
                "base_url": (base_url or cfg["base_url"]).rstrip("/"),
            }
        ]
    else:
        chain = fallback_chain(choice)

    if not chain:
        missing = llm_config(choice) if choice and choice != "auto" else None
        env = missing["api_key_env"] if missing else "ZAI_API_KEY or INCEPTION_API_KEY"
        raise ValueError(f"No API key configured. Set {env} in .env.")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt},
    ]
    used: list[dict[str, Any]] = []
    models_used: list[str] = []
    usage = Usage()
    browser = Browser()
    index = 0
    rounds = 0

    async with httpx.AsyncClient(**client_kwargs()) as web:
        async with httpx.AsyncClient(timeout=120.0) as llm:
            try:
                while rounds < max_rounds:
                    cfg = chain[index]
                    if cfg["model"] not in models_used:
                        models_used.append(cfg["model"])
                    try:
                        raw = await chat_llm(
                            messages,
                            api_key=cfg["api_key"],
                            base_url=cfg["base_url"],
                            model=cfg["model"],
                            provider=cfg["provider"],
                            client=llm,
                            thinking=thinking,
                        )
                    except (LlmRateLimitError, LlmBillingError):
                        if index + 1 < len(chain):
                            index += 1
                            _strip_vendor_fields(messages)
                            if on_event:
                                on_event(
                                    "fallback",
                                    {"from": cfg["model"], "to": chain[index]["model"]},
                                )
                            continue
                        raise

                    rounds += 1
                    usage.add(raw.get("usage"))
                    first = (raw.get("choices") or [{}])[0]
                    message = first.get("message") or {}
                    tool_calls = message.get("tool_calls") or []
                    text = message_text(message.get("content"))

                    if on_event:
                        on_event(
                            "model",
                            {
                                "round": rounds,
                                "model": cfg["model"],
                                "text": text,
                                "tool_calls": len(tool_calls),
                            },
                        )

                    if not tool_calls:
                        return AgentResult(
                            answer=text.strip() or "(the model returned an empty reply)",
                            model=cfg["model"],
                            provider=cfg["provider"],
                            rounds=rounds,
                            tool_calls=used,
                            models_used=models_used,
                            usage=usage,
                        )

                    assistant: dict[str, Any] = {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": tool_calls,
                    }
                    if message.get("reasoning_content"):
                        assistant["reasoning_content"] = message["reasoning_content"]
                    messages.append(assistant)

                    for call in tool_calls:
                        name = (call.get("function") or {}).get("name") or ""
                        args = _tool_arguments(call)
                        if on_event:
                            on_event("tool", {"name": name, "arguments": args})
                        try:
                            result = await invoke(web, browser, name, args)
                            payload = serialize_tool_result(result)
                            ok = True
                        except Exception as exc:
                            payload = json.dumps(
                                {"error": f"{type(exc).__name__}: {exc}"[:400]}
                            )
                            ok = False
                        used.append({"name": name, "arguments": args, "ok": ok})
                        if on_event:
                            on_event("tool_result", {"name": name, "ok": ok})
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id") or name,
                                "content": payload,
                            }
                        )

                last = chain[index]
                return AgentResult(
                    answer=(
                        f"Stopped after {max_rounds} tool rounds without a final answer. "
                        "Re-run with --max-rounds higher or a narrower question."
                    ),
                    model=last["model"],
                    provider=last["provider"],
                    rounds=rounds,
                    tool_calls=used,
                    models_used=models_used,
                    usage=usage,
                    truncated=True,
                )
            finally:
                await browser.close_all()
