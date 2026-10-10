import asyncio
import unittest

import httpx

from webresearch.engine.runtime import run_scripts
from webresearch.parse import parse
from webresearch.types import FetchResult


def _run(html, handler=None, url="https://example.com/", actions=None):
    transport = httpx.MockTransport(handler) if handler else None

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await run_scripts(html, url, client, force=True, actions=actions)

    return asyncio.run(go())


class ParseNoiseTests(unittest.TestCase):
    def test_keeps_ldjson_and_next_data_but_drops_src_scripts(self):
        html = (
            "<html><head>"
            '<script type="application/ld+json">{"@type":"Product"}</script>'
            '<script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>'
            '<script src="/app.js"></script>'
            "</head><body><p>Hello</p><svg><title>t</title></svg></body></html>"
        )
        doc = parse(
            FetchResult(
                url="https://x.com",
                final_url="https://x.com",
                status=200,
                content_type="text/html",
                kind="html",
                body=html,
            )
        )
        self.assertEqual(len(doc.tree.css('script[type="application/ld+json"]')), 1)
        self.assertEqual(len(doc.tree.css("script#__NEXT_DATA__")), 1)
        self.assertEqual(len(doc.tree.css("script[src]")), 0)
        self.assertEqual(len(doc.tree.css("svg")), 1)


class CanvasFingerprintTests(unittest.TestCase):
    def test_canvas_2d_and_webgl_fingerprint(self):
        page = (
            "<html><body><div id='root'></div><script>"
            "var out = [];"
            "var c = document.createElement('canvas');"
            "var ctx = c.getContext('2d');"
            "ctx.fillRect(0,0,10,10);"
            "out.push('2d=' + (typeof ctx.fillRect));"
            "out.push('url=' + c.toDataURL().slice(0, 22));"
            "var gl = c.getContext('webgl');"
            "out.push('v=' + gl.getParameter(0x9245));"
            "out.push('r=' + gl.getParameter(0x9246).slice(0, 20));"
            "out.push('ext=' + gl.getSupportedExtensions().length);"
            "document.getElementById('root').textContent = out.join('|');"
            "</script></body></html>"
        )
        html, info = _run(page)
        self.assertEqual(info["errors"], 0)
        self.assertIn("2d=function", html)
        self.assertIn("url=data:image/png;base64,", html)
        self.assertIn("v=Google Inc. (Apple)", html)
        self.assertIn("ANGLE", html)

    def test_canvas_dataurl_deterministic(self):
        page = (
            "<html><body><div id='root'></div><script>"
            "var a = document.createElement('canvas');"
            "var b = document.createElement('canvas');"
            "document.getElementById('root').textContent = String(a.toDataURL() === b.toDataURL());"
            "</script></body></html>"
        )
        html, _info = _run(page)
        self.assertIn("true", html)

    def test_webgl_renderer_reflects_real_device(self):
        """The UNMASKED_RENDERER string must come from the real host probe."""
        from webresearch import device
        from webresearch.engine.runtime import _device_block

        block = _device_block()
        self.assertIn("gpu_renderer", block)
        self.assertNotEqual(block["gpu_renderer"], "unknown")

        page = (
            "<html><body><div id='root'></div><script>"
            "var gl = document.createElement('canvas').getContext('webgl');"
            "document.getElementById('root').textContent = gl.getParameter(0x9246);"
            "</script></body></html>"
        )
        html, _info = _run(page)
        self.assertIn(block["gpu_renderer"], html)

        # On this Apple host the renderer must name the real chip.
        profile = device.probe()
        if profile.platform == "Darwin" and profile.cpu_brand != "unknown":
            self.assertIn(profile.cpu_brand, block["gpu_renderer"])


class CorsTests(unittest.TestCase):
    def test_cross_origin_blocked_and_same_origin_allowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/ok":
                return httpx.Response(200, json={"v": 1})
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

        page = (
            "<html><body><div id='root'></div><script>"
            "var out = [];"
            "function upd(){ document.getElementById('root').textContent = out.join('|'); }"
            "fetch('/api/ok').then(function(r){ return r.json(); }).then(function(d){ out.push('same=' + d.v); upd(); });"
            "fetch('https://other.evil.com/steal').then(function(r){ out.push('cross=' + r.status); upd(); })"
            ".catch(function(e){ out.push('cross=ERR'); upd(); });"
            "</script></body></html>"
        )
        html, info = _run(page, handler)
        self.assertIn("same=1", html)
        self.assertIn("cross=0", html)
        self.assertTrue(
            any(r.get("content_type") == "cors-blocked" for r in info["resource_requests"])
        )

    def test_cross_origin_with_acao_allowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.example.com":
                return httpx.Response(
                    200,
                    json={"v": 2},
                    headers={"access-control-allow-origin": "*"},
                )
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

        page = (
            "<html><body><div id='root'></div><script>"
            "var out = [];"
            "function upd(){ document.getElementById('root').textContent = out.join('|'); }"
            "fetch('https://api.example.com/data').then(function(r){ out.push('status=' + r.status); upd(); })"
            ".catch(function(e){ out.push('ERR'); upd(); });"
            "</script></body></html>"
        )
        html, info = _run(page, handler)
        self.assertIn("status=200", html)


