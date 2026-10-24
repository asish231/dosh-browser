"""End-to-end tab-surfing check: link/button navigation, repeated scrolling,
fetch API, window.open popups, multi-tab acts — against a real local HTTP server
through the actual engine (no mocks).

The lazy-feed page uses realistic batches: each append is tall enough to push
the IntersectionObserver sentinel below the 720px viewport, so repeated wheel
scrolls keep re-triggering it (same as a real infinite feed).
"""
from __future__ import annotations

import asyncio
import socketserver
import threading
import http.server
import sys

import httpx

from webresearch.session import Browser

PORT = 8971
BASE = f"http://127.0.0.1:{PORT}"

PAD = "<br>".join(f"line {i}" for i in range(1, 45))

# Page A: nav links, a plain link to B, a button whose JS fetches /b and
# rewrites the DOM, a button opening a popup, and an IntersectionObserver
# infinite feed whose sentinel re-enters the viewport after each batch.
PAGE_A = f"""<!DOCTYPE html><html><head><title>Page A</title></head><body>
<nav><a href="/a" title="Home">A home</a> <a href="/c" title="Go C">nav to C</a></nav>
<h1>alpha heading</h1>
<a href="/b" id="link-b">read page B</a>
<button id="jsfetch" title="Fetch B now">fetch item</button>
<button id="openb" title="Open B tab">popup item</button>
<div id="feed"><div class="item">item 1</div><div class="item">item 2</div><div class="item">item 3</div></div>
<div class="sentinel" id="sent">bottom marker</div>
<script>
  document.getElementById("jsfetch").addEventListener("click", function () {{
    fetch("/b").then(function (r) {{ return r.text(); }})
      .then(function (t) {{
        document.getElementById("feed").insertAdjacentHTML("beforeend",
          "<div id='jsin'>JSF:" + t.length + "</div>");
      }});
  }});
  document.getElementById("openb").addEventListener("click", function () {{
    window.open("/b", "bpopup");
  }});
  var feed = document.getElementById("feed");
  try {{
    new IntersectionObserver(function (ents) {{
      ents.forEach(function (e) {{
        if (e.isIntersecting) {{
          for (var k = 0; k < 4; k++) {{
            var n = document.querySelectorAll(".item").length + 1;
            var d = document.createElement("div");
            d.className = "item";
            var txt = "item " + n;
            for (var w = 0; w < 60; w++) txt += " wrapwrapwrapwrapwrapwrapwrap";
            d.textContent = txt;
            feed.appendChild(d);
          }}
        }}
      }});
    }}).observe(document.getElementById("sent"));
  }} catch (ioerr) {{
    document.body.setAttribute("data-ioerr", "cb:" + String(ioerr));
  }}
</script>
</body></html>"""

PAGE_B = """<!DOCTYPE html><html><head><title>Page B</title></head><body>
<h1>beta heading</h1><p>b body text</p><a href="/a" id="back">back to A</a>
</body></html>"""

