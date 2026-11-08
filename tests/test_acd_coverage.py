"""Regression tests for the ACD coverage fixes.

Pinned defects (all reproduced against the live code before fixing):
1. BLOCK_SEL missed itemprop text (schema.org text/articleBody/headline/
   caption/abstract) and all div/span card content — the region fallback
   pass recovers cards whose content no semantic tag carries.
2. max_blocks truncation was completely silent — blocks_total + BLOCKS line
   + UNAVAILABLE entries now disclose it, plus chrome-skipped candidates.
3. slice_acd silently returned the FIRST blocks on an unknown id — a
   confident wrong answer; now an explicit ValueError.
4. JSON-LD Product was stored but never projected into blocks (README
   claimed it was).
5. The control realm's reading filter dropped code and table blocks.
6. Stale disclosure claimed drag was "heuristic without a layout engine".
"""

import json
import unittest
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from webresearch.acd import (
    BLOCK_SEL,
    REGION_MIN_CHARS,
    acd_view,
    build_acd,
    slice_acd,
)

FIXTURE = Path(__file__).parent / "fixtures" / "quotes_toscrape.html"
# Real captured markup: quotes.toscrape.com, 10 quote cards, 10
# itemprop="text" spans, captured live 2026-11-03 (11021 bytes).
CARDS_HTML = """<!doctype html><html><body>
<div class="cards">
  <div class="card">Card alpha carries its whole body as div text with no
  semantic tag anywhere inside it, long enough to clear the region floor</div>
  <div class="card">Card beta also carries its whole body as div text with
  no paragraph or heading inside, also beyond the region floor</div>
</div>
<div class="item"><p>semantic text inside a region wrapper</p></div>
<p>only real paragraph</p>
</body></html>"""


def _acd(html: str, **kw):
    return build_acd(url="https://example.com/", title="t", html=html, **kw)


class QuotesFixtureTests(unittest.TestCase):
    """The defect report's reproduction page, as a hermetic fixture."""

    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE.read_text(encoding="utf-8")
        cls.acd = _acd(cls.html, links=[])

    def test_quotes_page_content_is_now_projected(self):
        texts = " ".join(b.text for b in self.acd.blocks if b.text)
        self.assertIn("The world as we have created it", texts)
        self.assertGreaterEqual(len([b for b in self.acd.blocks if b.text]), 10)

    def test_quote_blocks_come_from_itemprop_text(self):
        quote_blocks = [b for b in self.acd.blocks if "world as we" in (b.text or "")]
        self.assertGreaterEqual(len(quote_blocks), 1)

    def test_blocks_total_is_present_and_honest(self):
        self.assertIn("blocks_total", self.acd.counts)
        self.assertGreater(self.acd.counts["blocks_total"], 0)
        self.assertLessEqual(self.acd.counts["blocks"], self.acd.counts["blocks_total"])

    def test_no_double_counted_cards(self):
        # Each quote's text appears exactly once in the text blocks (the
        # itemprop span carries it; the wrapper card must not repeat it).
        texts = [b.text for b in self.acd.blocks if b.text]
        for text in texts:
            self.assertEqual(texts.count(text), 1, text[:60])


class RegionFallbackTests(unittest.TestCase):
    def test_div_card_content_is_recovered(self):
        acd = _acd(CARDS_HTML)
        texts = [b.text for b in acd.blocks if b.text]
        self.assertTrue(any("Card alpha" in t for t in texts), texts)
        self.assertTrue(any("Card beta" in t for t in texts), texts)

    def test_regions_with_semantic_content_do_not_double_count(self):
        acd = _acd(CARDS_HTML)
        texts = [b.text for b in acd.blocks if b.text]
        # the wrapper region around the <p> is skipped: its content is the
        # p block, and re-projecting the wrapper would duplicate the text
        self.assertEqual(sum("semantic text inside" in t for t in texts), 1)
        # the plain paragraph is still there exactly once
        self.assertEqual(sum(t == "only real paragraph" for t in texts), 1)

    def test_region_floor_excludes_junk(self):
        html = '<html><body><div class="item">tiny label</div></body></html>'
        acd = _acd(html)
        self.assertEqual([b for b in acd.blocks if b.text], [])
        self.assertGreaterEqual(REGION_MIN_CHARS, 40)

    def test_blocks_total_counts_regions(self):
        acd = _acd(CARDS_HTML)
        # 2 cards + 1 semantic p + the p inside the region wrapper = candidates
        self.assertGreaterEqual(acd.counts["blocks_total"], 4)


