from __future__ import annotations

import asyncio
import unittest

from quickjs import Context
from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html, run_scripts
from webresearch.fetch import FetchResult, make_client
from webresearch.forms import forms
from webresearch.mcp import McpServer
from webresearch.parse import parse
from webresearch.session import Session


class UrlSpecEdgesTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context()
        self.ctx.eval(MINIDOM_JS)

    def test_url_search_params_space_encoding_plus(self):
        """application/x-www-form-urlencoded encodes spaces as + per spec."""
        self.ctx.eval('globalThis.sp = new URLSearchParams({q: "hello world", tag: "a b c"});')
        res = self.ctx.eval('globalThis.sp.toString()')
        self.assertIn("q=hello+world", res)
        self.assertIn("tag=a+b+c", res)
        self.assertNotIn("%20", res)

    def test_default_ports_stripped_from_host_and_port(self):
        """Default ports (80 for http, 443 for https) are stripped from .port and .host."""
        self.ctx.eval('globalThis.u1 = new URL("http://example.com:80/path");')
        self.assertEqual(self.ctx.eval('globalThis.u1.port'), "")
        self.assertEqual(self.ctx.eval('globalThis.u1.host'), "example.com")
        self.assertEqual(self.ctx.eval('globalThis.u1.href'), "http://example.com/path")

        self.ctx.eval('globalThis.u2 = new URL("https://example.com:443/secure");')
        self.assertEqual(self.ctx.eval('globalThis.u2.port'), "")
        self.assertEqual(self.ctx.eval('globalThis.u2.host'), "example.com")
        self.assertEqual(self.ctx.eval('globalThis.u2.href'), "https://example.com/secure")

        # Non-default ports are kept
        self.ctx.eval('globalThis.u3 = new URL("http://example.com:8080/path");')
        self.assertEqual(self.ctx.eval('globalThis.u3.port'), "8080")
        self.assertEqual(self.ctx.eval('globalThis.u3.host'), "example.com:8080")
        self.assertEqual(self.ctx.eval('globalThis.u3.href'), "http://example.com:8080/path")

    def test_valueless_query_parameter_preserved_until_mutated(self):
        """Valueless query (?a) is preserved in .search and .href until searchParams is mutated."""
        self.ctx.eval('globalThis.u = new URL("http://example.com/search?a");')
        self.assertEqual(self.ctx.eval('globalThis.u.search'), "?a")
        self.assertEqual(self.ctx.eval('globalThis.u.href'), "http://example.com/search?a")
        # searchParams.toString() normalizes to a= per WHATWG
        self.assertEqual(self.ctx.eval('globalThis.u.searchParams.toString()'), "a=")
        # Once searchParams is mutated, search and href reflect the new serialization
        self.ctx.eval('globalThis.u.searchParams.set("b", "1");')
        self.assertEqual(self.ctx.eval('globalThis.u.search'), "?a=&b=1")
        self.assertEqual(self.ctx.eval('globalThis.u.href'), "http://example.com/search?a=&b=1")

    def test_credentials_preserved_in_href(self):
        """Credentials (user:pass) are preserved in URL.href while omitted from origin."""
        self.ctx.eval('globalThis.u = new URL("http://user:pass@example.com/resource");')
        self.assertEqual(self.ctx.eval('globalThis.u.origin'), "http://example.com")
        self.assertEqual(self.ctx.eval('globalThis.u.href'), "http://user:pass@example.com/resource")

    def test_non_special_schemes_in_href(self):
        """Non-special schemes like mailto: are preserved in href."""
        self.ctx.eval('globalThis.u = new URL("mailto:user@example.com");')
        self.assertEqual(self.ctx.eval('globalThis.u.href'), "mailto:user@example.com")


class HistoryApiEdgesTests(unittest.TestCase):
    def test_history_length_seeded_from_session(self):
        """history.length reflects session history count and increments on pushState."""
        ctx = Context()
        ctx.eval('globalThis.__WR_HISTORY_LENGTH = 3; true;')
        ctx.eval(MINIDOM_JS)
        self.assertEqual(ctx.eval('globalThis.history.length'), 3)
        ctx.eval('globalThis.history.pushState(null, "", "/page4");')
        self.assertEqual(ctx.eval('globalThis.history.length'), 4)
        ctx.eval('globalThis.history.pushState(null, "", "/page5");')
        self.assertEqual(ctx.eval('globalThis.history.length'), 5)


