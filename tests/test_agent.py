import asyncio
import json
import os
import unittest
from unittest import mock
from unittest.mock import AsyncMock, patch

from webresearch.agent import (
    LlmBillingError,
    LlmRateLimitError,
    ask,
    chat_llm,
    message_text,
    serialize_tool_result,
)
from webresearch.settings import (
    available_models,
    fallback_chain,
    llm_config,
    resolve_model,
)
from webresearch.types import SearchHit, SearchResult


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


def _final(text: str) -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


FAKE_SEARCH = SearchResult(
    query="example.com",
    engine="ddg",
    hits=[SearchHit(n=1, title="Example Domain", url="https://example.com")],
    view="[1] Example Domain → https://example.com",
)


class SettingsTests(unittest.TestCase):
    def test_model_aliases_resolve(self):
        self.assertEqual(resolve_model("glm"), "glm-4.7-flash")
        self.assertEqual(resolve_model("flash"), "glm-4.7-flash")
        self.assertEqual(resolve_model("mercury"), "mercury-2")
        self.assertEqual(resolve_model("auto"), "auto")

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_model("gpt-9")

    def test_paid_glm_tiers_are_not_offered(self):
        """Only the free GLM tier is registered; paid tiers return 1113."""
        ids = {row["id"] for row in available_models()}
        self.assertNotIn("glm-4.7", ids)
        self.assertNotIn("glm-4.7-flashx", ids)
        self.assertIn("glm-4.7-flash", ids)

    def test_glm_flash_is_first_in_auto_order(self):
        self.assertEqual(available_models()[0]["id"], "glm-4.7-flash")

    def test_compat_family_requires_an_explicit_base_url(self):
        """A key alone does not identify the provider, so refuse to guess."""
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ, {"OPENAI_COMPAT_BASE_URL": ""}, clear=False
        ):
            with self.assertRaises(ValueError) as ctx:
                llm_config("pool-auto")
        self.assertIn("OPENAI_COMPAT_BASE_URL", str(ctx.exception))

    def test_compat_family_resolves_once_base_url_is_set(self):
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ, {"OPENAI_COMPAT_BASE_URL": "https://example.test/v1"}, clear=False
        ):
            cfg = llm_config("pool-auto")
        self.assertEqual(cfg["base_url"], "https://example.test/v1")
        self.assertEqual(cfg["provider"], "openai_compat")

    def test_registry_id_and_wire_model_name_differ_for_a_pool(self):
        """`--model pool-auto` must send "auto", not the registry id."""
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ, {"OPENAI_COMPAT_BASE_URL": "https://example.test/v1"}, clear=False
        ):
            os.environ.pop("OPENAI_COMPAT_MODEL", None)
            self.assertEqual(llm_config("pool-auto")["model"], "auto")
            self.assertEqual(llm_config("pool-auto-code")["model"], "auto-code")

    def test_compat_family_is_skipped_in_auto_when_unconfigured(self):
        """An unconfigured family must not break the auto chain."""
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ, {"OPENAI_COMPAT_BASE_URL": "", "OPENAI_COMPAT_API_KEY": ""}, clear=False
        ):
            chain = fallback_chain("auto")
            models = available_models()
        self.assertTrue(all(c["provider"] != "openai_compat" for c in chain))
        compat_rows = [m for m in models if m["provider"] == "openai_compat"]
        self.assertTrue(all(not m["configured"] for m in compat_rows))

    def test_openai_compat_model_env_pins_only_the_default_entry(self):
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ,
            {
                "OPENAI_COMPAT_BASE_URL": "https://example.test/v1",
                "OPENAI_COMPAT_MODEL": "deepseek-v4-pro",
            },
            clear=False,
        ):
            self.assertEqual(llm_config("pool-auto")["model"], "deepseek-v4-pro")
            self.assertEqual(llm_config("pool-auto-code")["model"], "auto-code")

    def test_pool_endpoint_can_be_reached(self):
        """The configured pool must answer a real tool-calling request.

        Without this the registry can look perfectly configured while the
        endpoint is dead, which is how a typo in a base URL goes unnoticed.
        """
        chain = fallback_chain("auto")
        if not any(c["provider"] == "openai_compat" for c in chain):
            self.skipTest("no OpenAI-compatible endpoint configured")
        cfg = next(c for c in chain if c["provider"] == "openai_compat")

        async def run():
            return await chat_llm(
                [{"role": "user", "content": "Reply with exactly: PONG"}],
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                provider=cfg["provider"],
                tools=None,
                retries=2,
                max_tokens=32,
            )

        body = asyncio.run(run())
        message = (body.get("choices") or [{}])[0].get("message") or {}
        text = (message.get("content") or "").strip()
        self.assertTrue(text, f"empty completion from {cfg['model']}: {body}")

    def test_pool_endpoint_supports_tool_calling(self):
        """browser_* tools are useless if the model cannot call them."""
        chain = fallback_chain("auto")
        if not any(c["provider"] == "openai_compat" for c in chain):
            self.skipTest("no OpenAI-compatible endpoint configured")
        cfg = next(c for c in chain if c["provider"] == "openai_compat")
        # Prompt must make the tool choice unambiguous. "Search for a walking
        # tour" reads as answerable in prose, and a pool that routes per request
        # will happily answer it without calling anything.
        tools = [{
            "type": "function",
            "function": {
                "name": "browser_search",
                "description": "Search the web.",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            },
        }]

        async def run():
            return await chat_llm(
                [{"role": "user", "content": "Search for a walking tour. "
                  "You must call the browser_search tool to do this; do not "
                  "answer from memory."}],
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                model=cfg["model"],
                provider=cfg["provider"],
                tools=tools,
                retries=3,
                max_tokens=256,
            )

        # An `auto` route picks a different model per request, so a single call
        # is not representative: the same prompt can be answered in prose by one
        # model and acted on by the next. Poll until a tool call appears, which
        # measures the route's capability rather than one draw from it.
        last = None
        for _ in range(4):
            last = asyncio.run(run())
            message = (last.get("choices") or [{}])[0].get("message") or {}
            if message.get("tool_calls"):
                break
        message = (last.get("choices") or [{}])[0].get("message") or {}
        calls = message.get("tool_calls") or []
        self.assertTrue(
            calls,
            f"{cfg['model']} never called a tool in 4 attempts: {last}",
        )
        self.assertEqual(calls[0]["function"]["name"], "browser_search")
        self.assertIn("q", json.loads(calls[0]["function"]["arguments"]))

    def test_auto_chain_has_no_duplicate_endpoints(self):
        """Two entries resolving to one deployment would retry the same call."""
        with mock.patch("webresearch.settings.load_dotenv"), mock.patch.dict(
            os.environ,
            {
                "OPENAI_COMPAT_BASE_URL": "https://example.test/v1",
                "OPENAI_COMPAT_API_KEY": "k",
                "OPENAI_COMPAT_MODEL": "auto-code",
            },
            clear=False,
        ):
            chain = fallback_chain("auto")
        keys = [(c["base_url"], c["model"]) for c in chain]
        self.assertEqual(len(keys), len(set(keys)), keys)

    def test_dotenv_does_not_clobber_an_explicit_override(self):
        """setdefault semantics: an exported variable wins over the file."""
        with mock.patch.dict(os.environ, {"OPENAI_COMPAT_MODEL": "glm-5.3"}, clear=False):
            self.assertEqual(llm_config("pool-auto")["model"], "glm-5.3")

    def test_registry_lists_both_vendors(self):
        vendors = {row["vendor"] for row in available_models()}
        self.assertIn("z.ai", vendors)
        self.assertIn("Inception", vendors)


