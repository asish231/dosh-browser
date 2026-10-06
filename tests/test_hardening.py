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