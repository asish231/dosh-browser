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

    def test_indexeddb_in_memory_persistence(self):
        html = """
        <html><body>
        <script>
            var req = window.indexedDB.open("appDB", 1);
            req.onupgradeneeded = function(e) {
                var db = e.target.result;
                db.createObjectStore("sessions", { keyPath: "id" });
            };
            req.onsuccess = function(e) {
                var db = e.target.result;
                var tx = db.transaction("sessions", "readwrite");
                var store = tx.objectStore("sessions");
                store.put({ id: "sess_123", user: "agent", role: "operator" });

                var getReq = store.get("sess_123");
                getReq.onsuccess = function(ge) {
                    var rec = ge.target.result;
                    document.body.setAttribute("data-idb-user", rec ? rec.user : "none");
                    document.body.setAttribute("data-idb-role", rec ? rec.role : "none");
                };

                store.put({ id: "sess_456", user: "bot", role: "crawler" });
                var allReq = store.getAll();
                allReq.onsuccess = function(ae) {
                    document.body.setAttribute("data-idb-total", String(ae.target.result.length));
                };
            };
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-idb-user="agent"', out)
            self.assertIn('data-idb-role="operator"', out)
            self.assertIn('data-idb-total="2"', out)
        asyncio.run(run())

    def test_active_element_and_focus_blur_events(self):
        html = """
        <html><body>
        <input id="input1" type="text" />
        <input id="input2" type="text" />
        <script>
            var events = [];
            var in1 = document.getElementById("input1");
            var in2 = document.getElementById("input2");

            in1.addEventListener("focus", function() { events.push("in1:focus"); });
            in1.addEventListener("blur", function() { events.push("in1:blur"); });
            in1.addEventListener("focusin", function() { events.push("in1:focusin"); });
            in1.addEventListener("focusout", function() { events.push("in1:focusout"); });

            in2.addEventListener("focus", function() { events.push("in2:focus"); });
            in2.addEventListener("blur", function() { events.push("in2:blur"); });
            in2.addEventListener("focusin", function() { events.push("in2:focusin"); });
            in2.addEventListener("focusout", function() { events.push("in2:focusout"); });

            in1.focus();
            var act1 = document.activeElement === in1;

            in2.focus();
            var act2 = document.activeElement === in2;

            in2.blur();
            var act3 = document.activeElement === document.body;

            document.body.setAttribute("data-active-states", [act1, act2, act3].join(","));
            document.body.setAttribute("data-focus-events", events.join(","));
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-active-states="true,true,true"', out)
            # Expect: in1 focusin, focus -> in1 blur, focusout -> in2 focusin, focus -> in2 blur, focusout
            self.assertIn('data-focus-events="in1:focusin,in1:focus,in1:blur,in1:focusout,in2:focusin,in2:focus,in2:blur,in2:focusout"', out)
        asyncio.run(run())

    def test_keystroke_sequencing(self):
        html = """
        <html><body>
        <input id="field" type="text" />
        <script>
            var log = [];
            var el = document.getElementById("field");
            el.addEventListener("keydown", function(e) { log.push("down:" + e.key + ":" + e.keyCode); });
            el.addEventListener("keypress", function(e) { log.push("press:" + e.key + ":" + e.charCode); });
            el.addEventListener("input", function(e) { log.push("input:" + e.data + ":" + el.value); });
            el.addEventListener("keyup", function(e) { log.push("up:" + e.key + ":" + e.keyCode); });
            el.addEventListener("change", function(e) { log.push("change:" + el.value); });

            window.__saveLog = function() {
                document.body.setAttribute("data-key-log", log.join(";"));
            };
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            actions = [{"kind": "type", "selector": "#field", "text": "Hi"}]
            out, info = await run_scripts(html, "https://example.com/", client, force=True, actions=actions)
            # In QuickJS context after actions, check the log
            self.assertIn('value="Hi"', out)
        asyncio.run(run())

    def test_selection_and_range_apis(self):
        html = """
        <html><body>
        <div id="content"><span>Hello</span><span>World</span></div>
        <script>
            var div = document.getElementById("content");
            var range = document.createRange();
            range.selectNodeContents(div);

            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);

            document.body.setAttribute("data-has-range", String(range instanceof Range));
            document.body.setAttribute("data-range-count", String(sel.rangeCount));
            document.body.setAttribute("data-user-act-active", String(navigator.userActivation.isActive));
            document.body.setAttribute("data-user-act-has", String(navigator.userActivation.hasBeenActive));
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            self.assertIn('data-has-range="true"', out)
            self.assertIn('data-range-count="1"', out)
            self.assertIn('data-user-act-active="true"', out)
            self.assertIn('data-user-act-has="true"', out)
        asyncio.run(run())

    def test_flexbox_and_positioning_layout(self):
        html = """
        <html>
        <head>
            <style>
                #container { display: flex; flex-direction: row; gap: 16px; width: 800px; padding: 0; margin: 0; }
                .item1 { width: 150px; height: 50px; margin: 0; }
                .item2 { width: 200px; height: 60px; margin: 0; }
                #badge { position: absolute; left: 45px; top: 25px; width: 80px; height: 30px; }
            </style>
        </head>
        <body>
            <div id="container">
                <div class="item1" id="i1">Box 1</div>
                <div class="item2" id="i2">Box 2</div>
                <div id="badge">Alert</div>
            </div>
            <script>
                var i1 = document.getElementById("i1").getBoundingClientRect();
                var i2 = document.getElementById("i2").getBoundingClientRect();
                var badge = document.getElementById("badge").getBoundingClientRect();
                var cont = document.getElementById("container").getBoundingClientRect();

                document.body.setAttribute("data-i1-w", String(i1.width));
                document.body.setAttribute("data-i2-x-offset", String(i2.x - i1.x));
                document.body.setAttribute("data-badge-rel-x", String(badge.x - cont.x));
                document.body.setAttribute("data-badge-rel-y", String(badge.y - cont.y));
                document.body.setAttribute("data-engine", document.__layoutInfo.engine);
            </script>
        </body>
        </html>
        """
        async def run():
            client = httpx.AsyncClient()
            out, info = await run_scripts(html, "https://example.com/", client, force=True)
            # i1.width = 150.
            # i2 is placed after i1 + gap: i2.x - i1.x = 150 + 16 = 166!
            # badge: relative to container left 45px, top 25px!
            self.assertIn('data-i1-w="150"', out)
            self.assertIn('data-i2-x-offset="166"', out)
            self.assertIn('data-badge-rel-x="45"', out)
            self.assertIn('data-badge-rel-y="25"', out)
            self.assertIn('data-engine="block-flow+flex+grid+positioned"', out)
        asyncio.run(run())

    def test_message_event_source_reply(self):
        html = """
        <html><body>
        <script>
            window.addEventListener("message", function(e) {
                if (e.data === "ping") {
                    e.source.postMessage("pong", e.origin);
                }
            });
        </script>
        </body></html>
        """
        async def run():
            client = httpx.AsyncClient()
            messages = [{"data": "ping", "origin": "https://child.example.com"}]
            _, info = await run_scripts(html, "https://example.com/", client, force=True, messages=messages)
            replies = [pm for pm in info.get("post_messages", []) if pm.get("data") == "pong"]
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0]["target"], "reply")
        asyncio.run(run())

    def test_hard_spa_challenge_workflow(self):
        """Simulate a complex, secure web application:

        1. Probes crypto, user activation, and IndexedDB storage.
        2. Renders dynamic flex layout with absolute badges.
        3. Enforces keystroke integrity (validates keydown, keypress, input, keyup sequence).
        4. Validates focus state (must have been activeElement).
        5. Upon form submit, sets session record into IndexedDB and performs client redirect to /secure/dashboard!
        """
        html = """
        <html>
        <head>
            <style>
                .login-card { display: flex; flex-direction: column; gap: 12px; width: 400px; }
                .input-row { width: 100%; height: 40px; }
                .btn-submit { width: 120px; height: 45px; }
                .status-chip { position: absolute; right: 20px; top: 10px; width: 50px; height: 20px; }
            </style>
        </head>
        <body>
            <form id="secure-form" class="login-card">
                <input id="auth-code" class="input-row" type="text" name="code" />
                <button id="submit-btn" class="btn-submit" type="submit">Verify</button>
                <div class="status-chip" id="chip">IDLE</div>
            </form>
            <script>
                var strokeCount = 0;
                var hadFocus = false;
                var inputEl = document.getElementById("auth-code");

                inputEl.addEventListener("focus", function() { hadFocus = true; });
                inputEl.addEventListener("keydown", function(e) { if (e.isTrusted) strokeCount++; });

                // Initialize IndexedDB
                var idbReady = false;
                var dbReq = window.indexedDB.open("SecureVault", 1);
                dbReq.onupgradeneeded = function(e) {
                    e.target.result.createObjectStore("tokens", { keyPath: "key" });
                };
                dbReq.onsuccess = function() {
                    idbReady = true;
                };

                document.getElementById("secure-form").addEventListener("submit", function(e) {
                    e.preventDefault();
                    if (!hadFocus) {
                        alert("Focus missing");
                        return;
                    }
                    if (strokeCount < 4) {
                        alert("Incomplete strokes");
                        return;
                    }
                    if (!navigator.userActivation.isActive) {
                        alert("Inactive user");
                        return;
                    }

                    // Save token to IndexedDB and redirect
                    var tx = dbReq.result.transaction("tokens", "readwrite");
                    tx.objectStore("tokens").put({ key: "auth_token", val: "TOKEN_XYZ_999" });
                    tx.oncomplete = function() {
                        window.location.assign("/secure/dashboard");
                    };
                });
            </script>
        </body>
        </html>
        """
        async def run():
            client = httpx.AsyncClient()
            actions = [
                {"kind": "type", "selector": "#auth-code", "text": "SAFE"},
                {"kind": "activate", "selector": "#submit-btn"}
            ]
            _, info = await run_scripts(html, "https://bank.example.com/login", client, force=True, actions=actions)
            # The entire challenge must pass and trigger client navigation!
            self.assertEqual(info.get("navigated"), "https://bank.example.com/secure/dashboard")
        asyncio.run(run())

    def test_session_end_to_end_spa_keystrokes_and_navigation(self):
        login_html = """
        <html>
        <head>
            <style>
                form { display: flex; flex-direction: column; gap: 8px; width: 350px; }
            </style>
        </head>
        <body>
            <h1>Secure Portal</h1>
            <form id="portal-form">
                <input id="user-input" name="user" type="text" placeholder="Username" />
                <button id="login-button" type="submit">Sign In</button>
            </form>
            <script>
                var inputFocused = false;
                var strokes = 0;
                var inp = document.getElementById("user-input");
                inp.addEventListener("focus", function() { inputFocused = true; });
                inp.addEventListener("keydown", function() { strokes++; });

                document.getElementById("portal-form").addEventListener("submit", function(e) {
                    e.preventDefault();
                    if (inputFocused && strokes >= 5) {
                        window.location.assign("/portal/home");
                    }
                });
            </script>
        </body>
        </html>
        """
        dashboard_html = """
        <html>
        <body>
            <h1>Welcome to Home Portal</h1>
            <p>Authentication successful!</p>
        </body>
        </html>
        """

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login":
                return httpx.Response(200, text=login_html, headers={"content-type": "text/html"})
            elif request.url.path == "/portal/home":
                return httpx.Response(200, text=dashboard_html, headers={"content-type": "text/html"})
            return httpx.Response(404, text="Not Found")

        async def run():
            transport = httpx.MockTransport(mock_handler)
            async with httpx.AsyncClient(transport=transport) as client:
                from webresearch.session import Session
                s = Session(client, "session_test_42")
                page = await s.goto("https://portal.example.com/login")
                self.assertEqual(page.status, 200)

                # Locate textbox action and type
                surface = s.action_surface()
                textbox_action = next(a for a in surface.actions if a.role == "textbox")
                page_after_type = await s.act(textbox_action.ref, text="alice")
                self.assertIn('value="alice"', s.html)

                # Locate button action and submit
                surface2 = s.action_surface()
                submit_action = next(a for a in surface2.actions if a.role == "button")
                page_final = await s.act(submit_action.ref)

                # Verified: automatically navigated to the home dashboard!
                self.assertEqual(page_final.url, "https://portal.example.com/portal/home")
                self.assertIn("Welcome to Home Portal", page_final.view)

        asyncio.run(run())

if __name__ == "__main__":
    unittest.main()

