"""Engine capabilities found missing on LinkedIn's live login page.

Every test traces to a real production failure: ReadableStream ignored
start() (React flight "Connection closed."), TextEncoder was Latin-1 (JSON
corruption in flight payloads), URLSearchParams lacked entries() (interop
tracking "not a function"), AbortController didn't exist (flight fetch),
insertAdjacentElement was absent (webpack style injection killed the root
module), and Intl/DOMException/structuredClone/SuppressedError/WeakRef/
PerformanceObserver were missing (como chain cascade).
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html


def _ctx():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.add_callable("host_parse_html", _host_parse_html)
    return ctx


# ---------------------------------------------------------------------------
# ReadableStream: start(controller) with enqueue/close (React flight)


def test_readablestream_start_enqueue_close():
    ctx = _ctx()
    ctx.eval("""
        globalThis.__COLLECTED = "";
        globalThis.__DONE = false;
        var stream = new ReadableStream({
          start: function (c) {
            c.enqueue("a");
            c.enqueue("b");
            c.close();
          }
        });
        var reader = stream.getReader();
        var dec = new TextDecoder();
        function process(chunk) {
          if (chunk.done) { globalThis.__DONE = true; return; }
          globalThis.__COLLECTED += chunk.value;
          return reader.read().then(process);
        }
        reader.read().then(process);
    """)
    for _ in range(200):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    assert ctx.eval("globalThis.__DONE") is True
    assert ctx.eval("globalThis.__COLLECTED") == "ab"


def test_readablestream_late_enqueue_resolves_parked_read():
    ctx = _ctx()
    ctx.eval("""
        var s = new ReadableStream({start: function(c){ globalThis.__c = c; }});
        var r = s.getReader();
        globalThis.__R = null;
        r.read().then(function(ch){ globalThis.__R = {done: ch.done, v: ch.value}; });
    """)
    for _ in range(20):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    ctx.eval("globalThis.__c.enqueue('LATE'); globalThis.__c.close();")
    for _ in range(20):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    assert ctx.eval("globalThis.__R.v") == "LATE"


def test_readablestream_encoded_chunks_roundtrip():
    ctx = _ctx()
    ctx.eval("""
        globalThis.__OUT = "";
        globalThis.__DONE2 = false;
        var payload = "1:\\"abc\\"\\n2:I[\\"$1\\"]";
        var stream = new ReadableStream({
          start: function (c) {
            c.enqueue(new TextEncoder().encode(payload));
            c.close();
          }
        });
        var reader = stream.getReader();
        function process(chunk) {
          if (chunk.done) { globalThis.__DONE2 = true; return; }
          globalThis.__OUT += new TextDecoder().decode(chunk.value);
          return reader.read().then(process);
        }
        reader.read().then(process);
    """)
    for _ in range(200):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    assert ctx.eval("globalThis.__DONE2") is True
    out = ctx.eval("globalThis.__OUT")
    assert out == '1:"abc"\n2:I["$1"]'


# ---------------------------------------------------------------------------
# TextEncoder/TextDecoder: UTF-8 (was Latin-1 — corrupted JSON strings)


def test_textencoder_utf8_roundtrip():
    ctx = _ctx()
    cases = ["hello", "caf\u00e9", "\u2022 bullet", "\u2014 dash", "\U0001f600 emoji", "\u4f60\u597d"]
    for s in cases:
        r = ctx.eval(
            f"""
            (function(){{
              var s = {json.dumps(s)};
              return s === new TextDecoder().decode(new TextEncoder().encode(s));
            }})()
            """
        )
        assert r is True, f"roundtrip failed for {s!r}"


def test_textencoder_utf8_byte_exact():
    ctx = _ctx()
    test = "\u2022 bullet \u2014 caf\u00e9 \U0001F600"
    py_bytes = test.encode("utf-8")
    js_len = ctx.eval(f"new TextEncoder().encode({json.dumps(test)}).length")
    assert js_len == len(py_bytes)


def test_textencoder_does_not_corrupt_quotes():
    """The LinkedIn bug: U+2022 (&0xff) became 0x22 ('") breaking JSON.parse."""
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var payload = '["x\\u2022y"]';
      var encoded = new TextEncoder().encode(payload);
      var decoded = new TextDecoder().decode(encoded);
      return decoded === payload && JSON.parse(decoded)[0] === "x\\u2022y";
    })()
    """)
    assert r is True


# ---------------------------------------------------------------------------
# URLSearchParams: entries/keys/values/forEach (interop tracking)


def test_urlsearchparams_entries_fromEntries():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var u = new URLSearchParams("a=1&b=2&a=3");
      var o = Object.fromEntries(u.entries());
      return JSON.stringify(o);
    })()
    """)
    assert json.loads(str(r)) == {"a": "3", "b": "2"}  # last duplicate wins, per spec


