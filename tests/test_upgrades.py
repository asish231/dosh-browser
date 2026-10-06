from __future__ import annotations

import asyncio
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from webresearch.acd import build_acd
from webresearch.engine import chromium
from webresearch.parse import parse
from webresearch.session import Browser
from webresearch.snapshot import snapshot
from webresearch.types import FetchResult


class AdFilteringTests(unittest.TestCase):
    def test_ad_and_sponsored_classes_skipped_in_inside_chrome(self):
        html = """
        <html>
          <body>
            <main>
              <article>
                <h1>Real Article Headline</h1>
                <p>This is genuine content.</p>
                <div class="advertisement">
                  <p>Buy this product now!</p>
                </div>
                <div class="sponsored-content">
                  <p>Sponsored partner message</p>
                </div>
                <div id="google_ads">
                  <p>Ad banner</p>
                </div>
                <div class="taboola-feed">
                  <p>Around the web recommendation</p>
                </div>
              </article>
            </main>
          </body>
        </html>
        """
        doc = parse(
            FetchResult(
                url="https://example.com/news",
                final_url="https://example.com/news",
                status=200,
                content_type="text/html",
                kind="html",
                body=html,
            )
        )
        snap = snapshot(doc)
        # Genuine content must be retained
        self.assertIn("Real Article Headline", snap.view)
        self.assertIn("This is genuine content", snap.view)

        # Ads and sponsored blocks must be filtered
        self.assertNotIn("Buy this product now", snap.view)
        self.assertNotIn("Sponsored partner message", snap.view)
        self.assertNotIn("Ad banner", snap.view)
        self.assertNotIn("Around the web recommendation", snap.view)

        # ACD blocks must also omit the ad content
        acd = build_acd(html=html, url="https://example.com/news", title="Real Article Headline")
        acd_text = " ".join(b.text for b in acd.blocks)
        self.assertIn("Real Article Headline", acd_text)
        self.assertNotIn("Buy this product now", acd_text)
        self.assertNotIn("Around the web recommendation", acd_text)


class WafEscalationTests(unittest.TestCase):
    @unittest.skipUnless(chromium.available(), "chromium driver not installed")
    def test_waf_challenge_markers_trigger_escalation(self):
        samples = [
            "<html><head><script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script></head><body>cf</body></html>",
            "<html><body><div id='cf-challenge'>Just a moment...</div></body></html>",
            "<html><body><form action='https://www.google.com/sorry/index'>robot check</form></body></html>",
            "<html><body>Our systems have detected unusual traffic from your computer network.</body></html>",
            "<html><head><script src='https://js.datadome.co/tags.js'></script></head><body>blocked</body></html>",
        ]
        for html in samples:
            self.assertTrue(
                chromium.should_escalate(html),
                f"Expected escalation for marker in: {html[:60]}",
            )

    @unittest.skipUnless(chromium.available(), "chromium driver not installed")
    def test_benign_html_does_not_escalate(self):
        html = "<html><body><h1>Hello World</h1><p>Simple static text.</p></body></html>"
        self.assertFalse(chromium.should_escalate(html))


class CookieSyncTests(unittest.TestCase):
    def test_sync_cookies_to_client(self):
        async def run():
            client = httpx.AsyncClient()
            cookies_from_browser = [
                {
                    "name": "cf_clearance",
                    "value": "secret_token_12345",
                    "domain": ".example.com",
                    "path": "/",
                },
                {
                    "name": "session_id",
                    "value": "xyz987",
                    "domain": "example.com",
                    "path": "/app",
                },
            ]
            chromium.sync_cookies_to_client(cookies_from_browser, client)
            self.assertEqual(
                client.cookies.get("cf_clearance", domain="example.com"), "secret_token_12345"
            )
            self.assertEqual(client.cookies.get("session_id", domain="example.com"), "xyz987")
            await client.aclose()

        asyncio.run(run())

    def test_extract_cookies_for_chromium(self):
        client = httpx.AsyncClient()
        client.cookies.set("auth_user", "alice", domain="example.com", path="/")
        client.cookies.set("pref_theme", "dark", domain="example.com", path="/")

        extracted = chromium.extract_cookies_for_chromium(client)
        names = {c["name"] for c in extracted}
        self.assertIn("auth_user", names)
        self.assertIn("pref_theme", names)
        auth = next(c for c in extracted if c["name"] == "auth_user")
        self.assertEqual(auth["value"], "alice")
        self.assertEqual(auth["domain"], "example.com")


COOKIE_SPA = b"""<!doctype html><html><head><title>CookieSPA</title></head><body>
<div id="root"></div>
<script type="module">
document.cookie = "server_clearance=hostel_verified_abc; path=/";
const root = document.getElementById('root');
root.innerHTML = '<h1>Logged In Successfully</h1>';
</script>
</body></html>"""


class _CookieHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("Set-Cookie", "http_challenge=resolved_pass; Path=/")
        self.send_header("content-length", str(len(COOKIE_SPA)))
        self.end_headers()
        self.wfile.write(COOKIE_SPA)

    def log_message(self, *_args):
        return


@unittest.skipUnless(chromium.available(), "chromium lane not installed")
class LiveChromiumCookieSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _CookieHandler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.site.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()

    def test_session_syncs_cookies_from_escalated_chromium_render(self):
        async def go():
            browser = Browser()
            session = await browser.create()
            try:
                page = await session.goto(self.url, js="always")
                self.assertEqual(page.engine, "chromium")
                # Both JS-set and server-set cookies must now exist in session.client.cookies!
                cookies = dict(session.client.cookies)
                self.assertIn("server_clearance", cookies)
                self.assertEqual(cookies["server_clearance"], "hostel_verified_abc")
            finally:
                await browser.close_all()

        asyncio.run(go())
