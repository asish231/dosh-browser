"""Platform capability tests.

Verifies native implementations for:
1. Cross-frame postMessage bi-directional handshake and origin security boundary
2. iframe.contentDocument and contentWindow.document DOM access
3. Real CSS Grid layout and offsetParent / offsetLeft / offsetTop geometry
4. IndexedDB IDBFactory.databases() inspection
5. Truthful action surface disclosure for canvas and media elements
"""

from __future__ import annotations

import json
import unittest
import pytest
import httpx

from webresearch.actions import collect
from webresearch.engine.runtime import Context, MINIDOM_JS, _pump_jobs
from webresearch.engine.tree import html_to_tree
from webresearch.read import read_live


def make_dom(html: str = "<html><body></body></html>", url: str = "https://example.com/") -> Context:
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ast = json.dumps(html_to_tree(html))
    ctx.eval(f"__load({ast}, {json.dumps(url)});")
    return ctx


class IndexedDBUpgradeTest(unittest.TestCase):
    """Verify IndexedDB IDBFactory.databases() and storage operations."""

    def test_indexeddb_databases_method(self) -> None:
        ctx = make_dom()
        code = """
        var results = [];
        var req1 = indexedDB.open("app_db_1", 2);
        req1.onsuccess = function (e) {
          var req2 = indexedDB.open("app_db_2", 1);
          req2.onsuccess = function () {
            indexedDB.databases().then(function (dbs) {
              results = dbs;
            });
          };
        };
        """
        ctx.eval(code)
        # Pump jobs to resolve all nested promises
        for _ in range(10):
            _pump_jobs(ctx)

        names_json = ctx.eval("JSON.stringify(results.map(function(d) { return d.name; }).sort())")
        self.assertIn("app_db_1", names_json)
        self.assertIn("app_db_2", names_json)


class CSSGeometryAndGridLayoutTest(unittest.TestCase):
    """Verify offsetLeft, offsetTop, offsetParent, and CSS Grid layout."""

    def test_offset_parent_and_coords(self) -> None:
        html = """
        <html><body>
          <div id="container" style="position: relative; width: 500px; height: 300px;">
            <div id="inner" style="position: absolute; left: 45px; top: 60px; width: 100px; height: 50px;"></div>
          </div>
        </body></html>
        """
        ctx = make_dom(html)

        op_id = ctx.eval("document.getElementById('inner').offsetParent.id")
        self.assertEqual(op_id, "container")

        left = ctx.eval("document.getElementById('inner').offsetLeft")
        top = ctx.eval("document.getElementById('inner').offsetTop")
        self.assertEqual(left, 45)
        self.assertEqual(top, 60)

    def test_css_grid_layout_columns_and_gap(self) -> None:
        html = """
        <html><body style="margin: 0;">
          <div id="grid-container" style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; width: 420px;">
            <div id="col1" style="height: 50px;">Item 1</div>
            <div id="col2" style="height: 50px;">Item 2</div>
          </div>
        </body></html>
        """
        ctx = make_dom(html)

        b1 = json.loads(ctx.eval("JSON.stringify(document.getElementById('col1').getBoundingClientRect())"))
        b2 = json.loads(ctx.eval("JSON.stringify(document.getElementById('col2').getBoundingClientRect())"))

        # Under standard 8px document body margin:
        # col1 is at left 8 with width 200 ((420 - 20) / 2 = 200)
        # col2 is at left 228 (8 + 200 + 20 gap = 228)
        self.assertAlmostEqual(b1["left"], 8, delta=2)
        self.assertAlmostEqual(b1["width"], 200, delta=2)
        self.assertAlmostEqual(b2["left"], 228, delta=2)
        self.assertAlmostEqual(b2["left"] - b1["left"], 220, delta=2)
        self.assertAlmostEqual(b2["width"], 200, delta=2)

        engine = ctx.eval("document.__layoutInfo.engine")
        self.assertEqual(engine, "block-flow+flex+grid+positioned")


