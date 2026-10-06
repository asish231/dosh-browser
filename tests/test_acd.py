import unittest

from webresearch.acd import build_acd, query_acd, slice_acd
from webresearch.fetch import FetchResult
from webresearch.parse import parse
from webresearch.snapshot import snapshot

HTML = """<!doctype html><html><body>
<main>
  <h1>Paris hotels</h1>
  <p>Compare prices for the best hotels in Paris.</p>
  <table>
    <tr><th>Hotel</th><th>Price</th><th>Rating</th></tr>
    <tr><td>Ritz</td><td>900</td><td>4.8</td></tr>
    <tr><td>Lutetia</td><td>500</td><td>4.6</td></tr>
  </table>
  <button>Load more</button>
</main>
</body></html>"""


def _doc(html: str = HTML):
    result = FetchResult(
        url="https://example.com/hotels",
        final_url="https://example.com/hotels",
        status=200,
        content_type="text/html",
        kind="html",
        body=html,
    )
    return parse(result)


class AcdTests(unittest.TestCase):
    def test_acd_blocks_include_table(self):
        doc = _doc()
        page = snapshot(doc)
        acd = build_acd(url=page.url, title=page.title, html=HTML, links=page.links)
        kinds = [b.type for b in acd.blocks]
        self.assertIn("heading", kinds)
        self.assertIn("table", kinds)
        table = next(b for b in acd.blocks if b.type == "table")
        self.assertEqual(table.rows[0], ["Hotel", "Price", "Rating"])
        self.assertEqual(table.rows[1], ["Ritz", "900", "4.8"])
        self.assertTrue(all(b.id.startswith("b") for b in acd.blocks))

    def test_query_and_slice(self):
        doc = _doc()
        page = snapshot(doc)
        acd = build_acd(url=page.url, title=page.title, html=HTML)
        hits = query_acd(acd, "ritz")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].type, "table")
        first = acd.blocks[0].id
        chunk = slice_acd(acd, start=first, limit=1)
        self.assertEqual(len(chunk), 1)
        self.assertEqual(chunk[0].id, first)

    def test_snapshot_renders_markdown_table(self):
        page = snapshot(_doc())
        self.assertIn("| Hotel | Price | Rating |", page.view)
        self.assertIn("| Ritz | 900 | 4.8 |", page.view)

    def test_acd_surfaces_framework_and_api(self):
        js_info = {
            "framework_data": {"__NEXT_DATA__": {"props": {}}},
            "resource_requests": [
                {"url": "https://example.com/api/hotels", "status": 200, "content_type": "application/json"}
            ],
            "error_messages": ["TypeError: boom"],
        }
        acd = build_acd(
            url="https://example.com",
            title="t",
            html=HTML,
            js_info=js_info,
        )
        self.assertEqual(acd.counts["api"], 1)
        self.assertIn("__NEXT_DATA__", acd.framework)
        self.assertEqual(acd.errors, ["TypeError: boom"])

    def test_acd_extracts_json_ld_faq_and_howto(self):
        json_ld_html = """<!doctype html><html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{
                "@type": "Question",
                "name": "How to make a website?",
                "acceptedAnswer": {"@type": "Answer", "text": "Pick a template and customize."}
            }]
        }
        </script>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": "Build Guide",
            "step": [
                {"@type": "HowToStep", "name": "Step 1", "itemListElement": {"@type": "HowToDirection", "text": "Choose name"}},
                {"@type": "HowToStep", "name": "Step 2", "itemListElement": {"@type": "HowToDirection", "text": "Launch"}}
            ]
        }
        </script>
        </head><body><h1>Guide</h1></body></html>"""
        acd = build_acd(url="https://example.com/guide", title="Guide", html=json_ld_html)
        self.assertIn("json_ld", acd.framework)
        self.assertEqual(len(acd.framework["json_ld"]), 2)
        faq_hits = query_acd(acd, "template")
        self.assertTrue(len(faq_hits) > 0)
        howto_hits = query_acd(acd, "Choose name")
        self.assertTrue(len(howto_hits) > 0)


class ToolSurfaceTests(unittest.TestCase):
    def test_tools_include_query_slice(self):
        from webresearch.tools import TOOLS

        names = {t["function"]["name"] for t in TOOLS}
        self.assertIn("browser_query", names)
        self.assertIn("browser_slice", names)


if __name__ == "__main__":
    unittest.main()