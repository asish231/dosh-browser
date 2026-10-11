import asyncio
import unittest
import httpx

from webresearch.mcp import McpServer, TOOLS
from webresearch.session import Browser, Session
from webresearch.engine.runtime import run_scripts


class StealthModeTests(unittest.TestCase):
    def test_tools_schema_exposes_stealth(self):
        tool_names = {t["name"]: t for t in TOOLS}
        self.assertIn("browser_stealth", tool_names)
        self.assertIn("stealth", tool_names["browser_click"]["inputSchema"]["properties"])
        self.assertIn("stealth", tool_names["browser_type"]["inputSchema"]["properties"])
        self.assertIn("stealth", tool_names["browser_act"]["inputSchema"]["properties"])
        self.assertIn("stealth", tool_names["browser_scroll"]["inputSchema"]["properties"])

    def test_fast_vs_stealth_clock_and_events(self):
        async def run():
            html = """
            <!DOCTYPE html>
            <html>
            <body>
                <input id="test-input" type="text" value="" />
                <div id="slider" style="width: 200px; height: 30px;">Handle</div>
                <button id="btn" onclick="document.getElementById('test-input').value = 'btn-clicked'">Click Me</button>
            </body>
            </html>
            """
            client = httpx.AsyncClient()

            # Fast mode typing: instant, clock offset remains 0
            new_html, info = await run_scripts(
                html,
                "http://example.com",
                client,
                force=True,
                actions=[{"kind": "type", "selector": "#test-input", "text": "fast", "stealth": False}],
            )
            ctx = info["ctx"]
            offset_fast = ctx.eval("__getClockOffset()")
            self.assertEqual(offset_fast, 0)
            self.assertEqual(ctx.eval("document.getElementById('test-input').value"), "fast")

            # Stealth mode typing: advances simulated clock
            new_html2, info2 = await run_scripts(
                html,
                "http://example.com",
                client,
                force=True,
                actions=[{"kind": "type", "selector": "#test-input", "text": "stealth", "stealth": True}],
            )
            ctx2 = info2["ctx"]
            offset_stealth = ctx2.eval("__getClockOffset()")
            self.assertGreater(offset_stealth, 50)
            self.assertEqual(ctx2.eval("document.getElementById('test-input').value"), "stealth")

            # Fast mode drag: minimal linear steps, clock offset remains 0
            new_html3, info3 = await run_scripts(
                html,
                "http://example.com",
                client,
                force=True,
                actions=[{"kind": "drag", "selector": "#slider", "dx": 100, "dy": 0, "stealth": False}],
            )
            offset_drag_fast = info3["ctx"].eval("__getClockOffset()")
            self.assertEqual(offset_drag_fast, 0)

            # Stealth mode drag: Bézier arc + tremor + pauses, clock advances
            new_html4, info4 = await run_scripts(
                html,
                "http://example.com",
                client,
                force=True,
                actions=[{"kind": "drag", "selector": "#slider", "dx": 100, "dy": 0, "stealth": True}],
            )
            offset_drag_stealth = info4["ctx"].eval("__getClockOffset()")
            self.assertGreater(offset_drag_stealth, 100)

            await client.aclose()

        asyncio.run(run())

    def test_session_stealth_toggle_and_actions(self):
        async def run():
            browser = Browser()
            session = await browser.create()
            try:
                self.assertFalse(session.stealth)
                session.set_stealth(True)
                self.assertTrue(session.stealth)
                session.set_stealth(False)
                self.assertFalse(session.stealth)

                # Test page interaction
                html = """
                <!DOCTYPE html>
                <html>
                <body>
                    <input id="q" name="q" type="text" value="" />
                    <button id="go" onclick="document.getElementById('q').value = 'done'">Go</button>
                </body>
                </html>
                """
                session.html = html
                session.url = "http://test.local"
                await session._apply([], html, max_chars=8000)

                actions = session.actions()
                input_act = next(a for a in actions if a.selector == "#q")
                button_act = next(a for a in actions if a.selector == "#go")

                # Fast typing through session.act
                await session.act(input_act.ref, text="fast-query", stealth=False)
                self.assertIn("fast-query", session.html)

                # Stealth clicking through session.act
                await session.act(button_act.ref, stealth=True)
                self.assertIn("done", session.html)

            finally:
                await session.close()
                await browser.close_all()

        asyncio.run(run())

    def test_mcp_stealth_tool_and_actuation(self):
        async def run():
            server = McpServer()
            try:
                # 1. Query stealth tool
                res = await server.handle_call_tool("browser_stealth", {})
                self.assertIn("disabled", res["content"][0]["text"])

                # 2. Enable stealth tool
                res_on = await server.handle_call_tool("browser_stealth", {"enabled": True})
                self.assertIn("enabled", res_on["content"][0]["text"])
                session = await server._get_session()
                self.assertTrue(session.stealth)

                # 3. Disable stealth tool
                res_off = await server.handle_call_tool("browser_stealth", {"enabled": False})
                self.assertIn("disabled", res_off["content"][0]["text"])
                self.assertFalse(session.stealth)

                # Setup mock page
                html = """
                <!DOCTYPE html>
                <html>
                <body>
                    <input id="text-field" name="text-field" type="text" value="" />
                    <button id="btn1">Btn</button>
                </body>
                </html>
                """
                session.html = html
                session.url = "http://test.local"
                page = await session._apply([], html, max_chars=8000)

                first_target_ref = page.targets[0].ref

                # Call browser_type with stealth=True
                type_res = await server.handle_call_tool(
                    "browser_type",
                    {"ref": first_target_ref, "text": "hello-stealth", "stealth": True},
                )
                self.assertIn("hello-stealth", type_res["content"][0]["text"])

                # Call browser_scroll with stealth=False
                scroll_res = await server.handle_call_tool(
                    "browser_scroll",
                    {"y": 400, "stealth": False},
                )
                self.assertIn("test.local", scroll_res["content"][0]["text"])

            finally:
                if server.session is not None:
                    await server.session.close()
                await server.browser.close_all()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
