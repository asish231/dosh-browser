"""Docs-pass regressions: blob: URLs resolve locally and never leak to the
network lane.

Before the fix, fetch('blob:webresearch-1') built a malformed relative URL
('/webresearch-1') and hit the page's origin over HTTP — the registry is
in-process bookkeeping, so the request could never succeed and the leak
exposed engine internals to servers as traffic.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"

PAGE = """<html><body><div id="v">unset</div>
<script>
  var code = "marker-payload-123";
  var b = new Blob([code], {type: "text/plain"});
  var u = URL.createObjectURL(b);
  window.__u = u;
  fetch(u).then(function (r) { return r.text(); })
    .then(function (t) {
      document.getElementById('v').textContent = 'FETCHED:' + t;
      window.__ok = true;
    })
    .catch(function (e) { document.getElementById('v').textContent = 'ERR:' + e.message; });
  fetch("blob:webresearch-does-not-exist").catch(function (e) {
    window.__badBlob = String(e.message).slice(0, 40);
  });
  var xhr = new XMLHttpRequest();
  xhr.open("GET", u);
  xhr.onload = function () { window.__xhr = xhr.responseText; };
  xhr.send();
</script>
</body></html>"""


@pytest.mark.asyncio
async def test_fetch_blob_url_serves_registered_content():
    requests: list[str] = []

    def handler(req: httpx.Request):
        requests.append(str(req.url))
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_blob")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__ok") is True
    line = [l for l in session.html.splitlines() if 'id="v"' in l]
    assert "FETCHED:marker-payload-123" in (line[0] if line else "")


@pytest.mark.asyncio
async def test_unknown_blob_url_rejects_honestly():
    def handler(req: httpx.Request):
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_blob2")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    got = ctx.eval("window.__badBlob")
    assert got and "unknown blob URL" in got, got


@pytest.mark.asyncio
async def test_blob_never_reaches_the_network():
    requests: list[str] = []

    def handler(req: httpx.Request):
        requests.append(f"{req.method} {req.url}")
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_blob3")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    # pump deliveries
    await session.scroll(y=1, max_chars=20_000)
    assert ctx.eval("window.__xhr") == "marker-payload-123"
    assert requests == [f"GET {BASE}/"], (
        f"blob: URL leaked to the network lane: {requests}"
    )
