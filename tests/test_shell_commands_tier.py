"""Regression tests for the tier-2 shell commands: read, history, cookies,
wait, caps, screenshot, frame.eval.

Hermetic: pages served from a MockTransport; no network, no yt-dlp, no
external binaries. The screenshot leak assertions pin the secret-hygiene
rule: hidden/password input values must never be painted.
"""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session
from webresearch.shell import COMMANDS, DoshShell

BASE = "https://shell.test"
HTML = """<!doctype html><html><head><title>Shell fixture</title></head><body>
<nav><a href="/next">Next</a></nav>
<main><p id="reading">Visible reading evidence.</p><p id="out">ready</p>
<input type="hidden" name="csrf_token" value="HIDDEN_SECRET">
<input type="password" aria-label="Password" value="PASSWORD_SECRET">
<button id="go">Go</button><button id="disabled" disabled>Disabled</button>
<button id="hidden" style="display:none">Hidden</button></main>
<script>
window.pageSecret = 'PAGE_SECRET';
window.clicks = 0;
document.getElementById('go').addEventListener('click', function () {
  window.clicks++;
  document.getElementById('out').textContent = 'clicked ' + window.clicks;
});
</script></body></html>"""
NEXT = '<html><head><title>Next page</title></head><body><button>Continue</button></body></html>'
CAPS_HTML = """<html><head><title>Caps fixture</title></head><body>
<canvas id="chart"></canvas>
<video controls></video><audio></audio>
<div contenteditable="true">edit me</div>
<script type="application/wasm"></script>
</body></html>"""
COOKIE_PAGE = httpx.Response(
    200,
    text='<html><body>cookie set</body></html>',
    headers={
        "content-type": "text/html",
        "cache-control": "no-store",
        "set-cookie": "sid=COOKIE_SECRET_VALUE; Path=/",
    },
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    @asynccontextmanager
    async def create(pages=None):
        routes = {BASE + "/": HTML, BASE + "/next": NEXT, BASE + "/caps": CAPS_HTML,
                  BASE + "/cookies": COOKIE_PAGE}
        routes.update(pages or {})
        routes = {str(httpx.URL(url)): response for url, response in routes.items()}
        requests = []

        def handler(request):
            requests.append(request)
            url = str(request.url)
            assert url in routes, f"unexpected HTTP request: {request.method} {url}"
            response = routes[url]
            if isinstance(response, httpx.Response):
                return response
            return httpx.Response(200, text=response, headers={
                "content-type": "text/html", "cache-control": "no-store",
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            session = Session(client, "test")
            shell = DoshShell(session, database=tmp_path / "workspace.sqlite")
            try:
                yield shell, session, requests
            finally:
                shell.close()
                await session.close()

    return create


async def command(shell, operation, **arguments):
    response = await shell.execute(operation, arguments)
    assert response["ok"], response
    return response["result"]


async def opened(shell, **arguments):
    return await command(shell, "open", url=BASE + "/", **arguments)


async def fail(shell, operation, **arguments):
    """Assertions on the honest error envelope."""
    response = await shell.execute(operation, arguments)
    assert not response["ok"], response
    return response["error"]


# ---------------------------------------------------------------- help/registry


def test_all_new_commands_registered():
    for name in ("read", "history", "cookies", "wait", "caps", "screenshot", "frame.eval", "video"):
        assert name in COMMANDS


# ---------------------------------------------------------------- read


@pytest.mark.asyncio
async def test_read_returns_blocks_without_secrets(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "read")
        assert result["source"] == "acd-blocks"
        assert result["title"] == "Shell fixture"
        joined = json.dumps(result)
        assert "Visible reading evidence" in joined
        assert "ready" in joined
        # secrets never enter the reading projection
        assert "HIDDEN_SECRET" not in joined
        assert "PASSWORD_SECRET" not in joined
        assert "PAGE_SECRET" not in joined


@pytest.mark.asyncio
async def test_read_paginates_with_honest_coverage(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        full = await command(shell, "read")
        if full["total"] < 2:
            pytest.skip("fixture produced fewer than 2 blocks")
        page1 = await command(shell, "read", offset=0, limit=1)
        assert page1["returned"] == 1
        assert page1["omitted_after"] == full["total"] - 1
        assert page1["next_offset"] == 1
        page2 = await command(shell, "read", offset=1, limit=1)
        assert page2["returned"] == 1
        assert page2["omitted_before"] == 1
        assert page2["blocks"][0]["text"] != page1["blocks"][0]["text"]


@pytest.mark.asyncio
async def test_read_truncates_long_blocks_with_flag(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "read", max_chars=10)
        for block in result["blocks"]:
            if len(block["text"]) > 10:
                assert block["text"] == block["text"][:10]
                assert block.get("truncated") is True


@pytest.mark.asyncio
async def test_read_falls_back_to_view_when_acd_has_no_blocks(workspace):
    async with workspace() as (shell, session, _):
        # /next is control-only: the ACD compiles zero reading blocks for it
        await command(shell, "open", url=BASE + "/next")
        result = await command(shell, "read")
        assert result["source"] == "page-view"
        assert result["total"] >= 1
        assert result["blocks"][0]["text"].startswith("TITLE: Next page")


@pytest.mark.asyncio
async def test_read_errors_are_honest(workspace):
    async with workspace() as (shell, session, _):
        err = await fail(shell, "read")
        assert "no loaded document" in err
        await opened(shell)
        err = await fail(shell, "read", offset=-1)
        assert "offset" in err
        err = await fail(shell, "read", max_chars=5)
        assert "max_chars" in err
        err = await fail(shell, "read", limit=0)
        assert "limit" in err


# ---------------------------------------------------------------- history


@pytest.mark.asyncio
async def test_history_lists_and_traverses(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        await command(shell, "open", url=BASE + "/next")
        listed = await command(shell, "history")
        assert listed["total"] == 2
        assert listed["current_index"] == 1
        assert listed["entries"][0]["url"].endswith("/")
        assert listed["entries"][1]["title"] == "Next page"
        assert listed["entries"][1]["current"] is True
        back = await command(shell, "history", move=-1)
        assert back["index"] == 0
        assert back["url"].endswith("/")
        assert back["title"] == "Shell fixture"
        fwd = await command(shell, "history", goto=1)
        assert fwd["index"] == 1
        assert fwd["title"] == "Next page"


@pytest.mark.asyncio
async def test_history_rejects_out_of_range(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        err = await fail(shell, "history", goto=99)
        assert "no page at history index 99" in err
        err = await fail(shell, "history", move=-5)
        assert "history index" in err


@pytest.mark.asyncio
async def test_history_move_requires_integer(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        err = await fail(shell, "history", move=True)
        assert "move must be an integer" in err


# ---------------------------------------------------------------- cookies


@pytest.mark.asyncio
async def test_cookies_reports_metadata_never_values(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/cookies")
        result = await command(shell, "cookies")
        assert result["total"] >= 1
        row = next(c for c in result["cookies"] if c["name"] == "sid")
        assert row["domain"]
        # rows carry metadata ONLY — no value field ever
        assert set(row.keys()) == {"name", "domain", "path", "secure", "expires"}
        dumped = json.dumps(result)
        assert "COOKIE_SECRET_VALUE" not in dumped


@pytest.mark.asyncio
async def test_cookies_empty_is_honest(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "cookies")
        assert result["cookies"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------- wait


@pytest.mark.asyncio
async def test_wait_text_and_url_conditions_met(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        r1 = await command(shell, "wait", text="Visible reading evidence", timeout=2, interval=0.05)
        assert r1["met"] is True
        assert r1["elapsed_ms"] >= 0
        assert r1["checks"]["text"] is True
        r2 = await command(shell, "wait", url="shell.test", timeout=2, interval=0.05)
        assert r2["met"] is True
        assert r2["checks"]["url"] is True


@pytest.mark.asyncio
async def test_wait_ref_condition_uses_real_inventory(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        inventory = await command(shell, "actions", limit=10)
        ref = inventory["actions"][0]["ref"]
        result = await command(shell, "wait", ref=ref, timeout=2, interval=0.05)
        assert result["met"] is True
        assert result["checks"]["ref"] is True


@pytest.mark.asyncio
async def test_wait_timeout_reports_latest_state(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "wait", text="WILL_NEVER_APPEAR",
                               timeout=0.3, interval=0.05)
        assert result["met"] is False
        assert result["elapsed_ms"] >= 250
        assert result["checks"]["text"] is False
        assert "timeout" in result["note"]


@pytest.mark.asyncio
async def test_wait_net_idle(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "wait", net_idle_ms=100, timeout=3, interval=0.1)
        assert result["met"] is True
        assert result["checks"]["net_idle"] is True


@pytest.mark.asyncio
async def test_wait_validation_is_honest(workspace):
    async with workspace() as (shell, session, _):
        err = await fail(shell, "wait")
        assert "wait needs at least one of" in err
        await opened(shell)
        err = await fail(shell, "wait", text="x", timeout=0.01)
        assert "timeout must be" in err
        err = await fail(shell, "wait", text="x", interval=10)
        assert "interval must be" in err
        err = await fail(shell, "wait", text="x", net_idle_ms=-1)
        assert "net_idle_ms must be" in err


@pytest.mark.asyncio
async def test_wait_rejects_unknown_kwargs(workspace):
    """A wrong param name (e.g. net_idle vs net_idle_ms) must be rejected, not
    silently dropped onto the default deadline."""
    async with workspace() as (shell, session, _):
        await opened(shell)
        err = await fail(shell, "wait", text="x", timeout_ms=3000, net_idle=False)
        assert "unknown argument" in err
        assert "net_idle" in err


@pytest.mark.asyncio
async def test_wait_timeout_ms_is_honored_not_default(workspace):
    """timeout_ms (milliseconds) must bound the wait. Previously it was silently
    ignored and the call fell back to the 15s default."""
    import time as _t
    async with workspace() as (shell, session, _):
        await opened(shell)
        t0 = _t.monotonic()
        result = await command(shell, "wait", text="WILL_NEVER_APPEAR",
                               timeout_ms=200, interval=0.05)
        dt = _t.monotonic() - t0
        assert result["met"] is False
        assert result["elapsed_ms"] < 1000  # bounded, nowhere near the 15s default
        assert dt < 1.5
        assert "timeout" in result["note"]


@pytest.mark.asyncio
async def test_wait_timeout_ms_and_timeout_mutually_exclusive(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        err = await fail(shell, "wait", text="x", timeout=1, timeout_ms=500)
        assert "not both" in err


@pytest.mark.asyncio
async def test_wait_timeout_ms_validation(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        err = await fail(shell, "wait", text="x", timeout_ms=10)
        assert "timeout_ms must be" in err


@pytest.mark.asyncio
async def test_wait_ref_check_does_not_rebuild_inventory_per_tick(workspace, monkeypatch):
    """Waiting for a ref must not rebuild the full inventory on every poll tick
    when no network activity changes (the old per-tick 1M-entry rebuild)."""
    async with workspace() as (shell, session, _):
        await opened(shell)
        inventory = await command(shell, "actions", limit=10)
        ref = inventory["actions"][0]["ref"]
        calls = {"n": 0}
        real = shell._inventory

        def counting(offset, limit):
            calls["n"] += 1
            return real(offset, limit)

        monkeypatch.setattr(shell, "_inventory", counting)
        result = await command(shell, "wait", ref=ref, timeout_ms=500, interval=0.05)
        assert result["met"] is True
        # ref present at open + no network change -> inventory built at most once
        assert calls["n"] <= 1, calls


# ---------------------------------------------------------------- caps


@pytest.mark.asyncio
async def test_caps_live_counts_and_engine_gaps(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "caps")
        assert result["source"] == "live-realm"
        assert result["counts"]["input_types"].get("hidden") == 1
        assert result["counts"]["input_types"].get("password") == 1
        assert result["counts"]["elements"] > 0
        assert result["dialogs_pending"] == 0
        assert len(result["engine_gaps"]) >= 6
        for gap in result["engine_gaps"]:
            assert gap["capability"] and gap["reason"]


@pytest.mark.asyncio
async def test_caps_reports_unavailable_capabilities(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/caps")
        result = await command(shell, "caps")
        caps = {item["capability"] for item in result["page_unavailable"]}
        assert "canvas pixels" in caps
        assert "media playback/raster" in caps
        assert "caret/selection in editors" in caps
        assert "wasm execution" in caps
        assert result["counts"]["canvas"] == 1
        assert result["counts"]["video"] == 1
        assert result["counts"]["editable"] == 1
        assert result["counts"]["wasm_script"] == 1


@pytest.mark.asyncio
async def test_caps_static_fallback_when_no_realm(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell, js="never")
        result = await command(shell, "caps")
        assert result["source"] == "static-tree"
        # the static tree sees the same fixture inputs
        assert result["counts"]["input_types"].get("hidden") == 1


# ---------------------------------------------------------------- screenshot


@pytest.mark.asyncio
async def test_screenshot_writes_svg_and_never_secrets(workspace, tmp_path):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "screenshot", dest_dir=str(tmp_path))
        path = Path(result["path"])
        assert path.exists()
        svg = path.read_text(encoding="utf-8")
        assert svg.startswith("<svg")
        assert result["bytes"] == len(svg.encode("utf-8"))
        # secret hygiene: hidden and password values never painted
        assert "HIDDEN_SECRET" not in svg
        assert "PASSWORD_SECRET" not in svg
        assert "••••••••••" in svg  # password painted as dots, like a browser
        assert "approximate" in result["note"]


@pytest.mark.asyncio
async def test_screenshot_requires_live_realm(workspace, tmp_path):
    async with workspace() as (shell, session, _):
        await opened(shell, js="never")
        err = await fail(shell, "screenshot", dest_dir=str(tmp_path))
        assert "no live QuickJS realm" in err


# ---------------------------------------------------------------- frame.eval


@pytest.mark.asyncio
async def test_frame_eval_observes_live_realm(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        r = await command(shell, "frame.eval", expr="window.clicks")
        assert r["evaluated"] is True and r["value"] == "0"
        r = await command(shell, "frame.eval", expr="document.querySelector('title').textContent")
        assert r["evaluated"] is True
        assert json.loads(r["value"]) == "Shell fixture"


@pytest.mark.asyncio
async def test_frame_eval_mutates_page_realm(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        r = await command(shell, "frame.eval",
                          expr="document.getElementById('out').textContent = 'PROBED'")
        assert r["evaluated"] is True
        assert json.loads(r["value"]) == "PROBED"
        r2 = await command(shell, "frame.eval", expr="document.getElementById('out').textContent")
        assert json.loads(r2["value"]) == "PROBED"


@pytest.mark.asyncio
async def test_frame_eval_reports_errors_without_killing_ctx(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        bad = await command(shell, "frame.eval", expr="syntax((")
        assert bad["evaluated"] is False
        assert "error" in bad
        # an infinite probe is interrupted and the realm SURVIVES + unlimited again
        loop = await command(shell, "frame.eval", expr="while(true){}")
        assert loop["evaluated"] is False
        alive = await command(shell, "frame.eval", expr="1+1")
        assert alive["evaluated"] is True and alive["value"] == "2"
        # and a normal action pass still works afterwards
        await command(shell, "act", ref=await _ref_of(shell, "Go"))


async def _ref_of(shell, label):
    inventory = await command(shell, "actions", limit=100)
    matches = [a for a in inventory["actions"] if a["label"] == label]
    assert len(matches) == 1, matches
    return matches[0]["ref"]


@pytest.mark.asyncio
async def test_frame_eval_validation(workspace):
    async with workspace() as (shell, session, _):
        err = await fail(shell, "frame.eval")
        assert "expr must contain" in err
        await opened(shell)
        err = await fail(shell, "frame.eval", expr="x" * 65537)
        assert "expr must contain 1-65536 bytes" in err
        # no-realm variant: fresh document loaded without scripts
        await command(shell, "open", url=BASE + "/next", js="never")
        err = await fail(shell, "frame.eval", expr="1")
        assert "no live QuickJS realm" in err
