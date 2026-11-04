"""Hermetic tests for Native Function Cloaking & Bot Detection Defenses in minidom.js.

Verifies:
- Function.prototype.toString anti-fingerprinting compliance (CreepJS, FingerprintJS, Turnstile, Kasada)
- DOM method cloaking (document.getElementById, document.querySelector, Element.prototype methods)
- Global and window method cloaking (fetch, setTimeout, postMessage, alert)
- WebAssembly API method cloaking (validate, compile, instantiate)
- Navigator and chrome object method cloaking
- Storage method cloaking (localStorage, sessionStorage)
- Canvas & WebGL context method cloaking
- Preservation of user-defined JavaScript function source code
- Spec-compliant TypeError throwing on incompatible receivers
"""

import json
from pathlib import Path
import pytest
import quickjs

MINIDOM_PATH = Path(__file__).parent.parent / "webresearch" / "engine" / "minidom.js"
MINIDOM_JS = MINIDOM_PATH.read_text(encoding="utf-8")


@pytest.fixture
def js_ctx():
    ctx = quickjs.Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval(
        '__load({"t":"document","c":[{"t":"html","a":{},"c":'
        '[{"t":"body","a":{},"c":[{"t":"canvas","a":{"id":"test_canvas"},"c":[]}]}]}]}, "https://example.com/")'
    )
    return ctx


def test_dom_methods_cloaked(js_ctx):
    res = js_ctx.eval("""
        JSON.stringify({
            getId_name: document.getElementById.name,
            getId_len: document.getElementById.length,
            getId_str: document.getElementById.toString(),
            getId_call: Function.prototype.toString.call(document.getElementById),
            query_name: document.querySelector.name,
            query_len: document.querySelector.length,
            query_str: document.querySelector.toString(),
            create_name: document.createElement.name,
            create_len: document.createElement.length,
            create_str: document.createElement.toString()
        });
    """)
    data = json.loads(res)
    assert data["getId_name"] == "getElementById"
    assert data["getId_len"] == 1
    assert data["getId_str"] == "function getElementById() { [native code] }"
    assert data["getId_call"] == "function getElementById() { [native code] }"

    assert data["query_name"] == "querySelector"
    assert data["query_len"] == 1
    assert data["query_str"] == "function querySelector() { [native code] }"

    assert data["create_name"] == "createElement"
    assert data["create_len"] == 1
    assert data["create_str"] == "function createElement() { [native code] }"


def test_window_and_global_methods_cloaked(js_ctx):
    res = js_ctx.eval("""
        JSON.stringify({
            fetch_name: window.fetch.name,
            fetch_len: window.fetch.length,
            fetch_str: window.fetch.toString(),
            setTimeout_str: window.setTimeout.toString(),
            postMessage_str: window.postMessage.toString(),
            alert_str: window.alert.toString()
        });
    """)
    data = json.loads(res)
    assert data["fetch_name"] == "fetch"
    assert data["fetch_len"] == 1
    assert data["fetch_str"] == "function fetch() { [native code] }"
    assert data["setTimeout_str"] == "function setTimeout() { [native code] }"
    assert data["postMessage_str"] == "function postMessage() { [native code] }"
    assert data["alert_str"] == "function alert() { [native code] }"


def test_wasm_and_chrome_cloaked(js_ctx):
    res = js_ctx.eval("""
        JSON.stringify({
            wasmValidate_name: WebAssembly.validate.name,
            wasmValidate_str: WebAssembly.validate.toString(),
            wasmCompile_str: WebAssembly.compile.toString(),
            wasmInstantiate_str: WebAssembly.instantiate.toString(),
            chromeLoadTimes_str: window.chrome.loadTimes.toString(),
            chromeCsi_str: window.chrome.csi.toString()
        });
    """)
    data = json.loads(res)
    assert data["wasmValidate_name"] == "validate"
    assert data["wasmValidate_str"] == "function validate() { [native code] }"
    assert data["wasmCompile_str"] == "function compile() { [native code] }"
    assert data["wasmInstantiate_str"] == "function instantiate() { [native code] }"
    assert data["chromeLoadTimes_str"] == "function loadTimes() { [native code] }"
    assert data["chromeCsi_str"] == "function csi() { [native code] }"


def test_storage_and_canvas_cloaked(js_ctx):
    res = js_ctx.eval("""
        var canvas = document.getElementById("test_canvas");
        var gl = canvas.getContext("webgl");
        JSON.stringify({
            getItem_str: localStorage.getItem.toString(),
            setItem_str: localStorage.setItem.toString(),
            canvasGetContext_str: canvas.getContext.toString(),
            canvasToDataURL_str: canvas.toDataURL.toString(),
            glParam_str: gl.getParameter.toString()
        });
    """)
    data = json.loads(res)
    assert data["getItem_str"] == "function getItem() { [native code] }"
    assert data["setItem_str"] == "function setItem() { [native code] }"
    assert data["canvasGetContext_str"] == "function getContext() { [native code] }"
    assert data["canvasToDataURL_str"] == "function toDataURL() { [native code] }"
    assert data["glParam_str"] == "function getParameter() { [native code] }"


def test_function_to_string_anti_fingerprint_integrity(js_ctx):
    res = js_ctx.eval(r"""
        function userFn(x, y) {
            return x * y + 42;
        }

        var isNativeRegex = /\{\s*\[native code\]\s*\}/;

        JSON.stringify({
            toString_name: Function.prototype.toString.name,
            toString_len: Function.prototype.toString.length,
            toString_self: Function.prototype.toString.toString(),
            toString_call_self: Function.prototype.toString.call(Function.prototype.toString),
            userFn_preserved: userFn.toString().indexOf("return x * y + 42") !== -1,
            userFn_native: isNativeRegex.test(userFn.toString()),
            dom_is_native: isNativeRegex.test(document.getElementById.toString()),
            fetch_is_native: isNativeRegex.test(window.fetch.toString()),
            wasm_is_native: isNativeRegex.test(WebAssembly.validate.toString())
        });
    """)
    data = json.loads(res)
    assert data["toString_name"] == "toString"
    assert data["toString_len"] == 0
    assert data["toString_self"] == "function toString() { [native code] }"
    assert data["toString_call_self"] == "function toString() { [native code] }"
    assert data["userFn_preserved"] is True
    assert data["userFn_native"] is False
    assert data["dom_is_native"] is True
    assert data["fetch_is_native"] is True
    assert data["wasm_is_native"] is True


def test_function_to_string_incompatible_receiver_throws(js_ctx):
    res = js_ctx.eval("""
        var objError = false;
        var strError = false;
        try {
            Function.prototype.toString.call({});
        } catch (e) {
            objError = e instanceof TypeError;
        }
        try {
            Function.prototype.toString.call("not a function");
        } catch (e) {
            strError = e instanceof TypeError;
        }
        JSON.stringify({ objError: objError, strError: strError });
    """)
    data = json.loads(res)
    assert data["objError"] is True
    assert data["strError"] is True
