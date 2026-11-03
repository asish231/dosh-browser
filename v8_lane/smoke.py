#!/usr/bin/env python3
"""Hour-zero V8 smoke harness — the v8_lane test version, NOT the product.

Boots a real embedded V8 (mini-racer, prebuilt V8 static) and runs the
product's own `webresearch/engine/minidom.js` VERBATIM inside it, then
re-runs the identical probes under QuickJS (the product default) in the
same process as the oracle. Host callables are implemented as JS stubs so
that the comparison isolates the JS/WASM execution engine; the full
Python↔V8 host-callable seam is Phase 1 of the planned rusty_v8 scaffold.

Boundary (per AGENTS.md): `webresearch/` never imports this lane. The
product default engine stays QuickJS.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MINIDOM_JS = (REPO / "webresearch" / "engine" / "minidom.js").read_text(encoding="utf-8")

from py_mini_racer import MiniRacer  # noqa: E402
from quickjs import Context  # noqa: E402


STUBS = """
globalThis.__WR_STORAGE_SEED = {};
globalThis.__WR_HISTORY_LENGTH = 1;
globalThis.__WR_UA = "V8Smoke/1.0";
globalThis.__WR_DEVICE = {};
/* preserve native constructors before minidom.js overwrites them with Proxy handlers */
globalThis.__realUint8Array = Uint8Array;
globalThis.__realFloat64Array = Float64Array;

