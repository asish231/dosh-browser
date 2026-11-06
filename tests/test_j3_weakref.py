"""J3: WeakRef / FinalizationRegistry — spec-shaped shims, honestly disclosed.

Upgrades pinned here:
- WeakRef constructor rejects non-objects (spec: TypeError).
- deref() throws TypeError on invalid construction and returns the
  target while strongly retained (disclosed: no real weak semantics in
  QuickJS; never observes a collection that did not happen).
- FinalizationRegistry constructor validates the callback (spec).
- register() validates target/unregisterToken (spec), carries REAL
  bookkeeping; unregister(token) returns true/false per spec and removes.
- cleanupSome never fires callbacks (disclosed), and the registry's
  state is inspectable (__wrInspect) so the no-GC contract is visible.
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


def test_weakref_rejects_non_object():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var out = {prim: null, fn: null, obj: null};
            try { new WeakRef(42); out.prim = 'no-throw'; }
            catch (e) { out.prim = e instanceof TypeError ? 'TypeError' : String(e && e.name); }
            try { new WeakRef(function () {}); out.fn = 'ok'; }
            catch (e) { out.fn = 'threw'; }
            var o = {};
            out.obj = new WeakRef(o).deref() === o ? 'ok' : 'wrong';
            return JSON.stringify(out);
        })()
    """))
    assert got["prim"] == "TypeError", f"primitives must throw TypeError, got {got['prim']}"
    assert got["fn"] == "ok", "functions are valid WeakRef targets (spec)"
    assert got["obj"] == "ok", "deref returns the target while retained"


def test_finalization_registry_register_unregister():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var fr = new FinalizationRegistry(function (v) { globalThis.__CLEANED = v; });
            var target = { name: 't' };
            var token = { token: true };
            fr.register(target, 'held-1', token);
            fr.register({}, 'held-2');
            var before = JSON.parse(fr.__wrInspect());
            var un1 = fr.unregister(token);      /* removes held-1 */
            var un2 = fr.unregister(token);      /* nothing left: false */
            var after = JSON.parse(fr.__wrInspect());
            return JSON.stringify({
                before: before.registered,
                un1: un1,
                un2: un2,
                after: after.registered,
                cleanedFired: globalThis.__CLEANED === undefined
            });
        })()
    """))
    assert got["before"] == 2
    assert got["un1"] is True, "unregister with a live token returns true"
    assert got["un2"] is False, "second unregister returns false (spec)"
    assert got["after"] == 1
    assert got["cleanedFired"] is True, "cleanup callback must NOT fire (no GC, disclosed)"


def test_finalization_registry_validation():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var out = {ctor: null, target: null, token: null};
            try { new FinalizationRegistry('not-a-fn'); out.ctor = 'no-throw'; }
            catch (e) { out.ctor = e instanceof TypeError ? 'TypeError' : 'other'; }
            var fr = new FinalizationRegistry(function () {});
            try { fr.register(42, 'x'); out.target = 'no-throw'; }
            catch (e) { out.target = e instanceof TypeError ? 'TypeError' : 'other'; }
            try { fr.register({}, 'x', 'bad-token'); out.token = 'no-throw'; }
            catch (e) { out.token = e instanceof TypeError ? 'TypeError' : 'other'; }
            return JSON.stringify(out);
        })()
    """))
    assert got["ctor"] == "TypeError"
    assert got["target"] == "TypeError"
    assert got["token"] == "TypeError"


def test_inspect_discloses_no_gc_contract():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var fr = new FinalizationRegistry(function () {});
            fr.register({}, 'h');
            return fr.__wrInspect();
        })()
    """))
    assert got["registered"] == 1
    assert got["cleanupWillFire"] is False
    assert "no GC integration" in got["reason"]
