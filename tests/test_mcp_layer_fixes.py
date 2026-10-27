"""MCP-layer regressions from the 2026-10-24 end-to-end exercise: false captcha
banners, twin-form Enter-to-submit, list-container reading roots, the
example.com missing-prepend crash, real resource-timing entries, error
provenance, token accounting placement, and session_id uniformity.
"""

import httpx
import pytest

from webresearch.acd import build_acd
from webresearch.content import content_root
from webresearch.engine.inspect import inspect_html
from webresearch.engine.runtime import run_scripts
from webresearch.session import Session
from selectolax.lexbor import LexborHTMLParser

BASE = "https://test.local"


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                text=pages.get(req.url.path, pages.get("*", "<html><body>404</body></html>")),
                headers={"content-type": "text/html"},
            )
        )
    )


# ---------------------------------------------------------------------------
# mcp-a: false captcha banner


WIKI_LIKE = """<html><body>
<form action="/w/index.php" method="get"><input name="search"><button type="submit">S</button></form>
<script>mw.config.set({"wgConfirmEditHCaptchaSiteKey":"abc","wgUserName":null});</script>
</body></html>"""

REAL_HCAPTCHA = """<html><body>
<div class="h-captcha" data-sitekey="xxx"></div>
<script src="https://js.hcaptcha.com/1/api.js"></script>
</body></html>"""


def test_inline_config_blob_does_not_fire_challenge():
    targets, challenges = inspect_html(WIKI_LIKE, f"{BASE}/wiki")
    assert challenges == [], "inline config strings are not challenge evidence"


def test_real_widget_still_fires_challenge():
    targets, challenges = inspect_html(REAL_HCAPTCHA, f"{BASE}/hc")
    kinds = [c.kind for c in challenges]
    assert "hcaptcha" in kinds, kinds


@pytest.mark.asyncio
async def test_inspect_and_captcha_lanes_agree_on_quiet_page():
    session = Session(_client({"*": WIKI_LIKE}), "s_mcp_captcha")
    page = await session.goto(f"{BASE}/wiki", max_chars=20_000)
    assert "CHALLENGE" not in (page.view or "")


# ---------------------------------------------------------------------------
# mcp-b: twin-form Enter-to-submit


TWIN_FORMS = """<html><body>
<form id="fa" action="/searchA" method="get"><input name="q" id="qa" placeholder="search"></form>
<div style="height:400px"></div>
<form id="fb" action="/searchB" method="get"><input name="q" id="qb" placeholder="search"></form>
<script>window.__ready=1;</script>
</body></html>"""


@pytest.mark.asyncio
async def test_refs_survive_typing():
    """The ref is identity, not state: typing must not change it."""
    session = Session(_client({"*": TWIN_FORMS}), "s_mcp_twin")
    await session.goto(f"{BASE}/", max_chars=20_000)
    refs = [t.ref for t in inspect_html(session.html, session.url)[0]]
    ref_b = refs[1]
    await session.type(ref_b, "engines", max_chars=20_000)
    refs_after = [t.ref for t in inspect_html(session.html, session.url)[0]]
    assert ref_b in refs_after, f"typed field's ref changed: {refs} -> {refs_after}"


@pytest.mark.asyncio
async def test_enter_submits_the_typed_element_form_with_query():
    pages = {
        "*": TWIN_FORMS,
        "/searchA": "<html><body><h1>searchA results</h1></body></html>",
        "/searchB": "<html><body><h1>searchB results QUERY</h1></body></html>",
    }

    def handler(req: httpx.Request):
        body = pages.get(req.url.path, pages["*"])
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_mcp_twin2")
    await session.goto(f"{BASE}/", max_chars=20_000)
    refs = [t.ref for t in inspect_html(session.html, session.url)[0]]
    ref_b = refs[1]
    await session.type(ref_b, "engines", max_chars=20_000)
    await session.press_key(ref_b, "enter", max_chars=20_000)
    assert "searchB results" in session.html, "Enter must submit the typed element's form"
    assert "searchA" not in session.html, "the empty twin form must not win"


# ---------------------------------------------------------------------------
# mcp-c: list containers become the reading root


LIST_PAGE = """<html><body>
<main>
<nav><a href="/x">nav link</a></nav>
<article class="Box-row"><h2>repo one</h2><p>first description</p></article>
<article class="Box-row"><h2>repo two</h2><p>second description</p></article>
<article class="Box-row"><h2>repo three</h2><p>third description</p></article>
<article class="Box-row"><h2>repo four</h2><p>fourth description</p></article>
<article class="Box-row"><h2>repo five</h2><p>fifth description</p></article>
</main>
<footer><p>footer noise</p></footer>
</body></html>"""


