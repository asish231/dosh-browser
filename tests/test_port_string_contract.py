"""MessagePort string-payload contract: a posted string arrives as that exact string.

Regression from the reCAPTCHA anchor handshake: the anchor posts JSON-text
strings over its MessagePort (["<payload>","z","<id>"] serialized as text).
The port delivery lane used to eagerly JSON.parse recorded strings, so the
parent's handler received an ARRAY where a real browser delivers the STRING —
Google's handler ignored every message ("o"/open never answered) and the
checkbox never resolved. The window lane already honored the contract
(__wrWireDecode); this pins the port lane to the same behavior.
"""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.frames import (
    drain_frame_posts,
    frame_message_roundtrip,
    load_frames,
)
from webresearch.engine.runtime import run_scripts
from webresearch.fetch import make_client

CHILD_JS = """
  var ch = new MessageChannel();
  parent.postMessage('handshake', '*', [ch.port2]);
  // Google's anchor channel sends JSON-TEXT strings over the port.
  ch.port1.postMessage('["Bpayload","z","abc123"]');
  ch.port1.postMessage('plain-string');
  ch.port1.postMessage({ obj: true, n: 3 });
"""

PARENT_HTML = (
    "<!DOCTYPE html><html><body>"
    "<iframe src=\"data:text/html;base64,"
    + base64.b64encode(
        f"<!DOCTYPE html><html><body><script>{CHILD_JS}</script></body></html>".encode()
    ).decode()
    + '"></iframe>'
    "<script>"
    "window.__parent_port_log = [];"
    "window.addEventListener('message', function (e) {"
    "  if (e.data === 'handshake' && e.ports && e.ports.length) {"
    "    var port = e.ports[0];"
    "    port.addEventListener('message', function (me) {"
    "      /* the receiver asserts the exact posted TYPE: a string must arrive as a string, JSON text included */"
    "      window.__parent_port_log.push(typeof me.data === 'string' ? 'str:' + me.data : 'other:' + Object.prototype.toString.call(me.data));"
    "    });"
    "  }"
    "});"
    "</script></body></html>"
)


async def _session():
    client = make_client()
    html, info = await run_scripts(PARENT_HTML, "https://parent.example/", client)
    frames = await load_frames(
        html,
        "https://parent.example/",
        client,
        parent_messages=info.get("post_messages"),
        parent_ctx=info.get("ctx"),
    )
    for doc in frames:
        drain_frame_posts(doc)
    await frame_message_roundtrip(
        frames,
        parent_ctx=info["ctx"],
        parent_url="https://parent.example/",
        parent_html=html,
        client=client,
    )
    return info


@pytest.mark.asyncio
async def test_port_string_payload_stays_string():
    info = await _session()
    import json

    log = json.loads(info["ctx"].eval("JSON.stringify(window.__parent_port_log || [])"))
    assert "str:[\"Bpayload\",\"z\",\"abc123\"]" in log, (
        f"JSON-text port payload was mutated in transit: {log}"
    )
    assert "str:plain-string" in log, f"plain string payload missing: {log}"


@pytest.mark.asyncio
async def test_port_object_payload_decodes():
    info = await _session()
    log = info["ctx"].eval("JSON.stringify(window.__parent_port_log || [])")
    # Objects still travel through the structured-clone envelope.
    assert "other:[object Object]" in log, f"object payload missing: {log}"
