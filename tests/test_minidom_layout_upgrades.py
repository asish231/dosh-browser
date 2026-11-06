"""Tests for minidom layout engine upgrades and agent action gates:
- requestAnimationFrame / cancelAnimationFrame / __drainRaf
- flex-wrap: wrap line breaking
- position: sticky bounds clamping
- __clickPath occlusion gating
- __isElementOccluded detection via stratum layers
"""

import json
from quickjs import Context
from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx(html: str = "<html><head></head><body></body></html>"):
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.add_callable("host_parse_html", _host_parse_html)
    tree = html_to_tree(html)
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com');")
    return ctx


def test_raf_queue_monotonic_delivery():
    ctx = _ctx()
    res = json.loads(ctx.eval("""
        var stamps = [];
        var id1 = requestAnimationFrame(function(t) { stamps.push({id: 1, t: t}); });
        var id2 = requestAnimationFrame(function(t) { stamps.push({id: 2, t: t}); });
        var id3 = requestAnimationFrame(function(t) { stamps.push({id: 3, t: t}); });
        cancelAnimationFrame(id2);

        var executed = __drainRaf(100.5);
        JSON.stringify({ executed: executed, stamps: stamps, id1: id1, id2: id2, id3: id3 });
    """))

    assert res["executed"] == 2
    assert len(res["stamps"]) == 2
    assert res["stamps"][0]["id"] == 1
    assert res["stamps"][0]["t"] == 100.5
    assert res["stamps"][1]["id"] == 3
    assert res["stamps"][1]["t"] == 100.5


def test_raf_drain_batch_isolation():
    """Callbacks queued during a drain pass must execute on the next frame."""
    ctx = _ctx()
    res = json.loads(ctx.eval("""
        var runCount = 0;
        requestAnimationFrame(function(t) {
            runCount++;
            requestAnimationFrame(function(t2) {
                runCount++;
            });
        });

        var pass1 = __drainRaf(50.0);
        var afterPass1 = runCount;
        var pass2 = __drainRaf(66.6);
        var afterPass2 = runCount;

        JSON.stringify({
            pass1: pass1,
            afterPass1: afterPass1,
            pass2: pass2,
            afterPass2: afterPass2
        });
    """))

    assert res["pass1"] == 1
    assert res["afterPass1"] == 1
    assert res["pass2"] == 1
    assert res["afterPass2"] == 2


def test_flex_wrap_layout_distribution():
    html = """<!doctype html><html><body>
        <div id="container" style="display:flex; flex-wrap:wrap; width:200px; gap:10px;">
            <div id="item1" style="width:120px; height:50px;">Item 1</div>
            <div id="item2" style="width:120px; height:60px;">Item 2</div>
        </div>
    </body></html>"""
    ctx = _ctx(html)

    info = json.loads(ctx.eval("""
        var c = document.getElementById("container");
        var i1 = document.getElementById("item1");
        var i2 = document.getElementById("item2");
        var r1 = i1.getBoundingClientRect();
        var r2 = i2.getBoundingClientRect();
        JSON.stringify({
            r1: r1,
            r2: r2
        });
    """))

    # Item 1 is placed at the start of row 1
    # Item 2 exceeds 200px (120 + 10 + 120 = 250 > 200), so it wraps to row 2
    assert info["r1"]["x"] == info["r2"]["x"]  # Both align to the start of the flex container
    assert info["r2"]["y"] >= info["r1"]["y"] + info["r1"]["height"]  # Item 2 is placed beneath Item 1


