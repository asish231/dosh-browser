"""A1: In-VM obfuscated widget channel handshake — regression test.

Exercises the port-lane machinery an obfuscated widget (reCAPTCHA-style
botguard) depends on, end to end across realms:

- PROXY MATERIALIZATION: the widget realm receives a transferred port as
  a live proxy end whose posts route back to the parent's kept end.
- SENDER-IDENTITY: the widget's listener checks event.source against its
  own parent window before trusting the setup frame.
- OBFUSCATED-KEY PROTOCOL: the channel key is an opaque base64 blob the
  widget transforms inside its own realm (rotation + re-encode) — the
  host routing never inspects or derives it.
- JSON-TEXT STRING CONTRACT: handshake frames arrive as the EXACT string
  they were posted as (["<b64>","z","<id>"] shape) — eager JSON-parsing
  on delivery historically broke this widget's dispatcher.
- MULTI-ROUND: setup -> challenge -> token, all through the port lane.

The obfuscated key is derived and validated in-VM by the widget's own
code; the engine only routes opaque strings.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from webresearch.engine.frames import FrameDocument, frame_message_roundtrip, is_port_message
from webresearch.engine.runtime import run_scripts, take_pending_posts
from webresearch.types import FetchResult

PARENT_URL = "https://host.example/page.html"
WIDGET_URL = "https://widget.example/challenge.html"

# The widget's obfuscated protocol: frames are JSON-text strings shaped
# ["<base64 payload>", "<type>", "<id>"]. The engine must deliver them
# as strings (never eagerly parsed).
WIDGET_HTML = """<!doctype html><html><body>
<script>
window.__widget_state = { stage: 'idle', keys: [], frames: [], sourceOk: null };

/* Obfuscated key derivation, entirely in-VM: rotate + re-encode base64.
   The engine never sees or derives this — it routes opaque strings. */
function deriveReplyKey(seedKey) {
  var rotated = '';
  for (var i = 0; i < seedKey.length; i++) {
    rotated += String.fromCharCode(seedKey.charCodeAt(i) ^ 0x5a);
  }
  return btoa(unescape(encodeURIComponent(rotated)));
}