class IFrameDOMPropertiesTest(unittest.TestCase):
    """Verify iframe.contentDocument and iframe.contentWindow standard DOM access."""

    def test_iframe_content_document_and_window(self) -> None:
        ctx = make_dom()
        code = """
        var frame = document.createElement("iframe");
        frame.id = "test-frame";
        document.body.appendChild(frame);

        var doc1 = frame.contentDocument;
        var win = frame.contentWindow;
        var doc2 = win ? win.document : null;

        var matches = (doc1 === doc2 && doc1 !== null);
        var hasBody = (doc1 && doc1.body !== null);
        """
        ctx.eval(code)
        matches = ctx.eval("matches")
        has_body = ctx.eval("hasBody")
        self.assertTrue(matches)
        self.assertTrue(has_body)


class CrossFrameMessagingTest(unittest.TestCase):
    """Verify cross-frame postMessage handshake and origin security boundary."""

    def test_origin_security_boundary_rejects_mismatched_target_origin(self) -> None:
        ctx = make_dom(url="https://bank.example.com/portal")
        code = """
        var received = [];
        window.addEventListener("message", function (e) {
          received.push(e.data);
        });

        // 1. Message targeted to wrong origin should be rejected
        window.__receiveMessage("secret_data_1", "https://attacker.com", "https://other.com");

        // 2. Message targeted to wildcard '*' should be accepted
        window.__receiveMessage("wildcard_data", "https://trusted.com", "*");

        // 3. Message targeted to matching origin should be accepted
        window.__receiveMessage("matching_data", "https://trusted.com", "https://bank.example.com");
        """
        ctx.eval(code)
        for _ in range(5):
            _pump_jobs(ctx)

        res_json = ctx.eval("JSON.stringify(received)")
        self.assertNotIn("secret_data_1", res_json)
        self.assertIn("wildcard_data", res_json)
        self.assertIn("matching_data", res_json)

    @pytest.mark.anyio
    async def test_bidirectional_postmessage_handshake_live(self) -> None:
        parent_html = """<!doctype html>
        <html>
        <head><title>Parent</title></head>
        <body>
          <div id="status">pending</div>
          <iframe id="child-frame" src="http://test.local/child.html"></iframe>
          <script>
            window.addEventListener("message", function (e) {
              if (e.data === "ready_from_child") {
                document.getElementById("status").textContent = "handshake_ok";
              }
            });
          </script>
        </body>
        </html>
        """
        child_html = """<!doctype html>
        <html>
        <head><title>Child</title></head>
        <body>
          <script>
            window.parent.postMessage("ready_from_child", "*");
          </script>
        </body>
        </html>
        """

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/" or request.url.path == "/parent.html":
                return httpx.Response(200, html=parent_html, headers={"content-type": "text/html"})
            elif request.url.path == "/child.html":
                return httpx.Response(200, html=child_html, headers={"content-type": "text/html"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            page, final_html, frames, captcha = await read_live(
                "http://test.local/parent.html",
                client=client,
                js="always",
            )
            self.assertEqual(len(frames), 1)
            # The parent DOM should have received the child's message and updated #status to "handshake_ok"
            self.assertIn("handshake_ok", final_html)


class ActionDisclosureTest(unittest.TestCase):
    """Verify truthful disclosure in action surface notes."""

    def test_canvas_and_media_notes_disclosure(self) -> None:
        html = """
        <html><body>
          <canvas id="cv" width="100" height="100"></canvas>
          <iframe id="ifr" src="https://example.com/frame"></iframe>
          <audio id="aud" src="https://example.com/sound.mp3"></audio>
        </body></html>
        """
        surface = collect(html, "https://example.com/")
        actions = {a.tag: a for a in surface.actions}

        self.assertIn("canvas", actions)
        self.assertIn("iframe", actions)
        self.assertIn("audio", actions)

        self.assertEqual(
            actions["canvas"].notes,
            "canvas element: visual pixels not rendered natively; inspect AST/attributes or data-URL",
        )
        self.assertEqual(
            actions["iframe"].notes,
            "content is a separate document; read its source",
        )
        self.assertEqual(
            actions["audio"].notes,
            "media stream: inspect audio/video source and track attributes",
        )