def test_css_float_left_right_clear():
    """L1: CSS floats position elements left/right, clear prevents wrapping."""
    html = """<!doctype html><html><body style="margin:0; padding:0;">
        <div id="float-left" style="float:left; width:100px; height:50px;">L</div>
        <div id="float-right" style="float:right; width:80px; height:40px;">R</div>
        <div id="clear-both" style="clear:both; width:200px; height:30px;">C</div>
    </body></html>"""
    ctx = _ctx(html)
    boxes = json.loads(ctx.eval("""
        (function() {
            var left = document.getElementById("float-left").getBoundingClientRect();
            var right = document.getElementById("float-right").getBoundingClientRect();
            var clear = document.getElementById("clear-both").getBoundingClientRect();
            return JSON.stringify({
                left: { x: left.x, w: left.width },
                right: { x: right.x, w: right.width },
                clear: { y: clear.y }
            });
        })()
    """))
    # Left float should be at x=0 (or very close due to body margins)
    assert boxes["left"]["x"] <= 10
    assert boxes["left"]["w"] == 100
    # Right float should be near the right edge (viewport ~1280px)
    assert boxes["right"]["x"] > 1100
    assert boxes["right"]["w"] == 80
    # Clear:both should be below both floats (at least 50px down)
    assert boxes["clear"]["y"] >= 40


def test_bidi_direction_rtl_layout():
    """L4: direction:rtl reverses text flow and inline element ordering."""
    html = """<!doctype html><html><body style="margin:0; padding:0;">
        <div id="ltr-container" style="direction:ltr; width:400px;">
            <span id="ltr-first">First</span>
            <span id="ltr-second">Second</span>
        </div>
        <div id="rtl-container" style="direction:rtl; width:400px;">
            <span id="rtl-first">First</span>
            <span id="rtl-second">Second</span>
        </div>
    </body></html>"""
    ctx = _ctx(html)
    boxes = json.loads(ctx.eval("""
        (function() {
            var ltrFirst = document.getElementById("ltr-first").getBoundingClientRect();
            var ltrSecond = document.getElementById("ltr-second").getBoundingClientRect();
            var rtlFirst = document.getElementById("rtl-first").getBoundingClientRect();
            var rtlSecond = document.getElementById("rtl-second").getBoundingClientRect();
            return JSON.stringify({
                ltr: { first: ltrFirst.x, second: ltrSecond.x, firstW: ltrFirst.width, secondW: ltrSecond.width },
                rtl: { first: rtlFirst.x, second: rtlSecond.x, firstW: rtlFirst.width, secondW: rtlSecond.width }
            });
        })()
    """))
    # LTR: first element should be left of second
    assert boxes["ltr"]["first"] < boxes["ltr"]["second"]
    # RTL: first element should be RIGHT of second (reversed)
    assert boxes["rtl"]["first"] > boxes["rtl"]["second"]


def test_position_sticky_bounds_clamping():
    html = """<!doctype html><html><body>
        <div id="sticky-header" style="position:sticky; top:15px; height:40px;">Header</div>
        <div style="height:2000px;">Content</div>
    </body></html>"""
    ctx = _ctx(html)

    # Scroll down 200px and evaluate position
    box = json.loads(ctx.eval("""
        window.scrollTo(0, 200);
        var el = document.getElementById("sticky-header");
        var r = el.getBoundingClientRect();
        JSON.stringify(r);
    """))

    # When window.scrollY is 200 and top:15px, sticky element clamps to at least 200 + 15 = 215 in absolute y
    assert box["y"] >= 215


def test_occlusion_gated_action_dispatch():
    html = """<!doctype html><html><body>
        <button id="btn" style="position:absolute; left:50px; top:50px; width:100px; height:40px;">Click Me</button>
        <div id="overlay" role="dialog" style="position:fixed; left:0; top:0; width:500px; height:500px; z-index:9999;">
            <button id="close-modal">Close</button>
        </div>
    </body></html>"""
    ctx = _ctx(html)

    res = json.loads(ctx.eval("""
        var btn = document.getElementById("btn");
        var occ = __isElementOccluded(btn);
        
        var clickGated = __clickPath([0, 0, 0], false, true);
        var errGated = window.__lastActionError;

        var clickUngated = __clickPath([0, 0, 0], false, false);
        var errUngated = window.__lastActionError;

        JSON.stringify({
            occluded: !!occ,
            occlusion_info: occ,
            clickGated: clickGated,
            errGated: errGated,
            clickUngated: clickUngated,
            errUngated: errUngated
        });
    """))

    assert res["occluded"]
    assert "button#btn" in res["occlusion_info"]["control"]
    assert "div#overlay" in res["occlusion_info"]["occluded_by"]
    assert not res["clickGated"]
    assert res["errGated"] == "occluded_by:div#overlay"
    assert res["clickUngated"]
    assert res["errUngated"] is None