def test_list_container_wins_over_single_row():
    tree = LexborHTMLParser(LIST_PAGE)
    root = content_root(tree)
    assert root is not None and root.tag == "main", (
        f"reading root must be the list container, got {root.tag if root else None}"
    )


def test_acd_extracts_all_list_rows():
    page_html = LIST_PAGE
    acd = build_acd(url=BASE, title="t", html=page_html)
    texts = " ".join(b.text or "" for b in acd.blocks)
    for i in range(1, 6):
        assert f"repo {'one two three four five'.split()[i-1]}" in texts, (
            f"row {i} missing from ACD blocks"
        )
    assert "footer noise" not in texts, "chrome must stay out of the reading projection"


# ---------------------------------------------------------------------------
# mcp-d: example.com's missing prepend + resource entries + provenance


@pytest.mark.asyncio
async def test_parent_node_insertion_methods():
    page = """<html><body><div id="wrap"><span id="first">first</span></div>
<script>
var w = document.getElementById('wrap');
w.prepend("zero-text", document.createElement('em'));
w.append("last-text");
var f = document.getElementById('first');
f.before(document.createTextNode('before-first'));
f.after("after-first");
window.__html = document.getElementById('wrap').textContent;
var r2 = document.getElementById('wrap');
r2.replaceChildren("replaced");
window.__replaced = r2.textContent;
</script></body></html>"""
    session = Session(_client({"*": page}), "s_mcp_prepend")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    assert "zero-text" in ctx.eval("window.__html")
    assert "last-text" in ctx.eval("window.__html")
    assert "before-first" in ctx.eval("window.__html")
    assert "after-first" in ctx.eval("window.__html")
    assert ctx.eval("window.__replaced") == "replaced"


@pytest.mark.asyncio
async def test_resource_timing_entries_are_real():
    """External scripts become real resource entries (real URL + size)."""
    script_body = "window.__sjs = 1;"

    def handler(req: httpx.Request):
        if req.url.path == "/s.js":
            return httpx.Response(200, text=script_body, headers={"content-type": "text/javascript"})
        page = '<html><body><div id="v">x</div><script src="/s.js"></script></body></html>'
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    session = Session(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "s_mcp_res")
    await session.goto(f"{BASE}/", max_chars=20_000)
    ctx = session.current_tab.ctx
    n = ctx.eval("performance.getEntriesByType('resource').length")
    assert n >= 1, "external script fetch must appear on the resource timeline"
    first = ctx.eval(
        "(function(){var e = performance.getEntriesByType('resource')[0];"
        "return e.name + '|' + e.transferSize + '|' + e.initiatorType;})()"
    )
    assert "/s.js" in first and str(len(script_body)) in first and "script" in first, first


@pytest.mark.asyncio
async def test_module_error_names_its_source():
    page = """<html><body>
<script type="module">window.__x = nope.nope.nope;</script>
</body></html>"""
    session = Session(_client({"*": page}), "s_mcp_prov")
    await session.goto(f"{BASE}/", max_chars=20_000)
    errs = session.error_log()
    assert errs, "the module error must be surfaced"
    assert "in module" in errs[0], f"error must name its module: {errs[0]}"


# ---------------------------------------------------------------------------
# mcp-e: token accounting placement


def test_token_account_header_survives_tail_truncation():
    from webresearch.mcp import McpServer

    server = McpServer()
    big = "x" * 9000
    result = server._format_result(big)
    text = result["content"][0]["text"]
    # The accounting must be at the TOP: an MCP client that truncates the
    # tail (where the old footer lived) still sees the numbers.
    assert text.startswith("[tokens:"), text[:60]
    assert "session total:" in text[:200]
    assert result["_meta"]["tokens"] > 0
    assert result["_meta"]["session_total_tokens"] == result["_meta"]["tokens"]


def test_token_totals_reconcile_across_calls():
    from webresearch.mcp import McpServer

    server = McpServer()
    first = server._format_result("a" * 100)
    second = server._format_result("b" * 300)
    summed = first["_meta"]["tokens"] + second["_meta"]["tokens"]
    assert second["_meta"]["session_total_tokens"] == summed, (
        "the session total must be exactly the sum of printed per-call numbers"
    )


# ---------------------------------------------------------------------------
# mcp-f: session_id uniformity


def test_session_id_optional_everywhere():
    from webresearch.mcp import TOOLS

    takes_it = [t for t in TOOLS if "session_id" in (t.get("inputSchema") or {}).get("properties", {})]
    assert takes_it, "session_id should still be accepted where it was"
    for tool in takes_it:
        req = (tool.get("inputSchema") or {}).get("required") or []
        assert "session_id" not in req, f"{tool['name']} must not require session_id"
    # every tool that accepts it documents the shared-session semantics
    for tool in takes_it:
        desc = tool["inputSchema"]["properties"]["session_id"].get("description", "")
        assert "shared session" in desc, tool["name"]
