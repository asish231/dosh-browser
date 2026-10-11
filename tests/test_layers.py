import unittest

from webresearch.extract import extract
from webresearch.fetch import FetchResult
from webresearch.forms import forms
from webresearch.links import links
from webresearch.parse import parse
from webresearch.search import (
    _parse_bing,
    _parse_ddg_lite,
    _parse_google,
    _parse_yahoo,
    _unwrap_google,
)
from webresearch.snapshot import snapshot

HTML = """<!doctype html>
<html lang="en">
<head>
  <title>Widget Labs</title>
  <meta name="description" content="Tiny widgets for agents.">
</head>
<body>
  <header><nav><a href="/home">Home</a></nav></header>
  <main>
    <article>
      <h1>Widget Labs</h1>
      <p>We build tiny widgets for language models.</p>
      <p>Start with the <a href="https://docs.example.com/start">quickstart</a>.</p>
      <h2>Pricing</h2>
      <ul>
        <li>Free tier</li>
        <li>Team plan</li>
      </ul>
      <form method="post" action="/search">
        <input type="hidden" name="token" value="abc">
        <input type="text" name="q" required>
        <button type="submit">Go</button>
      </form>
    </article>
  </main>
  <aside class="sidebar"><a href="https://ads.example.com">Buy now</a></aside>
  <footer><a href="/legal">Legal</a></footer>
</body>
</html>
"""

WIKI_ISH = """<!doctype html>
<html>
<head><title>Web browser - Wikipedia</title></head>
<body>
  <header class="mw-header"><nav><a href="/wiki/Main_Page">Main page</a></nav></header>
  <main id="content">
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
        </section>
        <ol class="references"><li id="cite_note-1"><a href="#cite_ref-1">^</a> Source</li></ol>
      </div>
    </div>
  </main>
</body>
</html>
"""


def _page(html: str, url: str = "https://example.com/x"):
    result = FetchResult(
        url=url,
        final_url=url,
        status=200,
        content_type="text/html",
        kind="html",
        body=html,
    )
    return parse(result)


class SnapshotTests(unittest.TestCase):
    def test_article_text_and_numbered_links(self):
        doc = _page(HTML)
        page = snapshot(doc)
        self.assertIn("Widget Labs", page.view)
        self.assertIn("tiny widgets", page.view)
        self.assertIn("[1]", page.view)
        self.assertEqual(page.links[0].url, "https://docs.example.com/start")
        self.assertFalse(any("ads.example.com" in link.url for link in page.links))
        self.assertFalse(any("legal" in link.url.lower() for link in page.links))
        self.assertEqual(page.forms[0].fields[0].name, "token")
        self.assertTrue(any(f.name == "q" and f.required for f in page.forms[0].fields))

    def test_wikipedia_section_headers_are_content(self):
        doc = _page(WIKI_ISH, "https://en.wikipedia.org/wiki/Web_browser")
        page = snapshot(doc)
        self.assertIn("A web browser fetches pages", page.view)
        self.assertIn("Mosaic", page.view)
        self.assertIn("## Function", page.view)
        self.assertNotIn("Main page", page.view)
        self.assertFalse(page.spa_hint)
        urls = [link.url for link in page.links]
        self.assertFalse(any("cite_note" in u for u in urls))

    def test_extract_builtins_and_css(self):
        doc = _page(HTML)
        out = extract(doc, {"title": "title", "lead": "p", "h": "h2"})
        self.assertEqual(out.fields["title"], "Widget Labs")
        self.assertIn("language models", out.fields["lead"][0])
        self.assertEqual(out.fields["h"], "Pricing")

    def test_links_and_forms_modules(self):
        doc = _page(HTML)
        self.assertEqual(len(links(doc)), 1)
        self.assertEqual(forms(doc)[0].method, "POST")

    def test_google_result_parse(self):
        html = """<!doctype html><html><body>
        <div id="search">
          <div class="g">
            <a href="/url?q=https://www.tripadvisor.com/Hotel_Review.html&sa=U">
              <h3>Paris Hotel - Tripadvisor</h3>
            </a>
            <div class="s"><span class="st">Reviews and prices.</span></div>
          </div>
          <div class="g">
            <a href="https://www.google.com/search?q=more"><h3>More</h3></a>
          </div>
        </div>
        </body></html>"""
        doc = _page(html, "https://www.google.com/search?q=paris+hotel")
        hits = _parse_google(doc, max_hits=8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://www.tripadvisor.com/Hotel_Review.html")
        self.assertIn("Tripadvisor", hits[0].title)
        self.assertIsNone(_unwrap_google("/aclk?ad=1"))

    def test_bing_result_parse(self):
        html = """<!doctype html><html><body>
        <ol id="b_results">
          <li class="b_algo">
            <h2><a href="https://docs.python.org/3/library/asyncio.html">asyncio — Python</a></h2>
            <div class="b_caption"><p>Asynchronous I/O library documentation.</p></div>
          </li>
        </ol>
        </body></html>"""
        doc = _page(html, "https://www.bing.com/search?q=python+asyncio")
        hits = _parse_bing(doc, max_hits=8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://docs.python.org/3/library/asyncio.html")
        self.assertIn("asyncio", hits[0].title)
        self.assertIn("documentation", hits[0].snippet)

    def test_yahoo_result_parse(self):
        html = """<!doctype html><html><body>
        <div id="web">
          <div class="dd algo algo-sr">
            <h3 class="title"><a href="https://realpython.com/async-io-python/">Real Python Asyncio</a></h3>
            <div class="compText"><p>Hands-on walkthrough for asyncio.</p></div>
          </div>
        </div>
        </body></html>"""
        doc = _page(html, "https://search.yahoo.com/search?p=python+asyncio")
        hits = _parse_yahoo(doc, max_hits=8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://realpython.com/async-io-python/")
        self.assertIn("Real Python", hits[0].title)

    def test_ddg_lite_result_parse(self):
        html = """<!doctype html><html><body>
        <table>
          <tr><td><a class="result-link" href="https://example.com/item">Item Title</a></td></tr>
          <tr><td class="result-snippet">Snippet text here.</td></tr>
        </table>
        </body></html>"""
        doc = _page(html, "https://lite.duckduckgo.com/lite/")
        hits = _parse_ddg_lite(doc, max_hits=8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://example.com/item")
        self.assertEqual(hits[0].title, "Item Title")
        self.assertEqual(hits[0].snippet, "Snippet text here.")


if __name__ == "__main__":
    unittest.main()