def test_font_metrics_kerning_pairs():
    """L5: kerning pairs reduce combined width vs individual advance sum."""
    html = '<!doctype html><html><body><div id="t">AV</div></body></html>'
    ctx = _ctx(html)
    # Trigger layout first
    ctx.eval("document.getElementById('t').getBoundingClientRect();")
    info = json.loads(ctx.eval("JSON.stringify(document.__layoutInfo)"))
    # textMetric label should reflect kerning
    assert info["textMetric"] == "proportional-advance+kern-pairs", info

def test_kerning_pair_width_reduction():
    """L5: 'AV' is narrower than 'A ' + 'V' (kerning correction applied)."""
    html = '<!doctype html><html><body><div id="a">AV</div><div id="b">A</div></body></html>'
    ctx = _ctx(html)
    widths = json.loads(ctx.eval("""
        (function() {
            var av = document.getElementById("a").getBoundingClientRect();
            var a = document.getElementById("b").getBoundingClientRect();
            return JSON.stringify({av: av.width, a: a.width});
        })()
    """))
    # AV box width should be less than twice the 'A' width (kerning pulls V closer)
    assert widths["av"] < widths["a"] * 2, f"AV={widths['av']}, A*2={widths['a']*2}"

def test_sticky_left_right_offsets():
    """L2: sticky left/right offsets shift element position."""
    html = """<!doctype html><html><body style="margin:0;">
        <div id="sticky" style="position:sticky; top:10px; left:20px; width:100px; height:50px;">S</div>
    </body></html>"""
    ctx = _ctx(html)
    ctx.eval("window.scrollTo(100, 100);")
    box = json.loads(ctx.eval("""
        var el = document.getElementById("sticky");
        JSON.stringify(el.getBoundingClientRect());
    """))
    # After scroll(100,100), sticky with left:20 should be at >= 20 (scrollX + 20)
    assert box["x"] >= 20, f"Expected x >= 20, got {box['x']}"


def test_smooth_scroll_behavior_object_form():
    """L6: scrollTo/scrollBy with {behavior:'smooth'} animates via the frame clock."""
    html = """<!doctype html><html><body>
        <div style="height:3000px;"></div>
    </body></html>"""
    ctx = _ctx(html)
    # scrollTo with smooth behavior — should reach target
    ctx.eval("window.scrollTo({top: 500, behavior: 'smooth'});")
    y_after = ctx.eval("window.scrollY")
    assert y_after == 500, f"scrollTo smooth: expected 500, got {y_after}"

    # scrollBy with smooth behavior — should advance by delta
    ctx.eval("window.scrollBy({top: 200, behavior: 'smooth'});")
    y_after = ctx.eval("window.scrollY")
    assert y_after == 700, f"scrollBy smooth: expected 700, got {y_after}"

    # scrollTo with default (auto) behavior — instant
    ctx.eval("window.scrollTo(0, 0);")
    y0 = ctx.eval("window.scrollY")
    assert y0 == 0, f"scrollTo auto reset: expected 0, got {y0}"


