import asyncio
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi.testclient import TestClient

from webresearch.engine import chromium
from webresearch.read import read
from webresearch.server import create_app
from webresearch.session import Browser

PAGE = """<!doctype html><html><body>
<div id="root">loading</div>
<script>
window.__NEXT_DATA__ = {props:{pageProps:{city:"Paris"}}};
fetch('/api/hotels').then(function(r){ return r.json(); }).then(function(d){
  document.getElementById('root').textContent = d.hotels.join(', ');
});
</script>
</body></html>"""


class PipelineTests(unittest.TestCase):
    def test_read_runs_fetch_and_builds_acd(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/hotels":
                return httpx.Response(200, json={"hotels": ["Ritz", "Lutetia"]})
            return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await read("https://example.com/", client=client, js="always")

        page = asyncio.run(go())
        self.assertIn("Ritz", page.view)
        self.assertIsNotNone(page.acd)
        self.assertIn("__NEXT_DATA__", page.acd.framework)
        self.assertGreaterEqual(page.acd.counts["api"], 1)


class SessionLimitTests(unittest.TestCase):
    def test_browser_evicts_oldest_over_cap(self):
        async def go():
            browser = Browser(max_sessions=2, ttl_seconds=1000)
            s1 = await browser.create()
            s2 = await browser.create()
            s3 = await browser.create()
            alive = list(browser.sessions)
            await browser.close_all()
            return s1.id, s2.id, s3.id, alive

        s1, s2, s3, alive = asyncio.run(go())
        self.assertNotIn(s1, alive)
        self.assertIn(s2, alive)
        self.assertIn(s3, alive)
        self.assertEqual(len(alive), 2)


SPA_PAGE = b"""<!doctype html><html><head><title>SPA</title></head><body>
<div id="root">loading</div>
<script type="module">
const res = await fetch('/api/data');
const data = await res.json();
document.getElementById('root').textContent = data.items.join(' | ');
</script>
</body></html>"""


class _SpaHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            body = b'{"items":["alpha","beta","gamma"]}'
            content_type = "application/json"
        else:
            body = SPA_PAGE
            content_type = "text/html"
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@unittest.skipUnless(chromium.available(), "chromium lane not installed")
class ChromiumEscalationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _SpaHandler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.site.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()

    def test_spa_escalates_and_renders(self):
        async def go():
            async with httpx.AsyncClient() as client:
                return await read(self.url, client=client, js="always")

        page = asyncio.run(go())
        self.assertIn("alpha | beta | gamma", page.view)
        self.assertTrue(page.js_ran)

    def test_chromium_escalation_and_engine(self):
        async def go():
            browser = Browser()
            session = await browser.create()
            try:
                page = await session.goto(self.url, js="always")
                self.assertEqual(page.engine, "chromium")
            finally:
                await browser.close_all()

        asyncio.run(go())


CLICK_SPA = b"""<!doctype html><html><head><title>ClickSPA</title></head><body>
<div id="root"></div>
<script type="module">
const root = document.getElementById('root');
let count = 0;
function render() {
  root.innerHTML = '<button id="b">count: ' + count + '</button>';
  document.getElementById('b').onclick = function(){ count++; render(); };
}
render();
</script>
</body></html>"""


class _ClickHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(CLICK_SPA)))
        self.end_headers()
        self.wfile.write(CLICK_SPA)

    def log_message(self, *_args):
        return


@unittest.skipUnless(chromium.available(), "chromium lane not installed")
class ChromiumInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _ClickHandler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.site.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()

    def test_cdp_click_increments_on_live_spa(self):
        async def go():
            browser = Browser()
            session = await browser.create()
            try:
                page = await session.goto(self.url, js="always")
                self.assertEqual(page.engine, "chromium")
                btn = next(t for t in page.targets if t.tag == "button")
                page2 = await session.click(btn.ref)
                self.assertEqual(page2.engine, "chromium")
                self.assertIn("count: 1", page2.view)
            finally:
                await browser.close_all()

        asyncio.run(go())


class AuthTests(unittest.TestCase):
    def test_token_required_when_configured(self):
        os.environ["WR_API_TOKEN"] = "secret"
        try:
            with TestClient(create_app()) as api:
                denied = api.get("/v1/tools")
                self.assertEqual(denied.status_code, 401)
                allowed = api.get("/v1/tools", headers={"x-api-token": "secret"})
                self.assertEqual(allowed.status_code, 200)
                bearer = api.post(
                    "/v1/sessions",
                    json={},
                    headers={"authorization": "Bearer secret"},
                )
                self.assertEqual(bearer.status_code, 200)
        finally:
            os.environ.pop("WR_API_TOKEN", None)


if __name__ == "__main__":
    unittest.main()