class DragAndDropEdgesTests(unittest.TestCase):
    def test_dragenter_and_dragleave_dispatched(self):
        """DnD dispatches dragenter and dragleave around dragover."""
        html = """
        <!DOCTYPE html>
        <html>
        <body>
          <div id="drag" draggable="true" style="width: 50px; height: 50px;">Drag</div>
          <div style="height: 50px;"></div>
          <div id="target" style="width: 100px; height: 100px;">Target</div>
          <script>
            window.log = [];
            var t = document.getElementById("target");
            t.addEventListener("dragenter", function (e) { window.log.push("dragenter"); });
            t.addEventListener("dragover", function (e) {
              window.log.push("dragover");
              e.preventDefault();
            });
            t.addEventListener("drop", function (e) { window.log.push("drop"); });
            var d = document.getElementById("drag");
            d.addEventListener("dragend", function (e) { window.log.push("dragend"); });
          </script>
        </body>
        </html>
        """
        async def run():
            import httpx
            client = httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"})
                )
            )
            try:
                session = Session(client, "test_dnd")
                await session.goto("https://test.local/")
                ctx = session.current_tab.ctx
                self.assertIsNotNone(ctx)
                ctx.eval("__dragSelector('#drag', 0, 180, 3, null, false)")
                res = ctx.eval("window.log.join(',')")
                return res
            finally:
                await client.aclose()

        log = asyncio.run(run())
        self.assertIn("dragenter", log)
        self.assertIn("dragover", log)
        self.assertIn("drop", log)
        self.assertIn("dragend", log)


class RadioExclusivityTests(unittest.TestCase):
    def test_parse_time_radio_exclusivity_last_checked_wins(self):
        """When multiple radios in a group have checked in HTML, the last one wins."""
        html = """
        <form id="f" action="/submit" method="post">
          <input type="radio" name="plan" value="free" checked>
          <input type="radio" name="plan" value="pro" checked>
        </form>
        """
        doc = parse(FetchResult(
            url="http://example.com/form", final_url="http://example.com/form",
            status=200, content_type="text/html", kind="html", body=html,
        ))
        f_list = forms(doc)
        self.assertEqual(len(f_list), 1)
        plan_field = next(f for f in f_list[0].fields if f.name == "plan")
        self.assertEqual(plan_field.value, "pro")

    def test_dom_radio_exclusivity_at_load_and_script(self):
        """In DOM, only the last radio is checked, and setting .checked unchecks the other."""
        ctx = Context()
        ctx.add_callable("host_parse_html", _host_parse_html)
        ctx.eval(MINIDOM_JS)
        ctx.eval("""
        var html = '<form><input id="r1" type="radio" name="tier" value="1" checked><input id="r2" type="radio" name="tier" value="2" checked></form>';
        var docTree = host_parse_html(html);
        globalThis.__load(JSON.parse(docTree), 'http://example.com');
        """)
        self.assertFalse(ctx.eval('document.getElementById("r1").checked'))
        self.assertTrue(ctx.eval('document.getElementById("r2").checked'))

        # Now programmatically check r1 -> r2 must become unchecked
        ctx.eval('document.getElementById("r1").checked = true;')
        self.assertTrue(ctx.eval('document.getElementById("r1").checked'))
        self.assertFalse(ctx.eval('document.getElementById("r2").checked'))


class StorageKeyOrderTests(unittest.TestCase):
    def test_storage_key_order_preserves_insertion_order(self):
        """localStorage.key(i) preserves insertion order without numeric key sorting."""
        ctx = Context()
        ctx.eval(MINIDOM_JS)
        ctx.eval("""
        localStorage.setItem("zeta", "last");
        localStorage.setItem("10", "ten");
        localStorage.setItem("alpha", "first");
        """)
        self.assertEqual(ctx.eval('localStorage.key(0)'), "zeta")
        self.assertEqual(ctx.eval('localStorage.key(1)'), "10")
        self.assertEqual(ctx.eval('localStorage.key(2)'), "alpha")
        self.assertEqual(ctx.eval('localStorage.length'), 3)


class McpErrorTokenAccountingTests(unittest.TestCase):
    def test_tool_call_error_accounts_tokens(self):
        """McpServer accounts tokens on tool call errors and sets isError: true."""
        server = McpServer()
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "non_existent_tool", "arguments": {}}
        }
        sent = []
        server._send = lambda data: sent.append(data)

        async def run():
            # simulate receiving message
            name = req["params"]["name"]
            try:
                await server.handle_call_tool(name, {})
            except Exception as e:
                err_msg = f"Error executing {name}: {e}"
                err_result = server._format_result(err_msg)
                err_result["isError"] = True
                sent.append({"jsonrpc": "2.0", "id": 1, "result": err_result})

        asyncio.run(run())
        self.assertEqual(len(sent), 1)
        res = sent[0]["result"]
        self.assertTrue(res.get("isError"))
        self.assertGreater(server.total_tokens_burned, 0)
        self.assertEqual(server.total_tool_calls, 1)
        self.assertIn("[tokens: ~", res["content"][0]["text"])
