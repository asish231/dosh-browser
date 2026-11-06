"""M2: MediaStream / capture — hermetic tests.

Before M2: getUserMedia rejected NotSupportedError, MediaStream was an
empty shell, captureStream returned zero tracks, MediaRecorder did not
exist. These tests pin the real (honest) capture surface:

- getUserMedia returns a real MediaStream with real MediaStreamTracks
  (kind from constraints), tracks are live, stop() ends them and fires
  onended.
- enumerateDevices reports the virtual devices the engine provides.
- canvas.captureStream() produces a video track bound to the canvas
  raster (true width/height in getSettings).
- MediaRecorder records a structured webm chunk; the container bytes
  are genuine EBML/webm markers, and the payload is the track's honest
  silent frames. The engine discloses it does not capture real mic/
  camera content — structure is real, content is silence/black.
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx(html: str = "<html><body></body></html>") -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree(html)
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def _pump(ctx: Context, rounds: int = 50) -> None:
    for _ in range(rounds):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break


def _gum(ctx: Context, constraints: str = "{ audio: true }") -> str:
    """getUserMedia, storing the stream into globalThis.__STREAM via then;
    pump; return eval-able reference expression."""
    ctx.eval(
        "globalThis.__STREAM = null;"
        "navigator.mediaDevices.getUserMedia(" + constraints + ").then(function (s) {"
        "  globalThis.__STREAM = s;"
        "});"
    )
    _pump(ctx)
    assert ctx.eval("globalThis.__STREAM ? 'ok' : 'null'") == "ok", \
        "getUserMedia promise must resolve to a MediaStream"
    return "globalThis.__STREAM"


def test_get_user_media_audio_stream():
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true }")
    got = json.loads(ctx.eval(
        "(function() {"
        "  var res = " + S + ";"
        "  var tracks = res.getTracks();"
        "  return JSON.stringify({"
        "    active: res.active,"
        "    trackCount: tracks.length,"
        "    audioCount: res.getAudioTracks().length,"
        "    kind: tracks[0].kind,"
        "    state: tracks[0].readyState,"
        "    hasId: !!tracks[0].id"
        "  });"
        "})()"
    ))
    assert got["active"] is True
    assert got["trackCount"] == 1
    assert got["audioCount"] == 1
    assert got["kind"] == "audio"
    assert got["state"] == "live"
    assert got["hasId"] is True


def test_track_stop_ends_and_fires_onended():
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true }")
    got = json.loads(ctx.eval(
        "(function() {"
        "  var res = " + S + ";"
        "  var t = res.getTracks()[0];"
        "  var endedFired = false;"
        "  t.onended = function () { endedFired = true; };"
        "  t.stop();"
        "  return JSON.stringify({"
        "    state: t.readyState,"
        "    endedFired: endedFired,"
        "    streamActive: res.active"
        "  });"
        "})()"
    ))
    assert got["state"] == "ended"
    assert got["endedFired"] is True, "stop() must fire onended"
    assert got["streamActive"] is False


def test_get_user_media_constraints_video():
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true, video: true }")
    got = json.loads(ctx.eval(
        "(function() {"
        "  var res = " + S + ";"
        "  return JSON.stringify({"
        "    tracks: res.getTracks().length,"
        "    video: res.getVideoTracks().length,"
        "    audio: res.getAudioTracks().length"
        "  });"
        "})()"
    ))
    assert got["tracks"] == 2
    assert got["video"] == 1
    assert got["audio"] == 1


def test_enumerate_devices_reports_virtual_devices():
    ctx = _ctx()
    ctx.eval(
        "globalThis.__DEVS = null;"
        "navigator.mediaDevices.enumerateDevices().then(function (d) { globalThis.__DEVS = d; });"
    )
    _pump(ctx)
    got = json.loads(ctx.eval("JSON.stringify(globalThis.__DEVS || [])"))
    assert len(got) >= 2, f"virtual audio in/out expected, got {got}"
    kinds = {d["kind"] for d in got}
    assert "audioinput" in kinds and "audiooutput" in kinds


def test_canvas_capture_stream_track_bound_to_raster():
    ctx = _ctx('<html><body><canvas id="cv" width="320" height="240"></canvas></body></html>')
    got = json.loads(ctx.eval("""
        (function() {
            var cv = document.getElementById('cv');
            var stream = cv.captureStream(30);
            var t = stream.getTracks()[0];
            var settings = t.getSettings();
            return JSON.stringify({
                trackCount: stream.getTracks().length,
                kind: t.kind,
                width: settings.width,
                height: settings.height,
                frameRate: settings.frameRate,
                boundCanvas: t.__canvas === cv
            });
        })()
    """))
    assert got["trackCount"] == 1
    assert got["kind"] == "video"
    assert got["width"] == 320 and got["height"] == 240, "settings report the canvas's true size"
    assert got["frameRate"] == 30
    assert got["boundCanvas"] is True


def test_media_recorder_records_webm_chunk():
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true }")
    got = json.loads(ctx.eval(
        "(function() {"
        "  var res = " + S + ";"
        "  var rec = new MediaRecorder(res);"
        "  globalThis.__CHUNK = null;"
        "  rec.ondataavailable = function (e) { globalThis.__CHUNK = e.data; };"
        "  rec.start();"
        "  rec.stop();"
        "  var chunk = globalThis.__CHUNK;"
        "  return JSON.stringify({"
        "    state: rec.state,"
        "    hasChunk: !!chunk,"
        "    chunkType: chunk ? chunk.type : null,"
        "    chunkSize: chunk ? chunk.size : 0"
        "  });"
        "})()"
    ))
    assert got["state"] == "inactive"
    assert got["hasChunk"] is True, "stop() must emit a final dataavailable chunk"
    assert got["chunkType"] == "audio/webm"
    assert got["chunkSize"] > 0


def test_media_recorder_webm_magic_bytes():
    """The recorded blob carries the genuine EBML/webm magic."""
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true }")
    ctx.eval(
        "var rec = new MediaRecorder(" + S + ");"
        "globalThis.__CHUNK_BYTES = null;"
        "rec.ondataavailable = function (e) {"
        "  e.data.text().then(function (t) {"
        "    var bytes = [];"
        "    for (var i = 0; i < Math.min(16, t.length); i++) bytes.push(t.charCodeAt(i) & 0xFF);"
        "    globalThis.__CHUNK_BYTES = bytes;"
        "  });"
        "};"
        "rec.start();"
        "rec.stop();"
    )
    _pump(ctx)
    head = json.loads(ctx.eval("JSON.stringify(globalThis.__CHUNK_BYTES || [])"))
    assert head, "blob bytes must be readable"
    assert head[0] == 0x1A and head[1] == 0x45, f"EBML magic must start the webm, got {head[:4]}"
    doc_type = "".join(chr(b) if 32 <= b < 127 else "?" for b in head[11:15])
    assert "webm" in doc_type, f"DocType must be webm, head={head}"


def test_media_recorder_double_start_throws():
    ctx = _ctx()
    S = _gum(ctx, "{ audio: true }")
    err = ctx.eval(
        "(function() {"
        "  var rec = new MediaRecorder(" + S + ");"
        "  rec.start();"
        "  try { rec.start(); return 'no-throw'; }"
        "  catch (e) { return 'throws'; }"
        "})()"
    )
    assert err == "throws", "a second start() must throw InvalidStateError (spec)"
