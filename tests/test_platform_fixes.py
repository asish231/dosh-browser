"""Regression tests for the engine/platform bugs found in the dosh-browser session.

Every fixture below is shaped from *real captured markup*: the class names are
the ones the live pages actually shipped, because the bugs only reproduce with
them. Hand-written HTML that omits ``vector-toc-available`` proves nothing about
the chrome-overshoot bug.
"""

from __future__ import annotations

import asyncio
import unittest

import httpx

from webresearch.acd import acd_view, build_acd, error_summary
from webresearch.content import content_root, inside_chrome
from webresearch.engine.captcha import inspect_captcha
from webresearch.engine.runtime import MINIDOM_JS
from webresearch.fetch import FetchResult
from webresearch.parse import parse
from webresearch.session import Browser
from webresearch.snapshot import _spa_hint, snapshot
from webresearch.types import Page

# Verbatim from en.wikipedia.org/wiki/Web_browser: the root element carries
# feature-flag classes whose hyphen fragments collide with the chrome tokens
# (`vector-toc-available` -> `toc`), and the body carries the skin classes.
WIKIPEDIA_HTML_CLASS = (
    "client-nojs vector-feature-language-in-header-enabled "
    "vector-feature-language-in-main-menu-disabled "
    "vector-feature-language-in-main-page-header-disabled "
    "vector-feature-page-tools-pinned-disabled "
    "vector-feature-toc-pinned-clientpref-1 "
    "vector-feature-main-menu-pinned-disabled "
    "vector-feature-limited-width-clientpref-1 "
    "vector-feature-limited-width-content-enabled "
    "vector-feature-custom-font-size-clientpref-1 "
    "vector-feature-appearance-pinned-clientpref-1 "
    "skin-theme-clientpref-day vector-sticky-header-enabled "
    "vector-toc-available skin-thumbsize-clientpref-standard"
)

WIKIPEDIA_BODY_CLASS = (
    "skin--responsive skin-vector skin-vector-search-vue mediawiki ltr "
    "sitedir-ltr mw-hide-empty-elt ns-0 ns-subject page-Web_browser "
    "rootpage-Web_browser skin-vector-2022 action-view"
)

# The real page nests the article 9+ levels below <html>: html > body >
# div.vector-header-container > div.mw-page-container > div.mw-page-container-inner
# > div.mw-content-container > main#content > div#mw-content-text >
# div.mw-parser-output > section > p
WIKIPEDIA_ARTICLE = f"""<!doctype html>
<html class="{WIKIPEDIA_HTML_CLASS}" lang="en" dir="ltr">
<body class="{WIKIPEDIA_BODY_CLASS}">
<div class="vector-header-container">
  <nav class="vector-menu"><ul><li><a href="/wiki/Main_Page">Main page</a></li></ul></nav>
</div>
<div class="mw-page-container">
  <div class="mw-page-container-inner">
    <div class="mw-content-container">
      <main id="content" class="mw-body" role="main">
        <div id="mw-content-text">
          <div class="mw-parser-output">
            <section>
              <header><h2>Function</h2></header>
              <p>A web browser fetches pages over HTTP and shows them.</p>
            </section>
            <section>
              <h2>History</h2>
              <p>The first widely used browser was Mosaic.</p>
              <div id="toc" class="toc"><ul><li><a href="#Function">Function</a></li></ul></div>
              <ol class="references"><li id="cite_note-1"><a href="#cite_ref-1">^</a> Source</li></ol>
            </section>
          </div>
        </div>
      </main>
    </div>
  </div>
</div>
</body>
</html>"""

