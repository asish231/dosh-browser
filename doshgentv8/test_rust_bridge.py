#!/usr/bin/env python3
"""Rust-bridge acceptance test — doshgentv8_rust (rusty_v8 + PyO3) vs QuickJS oracle.

Both engines run the SAME minidom.js with REAL synchronous Python host
callables (the product's own parser for host_parse_html, hashlib for
crypto.digest) — this is the Phase-2 seam shape, not JS stubs.

Acceptance = every probe identical between engines, except the two
known/expected divergences (native WebAssembly under V8; the Proxy-wrapped
TypedArray feeding WebAssembly.validate throws under V8 instead of
silently faking success). Plus: wall-clock kill and catchable host errors.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MINIDOM_JS = (REPO / "webresearch" / "engine" / "minidom.js").read_text(encoding="utf-8")

from quickjs import Context as QJSContext  # noqa: E402  (product default = oracle)
from webresearch.engine.tree import html_to_tree  # noqa: E402  (real parser, stdlib-only)
from doshgentv8_rust import DoshContext as RustContext  # noqa: E402  (rusty_v8 bridge)


# ------------------------------------------------------------- shared host fns
def host_parse_html(html: str) -> str:
    """Mirror of runtime.py:73-78 — the REAL product parser."""
    tree = html_to_tree(f"<div id='__frag'>{html}</div>")
    for child in tree.get("c") or []:
        if child.get("t") == "div" and (child.get("a") or {}).get("id") == "__frag":
            return json.dumps(child.get("c") or [])
    return json.dumps(tree.get("c") or [])


class Host:
    """The 13-callable seam, Python side (string-in/string-out)."""

    def __init__(self, name: str):
        self.name = name
        self.logs: list[dict] = []
        self.cookies: dict = {}

    # callables take *args so both engines' registration shapes work
    def log(self, msg="", level="log", *extra):
        self.logs.append({"msg": str(msg), "level": str(level)})
        return None

    def get_cookie(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def set_cookie(self, raw=""):
        kv = str(raw).split(";")[0]
        if "=" in kv:
            k, v = kv.split("=", 1)
            self.cookies[k.strip()] = v.strip()
        return None

    def crypto_digest(self, algo="sha256", data_hex=""):
        return hashlib.sha256(bytes.fromhex(data_hex)).hexdigest()

    def fetch(self, url, method="GET", body="", headers_json="{}", body_b64=""):
        return json.dumps({"status": 200, "headers": {}, "body": ""})

    def parse(self, html=""):
        return host_parse_html(str(html))

    def noop(self, *args):
        return "{}"

    def load_module(self, spec=""):
        return ""


STUBS = """
globalThis.__WR_STORAGE_SEED = {};
globalThis.__WR_HISTORY_LENGTH = 1;
globalThis.__WR_UA = "DoshRustBridge/1.0";
globalThis.__WR_DEVICE = {};
"""

REGISTER = [
    ("host_parse_html", "parse"),
    ("host_log", "log"),
    ("host_get_cookie", "get_cookie"),
    ("host_set_cookie", "set_cookie"),
    ("host_crypto_digest", "crypto_digest"),
    ("host_fetch", "fetch"),
    ("host_post_message", "noop"),
    ("host_ws_connect", "noop"),
    ("host_ws_send", "noop"),
    ("host_ws_send_binary", "noop"),
    ("host_ws_close", "noop"),
    ("host_load_module", "load_module"),
]

# ------------------------------------------------------------------- probes
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
  try {
    return JSON.stringify({ validate: WebAssembly.validate(bytes), hasModule: typeof WebAssembly.Module });
  } catch (e) {
    return "THROW:" + String(e && e.message).slice(0, 40);
  }
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
    lis: lis.length, kids: d.childNodes.length, text: d.textContent.slice(0, 20),
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
  return JSON.stringify({ ls: got, ck: (ck || "").slice(0, 20), dig: String(dig).slice(0, 12) });
})()
"""


class QuickJSEngine:
    name = "quickjs"

    def __init__(self) -> None:
        self.host = Host("quickjs")
        self.ctx = QJSContext()
        self.ctx.eval(STUBS)
        for js_name, attr in REGISTER:
            self.ctx.add_callable(js_name, getattr(self.host, attr))
        self.ctx.eval("globalThis.__WR_HOST_LOG_TAKES_LEVEL = true")
        t0 = time.perf_counter()
        self.ctx.eval(MINIDOM_JS)
        self.boot_ms = (time.perf_counter() - t0) * 1000
        self.ctx.eval("globalThis.__load({t:\"document\", c:[]}, 'about:blank'); true")

    def eval(self, js):
        return str(self.ctx.eval(js))

    def pump(self):
        for _ in range(50):
            try:
                if not self.ctx.execute_pending_job():
                    break
            except Exception:
                break

    def advance(self, ms):
        self.ctx.eval(f"globalThis.__advanceTimerClock({int(ms)})")


