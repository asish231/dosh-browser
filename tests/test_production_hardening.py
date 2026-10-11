import contextlib
import http.cookiejar
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import httpx
from fastapi.testclient import TestClient

from webresearch.engine.frames import extract_origin, load_frames
from webresearch.engine.runtime import run_scripts
from webresearch.profile import Profile
from webresearch.server import create_app
from webresearch.session import Browser, Session


class ProductionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.profile = Profile("test_user", base_dir=self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # 1. Cookie Deletion Sync Test
    def test_deleted_cookies_do_not_return_after_restart(self):
        c1 = {"name": "c1", "value": "v1", "domain": "example.com", "path": "/"}
        c2 = {"name": "c2", "value": "v2", "domain": "example.com", "path": "/"}
        self.profile.save_cookies([c1, c2])

        loaded = self.profile.load_cookies()
        self.assertEqual(len(loaded), 2)

        # Delete c1 using delete_cookie
        self.profile.delete_cookie("c1", domain="example.com")

        # Simulate restart by instantiating fresh Profile object pointing to same directory
        reopened = Profile("test_user", base_dir=self.temp_dir)
        remaining = reopened.load_cookies()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["name"], "c2")

        # Clear remaining cookies by saving empty list
        reopened.save_cookies([])
        reopened_again = Profile("test_user", base_dir=self.temp_dir)
        self.assertEqual(reopened_again.load_cookies(), [])

    # 2. Cookie Security Attributes Persistence & Restoration
    def test_cookie_security_attributes_restored_to_client(self):
        c_sec = {
            "name": "auth_token",
            "value": "secret999",
            "domain": ".example.com",
            "path": "/api",
            "secure": True,
            "expires": 1899999999,
            "http_only": True,
            "same_site": "Strict",
            "host_only": False,
            "partition_key": "top_site",
        }
        self.profile.save_cookies([c_sec])

        # Verify attributes persisted in SQLite and load_cookies
        reloaded = Profile("test_user", base_dir=self.temp_dir).load_cookies()
        self.assertEqual(len(reloaded), 1)
        ck = reloaded[0]
        self.assertTrue(ck["secure"])
        self.assertTrue(ck["http_only"])
        self.assertEqual(ck["same_site"], "Strict")
        self.assertEqual(ck["partition_key"], "top_site")
        self.assertEqual(ck["expires"], 1899999999)

        # Restore into httpx client
        client = httpx.AsyncClient()
        self.profile.apply_to_client(client)

        found_cookie = None
        for cookie_obj in client.cookies.jar:
            if cookie_obj.name == "auth_token":
                found_cookie = cookie_obj
                break

        self.assertIsNotNone(found_cookie)
        self.assertTrue(found_cookie.secure)
        self.assertEqual(found_cookie.expires, 1899999999)
        self.assertIn("HttpOnly", getattr(found_cookie, "_rest", {}))
        self.assertEqual(getattr(found_cookie, "_rest", {}).get("SameSite"), "Strict")

        # Secure attribute enforcement: must NOT send over insecure http://
        insecure_req = httpx.Request("GET", "http://example.com/api")
        client.cookies.set_cookie_header(insecure_req)
        self.assertIsNone(insecure_req.headers.get("cookie"))

        # Secure attribute enforcement: MUST send over https://
        secure_req = httpx.Request("GET", "https://example.com/api")
        client.cookies.set_cookie_header(secure_req)
        self.assertEqual(secure_req.headers.get("cookie"), "auth_token=secret999")

    # 3. Domain Matching: No Subdomain Leakage
    def test_domain_matching_does_not_leak_subdomain_to_parent(self):
        cookies = [
            {
                "name": "parent_scoped",
                "value": "p1",
                "domain": "example.com",
                "path": "/",
                "host_only": False,
            },
            {
                "name": "sub_host_only",
                "value": "s1",
                "domain": "sub.example.com",
                "path": "/",
                "host_only": True,
            },
            {
                "name": "sub_domain_scoped",
                "value": "s2",
                "domain": "sub.example.com",
                "path": "/",
                "host_only": False,
            },
        ]
        self.profile.save_cookies(cookies)

        # Requesting parent domain "example.com" MUST NOT return subdomain cookies
        parent_cookies = self.profile.get_domain_cookies("example.com")
        names_for_parent = [c["name"] for c in parent_cookies]
        self.assertIn("parent_scoped", names_for_parent)
        self.assertNotIn("sub_host_only", names_for_parent)
        self.assertNotIn("sub_domain_scoped", names_for_parent)

        # Requesting subdomain "sub.example.com" should match subdomain cookies plus domain-scoped parent
        sub_cookies = self.profile.get_domain_cookies("sub.example.com")
        names_for_sub = [c["name"] for c in sub_cookies]
        self.assertIn("sub_host_only", names_for_sub)
        self.assertIn("sub_domain_scoped", names_for_sub)
        self.assertIn("parent_scoped", names_for_sub)

    # 4. PostMessage Origin Integrity & Async Delivery
    def test_post_message_origin_cannot_be_forged_and_delivers_async(self):
        html = """
        <html><body>
        <script>
        window.log = [];
        window.received = null;
        window.addEventListener('message', function(ev) {
            window.log.push('msg_handler');
            window.received = { data: ev.data, origin: ev.origin };
        });

        // 1. Target origin mismatch: destination is self (https://example.com), but target is attacker
        window.postMessage('secret', 'https://attacker-domain.org');

        // 2. Target origin matching wildcard: sender is https://example.com:443
        window.postMessage('legit', '*');
        window.log.push('after_post_message');
        </script>
        </body></html>
        """
        async def run_test():
            client = httpx.AsyncClient()
            out_html, info = await run_scripts(html, "https://example.com/page", client, force=True)
            ctx = info.get("ctx")
            self.assertIsNotNone(ctx)

            # Ordering verification: 'after_post_message' pushed before async microtask 'msg_handler'
            order = ctx.eval("window.log.join(',')")
            self.assertEqual(order, "after_post_message,msg_handler")

            # Origin integrity verification: origin is sender's actual origin (https://example.com)
            recv_origin = ctx.eval("window.received ? window.received.origin : ''")
            self.assertEqual(recv_origin, "https://example.com")
            recv_data = ctx.eval("window.received ? window.received.data : ''")
            self.assertEqual(recv_data, "legit")

        import asyncio
        asyncio.run(run_test())

    # 5. Programmatic Script Clicks are isTrusted=false, Agent Native Clicks are isTrusted=true
    def test_programmatic_script_clicks_are_untrusted_and_agent_clicks_are_trusted(self):
        html = """
        <html><body>
        <button id="btn" onclick="document.body.setAttribute('data-last-trusted', String(event.isTrusted))">Click</button>
        <script>
        // Script attempts to call click() programmatically
        document.getElementById('btn').click();
        </script>
        </body></html>
        """
        async def run_test():
            client = httpx.AsyncClient()
            # First run: page script executed .click()
            out_html, info = await run_scripts(html, "https://example.com/", client, force=True)
            # Must be untrusted
            self.assertIn('data-last-trusted="false"', out_html)

            # Second run: agent performs native activation
            actions = [{"kind": "activate", "selector": "#btn"}]
            out_html_action, info2 = await run_scripts(html, "https://example.com/", client, actions=actions, force=True)
            # Native agent input must be trusted
            self.assertIn('data-last-trusted="true"', out_html_action)

        import asyncio
        asyncio.run(run_test())

    # 6. Pointer Capture Lifecycle and Retargeting
    def test_pointer_capture_lifecycle_and_retargeting(self):
        html = """
        <html><body>
        <div id="parent"><button id="handle">Drag</button></div>
        <script>
        window.pointerEvents = [];
        var handle = document.getElementById('handle');
        handle.addEventListener('pointerdown', function(e) {
            handle.setPointerCapture(e.pointerId);
        });
        handle.addEventListener('gotpointercapture', function(e) {
            window.pointerEvents.push('gotcapture:' + e.pointerId);
        });
        handle.addEventListener('pointermove', function(e) {
            window.pointerEvents.push('move:' + e.target.id);
        });
        handle.addEventListener('lostpointercapture', function(e) {
            window.pointerEvents.push('lostcapture:' + e.pointerId);
        });
        </script>
        </body></html>
        """
        async def run_test():
            client = httpx.AsyncClient()
            actions = [{"kind": "drag", "selector": "#handle", "dx": 50, "dy": 0}]
            out_html, info = await run_scripts(html, "https://example.com/", client, actions=actions, force=True)
            ctx = info.get("ctx")
            events = ctx.eval("window.pointerEvents.join(',')")
            self.assertIn("gotcapture:1", events)
            self.assertIn("move:handle", events)
            self.assertIn("lostcapture:1", events)

        import asyncio
        asyncio.run(run_test())

    # 7. Frame Message Targeting Origin and Identity
    def test_frame_message_exact_origin_and_identity_targeting(self):
        # Origin extractor tests
        self.assertEqual(extract_origin("https://example.com/some/path?q=1"), "https://example.com")
        self.assertEqual(extract_origin("https://example.com:8443/api"), "https://example.com:8443")
        self.assertEqual(extract_origin("http://example.com:80/"), "http://example.com")

        # Verify that https://example.com does NOT match prefix https://example.com.evil.org
        child_a = "https://example.com/child"
        attacker = "https://example.com.evil.org/child"

        self.assertNotEqual(extract_origin(child_a), extract_origin(attacker))
        self.assertEqual(extract_origin(child_a), "https://example.com")
        self.assertEqual(extract_origin(attacker), "https://example.com.evil.org")

    # 8. Server Tenant Isolation and Session Ownership Check
    def test_api_tenant_session_ownership_and_client_isolation(self):
        os.environ["WR_TENANT_TOKENS"] = '{"token_alpha": "tenant_alpha", "token_beta": "tenant_beta"}'
        try:
            app = create_app()
            with TestClient(app) as client:
                # 1. Tenant Alpha creates a session
                res_create = client.post(
                    "/v1/sessions",
                    headers={"Authorization": "Bearer token_alpha"},
                    json={},
                )
                self.assertEqual(res_create.status_code, 200)
                sid = res_create.json()["id"]

                # 2. Tenant Alpha can access its own session
                res_alpha_access = client.get(
                    f"/v1/sessions/{sid}/actions",
                    headers={"Authorization": "Bearer token_alpha"},
                )
                self.assertEqual(res_alpha_access.status_code, 200)

                # 3. Tenant Beta attempts to access Tenant Alpha's session -> MUST be 403 Forbidden!
                res_beta_access = client.get(
                    f"/v1/sessions/{sid}/actions",
                    headers={"Authorization": "Bearer token_beta"},
                )
                self.assertEqual(res_beta_access.status_code, 403)
                self.assertIn("access denied", res_beta_access.json()["detail"])

                # 4. Tenant Beta attempts to delete Tenant Alpha's session -> MUST be 403 Forbidden!
                res_beta_delete = client.delete(
                    f"/v1/sessions/{sid}",
                    headers={"Authorization": "Bearer token_beta"},
                )
                self.assertEqual(res_beta_delete.status_code, 403)

                # 5. Unauthorized caller without token -> 401
                res_unauth = client.get(
                    f"/v1/sessions/{sid}/actions",
                )
                self.assertEqual(res_unauth.status_code, 401)
        finally:
            os.environ.pop("WR_TENANT_TOKENS", None)


if __name__ == "__main__":
    unittest.main()