# Verbatim shape from deepmind.google/models/model-cards/gemini-3-8-flash/:
# a small promotional <article> card for *another* model precedes the document's
# much larger <main id="page-content"> holding the actual model card.
DEEPMIND_MODEL_CARD = """<!doctype html>
<html class="deepai" data-gtm-id="GTM-NRDHXJGK" lang="en">
<head><title>Gemini 3.8 Flash - Model Card</title></head>
<body>
<div class="site-switcher"><nav class="site-switcher__nav"><a href="/">Home</a></nav></div>
<article class="card card-blog card--medium card--is-link">
  <div class="card__inner">
    <a class="card__overlay-link" href="/models/model-cards/nano-banana-2-1/"></a>
    <div class="card__content">
      <h3 class="heading-6 card__title">Nano Banana 2.1</h3>
      <div class="meta"><div class="meta__cta"><a class="button" href="/models/model-cards/nano-banana-2-1/">Learn more</a></div></div>
    </div>
  </div>
</article>
<main id="page-content">
  <section class="section-cover" id="cover">
    <div class="cover">
      <div class="cover__content">
        <div class="cover__text">
          <span class="cover__text--eyebrow">Published 2 September 2026</span>
          <h1 class="cover__text--title">Gemini 3.8 Flash</h1>
        </div>
      </div>
    </div>
  </section>
  <section class="grid section-default">
    <div class="rich-text"><h2>Model Information</h2></div>
    <div class="rich-text"><h3>Description</h3>
      <p>Gemini 3.8 Flash is the next iteration in the Gemini 3 model family,
      building on Gemini 3.7 Flash, delivering performance advancements across
      software engineering and agentic knowledge workflows. It continues to
      support customizable effort levels to control the mix of quality, cost
      and latency.</p>
    </div>
    <div class="rich-text"><h3>Outputs</h3>
      <p>Text, with a 64K token output.</p>
    </div>
    <div class="rich-text"><h3>Architecture</h3>
      <p>Gemini 3.8 Flash is based on Gemini 3.7 Flash. For more information
      about the model architecture for Gemini 3.8 Flash, see the Gemini 3.7
      Flash model card.</p>
    </div>
    <div class="rich-text"><h2>Evaluation</h2></div>
    <div class="rich-text"><h3>Results</h3>
      <p>Results as of September, 2026 are listed below:</p>
      <table>
        <tr><th>Benchmark</th><th>Gemini 3.8 Flash</th><th>Claude Opus 5</th></tr>
        <tr><td>DeepSWE v1.1</td><td>73.7%</td><td>74.0%</td></tr>
        <tr><td>Vals Finance Agent v2</td><td>61.4%</td><td>58.6%</td></tr>
      </table>
    </div>
  </section>
</main>
</body>
</html>"""

SPA_SHELL = """<!doctype html><html><head><title>App</title></head>
<body><div id="root"></div>
<script>var a = "%s";</script>
<script>var b = "%s";</script>
<script>var c = "%s";</script>
<script>var d = "%s";</script>
<script>var e = "%s";</script>
<script>var f = "%s";</script>
</body></html>""" % ("x" * 900, "y" * 900, "z" * 900, "w" * 900, "v" * 900, "u" * 900)

SHORT_ARTICLE_WITH_SCRIPTS = """<!doctype html><html><head><title>Note</title></head>
<body><main><article>
<h1>A short article</h1>
<p>Still an article.</p>
<p>Real content, not a shell.</p>
</article></main>
<script>var cfg = 1;</script><script>var cfg2 = 2;</script><script>var cfg3 = 3;</script>
<script>var cfg4 = 4;</script><script>var cfg5 = 5;</script>
</body></html>"""

# Verbatim from en.wikipedia.org/wiki/Web_browser: the config blob names
# hCaptcha to gate *anonymous edits*, and the page renders no challenge.
WIKIPEDIA_EDIT_CONFIG = """<!doctype html>
<html class="client-nojs skin-vector-2022"><body class="mediawiki page-Web_browser">
<script>window.RLQ = window.RLQ || [];</script>
<script>"wgFlaggedRevsParams":{"tags":{"status":{"levels":1}},"namespaces":[0,4]},
"wgConfirmEditCaptchaNeededForGenericEdit":"hcaptcha",
"wgConfirmEditForceShowCaptcha":false,
"wgConfirmEditHCaptchaSiteKey":"5d0c670e-a5f4-4258-ad16-1f42792c9c62"</script>
<main id="content"><div id="mw-content-text"><div class="mw-parser-output">
<p>Browsers are used on a range of devices, including desktops, laptops, tablets.</p>
</div></div></main>
</body></html>"""

