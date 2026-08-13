import json
import unittest
from unittest.mock import AsyncMock, patch

from webresearch.agent import (
    LlmBillingError,
    LlmRateLimitError,
    ask,
    chat_llm,
    message_text,
    serialize_tool_result,
)
from webresearch.settings import available_models, resolve_model
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
        ids = {row["id"] for row in available_models()}
        self.assertEqual(ids, {"glm-4.7-flash", "mercury-2"})

    def test_glm_flash_is_first_in_auto_order(self):
        self.assertEqual(available_models()[0]["id"], "glm-4.7-flash")

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
