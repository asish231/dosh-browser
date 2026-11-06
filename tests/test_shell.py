"""Hermetic command-surface tests; page behavior runs only in our QuickJS engine."""

import hashlib
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from selectolax.lexbor import LexborHTMLParser

from webresearch.cache import HttpCache
from webresearch.engine.runtime import run_scripts
from webresearch.session import Session
from webresearch.shell import DoshShell

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
  var button = document.createElement('button');
  button.textContent = 'Added ' + window.clicks;
  document.body.appendChild(button);
});
</script></body></html>"""
NEXT = '<html><head><title>Next page</title></head><body><button>Continue</button></body></html>'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from webresearch import cache

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    @asynccontextmanager
    async def create(pages=None):
        routes = {BASE + "/": HTML, BASE + "/next": NEXT}
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


def element_text(session, selector):
    node = LexborHTMLParser(session.html).css_first(selector)
    assert node is not None, selector
    return node.text()


async def action(shell, label):
    inventory = await command(shell, "actions")
    matches = [a for a in inventory["actions"] if a["label"] == label]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.mark.asyncio
async def test_help_status_open_and_action_mutation(workspace):
    async with workspace() as (shell, session, requests):
        help_result = await command(shell, "help")
        assert {"open", "actions", "act", "eval", "db", "frames", "layers"} <= help_result["commands"].keys()
        assert "not page eval" in help_result["scripting"]
        assert "unsupported" in help_result["rollback"]
        assert "earlier activity cannot be recovered" in help_result["capture"]
        status = await command(shell, "status")
        assert status["session_id"] == "test"
        assert status["persistent_workspace"] is True
        assert status["live_realm"] is False
        page = await opened(shell)
        assert page["status"] == 200
        assert page["url"] == BASE + "/"
        assert page["title"] == "Shell fixture"
        assert (await command(shell, "status"))["live_realm"] is True
        go = await action(shell, "Go")
        assert go["kind"] == "activate"
        assert (await action(shell, "Next"))["kind"] == "navigate"
        assert (await action(shell, "Disabled"))["disabled"] is True
        assert (await action(shell, "Hidden"))["hidden"] is True
        assert (await action(shell, "Go"))["ref"] == go["ref"]
        result = await command(shell, "act", ref=go["ref"])
        assert "outcome must be verified" in result["note"]
        assert element_text(session, "#out") == "clicked 1"
        assert (await action(shell, "Added 1"))["kind"] == "activate"
        assert [str(r.url) for r in requests] == [BASE + "/"]


@pytest.mark.asyncio
async def test_terminal_json_and_command_errors(workspace):
    async with workspace() as (shell, _, _):
        response = await shell.execute('open {"url":"https://shell.test/", "js":"never"}')
        assert response["ok"], response
        for line in ("unknown", "help []", "open {", "open {}"):
            response = await shell.execute(line)
            assert response["ok"] is False, line
            assert response["error"]
        assert (await shell.execute("help", []))["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"offset": -1}, {"offset": True}, {"offset": 1.5},
    {"limit": 0}, {"limit": True}, {"limit": 1001}, {"limit": "2"},
])
async def test_inventory_rejects_invalid_budgets(workspace, arguments):
    async with workspace() as (shell, _, _):
        await opened(shell, js="never")
        assert (await shell.execute("actions", arguments))["ok"] is False


@pytest.mark.asyncio
async def test_captured_markup_pagination_preserves_full_control_inventory(workspace):
    captured = (Path(__file__).resolve().parents[1] / "testhtml" / "test1.html").read_text()
    async with workspace({BASE + "/captured": captured}) as (shell, session, requests):
        # Inventory the captured document directly. The page-reading snapshot of
        # this markup is a separate, known-expensive path; it is not what this
        # pagination contract is proving.
        session.html = captured
        session.url = BASE + "/captured"
        assert session.current_tab.ctx is None
        first = await command(shell, "actions", limit=100)
        assert first["total"] > 200
        assert first["returned"] == 100
        assert first["omitted_before"] == 0
        assert first["omitted_after"] == first["total"] - 100
        assert first["next_offset"] == 100
        assert "top-level" in first["coverage"]
        items = list(first["actions"])
        page = first
        while page["next_offset"] is not None:
            offset = page["next_offset"]
            page = await command(shell, "actions", offset=offset, limit=100)
            assert page["total"] == first["total"]
            assert page["offset"] == offset
            assert page["omitted_before"] == offset
            assert page["returned"] == len(page["actions"])
            assert page["omitted_before"] + page["returned"] + page["omitted_after"] == first["total"]
            items.extend(page["actions"])
        assert len(items) == first["total"]

        assert any(item["kind"] == "navigate" for item in items)
        assert any(item["kind"] == "activate" for item in items)
        assert items == (await command(shell, "actions", limit=1000))["actions"]
        empty = await command(shell, "actions", offset=first["total"] + 10)
        assert empty["actions"] == []
        assert empty["omitted_before"] == first["total"]
        assert empty["omitted_after"] == 0
        assert empty["next_offset"] is None


@pytest.mark.asyncio
async def test_eval_emits_args_and_isolated_projection(workspace):
    async with workspace() as (shell, session, requests):
        await opened(shell)
        result = await command(shell, "eval", args={"answer": 42}, source="""
            dosh.emit('args', dosh.args.answer);
            dosh.emit('globals', [typeof window, typeof document, typeof fetch,
                typeof localStorage, typeof process, typeof require, typeof pageSecret]);
            dosh.emit('controls', dosh.actions());
            var copy = dosh.actions(); copy[0].label = 'tampered';
            dosh.emit('labels', dosh.actions().map(function(a) { return a.label; }));
            globalThis.controlSecret = 7;
        """)
        assert result["evaluated"] is True
        assert result["realm"] == "isolated-control"
        assert result["effects"] == []
        emits = {e["key"]: e["value"] for e in result["emits"]}
        assert emits["args"] == 42
        assert emits["globals"] == ["undefined"] * 7
        assert all(set(item) == {"ref", "kind", "label", "disabled", "hidden"} for item in emits["controls"])
        assert "tampered" not in emits["labels"]
        assert "HIDDEN_SECRET" not in json.dumps(result)
        assert "PASSWORD_SECRET" not in json.dumps(result)
        assert "PAGE_SECRET" not in json.dumps(result)
        assert session.current_tab.ctx.eval("typeof controlSecret") == "undefined"
        fresh = await command(shell, "eval", source="dosh.emit('fresh', typeof controlSecret);")
        assert fresh["emits"] == [{"key": "fresh", "value": "undefined"}]
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_eval_text_projects_visible_reading_content(workspace):
    async with workspace() as (shell, _, _):
        await opened(shell)
        result = await command(shell, "eval", source="dosh.emit('reading', dosh.text());")
        assert result["evaluated"] is True
        reading = result["emits"][0]["value"]
        assert "Visible reading evidence." in reading
        assert "PAGE_SECRET" not in reading
        assert "HIDDEN_SECRET" not in reading
        assert "PASSWORD_SECRET" not in reading


@pytest.mark.asyncio
async def test_script_actions_require_permission_and_dispatch_after_evaluation(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        source = "dosh.act(dosh.find('Go')[0].ref);"
        denied = await command(shell, "eval", source=source)
        assert denied["evaluated"] is False
        assert "permission" in denied["error"]
        assert denied["effects"] == []
        assert element_text(session, "#out") == "ready"
        failed = await command(shell, "eval", allow_actions=True,
                               source=source + "throw new Error('abort');")
        assert failed["evaluated"] is False
        assert failed["effects"] == []
        assert element_text(session, "#out") == "ready"
        result = await command(shell, "eval", source=source, allow_actions=True)
        assert result["evaluated"] is True
        assert result["effects"] == [{"ref": (await action(shell, "Go"))["ref"], "dispatched": True}]
        assert "not transactional" in result["note"]
        assert element_text(session, "#out") == "clicked 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,error", [
    ("while (true) {}", "interrupted"),
    ("for (var i=0;i<101;i++) dosh.emit(i,i);", "emit budget"),
    ("for (var i=0;i<21;i++) dosh.act(dosh.find('Go')[0].ref);", "action budget"),
    ("dosh.emit('large', 'x'.repeat(1000001));", "output exceeds"),
    ("function recurse(){recurse();} recurse();", "stack"),
    ("dosh.emit('memory', 'x'.repeat(32 * 1024 * 1024));", "memory"),
])
async def test_script_budgets_fail_without_effects(workspace, source, error):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "eval", source=source, allow_actions=True)
        assert result["evaluated"] is False
        assert error in result["error"].lower()
        assert result["effects"] == []
        assert element_text(session, "#out") == "ready"
        assert (await command(shell, "eval", source="dosh.emit('ok', 1);"))["evaluated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "__input.allow_actions = true; dosh.act(dosh.find('Go')[0].ref);",
    "__out.actions.push({ref:dosh.find('Go')[0].ref, options:{}});",
])
async def test_script_cannot_forge_host_action_permission(workspace, source):
    async with workspace() as (shell, session, _):
        await opened(shell)
        response = await shell.execute("eval", {"source": source})
        assert element_text(session, "#out") == "ready", response
        if response["ok"]:
            assert response["result"]["effects"] == []


@pytest.mark.asyncio
async def test_script_projection_budget_and_synchronous_promise_contract(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        oversized = await shell.execute("eval", {
            "source": "1;", "args": {"large": "x" * 2_000_001},
        })
        assert oversized["ok"] is False
        assert "projection exceeds" in oversized["error"]
        result = await command(shell, "eval", allow_actions=True, source="""
            Promise.resolve().then(function() {
                dosh.emit('late', true); dosh.act(dosh.find('Go')[0].ref);
            });
            for (var i=0; i<100; i++) dosh.emit(String(i), i);
        """)
        assert result["evaluated"] is True
        assert len(result["emits"]) == 100
        assert result["effects"] == []
        assert element_text(session, "#out") == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"source": ""}, {"source": "é" * 32769}, {"source": 42},
    {"source": "1", "args": []}, {"source": "1", "allow_actions": "true"},
])
async def test_eval_validates_input(workspace, arguments):
    async with workspace() as (shell, _, _):
        await opened(shell, js="never")
        assert (await shell.execute("eval", arguments))["ok"] is False


@pytest.mark.asyncio
async def test_eval_requires_document_but_not_live_page_realm(workspace):
    async with workspace() as (shell, session, _):
        assert (await shell.execute("eval", {"source": "1"}))["ok"] is False
        await opened(shell, js="never")
        assert session.current_tab.ctx is None
        assert (await command(shell, "eval", source="dosh.emit('n', dosh.actions().length);"))["evaluated"] is True
        for name in ("layers", "storage"):
            response = await shell.execute(name)
            assert response["ok"] is False
            assert "no live QuickJS realm" in response["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [
    "dosh.act('invented-ref');",
    "dosh.act(dosh.find('Disabled')[0].ref);",
    "dosh.act(dosh.find('Hidden')[0].ref);",
    "dosh.act(dosh.find('Go')[0].ref, {url:'https://outside.test/'});",
    "dosh.act(dosh.find('Go')[0].ref, []);",
])
async def test_all_queued_actions_validate_before_any_dispatch(workspace, queued):
    async with workspace() as (shell, session, _):
        await opened(shell)
        response = await shell.execute("eval", {
            "source": "dosh.act(dosh.find('Go')[0].ref);" + queued, "allow_actions": True,
        })
        if response["ok"]:
            result = response["result"]
            assert result["evaluated"] is False
            assert result["effects"] == []
        else:
            assert response["error"]
        assert element_text(session, "#out") == "ready", response


@pytest.mark.asyncio
async def test_queued_actions_stop_on_stale_ref_after_navigation(workspace):
    async with workspace() as (shell, session, requests):
        await opened(shell)
        result = await command(shell, "eval", allow_actions=True, source="""
            dosh.act(dosh.find('Next')[0].ref);
            dosh.act(dosh.find('Go')[0].ref);
        """)
        assert result["evaluated"] is True
        assert [e["dispatched"] for e in result["effects"]] == [True, False]
        assert result["effects"][1]["reason"] == "stale or unavailable ref"
        assert session.url == BASE + "/next"
        assert session.page.title == "Next page"
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_skills_persist_version_hash_list_query_and_delete(workspace):
    source = "dosh.emit('answer', dosh.args.answer);"
    async with workspace() as (shell, _, _):
        saved = await command(shell, "skill.save", name="reader", source=source, origins=[BASE])
        assert saved["version"] == 1
        assert saved["sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert "source" not in saved
        revised = source + "\n"
        updated = await command(shell, "skill.save", name="reader", source=revised, origins=[BASE])
        assert updated["version"] == 2
        assert updated["sha256"] == hashlib.sha256(revised.encode()).hexdigest()
        await command(shell, "skill.save", name="second", source="1;", origins=[BASE])
        listing = await command(shell, "skill.list", limit=1)
        assert listing["returned"] == 1
        assert listing["has_more"] is True
        assert "source" not in listing["rows"][0]
    async with workspace() as (shell, _, _):
        await opened(shell, js="never")
        loaded = await command(shell, "skill.run", name="reader", args={"answer": 42})
        assert loaded["version"] == 2
        assert loaded["sha256"] == updated["sha256"]
        assert loaded["emits"] == [{"key": "answer", "value": 42}]
        query = await command(shell, "db", sql="SELECT name, version FROM skills WHERE name=?", params=["reader"])
        assert query["read_only"] is True
        assert query["rows"] == [{"name": "reader", "version": 2}]
        assert "not SQL access to the website backend" in query["note"]
        assert (await command(shell, "skill.delete", name="reader"))["deleted"] is True
        assert (await command(shell, "skill.delete", name="reader"))["deleted"] is False
        assert (await shell.execute("skill.run", {"name": "reader"}))["ok"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("url,allowed", [
    (BASE + "/nested/path", True), (BASE + ":443/path", True),
    ("http://shell.test/", False), (BASE + ":8443/", False),
    ("https://sub.shell.test/", False), ("https://shell.test.evil.test/", False),
])
async def test_skill_origin_scope_is_exact(workspace, url, allowed):
    async with workspace({url: HTML}) as (shell, _, _):
        await command(shell, "skill.save", name="scoped", source="dosh.emit('ran', true);", origins=[BASE])
        await command(shell, "open", url=url, js="never")
        result = await shell.execute("skill.run", {"name": "scoped"})
        assert result["ok"] is allowed, result
        if not allowed:
            assert "origin" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("origins", [[], [BASE + "/path"], [BASE + "/"], ["https://*.test"],
                                           ["file:///tmp"], ["https://user:pass@shell.test"], [BASE + ":443"]])
async def test_skill_save_rejects_noncanonical_or_unsafe_origins(workspace, origins):
    async with workspace() as (shell, _, _):
        result = await shell.execute("skill.save", {"name": "bad", "source": "1;", "origins": origins})
        assert result["ok"] is False
        assert (await command(shell, "skill.list"))["rows"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "../escape", "x" * 65, "name'; DROP TABLE skills;--"])
async def test_skill_names_cannot_escape_workspace_registry(workspace, name):
    async with workspace() as (shell, _, _):
        response = await shell.execute("skill.save", {"name": name, "source": "1;", "origins": [BASE]})
        assert response["ok"] is False
        assert (await command(shell, "skill.list"))["rows"] == []


@pytest.mark.asyncio
async def test_skill_action_permission_requires_both_save_and_run_consent(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        source = "dosh.act(dosh.find('Go')[0].ref);"
        await command(shell, "skill.save", name="writer", source=source, origins=[BASE], allow_actions=True)
        denied = await shell.execute("skill.run", {"name": "writer"})
        assert denied["ok"] is False
        assert "explicit allow_actions=true" in denied["error"]
        assert element_text(session, "#out") == "ready"
        await command(shell, "skill.save", name="reader", source=source, origins=[BASE])
        reader = await command(shell, "skill.run", name="reader", allow_actions=True)
        assert reader["evaluated"] is False
        assert "permission" in reader["error"]
        assert element_text(session, "#out") == "ready"
        writer = await command(shell, "skill.run", name="writer", allow_actions=True)
        assert writer["effects"][0]["dispatched"] is True
        assert element_text(session, "#out") == "clicked 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "ATTACH DATABASE ':memory:' AS stolen",
    "UPDATE skills SET source='malicious'",
    "DELETE FROM skills", "DROP TABLE skills", "CREATE TABLE injected(value)",
    "PRAGMA writable_schema=ON", "SELECT load_extension('evil')",
    "SELECT 1; UPDATE skills SET source='malicious'",
])
async def test_sql_rejects_writes_attach_and_extension_without_poisoning_connection(workspace, sql):
    async with workspace() as (shell, _, _):
        await command(shell, "skill.save", name="safe", source="1;", origins=[BASE])
        rejected = await shell.execute("db", {"sql": sql})
        assert rejected["ok"] is False, rejected
        rows = await command(shell, "db", sql="SELECT name,source FROM skills")
        assert rows["rows"] == [{"name": "safe", "source": "1;"}]
        databases = shell.store.conn.execute("PRAGMA database_list").fetchall()
        assert [row["name"] for row in databases] == ["main"]
        assert (await command(shell, "skill.save", name="safe", source="2;", origins=[BASE]))["version"] == 2


@pytest.mark.asyncio
async def test_sql_row_and_execution_budgets(workspace):
    async with workspace() as (shell, _, _):
        query = await command(shell, "db", sql="""
            WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<4)
            SELECT x FROM n
        """, limit=2)
        assert query["rows"] == [{"x": 1}, {"x": 2}]
        assert query["has_more"] is True
        result = await shell.execute("db", {"sql": """
            WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n)
            SELECT sum(x) FROM n
        """})
        assert result["ok"] is False
        assert "interrupted" in result["error"]
        assert (await command(shell, "db", sql="SELECT 42 AS answer"))["rows"] == [{"answer": 42}]


@pytest.mark.asyncio
async def test_snapshot_diff_is_control_evidence_not_rollback(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        before = await command(shell, "snapshot")
        assert "no DOM text" in before["note"]
        await command(shell, "act", ref=(await action(shell, "Go"))["ref"])
        after = await command(shell, "snapshot")
        diff = await command(shell, "diff", before=before["id"], after=after["id"])
        assert [a["label"] for a in diff["added"]] == ["Added 1"]
        assert diff["removed"] == []
        assert diff["changed"] == []
        assert diff["url_changed"] is False
        assert element_text(session, "#out") == "clicked 1"
        same = await command(shell, "diff", before=after["id"], after=after["id"])
        assert same["added"] == same["removed"] == same["changed"] == []
        rows = await command(shell, "db", sql="SELECT evidence FROM snapshots ORDER BY id")
        for row in rows["rows"]:
            evidence = json.loads(row["evidence"])
            assert set(evidence) == {"title", "url", "actions"}
            assert all(set(a) == {"ref", "kind", "label"} for a in evidence["actions"])
            assert "HIDDEN_SECRET" not in row["evidence"]
            assert "PASSWORD_SECRET" not in row["evidence"]
        await command(shell, "open", url=BASE + "/next", js="never")
        next_snapshot = await command(shell, "snapshot")
        assert (await command(shell, "diff", before=after["id"], after=next_snapshot["id"]))["url_changed"] is True
        assert (await shell.execute("diff", {"before": -1, "after": after["id"]}))["ok"] is False


@pytest.mark.asyncio
async def test_runtime_observer_called_once_with_final_drains(workspace):
    async with workspace() as (shell, session, _):
        calls = []
        observer = session.client._dosh_runtime_observer

        def observe(info, url):
            assert info["ctx"] is not None
            assert info["storage"]["local"]["observer-key"] == "stored"
            assert any(e["message"] == "observer marker" for e in info["console"])
            calls.append((info, url))
            observer(info, url)

        session.client._dosh_runtime_observer = observe
        try:
            _, info = await run_scripts("""<html><body><script>
                localStorage.setItem('observer-key', 'stored');
                setTimeout(function() { console.log('observer marker'); }, 0);
            </script></body></html>""", BASE + "/observer", session.client, force=True)
            assert len(calls) == 1
            assert calls[0][0] is info
            assert calls[0][1] == BASE + "/observer"
            logs = await command(shell, "console", values=True)
            assert [e["message"] for e in logs["events"]] == ["observer marker"]
        finally:
            session.client._dosh_runtime_observer = observer


@pytest.mark.asyncio
async def test_console_runtime_observation_opt_in_filter_and_cursor(workspace):
    html = """<html><body><p>Logs</p><script>
        console.log('PRIVATE_CONSOLE'); console.warn('warning marker');
    </script></body></html>"""
    async with workspace({BASE + "/logs": html}) as (shell, _, _):
        await command(shell, "open", url=BASE + "/logs")
        default = await command(shell, "console", limit=1)
        assert len(default["events"]) == 1
        assert "PRIVATE_CONSOLE" not in json.dumps(default)
        assert default["omitted_limit"] == 1
        assert default["omitted_retention"] == 0
        assert default["events"][0]["realm"] not in {"", "unknown"}
        remaining = await command(shell, "console", after=default["next_after"], values=True)
        assert [e["message"] for e in remaining["events"]] == ["warning marker"]
        warning = await command(shell, "console", level="warn", values=True)
        assert [e["message"] for e in warning["events"]] == ["warning marker"]
        assert (await command(shell, "console", after=remaining["next_after"]))["events"] == []


@pytest.mark.asyncio
async def test_console_retention_and_script_errors_withhold_details(workspace):
    async with workspace() as (shell, _, _):
        shell.observe({"console": [{"message": str(i)} for i in range(1002)],
                       "error_messages": ["PRIVATE_ERROR"]}, BASE + "/?token=PRIVATE_URL")
        result = await command(shell, "console", after=0, limit=1000, values=True)
        assert len(result["events"]) == 1000
        assert result["omitted_retention"] == 3
        assert result["events"][-1]["level"] == "error"
        assert "PRIVATE_ERROR" not in json.dumps(result)
        assert "PRIVATE_URL" not in json.dumps(result)
        for invalid in (-1, True, "1"):
            assert (await shell.execute("console", {"after": invalid}))["ok"] is False


@pytest.mark.asyncio
async def test_network_rules_observe_mock_block_without_sending_requests(workspace):
    html = """<html><body><p id="answer">pending</p><script>
        fetch('/api').then(r=>r.json()).then(d=>document.getElementById('answer').textContent=String(d.answer));
        fetch('/blocked');
    </script></body></html>"""
    async with workspace({BASE + "/network": html}) as (shell, session, requests):
        assert (await command(shell, "net.capture", enabled=True))["capture_bodies"] is True
        mock = await command(shell, "net.rule", action="mock", pattern="/api", options={
            "status": 200, "content_type": "application/json", "body": '{"answer":42,"token":"PRIVATE_BODY"}',
        })
        block = await command(shell, "net.rule", action="block", pattern="/blocked")
        assert "runtime" in mock["scope"]
        assert "PRIVATE_BODY" not in json.dumps(mock)
        await command(shell, "open", url=BASE + "/network")
        assert element_text(session, "#answer") == "42"
        assert [str(r.url) for r in requests] == [BASE + "/network"]
        observed = await command(shell, "net")
        runtime = [r for r in observed["records"] if r["source"] == "runtime"]
        assert [r["state"] for r in runtime] == ["mocked", "blocked"]
        assert all(r["network_sent"] is False for r in runtime)
        assert all(r["tab_id"] == session.active_tab_id for r in runtime)
        assert json.loads(runtime[0]["response_body"]) == {"answer": 42, "token": "[REDACTED]"}
        assert "PRIVATE_BODY" not in json.dumps(observed)
        assert len([r for r in observed["records"] if r["source"] == "httpx"]) == 1
        assert (await command(shell, "net", after=observed["next_after"]))["records"] == []
        assert (await command(shell, "net.unrule", id=mock["id"]))["removed"] is True
        assert (await command(shell, "net.unrule", id=mock["id"]))["removed"] is False
        assert (await command(shell, "net.unrule", id=block["id"]))["removed"] is True


@pytest.mark.asyncio
async def test_network_capture_is_opt_in_and_rules_do_not_intercept_navigation(workspace):
    async with workspace({BASE + "/json?token=PRIVATE_URL": httpx.Response(
        200, json={"answer": 42, "password": "PRIVATE_BODY"},
    )}) as (shell, session, _):
        await opened(shell, js="never")
        initial = await command(shell, "net")
        assert initial["records"][0]["body_state"] == "disabled"
        assert (await shell.execute("net.capture", {"enabled": "true"}))["ok"] is False
        await command(shell, "net.capture", enabled=True)
        await session.client.get(BASE + "/json?token=PRIVATE_URL")
        records = (await command(shell, "net"))["records"]
        assert records[0]["body_state"] == "disabled"
        assert json.loads(records[1]["response_body"]) == {"answer": 42, "password": "[REDACTED]"}
        assert "PRIVATE_BODY" not in json.dumps(records)
        assert "PRIVATE_URL" not in json.dumps(records)
        await command(shell, "net.rule", action="block", pattern="/next")
        assert (await command(shell, "open", url=BASE + "/next", js="never"))["status"] == 200
        assert (await command(shell, "net", pattern="/next"))["records"][0]["source"] == "httpx"


@pytest.mark.asyncio
async def test_layers_and_loaded_frame_inventory_disclose_limits(workspace):
    parent = """<html><body><div style="position:fixed;z-index:10">Overlay</div>
        <iframe src="https://frame.shell.test/child" title="Child"></iframe>
        <script>console.log('parent realm');</script></body></html>"""
    child = """<html><body><button>Child button</button><script>
        console.log('child realm'); parent.postMessage('PRIVATE_MESSAGE', '*');
    </script></body></html>"""
    async with workspace({BASE + "/frames": parent, "https://frame.shell.test/child": child}) as (shell, _, requests):
        await command(shell, "open", url=BASE + "/frames")
        layers = await command(shell, "layers")
        assert layers["authoritative_hit_test"] is False
        assert isinstance(layers["projection"], dict)
        assert any("heuristic" in item for item in layers["limitations"])
        assert any("cap" in item for item in layers["limitations"])
        frames = await command(shell, "frames")
        assert frames["limits"] == {"max_frames": 16, "max_depth": 3}
        assert len(frames["frames"]) == 1
        frame = frames["frames"][0]
        assert frame["url"] == "https://frame.shell.test/child"
        assert frame["status"] == 200
        assert frame["live_realm"] is True
        assert frame["script_errors"] == 0
        assert isinstance(frame["messages_recorded"], int)
        assert isinstance(frame["messages_delivered"], int)
        assert "PRIVATE_MESSAGE" not in json.dumps(frames)
        assert "payloads withheld" in frames["note"]
        frame_layers = await command(shell, "layers", frame=frame["ref"])
        assert frame_layers["authoritative_hit_test"] is False
        assert len(requests) == 2


@pytest.mark.asyncio
async def test_storage_metadata_and_explicit_values_redaction(workspace):
    html = """<html><body><p>Storage</p><script>
        localStorage.setItem('theme', 'dark'); localStorage.setItem('token', 'PRIVATE_STORAGE');
        sessionStorage.setItem('draft', 'draft text');
    </script></body></html>"""
    async with workspace({BASE + "/storage": html}) as (shell, _, _):
        await command(shell, "open", url=BASE + "/storage")
        metadata = await command(shell, "storage")
        assert set(metadata["local_keys"]) == {"theme", "token"}
        assert metadata["session_keys"] == ["draft"]
        assert "PRIVATE_STORAGE" not in json.dumps(metadata)
        values = await command(shell, "storage", values=True)
        assert values["storage"]["local"] == {"theme": "dark", "token": "[REDACTED]"}
        assert "cannot detect arbitrary secrets" in values["warning"]


@pytest.mark.asyncio
async def test_shell_close_detaches_hooks_and_observer_and_is_idempotent(workspace):
    async with workspace() as (shell, session, _):
        with pytest.raises(ValueError, match="already has a shell observer"):
            DoshShell(session)
        await opened(shell, js="never")
        count = shell.network.query()["total_observed"]
        shell.close()
        shell.close()
        assert shell.closed is True
        assert getattr(session.client, "_dosh_runtime_observer", None) is None
        assert session.client.event_hooks == {"request": [], "response": []}
        with pytest.raises(sqlite3.ProgrammingError):
            shell.store.conn.execute("SELECT 1")
        assert (await shell.execute("help")) == {"ok": False, "error": "shell is closed"}
        await session.client.get(BASE + "/next")
        assert shell.network.query()["total_observed"] == count


@pytest.mark.asyncio
async def test_session_shell_is_lazy_cached_and_closed_with_session():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        session = Session(client, "test")
        assert getattr(client, "_dosh_runtime_observer", None) is None
        shell = None
        try:
            shell = session.shell
            assert isinstance(shell, DoshShell)
            assert session.shell is shell
            await session.close()
            assert shell.closed is True
            assert getattr(client, "_dosh_runtime_observer", None) is None
            assert (await shell.execute("status"))["ok"] is False
        finally:
            if shell is not None:
                shell.close()
            await session.close()


@pytest.mark.asyncio
async def test_session_close_closes_explicit_workspace_shell(workspace):
    async with workspace() as (shell, session, _):
        session._shell = shell
        await session.close()
        assert shell.closed is True
        assert getattr(session.client, "_dosh_runtime_observer", None) is None


@pytest.mark.asyncio
async def test_assist_walkthrough_synthesizes_command_without_leaking_values(workspace):
    # A page that sets a cookie via Set-Cookie; the assist walkthrough must
    # surface the cookie BY NAME in the synthesized command and never echo value.
    cookie_page = httpx.Response(
        200,
        text="<!doctype html><html><head><title>Auth</title></head><body>ok</body></html>",
        headers={
            "content-type": "text/html",
            "cache-control": "no-store",
            "set-cookie": "auth_token=supersecretvalue; Path=/",
        },
    )
    async with workspace(pages={BASE + "/auth": cookie_page}) as (shell, session, _):
        await command(shell, "open", url=BASE + "/auth", js="never")
        result = await command(shell, "assist", goal="collect tokens")

        # Full walkthrough recorded.
        assert len(result["steps"]) >= 5
        titles = [s["title"] for s in result["steps"]]
        assert any("cookies" in t for t in titles)
        assert any("storage" in t for t in titles)
        assert any("APIs" in t for t in titles)

        # Artifact references the cookie by name; the raw value never leaks.
        artifact = result["artifact"]
        assert "auth_token" in artifact
        assert "supersecretvalue" not in json.dumps(result)
        assert "curl" in artifact


@pytest.mark.asyncio
async def test_assist_empty_page_is_honest(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        result = await command(shell, "assist", goal="collect tokens")
        assert result["artifact"]
        assert "0 cookie" in result["summary"] or "cookie(s)" in result["summary"]


@pytest.mark.asyncio
async def test_state_snapshot_diff_rollback(workspace):
    """S1 (Python lane): one state DB with DOM + live cookies + storage +
    history + ledger, and snapshot/diff/rollback over it. Diff is structural
    and never echoes values; rollback rehydrates the session state."""
    async with workspace() as (shell, session, _):
        origin = "https://shell.test"
        await opened(shell)

        a = await command(shell, "state.snapshot", id="A")
        assert a["id"] == "A"
        assert a["counts"]["history_entries"] >= 1
        assert a["counts"]["cookies"] == 0

        # Mutate: a live cookie, a storage key, and a second navigation.
        session.client.cookies.set("sid", "abc123")
        session._web_storage[origin] = {"theme": "dark"}
        await command(shell, "open", url=BASE + "/next")

        b = await command(shell, "state.snapshot", id="B")
        assert b["counts"]["cookies"] == 1
        assert b["counts"]["history_entries"] == a["counts"]["history_entries"] + 1

        # Diff A->B: url changed, cookie added, storage key added, history grew.
        d = await command(shell, "state.diff", before="A", after="B")
        assert d["url"]["changed"] is True
        assert "sid" in d["cookies"]["added"]
        assert any("theme" in k for k in d["storage"]["added"])
        assert d["history"]["after"] == d["history"]["before"] + 1
        # Secret hygiene: the raw cookie value never appears in diff output.
        assert "abc123" not in json.dumps(d), "diff must not leak cookie values"

        # Roll back to A: document, live jar, storage, and history rehydrate.
        r = await command(shell, "state.rollback", id="A")
        assert r["ledger_rolled_back"] is False
        assert session.url == BASE + "/"
        assert "sid" not in session.client.cookies
        assert "theme" not in session._web_storage.get(origin, {})
        assert len(session.history) == a["counts"]["history_entries"]


@pytest.mark.asyncio
async def test_state_diff_reports_value_change_without_the_value(workspace):
    async with workspace() as (shell, session, _):
        origin = "https://shell.test"
        await opened(shell)
        session._web_storage[origin] = {"theme": "dark"}
        await command(shell, "state.snapshot", id="A")
        session._web_storage[origin] = {"theme": "light"}
        await command(shell, "state.snapshot", id="B")
        d = await command(shell, "state.diff", before="A", after="B")
        assert d["storage"]["changed"] == [f"{origin}/theme"]
        assert "dark" not in json.dumps(d) and "light" not in json.dumps(d)


@pytest.mark.asyncio
async def test_state_snapshot_unknown_id_is_honest(workspace):
    async with workspace() as (shell, session, _):
        await opened(shell)
        res = await shell.execute("state.diff", {"before": "nope", "after": "alsomissing"})
        assert res["ok"] is False
        assert "nope" in res["error"]


R1 = ('<html><head><title>r1</title></head><body>'
      '<div id="toolbar"><button id="save">Save</button>'
      '<button id="cancel">Cancel</button></div></body></html>')
R2 = ('<html><head><title>r2</title></head><body>'
      '<div class="banner">promo</div>'
      '<div id="toolbar"><button id="new">New</button>'
      '<button id="save">Save</button>'
      '<button id="cancel">Cancel</button></div></body></html>')


@pytest.mark.asyncio
async def test_ref_stable_survives_rerender_shift(workspace):
    """S2 (Python lane): content-addressed stable ids survive SPA re-renders —
    a banner and a new sibling shift document order, the stable id does not,
    and `ref` re-binds it onto the current document's ref."""
    async with workspace(pages={BASE + "/r1": R1, BASE + "/r2": R2}) as (shell, session, _):
        await command(shell, "open", url=BASE + "/r1")
        inv1 = await command(shell, "actions")
        save1 = next(a for a in inv1["actions"] if a["label"] == "Save")
        stable = save1["stable"]

        await command(shell, "open", url=BASE + "/r2")
        inv2 = await command(shell, "actions")
        save2 = next(a for a in inv2["actions"] if a["label"] == "Save")
        new1 = next(a for a in inv2["actions"] if a["label"] == "New")

        assert stable == save2["stable"], "stable identity must be URL/position-free"
        assert stable != new1["stable"]

        r = await command(shell, "ref", stable=stable)
        assert r["ref"] == save2["ref"]
        assert r["label"] == "Save"


