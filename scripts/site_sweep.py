"""Live sweep: load many different real websites, report what works, what
is bot-walled, and what the engine/ACD can and cannot project on each.

Each site gets: status, JS ran, html size, ACD coverage, action count,
caps counts (canvas/iframe/wasm), console errors, and a classification:
  loaded      - real content projected
  bot-wall    - challenge page, not the app (detected honestly)
  partial     - some content, engine gaps visible
  failed      - transport/engine failure
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.session import Browser

SITES = [
    # (url, site class)
    ("https://en.wikipedia.org/wiki/Web_browser", "encyclopedia (SSR)"),
    ("https://github.com/microsoft/vscode", "code hosting (JS-heavy SSR)"),
    ("https://nextjs.org/", "Next.js/React SPA"),
    ("https://angular.dev/", "Angular SPA (docs)"),
    ("https://vuejs.org/", "Vue SPA (docs)"),
    ("https://www.bbc.com/news", "news (SSR)"),
    ("https://stackoverflow.com/questions", "Q&A (SSR)"),
    ("https://medium.com/", "blog platform (React SPA)"),
    ("https://www.reddit.com/", "forum (heavy SPA + bot detection)"),
    ("https://x.com/", "social (hard bot wall)"),
    ("https://www.instagram.com/", "social (login+bot wall)"),
    ("https://www.amazon.com/", "e-commerce (bot detection)"),
    ("https://www.booking.com/", "travel (DataDome)"),
    ("https://news.ycombinator.com/", "link aggregator (minimal JS)"),
    ("https://developer.mozilla.org/en-US/", "docs (SSR)"),
    ("https://www.tiktok.com/", "video platform (bot wall)"),
]


def classify(page, session_html_len, acd_blocks, acd_total, actions, unavailable, console_errs):
    url = (page.url or "").lower()
    title = (page.title or "").lower()
    wall_markers = ["just a moment", "attention required", "access denied", "verify",
                    "bot", "captcha", "ddg-", "datadome", "sign in to continue",
                    "unusual traffic", "security check"]
    if page.status in (403, 429, 503):
        return "bot-wall"
    if page.status >= 400:
        return f"http-{page.status}"
    if any(m in title for m in wall_markers) and acd_blocks == 0:
        return "bot-wall"
    if session_html_len < 3000 and acd_blocks == 0:
        return "empty/shell"
    if acd_blocks > 0 and acd_blocks >= (acd_total or acd_blocks):
        return "loaded"
    if acd_blocks > 0:
        return "partial"
    return "partial"


async def main():
    browser = Browser()
    session = await browser.create()
    sh = session.shell
    results = []

    for url, site_class in SITES:
        line = {"url": url, "class": site_class}
        try:
            await asyncio.wait_for(
                session.goto(url, max_chars=2500), timeout=45.0
            )
            page = session.page
            acd = page.acd
            blocks = len(acd.blocks) if acd else 0
            total = acd.counts.get("blocks_total", 0) if acd else 0
            actions_n = acd.counts.get("actions", 0) if acd else 0
            links_n = acd.counts.get("links", 0) if acd else 0
            errs = acd.counts.get("errors", 0) if acd else 0
            unavail = acd.unavailable if acd else []
            line.update({
                "status": page.status,
                "js_ran": page.js_ran,
                "html_bytes": len(session.html or ""),
                "title": page.title[:60],
                "blocks": blocks,
                "blocks_total": total,
                "actions": actions_n,
                "links": links_n,
                "js_errors": errs,
                "unavailable_n": len(unavail),
            })
            # caps: canvas/iframe/wasm counts
            r = await sh.execute("caps", {})
            if r.get("ok"):
                c = r["result"]["counts"]
                line["canvas"] = c.get("canvas", 0)
                line["iframe"] = c.get("iframe", 0)
                line["wasm"] = c.get("wasm_script", 0)
                line["elements"] = c.get("elements", 0)
            line["verdict"] = classify(page, len(session.html or ""), blocks, total, actions_n, unavail, errs)
        except asyncio.TimeoutError:
            line.update({"verdict": "timeout(45s)"})
        except Exception as exc:
            line.update({"verdict": f"failed:{type(exc).__name__}"})
        results.append(line)
        print(f"{line.get('verdict','?'):14s} {line.get('status','-'):>4} {url[:52]:54s} "
              f"blocks={line.get('blocks','-')}/{line.get('blocks_total','-')} "
              f"acts={line.get('actions','-')} err={line.get('js_errors','-')}")
        await asyncio.sleep(1.5)

    await browser.close_all()

    print("\n================ DETAILED RESULTS ================")
    for r in results:
        print(f"\n{r['url']}  [{r['class']}]")
        print(f"  verdict: {r.get('verdict','?')}  status={r.get('status','-')} js_ran={r.get('js_ran','-')}")
        print(f"  title: {r.get('title','')!r}")
        print(f"  html: {r.get('html_bytes','-')}B  elements={r.get('elements','-')}  "
              f"ACD: {r.get('blocks','-')}/{r.get('blocks_total','-')}  "
              f"actions={r.get('actions','-')} links={r.get('links','-')} js_errors={r.get('js_errors','-')}")
        print(f"  canvas={r.get('canvas','-')} iframe={r.get('iframe','-')} wasm={r.get('wasm','-')}")


if __name__ == "__main__":
    asyncio.run(main())
