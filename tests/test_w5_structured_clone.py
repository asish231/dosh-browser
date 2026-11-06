"""W5: structuredClone of class instances — hermetic tests.

Before W5 the clone codec threw DataCloneError on any object whose
prototype was not Object.prototype (class instances were 'unsupported
type'). Real structuredClone flattens them to plain objects (methods
lost). This engine does BETTER, disclosed: class instances clone with
their PROTOTYPE preserved — the constructor is resolved by name at
decode time, own properties carry over, methods keep working. Host
objects (DOM nodes, engines) stay refused (clone is data, not a
live-handle copier). Cyclic class graphs still clone.
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx() -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree("<html><body></body></html>")
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def test_class_instance_clones_with_methods():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            class Point {
                constructor(x, y) { this.x = x; this.y = y; }
                norm() { return Math.sqrt(this.x * this.x + this.y * this.y); }
            }
            var p = new Point(3, 4);
            var c = structuredClone(p);
            return JSON.stringify({
                isPoint: c instanceof Point,
                x: c.x, y: c.y,
                normWorks: (typeof c.norm === "function") && c.norm() === 5,
                protoSame: Object.getPrototypeOf(c) === Point.prototype
            });
        })()
    """))
    assert got["isPoint"] is True, f"the clone must be an instance of the class: {got}"
    assert got["x"] == 3 and got["y"] == 4
    assert got["normWorks"] is True, "methods must survive the clone"
    assert got["protoSame"] is True


def test_class_instance_getter_values_carry():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            class Box {
                constructor() { this._items = ['a', 'b']; }
                get first() { return this._items[0]; }
            }
            var b = new Box();
            var c = structuredClone(b);
            return JSON.stringify({
                itemsKept: JSON.stringify(c._items) === '["a","b"]',
                getterWorks: c.first === 'a'
            });
        })()
    """))
    assert got["itemsKept"] is True, "own properties carry"
    assert got["getterWorks"] is True, "prototype getters work on the clone"


def test_nested_class_graph():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            class Node2 { constructor(name) { this.name = name; this.children = []; } }
            class Tree { constructor() { this.root = new Node2('root'); } }
            var t = new Tree();
            var kid = new Node2('kid');
            t.root.children.push(kid);
            var c = structuredClone(t);
            return JSON.stringify({
                treeIsTree: c instanceof Tree,
                rootIsNode: c.root instanceof Node2,
                kidIsNode: c.root.children[0] instanceof Node2,
                kidName: c.root.children[0].name
            });
        })()
    """))
    assert got["treeIsTree"] is True
    assert got["rootIsNode"] is True
    assert got["kidIsNode"] is True, "nested class instances keep their prototypes"
    assert got["kidName"] == "kid"


def test_cyclic_class_reference():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            class Cell { constructor() { this.peer = null; } }
            var a = new Cell();
            var b = new Cell();
            a.peer = b;
            b.peer = a;
            var c = structuredClone(a);
            return JSON.stringify({
                isCell: c instanceof Cell,
                peerIsCell: c.peer instanceof Cell,
                cycle: c.peer.peer === c
            });
        })()
    """))
    assert got["isCell"] is True
    assert got["peerIsCell"] is True
    assert got["cycle"] is True, "cyclic class graphs clone correctly"


def test_unknown_class_degrades_to_plain_object():
    """A class instance serialized in one realm and decoded where the
    class does not exist keeps its DATA (plain object) — honest
    degradation, methods not fabricated."""
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            // Simulate: encode an instance, then decode with the class
            // hidden from the global scope (decode-time resolution).
            class Secret { constructor() { this.v = 42; } hi() { return 'hi'; } }
            var s = new Secret();
            var enc = globalThis.__structuredCloneEncode(s);
            /* Cross-realm model: the class AND the per-call constructor
            side table are both gone where the decode runs. */
            var savedSecret = globalThis.Secret;
            var savedTable = globalThis.__wrSCCtors;
            delete globalThis.Secret;
            globalThis.Secret = undefined;
            globalThis.__wrSCCtors = null;
            var dec;
            try {
                dec = globalThis.__structuredCloneDecode(enc);
            } finally {
                globalThis.Secret = savedSecret;
                globalThis.__wrSCCtors = savedTable;
            }
            return JSON.stringify({
                dataKept: dec && dec.v === 42,
                isPlain: dec && Object.getPrototypeOf(dec) === Object.prototype,
                noFakeMethods: dec && typeof dec.hi !== 'function'
            });
        })()
    """))
    assert got["dataKept"] is True, "data must survive without the class"
    assert got["isPlain"] is True
    assert got["noFakeMethods"] is True, "methods must not be fabricated"


def test_plain_objects_and_builtins_still_clone():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var plain = { a: 1, b: [2, 3], d: new Date(0) };
            var c = structuredClone(plain);
            return JSON.stringify({
                plain: c.a === 1 && c.b[1] === 3,
                dateIsDate: c.d instanceof Date && c.d.getTime() === 0,
                mapRound: structuredClone(new Map([['k', 'v']])).get('k') === 'v',
                setRound: structuredClone(new Set([1])).has(1)
            });
        })()
    """))
    assert got["plain"] is True and got["dateIsDate"] is True
    assert got["mapRound"] is True and got["setRound"] is True


def test_dom_nodes_still_refused():
    ctx = _ctx()
    err = ctx.eval("""
        (function() {
            try { structuredClone(document.body); return 'cloned'; }
            catch (e) { return e && e.name ? e.name : String(e); }
        })()
    """)
    assert err != "cloned", "DOM nodes must stay uncloneable (live handles)"
