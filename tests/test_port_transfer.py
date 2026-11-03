"""Cross-realm MessagePort transfer regressions.

Widgets hand a channel end across documents — reCAPTCHA answers its
recaptcha-setup handshake only through a transferred MessagePort (the
receiver requires `event.ports.length > 0` and replies through that port).
Before the transfer model these messages carried `ports: []` and the
handshake died silently.

Covers:
- the legacy form postMessage(data, [ports]) — live ends in the same realm;
- the modern form postMessage(data, targetOrigin, [ports]) across realms,
  with a proxy end on the receiving side and a rewired kept end on the
  sending side, so both directions of the split channel can talk;
- transferring an already-neutered end doesn't resurrect it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.frames import (
    FrameDocument,
    frame_message_roundtrip,
)
from webresearch.engine.runtime import _pump_jobs, run_scripts
from webresearch.types import FetchResult

PARENT_URL = "https://parent.example/page.html"
FRAME_URL = "https://frame.example/widget.html"

PARENT_HTML = """<!doctype html><html><body>
<script>
window.__parent_log = [];
window.addEventListener('message', function (e) {
  var rec = { data: String(e.data), ports: (e.ports || []).length, trusted: !!e.isTrusted };
  if (e.ports && e.ports.length > 0) {
    var port = e.ports[0];
    port.onmessage = function (me) {
      window.__parent_log.push('parent-got:' + String(me.data));
    };
    /* The real handshake direction: the parent answers setup THROUGH the
       transferred port, not the window lane. */
    if (e.data === 'setup-with-port') port.postMessage('parent-via-port');
  }
  window.__parent_log.push(JSON.stringify(rec));
});
</script>
</body></html>"""

FRAME_HTML = """<!doctype html><html><body>
<script>
window.__child_log = [];
var ch = new MessageChannel();
ch.port1.onmessage = function (e) {
  window.__child_log.push('child-got:' + String(e.data));
  ch.port1.postMessage('child-ack');
};
// Modern form: (data, targetOrigin, transfer) across realms.
parent.postMessage('setup-with-port', 'https://parent.example:443', [ch.port2]);
</script>
</body></html>"""


def _fake_client():
    import httpx

    client = httpx.Client()
    client.headers = {"user-agent": "test-port-transfer"}
    return client


def _fetch_result(html, url):
    return FetchResult(
        url=url, final_url=url, status=200, content_type="text/html",
        kind="html", body=html,
    )


def _drain(ctx, passes: int = 6) -> None:
    """Mirror the runtime's pump: promise jobs AND timers, interleaved."""
    for _ in range(passes):
        _pump_jobs(ctx)
        try:
            left = int(ctx.eval("__drainTimers()") or 0)
        except Exception:
            left = 0
        if not left:
            break
    _pump_jobs(ctx)


