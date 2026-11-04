"""Hermetic integration tests for the in-DOM WebAssembly engine in minidom.js.

Verifies:
- WebAssembly namespace and constructor existence (validate, compile, instantiate, Memory, Table)
- Binary validation of WASM headers in JS
- Memory allocation, grow, and bounds in JS
- Table allocation and indexing in JS
- Compiling and instantiating WASM modules inside QuickJS
- Calling exported WASM functions and retrieving results
- Agentic telemetry recording in global.__WR_WASM_CALLS
"""

import quickjs
import pytest
from pathlib import Path

MINIDOM_PATH = Path(__file__).parent.parent / "webresearch" / "engine" / "minidom.js"
MINIDOM_JS = MINIDOM_PATH.read_text(encoding="utf-8")


@pytest.fixture
def js_ctx():
    ctx = quickjs.Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval(
        '__load({"t":"document","c":[{"t":"html","a":{},"c":'
        '[{"t":"body","a":{},"c":[]}]}]}, "https://example.com/")'
    )
    return ctx


def test_wasm_namespace_and_constructors(js_ctx):
    assert js_ctx.eval("typeof WebAssembly") == "object"
    assert js_ctx.eval("typeof WebAssembly.validate") == "function"
    assert js_ctx.eval("typeof WebAssembly.compile") == "function"
    assert js_ctx.eval("typeof WebAssembly.instantiate") == "function"
    assert js_ctx.eval("typeof WebAssembly.Memory") == "function"
    assert js_ctx.eval("typeof WebAssembly.Table") == "function"


def test_wasm_validate(js_ctx):
    # Valid 8-byte header
    res = js_ctx.eval("""
        var valid = new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]);
        WebAssembly.validate(valid);
    """)
    assert res is True

    # Invalid header
    res_bad = js_ctx.eval("""
        var bad = new Uint8Array([0x00, 0x62, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]);
        WebAssembly.validate(bad);
    """)
    assert res_bad is False


def test_wasm_memory(js_ctx):
    res = js_ctx.eval("""
        var mem = new WebAssembly.Memory({ initial: 1, maximum: 2 });
        var initialLen = mem.buffer.byteLength;
        var prevPages = mem.grow(1);
        var grownLen = mem.buffer.byteLength;
        JSON.stringify([initialLen, prevPages, grownLen, mem instanceof WebAssembly.Memory]);
    """)
    import json
    assert json.loads(res) == [65536, 1, 131072, True]


def test_wasm_table(js_ctx):
    res = js_ctx.eval("""
        var tbl = new WebAssembly.Table({ initial: 3, element: "anyfunc" });
        var fn = function () { return 42; };
        tbl.set(1, fn);
        var fetched = tbl.get(1);
        var prevLen = tbl.grow(2);
        JSON.stringify([tbl.length, fetched === fn, prevLen]);
    """)
    import json
    assert json.loads(res) == [5, True, 3]


def test_wasm_instantiate_and_call(js_ctx):
    hex_add = "0061736d0100000001070160027f7f017f030201000707010361646400000a09010700200020016a0b"
    js_ctx.eval(f"""
        var hex = "{hex_add}";
        var bytes = new Uint8Array(hex.length / 2);
        for (var i = 0; i < hex.length; i += 2) {{
            bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
        }}
        var mod = new WebAssembly.Module(bytes);
        var inst = new WebAssembly.Instance(mod);
        var sum = inst.exports.add(18, 24);
        window.__test_result = {{
            sum: sum,
            hasExport: typeof inst.exports.add === "function",
            callLogged: window.__WR_WASM_CALLS.length > 0,
            loggedName: window.__WR_WASM_CALLS[0] ? window.__WR_WASM_CALLS[0].name : null,
            loggedResult: window.__WR_WASM_CALLS[0] ? window.__WR_WASM_CALLS[0].result : null
        }};
    """)
    result = js_ctx.eval("JSON.stringify(window.__test_result)")
    import json
    parsed = json.loads(result)
    assert parsed["hasExport"] is True
    assert parsed["sum"] == 42
    assert parsed["callLogged"] is True
    assert parsed["loggedName"] == "add"
    assert parsed["loggedResult"] == 42


def test_wasm_async_instantiate(js_ctx):
    hex_add = "0061736d0100000001070160027f7f017f030201000707010361646400000a09010700200020016a0b"
    js_ctx.eval(f"""
        var hex = "{hex_add}";
        var bytes = new Uint8Array(hex.length / 2);
        for (var i = 0; i < hex.length; i += 2) {{
            bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
        }}
        window.__promise_res = null;
        WebAssembly.instantiate(bytes).then(function(res) {{
            window.__promise_res = {{
                hasModule: !!res.module,
                hasInstance: !!res.instance,
                calc: res.instance.exports.add(100, 250)
            }};
        }});
    """)
    # Flush microtask queue
    while js_ctx.execute_pending_job():
        pass
    res = js_ctx.eval("JSON.stringify(window.__promise_res)")
    import json
    parsed = json.loads(res)
    assert parsed["hasModule"] is True
    assert parsed["hasInstance"] is True
    assert parsed["calc"] == 350


def test_wasm_fuel_exhaustion_trap(js_ctx):
    # infinite loop: loop br 0 end end
    # 0061736d0100000001040160000003020100070801046c6f6f7000000a0901070003400c000b0b
    hex_loop = "0061736d0100000001040160000003020100070801046c6f6f7000000a0901070003400c000b0b"
    js_ctx.eval(f"""
        var hex = "{hex_loop}";
        var bytes = new Uint8Array(hex.length / 2);
        for (var i = 0; i < hex.length; i += 2) {{
            bytes[i / 2] = parseInt(hex.substr(i, 2), 16);
        }}
        var mod = new WebAssembly.Module(bytes);
        var inst = new WebAssembly.Instance(mod);
        inst.maxFuel = 1000;
        var trapped = false;
        var errMsg = "";
        try {{
            inst.exports.loop();
        }} catch (e) {{
            trapped = true;
            errMsg = e.message;
        }}
        window.__trap_res = {{ trapped: trapped, errMsg: errMsg }};
    """)
    res = js_ctx.eval("JSON.stringify(window.__trap_res)")
    import json
    parsed = json.loads(res)
    assert parsed["trapped"] is True
    assert "fuel exhausted" in parsed["errMsg"].lower()

