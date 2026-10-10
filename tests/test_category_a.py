import asyncio
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

import httpx

from webresearch.engine.binary import unpack_anbf
from webresearch.engine.runtime import run_scripts
from webresearch.profile import Profile
from webresearch.session import Session


class CategoryATests(unittest.TestCase):
    def test_proportional_font_advances_and_canvas_metrics(self):
        html = """
        <html><body>
        <canvas id="c"></canvas>
        <script>
            var c = document.getElementById("c");
            var ctx = c.getContext("2d");
            ctx.font = "20px Arial";
            var mNarrow = ctx.measureText("iii");
            var mWide = ctx.measureText("MMM");
            document.body.setAttribute("data-narrow-w", String(mNarrow.width));
            document.body.setAttribute("data-wide-w", String(mWide.width));
            document.body.setAttribute("data-ascent", String(mNarrow.actualBoundingBoxAscent));
            document.body.setAttribute("data-font-ascent", String(mNarrow.fontBoundingBoxAscent));
        </script>
        </body></html>
        """

        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-narrow-w="', out)
            self.assertIn('data-wide-w="', out)
            import re
            narrow_w = float(re.search(r'data-narrow-w="([^"]+)"', out).group(1))
            wide_w = float(re.search(r'data-wide-w="([^"]+)"', out).group(1))
            ascent = float(re.search(r'data-ascent="([^"]+)"', out).group(1))
            # Narrow chars (i) must have significantly smaller advance than wide chars (M)
            self.assertGreater(wide_w, narrow_w * 2.0)
            self.assertGreater(ascent, 0)

        asyncio.run(run())

    def test_custom_elements_lifecycle_and_shadow_dom(self):
        html = """
        <html><body>
        <div id="container"></div>
        <script>
            class UserBadge extends HTMLElement {
                static get observedAttributes() { return ["badge-title"]; }
                connectedCallback() {
                    this.setAttribute("data-connected", "yes");
                }
                attributeChangedCallback(name, oldVal, newVal) {
                    this.setAttribute("data-changed", name + ":" + newVal);
                }
            }
            customElements.define("user-badge", UserBadge);

            var el = document.createElement("user-badge");
            el.setAttribute("badge-title", "Senior Engineer");
            var shadow = el.attachShadow({ mode: "open" });
            shadow.appendChild(document.createElement("span"));

            var container = document.getElementById("container");
            container.appendChild(el);
            document.body.setAttribute("data-shadow-nodes", String(el.shadowRoot ? el.shadowRoot.childNodes.length : 0));
        </script>
        </body></html>
        """

        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-connected="yes"', out)
            self.assertIn('data-changed="badge-title:Senior Engineer"', out)
            self.assertIn('data-shadow-nodes="1"', out)

        asyncio.run(run())

    def test_modern_navigator_apis_support(self):
        html = """
        <html><body>
        <script>
            navigator.permissions.query({ name: "geolocation" }).then(function(res) {
                document.body.setAttribute("data-perm-state", res.state);
            });
            navigator.clipboard.readText().then(function(txt) {
                document.body.setAttribute("data-clipboard", "ok");
            });
            navigator.mediaDevices.enumerateDevices().then(function(devs) {
                document.body.setAttribute("data-devices", String(devs.length));
            });
        </script>
        </body></html>
        """

        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-perm-state="granted"', out)
            self.assertIn('data-clipboard="ok"', out)
            self.assertIn('data-devices="0"', out)

        asyncio.run(run())

    def test_live_mutation_observer_deltas_and_anbf_binary_patch(self):
        html = """
        <html><body>
        <div id="root">Initial Content</div>
        <button id="btn" onclick="document.getElementById('root').textContent = 'Updated Content';">Click</button>
        </body></html>
        """

        async def run():
            client = httpx.AsyncClient()
            actions = [{"kind": "activate", "selector": "#btn"}]
            out, info = await run_scripts(html, "https://example.com/", client, force=True, actions=actions)
            deltas = info.get("deltas", [])
            self.assertTrue(len(deltas) > 0)
            self.assertTrue(any("Updated Content" in d.get("content", "") for d in deltas))

            # Verify Session integration and ANBF delta packing
            session = Session(client, "sess_deltas")
            session.html = html
            session.url = "https://example.com/"
            await session._apply(actions, html, max_chars=8000)
            self.assertTrue(len(session.last_deltas) > 0)

            patch_bytes = session.get_last_delta_patch()
            self.assertGreater(len(patch_bytes), 32)
            unpacked = unpack_anbf(patch_bytes)
            self.assertTrue(len(unpacked.deltas) > 0)
            self.assertTrue(any("Updated Content" in d.content for d in unpacked.deltas))

        asyncio.run(run())

    def test_warmed_profile_and_waf_clearance(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = Profile(name="test_waf_prof", base_dir=tmp)
            # Initially not warmed
            self.assertFalse(prof.is_warmed("cloudflare.com"))

            # Save Cloudflare clearance cookie
            future_time = time.time() + 86400
            prof.save_cookies([
                {"name": "cf_clearance", "value": "test_token_12345", "domain": ".cloudflare.com", "path": "/", "expires": future_time},
                {"name": "_ga", "value": "GA1.1", "domain": ".cloudflare.com", "path": "/"}
            ])

            self.assertTrue(prof.is_warmed("cloudflare.com"))
            waf_tokens = prof.get_waf_tokens("cloudflare.com")
            self.assertIn("cf_clearance", waf_tokens)
            self.assertEqual(waf_tokens["cf_clearance"], "test_token_12345")

            # Local storage persistence in SQLite & JSON
            prof.save_storage("https://example.com", "user_pref", "dark_mode")
            storage = prof.load_storage("https://example.com")
            self.assertEqual(storage.get("user_pref"), "dark_mode")

            # Verify SQLite database table storage contains it directly
            with sqlite3.connect(prof.db_file) as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM storage WHERE origin = ? AND key = ?", ("https://example.com", "user_pref"))
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "dark_mode")

            # Test import_browser_sqlite from simulated Chrome and Firefox sqlite files
            chrome_db = Path(tmp) / "chrome_cookies.sqlite"
            with sqlite3.connect(chrome_db) as conn:
                conn.execute("""
                    CREATE TABLE cookies (
                        name TEXT, value TEXT, host_key TEXT, path TEXT, is_secure INTEGER, expires_utc INTEGER
                    )
                """)
                conn.execute(
                    "INSERT INTO cookies VALUES (?, ?, ?, ?, ?, ?)",
                    ("sessionid", "ig_session_998877", ".instagram.com", "/", 1, 0)
                )
                conn.commit()

            imported_count = prof.import_browser_sqlite(chrome_db)
            self.assertEqual(imported_count, 1)
            ig_cookies = prof.get_domain_cookies("instagram.com")
            self.assertTrue(any(c["name"] == "sessionid" and c["value"] == "ig_session_998877" for c in ig_cookies))

            # Test clear() clears both files and SQLite records
            prof.clear()
            self.assertEqual(len(prof.load_cookies()), 0)
            self.assertEqual(len(prof.load_storage("https://example.com")), 0)

    def test_cross_origin_postmessage_handshake(self):
        # 1. Parent posting to iframe window
        parent_html = """
        <html><body>
        <iframe id="child-frame" src="https://child.example.com/"></iframe>
        <script>
            var f = document.getElementById("child-frame");
            f.contentWindow.postMessage("hello from parent", "*");
        </script>
        </body></html>
        """

        async def run_parent():
            client = httpx.AsyncClient()
            out, info = await run_scripts(parent_html, "https://parent.example.com/", client, force=True)
            self.assertTrue(len(info["post_messages"]) > 0)
            msg = info["post_messages"][0]
            self.assertEqual(msg["data"], "hello from parent")

        asyncio.run(run_parent())

        # 2. Child receiving message via initial_messages
        child_html = """
        <html><body>
        <div id="output">No Message</div>
        <script>
            window.addEventListener("message", function(e) {
                document.getElementById("output").textContent = "Received: " + e.data;
            });
            window.parent.postMessage("ack from child", "https://parent.example.com");
        </script>
        </body></html>
        """

        async def run_child():
            client = httpx.AsyncClient()
            inbox = [{"data": "payload_test", "origin": "https://parent.example.com"}]
            out, info = await run_scripts(child_html, "https://child.example.com/", client, force=True, messages=inbox)
            self.assertIn("Received: payload_test", out)
            # Verify child posted message back to parent
            child_msgs = [m for m in info["post_messages"] if m.get("target") == "parent"]
            self.assertTrue(len(child_msgs) > 0)
            self.assertEqual(child_msgs[0]["data"], "ack from child")

        asyncio.run(run_child())


if __name__ == "__main__":
    unittest.main()
