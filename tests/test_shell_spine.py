"""Regression tests for the agent-efficiency spine: ref (stable identity),
trace (causal bundle), watch (mutation stream).

Hermetic: MockTransport pages; the engine runs our minidom via QuickJS. The
fixtures exercise the exact patterns the spine exists for — identity that
survives URL churn, one response per action, event-driven observation.
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session
from webresearch.shell import COMMANDS, DoshShell

BASE = "https://shell.test"
HTML = """<!doctype html><html><head><title>Spine fixture</title></head><body>
<nav><a href="/next">Next</a></nav>
<main><p id="out">ready</p>
<input type="hidden" name="csrf_token" value="HIDDEN_SECRET">
<button id="go">Go</button><button id="disabled" disabled>Disabled</button></main>
<script>
window.clicks = 0;
document.getElementById('go').addEventListener('click', function () {
  window.clicks++;
  document.getElementById('out').textContent = 'clicked ' + window.clicks;
  var button = document.createElement('button');
  button.textContent = 'Added ' + window.clicks;
  document.body.appendChild(button);
});
</script></body></html>"""
# Same page served under a different URL path: identity must survive.
URL_CHURN = BASE + "/churned"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    @asynccontextmanager
    async def create():
        routes = {BASE + "/": HTML, URL_CHURN: HTML}
        routes = {str(httpx.URL(url)): text for url, text in routes.items()}
        requests = []

        def handler(request):
            requests.append(request)
            url = str(request.url)
            assert url in routes, f"unexpected HTTP request: {request.method} {url}"
            return httpx.Response(200, text=routes[url], headers={
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


async def fail(shell, operation, **arguments):
    response = await shell.execute(operation, arguments)
    assert not response["ok"], response
    return response["error"]


async def inventory_map(shell):
    inv = await command(shell, "actions", limit=1000)
    return {a["label"]: a for a in inv["actions"]}


def test_spine_commands_registered():
    for name in ("ref", "trace", "watch"):
        assert name in COMMANDS


# ---------------------------------------------------------------- ref (identity)


@pytest.mark.asyncio
async def test_every_action_carries_a_stable_id(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        acts = (await command(shell, "actions", limit=1000))["actions"]
        assert acts
        for a in acts:
            assert a["stable"].startswith("s") and len(a["stable"]) == 9


@pytest.mark.asyncio
async def test_ref_resolves_stable_to_current_ref(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        acts = (await command(shell, "actions", limit=1000))["actions"]
        go = next(a for a in acts if a["label"] == "Go")
        result = await command(shell, "ref", stable=go["stable"])
        assert result["ref"] == go["ref"]
        assert result["label"] == "Go"
        assert result["disabled"] is False


@pytest.mark.asyncio
async def test_identity_survives_url_churn(workspace):
    """The reason the spine exists: refs churn with the URL, stable ids do not."""
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        first = await inventory_map(shell)
        await command(shell, "open", url=URL_CHURN)
        second = await inventory_map(shell)
        # Same controls, different document URL...
        assert first["Go"]["ref"] != second["Go"]["ref"]
        # ...but the SAME identity.
        assert first["Go"]["stable"] == second["Go"]["stable"]
        # and `ref` re-resolves the stable id onto the new document.
        resolved = await command(shell, "ref", stable=first["Go"]["stable"])
        assert resolved["ref"] == second["Go"]["ref"]


@pytest.mark.asyncio
async def test_ref_misses_and_ambiguities_are_honest(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        err = await fail(shell, "ref", stable="snope0000")
        assert "no action with stable id" in err
        err = await fail(shell, "ref")
        assert "ref needs a stable id" in err


# ---------------------------------------------------------------- trace


@pytest.mark.asyncio
async def test_trace_returns_one_causal_bundle(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        go = (await inventory_map(shell))["Go"]
        result = await command(shell, "trace", ref=go["ref"])
        # the click ran the page script: the Added-1 button is in the delta
        added_labels = [x.get("label") for x in result["dom_delta"]["added"]]
        assert "Added 1" in added_labels
        assert result["dom_delta"]["stable_before"] > 0
        assert result["dom_delta"]["stable_after"] == result["dom_delta"]["stable_before"] + 1
        assert result["page"]["title"] == "Spine fixture"
        assert "causal footprint" in result["note"]


@pytest.mark.asyncio
async def test_trace_includes_network_and_console_sections(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        go = (await inventory_map(shell))["Go"]
        result = await command(shell, "trace", ref=go["ref"])
        assert "network" in result and "console" in result and "dialogs" in result
        assert result["network"]["latest_id"] >= 0
        assert result["console"]["omitted"] >= 0


@pytest.mark.asyncio
async def test_trace_delta_is_identity_keyed_not_ref_keyed(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        go = (await inventory_map(shell))["Go"]
        result = await command(shell, "trace", ref=go["ref"])
        for entry in result["dom_delta"]["added"]:
            assert "stable" in entry
        # existing controls are NOT reported as removed/added when their refs
        # churn (identity-keyed diff, not ref-keyed)
        removed_labels = [x.get("label") for x in result["dom_delta"]["removed"]]
        assert "Go" not in removed_labels


@pytest.mark.asyncio
async def test_trace_validation_and_act_errors(workspace):
    async with workspace() as (shell, session, _):
        err = await fail(shell, "trace")
        assert "trace needs a ref" in err
        await command(shell, "open", url=BASE + "/")
        err = await fail(shell, "trace", ref="anope000")
        assert "no action" in err


# ---------------------------------------------------------------- watch


@pytest.mark.asyncio
async def test_watch_enable_act_drain_disable_cycle(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        enabled = await command(shell, "watch", enable=True)
        assert enabled["enabled"] is True
        assert enabled["cap"] == 500
        go = (await inventory_map(shell))["Go"]
        await command(shell, "act", ref=go["ref"])
        drained = await command(shell, "watch")
        assert drained["enabled"] is True
        assert drained["returned"] >= 2  # text change on #out + appended button
        seqs = [r["seq"] for r in drained["records"]]
        assert seqs == sorted(seqs)
        assert drained["latest_seq"] >= max(seqs)
        types = {r["type"] for r in drained["records"]}
        assert "childList" in types
        disabled = await command(shell, "watch", enable=False)
        assert disabled["enabled"] is False
        assert disabled["retained"] >= drained["returned"]


@pytest.mark.asyncio
async def test_watch_records_carry_compact_descriptors_only(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        await command(shell, "watch", enable=True)
        go = (await inventory_map(shell))["Go"]
        await command(shell, "act", ref=go["ref"])
        drained = await command(shell, "watch")
        for rec in drained["records"]:
            dumped = json.dumps(rec)
            # compact descriptors, never live node references or page secrets
            assert "HIDDEN_SECRET" not in dumped
            assert isinstance(rec.get("target"), str)
            for node_desc in rec.get("added", []) + rec.get("removed", []):
                assert isinstance(node_desc, str)


@pytest.mark.asyncio
async def test_watch_cursor_pagination(workspace):
    async with workspace() as (shell, session, _):
        await command(shell, "open", url=BASE + "/")
        await command(shell, "watch", enable=True)
        go = (await inventory_map(shell))["Go"]
        await command(shell, "act", ref=go["ref"])
        first = await command(shell, "watch", limit=2)
        assert first["returned"] <= 2
        if first["returned"] == 2:
            second = await command(shell, "watch", after=first["next_after"])
            assert all(r["seq"] > first["next_after"] - 1 for r in second["records"])
            assert not (set(r["seq"] for r in second["records"]) &
                        set(r["seq"] for r in first["records"]))


@pytest.mark.asyncio
async def test_watch_requires_live_realm_and_valid_args(workspace):
    async with workspace() as (shell, session, _):
        err = await fail(shell, "watch", enable=True)
        assert "no live QuickJS realm" in err
        await command(shell, "open", url=BASE + "/")
        err = await fail(shell, "watch", enable="yes")
        assert "enable must be boolean" in err
        err = await fail(shell, "watch", after=-1)
        assert "after must be" in err
