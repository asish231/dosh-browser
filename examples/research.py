"""Chain the layers like an agent would: search → open hit → follow a link."""

from __future__ import annotations

import asyncio

from webresearch import Browser


async def main() -> None:
    browser = Browser()
    session = await browser.create()
    try:
        found = await session.search("example domain")
        print(found.view)
        print("---")
        page = await session.goto("https://example.com")
        print(page.view)
        if page.links:
            next_page = await session.open(1)
            print("--- opened [1] ---")
            print(next_page.view[:1500])
    finally:
        await browser.close_all()


if __name__ == "__main__":
    asyncio.run(main())