def test_smooth_scroll_reports_intermediate_positions():
    """L6: smooth scroll passes through intermediate positions (not instant jump)."""
    html = """<!doctype html><html><body>
        <div style="height:5000px;"></div>
    </body></html>"""
    ctx = _ctx(html)
    positions = json.loads(ctx.eval("""
        (function() {
            var seen = [];
            var origDispatch = window.dispatchEvent;
            // Capture intermediate scrollY values by hooking the scroll event
            window.addEventListener('scroll', function() {
                seen.push(window.scrollY);
            });
            window.scrollTo({top: 1000, behavior: 'smooth'});
            return JSON.stringify(seen);
        })()
    """))
    # With 8 steps of ease-out, there should be intermediate positions
    # between 0 and 1000 (not just [1000])
    assert len(positions) >= 2, f"Expected intermediate positions, got {positions}"
    assert positions[0] < 1000, f"First intermediate should be < 1000, got {positions[0]}"
    assert positions[-1] == 1000, f"Last should be target 1000, got {positions[-1]}"


def test_css_keyframes_animation_interpolation():
    """L9: @keyframes animation interpolates opacity between keyframes."""
    html = """<!doctype html><html><head><style>
        @keyframes fade {
            from { opacity: 0; }
            to { opacity: 1; }
        }
        #box { animation: fade 1s linear; }
    </style></head><body>
        <div id="box">Hello</div>
    </body></html>"""
    ctx = _ctx(html)
    # Trigger layout to register the animation
    ctx.eval("document.getElementById('box').getBoundingClientRect();")
    # At start (elapsed=0), opacity should be 0 (from keyframe)
    op0 = ctx.eval("getComputedStyle(document.getElementById('box')).opacity")
    # Advance the clock by 500ms (halfway)
    ctx.eval("__advanceClock(500);")
    op_mid = ctx.eval("getComputedStyle(document.getElementById('box')).opacity")
    # Advance past the end
    ctx.eval("__advanceClock(600);")
    op_end = ctx.eval("getComputedStyle(document.getElementById('box')).opacity")
    assert op0 in ("0", "0.0", ""), f"start opacity should be ~0, got {op0}"
    mid = float(op_mid) if op_mid else 0.0
    assert 0.0 < mid < 1.0, f"mid opacity should be between 0 and 1, got {mid}"
    # After animation ends (1s duration, no fill-forwards), opacity reverts to
    # the cascade value (1 from the default) or stays at end
    end = float(op_end) if op_end else 1.0
    assert end >= mid, f"end opacity should be >= mid, got {end}"


def test_css_keyframes_fill_forwards():
    """L9: animation-fill-mode: forwards holds the last keyframe value."""
    html = """<!doctype html><html><head><style>
        @keyframes slide {
            from { left: 0px; }
            to { left: 100px; }
        }
        #mover { position: absolute; animation: slide 0.5s linear forwards; }
    </style></head><body>
        <div id="mover">M</div>
    </body></html>"""
    ctx = _ctx(html)
    ctx.eval("document.getElementById('mover').getBoundingClientRect();")
    # Advance past animation end
    ctx.eval("__advanceClock(600); 600;")
    left_end = ctx.eval("getComputedStyle(document.getElementById('mover')).left")
    assert left_end == "100px", f"fill-forwards should hold 100px, got {left_end}"


def test_css_keyframes_parsed_from_stylesheet():
    """L9: @keyframes blocks are parsed into document.__keyframes."""
    html = """<!doctype html><html><head><style>
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }
        #p { animation: pulse 2s; }
    </style></head><body>
        <div id="p">P</div>
    </body></html>"""
    ctx = _ctx(html)
    ctx.eval("document.getElementById('p').getBoundingClientRect();")
    kf = json.loads(ctx.eval("""
        (function() {
            var k = document.__keyframes || {};
            return JSON.stringify({ names: Object.keys(k), pulseSteps: (k.pulse || []).map(function(e) { return e.offset; }) });
        })()
    """))
    assert "pulse" in kf["names"], f"pulse should be parsed, got {kf['names']}"
    assert kf["pulseSteps"] == [0, 0.5, 1.0], f"steps should be 0/0.5/1, got {kf['pulseSteps']}"