class TruncationDisclosureTests(unittest.TestCase):
    def test_budget_truncation_is_disclosed(self):
        html = "<html><body>" + "".join(
            f"<p>paragraph {i} with some words</p>" for i in range(500)
        ) + "</body></html>"
        acd = _acd(html, max_blocks=400)
        self.assertEqual(acd.counts["blocks"], 400)
        self.assertEqual(acd.counts["blocks_total"], 500)
        view = acd_view(acd)
        self.assertIn("BLOCKS: 400 of 500", view)
        self.assertTrue(any("beyond the 400-block budget" in u for u in acd.unavailable))

    def test_no_truncation_no_disclosure(self):
        html = "<html><body><p>one paragraph</p></body></html>"
        acd = _acd(html)
        self.assertEqual(acd.counts["blocks"], acd.counts["blocks_total"])
        self.assertNotIn("BLOCKS:", acd_view(acd))

    def test_chrome_skipped_candidates_are_counted(self):
        # The nav must be INSIDE the content root to be a skipped candidate;
        # outside it, content is simply out of scope (content_root's model).
        html = ('<html><body><main><p>content</p>'
                '<nav><p>nav text</p></nav></main></body></html>')
        acd = _acd(html)
        self.assertTrue(any("chrome" in u for u in acd.unavailable))
        self.assertNotIn("nav text", " ".join(b.text or "" for b in acd.blocks))


class SliceUnknownIdTests(unittest.TestCase):
    def setUp(self):
        self.acd = _acd("<html><body>" + "".join(
            f"<p>block {i}</p>" for i in range(10)) + "</body></html>")

    def test_unknown_start_raises(self):
        with self.assertRaises(ValueError) as ctx:
            slice_acd(self.acd, "b999", "b1005")
        self.assertIn("unknown block id: b999", str(ctx.exception))
        self.assertIn("b1..b10", str(ctx.exception))

    def test_unknown_end_raises(self):
        with self.assertRaises(ValueError) as ctx:
            slice_acd(self.acd, "b1", "b1005")
        self.assertIn("unknown block id: b1005", str(ctx.exception))

    def test_valid_slice_still_works(self):
        chunk = slice_acd(self.acd, "b2", "b4")
        self.assertEqual([b.id for b in chunk], ["b2", "b3", "b4"])

    def test_empty_acd_unknown_id_is_an_error_not_a_wrong_answer(self):
        acd = _acd("<html><body><div></div></body></html>")
        with self.assertRaises(ValueError):
            slice_acd(acd, "b1")


class JsonLdProductTests(unittest.TestCase):
    def test_product_projects_into_blocks(self):
        html = """<html><head><script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product",
         "name": "Mechanical Keyboard",
         "description": "A tenkeyless board with hot-swap switches.",
         "offers": {"@type": "Offer", "price": "89.99",
                     "priceCurrency": "USD",
                     "availability": "https://schema.org/InStock"}}
        </script></head><body><p>fallback</p></body></html>"""
        acd = _acd(html)
        texts = [b.text for b in acd.blocks if b.text]
        self.assertTrue(any("Product: Mechanical Keyboard" in t for t in texts), texts)
        self.assertTrue(any("hot-swap switches" in t for t in texts))
        self.assertTrue(any("89.99 USD" in t and "InStock" in t for t in texts), texts)
        # still stored in framework data too
        self.assertIn("json_ld", acd.framework)

    def test_product_without_offers_still_projects(self):
        html = ('<html><head><script type="application/ld+json">'
                '{"@type": "Product", "name": "Bare Product"}</script>'
                '</head><body></body></html>')
        acd = _acd(html)
        texts = [b.text for b in acd.blocks if b.text]
        self.assertTrue(any("Product: Bare Product" in t for t in texts))


