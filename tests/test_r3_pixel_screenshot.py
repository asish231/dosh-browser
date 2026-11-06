"""R3: Pixel-accurate screenshot — hermetic tests.

The screenshot lane paints canvas elements with their TRUE raster as an
embedded PNG (deflate stored blocks, deterministic) when the canvas has
2d content. Blank canvases get an honest placeholder, not faked pixels.

The PNG encoder is spec-complete (IHDR/IDAT/IEND, crc32, adler32), so the
embedded image decodes in any viewer. These tests verify the data URL
structure and that the encoded pixels roundtrip against the raster.
"""

import base64
import json
import struct
import zlib

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx(html: str) -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree(html)
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def _decode_png(data_url: str) -> tuple:
    """Minimal PNG decoder: return (width, height, pixels[rgba]) from a
    data:image/png;base64 URL. Validates magic, parses IHDR, and inflates
    the IDAT (stored-block zlib stream) back to raw scanlines."""
    assert data_url.startswith("data:image/png;base64,"), "must be a PNG data URL"
    raw = base64.b64decode(data_url[len("data:image/png;base64,"):])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "PNG magic"
    pos = 8
    width = height = None
    idat = b""
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        payload = raw[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", raw[pos + 8 + length:pos + 12 + length])
        # verify chunk crc
        assert crc == (zlib.crc32(ctype + payload) & 0xFFFFFFFF), f"crc mismatch in {ctype}"
        if ctype == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", payload[:10])
            assert depth == 8 and color == 6, "8-bit RGBA expected"
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            break
        pos += 12 + length
    scanlines = zlib.decompress(idat)
    assert len(scanlines) == height * (1 + width * 4), "scanline size"
    pixels = []
    for y in range(height):
        row = scanlines[y * (1 + width * 4):(y + 1) * (1 + width * 4)]
        assert row[0] == 0, "filter byte must be 0 (None)"
        for x in range(width):
            i = 1 + x * 4
            pixels.append(tuple(row[i:i + 4]))
    return width, height, pixels


def test_screenshot_embeds_true_canvas_pixels():
    ctx = _ctx('<html><body><canvas id="c" width="8" height="8"></canvas></body></html>')
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#3477eb';
        g.fillRect(0, 0, 8, 8);
    """)
    svg = ctx.eval("__screenshotSVG()")
    # The canvas must be embedded as a PNG data URL, not a placeholder
    assert 'data:image/png;base64,' in svg, "drawn canvas must embed a PNG"
    assert '>canvas<' not in svg, "a drawn canvas must not get the blank placeholder"
    # Extract and decode the embedded PNG
    start = svg.index('data:image/png;base64,') + len('data:image/png;base64,')
    end = svg.index('"', start)
    w, h, pixels = _decode_png("data:image/png;base64," + svg[start:end])
    assert (w, h) == (8, 8)
    # Every pixel is the drawn fill color
    for px in pixels:
        assert px == (0x34, 0x77, 0xEB, 255), f"pixel mismatch: {px}"


def test_screenshot_blank_canvas_gets_placeholder():
    ctx = _ctx('<html><body><canvas id="c" width="8" height="8"></canvas></body></html>')
    ctx.eval("document.getElementById('c').getContext('2d');")
    svg = ctx.eval("__screenshotSVG()")
    assert 'data:image/png;base64,' not in svg, "blank canvas must not fake pixels"
    assert ">canvas<" in svg, "blank canvas gets the honest placeholder"


def test_png_encoder_roundtrip_partial_draw():
    """A partially-drawn canvas keeps transparent pixels transparent."""
    ctx = _ctx('<html><body><canvas id="c" width="4" height="4"></canvas></body></html>')
    ctx.eval("""
        var g = document.getElementById('c').getContext('2d');
        g.fillStyle = '#ff0000';
        g.fillRect(0, 0, 2, 2);
    """)
    svg = ctx.eval("__screenshotSVG()")
    start = svg.index('data:image/png;base64,') + len('data:image/png;base64,')
    end = svg.index('"', start)
    w, h, pixels = _decode_png("data:image/png;base64," + svg[start:end])
    assert (w, h) == (4, 4)
    assert pixels[0] == (255, 0, 0, 255), "top-left is drawn red"
    assert pixels[3 + 3 * 4] == (0, 0, 0, 0), "bottom-right stays transparent"
    assert pixels[2] == (0, 0, 0, 0), "right of the fill stays transparent"
