"""R4: Paint stack / top layer / ::backdrop — hermetic tests.

Before R4 the engine disclosed "no top-layer rendering (there is no
paint stack)". These tests pin the new machinery:

- A document-owned ordered top layer (__wrTopLayer) that modal dialogs
  and open popovers join/leave.
- ::backdrop pseudo gets a real backing box (__backdropBox) on the
  promoted element, full-viewport.
- The layers inspector reports TOP_LAYER and BACKDROPS strata.
- Stacking order: the most recently promoted element is on top.
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


def test_modal_dialog_joins_top_layer_with_backdrop():
    ctx = _ctx("""
        <html><body>
            <dialog id="dlg">Hello</dialog>
        </body></html>
    """)
    got = json.loads(ctx.eval("""
        (function() {
            var before = globalThis.__wrTopLayer().length;
            var dlg = document.getElementById('dlg');
            dlg.showModal();
            var tl = globalThis.__wrTopLayer();
            var info = JSON.parse(globalThis.__wrTopLayerInfo());
            return JSON.stringify({
                before: before,
                count: tl.length,
                kind: tl.length ? tl[0].kind : null,
                isDlg: tl.length ? tl[0].el === dlg : false,
                hasBackdrop: !!(dlg.__backdropBox),
                backdropW: dlg.__backdropBox ? dlg.__backdropBox.width : 0,
                backdropH: dlg.__backdropBox ? dlg.__backdropBox.height : 0,
                info: info
            });
        })()
    """))
    assert got["before"] == 0, "top layer starts empty"
    assert got["count"] == 1
    assert got["kind"] == "modal"
    assert got["isDlg"] is True
    assert got["hasBackdrop"] is True, "::backdrop must get a real backing box"
    assert got["backdropW"] == 1280 and got["backdropH"] == 720, "backdrop covers the viewport"
    assert got["info"][0]["kind"] == "modal"


def test_close_dialog_leaves_top_layer():
    ctx = _ctx("""
        <html><body><dialog id="dlg">X</dialog></body></html>
    """)
    got = json.loads(ctx.eval("""
        (function() {
            var dlg = document.getElementById('dlg');
            dlg.showModal();
            var during = globalThis.__wrTopLayer().length;
            dlg.close();
            var after = globalThis.__wrTopLayer().length;
            return JSON.stringify({
                during: during,
                after: after,
                backdropAfter: !!dlg.__backdropBox
            });
        })()
    """))
    assert got["during"] == 1
    assert got["after"] == 0, "close() must leave the top layer"
    assert got["backdropAfter"] is False, "backdrop box must be dropped on close"


def test_popover_joins_and_leaves_top_layer():
    ctx = _ctx("""
        <html><body>
            <div id="pop" popover>Menu</div>
        </body></html>
    """)
    got = json.loads(ctx.eval("""
        (function() {
            var pop = document.getElementById('pop');
            var before = globalThis.__wrTopLayer().length;
            pop.showPopover();
            var tl = globalThis.__wrTopLayer();
            var hasBackdrop = !!(pop.__backdropBox);
            pop.hidePopover();
            var after = globalThis.__wrTopLayer().length;
            return JSON.stringify({
                before: before,
                during: tl.length,
                kind: tl.length ? tl[0].kind : null,
                hasBackdrop: hasBackdrop,
                after: after
            });
        })()
    """))
    assert got["before"] == 0
    assert got["during"] == 1
    assert got["kind"] == "popover"
    assert got["hasBackdrop"] is True
    assert got["after"] == 0


def test_top_layer_stacking_order_last_promoted_on_top():
    ctx = _ctx("""
        <html><body>
            <dialog id="a">A</dialog>
            <div id="b" popover>B</div>
        </body></html>
    """)
    got = json.loads(ctx.eval("""
        (function() {
            var a = document.getElementById('a');
            var b = document.getElementById('b');
            a.showModal();
            b.showPopover();
            var tl = globalThis.__wrTopLayer();
            return JSON.stringify({
                count: tl.length,
                firstKind: tl[0].kind,
                lastKind: tl[tl.length - 1].kind,
                lastIsB: tl[tl.length - 1].el === b
            });
        })()
    """))
    assert got["count"] == 2
    assert got["firstKind"] == "modal"
    assert got["lastKind"] == "popover", "most recently promoted paints on top"
    assert got["lastIsB"] is True


def test_layers_inspector_reports_top_layer_and_backdrops():
    ctx = _ctx("""
        <html><body>
            <dialog id="modal-dlg">M</dialog>
        </body></html>
    """)
    ctx.eval("document.getElementById('modal-dlg').showModal();")
    layers = json.loads(ctx.eval("__inspectLayers()"))
    assert "TOP_LAYER" in layers["strata"], "inspector must carry the TOP_LAYER stratum"
    assert "BACKDROPS" in layers["strata"], "inspector must carry the BACKDROPS stratum"
    top = layers["strata"]["TOP_LAYER"]
    bd = layers["strata"]["BACKDROPS"]
    assert len(top) == 1 and top[0]["kind"] == "modal" and top[0]["id"] == "modal-dlg"
    assert len(bd) == 1 and bd[0]["width"] == 1280 and bd[0]["height"] == 720


def test_non_modal_show_does_not_join_top_layer():
    """dialog.show() (non-modal) must NOT promote to the top layer —
    spec: only showModal() paints in the top layer."""
    ctx = _ctx("""
        <html><body><dialog id="dlg">Plain</dialog></body></html>
    """)
    got = json.loads(ctx.eval("""
        (function() {
            var dlg = document.getElementById('dlg');
            dlg.show();
            return JSON.stringify({
                topLayer: globalThis.__wrTopLayer().length,
                hasBackdrop: !!dlg.__backdropBox
            });
        })()
    """))
    assert got["topLayer"] == 0, "show() is non-modal: no top-layer promotion"
    assert got["hasBackdrop"] is False, "no ::backdrop for non-modal show()"