class ReadingProjectionTests(unittest.TestCase):
    """The control realm's dosh.text() and the read command must carry all
    six block types — code and table included."""

    HTML = """<html><head><title>reading</title></head><body><main>
    <h1>Code and table page</h1>
    <pre>def solve(): return 42</pre>
    <table><tr><th>Name</th><th>Price</th></tr><tr><td>Ritz</td><td>900</td></tr></table>
    <p>plain paragraph</p>
    </main></body></html>"""

    def test_reading_text_renders_code_and_table(self):
        from webresearch.shell import _reading_text

        acd = _acd(self.HTML)
        reading = "\n".join(
            _reading_text(b) for b in acd.blocks
            if b.type in {"heading", "text", "list", "quote", "code", "table"}
        )
        self.assertIn("def solve(): return 42", reading)
        self.assertIn("Name | Price", reading)
        self.assertIn("Ritz | 900", reading)

    def test_control_realm_receives_code_and_table(self):
        import asyncio

        import httpx

        from webresearch.session import Session
        from webresearch.shell import DoshShell

        async def run():
            import webresearch.cache as cache
            from webresearch.cache import HttpCache

            # Module-global cache swap must not leak into other tests:
            # save, swap, and restore in finally (the monkeypatch fixture is
            # unavailable inside this inline async helper).
            original_cache = cache.CACHE
            cache.CACHE = HttpCache()
            routes = {"https://rd.test/": self.HTML}

            def handler(request):
                return httpx.Response(200, text=routes[str(request.url)],
                                      headers={"content-type": "text/html"})

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                         follow_redirects=True) as client:
                session = Session(client, "t")
                shell = DoshShell(session)
                try:
                    await shell.execute("open", {"url": "https://rd.test/"})
                    result = await shell.execute(
                        "eval", {"source": "dosh.emit('reading', dosh.text())"})
                    text = json.dumps(result["result"]["emits"])
                    self.assertIn("def solve(): return 42", text)
                    self.assertIn("Ritz | 900", text)
                    # and the read command carries them too
                    result = await shell.execute("read", {})
                    dumped = json.dumps(result)
                    self.assertIn("code", dumped)
                    self.assertIn("Ritz | 900", dumped)
                finally:
                    shell.close()
                    await session.close()
                    cache.CACHE = original_cache

        asyncio.run(run())


class DisclosureTests(unittest.TestCase):
    def test_drag_disclosure_reflects_the_layout_engine(self):
        html = ('<html><body><input type="range" id="vol" name="vol"></body></html>')
        from webresearch.engine.inspect import inspect_html
        targets, _ = inspect_html(html, "https://example.com/")
        acd = _acd(html, targets=targets)
        drag_lines = [u for u in acd.unavailable if u.startswith("drag")]
        self.assertEqual(len(drag_lines), 1)
        self.assertIn("layout engine", drag_lines[0])
        self.assertNotIn("without a layout engine", drag_lines[0])

    def test_embed_disclosure_is_current(self):
        html = '<html><body><iframe src="https://e.com/f"></iframe></body></html>'
        from webresearch.engine.inspect import inspect_html
        targets, _ = inspect_html(html, "https://example.com/")
        acd = _acd(html, targets=targets)
        embed_lines = [u for u in acd.unavailable if u.startswith("embed")]
        self.assertEqual(len(embed_lines), 1)
        self.assertIn("postMessage lanes are live", embed_lines[0])