# ---------------------------------------------------------------------------
# L10: occlusion-gated clicks (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_occlusion_gate_refuses_click_under_overlay(workspace):
    """L10: a click on a control under a higher-z overlay is refused with
    the occluder named; after the overlay is removed the same ref clicks
    through. The refusal is an error, never a silent success."""
    async with workspace() as (shell, session, requests):
        await opened(shell)
        # Overlay a div on top of the button (use frame.eval to run in page realm)
        # Get the button's actual position and create an overlay at the same coordinates
        await command(shell, "frame.eval", expr="""(function() {
            var btn = document.querySelector('button#go');
            var btnStyle = window.getComputedStyle(btn);
            var btnRect = btn.getBoundingClientRect();
            
            var overlay = document.createElement('div');
            overlay.id = 'overlay';
            overlay.style.position = 'fixed';
            overlay.style.left = btnRect.left + 'px';
            overlay.style.top = btnRect.top + 'px';
            overlay.style.width = btnRect.width + 'px';
            overlay.style.height = btnRect.height + 'px';
            overlay.style.zIndex = '100';
            overlay.style.background = 'rgba(0,0,0,0.5)';
            document.body.appendChild(overlay);
            return 'overlay added at ' + btnRect.left + ',' + btnRect.top;
        })()""")
        # Debug: check if occlusion detection works
        debug = await command(shell, "frame.eval", expr="""(function() {
            var btn = document.querySelector('button');
            var layers = typeof __inspectLayers === 'function' ? __inspectLayers() : 'no function';
            var occluded = typeof __isElementOccluded === 'function' ? __isElementOccluded(btn) : 'function not found';
            var overlay = document.getElementById('overlay');
            var overlayRect = overlay ? overlay.getBoundingClientRect() : null;
            return JSON.stringify({
                buttonId: btn ? btn.id : null,
                layers: layers,
                occluded: occluded,
                overlayExists: !!overlay,
                overlayRect: overlayRect
            });
        })()""")
        # The button should be in the action inventory but clicking it must fail
        inv = await command(shell, "actions")
        button = next((a for a in inv["actions"] if a["label"] == "Go"), None)
        assert button is not None, "button must be in inventory"
        res = await shell.execute("act", {"ref": button["ref"]})
        assert res["ok"] is False, f"click under overlay must fail: {res}"
        assert "occluded_by" in res["error"], f"error must name occluder: {res}"
        # Remove the overlay (use frame.eval to run in page realm)
        await command(shell, "frame.eval", expr="(function() { document.getElementById('overlay').remove(); return 'removed'; })()")
        # Now the click must succeed
        res2 = await shell.execute("act", {"ref": button["ref"]})
        assert res2["ok"] is True, f"click after overlay removal must succeed: {res2}"