RECAPTCHA_WIDGET = """<!doctype html><html><body>
<div class="g-recaptcha" data-sitekey="site-key-123" data-callback="done"></div>
<script src="https://www.google.com/recaptcha/api.js" async defer></script>
</body></html>"""


def _doc(html: str, url: str = "https://example.com/x") -> object:
    result = FetchResult(
        url=url,
        final_url=url,
        status=200,
        content_type="text/html",
        kind="html",
        body=html,
    )
    return parse(result)


def _captcha(html: str, url: str = "https://example.com/x"):
    async def go():
        async with httpx.AsyncClient() as client:
            return await inspect_captcha(html, url, client, fetch_sprite=False)

    return asyncio.run(go())


class ChromeFilterTests(unittest.TestCase):
    """Bug 1: inside_chrome() overshooting the content root."""

    def test_html_feature_flag_classes_do_not_veto_article(self):
        doc = _doc(WIKIPEDIA_ARTICLE, "https://en.wikipedia.org/wiki/Web_browser")
        page = snapshot(doc)
        # Read the specific elements, never a substring of the whole page.
        self.assertIn("A web browser fetches pages over HTTP", page.view)
        self.assertIn("The first widely used browser was Mosaic", page.view)
        self.assertIn("## Function", page.view)
        # Real chrome is still dropped.
        self.assertNotIn("Main page", page.view)
        self.assertNotIn("Source", page.view)
        self.assertFalse(page.spa_hint)

    def test_selectolax_wrappers_are_not_identical_but_are_equal(self):
        """The `stop` boundary must survive fresh wrapper objects."""
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser("<main><div><p>hi</p></div></main>")
        root = tree.css_first("main")
        para = tree.css_first("p")
        # Two wrappers over the same node: `is` fails, `==` holds.
        self.assertIsNot(root, tree.css_first("main"))
        self.assertEqual(root, tree.css_first("main"))
        self.assertFalse(inside_chrome(para, stop=root))

    def test_stop_boundary_ignores_chrome_above_it(self):
        """A chrome ancestor above `stop` must not condemn a node below it."""
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser(
            '<aside class="sidebar"><div id="inner"><p>deep</p></div></aside>'
        )
        stop = tree.css_first("#inner")
        para = tree.css_first("p")
        self.assertTrue(inside_chrome(para, stop=None))
        self.assertFalse(inside_chrome(para, stop=stop))

    def test_class_tokens_are_matched_not_substringed(self):
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser('<main><div class="sliderContainer"><p>x</p></div></main>')
        root = tree.css_first("main")
        node = tree.css_first(".sliderContainer")
        # `sliderContainer` is not a chrome class, and no chrome ancestor exists.
        self.assertFalse(inside_chrome(node, stop=root))