PAGE_C = """<!DOCTYPE html><html><head><title>Page C</title></head><body>
<h1>gamma heading</h1><a href="/a" id="backc">back to A from C</a>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def _serve(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/a"):
            self._serve(PAGE_A.encode(), "text/html")
        elif self.path.startswith("/b"):
            self._serve(PAGE_B.encode(), "text/html")
        elif self.path.startswith("/c"):
            self._serve(PAGE_C.encode(), "text/html")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def main() -> int:
    fails: list[str] = []
    socketserver.TCPServer.allow_reuse_address = True

    def check(name: str, cond: bool, detail: str = ""):
        print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(name)

    async def run():
        httpd = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            browser = Browser()
            session = await browser.create()
            session.client = httpx.AsyncClient()
            await session.goto(f"{BASE}/a", max_chars=80_000)
            print("  engine errors after load:", session.error_log()[:5])

            # 1. Plain link click: same-tab navigation, history grows.
            surf = session.action_surface(max_actions=200)
            link = next(a for a in surf.actions if a.kind == "navigate" and a.target.endswith("/b"))
            await session.act(link.ref, max_chars=80_000)
            check("click link -> page B loads in same tab", "beta heading" in session.html)
            check("active tab unchanged", session.active_tab_id == "tab_1")
            check("history has 2 entries", len(session.history) == 2, f"len={len(session.history)}")

            # 2. back() must restore the live DOM of the previous entry.
            page_a = session.back()
            check("browser back returns to A", "alpha heading" in (page_a.view or ""))
            check("back() restores live DOM to A", "alpha heading" in session.html)
            surf = session.action_surface(max_actions=200)
            check("action surface rebuilt from restored A (has C link)",
                  any(a.kind == "navigate" and a.target.endswith("/c") for a in surf.actions))

            # forward again, then back — round trip keeps the engine consistent.
            page_b = session.forward()
            check("forward() returns to B and restores its DOM", "beta heading" in session.html)
            session.back()

            # 3. JS fetch button: fetch API + DOM update in same tab.
            surf = session.action_surface(max_actions=200)
            btn = next(a for a in surf.actions if a.label and "Fetch B now" in a.label)
            await session.act(btn.ref, max_chars=80_000)
            check("page JS fetch() button runs in the engine", "JSF:" in session.html)

            # 4. Repeated scrolling with realistic lazy batches.
            for _ in range(5):
                await session.scroll(y=800, max_chars=80_000)
            count = session.html.count('class="item"')  # exact token match, no prefix games
            check("repeated wheel scrolls grow the feed (>=9)", count >= 9, f"items={count}")
            check("scroll still on page A", "alpha heading" in session.html)

            # 5. window.open popup becomes a real tab; agent stays put.
            surf = session.action_surface(max_actions=200)
            pop = next(a for a in surf.actions if a.label and "Open B tab" in a.label)
            await session.act(pop.ref, max_chars=80_000)
            tabs = session.tabs
            check("window.open created tab 2", len(tabs) == 2, f"tabs={[t['id'] for t in tabs]}")
            check("agent stayed on tab_1", session.active_tab_id == "tab_1")
            helper = next(t for t in tabs if t["id"] != "tab_1")
            check("tab 2 shows page B", helper["title"] == "Page B", str(helper))

            # 6. Cross-tab: act in tab 2, return to tab 1 state intact.
            session.switch_tab(helper["id"])
            check("switch to tab 2 shows B content", "beta heading" in session.html)
            surf = session.action_surface(max_actions=200)
            back = next(a for a in surf.actions if a.kind == "navigate" and a.target.endswith("/a"))
            await session.act(back.ref, max_chars=80_000)
            check("navigate within tab 2 works", "alpha heading" in session.html)
            session.switch_tab("tab_1")
            check("tab 1 kept its DOM + JS state after the round trip",
                  "JSF:" in session.html and "alpha heading" in session.html)

            # 7. Close tabs; closing the LAST tab must honestly refuse.
            session.close_tab("tab_2")
            try:
                session.close_tab("tab_1")
                check("cannot close the only open tab", False)
            except ValueError:
                check("cannot close the only open tab", True)

            # 8. Flurry: back-to-back navigations keep the engine alive.
            ok_seq = True
            for _ in range(3):
                await session.goto(f"{BASE}/b", max_chars=20_000)
                if "beta heading" not in session.html:
                    ok_seq = False
                await session.goto(f"{BASE}/c", max_chars=20_000)
                if "gamma heading" not in session.html:
                    ok_seq = False
                await session.goto(f"{BASE}/a", max_chars=80_000)
                if "alpha heading" not in session.html:
                    ok_seq = False
            check("6 back-to-back navigations keep working", ok_seq)

            # 9. Scrolling still functional after the flurry (fresh ctx each time).
            for _ in range(3):
                await session.scroll(y=800, max_chars=80_000)
            cnt2 = session.html.count('class="item"')
            check("scroll works again after repeated navigations", cnt2 >= 7, f"items={cnt2}")

            # 10. back() after the flurry lands on page C, restored and actionable.
            session.back()
            check("back() after flurry lands on C with live DOM", "gamma heading" in session.html)
            surf = session.action_surface(max_actions=200)
            check("C's action surface is usable after back()",
                  any(a.kind == "navigate" and a.target.endswith("/a") for a in surf.actions))

            await session.close()
            await browser.close_all()
        finally:
            httpd.shutdown()

    asyncio.run(run())
    print("\n" + ("ALL CHECKS PASSED" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
