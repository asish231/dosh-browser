"""Regression tests for the shell upgrade audit items (2026-11-03).

Implemented after verifying the audit's claims against live code:
- #1 ref collision: CONFIRMED REAL (two identical buttons shared one ref;
  first-match-wins made the second unreachable) -> occurrence suffixes.
- #2 warrant TTL: the real gap (skills never expired). NOTE: the audit's
  R-02 evidence ("run on bank.com succeeds") was FALSE — the origin gate
  refuses cross-origin runs (live-proven 2026-11-03); only TTL was missing.
- #4 caps export: manifest to disk as machine-readable JSON.
- #5 effect labels: read/mutate/privileged privilege classes on actions.

Deferred with reasons: #3 receipts
(a real subsystem — new table, HMAC key management, chain verification),
#6 privileged gating (the bridge it gates on does not exist; the spec
would block every login in single-user DOSH).
"""

import json
from contextlib import asynccontextmanager

import httpx
import pytest

from webresearch.cache import HttpCache
from webresearch.session import Session
from webresearch.shell import COMMANDS, DoshShell

BASE = "https://dup.test"
URL_B = "https://churn.test/"
# Two identical digit boxes + two identical buttons whose inline handlers
# DIFFER: same content identity (no id/name/value/aria-label), different
# behavior. Before the fix both buttons shared one ref and the second was
# unreachable.
HTML = """<!doctype html><html><head><title>Dupes</title></head><body><form>
<script>window.__ready = true;</script>
<div id="out">initial</div>
<input type="text" maxlength="1">
<input type="text" maxlength="1">
<button type="button" onclick="document.getElementById('out').textContent='first-blast'">Blast</button>
<button type="button" onclick="document.getElementById('out').textContent='second-blast'">Blast</button>
<a href="/away">Away</a>
<input type="password" aria-label="Password">
</form></body></html>"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    @asynccontextmanager
    async def create(pages=None):
        routes = {BASE + "/": HTML, URL_B: HTML}
        routes.update(pages or {})
        routes = {str(httpx.URL(u)): t for u, t in routes.items()}

        def handler(request):
            url = str(request.url)
            assert url in routes, f"unexpected HTTP request: {url}"
            return httpx.Response(200, text=routes[url], headers={
                "content-type": "text/html", "cache-control": "no-store"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     follow_redirects=True) as client:
            session = Session(client, "test")
            shell = DoshShell(session, database=tmp_path / "workspace.sqlite")
            try:
                yield shell, session
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


async def _acts(shell):
    inv = await command(shell, "actions", limit=1000)
    return inv["actions"]


# ------------------------------------------------------- #1 ref collision


@pytest.mark.asyncio
async def test_identical_siblings_get_unique_refs(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        acts = await _acts(shell)
        refs = [a["ref"] for a in acts]
        assert len(refs) == len(set(refs)), f"duplicate refs remain: {refs}"
        # first occurrence keeps the bare ref; the sibling carries ~2
        buttons = [a for a in acts if a["label"] == "Blast"]
        assert len(buttons) == 2
        bare = [a for a in buttons if "~" not in a["ref"]]
        suffixed = [a for a in buttons if "~" in a["ref"]]
        assert len(bare) == 1 and len(suffixed) == 1
        assert suffixed[0]["ref"].startswith(bare[0]["ref"] + "~2")
        # stables are unique too, with the same suffix pattern
        stables = [a["stable"] for a in acts]
        assert len(stables) == len(set(stables))


@pytest.mark.asyncio
async def test_act_on_suffixed_ref_hits_the_second_control(workspace):
    """The bug: first-match-wins made identical sibling #2 unreachable.

    Asserts on the DOM (#out text), not JS variables: the engine rebuilds
    the page model after a pass, which re-runs scripts and resets JS state
    by design — DOM mutations are what survive, and what an agent sees.
    """
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        acts = await _acts(shell)
        second = next(a for a in acts if a["label"] == "Blast" and "~2" in a["ref"])
        await command(shell, "act", ref=second["ref"])
        probe = await command(shell, "frame.eval",
                              expr="document.getElementById('out').textContent")
        assert json.loads(probe["value"]) == "second-blast"
        # and the first still hits its own handler
        first = next(a for a in acts if a["label"] == "Blast" and "~" not in a["ref"])
        await command(shell, "act", ref=first["ref"])
        probe = await command(shell, "frame.eval",
                              expr="document.getElementById('out').textContent")
        assert json.loads(probe["value"]) == "first-blast"


@pytest.mark.asyncio
async def test_suffixes_survive_url_churn(workspace):
    """Occurrence suffixes are document-order, URL-free: same document under
    a different URL keeps pairwise-identical stable ids (suffix included)."""
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        first = {(a["label"], a["kind"]): a["stable"] for a in await _acts(shell)}
        await command(shell, "open", url=URL_B)
        second = {(a["label"], a["kind"]): a["stable"] for a in await _acts(shell)}
        assert first == second


# ------------------------------------------------------- #5 effect labels


@pytest.mark.asyncio
async def test_effect_classes_are_labeled(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        acts = await _acts(shell)
        by_label = {a["label"]: a for a in acts}
        assert by_label["Away"]["effect"] == "read"          # navigation
        assert by_label["Blast"]["effect"] == "mutate"       # button
        assert by_label["Password"]["effect"] == "privileged"  # password field
        for a in acts:
            assert a["effect"] in {"read", "mutate", "privileged"}


# ------------------------------------------------------- #2 warrant TTL


@pytest.mark.asyncio
async def test_expired_skill_refuses_to_run(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        await command(shell, "skill.save", name="ephemeral",
                      source="dosh.emit('k', 'v')", origins=[BASE],
                      expires_in=0.05)
        import asyncio as _aio

        await _aio.sleep(0.1)
        err = await fail(shell, "skill.run", name="ephemeral")
        assert "warrant expired" in err


@pytest.mark.asyncio
async def test_fresh_skill_runs_and_expiry_is_reported(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        saved = await command(shell, "skill.save", name="fresh",
                              source="dosh.emit('k', 'v')", origins=[BASE],
                              expires_in=3600)
        assert saved["warrant_expires"] > 0
        run = await command(shell, "skill.run", name="fresh")
        assert run["evaluated"] is True


@pytest.mark.asyncio
async def test_skill_update_extends_and_clears(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        await command(shell, "skill.save", name="revive",
                      source="dosh.emit('k', 'v')", origins=[BASE],
                      expires_in=0.05)
        import asyncio as _aio

        await _aio.sleep(0.1)
        err = await fail(shell, "skill.run", name="revive")
        assert "warrant expired" in err
        # extend: runs again
        updated = await command(shell, "skill.update", name="revive", expires_in=3600)
        assert updated["warrant_expires"] > 0
        run = await command(shell, "skill.run", name="revive")
        assert run["evaluated"] is True
        # clear: permanent again (pre-TTL behavior)
        cleared = await command(shell, "skill.update", name="revive", expires_in=0)
        assert cleared["warrant_expires"] is None
        run = await command(shell, "skill.run", name="revive")
        assert run["evaluated"] is True


@pytest.mark.asyncio
async def test_warrant_ttl_validation(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        err = await fail(shell, "skill.save", name="neg", source="dosh.emit('k','v')",
                         origins=[BASE], expires_in=-5)
        assert "nonnegative" in err
        err = await fail(shell, "skill.update", name="neg", expires_in=-1)
        assert "nonnegative" in err  # args validate before the skill lookup
        err = await fail(shell, "skill.update", name="nonexistent", expires_in=100)
        assert "unknown skill" in err
        await command(shell, "skill.save", name="perm", source="dosh.emit('k','v')",
                      origins=[BASE])
        saved = await command(shell, "skill.list")
        row = next(r for r in saved["rows"] if r["name"] == "perm")
        assert row["warrant_expires"] is None  # permanent by default (unchanged)


def test_skill_update_registered():
    assert "skill.update" in COMMANDS


# ------------------------------------------------------- #4 caps export


@pytest.mark.asyncio
async def test_caps_export_writes_machine_readable_manifest(workspace, tmp_path):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        out = tmp_path / "caps.json"
        result = await command(shell, "caps", export=str(out))
        assert result["exported_to"] == str(out)
        assert result["exported_bytes"] > 0
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert manifest["url"] == BASE + "/"
        assert manifest["counts"]["input_types"].get("password") == 1
        assert manifest["engine_gaps"]
        # the in-band return is the same manifest
        assert result["counts"] == manifest["counts"]


@pytest.mark.asyncio
async def test_caps_export_requires_absolute_path(workspace):
    async with workspace() as (shell, session):
        await command(shell, "open", url=BASE + "/")
        err = await fail(shell, "caps", export="relative/caps.json")
        assert "absolute" in err