class AgentHelperTests(unittest.TestCase):
    def test_message_text_joins_parts(self):
        self.assertEqual(message_text("hi"), "hi")
        self.assertEqual(message_text([{"type": "text", "text": "a"}, {"text": "b"}]), "ab")

    def test_serialize_prefers_view(self):
        payload = json.loads(serialize_tool_result(FAKE_SEARCH))
        self.assertIn("view", payload)
        self.assertEqual(payload["engine"], "ddg")


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_calls_browser_search_then_answers(self):
        with (
            patch(
                "webresearch.agent.chat_llm",
                new=AsyncMock(
                    side_effect=[
                        _tool_call("browser_search", {"q": "example.com"}),
                        _final("example.com is the IANA example domain."),
                    ]
                ),
            ),
            patch("webresearch.agent.invoke", new=AsyncMock(return_value=FAKE_SEARCH)) as tool,
        ):
            result = await ask(
                "What is example.com?",
                model="glm",
                api_key="test-key",
                max_rounds=3,
            )

        self.assertIn("IANA", result.answer)
        self.assertEqual(result.model, "glm-4.7-flash")
        self.assertEqual(result.provider, "glm")
        self.assertEqual(result.tool_calls[0]["name"], "browser_search")
        self.assertTrue(result.tool_calls[0]["ok"])
        self.assertEqual(result.usage.total_tokens, 15)
        tool.assert_awaited()

    async def test_rate_limited_model_fails_over_to_next(self):
        chain = [
            {
                "provider": "glm",
                "vendor": "z.ai",
                "label": "flash",
                "model": "glm-4.7-flash",
                "api_key": "k1",
                "api_key_env": "ZAI_API_KEY",
                "base_url": "https://api.z.ai/api/paas/v4",
            },
            {
                "provider": "mercury",
                "vendor": "Inception",
                "label": "mercury",
                "model": "mercury-2",
                "api_key": "k2",
                "api_key_env": "INCEPTION_API_KEY",
                "base_url": "https://api.inceptionlabs.ai/v1",
            },
        ]
        events: list[tuple[str, dict]] = []

        with (
            patch("webresearch.agent.fallback_chain", return_value=chain),
            patch(
                "webresearch.agent.chat_llm",
                new=AsyncMock(
                    side_effect=[
                        LlmRateLimitError("1305", "overloaded", model="glm-4.7-flash"),
                        _final("Answered by the backup model."),
                    ]
                ),
            ),
        ):
            result = await ask("hello", model="auto", max_rounds=3, on_event=lambda k, p: events.append((k, p)))

        self.assertEqual(result.model, "mercury-2")
        self.assertEqual(result.rounds, 1)
        self.assertEqual(result.models_used, ["glm-4.7-flash", "mercury-2"])
        self.assertIn(("fallback", {"from": "glm-4.7-flash", "to": "mercury-2"}), events)

    async def test_tool_error_is_reported_not_raised(self):
        with (
            patch(
                "webresearch.agent.chat_llm",
                new=AsyncMock(
                    side_effect=[
                        _tool_call("browser_read", {"url": "https://blocked.test"}),
                        _final("That page could not be fetched."),
                    ]
                ),
            ),
            patch("webresearch.agent.invoke", new=AsyncMock(side_effect=RuntimeError("403"))),
        ):
            result = await ask("read it", model="mercury", api_key="k", max_rounds=3)

        self.assertFalse(result.tool_calls[0]["ok"])
        self.assertIn("could not be fetched", result.answer)

    async def test_billing_code_1113_is_not_retried(self):
        import httpx

        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                429,
                json={"error": {"code": "1113", "message": "Insufficient balance"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(LlmBillingError):
                await chat_llm(
                    [{"role": "user", "content": "hi"}],
                    api_key="k",
                    base_url="https://api.z.ai/api/paas/v4",
                    model="glm-4.7",
                    provider="glm",
                    tools=None,
                    client=client,
                    retries=3,
                )
        self.assertEqual(calls, 1)

    async def test_missing_key_raises_value_error(self):
        with patch("webresearch.agent.fallback_chain", return_value=[]):
            with self.assertRaises(ValueError):
                await ask("hi", model="auto")


if __name__ == "__main__":
    unittest.main()
