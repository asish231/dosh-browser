"""R2: WebGL software path — hermetic tests.

The WebGL context previously had a pure no-op pipeline and readPixels
returned deterministic PSEUDO-NOISE (an anti-fingerprint pattern that
ignored what was drawn). R2 adds an honest software framebuffer:

- clearColor()/clear() fill the real RGBA buffer.
- drawArrays rasterizes TRIANGLES/TRIANGLE_STRIP as flat-colored
  primitives (barycentric fill) in clip space, with the u_color uniform
  (fallback white).
- readPixels returns TRUE framebuffer bytes, bottom-up per spec.

Disclosed in-engine: shaders are not executed (no GLSL compiler) —
rasterization is flat-colored geometry, not per-fragment shading.
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx(html: str = '<html><body><canvas id="gl" width="16" height="16"></canvas></body></html>') -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree(html)
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def _px(ctx: Context, x: int, y: int) -> list:
    """Read one pixel via readPixels (bottom-up GL coordinates)."""
    return json.loads(ctx.eval(
        "(function() {"
        "  var gl = document.getElementById('gl').getContext('webgl');"
        "  var p = new Uint8Array(4);"
        "  gl.readPixels(" + str(x) + ", " + str(y) + ", 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p);"
        "  return JSON.stringify([p[0], p[1], p[2], p[3]]);"
        "})()"
    ))


def test_clear_color_fills_framebuffer():
    ctx = _ctx()
    ctx.eval("""
        var gl = document.getElementById('gl').getContext('webgl');
        gl.clearColor(0.25, 0.5, 0.75, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT);
    """)
    # Any pixel: the clear color (255*c rounded)
    px = _px(ctx, 5, 5)
    assert px == [64, 128, 191, 255], f"clear must fill the fb, got {px}"


def test_clear_then_read_all_pixels():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var gl = document.getElementById('gl').getContext('webgl');
            gl.clearColor(1, 0, 0, 1);
            gl.clear(gl.COLOR_BUFFER_BIT);
            var p = new Uint8Array(16 * 16 * 4);
            gl.readPixels(0, 0, 16, 16, gl.RGBA, gl.UNSIGNED_BYTE, p);
            var all = true;
            for (var i = 0; i < p.length; i += 4) {
                if (p[i] !== 255 || p[i+1] !== 0 || p[i+2] !== 0 || p[i+3] !== 255) { all = false; break; }
            }
            return JSON.stringify({allRed: all});
        })()
    """))
    assert got["allRed"] is True


def test_draw_arrays_rasterizes_triangle():
    ctx = _ctx()
    ctx.eval("""
        var gl = document.getElementById('gl').getContext('webgl');
        var prog = gl.createProgram();
        gl.useProgram(prog);
        var buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        /* fullscreen quad via TRIANGLE_STRIP */
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
            -1, -1,   1, -1,   -1, 1,   1, 1
        ]), gl.STATIC_DRAW);
        gl.uniform4f(gl.getUniformLocation(prog, 'u_color'), 0.0, 1.0, 0.0, 1.0);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    """)
    # Center of the quad: green
    px = _px(ctx, 8, 8)
    assert px == [0, 255, 0, 255], f"drawn triangle must rasterize u_color, got {px}"


def test_draw_respects_clip_space_geometry():
    """A small triangle only covers part of the framebuffer."""
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var gl = document.getElementById('gl').getContext('webgl');
            var prog = gl.createProgram();
            gl.useProgram(prog);
            var buf = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, buf);
            /* small triangle in the bottom-left quadrant (clip space) */
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
                -1, -1,   -0.5, -1,   -1, -0.5
            ]), gl.STATIC_DRAW);
            gl.uniform4f(gl.getUniformLocation(prog, 'u_color'), 1, 0, 0, 1);
            gl.drawArrays(gl.TRIANGLES, 0, 3);
            var inTri = new Uint8Array(4);
            var farAway = new Uint8Array(4);
            /* GL bottom-left: the triangle lives near readPixels (0,0) */
            gl.readPixels(1, 1, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, inTri);
            /* top-right of the fb is untouched */
            gl.readPixels(15, 15, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, farAway);
            return JSON.stringify({
                inTri: [inTri[0], inTri[1], inTri[2], inTri[3]],
                farAway: [farAway[0], farAway[1], farAway[2], farAway[3]]
            });
        })()
    """))
    assert got["inTri"] == [255, 0, 0, 255], f"inside the triangle is red, got {got['inTri']}"
    assert got["farAway"] == [0, 0, 0, 0], f"outside stays untouched, got {got['farAway']}"


def test_read_pixels_unsupported_format_throws():
    ctx = _ctx()
    err = ctx.eval("""
        (function() {
            var gl = document.getElementById('gl').getContext('webgl');
            try {
                gl.readPixels(0, 0, 1, 1, gl.RGB, gl.UNSIGNED_BYTE, new Uint8Array(3));
                return 'no-throw';
            } catch (e) {
                return e && e.message ? e.message : String(e);
            }
        })()
    """)
    assert "RGBA" in str(err) or "UNSIGNED_BYTE" in str(err), f"unsupported format must throw honestly, got {err!r}"


def test_no_clear_no_draw_reads_transparent():
    """An untouched framebuffer reads back transparent black — the old
    pseudo-noise stub could never do that."""
    ctx = _ctx()
    ctx.eval("document.getElementById('gl').getContext('webgl');")
    px = _px(ctx, 3, 3)
    assert px == [0, 0, 0, 0], f"untouched fb must be transparent, got {px}"
