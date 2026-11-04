"""Config contract tests: the official/primary lane status is one source
of truth (webresearch/config.py) and surfaces honestly everywhere.

Pins the policy the project runs on:
- RUST (dosh-browser-core/) is the primary product.
- PYTHON (webresearch/) is the optional companion SDK.
- QuickJS is the default JS engine for Python companion flows; the V8 lane is
  proven against the QuickJS oracle, selectable via WR_JS_ENGINE.
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


def test_quickjs_is_the_official_engine():
    assert JS_ENGINE_DEFAULT == "quickjs"
    assert JS_ENGINES["quickjs"]["status"] == "official"


def test_v8_lane_is_experimental_until_phase_2():
    v8 = JS_ENGINES["doshgentv8"]
    assert v8["status"] == "experimental"
    assert "Phase 2" in v8["summary"]


def test_js_engine_defaults_to_quickjs(monkeypatch):
    monkeypatch.delenv("WR_JS_ENGINE", raising=False)
    assert js_engine() == "quickjs"


def test_js_engine_selects_v8_when_configured(monkeypatch):
    monkeypatch.setenv("WR_JS_ENGINE", "doshgentv8")
    assert js_engine() == "doshgentv8"
    status = js_engine_status()
    assert status["active"] == "doshgentv8"
    assert "experimental" in status["note"]
    assert "not yet validated" in status["note"]


def test_unknown_engine_falls_back_to_official(monkeypatch):
    monkeypatch.setenv("WR_JS_ENGINE", "turbojs")
    assert js_engine() == "quickjs"
    status = js_engine_status()
    assert status["requested"] == "turbojs"
    assert status["active"] == "quickjs"


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
                assert r2["result"]["product"]["active_js_engine"] == "quickjs"
            finally:
                shell.close()
                await session.close()


    await run()
