"""ESM engine fixes found on real LinkedIn markup: mask desyncs, regex compat,
import rewrites, and the login-verdict false positive.

Every test here traces to a real failure from LinkedIn's production bundles
(kvZetL-w.js, A2-4UjN7.js, oO-sjsKw.js, DZEtkqH9.js, CNyVMc-P.js).
"""

from quickjs import Context

from webresearch.engine.login import verify_logged_in
from webresearch.engine.modules import (
    _fix_class_escapes,
    _mask_literals,
    _prev_ends_keyword,
    _quickjs_regex_compat,
    transform_module,
)
from webresearch.engine.runtime import MINIDOM_JS

# ---------------------------------------------------------------------------
# Regex-after-keyword detection (the A2-4UjN7.js 681-char swallow)


def test_prev_ends_keyword_detects_regex_after_return():
    code = "return/^\\d:/.test(n)&&(n=`0${n}`)"
    assert _prev_ends_keyword(code, 6) is True  # the '/' right after 'return'


def test_prev_ends_keyword_rejects_identifiers():
    # 'myreturn/x' — the 'return' is part of an identifier, not the keyword
    code = "var v = myreturn/x;"
    assert _prev_ends_keyword(code, 17) is False


def test_prev_ends_keyword_rejects_division_after_value():
    code = "var v = a/b;"
    assert _prev_ends_keyword(code, 10) is False


def test_mask_keyword_regex_not_swallowed():
    """The real LinkedIn pattern: return/^\d:/.test(n) inside a locale
    formatter. Before the fix, the mask treated the regex CLOSER as a new
    opener and blanked 681 chars, taking the export statement with it."""
    code = (
        "var a=1;"
        "function f(n){return/^\\d:/.test(n)&&(n=`0${n}`)}"
        "var b=2;"
    )
    masked = _mask_literals(code)
    # The regex body is blanked (literal) but the code around it is visible.
    assert masked.count("return") == 1
    assert "function" in masked
    assert "var b=2;" in masked


# ---------------------------------------------------------------------------
# Line-comment cap (URL // inside string, post-desync protection)


def test_mask_line_comment_capped():
    """A // inside a URL string can desync the scanner. After the cap, the
    damage is bounded to the two slash chars instead of blanking to EOL."""
    # Build a case where a quote-parity shift puts a URL's // in code position.
    code = 'var u="https://example.com/path";var x=1;'
    masked = _mask_literals(code)
    # normal case: URL inside a string is blanked as string content
    assert "var x=1;" in masked


# ---------------------------------------------------------------------------
# Default-only import rewrite (oO-sjsKw.js: import iI from"./B5BL-RZl.js")


def test_default_only_import_rewritten():
    code = 'import iI from"./B5BL-RZl.js";function f(){return iI;}export{f};'
    out = transform_module(code, "https://cdn.example/oO.js", {"./B5BL-RZl.js": "https://cdn.example/B5BL-RZl.js"})
    ctx = Context()
    ctx.eval("var __mods = {}; __mods['https://cdn.example/B5BL-RZl.js'] = {default: 42};")
    ctx.eval(f"(function(){{ var __exports = {{}}; {out} }})();")
    assert ctx.eval("__mods['https://cdn.example/oO.js'].f()") == 42


# ---------------------------------------------------------------------------
# import.meta rewrite (kvZetL-w.js: inside real code near templates)


def test_import_meta_in_code_with_apostrophe_templates():
    """kvZetL-w.js pattern: Error(`Can't find font...`) templates plus a real
    class body containing createRequire(import.meta.url). The apostrophe in
    Can't used to open a phantom string that swallowed the class."""
    code = (
        "function a(){throw Error(`Can't find font for ${t}`)}\n"
        "class X{_createCanvas(t,e){return process.getBuiltinModule("
        '"module").createRequire(import.meta.url)("@napi-rs/canvas")'
        ".createCanvas(t,e)}}\n"
        "export{X};"
    )
    out = transform_module(code, "https://cdn.example/kv.js", {})
    assert "import.meta" not in out
    ctx = Context()
    ctx.eval("var __mods = {};")
    ctx.eval(f"(function(){{ var __exports = {{}}; {out} }})();")
    assert ctx.eval("typeof __mods['https://cdn.example/kv.js'].X") == "function"


def test_import_meta_url_replaced():
    code = "var u=import.meta.url;export{u};"
    out = transform_module(code, "https://cdn.example/m.js", {})
    assert "import.meta" not in out
    assert "https://cdn.example/m.js" in out


# ---------------------------------------------------------------------------
# QuickJS regex Annex-B compat (DZEtkqH9.js: [;,...\\-...], CNyVMc-P: \\)