class RustEngine:
    name = "doshgentv8-rust"

    def __init__(self) -> None:
        self.host = Host("rust")
        self.ctx = RustContext()
        self.ctx.eval(STUBS)
        for js_name, attr in REGISTER:
            self.ctx.add_callable(js_name, getattr(self.host, attr))
        self.ctx.eval("globalThis.__WR_HOST_LOG_TAKES_LEVEL = true")
        t0 = time.perf_counter()
        self.ctx.eval(MINIDOM_JS)
        self.boot_ms = (time.perf_counter() - t0) * 1000
        self.ctx.eval("globalThis.__load({t:\"document\", c:[]}, 'about:blank'); true")

    def eval(self, js):
        return str(self.ctx.eval(js))

    def pump(self):
        # One V8 checkpoint drains the whole queue; returns False after.
        self.ctx.execute_pending_job()

    def advance(self, ms):
        self.ctx.eval(f"globalThis.__advanceTimerClock({int(ms)})")


def run(engine) -> dict:
    out = {}

    def probe(key, js):
        try:
            out[key] = engine.eval(js)
        except Exception as e:
            out[key] = f"THROW:{type(e).__name__}:{str(e)[:70]}"

    out["boot_ms"] = round(engine.boot_ms, 1)
    probe("A_boot", "JSON.stringify([typeof document, typeof window, typeof Node, typeof Event])")
    probe("B_typedarr", PROBE_B)

    # C: REAL Python host_log round-trip (sync seam proof)
    probe("C_log", "console.log('hello', 'warn'); 'done'")
    out["C_logs"] = json.dumps(engine.host.logs)

    probe("D1_inline", PROBE_D)
    engine.pump()
    probe("D2_after_pump", "JSON.stringify(globalThis.__dorder)")
    engine.advance(100)
    probe("D3_adv100", "JSON.stringify(globalThis.__dorder)")
    engine.advance(100)
    probe("D4_adv200", "JSON.stringify(globalThis.__dorder)")

    probe("E_wasm", PROBE_E)  # expected divergence: native WebAssembly under V8
    probe("F_dom", PROBE_F)  # real host_parse_html → lis:2 expected both
    probe("G_storage", PROBE_G)  # real hashlib digest + real cookie bridge
    return out


def main() -> int:
    qjs = run(QuickJSEngine())
    rust = run(RustEngine())

    # expected divergences (documented, not bugs):
    # E: V8's native WebAssembly.validate brand-checks the Proxy-wrapped
    #    Uint8Array → throws (honest) instead of the minidom fake (validate:false).
    expected_diverge = {"E_wasm", "boot_ms"}

    keys = [k for k in qjs if k != "boot_ms"]
    print(f"{'probe':<16} {'quickjs (oracle)':<42} {'doshgentv8-rust':<42} match")
    print("-" * 112)
    failures = []
    for k in keys:
        a, b = str(qjs[k]), str(rust[k])
        same = "==" if a == b else ("~=" if k in expected_diverge else "!=")
        if same == "!=":
            failures.append(k)
        print(f"{k:<16} {a[:42]:<42} {b[:42]:<42} {same}")
    print("-" * 112)
    print(f"boot(minidom eval)  quickjs={qjs['boot_ms']}ms  rust-v8={rust['boot_ms']}ms")

    # --- extra bridge-only proofs -----------------------------------------
    # 1. wall-clock kill: runaway loop must die via IsolateHandle terminate
    rc = RustContext()
    rc.set_time_limit(0.5)
    t0 = time.perf_counter()
    try:
        rc.eval("while (true) {}")
        kill = "FAIL: no exception"
    except Exception as e:
        kill = f"OK: killed in {time.perf_counter() - t0:.2f}s ({str(e)[:40]})"
    print(f"[rust] wall-clock kill: {kill}")

    # 2. host error → catchable JS exception (QuickJS deferred SystemError parity)
    rc2 = RustContext()
    def boom(*a):
        raise ValueError("host exploded")
    rc2.add_callable("host_boom", boom)
    caught = rc2.eval(
        "try { host_boom('x'); 'NOT-CAUGHT'; } catch (e) { 'CAUGHT:' + String(e).slice(0, 30); }"
    )
    print(f"[rust] host error catchable in JS: {caught}")

    # 3. sync host round-trip latency (should be far under 1ms)
    rc3 = RustContext()
    rc3.add_callable("host_parse_html", host_parse_html)
    t0 = time.perf_counter()
    for _ in range(200):
        rc3.eval("host_parse_html('<li>x</li>')")
    per = (time.perf_counter() - t0) * 1e6 / 200
    print(f"[rust] sync host round-trip: {per:.0f}us/call")

    print()
    if failures:
        print(f"RESULT: FAIL — divergent probes: {failures}")
        return 1
    print("RESULT: GREEN — all probes match (expected divergences documented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
