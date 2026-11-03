"""Frame-load lifecycle: a loaded frame fires `load` on its iframe element in the parent.

Regression from the reCAPTCHA anchor flow: the parent realm's widget code
gates its channel processing on the anchor iframe's load event. The engine
loaded frame documents but never dispatched `load` on the iframe element, so
parent-side widget logic that runs on iframe load never executed — the
anchor channel handshake stalled permanently.
"""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.frames import load_frames
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import make_client

CHILD_HTML = b"<!DOCTYPE html><html><body><p>frame body</p></body></html>"

PARENT_HTML = (
    "<!DOCTYPE html><html><body>"
    "<iframe src=\"data:text/html;base64," + base64.b64encode(CHILD_HTML).decode() + '"></iframe>'
    "<script>"
    "window.__load_events = [];"
    "var ifr = document.querySelector('iframe');"
    "/* both the onload property and addEventListener must see the load */"
    "ifr.onload = function(){ window.__load_events.push('onload'); };"
    "ifr.addEventListener('load', function(){ window.__load_events.push('listener'); });"
    "</script></body></html>"
)


@pytest.mark.asyncio
async def test_frame_load_fires_on_iframe_element():
    client = make_client()
    html, info = await run_scripts(PARENT_HTML, "https://parent.example/", client)
    frames = await load_frames(
        html,
        "https://parent.example/",
        client,
        parent_messages=info.get("post_messages"),
        parent_ctx=info.get("ctx"),
    )
    assert len(frames) == 1
    ctx = info["ctx"]
    events = ctx.eval("JSON.stringify(window.__load_events || [])")
    assert '"onload"' in events, f"iframe.onload never fired: {events}"
    assert '"listener"' in events, f"addEventListener('load') never fired: {events}"


@pytest.mark.asyncio
async def test_frame_load_fires_once():
    client = make_client()
    html, info = await run_scripts(PARENT_HTML, "https://parent.example/", client)
    frames = await load_frames(
        html,
        "https://parent.example/",
        client,
        parent_messages=info.get("post_messages"),
        parent_ctx=info.get("ctx"),
    )
    ctx = info["ctx"]
    count = ctx.eval("(window.__load_events || []).length")
    assert int(count) == 2, f"load fired more than once per handler: {count}"