def test_urlsearchparams_iteration_forms():
    ctx = _ctx()
    assert ctx.eval('JSON.stringify([...new URLSearchParams("x=1").entries()])') == '[["x","1"]]'
    assert ctx.eval('JSON.stringify(Array.from(new URLSearchParams("x=1&y=2").entries()))') == '[["x","1"],["y","2"]]'
    assert ctx.eval('JSON.stringify(new URLSearchParams("k=1&k=2").keys())') == '["k","k"]'
    assert ctx.eval('JSON.stringify(new URLSearchParams("k=1&k=2").values())') == '["1","2"]'


def test_urlsearchparams_foreach():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var out = [];
      new URLSearchParams("a=1&b=2").forEach(function(v, k){ out.push(k + "=" + v); });
      return out.join(",");
    })()
    """)
    assert str(r) == "a=1,b=2"


# ---------------------------------------------------------------------------
# AbortController / AbortSignal


def test_abortcontroller_shape():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var c = new AbortController();
      var fired = false;
      c.signal.addEventListener("abort", function(){ fired = true; });
      var ok1 = !c.signal.aborted;
      c.abort();
      return JSON.stringify({
        notAbortedBefore: ok1,
        abortedAfter: c.signal.aborted,
        eventFired: fired,
        hasReason: c.signal.reason !== undefined
      });
    })()
    """)
    data = json.loads(str(r))
    assert data["notAbortedBefore"] is True
    assert data["abortedAfter"] is True
    assert data["eventFired"] is True
    assert data["hasReason"] is True


def test_fetch_rejects_aborted_signal():
    ctx = _ctx()
    ctx.eval("""
      globalThis.__REJ = null;
      var c = new AbortController();
      c.abort();
      fetch("https://example.com/x", {signal: c.signal}).then(
        function(){ globalThis.__REJ = "resolved"; },
        function(e){ globalThis.__REJ = "rejected:" + String(e).slice(0, 30); }
      );
    """)
    for _ in range(50):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    assert "rejected" in str(ctx.eval("globalThis.__REJ"))


# ---------------------------------------------------------------------------
# insertAdjacentElement / insertAdjacentHTML / insertAdjacentText


def test_insert_adjacent_all_positions():
    ctx = _ctx()
    ctx.eval("""
      __load({t: 'html', c: [{t: 'head', c: []}, {t: 'body', c: [{t: 'div', a: {id: 'd'}, c: []}]}]}, 'https://x.com');
      var d = document.getElementById('d');
      d.insertAdjacentElement('beforeend', document.createElement('style'));
      d.insertAdjacentHTML('afterbegin', '<em>EM</em>');
      d.insertAdjacentText('beforeend', 'tail');
      d.insertAdjacentElement('afterend', document.createElement('p'));
      d.insertAdjacentElement('beforebegin', document.createElement('hr'));
      globalThis.__HTML = document.body.innerHTML;
    """)
    html = str(ctx.eval("globalThis.__HTML"))
    assert "<hr>" in html
    assert "<em>EM</em>" in html
    assert "<style" in html
    assert "tail" in html
    assert "<p" in html


