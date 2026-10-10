import asyncio
import unittest
import httpx

from webresearch.engine.runtime import run_scripts

class ModernWebPlatformTests(unittest.TestCase):
    def test_crypto_subtle_digest_sha256(self):
        html = """
        <html><body>
        <script>
            var text = "hello world";
            var enc = new TextEncoder();
            crypto.subtle.digest("SHA-256", enc.encode(text)).then(function(buf) {
                var arr = new Uint8Array(buf);
                var hex = "";
                for (var i = 0; i < arr.length; i++) {
                    var h = arr[i].toString(16);
                    hex += (h.length === 1 ? "0" : "") + h;
                }
                document.body.setAttribute("data-sha256", hex);
            });
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            # Standard SHA-256 of "hello world": b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9
            expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
            self.assertIn(f'data-sha256="{expected}"', out)
        asyncio.run(run())

    def test_readable_stream_and_response_body(self):
        html = """
        <html><body>
        <script>
            var resp = new Response("Streaming chunk data", {
                status: 200,
                headers: { "Content-Type": "text/plain" }
            });
            var reader = resp.body.getReader();
            reader.read().then(function(res) {
                var dec = new TextDecoder();
                var text = dec.decode(res.value);
                document.body.setAttribute("data-read", text);
                document.body.setAttribute("data-done", String(res.done));
            });
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-read="Streaming chunk data"', out)
            self.assertIn('data-done="false"', out)
        asyncio.run(run())

    def test_message_channel_macro_scheduling(self):
        html = """
        <html><body>
        <script>
            var channel = new MessageChannel();
            channel.port1.onmessage = function(e) {
                document.body.setAttribute("data-port-msg", e.data);
            };
            channel.port2.postMessage("react_scheduler_ping");
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-port-msg="react_scheduler_ping"', out)
        asyncio.run(run())

    def test_headers_and_event_source(self):
        html = """
        <html><body>
        <script>
            var h = new Headers({ "X-Custom": "Value1" });
            h.append("X-Custom", "Value2");
            document.body.setAttribute("data-hdr-get", h.get("x-custom"));
            document.body.setAttribute("data-hdr-has", String(h.has("x-custom")));

            var es = new EventSource("https://example.com/events");
            document.body.setAttribute("data-es-url", es.url);
            document.body.setAttribute("data-es-state", String(es.readyState));
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-hdr-get="Value1, Value2"', out)
            self.assertIn('data-hdr-has="true"', out)
            self.assertIn('data-es-url="https://example.com/events"', out)
        asyncio.run(run())

    def test_owner_document_and_window_and_scroll_dimensions(self):
        html = """
        <html><body>
        <div id="test-root"></div>
        <script>
            var root = document.getElementById("test-root");
            var isDoc = root.ownerDocument === document;
            document.body.setAttribute("data-owner-doc", String(isDoc));
            document.body.setAttribute("data-window-instance", String(window instanceof Window));
            document.body.setAttribute("data-has-scroll-w", String(typeof root.scrollWidth === "number"));
            document.body.setAttribute("data-has-scroll-h", String(typeof root.scrollHeight === "number"));
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-owner-doc="true"', out)
            self.assertIn('data-window-instance="true"', out)
            self.assertIn('data-has-scroll-w="true"', out)
            self.assertIn('data-has-scroll-h="true"', out)
        asyncio.run(run())

    def test_client_navigation_location_assign_and_href(self):
        html = """
        <html><body>
        <script>
            window.location.assign("/dashboard");
        </script>
        </body></html>
        """
        async def run():
            async with httpx.AsyncClient() as client:
                _, info = await run_scripts(html, "https://example.com/login", client, force=True)
                self.assertEqual(info.get("navigated"), "https://example.com/dashboard")
        asyncio.run(run())

    def test_form_submit_button_click_redirects(self):
        html = """
        <html><body>
        <form id="login">
            <input name="user" value="admin">
            <button id="btn" type="submit">Log in</button>
        </form>
        <script>
            document.getElementById("login").addEventListener("submit", function(e) {
                e.preventDefault();
                window.location.href = "/admin/panel";
            });
        </script>
        </body></html>
        """
        async def run():
            async with httpx.AsyncClient() as client:
                actions = [{"kind": "activate", "selector": "#btn"}]
                _, info = await run_scripts(html, "https://example.com/login", client, force=True, actions=actions)
                self.assertEqual(info.get("navigated"), "https://example.com/admin/panel")
        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()