window.addEventListener('message', function (e) {
  /* Sender-identity check: the widget only trusts its own parent. */
  window.__widget_state.sourceOk = (e.source === parent);
  if (!window.__widget_state.sourceOk) return;
  if (e.data === 'widget-launch' && e.ports && e.ports.length === 1) {
    var port = e.ports[0];
    window.__widget_port = port;
    port.onmessage = function (me) {
      /* The string contract: JSON-text frames arrive as strings. */
      var frame = String(me.data);
      window.__widget_state.frames.push(frame);
      /* Decode the obfuscated envelope IN-VM. */
      var envelope = null;
      try { envelope = JSON.parse(frame); } catch (err) { return; }
      if (!Array.isArray(envelope) || envelope.length < 2) return;
      var payloadB64 = envelope[0];
      var type = envelope[1];
      var payload = null;
      try { payload = JSON.parse(atob(payloadB64)); } catch (err) { return; }
      if (type === 'z' && payload.op === 'challenge') {
        /* Derive the response key inside this realm. */
        var replyKey = deriveReplyKey(payload.seed);
        var tokenObj = { op: 'token', key: replyKey, sig: payload.sig + 1 };
        var tokenFrame = JSON.stringify([
          btoa(JSON.stringify(tokenObj)), 'z', envelope[2]
        ]);
        port.postMessage(tokenFrame);
        window.__widget_state.stage = 'token-sent';
        window.__widget_state.keys.push(replyKey);
      }
    };
    /* The widget ACKs setup through the port proxy — proving the
       materialized proxy end routes back to the parent realm. */
    port.postMessage(JSON.stringify([btoa(JSON.stringify({op:'ready'})), 'z', '0']));
    window.__widget_state.stage = 'ready';
  }
});
</script>
</body></html>"""

PARENT_HTML = """<!doctype html><html><body>
<iframe id="wgt" src="https://widget.example/challenge.html"></iframe>
<script>
window.__parent_frames = [];
window.__launch = function (port) {
  /* The real parent->frame lane: postMessage through the iframe's
     contentWindow with the port transferred. */
  var wgt = document.getElementById('wgt');
  wgt.contentWindow.postMessage('widget-launch', '*', [port]);
};
</script>
</body></html>"""


def _client():
    return httpx.Client()


def _frame_doc(widget_info, html):
    return FrameDocument(
        ref="widget", parent_ref="", url=WIDGET_URL, title="w", name="",
        depth=1, content_type="text/html", html=html,
        js_info=widget_info, origin_url=WIDGET_URL, seed_src=WIDGET_URL,
    )


async def _main():
    client = _client()

    # Parent realm.
    _, parent_info = await run_scripts(PARENT_HTML, PARENT_URL, client, force=True)
    pctx = parent_info["ctx"]
    assert pctx is not None

    # Widget realm.
    _, widget_info = await run_scripts(WIDGET_HTML, WIDGET_URL, client, force=True)
    wctx = widget_info["ctx"]
    assert wctx is not None

    # 1. Parent creates the channel; the kept end collects the widget's
    #    frames; launch transfers port2 through the iframe contentWindow.
    pctx.eval(
        "var ch = new MessageChannel();"
        "ch.port1.onmessage = function (me) { window.__parent_frames.push(String(me.data)); };"
        "window.__launch(ch.port2);"
    )
    # Deliver the launch frame to the widget realm via the router. The
    # launch post was made OUTSIDE any run_scripts pass (a bare eval), so
    # hand it to the router explicitly — this is exactly the out-of-pass
    # posting lane widgets use when the parent realm drives them directly.
    launch_posts = take_pending_posts(pctx)
    assert launch_posts, "the launch post must be recorded on the context inbox"
    assert launch_posts[0]["ports"], "the launch post must carry the transferred port"
    frame = _frame_doc(widget_info, WIDGET_HTML)
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
        initial_parent_messages=launch_posts,
    )

    # 2. The widget ACKed through the port proxy -> parent kept end.
    acks = json.loads(pctx.eval("JSON.stringify(window.__parent_frames)"))
    assert acks, "widget ready-ACK must route back through the proxy port"
    ack = json.loads(acks[0])
    assert isinstance(ack, list) and ack[1] == "z", f"JSON-text frame contract: {acks[0]!r}"
    ready = json.loads(base64.b64decode(ack[0]))
    assert ready["op"] == "ready", f"widget ready envelope: {ready}"

    # 3. Parent sends the obfuscated challenge through the kept end.
    pctx.eval("ch.port1.postMessage(JSON.stringify([btoa(JSON.stringify({op:'challenge', seed:'widget-seed-42', sig:7})), 'z', 'c1']))")
    challenge_posts = take_pending_posts(pctx)
    assert any(is_port_message(p) for p in challenge_posts), \
        "the challenge must be posted through the port lane"
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
        initial_parent_messages=challenge_posts,
    )

    state = json.loads(wctx.eval("JSON.stringify(window.__widget_state)"))
    assert state["stage"] == "token-sent", f"widget must complete the challenge: {state}"
    assert state["sourceOk"] is True, "sender-identity check must pass for the parent"

    # The token frame reached the parent as an exact string.
    frames = json.loads(pctx.eval("JSON.stringify(window.__parent_frames)"))
    token_frames = [f for f in frames if "c1" in f]
    assert token_frames, f"token frame must reach the parent: {frames}"
    envelope = json.loads(token_frames[-1])
    token = json.loads(base64.b64decode(envelope[0]))
    assert token["op"] == "token"
    assert token["sig"] == 8, "in-VM key derivation result must roundtrip"
    # The reply key is the widget's OWN transform of the seed — verify it
    # matches the same transform computed here (in-VM derivation proof).
    seed = "widget-seed-42"
    rotated = "".join(chr(ord(c) ^ 0x5A) for c in seed)
    expected_key = base64.b64encode(rotated.encode("latin-1", "ignore")).decode()
    assert token["key"] == expected_key, (
        f"obfuscated key must be the widget's in-VM derivation: "
        f"got {token['key']!r}, expected {expected_key!r}"
    )


def test_a1_obfuscated_channel_handshake():
    asyncio.run(_main())


if __name__ == "__main__":
    asyncio.run(_main())
    print("OK")
