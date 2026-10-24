"""P0-1 regressions: URL.searchParams must be live over href/search.

The old URLImpl computed href as a plain data property at construction and
attached searchParams separately with no write-back, so every searchParams
mutation was silently dropped. Element.prototype.submit builds GET form URLs
through exactly that API, which made every engine-side GET form submit navigate
without its query string. All probes go through the real session/engine paths;
only the HTTP transport is a mock.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str] | None = None) -> httpx.AsyncClient:
    pages = pages or {}

    def handler(req: httpx.Request):
        body = pages.get(req.url.path)
        if body is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _session(client: httpx.AsyncClient) -> Session:
    return Session(client, "s_p0_url")


async def _ctx():
    """A live engine context on a minimal page (script tag keeps the ctx alive)."""
    session = _session(_client({"/": "<html><body><script>window.__ready=1;</script></body></html>"}))
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    return session, ctx


@pytest.mark.asyncio
async def test_searchparams_set_reflected_in_href():
    _, ctx = await _ctx()
    got = ctx.eval('var u = new URL("https://x.test/p"); u.searchParams.set("q","hi"); u.href')
    assert got == "https://x.test/p?q=hi", "searchParams.set must update href"


@pytest.mark.asyncio
async def test_searchparams_append_and_delete_reflected():
    _, ctx = await _ctx()
    assert ctx.eval(
        'var u = new URL("https://x.test/p"); u.searchParams.append("k","v"); u.href'
    ) == "https://x.test/p?k=v"
    assert ctx.eval(
        'var u = new URL("https://x.test/p?k=v&z=9"); u.searchParams.delete("k"); u.href'
    ) == "https://x.test/p?z=9"


@pytest.mark.asyncio
async def test_search_assignment_parses_into_searchparams():
    _, ctx = await _ctx()
    assert ctx.eval(
        'var u = new URL("https://x.test/p"); u.search = "?a=1&b=2"; u.href'
    ) == "https://x.test/p?a=1&b=2"
    assert ctx.eval(
        'var u = new URL("https://x.test/p?a=1&b=2"); u.searchParams.get("b")'
    ) == "2"


@pytest.mark.asyncio
async def test_search_assignment_keeps_searchparams_identity():
    """Code holding a searchParams reference must observe u.search = ..."""
    _, ctx = await _ctx()
    got = ctx.eval(
        'var u = new URL("https://x.test/p"); var sp = u.searchParams;'
        'u.search = "?n=1"; sp.get("n")'
    )
    assert got == "1"


@pytest.mark.asyncio
async def test_href_assignment_reparse():
    _, ctx = await _ctx()
    got = ctx.eval(
        'var u = new URL("https://x.test/p?old=1"); u.href = "https://y.test/z?c=3#h";'
        '[u.href, u.pathname, u.hash, u.searchParams.get("c")].join("|")'
    )
    assert got == "https://y.test/z?c=3#h|/z|#h|3"


@pytest.mark.asyncio
async def test_readonly_search_roundtrip():
    """A URL with a query parses back unchanged (decode/encode round-trip)."""
    _, ctx = await _ctx()
    assert ctx.eval('new URL("https://x.test/p?q=1&dup=2").search') == "?q=1&dup=2"
    assert ctx.eval('new URL("https://x.test/p").search') == ""


@pytest.mark.asyncio
async def test_get_form_submit_carries_query_string():
    """The P0 itself: session.submit on a GET form must send its fields.

    The engine's Element.prototype.submit builds the GET URL via
    searchParams.set(...); the session follows the resulting navigation, so the
    query must survive all the way onto the wire.
    """
    page = """<html><body>
<form action="/result" method="get">
  <input name="q" value="hello">
  <select name="plan"><option value="basic">Basic</option><option value="pro">Pro</option></select>
  <button type="submit">Send</button>
</form><script>window.__ready=1;</script></body></html>"""
    requests: list[tuple[str, str]] = []

    def handler(req: httpx.Request):
        requests.append((req.method, str(req.url)))
        if req.url.path == "/result":
            return httpx.Response(
                200, text="<html><body><h1>RESULT</h1></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = _session(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await session.goto(f"{BASE}/form", max_chars=20_000)
    await session.submit(1, {"plan": "pro", "q": "engines"}, max_chars=20_000)

    submitted = [url for method, url in requests if method == "GET" and "/result" in url]
    assert submitted, f"GET submit never fired; requests={requests}"
    assert "plan=pro" in submitted[0], f"plan lost from query: {submitted}"
    assert "q=engines" in submitted[0], f"q lost from query: {submitted}"