async def _main():
    client = _fake_client()

    # 1. Parent realm.
    _, parent_info = await run_scripts(PARENT_HTML, PARENT_URL, client, force=True)
    pctx = parent_info["ctx"]
    assert pctx is not None, "parent realm must have a live context"
    pnonce = pctx.eval("String(globalThis.__WR_CTX_KEY || '')")
    assert pnonce, "context nonce must be injected"

    # 2. Frame realm.
    _, frame_info = await run_scripts(FRAME_HTML, FRAME_URL, client, force=True)
    fctx = frame_info["ctx"]
    fnonce = fctx.eval("String(globalThis.__WR_CTX_KEY || '')")
    assert fnonce and fnonce != pnonce, "frames need their own realm nonce"

    # Legacy self-lane: postMessage(data, [ports]) keeps the live end local.
    fctx.eval(
        "var lc = new MessageChannel(); window.__self_inbox = [];"
        "window.addEventListener('message', function (e) {"
        "  if (e.data === 'legacy-self' && e.ports && e.ports.length) {"
        "    e.ports[0].onmessage = function (me) { window.__self_inbox.push(String(me.data)); };"
        "  }"
        "});"
        "postMessage('legacy-self', [lc.port2]);"
    )
    _drain(fctx)
    fctx.eval("lc.port1.postMessage('self-ping')")
    _drain(fctx)
    inbox = json.loads(fctx.eval("JSON.stringify(window.__self_inbox)"))
    assert inbox == ["self-ping"], f"legacy self-lane with ports must deliver live ends: {inbox}"

    # 3. Roundtrip: frame's setup message (carrying the transferred end)
    #    reaches the parent; the parent replies through the port; the frame's
    #    kept end hears it and answers.
    frame = FrameDocument(
        ref="f1", parent_ref="", url=FRAME_URL, title="w", name="",
        depth=1, content_type="text/html", html=FRAME_HTML,
        js_info=frame_info, origin_url=FRAME_URL, seed_src=FRAME_URL,
    )
    summary = await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    log = json.loads(pctx.eval("JSON.stringify(window.__parent_log)"))
    setups = [l for l in log if '"setup-with-port"' in l]
    assert setups, f"parent never received the setup message: {log}"
    rec = json.loads(setups[0])
    assert rec["ports"] == 1, f"receiver must see the transferred port: {rec}"
    assert rec["trusted"] is True, "router-delivered messages are UA events (trusted)"
    assert summary["port_wires"] >= 1, f"kept end must be rewired to the proxy: {summary}"
    assert "parent-got:child-ack" in log, f"parent never heard through the channel: {log}"

    child_log = json.loads(fctx.eval("JSON.stringify(window.__child_log)"))
    assert "child-got:parent-via-port" in child_log, (
        f"kept end never received the parent's reply: {child_log}"
    )

    # 4. Second channel: transfer, then prove the neutered end is inert in
    #    the sender's realm while the kept end still routes to the receiver.
    fctx.eval("var z = new MessageChannel(); parent.postMessage('z-setup', '*', [z.port2]);")
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    neutered = fctx.eval("String(z.port2._neutered)")
    assert neutered == "true", "the transferred end must be neutered in the sender's realm"

    # Posting through the neutered end: reported, and nothing arrives.
    fctx.eval("z.port2.postMessage('z-neutered-post')")
    assert "z-neutered-post" not in pctx.eval("JSON.stringify(window.__parent_log)"), (
        "a neutered end must not deliver"
    )
    frame_errors = json.loads(fctx.eval("JSON.stringify(globalThis.__WR_EVENT_ERRORS || [])"))
    assert any("inert" in str(e) for e in frame_errors), (
        f"posting through a neutered end must be reported: {frame_errors}"
    )

    # The KEPT end of the second channel routes to the parent's proxy.
    fctx.eval("z.port1.postMessage('z-kept-post')")
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    recs = json.loads(pctx.eval("JSON.stringify(window.__parent_log)"))
    assert "parent-got:z-kept-post" in recs, (
        f"kept end of the second channel must route to the parent: {recs}"
    )
    assert "parent-got:z-neutered-post" not in recs, "neutered end leaked a delivery"

    # 5. Buffering: the anchor posts through its kept end BEFORE the far
    #    realm's proxy exists (this is the real handshake's initial send —
    #    it used to vanish into the neutered twin's dead local lane). The
    #    messages must queue and flush once the channel is wired.
    fctx.eval(
        "var w = new MessageChannel();"
        "parent.postMessage('w-setup', '*', [w.port2]);"
        "w.port1.postMessage('w-buffered-1');"
        "w.port1.postMessage('w-buffered-2');"
    )
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    recs = json.loads(pctx.eval("JSON.stringify(window.__parent_log)"))
    assert "parent-got:w-buffered-1" in recs and "parent-got:w-buffered-2" in recs, (
        f"buffered posts must flush after wiring: {recs}"
    )

    # 6. Queue-until-started: messages that arrive BEFORE the receiver
    #    attaches must wait on the port and flush when it does (spec
    #    behavior; delivering them early into the void is how the widget's
    #    first channel requests got lost).
    fctx.eval(
        "var q = new MessageChannel();"
        "parent.postMessage('q-setup', '*', [q.port2]);"
    )
    # q-setup delivers and wires q.port1, but this time post through the kept
    # end BEFORE the parent's listener runs: the flush lands in the parent's
    # port with no handler attached yet (the parent's PARENT_HTML listener
    # only attaches onmessage when the setup arrives with ports).
    fctx.eval("q.port1.postMessage('q-early-1');")
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    # The parent's listener (from the q-setup delivery) attaches onmessage on
    # the q-proxy, which must flush any queued early posts.
    recs = json.loads(pctx.eval("JSON.stringify(window.__parent_log)"))
    assert "parent-got:q-early-1" in recs, (
        f"posts delivered before the receiver attached must queue, not die: {recs}"
    )

    # 7. Binary channel data. Cross-realm port posts used to JSON.stringify
    #    the payload, and a Uint8Array became {} on the far side. The bytes
    #    have to arrive as a typed array (and an ArrayBuffer as an ArrayBuffer).
    pctx.eval(
        "window.__bin = null;"
        "window.addEventListener('message', function (e) {"
        "  if (e.data === 'bin-setup' && e.ports && e.ports.length) {"
        "    e.ports[0].onmessage = function (me) {"
        "      var d = me.data;"
        "      if (d instanceof Uint8Array) window.__bin = {kind:'u8', bytes: Array.prototype.slice.call(d)};"
        "      else if (d instanceof ArrayBuffer) window.__bin = {kind:'ab', bytes: Array.prototype.slice.call(new Uint8Array(d))};"
        "      else window.__bin = {kind: typeof d, text: String(d).slice(0, 120)};"
        "    };"
        "  }"
        "});"
    )
    fctx.eval(
        "var bch = new MessageChannel();"
        "parent.postMessage('bin-setup', '*', [bch.port2]);"
    )
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    fctx.eval("bch.port1.postMessage(new Uint8Array([1, 2, 250, 0, 255]));")
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    got = json.loads(pctx.eval("JSON.stringify(window.__bin)"))
    assert got == {"kind": "u8", "bytes": [1, 2, 250, 0, 255]}, (
        f"Uint8Array must survive the port hop: {got}"
    )
    fctx.eval("bch.port1.postMessage(new Uint8Array([9, 8, 7]).buffer);")
    await frame_message_roundtrip(
        [frame],
        parent_ctx=pctx,
        parent_url=PARENT_URL,
        parent_html=PARENT_HTML,
        client=client,
    )
    got = json.loads(pctx.eval("JSON.stringify(window.__bin)"))
    assert got == {"kind": "ab", "bytes": [9, 8, 7]}, (
        f"ArrayBuffer must survive the port hop: {got}"
    )


def test_port_transfer_roundtrip():
    asyncio.run(_main())


if __name__ == "__main__":
    asyncio.run(_main())
    print("OK")
