"""Config contract tests: the official/primary lane status is one source
of truth (webresearch/config.py) and surfaces honestly everywhere.

Pins the policy the project runs on:
- RUST (dosh-browser-core/) is the primary product.
- PYTHON (webresearch/) is the optional companion SDK.
- QuickJS is BANNED on this build (2026-11-07): the doshgentv8 V8 lane is
  the only JS engine; an explicit WR_JS_ENGINE=quickjs is refused loudly.
"""

import pytest

from webresearch.config import (
    JS_ENGINE_DEFAULT,
    JS_ENGINES,
    LANES,
    OFFICIAL_LANE,
    js_engine,
    js_engine_status,
    lane_status,
    project_status,
)


def test_rust_is_the_primary_lane():
    assert OFFICIAL_LANE == "rust"
    assert LANES["rust"]["status"] == "primary"
    assert LANES["rust"]["path"] == "dosh-browser-core/"


def test_python_is_the_optional_sdk_lane():
    assert LANES["python"]["status"] == "optional-sdk"
    assert LANES["python"]["path"] == "webresearch/"
    summary = LANES["python"]["summary"]
    assert "companion Python package" in summary or "SDK" in summary


def test_rust_summary_names_primary_capabilities():
    summary = LANES["rust"]["summary"]
    assert "primary product" in summary.lower()
    assert "standalone browser binary" in summary


def test_unknown_lane_is_honest():
    got = lane_status("wasm")
    assert got["status"] == "unknown"
    assert "unknown lane" in got["error"]
    assert "python" in got["error"] and "rust" in got["error"]


def test_quickjs_is_banned_and_doshgentv8_is_official():
    assert JS_ENGINE_DEFAULT == "doshgentv8"
    assert JS_ENGINES["quickjs"]["status"] == "banned"
    assert JS_ENGINES["doshgentv8"]["status"] == "official"


def test_explicit_quickjs_selection_is_refused(monkeypatch):
    monkeypatch.setenv("WR_JS_ENGINE", "quickjs")
    with pytest.raises(ValueError, match="QuickJS is banned"):
        js_engine()


def test_js_engine_defaults_to_doshgentv8(monkeypatch):
    monkeypatch.delenv("WR_JS_ENGINE", raising=False)
    assert js_engine() == "doshgentv8"


def test_js_engine_selects_v8_when_configured(monkeypatch):
    monkeypatch.setenv("WR_JS_ENGINE", "doshgentv8")
    assert js_engine() == "doshgentv8"
    status = js_engine_status()
    assert status["active"] == "doshgentv8"
    assert "only engine" in status["note"]
    assert "banned" in status["note"]


def test_unknown_engine_falls_back_to_doshgentv8(monkeypatch):
    monkeypatch.setenv("WR_JS_ENGINE", "turbojs")
    assert js_engine() == "doshgentv8"
    status = js_engine_status()
    assert status["requested"] == "turbojs"
    assert status["active"] == "doshgentv8"
    assert "banned" in status["note"]


def test_project_status_answers_the_official_question():
    ps = project_status()
    assert ps["official_lane"] == "rust"
    assert ps["lanes"]["rust"]["status"] == "primary"
    assert ps["lanes"]["python"]["status"] == "optional-sdk"
    assert "Rust" in ps["policy"]
    assert "primary" in ps["policy"]


@pytest.mark.asyncio
async def test_shell_product_command_reports_official_lane():

    import httpx

    from webresearch.cache import HttpCache
    from webresearch.session import Session
    from webresearch.shell import DoshShell

    async def run():
        import webresearch.cache as cache

        cache.CACHE = HttpCache()
        routes = {"https://cfg.test/": "<html><body><p>cfg</p></body></html>"}

        def handler(request):
            return httpx.Response(200, text=routes[str(request.url)],
                                  headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     follow_redirects=True) as client:
            session = Session(client, "t")
            shell = DoshShell(session)
            try:
                r = await shell.execute("product", {})
                assert r["ok"], r
                result = r["result"]
                assert result["official_lane"] == "rust"
                assert result["lanes"]["rust"]["status"] == "primary"
                assert result["lanes"]["python"]["status"] == "optional-sdk"
                # status carries it too
                await shell.execute("open", {"url": "https://cfg.test/"})
                r2 = await shell.execute("status", {})
                assert r2["result"]["product"]["official_lane"] == "rust"
                # Post-2026-11-07 QuickJS ban: doshgentv8 is the ONLY JS lane.
                assert r2["result"]["product"]["active_js_engine"] == "doshgentv8"
            finally:
                shell.close()
                await session.close()


    await run()