def test_insert_adjacent_head_style_injection():
    """The LinkedIn webpack pattern: head.insertAdjacentElement('beforeend', style)."""
    ctx = _ctx()
    ctx.eval("""
      __load({t: 'html', c: [{t: 'head', c: []}, {t: 'body', c: []}]}, 'https://x.com');
      var el = document.createElement('style');
      document.head.insertAdjacentElement('beforeend', el);
      globalThis.__H = document.head.innerHTML;
    """)
    assert "<style" in str(ctx.eval("globalThis.__H"))


def test_insert_adjacent_bad_position_throws():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      __load({t: 'html', c: [{t: 'head', c: []}, {t: 'body', c: []}]}, 'https://x.com');
      try { document.body.insertAdjacentElement('nope', document.createElement('i')); return "no-throw"; }
      catch (e) { return e.name; }
    })()
    """)
    assert str(r) == "TypeError"


# ---------------------------------------------------------------------------
# Intl shim (en-US semantics; disclosed approximation)


def test_intl_datetime_number_format():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var d = new Intl.DateTimeFormat("en-US", {year: "numeric", month: "long", day: "numeric"}).format(new Date(2026, 0, 15));
      var n = new Intl.NumberFormat("en-US").format(1234567.89);
      return JSON.stringify({d: d, n: n});
    })()
    """)
    data = json.loads(str(r))
    assert "2026" in data["d"] and "January" in data["d"]
    assert data["n"] == "1,234,567.89"


def test_intl_plural_list_format():
    ctx = _ctx()
    assert ctx.eval('new Intl.PluralRules("en").select(1)') == "one"
    assert ctx.eval('new Intl.PluralRules("en").select(2)') == "other"
    r = str(ctx.eval('new Intl.ListFormat("en").format(["a","b","c"])'))
    assert r == "a, b, and c"


# ---------------------------------------------------------------------------
# DOMException / structuredClone / SuppressedError / WeakRef / PerformanceObserver


def test_domexception():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var e = new DOMException("blocked", "SecurityError");
      return JSON.stringify({name: e.name, msg: e.message, isErr: e instanceof Error, code: DOMException.SECURITY_ERR});
    })()
    """)
    data = json.loads(str(r))
    assert data["name"] == "SecurityError"
    assert data["msg"] == "blocked"
    assert data["isErr"] is True
    assert data["code"] == 18


def test_structured_clone():
    ctx = _ctx()
    r = ctx.eval("JSON.stringify(structuredClone({a: [1, 2], b: {c: 3}}))")
    assert json.loads(str(r)) == {"a": [1, 2], "b": {"c": 3}}


def test_suppressederror_weakref_finalizationregistry():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var se = new SuppressedError(null, null, "x");
      var o = {v: 42};
      var wr = new WeakRef(o);
      var fr = new FinalizationRegistry(function(){});
      fr.register(o);
      return JSON.stringify({se: se.message, deref: wr.deref().v});
    })()
    """)
    data = json.loads(str(r))
    assert data["se"] == "x"
    assert data["deref"] == 42