class SelectorContractTests(unittest.TestCase):
    def test_block_sel_carries_the_itemprop_extensions(self):
        for prop in ("text", "articleBody", "headline", "caption", "abstract"):
            self.assertIn(f"[itemprop='{prop}']", BLOCK_SEL)

    def test_itemprop_text_spans_match(self):
        html = ('<html><body><span itemprop="text">a quote worth reading'
                '</span></body></html>')
        tree = LexborHTMLParser(html)
        root = tree.body
        self.assertEqual(len(root.css(BLOCK_SEL)), 1)


class AcdV2AndDataDomeNativeTests(unittest.TestCase):
    def test_acd_v2_references_frames_and_json_ld_schemas(self):
        html = """<html><body><main>
            <p>Main article body.</p>
            <ol class="mw-references references">
                <li>Ref [1]: Agent Browser Design</li>
            </ol>
            <iframe srcdoc="<p>Nested frame widget text</p>"></iframe>
            <script type="application/ld+json">
            [
                {"@type": "Recipe", "name": "Cardamom Tea", "recipeIngredient": ["Tea", "Milk"], "recipeInstructions": "Simmer 5m."},
                {"@type": "JobPosting", "title": "Rust Compiler Engineer", "hiringOrganization": {"name": "Dosh"}, "description": "Work on V8."},
                {"@type": "BreadcrumbList", "itemListElement": [{"name": "Docs"}, {"name": "ACD"}]}
            ]
            </script>
        </main></body></html>"""
        acd_default = build_acd(url="https://en.wikipedia.org/wiki/T", title="T", html=html)
        texts_def = [b.text for b in acd_default.blocks if b.text]
        self.assertFalse(any("Ref [1]: Agent Browser Design" in t for t in texts_def))
        self.assertTrue(any("[frame-1] Nested frame widget text" in t for t in texts_def), texts_def)
        self.assertTrue(any("Recipe: Cardamom Tea" in t for t in texts_def), texts_def)
        self.assertTrue(any("Job: Rust Compiler Engineer at Dosh" in t for t in texts_def), texts_def)
        self.assertTrue(any("Breadcrumbs: Docs > ACD" in t for t in texts_def), texts_def)

        acd_refs = build_acd(url="https://en.wikipedia.org/wiki/T", title="T", html=html, include_references=True)
        texts_refs = [b.text for b in acd_refs.blocks if b.text]
        self.assertTrue(any("Ref [1]: Agent Browser Design" in t for t in texts_refs), texts_refs)

    def test_datadome_native_protocol_extraction_and_verdicts(self):
        import asyncio
        from webresearch.engine import datadome

        interstitial_fe = """<html><body class="dd-response-page--captcha">
            <script>var dd = {'cid':'AHrlqAAAAAMA','hsh':'A5829B','t':'fe','s':46694,'e':'9a8b','host':'geo.captcha-delivery.com'};</script>
            <iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=AHrlqAAAAAMA"></iframe>
        </body></html>"""
        det = datadome.detect(interstitial_fe, referer="https://booking.com/")
        self.assertTrue(det.present)
        self.assertEqual(det.kind, "captcha")
        self.assertEqual(det.variant, "fe")
        self.assertEqual(det.request_id, "AHrlqAAAAAMA")
        self.assertTrue(det.solvable)
        self.assertIn("https://geo.captcha-delivery.com/captcha/?", det.challenge_url)

        interstitial_bv = """<html><body class="dd-response-page--hard-block">
            <script>var dd = {'cid':'BLOCKED123','hsh':'A5829B','t':'bv','s':46694,'host':'geo.captcha-delivery.com'};</script>
        </body></html>"""
        det_bv = datadome.detect(interstitial_bv)
        self.assertTrue(det_bv.present)
        self.assertFalse(det_bv.solvable)

        class DummySession:
            html = interstitial_bv
            url = "https://booking.com/"
            frames = []

        res = asyncio.run(datadome.solve(DummySession()))
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("needs_new_ip"))


if __name__ == "__main__":
    unittest.main()