# ---------------------------------------------------------------------------
# E2: text-selection drag lane (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_text_drag_lane(workspace):
    """E2: select_text drags across a text field, selecting the range and
    firing selectstart/select/selectionchange — distinct from element DnD."""
    # Custom HTML with a text input field
    html_with_field = """<!doctype html><html><head><title>Select test</title></head><body>
<input type="text" id="field" value="hello world">
<script>
window.__selectEvents = [];
document.getElementById('field').addEventListener('select', function(e) {
    window.__selectEvents.push({type: 'select', target: e.target.id});
});
document.getElementById('field').addEventListener('selectstart', function(e) {
    window.__selectEvents.push({type: 'selectstart', target: e.target.id});
});
document.addEventListener('selectionchange', function() {
    window.__selectEvents.push({type: 'selectionchange'});
});
</script>
</body></html>"""
    
    async with workspace(pages={BASE + "/": html_with_field}) as (shell, session, requests):
        await opened(shell)
        inv = await command(shell, "actions")
        field = next((a for a in inv["actions"] if a["ref"] == "a92811e06" or a["selector"] == "#field"), None)
        assert field is not None, "text field must be in inventory"
        # Select characters 3..7 ("lo w")
        page = await command(shell, "select", ref=field["ref"], start=3, end=7)
        # The field must now have selectionStart=3, selectionEnd=7
        result = await command(shell, "frame.eval", expr="""(function() {
            var f = document.getElementById('field');
            return JSON.stringify({
                start: f.selectionStart,
                end: f.selectionEnd
            });
        })()""")
        got = json.loads(result["value"])
        # The value is a JSON string wrapped in another JSON string, so parse again
        if isinstance(got, str):
            got = json.loads(got)
        assert got["start"] == 3
        assert got["end"] == 7
        # Verify selection events were fired
        events = await command(shell, "frame.eval", expr="(function() { return JSON.stringify(window.__selectEvents); })()")
        events_list = json.loads(events["value"])
        if isinstance(events_list, str):
            events_list = json.loads(events_list)
        event_types = [e["type"] for e in events_list]
        assert "selectstart" in event_types, f"selectstart event not fired: {event_types}"
        assert "select" in event_types, f"select event not fired: {event_types}"