/* host-callable stubs that give enough shape for DOM+network probes */
globalThis.host_parse_html = function (html) {
  /* minimal JSON tree: one wrapping <div> with the raw html as a text child */
  return JSON.stringify([{
    t: "div", n: "div", a: {},
    c: [{ t: "text", x: String(html).slice(0, 80) }]
  }]);
};
globalThis.host_log = function (msg, level) {
  if (!globalThis.__smokeLogs) globalThis.__smokeLogs = [];
  globalThis.__smokeLogs.push({msg: String(msg || ""), level: String(level || "log")});
};
globalThis.host_log_one = function (msg) {
  if (!globalThis.__smokeLogsOne) globalThis.__smokeLogsOne = [];
  globalThis.__smokeLogsOne.push(String(msg || ""));
};
globalThis.host_get_cookie = function () { return "sid=42"; };
globalThis.host_set_cookie = function () {};
globalThis.host_crypto_digest = function (algo, data) { return "deadbeef"; };
globalThis.host_fetch = function () { return '{"status":200,"headers":{},"body":""}'; };
globalThis.host_post_message = function () {};
globalThis.host_ws_connect = function () { return "{}"; };
globalThis.host_ws_send = function () { return "{}"; };
globalThis.host_ws_send_binary = function () { return "{}"; };
globalThis.host_ws_close = function () { return "{}"; };
globalThis.host_load_module = function () { return ""; };
"""

# ---------------------------------------------------------------- probes (engine-agnostic)
PROBE_B = """
(function () {
  var a = new Uint8Array(4);
  a[2] = 7;
  a[10] = 99;
  var f = new Float64Array([1.5, 2.5]);
  return JSON.stringify({
    len: a.length, at2: a[2], at10: a[10] === undefined ? "undef" : String(a[10]),
    inst: a instanceof Uint8Array, ctor: a.constructor.name,
    f32: Math.round(f[0] * 100) / 100, reduce: a.reduce(function (x, y) { return x + y; }, 0),
  });
})()
"""

PROBE_D = """
(function () {
  globalThis.__dorder = [];
  Promise.resolve().then(function () { __dorder.push("promise"); });
  queueMicrotask(function () { __dorder.push("micro"); });
  setTimeout(function () { __dorder.push("t20"); }, 20);
  setTimeout(function () { __dorder.push("t150"); }, 150);
  return JSON.stringify(__dorder);
})()
"""

PROBE_E = """
(function () {
  var bytes = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]);
  var r = { present: typeof WebAssembly, validate: typeof WebAssembly.validate === "function" ? WebAssembly.validate(bytes) : "nofn",
            hasModuleCtor: typeof WebAssembly.Module };
  try {
    var m = new WebAssembly.Module(bytes);
    var inst = new WebAssembly.Instance(m, {});
    r.instance = "ok:" + Object.keys(inst.exports).length;
  } catch (e) { r.instance = "err:" + String(e && e.message).slice(0, 40); }
  return JSON.stringify(r);
})()
"""

PROBE_F = """
(function () {
  var d = document.createElement("div");
  d.innerHTML = '<ul id="l"><li>a</li><li>b</li></ul>';
  var lis = d.querySelectorAll("li");
  d.setAttribute("data-x", "1");
  var fired = 0;
  d.addEventListener("click", function () { fired++; });
  d.dispatchEvent(new Event("click"));
  var input = document.createElement("input");
  input.type = "text"; input.value = "hello";
  d.appendChild(input);
  return JSON.stringify({
    lis: lis.length, kids: d.childNodes.length, text: d.textContent.slice(0, 30),
    attr: d.getAttribute("data-x"), click: fired, inputVal: input.value,
    urlA: String(new URL("/x?a=1", "https://ex.com/b").searchParams.get("a")),
  });
})()
"""

PROBE_G = """
(function () {
  localStorage.setItem("k", "v");
  var got = localStorage.getItem("k");
  document.cookie = "sid=42; Path=/";
  var ck = document.cookie;
  var dig = host_crypto_digest("sha256", "00ff");
  return JSON.stringify({ ls: got, ck: (ck || "").slice(0, 20), dig: String(dig).slice(0, 8) });
})()
"""

# Native typed-array + WebAssembly: uses the pre-minidom constructors so the
# Proxy hack is bypassed. V8 should give native OOB semantics and a real
# WebAssembly.Module; QuickJS should give the minidom fake (no Module ctor).
PROBE_H = """
(function () {
  var R = globalThis.__realUint8Array;
  var a = new R(4); a[2] = 7; a[10] = 99;
  var bytes = new R([0, 97, 115, 109, 1, 0, 0, 0]);
  var r = { oob: a[10] === undefined ? "undef" : "val",
            hasRealWasm: typeof WebAssembly.Module === "function",
            inst: "n/a" };
  if (r.hasRealWasm) {
    try {
      var m = new WebAssembly.Module(bytes);
      var inst = new WebAssembly.Instance(m, {});
      r.inst = "ok:" + Object.keys(inst.exports).length;
      r.validate = WebAssembly.validate(bytes);
    } catch (e) { r.inst = "err:" + String(e && e.message).slice(0, 40); }
  } else {
    r.validate = typeof WebAssembly.validate === "function" ? WebAssembly.validate(bytes) : "nofn";
  }
  return JSON.stringify(r);
})()
"""


class QuickJSEngine:
    name = "quickjs"

    def __init__(self) -> None:
        self.ctx = Context()
        self.ctx.eval(STUBS)
        t0 = time.perf_counter()
        self.ctx.eval(MINIDOM_JS)
        self.boot_ms = (time.perf_counter() - t0) * 1000.0
        self.ctx.eval("globalThis.__load({t:\"document\", c:[]}, 'about:blank'); true")

    def eval(self, js: str) -> str:
        return str(self.ctx.eval(js))

    def advance(self, ms: int) -> None:
        self.ctx.eval(f"globalThis.__advanceTimerClock({int(ms)})")

    def pump(self) -> None:
        for _ in range(50):
            try:
                if not self.ctx.execute_pending_job():
                    break
            except Exception:
                break


class V8Engine:
    name = "v8"

    def __init__(self) -> None:
        self.ctx = MiniRacer()
        self.ctx.eval(STUBS)
        t0 = time.perf_counter()
        self.ctx.eval(MINIDOM_JS)
        self.boot_ms = (time.perf_counter() - t0) * 1000.0
        self.ctx.eval("globalThis.__load({t:\"document\", c:[]}, 'about:blank'); true")

    def eval(self, js: str) -> str:
        return str(self.ctx.eval(js))

    def advance(self, ms: int) -> None:
        self.ctx.eval(f"globalThis.__advanceTimerClock({int(ms)})")

    def pump(self) -> None:
        pass  # V8 drains microtasks at the end of each script run


def run(engine) -> dict:
    out = {}

    def probe(key, js):
        try:
            out[key] = engine.eval(js)
        except Exception as e:
            out[key] = f"THROW:{type(e).__name__}:{str(e)[:80]}"

    out["boot_ms"] = round(engine.boot_ms, 1)
    probe("A_boot", "JSON.stringify([typeof document, typeof window, typeof Node, typeof Event])")
    probe("B_typedarr", PROBE_B)

    # Arity: minidom picks 2-arg host when __WR_HOST_LOG_TAKES_LEVEL is true
    probe("C_log_2arg", "console.log('one','two'); 'done'")
    out["C_logs_2arg"] = engine.eval("JSON.stringify(globalThis.__smokeLogs || [])")

    probe("D1_inline", PROBE_D)
    engine.pump()
    probe("D2_after_pump", "JSON.stringify(globalThis.__dorder)")
    engine.advance(100)
    probe("D3_adv100", "JSON.stringify(globalThis.__dorder)")
    engine.advance(100)
    probe("D4_adv200", "JSON.stringify(globalThis.__dorder)")

    probe("E_wasm", PROBE_E)
    probe("F_dom", PROBE_F)
    probe("G_storage", PROBE_G)
    probe("H_native_ta_wasm", PROBE_H)
    return out


def qjs_arity_trap() -> str:
    """Demonstrate the QuickJS-specific deferred SystemError on host arity mismatch."""
    ctx = Context()
    ctx.add_callable("host_log_one", lambda msg: None)
    try:
        ctx.eval("try { host_log_one('a','b'); 'no-throw' } catch (e) { 'js-caught' }")
    except Exception as e:
        return f"immediate:{type(e).__name__}:{e}"
    try:
        ctx.eval("'next-eval'")
        return "no-deferred-error"
    except Exception as e:
        return f"deferred:{type(e).__name__}:{e}"


def main() -> int:
    qjs = run(QuickJSEngine())
    v8 = run(V8Engine())
    keys = [k for k in qjs if k != "boot_ms"]
    print(f"{'probe':<18} {'quickjs (oracle)':<42} {'v8':<42} match")
    print("-" * 108)
    diverges = []
    for k in keys:
        a, b = str(qjs[k]), str(v8[k])
        same = "==" if a == b else "!="
        if same == "!=":
            diverges.append(k)
        print(f"{k:<18} {a[:42]:<42} {b[:42]:<42} {same}")
    print("-" * 108)
    print(f"boot(minidom eval) quickjs={qjs['boot_ms']}ms  v8={v8['boot_ms']}ms")
    if diverges:
        print(f"divergences: {diverges}")
    else:
        print("divergences: none")
    print(f"\nquickjs arity-trap diagnostic: {qjs_arity_trap()}")
    print("\nraw JSON dump:")
    print(json.dumps({"quickjs": qjs, "v8": v8}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
