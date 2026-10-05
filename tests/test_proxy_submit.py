import asyncio
import os
import unittest

from webresearch.session import Browser


class ProxyPoolTests(unittest.TestCase):
    def test_rotation_cycles_pool(self):
        proxies = ["http://p1:8000", "http://p2:8000", "http://p3:8000"]
        browser = Browser(proxies=proxies)
        seen = [browser._next_proxy() for _ in range(5)]
        self.assertEqual(seen, ["http://p1:8000", "http://p2:8000", "http://p3:8000", "http://p1:8000", "http://p2:8000"])

    def test_create_assigns_rotating_proxy(self):
        async def go():
            browser = Browser(proxies=["http://p1:8000", "http://p2:8000"])
            s1 = await browser.create()
            s2 = await browser.create()
            s3 = await browser.create()
            out = (s1.proxy, s2.proxy, s3.proxy)
            await browser.close_all()
            return out

        p1, p2, p3 = asyncio.run(go())
        self.assertEqual((p1, p2, p3), ("http://p1:8000", "http://p2:8000", "http://p1:8000"))

    def test_explicit_proxy_wins(self):
        async def go():
            browser = Browser(proxies=["http://p1:8000"])
            s = await browser.create(proxy="http://explicit:8000")
            val = s.proxy
            await browser.close_all()
            return val

        self.assertEqual(asyncio.run(go()), "http://explicit:8000")

    def test_env_proxies_loaded(self):
        os.environ["WR_PROXIES"] = "http://a:1, http://b:2"
        try:
            browser = Browser()
            self.assertEqual(browser.proxies, ["http://a:1", "http://b:2"])
        finally:
            os.environ.pop("WR_PROXIES", None)


class SubmitValidationTests(unittest.TestCase):
    def test_preventdefault_blocks_raw_submit(self):
        import httpx

        from webresearch.session import Browser

        calls = {"posts": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                calls["posts"] += 1
                return httpx.Response(200, text="<html><body>posted</body></html>")
            html = (
                "<html><body><form method='post' action='/go'>"
                "<input name='q' value=''>"
                "<button type='submit'>Go</button></form>"
                "<script>"
                "document.querySelector('form').addEventListener('submit', function(e){"
                "  e.preventDefault();"
                "  document.body.setAttribute('data-js', 'handled');"
                "});"
                "</script></body></html>"
            )
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})

        async def go():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                browser = Browser()
                session = await browser.create()
                session.client = client
                await session.goto("https://example.com/")
                form_n = session.page.forms[0].n
                await session.submit(form_n, {"q": "test"})
                await browser.close_all()

        asyncio.run(go())
        self.assertEqual(calls["posts"], 0)


if __name__ == "__main__":
    unittest.main()