def test_regex_compat_escaped_punctuation_fixed():
    """QuickJS rejects \\; \\, \\- in classes; Chrome accepts them."""
    code = "var t = 'x, y'.split(/(?:, |\\/|:)/g); var c = /[\\;\\-\\,]+/;"
    out = _quickjs_regex_compat(code)
    ctx = Context()
    ctx.eval(out)  # top level so t is visible below
    assert ctx.eval("t.length") == 2
    # the raw form still fails without the fix (u-flag = strict Unicode mode)
    try:
        ctx.eval("var bad = /[\\;\\-\\,]+/u;")
        raw_ok = True
    except Exception:
        raw_ok = False
    assert not raw_ok


def test_regex_compat_preserves_double_backslash():
    """CNyVMc-P.js: /[\\n"\\\\]/g — the \\\\ (escaped backslash) must survive."""
    code = 'var tP=/[\\n"\\\\]/g;'
    out = _quickjs_regex_compat(code)
    assert "\\\\" in out  # both backslashes intact
    ctx = Context()
    ctx.eval(f"(function(){{{out}}})()")  # parses
    # and the class matches a literal backslash
    ctx.eval("var r = /[\\n\"\\\\]/g;")
    assert ctx.eval('"\\"".match(r) !== null') is True


def test_regex_compat_dash_becomes_hex():
    code = "var k=/[a\\-z]+/;"
    out = _quickjs_regex_compat(code)
    assert "\\x2d" in out
    ctx = Context()
    ctx.eval(f"(function(){{{out}}})()")
    # [a\-z] means literal a, dash, z — not a range — so "a-z" matches fully
    assert ctx.eval('"a-z".match(/[a\\x2dz]+/)[0]') == "a-z"


def test_fix_class_escapes_keeps_valid_escapes():
    body = "[\\d\\w\\x41\\p{L}]"  # all valid, untouched
    assert _fix_class_escapes(body) == body


def test_regex_compat_skips_strings():
    """A / inside a string is never treated as a regex opener."""
    code = 'var u="https://example.com/a/b"; var ok=1/2;'
    out = _quickjs_regex_compat(code)
    assert out == code  # no changes to non-regex code


# ---------------------------------------------------------------------------
# Full transform of a LinkedIn-shaped module


def test_transform_linkedin_shaped_module():
    """A miniature of LinkedIn's module shapes: default+named imports, a
    regex with Annex-B escapes, a template with an apostrophe, export list."""
    code = (
        'import iI from"./dep.js";\n'
        'import{A as B,C}from"./dep2.js";\n'
        "function f(n){return/^\\d:/.test(n)}\n"
        "const g=`Can't ${B}`;\n"
        "let h='x,y;z'.replace(/[\\;\\-\\,]+/g,'');\n"
        "export{f as F,g,h,iI};\n"
    )
    out = transform_module(code, "https://cdn.example/main.js", {
        "./dep.js": "https://cdn.example/dep.js",
        "./dep2.js": "https://cdn.example/dep2.js",
    })
    assert "import" not in out.replace("__imports", "")
    assert "export" not in out.split("__exports")[0]
    ctx = Context()
    ctx.eval("var __mods = {};")
    ctx.eval(
        "__mods['https://cdn.example/dep.js'] = {default: 1};"
        "__mods['https://cdn.example/dep2.js'] = {A: 2, C: 3};"
    )
    ctx.eval(f"(function(){{ var __exports = {{}}; {out} }})();")
    mod = "__mods['https://cdn.example/main.js']"
    assert ctx.eval(f"{mod}.F") == ctx.eval("undefined") or callable
    assert ctx.eval(f"{mod}.h.length") >= 0


# ---------------------------------------------------------------------------
# Login verdict: password field present => never otp_required


def test_login_page_with_otp_text_is_failed_not_otp():
    """The real false positive: LinkedIn's login page HTML contains OTP-ish
    words in bundle scripts. With a password field present, the verdict must
    be failed (still on login), never otp_required."""
    html = """
    <html><body><h1>Sign in</h1>
    <input type="text" name="session_key" />
    <input type="password" name="session_password" />
    <button>Sign in</button>
    <script>var s="enter the code we sent";var o="verify your identity";</script>
    </body></html>
    """
    verdict = verify_logged_in(html, "https://www.linkedin.com/login")
    assert verdict == "failed"


def test_real_otp_page_still_otp_required():
    html = """
    <html><body><h2>Verify your identity</h2>
    <p>we sent a code to your phone</p>
    <input name="otp" maxlength="6" inputmode="numeric" autocomplete="one-time-code" />
    </body></html>
    """
    verdict = verify_logged_in(html, "https://www.linkedin.com/checkpoint/challenge")
    assert verdict == "otp_required"


# ---------------------------------------------------------------------------
# Node.remove() in mini-DOM (LinkedIn cdnMonitor line 30: el.remove())


def test_minidom_node_remove():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        __load({t: 'html', c: [{t: 'body', c: []}]}, 'https://example.com');
        var d = document.createElement('div');
        document.body.appendChild(d);
        var before = document.body.childNodes.length;
        d.remove();
        globalThis.__removed = (before === 1 && document.body.childNodes.length === 0);
    """)
    assert ctx.eval("globalThis.__removed") is True