# ---------------------------------------------------------------------------
# S4: same-origin frame storage sharing (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_origin_frame_storage_shared(workspace):
    """S4: a same-origin frame sees the session's seeded localStorage and
    writes back to the session (shared area, not per-frame copies).
    Cross-origin frames never see the parent's storage."""
    # Custom HTML with iframes
    html_with_frames = """<!doctype html><html><head><title>Frames test</title></head><body>
<script>localStorage.setItem('parent-key', 'parent-value');</script>
<iframe id="same" src="/same.html"></iframe>
<iframe id="cross" src="https://other.test/frame.html"></iframe>
<script>window.__ready = true;</script>
</body></html>"""
    
    async with workspace(pages={
        BASE + "/": html_with_frames,
        BASE + "/same.html": "<html><body><script>window.__frameReady = true;</script></body></html>",
        "https://other.test/frame.html": "<html><body><script>window.__frameReady = true;</script></body></html>",
    }) as (shell, session, requests):
        await opened(shell)
        # Get frame refs from inventory
        frames_result = await command(shell, "frames")
        same_ref = next(f["ref"] for f in frames_result["frames"] if "same.html" in f["url"])
        cross_ref = next(f["ref"] for f in frames_result["frames"] if "other.test" in f["url"])
        # Navigate to the same-origin frame and read localStorage
        same_result = await command(shell, "frame.eval", frame=same_ref, expr="(function() { return localStorage.getItem('parent-key'); })()")
        same_value = json.loads(same_result["value"]) if isinstance(same_result["value"], str) else same_result["value"]
        assert same_value == "parent-value", f"same-origin frame must see parent storage: {same_result}"
        # Write from the frame
        await command(shell, "frame.eval", frame=same_ref, expr="(function() { localStorage.setItem('frame-key', 'frame-value'); return 'set'; })()")
        # Verify the frame's write is visible in the frame itself
        frame_check = await command(shell, "frame.eval", frame=same_ref, expr="(function() { return localStorage.getItem('frame-key'); })()")
        frame_value = json.loads(frame_check["value"]) if isinstance(frame_check["value"], str) else frame_check["value"]
        assert frame_value == "frame-value", f"frame must see its own write: {frame_check}"
        # Cross-origin frame should NOT see the parent's storage
        cross_result = await command(shell, "frame.eval", frame=cross_ref, expr="(function() { return localStorage.getItem('parent-key'); })()")
        cross_value = json.loads(cross_result["value"]) if isinstance(cross_result["value"], str) else cross_result["value"]
        assert cross_value is None, f"cross-origin frame must not see parent storage: {cross_result}"


