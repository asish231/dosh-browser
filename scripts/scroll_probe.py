"""Focused scroll probe: absolute vs accumulating scroll on an infinite feed."""
from __future__ import annotations

import asyncio
import socketserver
import threading
import http.server
import httpx

from webresearch.session import Browser

PORT = 8973
BASE = f"http://127.0.0.1:{PORT}"

PAGE = """<!DOCTYPE html><html><head><title>Feed</title></head><body>
<h1>feed page</h1>
<div id="feed"><div class="item">item 1</div><div class="item">item 2</div><div class="item">item 3</div></div>
<div class="sentinel">bottom marker</div>
<script>
  var feed = document.getElementById("feed");
  new IntersectionObserver(function (ents) {
    ents.forEach(function (e) {
      if (e.isIntersecting) {
        var n = document.querySelectorAll(".item").length + 1;
        var d = document.createElement("div");
        d.className = "item"; d.textContent = "item " + n;
        feed.appendChild(d);
      }
    });
  }).observe(document.querySelector(".sentinel"));
</script>
</head></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def run():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        browser = Browser()
        session = await browser.create()
        session.client = httpx.AsyncClient()
        async def probe(tag: str):
            ctx = session.current_tab.ctx
            info = ctx.eval(
                "JSON.stringify({y: scrollY, vh: innerHeight, "
                "sent: (function(){var s=document.querySelector('.sentinel');"
                "if(!s) return 'none'; var r=s.getBoundingClientRect();"
                "return {t: r.top, b: r.bottom};})()})"
            )
            print(f"  {tag}: {info}")

        await session.goto(f"{BASE}/x", max_chars=80_000)
        await probe("after goto")

        print("Accumulating absolute y (emulating a delta wheel):", flush=True)
        y = 0
        for i in range(6):
            y += 400
            await session.scroll(y=y, max_chars=80_000)
            await probe(f"after scroll to {y}")

        await session.close()
        await browser.close_all()
    finally:
        httpd.shutdown()


asyncio.run(run())
