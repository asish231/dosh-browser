#!/usr/bin/env python3
"""Integration test — doshgentv8 running minidom.js (the product's DOM).

This is NOT a product engine yet — it runs behind `WR_JS_ENGINE=doshgentv8`
and must match QuickJS semantics on the existing test suite before it can
replace it.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "doshgentv8"))
sys.path.insert(0, str(REPO))

MINIDOM_JS = (REPO / "webresearch" / "engine" / "minidom.js").read_text(encoding="utf-8")

from doshgentv8 import DoshContext  # noqa: E402

STUBS = """
globalThis.__WR_STORAGE_SEED = {};
globalThis.__WR_HISTORY_LENGTH = 1;
globalThis.__WR_UA = "doshgentv8/0.1.0";
globalThis.__WR_DEVICE = {};

/* host-callable stubs for DOM+network probes */
globalThis.host_parse_html = function (html) {
  return JSON.stringify([{
    t: "div", n: "div", a: {},
    c: [{ t: "text", x: String(html).slice(0, 80) }]
  }]);
};
globalThis.host_log = function (msg, level) {
  if (!globalThis.__smokeLogs) globalThis.__smokeLogs = [];
  globalThis.__smokeLogs.push({msg: String(msg || ""), level: String(level || "log")});
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


def main() -> int:
    ctx = DoshContext()
    ctx.eval(STUBS)
    t0 = time.perf_counter()
    ctx.eval(MINIDOM_JS)
    boot_ms = (time.perf_counter() - t0) * 1000.0
    print(f"minidom.js eval: {boot_ms:.1f}ms")

    # Initialize the document
    ctx.eval("globalThis.__load({t:\"document\", c:[]}, 'about:blank'); true")

    # Quick smoke probes
    probes = {
        "boot": ctx.eval("JSON.stringify([typeof document, typeof window, typeof Node, typeof Event])"),
        "dom": ctx.eval("""
            (function () {
              var d = document.createElement("div");
              d.innerHTML = '<ul id="l"><li>a</li><li>b</li></ul>';
              var lis = d.querySelectorAll("li");
              d.setAttribute("data-x", "1");
              var fired = 0;
              d.addEventListener("click", function () { fired++; });
              d.dispatchEvent(new Event("click"));
              return JSON.stringify({
                lis: lis.length, kids: d.childNodes.length,
                attr: d.getAttribute("data-x"), click: fired,
              });
            })()
        """),
        "storage": ctx.eval("""
            (function () {
              localStorage.setItem("k", "v");
              var got = localStorage.getItem("k");
              document.cookie = "sid=42; Path=/";
              var ck = document.cookie;
              return JSON.stringify({ ls: got, ck: (ck || "").slice(0, 20) });
            })()
        """),
    }

    print(json.dumps(probes, indent=2))
    print("\ndoshgentv8 integration smoke: GREEN" if all(probes.values()) else "FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