# ---------------------------------------------------------------------------
# S5: storage events reach same-origin frames (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_events_reach_same_origin_frames(workspace):
    """S5: when the parent writes to localStorage, same-origin frames
    receive the storage event; cross-origin frames do not (per spec)."""
    # Custom HTML with iframes
    html_with_frames = """<!doctype html><html><head><title>Frames test</title></head><body>
<script>
localStorage.setItem('from-parent', 'hello');
</script>
<iframe id="same" src="/same.html"></iframe>
<iframe id="cross" src="https://other.test/frame.html"></iframe>
<script>window.__ready = true;</script>
</body></html>"""
    
    same_frame_html = """<html><body><script>
window.__storageEvents = [];
window.addEventListener('storage', function(e) {
    window.__storageEvents.push({key: e.key, newValue: e.newValue, url: e.url});
});
window.__frameReady = true;
</script></body></html>"""
    
    async with workspace(pages={
        BASE + "/": html_with_frames,
        BASE + "/same.html": same_frame_html,
        "https://other.test/frame.html": same_frame_html,
    }) as (shell, session, requests):
        await opened(shell)
        # Get frame refs from inventory
        frames_result = await command(shell, "frames")
        same_ref = next(f["ref"] for f in frames_result["frames"] if "same.html" in f["url"])
        cross_ref = next(f["ref"] for f in frames_result["frames"] if "other.test" in f["url"])
        # Parent already wrote to localStorage during page load
        # Check same-origin frame received the event
        same_events = await command(shell, "frame.eval", frame=same_ref, expr="(function() { return JSON.stringify(window.__storageEvents); })()")
        same_events_list = json.loads(same_events["value"])
        if isinstance(same_events_list, str):
            same_events_list = json.loads(same_events_list)
        assert len(same_events_list) == 1, f"same-origin frame must receive event: {same_events_list}"
        assert same_events_list[0]["key"] == "from-parent"
        assert same_events_list[0]["newValue"] == "hello"
        # Check cross-origin frame did NOT receive the event
        cross_events = await command(shell, "frame.eval", frame=cross_ref, expr="(function() { return JSON.stringify(window.__storageEvents); })()")
        cross_events_list = json.loads(cross_events["value"])
        if isinstance(cross_events_list, str):
            cross_events_list = json.loads(cross_events_list)
        assert len(cross_events_list) == 0, f"cross-origin frame must not receive event: {cross_events_list}"


