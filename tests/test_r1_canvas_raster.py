"""R1: Canvas 2D software raster — hermetic tests.

The previous makeContext2D was a full no-op: fillRect did nothing and
getImageData returned deterministic PSEUDO-NOISE that never reflected
what was drawn. These tests pin the true software raster: pixels that
drawing commands actually wrote, read back via getImageData.

Scope (disclosed in the engine): rect fills/strokes, clear, Bresenham
lines, midpoint circles, drawImage from another canvas, put/getImageData,
and block-glyph fillText (a 5x7 microfont — legible, not a font engine).
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx(html: str = '<html><body><canvas id="c" width="40" height="30"></canvas></body></html>') -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree(html)
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def _px(ctx: Context, x: int, y: int) -> list:
    return json.loads(ctx.eval(
        "(function() {"
        "  var cv = document.getElementById('c');"
        "  var g = cv.getContext('2d');"
        "  var img = g.getImageData(" + str(x) + ", " + str(y) + ", 1, 1);"
        "  return JSON.stringify([img.data[0], img.data[1], img.data[2], img.data[3]]);"
        "})()"
    ))


def test_fill_rect_writes_true_pixels():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#ff0000';
        g.fillRect(0, 0, 10, 10);
    """)
    # Inside the fill: pure red
    assert _px(ctx, 5, 5) == [255, 0, 0, 255]
    # Outside the fill: untouched (transparent black)
    assert _px(ctx, 20, 20) == [0, 0, 0, 0]


def test_clear_rect_erasures():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#00ff00';
        g.fillRect(0, 0, 40, 30);
        g.clearRect(5, 5, 10, 10);
    """)
    assert _px(ctx, 1, 1) == [0, 255, 0, 255], "outside clearRect keeps the fill"
    assert _px(ctx, 10, 10) == [0, 0, 0, 0], "inside clearRect is erased"


def test_stroke_rect_draws_borders_only():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.strokeStyle = '#0000ff';
        g.lineWidth = 2;
        g.strokeRect(5, 5, 20, 15);
    """)
    # On the border: blue
    assert _px(ctx, 5, 12) == [0, 0, 255, 255]
    # In the middle: untouched
    assert _px(ctx, 15, 12) == [0, 0, 0, 0]


def test_line_bresenham():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.strokeStyle = '#ffffff';
        g.beginPath();
        g.moveTo(0, 0);
        g.lineTo(39, 29);
        g.stroke();
    """)
    # A point on the diagonal: white
    assert _px(ctx, 20, 15) == [255, 255, 255, 255]
    # Off the diagonal: untouched
    assert _px(ctx, 20, 10) == [0, 0, 0, 0]


def test_arc_fill_circle():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#123456';
        g.beginPath();
        g.arc(20, 15, 8, 0, Math.PI * 2);
        g.fill();
    """)
    assert _px(ctx, 20, 15) == [0x12, 0x34, 0x56, 255]
    # Outside the circle radius
    assert _px(ctx, 20, 2) == [0, 0, 0, 0]


def test_fill_text_draws_glyph_pixels():
    ctx = _ctx()
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#ffffff';
        g.font = '14px sans-serif';
        g.fillText('HI', 1, 14);
    """)
    # 'H' glyph occupies roughly x=1..5 (scale=2: 5*2 px wide), rows 0..13
    # At (2, 5): inside H's left vertical stroke (x=1..2 at scale 2)
    hit = _px(ctx, 2, 5)
    assert hit == [255, 255, 255, 255], f"H stroke should be white, got {hit}"
    # Blank region right of the glyphs
    assert _px(ctx, 30, 5) == [0, 0, 0, 0]


def test_put_then_get_image_data_roundtrip():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var g = document.getElementById('c').getContext('2d');
            var src = g.createImageData(4, 4);
            for (var i = 0; i < src.data.length; i++) src.data[i] = (i * 7) % 256;
            g.putImageData(src, 10, 10);
            var back = g.getImageData(10, 10, 4, 4);
            var ok = true;
            for (var j = 0; j < back.data.length; j++) {
                if (back.data[j] !== ((j * 7) % 256)) { ok = false; break; }
            }
            return JSON.stringify({roundtrip: ok, firstPx: [back.data[0], back.data[1], back.data[2], back.data[3]]});
        })()
    """))
    assert got["roundtrip"] is True, "putImageData -> getImageData must roundtrip exactly"
    assert got["firstPx"] == [0, 7, 14, 21]


def test_draw_image_copies_canvas_pixels():
    html = ('<html><body>'
            '<canvas id="src" width="8" height="8"></canvas>'
            '<canvas id="dst" width="40" height="30"></canvas>'
            '</body></html>')
    ctx = _ctx(html)
    got = json.loads(ctx.eval("""
        (function() {
            var s = document.getElementById('src').getContext('2d');
            s.fillStyle = '#abcdef';
            s.fillRect(0, 0, 8, 8);
            var d = document.getElementById('dst').getContext('2d');
            d.drawImage(document.getElementById('src'), 2, 3);
            var a = d.getImageData(2, 3, 1, 1);
            var b = d.getImageData(0, 0, 1, 1);
            return JSON.stringify({
                copied: [a.data[0], a.data[1], a.data[2], a.data[3]],
                untouched: [b.data[0], b.data[1], b.data[2], b.data[3]]
            });
        })()
    """))
    assert got["copied"] == [0xAB, 0xCD, 0xEF, 255], f"drawImage must copy true pixels, got {got['copied']}"
    assert got["untouched"] == [0, 0, 0, 0]


def test_raster_reflects_drawn_state_not_noise():
    """Two identical canvases with DIFFERENT draws must read back different
    pixels — the old pseudo-noise stub ignored what was drawn entirely."""
    html = ('<html><body>'
            '<canvas id="a" width="10" height="10"></canvas>'
            '<canvas id="b" width="10" height="10"></canvas>'
            '</body></html>')
    ctx = _ctx(html)
    got = json.loads(ctx.eval("""
        (function() {
            var ga = document.getElementById('a').getContext('2d');
            var gb = document.getElementById('b').getContext('2d');
            ga.fillStyle = '#ff0000';
            ga.fillRect(0, 0, 10, 10);
            gb.fillStyle = '#0000ff';
            gb.fillRect(0, 0, 10, 10);
            var pa = ga.getImageData(5, 5, 1, 1).data;
            var pb = gb.getImageData(5, 5, 1, 1).data;
            return JSON.stringify({
                a: [pa[0], pa[1], pa[2], pa[3]],
                b: [pb[0], pb[1], pb[2], pb[3]],
                differs: pa[0] !== pb[0] || pa[2] !== pb[2]
            });
        })()
    """))
    assert got["a"] == [255, 0, 0, 255]
    assert got["b"] == [0, 0, 255, 255]
    assert got["differs"] is True
