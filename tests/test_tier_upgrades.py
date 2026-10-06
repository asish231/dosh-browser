from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

import httpx

from webresearch.acd import build_acd
from webresearch.consent import dismiss_consent_dom, has_consent_dialog
from webresearch.kinematics import generate_human_trajectory, human_keystroke_delays
from webresearch.mcp import McpServer
from webresearch.profile import Profile
from webresearch.session import Browser, Session
from webresearch.types import Page


class ProfileTests(unittest.TestCase):
    def test_save_and_load_cookies(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = Profile(name="test_user", base_dir=tmp)
            cookies = [
                {"name": "session", "value": "secret_abc", "domain": "example.com", "path": "/"},
                {"name": "AEC", "value": "anti_abuse_123", "domain": "google.com", "path": "/"},
            ]
            prof.save_cookies(cookies)

            loaded = prof.load_cookies()
            self.assertEqual(len(loaded), 2)
            names = {c["name"] for c in loaded}
            self.assertIn("session", names)
            self.assertIn("AEC", names)

    def test_import_and_export_netscape(self):
        sample = (
            "# Netscape HTTP Cookie File\n"
            ".google.com\tTRUE\t/\tTRUE\t2147483647\tAEC\tcookie_aec_token\n"
            "example.com\tFALSE\t/app\tFALSE\t0\tsid\tmy_session_id\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            prof = Profile(name="imported", base_dir=tmp)
            count = prof.import_netscape(sample)
            self.assertEqual(count, 2)

            exported = prof.export_netscape()
            self.assertIn("cookie_aec_token", exported)
            self.assertIn("my_session_id", exported)

    def test_session_profile_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = Profile(name="persisted", base_dir=tmp)
            cookies = [{"name": "auth", "value": "token_xyz", "domain": "127.0.0.1", "path": "/"}]
            prof.save_cookies(cookies)

            async def run():
                client = httpx.AsyncClient()
                session = Session(client, "s_test", profile=prof)
                # Verify cookie was applied from profile
                self.assertEqual(session.client.cookies.get("auth", domain="127.0.0.1"), "token_xyz")

                # Set new cookie on client
                session.client.cookies.set("new_cookie", "val_123", domain="127.0.0.1", path="/")
                await session.close()

                # Verify profile automatically saved the new cookie on close
                reloaded = prof.load_cookies()
                names = {c["name"] for c in reloaded}
                self.assertIn("new_cookie", names)

            asyncio.run(run())


class ConsentDismisserTests(unittest.TestCase):
    def test_has_consent_dialog(self):
        self.assertTrue(has_consent_dialog("<div id='onetrust-banner-sdk'>Please accept</div>"))
        self.assertTrue(has_consent_dialog("<div class='cookie-consent'>Accept cookies</div>"))
        self.assertFalse(has_consent_dialog("<div>Welcome to my blog post</div>"))

    def test_dismiss_consent_dom(self):
        html = """
        <html>
          <body style="overflow: hidden;">
            <div id="onetrust-consent-sdk">
              <button id="onetrust-accept-btn-handler">Accept all cookies</button>
            </div>
            <main>
              <h1>Real Page Content</h1>
              <p>Hello world.</p>
            </main>
          </body>
        </html>
        """
        cleaned, dismissed = dismiss_consent_dom(html)
        self.assertTrue(dismissed)
        self.assertNotIn("onetrust-consent-sdk", cleaned)
        self.assertIn("Real Page Content", cleaned)
        self.assertNotIn("overflow: hidden", cleaned)


class KinematicsTests(unittest.TestCase):
    def test_trajectory_generation(self):
        start = (50.0, 50.0)
        end = (400.0, 300.0)
        trajectory = generate_human_trajectory(start, end)
        self.assertGreater(len(trajectory), 5)
        # End point must match target exactly
        self.assertEqual(trajectory[-1], end)

    def test_keystroke_cadence(self):
        text = "Hello, world! 123"
        delays = human_keystroke_delays(text)
        self.assertEqual(len(delays), len(text))
        for d in delays:
            self.assertGreater(d, 0.01)
            self.assertLess(d, 0.4)


class MultiTabTests(unittest.TestCase):
    def test_multi_tab_operations(self):
        async def run():
            browser = Browser()
            session = await browser.create()
            self.assertEqual(len(session.tabs), 1)
            self.assertEqual(session.active_tab_id, "tab_1")

            # Push a fake page to tab_1
            p1 = Page(url="https://example.com/one", title="Page One", view="Page One Content", status=200)
            session._push(p1)
            self.assertEqual(session.page.title, "Page One")

            # Open tab 2
            t2 = session.new_tab("tab_2")
            self.assertEqual(session.active_tab_id, "tab_2")
            self.assertIsNone(session.page)

            # Push page to tab 2
            p2 = Page(url="https://example.com/two", title="Page Two", view="Page Two Content", status=200)
            session._push(p2)
            self.assertEqual(session.page.title, "Page Two")

            # List tabs
            tab_list = session.tabs
            self.assertEqual(len(tab_list), 2)

            # Switch back to tab_1
            session.switch_tab("tab_1")
            self.assertEqual(session.page.title, "Page One")
            self.assertEqual(session.page.url, "https://example.com/one")

            # Close tab 2
            session.close_tab("tab_2")
            self.assertEqual(len(session.tabs), 1)
            await browser.close_all()

        asyncio.run(run())


class McpServerTests(unittest.TestCase):
    def test_mcp_tool_execution(self):
        async def run():
            server = McpServer()
            try:
                # Test browser_tabs tool
                tabs_res = await server.handle_call_tool("browser_tabs", {"action": "list"})
                self.assertIn("tab_1", tabs_res["content"][0]["text"])

                # Test browser_search tool with fast DDG fallback
                search_res = await server.handle_call_tool("browser_search", {"query": "python test", "max_hits": 2})
                self.assertIn("SEARCH: python test", search_res["content"][0]["text"])
            finally:
                if server.session is not None:
                    await server.session.close()
                await server.browser.close_all()

        asyncio.run(run())