class ContentRootTests(unittest.TestCase):
    """Bug 3: the first <article> shadowing the document's real content."""

    def test_deepmind_model_card_is_not_shadowed_by_the_promo_card(self):
        doc = _doc(DEEPMIND_MODEL_CARD, "https://deepmind.google/models/model-cards/")
        page = snapshot(doc)
        self.assertIn("Gemini 3.8 Flash", page.view)
        self.assertIn("Model Information", page.view)
        self.assertIn("next iteration in the Gemini 3 model family", page.view)
        self.assertIn("64K token output", page.view)
        self.assertIn("| DeepSWE v1.1 | 73.7% | 74.0% |", page.view)
        # The promo card is a sibling of the landmark, so it must not be what
        # the reading projection returns in place of the model card.
        self.assertNotIn("Nano Banana", page.view)
        self.assertNotIn("Learn more", page.view)
        # And it must not displace the card in the structured projection either.
        blocks = [b.text for b in build_acd(url=page.url, title="", html=DEEPMIND_MODEL_CARD).blocks]
        self.assertTrue(any("next iteration in the Gemini 3 model family" in t for t in blocks))
        self.assertFalse(any("Nano Banana" in t for t in blocks))

    def test_content_root_prefers_the_landmark_with_most_content(self):
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser(DEEPMIND_MODEL_CARD)
        root = content_root(tree)
        self.assertEqual((root.tag or "").lower(), "main")
        self.assertEqual(root.attributes.get("id"), "page-content")


class SpaHintTests(unittest.TestCase):
    """Bug 4: a static article mislabelled script-heavy."""

    def test_static_article_with_scripts_is_not_script_heavy(self):
        doc = _doc(SHORT_ARTICLE_WITH_SCRIPTS)
        page = snapshot(doc)
        self.assertFalse(page.spa_hint)
        self.assertNotIn("script-heavy", page.view)

    def test_empty_mount_shell_is_script_heavy(self):
        doc = _doc(SPA_SHELL)
        view = "<div>loading</div>"
        self.assertTrue(_spa_hint(doc, view))

    def test_wikipedia_page_is_not_script_heavy(self):
        doc = _doc(WIKIPEDIA_ARTICLE, "https://en.wikipedia.org/wiki/Web_browser")
        page = snapshot(doc)
        self.assertFalse(page.spa_hint)


class CaptchaFalsePositiveTests(unittest.TestCase):
    """Bug 2: page configuration reported as a live challenge."""

    def test_edit_only_hcaptcha_config_is_not_a_challenge(self):
        report = _captcha(WIKIPEDIA_EDIT_CONFIG, "https://en.wikipedia.org/wiki/Web_browser").report
        self.assertFalse(report.present)
        self.assertEqual(report.kind, "")

    def test_real_widget_is_still_detected(self):
        report = _captcha(RECAPTCHA_WIDGET).report
        self.assertTrue(report.present)
        self.assertEqual(report.kind, "recaptcha")
        self.assertEqual(report.site_key, "site-key-123")

    def test_provider_name_inside_inline_script_is_not_evidence(self):
        from webresearch.engine.captcha import _markup_only

        stripped = _markup_only(WIKIPEDIA_EDIT_CONFIG)
        self.assertNotIn("hcaptcha", stripped.lower())
        # The <script> tag itself survives, so a real widget script still counts.
        self.assertIn("<script", stripped)


class JsErrorSurfacingTests(unittest.TestCase):
    """Bug 5: Mini-DOM errors counted but never shown to the agent."""

    def test_error_summary_groups_distinct_classes(self):
        summary = error_summary(
            ["TypeError: cannot read property 'length' of undefined"] * 5
            + ["ReferenceError: $ is not defined"] * 2
        )
        self.assertIn("JS_ERRORS: 7 error(s), 2 distinct", summary)
        self.assertIn("TypeError: cannot read property 'length' of undefined (x5)", summary)
        self.assertIn("ReferenceError: $ is not defined (x2)", summary)

    def test_single_error_is_not_labelled_distinct(self):
        self.assertIn("1 distinct", error_summary(["TypeError: boom"]))

    def test_acd_view_surfs_errors_instead_of_a_dead_pointer(self):
        acd = build_acd(
            url="https://example.com",
            title="t",
            html="<main><p>x</p></main>",
            js_info={"error_messages": ["TypeError: boom", "TypeError: boom"]},
        )
        view = acd_view(acd)
        self.assertIn("JS_ERRORS: 2 error(s), 1 distinct", view)
        self.assertNotIn("see acd.errors", view)


