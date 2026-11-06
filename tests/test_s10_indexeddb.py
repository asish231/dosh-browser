"""S10: IndexedDB full semantics — hermetic tests.

Pins the upgraded semantics: real indexes (secondary-key views over the
index's keyPath), cursors (direction-honoring iteration with
continue/advance), add() ConstraintError, abort() rollback, and
onupgradeneeded. Before S10 the index methods returned the whole
store, cursors did not exist, add() upserted, and abort() was a no-op.
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


def _pump(ctx: Context, rounds: int = 60) -> None:
    for _ in range(rounds):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break


def _seed_store(ctx: Context) -> None:
    """Open a DB, upgrade-create a store with an index, and seed records."""
    ctx.eval("""
        globalThis.__up = null;
        var req = indexedDB.open('db10', 1);
        req.onupgradeneeded = function (e) {
            globalThis.__up = { old: e.oldVersion, new: e.newVersion };
            var db = e.target.result;
            var store = db.createObjectStore('people', { keyPath: 'id' });
            store.createIndex('by_name', 'name');
            store.createIndex('by_age', 'age');
        };
        req.onsuccess = function (e) { globalThis.__db = e.target.result; };
    """)
    _pump(ctx)


def test_onupgradeneeded_fires_with_versions():
    ctx = _ctx()
    _seed_store(ctx)
    up = json.loads(ctx.eval("JSON.stringify(globalThis.__up)"))
    assert up == {"old": 0, "new": 1}, f"a new DB fires upgradeneeded 0->1: {up}"
    assert ctx.eval("globalThis.__db.objectStoreNames.contains('people')") is True


def test_index_get_honors_key_path():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        var tx = globalThis.__db.transaction('people', 'readwrite');
        var store = tx.objectStore('people');
        store.put({ id: 1, name: 'ada', age: 36 });
        store.put({ id: 2, name: 'bob', age: 25 });
    """)
    _pump(ctx)
    got = json.loads(ctx.eval("""
        (function() {
            var tx = globalThis.__db.transaction('people', 'readonly');
            var idx = tx.objectStore('people').index('by_name');
            globalThis.__r = null;
            idx.get('ada').onsuccess = function (e) { globalThis.__r = e.target.result; };
        })()
    """) or "null")
    _pump(ctx)
    r = json.loads(ctx.eval("JSON.stringify(globalThis.__r)"))
    assert r == {"id": 1, "name": "ada", "age": 36}, f"index.get('ada') returns ada's record: {r}"


def test_index_get_all_filters_by_index_value():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        var tx = globalThis.__db.transaction('people', 'readwrite');
        var store = tx.objectStore('people');
        store.put({ id: 1, name: 'ada', age: 30 });
        store.put({ id: 2, name: 'bob', age: 30 });
        store.put({ id: 3, name: 'cyd', age: 40 });
    """)
    _pump(ctx)
    ctx.eval("""
        var tx = globalThis.__db.transaction('people', 'readonly');
        var idx = tx.objectStore('people').index('by_age');
        globalThis.__all = null;
        idx.getAll(30).onsuccess = function (e) { globalThis.__all = e.target.result; };
    """)
    _pump(ctx)
    all_ = json.loads(ctx.eval("JSON.stringify(globalThis.__all)"))
    names = sorted(r["name"] for r in all_)
    assert names == ["ada", "bob"], f"getAll(30) returns only age-30 records: {all_}"


def test_add_duplicate_key_constraint_error():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        globalThis.__err = null;
        var tx = globalThis.__db.transaction('people', 'readwrite');
        var store = tx.objectStore('people');
        store.add({ id: 7, name: 'x', age: 1 });
        store.add({ id: 7, name: 'y', age: 2 }).onerror = function (e) {
            globalThis.__err = String(e.target.error);
        };
    """)
    _pump(ctx)
    err = ctx.eval("globalThis.__err")
    assert err and "ConstraintError" in str(err), f"add() on an existing key must ConstraintError: {err!r}"
    # put() still upserts the same key:
    ctx.eval("""
        globalThis.__after = null;
        var tx2 = globalThis.__db.transaction('people', 'readwrite');
        tx2.objectStore('people').put({ id: 7, name: 'z', age: 3 });
        var txr = globalThis.__db.transaction('people', 'readonly');
        txr.objectStore('people').get(7).onsuccess = function (e) { globalThis.__after = e.target.result; };
    """)
    _pump(ctx)
    after = json.loads(ctx.eval("JSON.stringify(globalThis.__after)"))
    assert after["name"] == "z", f"put() upserts: {after}"


def test_cursor_iteration_with_continue():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        var tx = globalThis.__db.transaction('people', 'readwrite');
        var store = tx.objectStore('people');
        store.put({ id: 3, name: 'c' });
        store.put({ id: 1, name: 'a' });
        store.put({ id: 2, name: 'b' });
    """)
    _pump(ctx)
    ctx.eval("""
        globalThis.__visited = [];
        var tx = globalThis.__db.transaction('people', 'readonly');
        tx.objectStore('people').openCursor().onsuccess = function (e) {
            var cur = e.target.result;
            if (cur) {
                globalThis.__visited.push({ key: cur.key, pk: cur.primaryKey });
                cur.continue();
            }
        };
    """)
    _pump(ctx)
    visited = json.loads(ctx.eval("JSON.stringify(globalThis.__visited)"))
    pks = [v["pk"] for v in visited]
    assert pks == ["1", "2", "3"], f"next-direction cursor visits in key order: {visited}"


def test_abort_rolls_back_transaction_writes():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        globalThis.__abortFired = false;
        var tx = globalThis.__db.transaction('people', 'readwrite');
        tx.onabort = function () { globalThis.__abortFired = true; };
        var store = tx.objectStore('people');
        store.put({ id: 1, name: 'before' });
        tx.abort();
    """)
    _pump(ctx)
    assert ctx.eval("globalThis.__abortFired") is True, "abort fires onabort"
    ctx.eval("""
        globalThis.__after = 'unset';
        var txr = globalThis.__db.transaction('people', 'readonly');
        txr.objectStore('people').get(1).onsuccess = function (e) {
            globalThis.__after = e.target.result === undefined ? 'gone' : 'still-there';
        };
    """)
    _pump(ctx)
    assert ctx.eval("globalThis.__after") == "gone", "abort() must roll back the transaction's writes"


def test_index_count():
    ctx = _ctx()
    _seed_store(ctx)
    ctx.eval("""
        var tx = globalThis.__db.transaction('people', 'readwrite');
        var store = tx.objectStore('people');
        store.put({ id: 1, age: 30 });
        store.put({ id: 2, age: 30 });
        store.put({ id: 3, age: 40 });
    """)
    _pump(ctx)
    ctx.eval("""
        globalThis.__c = null;
        var tx = globalThis.__db.transaction('people', 'readonly');
        tx.objectStore('people').index('by_age').count(30).onsuccess = function (e) { globalThis.__c = e.target.result; };
    """)
    _pump(ctx)
    assert ctx.eval("globalThis.__c") == 2, "index.count(30) counts only matching records"
