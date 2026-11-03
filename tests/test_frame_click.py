"""A click_frame action must land in the frame realm that ran the frame's scripts.

The parent iframe's contentDocument is an empty shell. Clicking that shell
returns success without ever running the listener that lives in the fetched
frame.
"""

from __future__ import annotations

import asyncio

import httpx

from webresearch.engine.runtime import run_scripts

PARENT_URL = "https://parent.example/page.html"
FRAME_URL = "https://frame.example/widget.html"

PARENT_HTML = """<!doctype html><html><body>
<iframe name="f1"></iframe>
</body></html>"""

FRAME_HTML = """<!doctype html><html><body>
<span id="box" role="checkbox" class="recaptcha-checkbox">I'm not a robot</span>
<script>
window.__clicked = 0;
document.getElementById("box").addEventListener("click", function () {
  window.__clicked += 1;
  parent.postMessage("frame-clicked", "*");
});
</script>
</body></html>"""


async def _main():
    client = httpx.Client()
    client.headers = {"user-agent": "test-frame-click"}
    _, frame_info = await run_scripts(FRAME_HTML, FRAME_URL, client, force=True)
    fctx = frame_info["ctx"]
    assert fctx is not None
    assert int(fctx.eval("window.__clicked")) == 0

    _, parent_info = await run_scripts(
        PARENT_HTML,
        PARENT_URL,
        client,
        force=True,
        actions=[{
            "kind": "click_frame",
            "frame_ref": "f1",
            "frame_selector": "[role='checkbox'], .recaptcha-checkbox",
        }],
        frame_contexts={"f1": fctx},
    )
    applied = parent_info["actions_applied"]
    assert applied and applied[0]["ok"] is True, applied
    assert int(fctx.eval("window.__clicked")) == 1
    assert any(m.get("data") == "frame-clicked" for m in frame_info["post_messages"]), (
        frame_info["post_messages"]
    )


def test_click_frame_hits_frame_realm():
    asyncio.run(_main())
