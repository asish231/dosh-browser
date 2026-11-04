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
