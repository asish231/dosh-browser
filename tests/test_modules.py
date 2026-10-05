import asyncio
import unittest

import httpx

from webresearch.engine.modules import transform_module
from webresearch.engine.runtime import run_scripts


def _run(html: str, handler=None, url: str = "https://example.com/", actions=None):
    transport = httpx.MockTransport(handler) if handler else None

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await run_scripts(html, url, client, force=True, actions=actions)

    return asyncio.run(go())


class ModuleTests(unittest.TestCase):
    def test_transform_named_function_and_default_export(self):
        out = transform_module(
            "export function greet(n){ return 'hi ' + n; }\nexport default 'D';",
            "m.js",
            {},
        )
        self.assertIn("function greet", out)
        self.assertIn("__exports['greet'] = greet;", out)
        self.assertIn("__exports.default = 'D';", out)

    def test_module_import_renders_into_dom(self):
        page = (
            "<html><body><div id='root'>loading</div>"
            "<script type='module'>"
            "import greetDefault, { greet } from './greet.js';"
            "document.getElementById('root').textContent = greetDefault + ' ' + greet('world');"
            "</script></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/greet.js":
                return httpx.Response(
                    200,
                    text="export function greet(n){ return 'hi ' + n; }\nexport default 'D';",
                    headers={"content-type": "text/javascript"},
                )
            return httpx.Response(404, text="missing")

        html, info = _run(page, handler)
        self.assertEqual(info["errors"], 0)
        self.assertGreaterEqual(info["modules"], 2)
        self.assertIn("D hi world", html)

    def test_side_effect_import_and_reexport(self):
        page = (
            "<html><body><div id='root'>x</div>"
            "<script type='module'>"
            "import './side.js';"
            "import { value } from './re.js';"
            "document.getElementById('root').textContent = String(value);"
            "</script></body></html>"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/side.js":
                return httpx.Response(
                    200,
                    text="window.__side = true;",
                    headers={"content-type": "text/javascript"},
                )
            if request.url.path == "/re.js":
                return httpx.Response(
                    200,
                    text="export { value } from './base.js';",
                    headers={"content-type": "text/javascript"},
                )
            if request.url.path == "/base.js":
                return httpx.Response(
                    200,
                    text="export const value = 42;",
                    headers={"content-type": "text/javascript"},
                )
            return httpx.Response(404, text="missing")

        html, info = _run(page, handler)
        self.assertEqual(info["error_messages"], [])
        self.assertIn("42", html)

    def test_promise_microtasks_are_pumped(self):
        page = (
            "<html><body><div id='root'>a</div><script>"
            "Promise.resolve().then(function(){"
            "document.getElementById('root').textContent = 'promised';"
            "});"
            "</script></body></html>"
        )
        html, info = _run(page)
        self.assertEqual(info["errors"], 0)
        self.assertIn("promised", html)

    def test_framework_data_extracted(self):
        page = (
            "<html><body><script>"
            "window.__NEXT_DATA__ = {props:{pageProps:{title:'hi'}}};"
            "</script></body></html>"
        )
        _html, info = _run(page)
        self.assertEqual(info["framework_data"].get("__NEXT_DATA__"), {"props": {"pageProps": {"title": "hi"}}})

    def test_js_errors_are_surfaced(self):
        page = "<html><body><script>throw new Error('boom');</script></body></html>"
        _html, info = _run(page)
        self.assertEqual(info["errors"], 1)
        self.assertTrue(any("boom" in m for m in info["error_messages"]))

    def test_navigator_ua_mirrors_http_ua(self):
        import re

        async def go():
            async with httpx.AsyncClient(headers={"User-Agent": "UA-TEST-123"}) as client:
                return await run_scripts(
                    "<div id='r'></div><script>document.getElementById('r').textContent = navigator.userAgent;</script>",
                    "https://example.com/",
                    client,
                    force=True,
                )

        html, _info = asyncio.run(go())
        m = re.search(r"<div id=[\"']r[\"']>(.*?)</div>", html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "UA-TEST-123")

    def test_customelements_and_sessionstorage(self):
        page = (
            "<html><body><div id='root'></div><script>"
            "customElements.define('x-thing', class extends HTMLElement {});"
            "sessionStorage.setItem('k', 'v');"
            "document.getElementById('root').textContent = "
            "String(typeof customElements.get('x-thing')) + '/' + sessionStorage.getItem('k');"
            "</script></body></html>"
        )
        html, info = _run(page)
        self.assertEqual(info["errors"], 0)
        self.assertIn("function/v", html)

    def test_mutation_observer_fires_on_childlist(self):
        page = (
            "<html><body><ul id='list'></ul><div id='count'>0</div><script>"
            "var n = 0;"
            "var list = document.getElementById('list');"
            "new MutationObserver(function(records){"
            "document.getElementById('count').textContent = 'saw ' + records.length;"
            "}).observe(list, { childList: true });"
            "var li = document.createElement('li');"
            "li.textContent = 'x';"
            "list.appendChild(li);"
            "</script></body></html>"
        )
        html, info = _run(page)
        self.assertEqual(info["errors"], 0)
        self.assertIn("saw ", html)

    def test_mutation_observer_attributes_and_take_records(self):
        page = (
            "<html><body><div id='t'></div><div id='out'>none</div><script>"
            "var el = document.getElementById('t');"
            "var obs = new MutationObserver(function(){});"
            "obs.observe(el, { attributes: true });"
            "el.setAttribute('data-x', '1');"
            "el.classList.add('on');"
            "var recs = obs.takeRecords();"
            "document.getElementById('out').textContent = 'records=' + recs.length + ':' + recs[0].type + ':' + recs[0].attributeName;"
            "</script></body></html>"
        )
        html, info = _run(page)
        self.assertEqual(info["errors"], 0)
        self.assertIn("records=2:attributes:data-x", html)

    def test_click_mutation_observer_rerender(self):
        from webresearch.engine.inspect import inspect_html

        page = (
            "<html><body><button id='b'>Add</button><ul id='list'></ul>"
            "<div id='count'>0 items</div><script>"
            "var items = 0;"
            "var list = document.getElementById('list');"
            "new MutationObserver(function(){"
            "document.getElementById('count').textContent = items + ' items';"
            "}).observe(list, { childList: true });"
            "document.getElementById('b').addEventListener('click', function(){"
            "items++;"
            "var li = document.createElement('li');"
            "li.textContent = 'row ' + items;"
            "list.appendChild(li);"
            "});"
            "</script></body></html>"
        )
        targets, _ = inspect_html(page, "https://example.com/")
        btn = next(t for t in targets if t.tag == "button")
        html, info = _run(
            page,
            actions=[
                {"kind": "click", "path": btn.path},
                {"kind": "click", "path": btn.path},
                {"kind": "click", "path": btn.path},
            ],
        )
        self.assertEqual(info["errors"], 0)
        self.assertIn("3 items", html)
        self.assertEqual(html.count("<li>"), 3)

    def test_click_dispatches_real_event(self):
        from webresearch.engine.inspect import inspect_html

        page = (
            "<html><body><div id='root'>a</div><button id='b'>Go</button>"
            "<script>document.getElementById('b').addEventListener('click', function(){"
            "document.getElementById('root').textContent = 'clicked';});</script>"
            "</body></html>"
        )
        targets, _ = inspect_html(page, "https://example.com/")
        button = next(t for t in targets if t.tag == "button")
        self.assertTrue(button.path)
        html, info = _run(page, actions=[{"kind": "click", "path": button.path}])
        self.assertIn("clicked", html)
        self.assertTrue(info["actions_applied"][0]["ok"])

    def test_type_fires_input_event(self):
        from webresearch.engine.inspect import inspect_html

        page = (
            "<html><body><input id='q'><div id='out'>none</div>"
            "<script>document.getElementById('q').addEventListener('input', function(e){"
            "document.getElementById('out').textContent = 'got:' + e.target.value;});"
            "</script></body></html>"
        )
        targets, _ = inspect_html(page, "https://example.com/")
        field = next(t for t in targets if t.tag == "input")
        html, _info = _run(
            page, actions=[{"kind": "type", "path": field.path, "text": "paris"}]
        )
        self.assertIn("got:paris", html)


if __name__ == "__main__":
    unittest.main()