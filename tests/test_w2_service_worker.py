"""W2: Service Worker execution + fetch interception — hermetic tests.

The engine's navigator.serviceWorker.register() must FETCH the SW script
(blob: store or host_fetch), EXECUTE it in a ServiceWorkerGlobalScope
realm, CAPTURE its "fetch" event handler, and ROUTE page fetches within
the SW scope through event.respondWith(). Before W2, register() resolved
with a fake-activated registration object and never ran any script.

Async results cross the QuickJS boundary the way the rest of the suite
does them: store into globalThis.__* inside .then(), pump microtasks
with ctx.execute_pending_job(), then read the globals.
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _pump(ctx: Context, rounds: int = 200) -> None:
    for _ in range(rounds):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break


def _ctx_with_sw_script(script: str) -> Context:
    """Context whose host_fetch serves the SW script for /sw.js and a
    NETWORK-BODY response for everything else."""
    def host_fetch(req_url, method, body, headers_json, body_b64, initiator_stack):
        url = str(req_url)
        if "sw.js" in url:
            return json.dumps({"status": 200, "body": script, "content_type": "application/javascript"})
        return json.dumps({"status": 200, "body": "NETWORK-BODY", "content_type": "text/plain"})

    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.add_callable("host_fetch", host_fetch)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree("<html><body></body></html>")
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def _register(ctx: Context) -> None:
    ctx.eval("""
        globalThis.__REG = null;
        navigator.serviceWorker.register('/sw.js').then(function(reg) {
            globalThis.__REG = reg;
        });
    """)
    _pump(ctx)


def _fetch(ctx: Context, url: str, opts_js: str = "") -> dict:
    call_args = json.dumps(url) + (("," + opts_js) if opts_js else "")
    ctx.eval(f"""
        globalThis.__FETCH = null;
        fetch({call_args}).then(function(resp) {{
            return resp.text().then(function(t) {{
                globalThis.__FETCH = {{
                    body: t, status: resp.status,
                    type: resp.headers.get('content-type') || ''
                }};
            }});
        }});
    """)
    _pump(ctx)
    return json.loads(ctx.eval("JSON.stringify(globalThis.__FETCH)"))


def test_register_executes_sw_script_and_activates():
    """register() runs the SW script; install/activate handlers fire inside
    the SW realm and the fetch handler is captured on the active SW."""
    sw_script = (
        "addEventListener('install', function(e) { self.__installed = true; });"
        "addEventListener('activate', function(e) { self.__activated = true; });"
        "addEventListener('fetch', function(e) { });"
    )
    ctx = _ctx_with_sw_script(sw_script)
    _register(ctx)
    reg_state = ctx.eval("globalThis.__REG && globalThis.__REG.active ? globalThis.__REG.active.state : null")
    script_url = ctx.eval("globalThis.__REG && globalThis.__REG.active ? globalThis.__REG.active.scriptURL : null")
    assert reg_state == "activated", f"SW should be activated, got {reg_state}"
    assert script_url and "sw.js" in str(script_url), f"scriptURL should name the script, got {script_url}"
    has_handler = ctx.eval("!!(globalThis.__wrActiveSW && globalThis.__wrActiveSW.fetchHandler)")
    assert has_handler, "the SW fetch handler must be captured on __wrActiveSW"
    controller_set = ctx.eval("!!(navigator.serviceWorker.controller)")
    assert controller_set, "navigator.serviceWorker.controller must be set after registration"


def test_sw_intercepts_fetch_via_respond_with():
    """A page fetch within the SW scope returns the SW's Response, not the
    network body."""
    sw_script = (
        "addEventListener('fetch', function(event) {"
        "  event.respondWith(new Response('SW-BODY', {"
        "    status: 200, headers: {'content-type': 'text/sw'}"
        "  }));"
        "});"
    )
    ctx = _ctx_with_sw_script(sw_script)
    _register(ctx)
    result = _fetch(ctx, "https://example.com/page.html")
    assert result["body"] == "SW-BODY", f"fetch should return SW body, got {result['body']}"
    assert result["status"] == 200
    assert "text/sw" in result["type"], f"SW response headers must survive, got {result['type']}"


def test_sw_sees_request_method_and_url():
    """The fetch event carries the real request: method + URL."""
    sw_script = (
        "addEventListener('fetch', function(event) {"
        "  var req = event.request;"
        "  event.respondWith(new Response('method=' + req.method + ' url=' + req.url, {}));"
        "});"
    )
    ctx = _ctx_with_sw_script(sw_script)
    _register(ctx)
    result = _fetch(ctx, "https://example.com/api/data", "{method: 'POST', body: 'x=1'}")
    assert "method=POST" in result["body"], f"SW should see method=POST, got {result['body']}"
    assert "url=https://example.com/api/data" in result["body"], f"SW should see the URL, got {result['body']}"


def test_sw_without_respond_with_falls_through_to_network():
    """No respondWith -> the page fetch proceeds over the network (disclosed
    engine choice; the spec would error)."""
    sw_script = (
        "addEventListener('fetch', function(event) {"
        "  /* sees the event, never responds */"
        "});"
    )
    ctx = _ctx_with_sw_script(sw_script)
    _register(ctx)
    result = _fetch(ctx, "https://example.com/plain.txt")
    assert result["body"] == "NETWORK-BODY", f"fall-through should return the network body, got {result['body']}"


def test_sw_out_of_scope_not_intercepted():
    """A fetch outside the SW scope ('/app/' scope) goes to the network."""
    sw_script = (
        "addEventListener('fetch', function(event) {"
        "  event.respondWith(new Response('SW-BODY', {}));"
        "});"
    )
    def host_fetch(req_url, method, body, headers_json, body_b64, initiator_stack):
        url = str(req_url)
        if "sw.js" in url:
            return json.dumps({"status": 200, "body": sw_script, "content_type": "application/javascript"})
        return json.dumps({"status": 200, "body": "NETWORK-BODY", "content_type": "text/plain"})

    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.add_callable("host_fetch", host_fetch)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree("<html><body></body></html>")
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    ctx.eval("""
        globalThis.__REG = null;
        navigator.serviceWorker.register('/sw.js', {scope: 'https://example.com/app/'}).then(function(reg) {
            globalThis.__REG = reg;
        });
    """)
    _pump(ctx)
    assert ctx.eval("globalThis.__REG && globalThis.__REG.active ? globalThis.__REG.active.state : null") == "activated"
    result = _fetch(ctx, "https://example.com/outside/page.html")
    assert result["body"] == "NETWORK-BODY", f"out-of-scope fetch must not be intercepted, got {result['body']}"
    result_in = _fetch(ctx, "https://example.com/app/page.html")
    assert result_in["body"] == "SW-BODY", f"in-scope fetch must be intercepted, got {result_in['body']}"


def test_sw_unregister_stops_interception():
    """unregister() clears the interceptor: __wrActiveSW is null and later
    fetches hit the network."""
    sw_script = (
        "addEventListener('fetch', function(event) {"
        "  event.respondWith(new Response('SW-BODY', {}));"
        "});"
    )
    ctx = _ctx_with_sw_script(sw_script)
    _register(ctx)
    intercepted = _fetch(ctx, "https://example.com/a")
    assert intercepted["body"] == "SW-BODY"
    ctx.eval("""
        globalThis.__UNREG = null;
        navigator.serviceWorker.getRegistration().then(function(reg) {
            if (reg) return reg.unregister();
        }).then(function() { globalThis.__UNREG = true; });
    """)
    _pump(ctx)
    active = ctx.eval("!!globalThis.__wrActiveSW")
    assert active is False, "__wrActiveSW must be cleared after unregister"
    assert ctx.eval("globalThis.__UNREG") is True