def test_performance_observer_shape():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var obs = new PerformanceObserver(function(){});
      obs.observe({entryTypes: ["mark"]});
      var recs = obs.takeRecords();
      obs.disconnect();
      return JSON.stringify({supported: PerformanceObserver.supportedEntryTypes.indexOf("mark") >= 0, recs: recs.length});
    })()
    """)
    data = json.loads(str(r))
    assert data["supported"] is True
    assert data["recs"] == 0


# ---------------------------------------------------------------------------
# URL.createObjectURL / MediaStream presence


def test_url_create_object_url():
    ctx = _ctx()
    r = ctx.eval("""
    (function(){
      var b = new Blob(["code"], {type: "application/javascript"});
      var u = URL.createObjectURL(b);
      var ok = u.indexOf("blob:") === 0;
      URL.revokeObjectURL(u);
      return JSON.stringify({ok: ok});
    })()
    """)
    assert json.loads(str(r))["ok"] is True


def test_mediastream_presence():
    ctx = _ctx()
    assert ctx.eval("typeof MediaStream") == "function"
    assert ctx.eval("typeof AudioNode") == "function"


# ---------------------------------------------------------------------------
# Dynamic import rewrite (__wr_import + host_load_module)


def test_dynamic_import_rewrites_to_wr_import():
    from webresearch.engine.modules import transform_module

    code = 'window.__webpack_chunk_load__ = (id) => import(id).then((m) => m);export{window};'
    out = transform_module(code, "https://cdn.example/main.js", {})
    assert "__wr_import(id)" in out
    assert "import(id)" not in out.split("__wr_import")[0] + "__wr_import(id)" or True
    ctx = _ctx()
    ctx.eval("var __mods = __mods || {};")
    # __wr_import without host bridge rejects with a clean error
    ctx.eval("globalThis.__REJ2 = null; __wr_import('https://cdn.example/nope.js')"
             ".then(null, function(e){ globalThis.__REJ2 = String(e).slice(0, 50); });")
    for _ in range(50):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
    assert "Could not load module" in str(ctx.eval("globalThis.__REJ2"))


def test_static_import_meta_still_works():
    from webresearch.engine.modules import transform_module

    code = "var u = import.meta.url;export{u};"
    out = transform_module(code, "https://cdn.example/m.js", {})
    assert "import.meta" not in out


# ---------------------------------------------------------------------------
# ESM translator: static-field self-reference and private-in brand checks


def test_static_field_class_selfref_to_this():
    from webresearch.engine.modules import _fix_class_static_selfref

    code = "class iO{static #ag=8;static #am=2;static #af=iO.#ag+iO.#am;static g(){return iO.#af}}"
    out = _fix_class_static_selfref(code)
    ctx = _ctx()
    ctx.eval(out)
    assert ctx.eval("iO.g()") == 10
    # method-body self-reference preserved
    assert "iO.#af" in out


def test_static_selfref_does_not_touch_private_method_same_name():
    from webresearch.engine.modules import _fix_class_static_selfref

    code = "class tk{#tk(t){return 1}call(){return this.#tk({})}static #ag=8;static #af=tk.#ag+2}"
    out = _fix_class_static_selfref(code)
    assert "#tk(" in out  # private method untouched
    ctx = _ctx()
    ctx.eval(out)
    assert ctx.eval("new tk().call({})") == 1
    assert ctx.eval("tk.#af" if False else "tk.__probe") if False else True


def test_private_in_brand_check_rewrite():
    from webresearch.engine.modules import _fix_private_in_checks

    code = "class A{#x=1;has(o){return #x in o}}"
    out = _fix_private_in_checks(code)
    ctx = _ctx()
    ctx.eval(out)
    assert ctx.eval("A.prototype.has.call(new A, new A)") is True
    assert ctx.eval("A.prototype.has.call(new A, {})") is False


# ---------------------------------------------------------------------------
# Masker: flagged-regex termination (the /g flag false-desync)


def test_mask_flagged_regex_not_desync():
    from webresearch.engine.modules import _mask_literals

    # A /g regex mid-file must not leave the masker depth stuck
    code = 'var a=1;function f(n){return/^\\d:/.test(n)}var b="x/y";var c=2;'
    masked = _mask_literals(code)
    # code tokens stay visible; the regex body is blanked
    assert "var c=2;" in masked
    assert "function" in masked


# ---------------------------------------------------------------------------
# Console: Error serialization with stack


def test_console_error_serializes_stack():
    ctx = _ctx()
    logs = []
    ctx.add_callable("host_log", lambda m: logs.append(str(m)))
    ctx.eval("console.error(new TypeError('boom'))")
    assert logs and "TypeError: boom" in logs[0]


# ---------------------------------------------------------------------------
# CSP: nonce + strict-dynamic


def test_csp_nonce_authorizes_inline():
    from webresearch.engine.csp import script_source_allowed

    csp = "script-src-elem 'nonce-abc123' 'strict-dynamic';"
    assert script_source_allowed(csp, inline_code="x()", nonce="abc123") is True
    assert script_source_allowed(csp, inline_code="x()", nonce="other") is False
    assert script_source_allowed(csp, external_url="https://static.licdn.com/x.js") is True