class ScrollIntersectionTests(unittest.TestCase):
    def test_scroll_triggers_intersection_observer(self):
        page = (
            "<html><body>"
            "<div style='height:2000px'>tall</div>"
            "<div id='sentinel'></div>"
            "<div id='out'>not-loaded</div>"
            "<script>"
            "new IntersectionObserver(function(entries){"
            "entries.forEach(function(e){ if(e.isIntersecting){ document.getElementById('out').textContent = 'loaded'; } });"
            "}).observe(document.getElementById('sentinel'));"
            "</script></body></html>"
        )
        html, info = _run(page, actions=[{"kind": "scroll", "y": 5000}])
        self.assertEqual(info["errors"], 0)
        self.assertIn("loaded", html)

    def test_scroll_dispatches_scroll_event(self):
        page = (
            "<html><body><div id='out'>0</div><script>"
            "window.addEventListener('scroll', function(){"
            "document.getElementById('out').textContent = 'scrolled:' + window.scrollY;"
            "});"
            "</script></body></html>"
        )
        html, info = _run(page, actions=[{"kind": "scroll", "y": 1234}])
        self.assertEqual(info["errors"], 0)
        self.assertIn("scrolled:1234", html)


if __name__ == "__main__":
    unittest.main()

class SubresourceSecurityTests(unittest.TestCase):
    def test_sri_failure_blocks_script(self):
        import base64, hashlib
        good = "window.__sri_ok = 1;"
        digest = base64.b64encode(hashlib.sha256(good.encode()).digest()).decode()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/bad.js":
                return httpx.Response(200, text="window.__sri_bad = 1;")
            return httpx.Response(200, text=good)

        html = (
            '<html><head>'
            f'<script src="/good.js" integrity="sha256-{digest}"></script>'
            f'<script src="/bad.js" integrity="sha256-{digest}"></script>'
            "</head><body></body></html>"
        )
        _, info = _run(html, handler)
        self.assertEqual(info.get("blocked_sri_failures"), 1)

    def test_mixed_content_blocked(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="window.__mc = 1;")

        html = '<html><head><script src="http://example.com/a.js"></script></head><body></body></html>'
        _, info = _run(html, handler)
        self.assertEqual(info.get("blocked_mixed_content"), 1)

    def test_inline_onclick_handler_fires(self):
        handler = lambda request: httpx.Response(200, text="")
        html = '<html><body><button id="b" onclick="document.body.setAttribute(\'data-x\',\'yes\')">go</button></body></html>'
        actions = [{"kind": "activate", "selector": "#b"}]
        out, info = _run(html, handler, actions=actions)
        self.assertIn('data-x="yes"', out)

    def test_csp_blocks_inline_without_unsafe_inline(self):
        handler = lambda request: httpx.Response(200, text="")
        html = '<html><head><script>window.__csp=1;</script></head><body></body></html>'
        headers = {"content-security-policy": "script-src 'self'"}
        transport = httpx.MockTransport(handler)

        async def go():
            async with httpx.AsyncClient(transport=transport) as client:
                return await run_scripts(html, "https://example.com/", client, force=True, headers=headers)

        _, info = asyncio.run(go())
        self.assertEqual(info.get("blocked_csp"), 1)

    def test_agent_click_is_trusted_programmatic_is_not(self):
        handler = lambda request: httpx.Response(200, text="")
        html = '<html><body><button id="b" onclick="document.body.setAttribute(\'data-t\', String(event.isTrusted))">go</button></body></html>'
        actions = [{"kind": "activate", "selector": "#b"}]
        out, info = _run(html, handler, actions=actions)
        self.assertIn('data-t="true"', out)

    def test_cookie_policy_rejects_insecure_prefixes(self):
        from webresearch.engine.security import cookie_violates_policy
        self.assertTrue(cookie_violates_policy("__Secure-a=1; Path=/"))
        self.assertTrue(cookie_violates_policy("__Host-a=1; Path=/"))
        self.assertTrue(cookie_violates_policy("a=1; SameSite=None"))
        self.assertFalse(cookie_violates_policy("__Secure-a=1; Secure; Path=/"))
        self.assertFalse(cookie_violates_policy("__Host-a=1; Secure; Path=/"))
        self.assertFalse(cookie_violates_policy("a=1; SameSite=None; Secure"))

    def test_csp_style_src_blocks_inline_css(self):
        handler = lambda request: httpx.Response(200, text="")
        html = '<html><head><style>body { color: red }</style></head><body></body></html>'
        headers = {"content-security-policy": "style-src 'self'"}
        transport = httpx.MockTransport(handler)

        async def go():
            async with httpx.AsyncClient(transport=transport) as client:
                return await run_scripts(html, "https://example.com/", client, force=True, headers=headers)

        out, info = asyncio.run(go())
        self.assertEqual(info.get("blocked_csp_style"), 1)
        self.assertNotIn("color: red", out)

    def test_blocked_host_short_circuits_fetch(self):
        import os
        os.environ["WEBRESEARCH_BLOCKED_HOSTS"] = "evil.example"
        try:
            from webresearch.fetch import fetch

            async def go():
                async with httpx.AsyncClient() as client:
                    return await fetch("https://evil.example/page", client=client)

            result = asyncio.run(go())
            self.assertEqual(result.status, 403)
            self.assertEqual(result.headers.get("x-webresearch-blocked"), "1")
        finally:
            os.environ.pop("WEBRESEARCH_BLOCKED_HOSTS", None)

    def test_http_cache_fresh_hit_revalidate_and_vary(self):
        import time
        from webresearch.cache import CACHE
        from webresearch.fetch import fetch
        CACHE.clear()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200, text="v1",
                    headers={"content-type": "text/html", "cache-control": "max-age=60", "etag": '"a1"', "vary": "accept-language"},
                )
            inm = request.headers.get("if-none-match", "")
            if inm == '"a1"' and "fr" not in request.url.path:
                return httpx.Response(304, headers={"etag": '"a1"', "cache-control": "max-age=60"})
            return httpx.Response(
                200, text="v1",
                headers={"content-type": "text/html", "cache-control": "max-age=60", "etag": '"a1"', "vary": "accept-language"},
            )

        async def go(url, headers=None):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch(url, client=client, headers=headers)

        r1 = asyncio.run(go("https://example.com/p"))
        self.assertEqual(r1.body, "v1")
        self.assertNotIn("x-webresearch-cache", r1.headers)
        r2 = asyncio.run(go("https://example.com/p"))
        self.assertEqual(r2.headers.get("x-webresearch-cache"), "hit")
        self.assertEqual(calls["n"], 1)
        # Vary mismatch forces a separate entry, not a false hit.
        r3 = asyncio.run(go("https://example.com/p", headers={"accept-language": "fr"}))
        self.assertNotIn("x-webresearch-cache", r3.headers)

        # Stale entry with validator revalidates via 304.
        for entries in CACHE._entries.values():
            for e in entries:
                e.stored_at = time.time() - 3600
                e.resp_headers["cache-control"] = "max-age=1"
        before = calls["n"]
        r4 = asyncio.run(go("https://example.com/p"))
        self.assertEqual(r4.headers.get("x-webresearch-cache"), "revalidated")
        self.assertEqual(r4.body, "v1")
        self.assertGreater(calls["n"], before)
        CACHE.clear()

    def test_http_cache_no_store_never_served(self):
        from webresearch.cache import CACHE
        from webresearch.fetch import fetch
        CACHE.clear()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, text="secret", headers={"cache-control": "no-store"})

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch("https://example.com/s", client=client)

        asyncio.run(go())
        asyncio.run(go())
        self.assertEqual(calls["n"], 2)
        CACHE.clear()

    def test_frame_embedding_xfo_and_frame_ancestors(self):
        import asyncio
        from webresearch.engine.frames import load_frames
        from webresearch.cache import CACHE
        CACHE.clear()

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/deny":
                return httpx.Response(200, text="<html><body>no</body></html>",
                                      headers={"content-type": "text/html", "x-frame-options": "DENY"})
            if path == "/same":
                return httpx.Response(200, text="<html><body>ok</body></html>",
                                      headers={"content-type": "text/html", "x-frame-options": "SAMEORIGIN"})
            if path == "/fa-none":
                return httpx.Response(200, text="<html><body>no</body></html>",
                                      headers={"content-type": "text/html",
                                               "content-security-policy": "script-src 'none'; frame-ancestors 'none'"})
            if path == "/fa-self":
                return httpx.Response(200, text="<html><body>ok</body></html>",
                                      headers={"content-type": "text/html", "content-security-policy": "frame-ancestors 'self'"})
            return httpx.Response(404, text="x")

        async def go(src):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                html = f'<html><body><iframe src="{src}"></iframe></body></html>'
                return await load_frames(html, "https://parent.example/", client, js="never")

        docs = asyncio.run(go("https://child.example/deny"))
        self.assertIn("DENY", docs[0].error)
        self.assertEqual(docs[0].html, "")
        docs = asyncio.run(go("https://child.example/same"))
        self.assertIn("SAMEORIGIN", docs[0].error)
        docs = asyncio.run(go("https://child.example/fa-none"))
        self.assertIn("frame-ancestors", docs[0].error)
        docs = asyncio.run(go("https://parent.example/fa-self"))
        self.assertEqual(docs[0].error, "")
        CACHE.clear()

    def test_throttle_hash_prefix_requires_full_hash_confirmation(self):
        import hashlib
        import os
        from webresearch.engine.throttles import SafeBrowsingThrottle
        from webresearch.fetch import fetch
        from webresearch.cache import CACHE
        CACHE.clear()
        expr = "evil.example/malware"
        prefix = hashlib.sha256(expr.encode()).digest()[:4].hex()
        full = hashlib.sha256(expr.encode()).digest().hex()
        os.environ["WEBRESEARCH_SB_PREFIXES"] = prefix
        try:
            # Prefix hit but no full hash: must NOT block.
            t = SafeBrowsingThrottle()
            v = t.check("https://evil.example/malware")
            self.assertTrue(v.proceed)
            self.assertIn("without full-hash confirmation", v.reason)

            async def go():
                transport = httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))
                async with httpx.AsyncClient(transport=transport) as client:
                    return await fetch("https://evil.example/malware", client=client)
            r = asyncio.run(go())
            self.assertEqual(r.status, 200)

            # With full hash confirmed: must block before network.
            os.environ["WEBRESEARCH_SB_FULLHASHES"] = full
            r = asyncio.run(go())
            self.assertEqual(r.status, 403)
            self.assertEqual(r.headers.get("x-webresearch-blocked"), "throttle")
        finally:
            os.environ.pop("WEBRESEARCH_SB_PREFIXES", None)
            os.environ.pop("WEBRESEARCH_SB_FULLHASHES", None)
            CACHE.clear()

    def test_tracker_blocked_with_surrogate_and_first_party_allowed(self):
        from webresearch.engine.trackers import TrackerGraph, registrable_domain
        self.assertEqual(registrable_domain("a.example.co.uk"), "example.co.uk")
        self.assertEqual(registrable_domain("tracker.example.com"), "example.com")
        g = TrackerGraph()
        d = g.evaluate("https://www.google-analytics.com/g/collect", "https://shop.example/")
        self.assertEqual(d.status, "BLOCKED")
        self.assertEqual(d.entity, "Google")
        self.assertTrue(d.surrogate)
        d = g.evaluate("https://cdn.shop.example/app.js", "https://shop.example/")
        self.assertEqual(d.status, "ALLOWED")
        d = g.evaluate("https://analytics.shop.example/x.js", "https://shop.example/")
        self.assertEqual(d.status, "ALLOWED")
        d = g.evaluate("https://unknown-tracker-xyz.example/x.js", "https://shop.example/")
        self.assertEqual(d.status, "ALLOWED")

    def test_tracker_script_uses_surrogate_without_network(self):
        handler_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            handler_calls["n"] += 1
            return httpx.Response(200, text="window.__real_tracker=1;")

        html = '<html><head><script src="https://www.google-analytics.com/analytics.js"></script></head><body></body></html>'
        _, info = _run(html, handler)
        self.assertEqual(handler_calls["n"], 0)
        blocked = info.get("blocked_trackers") or []
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["entity"], "Google")
        self.assertTrue(blocked[0]["surrogate"])

    def test_tls_interstitial_deny_and_classify(self):
        import os
        from webresearch.cache import CACHE
        from webresearch.engine import tls_policy
        from webresearch.engine.tls_policy import classify_tls_error, handle_tls_error
        from webresearch.fetch import fetch
        CACHE.clear()
        tls_policy.DECISIONS.clear()

        self.assertEqual(classify_tls_error(Exception("[SSL: CERTIFICATE_VERIFY_FAILED] expired certificate")), "expired")
        self.assertEqual(classify_tls_error(Exception("SSL: hostname 'x' doesn't match certificate")), "wrong-host")
        self.assertIsNone(classify_tls_error(Exception("connection refused")))

        i = handle_tls_error("expired.example", Exception("ssl certificate expired"))
        self.assertEqual(i.decision, "deny")
        self.assertTrue(i.overridable)
        i = handle_tls_error("other.example", Exception("SSL certificate hostname mismatch"))
        self.assertEqual(i.decision, "deny")
        self.assertFalse(i.overridable)

        def handler(request: httpx.Request) -> httpx.Response:
            raise Exception("SSL: CERTIFICATE_VERIFY_FAILED certificate expired")

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch("https://expired.example/", client=client)

        r = asyncio.run(go())
        self.assertEqual(r.status, 525)
        self.assertEqual(r.headers.get("x-webresearch-tls"), "denied:expired")
        self.assertTrue(any(d["host"] == "expired.example" and d["decision"] == "deny" for d in tls_policy.DECISIONS))

        import sys as _sys
        fetch_mod = _sys.modules["webresearch.fetch"]
        os.environ["WEBRESEARCH_TLS_PROCEED_HOSTS"] = "expired.example"
        real_plain = fetch_mod._plain_fetch

        async def fake_plain(url, *, client, method, data, json_body, headers, max_bytes):
            return httpx.Response(200, text="insecure-ok") and __import__(
                "webresearch.fetch", fromlist=["_build_result"]
            )._build_result(
                url=url, final_url=url, status=200, content_type="text/plain",
                headers={"content-type": "text/plain"}, raw=b"insecure-ok", truncated=False,
            )

        fetch_mod._plain_fetch = fake_plain
        try:
            async def go2():
                transport = httpx.MockTransport(handler)
                async with httpx.AsyncClient(transport=transport) as client:
                    return await fetch("https://expired.example/", client=client)

            r = asyncio.run(go2())
            self.assertEqual(r.status, 200)
            self.assertIn("proceeded-unverified", r.headers.get("x-webresearch-tls", ""))
            self.assertTrue(any(d["host"] == "expired.example" and d["decision"] == "proceed" for d in tls_policy.DECISIONS))
        finally:
            fetch_mod._plain_fetch = real_plain
            os.environ.pop("WEBRESEARCH_TLS_PROCEED_HOSTS", None)
            CACHE.clear()

    def test_service_worker_registration_primes_precache(self):
        import asyncio
        from webresearch.cache import CACHE
        CACHE.clear()
        sw_code = (
            "self.addEventListener('fetch', e => {});"
            "workbox.precaching.precacheAndRoute(['/app.js', '/style.css']);"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/sw.js":
                return httpx.Response(200, text=sw_code, headers={"content-type": "application/javascript"})
            if path in ("/app.js", "/style.css"):
                return httpx.Response(200, text="/* asset */", headers={"content-type": "text/plain", "cache-control": "max-age=3600"})
            return httpx.Response(404, text="x")

        html = '<html><head><script>navigator.serviceWorker.register("/sw.js")</script></head><body></body></html>'
        _, info = _run(html, handler, url="https://example.com/")
        sw = info.get("service_workers") or []
        self.assertEqual(len(sw), 1)
        self.assertEqual(sw[0]["url"], "https://example.com/sw.js")
        self.assertTrue(sw[0]["has_fetch_handler"])
        self.assertEqual(sw[0]["primed"], 2)
        self.assertIsNotNone(CACHE.lookup("GET", "https://example.com/app.js", {}))
        CACHE.clear()

    def test_download_hash_verified_and_mismatch_rejected(self):
        import asyncio
        import base64
        import hashlib
        from webresearch.download import download
        payload = b"binary-payload-123"
        digest_b64 = base64.b64encode(hashlib.sha256(payload).digest()).decode()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload, headers={"content-type": "application/octet-stream"})

        async def go(expected):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await download("https://example.com/f.bin", client, expected=expected)

        rec = asyncio.run(go(f"sha256-{digest_b64}"))
        self.assertTrue(rec.verified)
        self.assertEqual(rec.bytes, len(payload))
        self.assertEqual(rec.sha256, hashlib.sha256(payload).hexdigest())
        self.assertFalse(rec.scanned)
        import os
        if rec.path:
            os.unlink(rec.path)

        rec = asyncio.run(go("sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="))
        self.assertFalse(rec.verified)
        self.assertEqual(rec.path, "")
        self.assertIn("mismatch", rec.error)

    def test_layout_boxes_derive_from_markup_and_styles(self):
        import json
        handler = lambda request: httpx.Response(200, text="")
        html = (
            '<html><head><style>#a { width: 400px; height: 50px; margin: 10px; }</style></head>'
            '<body><div id="a">x</div><div id="b">y</div>'
            '<div id="hid" style="display:none">z</div>'
            '<script>'
            'var A = document.getElementById("a").getBoundingClientRect();'
            'var B = document.getElementById("b").getBoundingClientRect();'
            'var H = document.getElementById("hid").getBoundingClientRect();'
            'document.body.setAttribute("data-ra", JSON.stringify(A));'
            'document.body.setAttribute("data-rb", JSON.stringify(B));'
            'document.body.setAttribute("data-rh", JSON.stringify(H));'
            'document.body.setAttribute("data-info", JSON.stringify(document.__layoutInfo || {}));'
            '</script></body></html>'
        )
        out, info = _run(html, handler)
        import re
        ra = json.loads(re.search(r'data-ra="([^"]*)"', out).group(1).replace("&quot;", '"'))
        rb = json.loads(re.search(r'data-rb="([^"]*)"', out).group(1).replace("&quot;", '"'))
        rh = json.loads(re.search(r'data-rh="([^"]*)"', out).group(1).replace("&quot;", '"'))
        # Styled box: 400 wide + 10px left margin (+8 body shift).
        self.assertEqual(ra["width"], 400)
        self.assertEqual(ra["height"], 50)
        self.assertEqual(ra["x"], 18)
        # Second block stacks below the first: margin collapsing keeps order.
        self.assertGreater(rb["y"], ra["y"])
        self.assertGreater(rb["y"], ra["bottom"] - 1)
        # display:none produces a zero box.
        self.assertEqual(rh["width"], 0)
        self.assertEqual(rh["height"], 0)

    def test_layout_deterministic_not_hashed(self):
        handler = lambda request: httpx.Response(200, text="")
        html = (
            '<html><body><div id="p">alpha</div><div id="q">beta</div>'
            '<script>'
            'var P = document.getElementById("p").getBoundingClientRect();'
            'var Q = document.getElementById("q").getBoundingClientRect();'
            'document.body.setAttribute("data-ok", (Q.y >= P.bottom && P.width === Q.width) ? "yes" : "no");'
            '</script></body></html>'
        )
        out, _ = _run(html, handler)
        self.assertIn('data-ok="yes"', out)

    def test_onion_address_checksum_validated_and_never_clearnet(self):
        import asyncio
        import base64
        import hashlib
        from webresearch.engine.onion import parse_v3_onion
        from webresearch.cache import CACHE
        from webresearch.fetch import fetch
        CACHE.clear()
        # Build a well-formed v3 address from a fixed pubkey.
        pubkey = hashlib.sha256(b"test-key").digest()
        checksum = hashlib.sha3_256(b".onion checksum" + pubkey + b"\x03").digest()[:2]
        good = base64.b32encode(pubkey + checksum + b"\x03").decode().lower()
        self.assertEqual(parse_v3_onion(good + ".onion"), pubkey)
        self.assertIsNone(parse_v3_onion("short.onion"))
        bad = good[:-1] + ("a" if good[-1] != "a" else "b")
        self.assertIsNone(parse_v3_onion(bad + ".onion"))

        seen = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["n"] += 1
            return httpx.Response(200, text="leaked")

        async def go(host):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await fetch(f"https://{host}/", client=client)

        r = asyncio.run(go("invalid123.onion"))
        self.assertEqual(r.status, 403)
        self.assertIn("invalid .onion address", r.body)
        r = asyncio.run(go(good + ".onion"))
        self.assertEqual(r.status, 403)
        self.assertIn("requires a Tor transport", r.body)
        self.assertEqual(seen["n"], 0)
        CACHE.clear()

    def test_remote_tracker_list_requires_pinned_hash(self):
        import hashlib
        import json
        import os
        from webresearch.engine.trackers import TrackerGraph
        body = json.dumps({"EvilCorp": ["evil-tracker.example"]}).encode()
        good_hex = hashlib.sha256(body).hexdigest()

        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/list.json"
        try:
            os.environ["WEBRESEARCH_TRACKER_LIST_URL"] = url
            os.environ["WEBRESEARCH_TRACKER_LIST_SHA256"] = "0" * 64
            g = TrackerGraph()
            self.assertIn("mismatch", g.remote_error)
            self.assertEqual(g.evaluate("https://evil-tracker.example/x.js", "https://shop.example/").status, "ALLOWED")
            os.environ["WEBRESEARCH_TRACKER_LIST_SHA256"] = good_hex
            g = TrackerGraph()
            self.assertEqual(g.remote_error, "")
            d = g.evaluate("https://evil-tracker.example/x.js", "https://shop.example/")
            self.assertEqual(d.status, "BLOCKED")
            self.assertEqual(d.entity, "EvilCorp")
        finally:
            server.shutdown()
            os.environ.pop("WEBRESEARCH_TRACKER_LIST_URL", None)
            os.environ.pop("WEBRESEARCH_TRACKER_LIST_SHA256", None)

    def test_external_stylesheet_gated_and_cascaded(self):
        import asyncio
        from webresearch.cache import CACHE
        CACHE.clear()

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/a.css":
                return httpx.Response(200, text="#box { width: 250px; height: 30px; }",
                                      headers={"content-type": "text/css"})
            if path == "/tracker.css":
                return httpx.Response(200, text="#box { width: 9999px; }",
                                      headers={"content-type": "text/css"})
            return httpx.Response(404, text="x")

        html = (
            '<html><head>'
            '<link rel="stylesheet" href="/a.css">'
            '<link rel="stylesheet" href="https://www.google-analytics.com/tracker.css">'
            '<link rel="stylesheet" href="/print.css" media="print">'
            '</head><body><div id="box">t</div>'
            '<script>'
            'var R = document.getElementById("box").getBoundingClientRect();'
            'document.body.setAttribute("data-r", JSON.stringify(R));'
            '</script></body></html>'
        )
        out, info = _run(html, handler, url="https://example.com/")
        import json as _json, re as _re
        rect = _json.loads(_re.search(r'data-r="([^"]*)"', out).group(1).replace("&quot;", '"'))
        self.assertEqual(rect["width"], 250)
        self.assertEqual(rect["height"], 30)
        sheets = info.get("stylesheets") or {}
        self.assertEqual(sheets["applied"], ["https://example.com/a.css"])
        reasons = {b["href"]: b["reason"] for b in sheets["blocked"]}
        self.assertTrue(reasons["https://www.google-analytics.com/tracker.css"].startswith("tracker:"))
        self.assertTrue(reasons["https://example.com/print.css"].startswith("media-mismatch"))
        CACHE.clear()

    def test_hsts_observe_upgrade_and_expiry(self):
        import time
        from webresearch.cache import CACHE
        from webresearch.engine.hsts import HstsState, parse_sts
        from webresearch.fetch import fetch
        CACHE.clear()
        self.assertEqual(parse_sts("max-age=31536000; includeSubDomains"), (31536000, True))
        self.assertIsNone(parse_sts("max-age=abc"))
        self.assertIsNone(parse_sts("bogus-directive"))

        state = HstsState()
        self.assertIsNotNone(state.upgrade_url("http://google.com/"))
        self.assertIsNone(state.upgrade_url("https://google.com/"))
        self.assertIsNone(state.upgrade_url("http://unlisted-xyz.example/"))
        self.assertTrue(state.observe("shop.example", "max-age=100; includeSubDomains", secure=True))
        self.assertEqual(state.upgrade_url("http://deep.shop.example/x"), "https://deep.shop.example/x")
        self.assertFalse(state.observe("evil.example", "max-age=100", secure=False))
        self.assertIsNone(state.upgrade_url("http://evil.example/"))
        state.entries["old.example"] = state.entries["shop.example"].__class__(
            "old.example", False, time.time() - 1)
        self.assertIsNone(state.upgrade_url("http://old.example/"))

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.scheme == "https"
            return httpx.Response(200, text="secure",
                                  headers={"strict-transport-security": "max-age=60"})

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                first = await fetch("https://newshop.example/", client=client)
                second = await fetch("http://newshop.example/", client=client)
                return first, second

        from webresearch.engine import hsts as hsts_mod
        saved = dict(hsts_mod.STATE.entries)
        try:
            first, second = asyncio.run(go())
            self.assertEqual(first.status, 200)
            self.assertEqual(second.body, "secure")
            self.assertIn("hsts:http://newshop.example/", second.headers.get("x-webresearch-upgraded", ""))
        finally:
            hsts_mod.STATE.entries = saved
            CACHE.clear()

    def test_corp_and_coep_block_cross_origin_subresources(self):
        import asyncio
        from webresearch.cache import CACHE
        CACHE.clear()

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/app.js":
                return httpx.Response(200, text="window.__x=1;",
                                      headers={"cross-origin-resource-policy": "same-origin"})
            if path == "/open.js":
                return httpx.Response(200, text="window.__y=1;")
            return httpx.Response(404, text="x")

        async def go(page_headers):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                html = ('<html><head><script src="https://cdn.example/app.js"></script>'
                        '<script src="https://cdn.example/open.js"></script></head><body></body></html>')
                return await run_scripts(html, "https://shop.example/", client, force=True, headers=page_headers)

        _, info = asyncio.run(go({}))
        blocked = {b["url"]: b["reason"] for b in info.get("blocked_corp_coep") or []}
        self.assertIn("https://cdn.example/app.js", blocked)
        self.assertIn("same-origin", blocked["https://cdn.example/app.js"])
        self.assertNotIn("https://cdn.example/open.js", blocked)

        _, info = asyncio.run(go({"cross-origin-embedder-policy": "require-corp"}))
        blocked = {b["url"]: b["reason"] for b in info.get("blocked_corp_coep") or []}
        self.assertIn("https://cdn.example/open.js", blocked)
        self.assertIn("require-corp", blocked["https://cdn.example/open.js"])
        CACHE.clear()

    def test_trusted_types_enforced_and_policies_work(self):
        import asyncio
        handler = lambda request: httpx.Response(200, text="")
        csp = "require-trusted-types-for 'script'; trusted-types myPolicy"

        async def go(code):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                html = f"<html><body><div></div><script>{code}</script></body></html>"
                return await run_scripts(html, "https://example.com/", client, force=True,
                                         headers={"content-security-policy": csp})

        # Plain string into innerHTML is refused.
        _, info = asyncio.run(go(
            "try { document.body.innerHTML = '<p>x</p>'; } catch (e) { document.body.setAttribute('data-e', e.name); }"
        ))
        self.assertTrue(info.get("trusted_types_violations"))
        # A policy-created TrustedHTML passes.
        out, info = asyncio.run(go(
            "var p = trustedTypes.createPolicy('myPolicy', { createHTML: function(s) { return s; } });"
            "document.body.innerHTML = p.createHTML('<p>y</p>');"
        ))
        self.assertIn("<p>y</p>", out)
        # Disallowed policy name is refused.
        _, info = asyncio.run(go(
            "try { trustedTypes.createPolicy('evil', {}); } catch (e) { document.body.setAttribute('data-p', e.name); }"
            "document.body.setAttribute('data-done', trustedTypes.isHTML(trustedTypes.emptyHTML) ? 'yes' : 'no');"
        ))
        self.assertNotIn("trusted_types_violations", info)

        # Without the CSP, strings flow (no enforcement).
        async def go_plain(code):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                html = f"<html><body><div></div><script>{code}</script></body></html>"
                return await run_scripts(html, "https://example.com/", client, force=True)
        out, _ = asyncio.run(go_plain("document.body.innerHTML = '<p>z</p>';"))
        self.assertIn("<p>z</p>", out)

    def test_sb_list_update_add_remove_with_checksum(self):
        import hashlib
        from webresearch.engine.sbupdate import ListStore, update_checksum
        from webresearch.engine.throttles import SafeBrowsingThrottle
        store = ListStore()
        p1 = hashlib.sha256(b"evil.example/malware").digest()[:4].hex()
        p2 = hashlib.sha256(b"bad.example/phish").digest()[:4].hex()
        ok, msg = store.apply_update("malware", {"version": 1, "add": [p1, p2], "remove": [],
                                                 "checksum": update_checksum([p1, p2])})
        self.assertTrue(ok, msg)
        t = SafeBrowsingThrottle(prefixes=set())
        # store-backed union: temporarily point the global STORE at ours
        from webresearch.engine import sbupdate as sbmod
        saved = dict(sbmod.STORE.lists)
        sbmod.STORE.lists = store.lists
        try:
            v = SafeBrowsingThrottle().check("https://evil.example/malware")
            self.assertTrue(v.proceed)  # prefix hit, no full hash yet
            ok, msg = store.apply_update("malware", {"version": 0, "add": [], "remove": [],
                                                     "checksum": update_checksum([])})
            self.assertFalse(ok)
            self.assertIn("stale", msg)
            ok, msg = store.apply_update("malware", {"version": 2, "add": [p1], "remove": [],
                                                     "checksum": "0" * 64})
            self.assertFalse(ok)
            self.assertIn("checksum mismatch", msg)
            # state unchanged after rejected update
            self.assertEqual(store.get("malware").version, 1)
            ok, msg = store.apply_update("malware", {"version": 2, "add": [], "remove": [p1],
                                                     "checksum": update_checksum([])})
            self.assertTrue(ok, msg)
            self.assertNotIn(bytes.fromhex(p1), store.get("malware").prefixes)
            self.assertIn(bytes.fromhex(p2), store.get("malware").prefixes)
        finally:
            sbmod.STORE.lists = saved

    def test_sw_cache_first_intercepts_and_background_fetch(self):
        import asyncio
        from webresearch.cache import CACHE
        from webresearch.download import background_fetch
        CACHE.clear()
        sw_code = (
            "workbox.routing.registerRoute(new RegExp('/api/'), new workbox.strategies.CacheFirst());"
            "self.addEventListener('fetch', e => {});"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/sw.js":
                return httpx.Response(200, text=sw_code, headers={"content-type": "application/javascript"})
            if path == "/api/data":
                return httpx.Response(200, text='{"v": 1}',
                                      headers={"content-type": "application/json", "cache-control": "max-age=3600"})
            if path.startswith("/dl"):
                return httpx.Response(200, content=b"x" * 100)
            return httpx.Response(404, text="x")

        html = (
            '<html><head><script>navigator.serviceWorker.register("/sw.js")</script>'
            '<script>fetch("/api/data").then(r => r.text()).then(t => document.body.setAttribute("data-t", t));</script>'
            '</head><body></body></html>'
        )
        out, info = _run(html, handler, url="https://example.com/")
        self.assertIn("v", out)
        self.assertIn("data-t=", out)
        reqs = info.get("resource_requests") or []
        self.assertTrue(any(r.get("sw", "").startswith("CacheFirst") or r.get("sw", "").startswith("NetworkFirst") for r in reqs))

        async def go_bg():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                seen = []
                rec = await background_fetch(
                    ["https://example.com/dl1", "https://example.com/dl2"],
                    client, tag="t1", progress=lambda d, t: seen.append((d, t)),
                )
                return rec, seen

        rec, seen = asyncio.run(go_bg())
        self.assertTrue(rec.done)
        self.assertEqual(len(rec.downloads), 2)
        self.assertEqual(rec.downloaded, 200)
        self.assertEqual(seen[-1], (2, 2))
        import os
        for d in rec.downloads:
            if d.path:
                os.unlink(d.path)
        CACHE.clear()

    def test_ct_sct_signature_verifies_with_pinned_log(self):
        import base64
        import hashlib
        import json
        import os
        import struct
        from webresearch.engine import ct as ct_mod
        # Test-only P-256 signer (same curve math as the verifier).
        d = int.from_bytes(hashlib.sha256(b"ct-test-key").digest(), "big") % ct_mod._N
        if d == 0:
            d = 1
        pt = ct_mod._point_mul(d, (ct_mod._GX, ct_mod._GY))
        log_id = hashlib.sha256(b"log-key" + pt[0].to_bytes(32, "big")).digest()
        cert_der = b"\x30\x03\x02\x01\x05"  # stand-in TBS bytes for the test
        ts = 1_700_000_000_000
        data = ct_mod.sct_signed_data(log_id, ts, b"\x00" * 32, cert_der)
        digest = hashlib.sha256(data).digest()
        e = int.from_bytes(digest, "big") % ct_mod._N
        import secrets
        while True:
            k = secrets.randbelow(ct_mod._N - 1) + 1
            kp = ct_mod._point_mul(k, (ct_mod._GX, ct_mod._GY))
            r = kp[0] % ct_mod._N
            if r == 0:
                continue
            s = (pow(k, ct_mod._N - 2, ct_mod._N) * (e + r * d)) % ct_mod._N
            if s != 0:
                break
        self.assertTrue(ct_mod.ecdsa_p256_verify(pt[0], pt[1], digest, r, s))
        self.assertFalse(ct_mod.ecdsa_p256_verify(pt[0], pt[1], digest, r, (s + 1) % ct_mod._N))

        # DER-encode signature + assemble SCT list + verify end to end.
        def der_int(x):
            b = x.to_bytes((x.bit_length() + 7) // 8 or 1, "big")
            if b[0] & 0x80:
                b = b"\x00" + b
            return b"\x02" + bytes([len(b)]) + b
        sig_der = b"\x30" + bytes([len(der_int(r) + der_int(s))]) + der_int(r) + der_int(s)
        sct_body = (b"\x00" + log_id + struct.pack(">Q", ts) + b"\x00\x00"
                    + b"\x04\x03" + struct.pack(">H", len(sig_der)) + sig_der)
        sct_list = struct.pack(">H", 2 + len(sct_body)) + struct.pack(">H", len(sct_body)) + sct_body
        parsed = ct_mod.parse_sct_list(sct_list)
        self.assertEqual(len(parsed), 1)
        # SPKI DER for the log key.
        point = b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")
        spki = (b"\x30\x59\x30\x13\x06\x07\x2a\x86\x48\xce\x3d\x02\x01\x06\x08\x2a\x86\x48\xce\x3d\x03\x01\x07"
                + b"\x03\x42\x00" + point)
        pem = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(spki).decode() + "\n-----END PUBLIC KEY-----\n"
        os.environ["WEBRESEARCH_CT_LOGS"] = json.dumps({base64.b64encode(log_id).decode(): pem})
        try:
            key = ct_mod.pinned_logs()[log_id]
            self.assertTrue(ct_mod.verify_sct(parsed[0], key, b"\x00" * 32, cert_der))
            bad = dict(parsed[0])
            bad["timestamp"] = ts + 1
            self.assertFalse(ct_mod.verify_sct(bad, key, b"\x00" * 32, cert_der))
        finally:
            os.environ.pop("WEBRESEARCH_CT_LOGS", None)

    def test_sxg_validated_and_bundle_listed(self):
        import asyncio
        import base64
        import hashlib
        import json as _json
        import struct
        import time
        from webresearch.cache import CACHE
        CACHE.clear()
        inner = b"sxg1-b3\x00" + b"PADDING-INNER-CONTENT-0123456789"
        integrity = "mi-sha256=" + base64.b64encode(hashlib.sha256(b"\x00" + inner).digest()).decode()
        cert_der = b"\x30\x03\x02\x01\x07"
        cert_sha = base64.b64encode(hashlib.sha256(cert_der).digest()).decode()
        now = int(time.time())
        sig = (f'sig1=:MEUCIQDfake:; validfor="https://x.example/"; integrity="{integrity}"; '
               f'cert-url="https://x.example/cert.cbor"; cert-sha256=":{cert_sha}:"; '
               f'validity-url="https://x.example/page"; date={now - 10}; expires={now + 3600}')
        # fix the sig param shape: sig="base64" item
        sig = sig.replace("sig1=:MEUCIQDfake:", 'sig1=:TUVVQ0lRRGZha2U=:')
        # validity-url check uses validity-url; validfor is ignored
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/page.sxg":
                return httpx.Response(200, content=inner,
                                      headers={"content-type": "application/signed-exchange;v=b3",
                                               "signature": sig})
            if path == "/cert.cbor":
                return httpx.Response(200, content=cert_der)
            return httpx.Response(404, text="x")

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                from webresearch.fetch import fetch
                return await fetch("https://x.example/page.sxg", client=client)

        r = asyncio.run(go())
        report = _json.loads(r.headers.get("x-webresearch-sxg", "{}"))
        self.assertTrue(report.get("valid"), report)
        self.assertTrue(any("cert-sha256 match" in c for c in report["checks"]))
        self.assertTrue(any("mi-sha256 match" in c for c in report["checks"]))

        # WebBundle: magic + version + sections listed, never executed.
        import struct as _st
        def cbor_bstr(b):
            return bytes([0x40 + len(b)]) + b
        def cbor_text(t):
            e = t.encode()
            return bytes([0x60 + len(e)]) + e
        def cbor_arr(items):
            return bytes([0x80 + len(items)]) + b"".join(items)
        version = cbor_bstr(b"b2\x00")
        sections = cbor_arr([cbor_arr([cbor_text("index"), cbor_bstr(b"\x01\x02")]),
                             cbor_arr([cbor_text("responses"), cbor_bstr(b"\x03")])])
        bundle = (b"\xf0\x9f\x8c\x90\xf0\x9f\x93\xa6" + version
                  + cbor_arr([]) + sections + cbor_arr([]))
        from webresearch.engine.sxg import inspect_webbundle
        rep = inspect_webbundle(bundle)
        self.assertEqual(rep["version"], b"b2\x00")
        self.assertIn("index", rep["sections"])
        self.assertFalse(rep["executed"])
        CACHE.clear()