class MinidIntegrityTests(unittest.TestCase):
    """Bug 6: the Mini-DOM source itself must not be silently broken."""

    def test_minidom_evaluates_in_a_fresh_context(self):
        import quickjs

        ctx = quickjs.Context()
        try:
            ctx.eval(MINIDOM_JS)
        except Exception as exc:  # pragma: no cover - only fires when broken
            self.fail(f"minidom.js failed to parse: {exc}")

    def test_minidom_installs_core_globals(self):
        import quickjs

        ctx = quickjs.Context()
        ctx.eval(MINIDOM_JS)
        ctx.eval(
            '__load({"t":"document","c":[{"t":"html","a":{},"c":'
            '[{"t":"body","a":{},"c":[]}]}]}, "https://example.com/")'
        )
        for probe in (
            "typeof document",
            "typeof performance.mark",
            "typeof performance.now",
            "typeof navigator",
            "typeof sessionStorage",
        ):
            self.assertIn(
                ctx.eval(probe),
                ("object", "function"),
                f"minidom did not install {probe}",
            )


class ApiSurfaceTests(unittest.TestCase):
    """Bug 7: fields and entry points that were undiscoverable."""

    def test_page_exposes_final_url(self):
        page = Page(url="https://example.com/a", status=200)
        self.assertEqual(page.final_url, "")
        page = Page(url="https://example.com/a", final_url="https://example.com/b", status=200)
        self.assertEqual(page.final_url, "https://example.com/b")

    def test_snapshot_populates_final_url(self):
        page = snapshot(_doc("<main><p>x</p></main>", "https://example.com/a"))
        self.assertEqual(page.final_url, "https://example.com/a")

    def test_parse_accepts_a_raw_httpx_response(self):
        response = httpx.Response(
            200,
            text="<html><body><main><p>Hi</p></main></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=httpx.Request("GET", "https://example.com/raw"),
        )
        doc = parse(response)
        self.assertEqual(doc.status, 200)
        self.assertEqual(doc.url, "https://example.com/raw")
        self.assertEqual(doc.kind, "html")
        self.assertIsNotNone(doc.tree)
        self.assertIn("Hi", snapshot(doc).view)

    def test_parse_substitutes_a_supplied_url(self):
        response = httpx.Response(
            200,
            text="<html><body><p>Hi</p></body></html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "https://example.com/raw"),
        )
        from webresearch.fetch import response_to_result

        result = response_to_result(response, url="https://example.com/decoy")
        self.assertEqual(result.url, "https://example.com/raw")

    def test_browser_is_awaitable_as_session_shorthand(self):
        async def go():
            browser = Browser()
            session = await browser
            try:
                self.assertTrue(session.client is not None)
            finally:
                await session.close()

        asyncio.run(go())


class TorTransportTests(unittest.TestCase):
    """Bug 8: .onion refused by design — the refusal must stay explicit."""

    @staticmethod
    def _valid_v3_onion() -> str:
        """Build a well-formed v3 address the way tor itself does."""
        import base64
        import hashlib

        pubkey = bytes(range(32))
        checksum = hashlib.sha3_256(b".onion checksum" + pubkey + b"\x03").digest()[:2]
        return (
            base64.b32encode(pubkey + checksum + b"\x03").decode().lower().rstrip("=") + ".onion"
        )

    def test_onion_refusal_names_the_missing_transport(self):
        from webresearch.engine.onion import onion_refusal

        status, reason = onion_refusal(self._valid_v3_onion())
        self.assertEqual(status, 403)
        self.assertIn("requires a Tor transport", reason)
        self.assertIn("refusing to resolve", reason)

    def test_malformed_onion_is_rejected_as_invalid(self):
        from webresearch.engine.onion import onion_refusal

        status, reason = onion_refusal("not-a-real-onion-address.onion")
        self.assertEqual(status, 403)
        self.assertIn("invalid .onion address", reason)


if __name__ == "__main__":
    unittest.main()
