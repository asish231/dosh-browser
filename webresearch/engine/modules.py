"""ES module support for the QuickJS mini-DOM.

Real-world bundles (Vite, Next, webpack ESM output) ship ``type="module"``
scripts that import each other with relative specifiers.  QuickJS evaluates
plain scripts, so we translate ES modules into a small registry-based form and
execute dependencies first.

This is intentionally a pragmatic translator, not a spec-complete bundler.
It covers the shapes that production bundles actually emit and degrades to a
best-effort bare evaluation when a module cannot be translated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from webresearch.engine.csp import script_source_allowed
from webresearch.engine.security import mixed_content_blocked, verify_integrity

SPECIFIER_RE = re.compile(r"[\"']([^\"']+)[\"']")


_REGEX_KEYWORDS = (
    "return",
    "typeof",
    "instanceof",
    "in",
    "of",
    "case",
    "delete",
    "void",
    "new",
    "do",
    "else",
    "yield",
    "await",
    "throw",
)


def _prev_ends_keyword(code: str, pos: int) -> bool:
    """True when the token right before ``pos`` ends a JS keyword that can
    legitimately precede a regex literal (``return/\\d/.test(x)``).

    Minified bundles glue regexes directly to keywords with no space, and the
    last keyword letter is alnum — so the plain prev-token heuristic calls the
    regex opener a division, walks into the literal as code, then treats the
    literal's CLOSING slash as a new opener and swallows hundreds of chars of
    real code (a real bug: LinkedIn's A2-4UjN7.js lost its export statement).
    """
    for kw in _REGEX_KEYWORDS:
        start = pos - len(kw)
        if start < 0:
            continue
        if code[start:pos] == kw:
            before = code[start - 1] if start > 0 else " "
            if not (before.isalnum() or before in "_$"):
                return True
    return False


_CLASS_ESCAPE_OK = set("dDwWsstrnvfbB0cxukpP") | set("123456789")
# Chars QuickJS rejects when backslash-escaped inside a character class but
# Chrome (Annex B) accepts literally.


def _fix_class_escapes(regex_body: str) -> str:
    """Drop invalid literal escapes inside a character class body.

    ``[\\;\\-\\,]`` → ``[;-\\,]``-equivalent form QuickJS accepts: every
    rejected ``\\<punct>`` loses its backslash (the punct is literal in a
    class), and ``\\-`` becomes ``\\x2d`` (a bare ``-`` could form a range).
    Valid escapes (``\\d``, ``\\s``, ``\\x2a``, ``\\p{L}``, ``\\]``, ...) are
    preserved untouched.
    """
    out: list[str] = []
    k, m = 0, len(regex_body)
    in_class = False
    while k < m:
        c = regex_body[k]
        if c == "\\" and k + 1 < m:
            nxt = regex_body[k + 1]
            if in_class and nxt == "\\":
                # \\ is a valid escaped backslash; keep both chars untouched.
                out.append(c)
                out.append(nxt)
                k += 2
                continue
            if in_class and nxt not in _CLASS_ESCAPE_OK and nxt != "[" and nxt != "]":
                if nxt == "-":
                    out.append("\\x2d")
                else:
                    # drop the backslash; the punctuation char is literal
                    out.append(nxt)
                k += 2
                continue
            out.append(c)
            out.append(nxt)
            k += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        out.append(c)
        k += 1
    return "".join(out)


def _quickjs_regex_compat(code: str) -> str:
    """Fix regex literals QuickJS rejects but real Chrome accepts.

    Chrome (Annex B) accepts backslash-escaped literal punctuation inside
    character classes — ``[;\\-\\/]`` from a real LinkedIn bundle — while
    QuickJS rejects ``\\- \\; \\, ...`` as invalid escapes, killing the whole
    module at parse time. This pass rewrites those escapes inside regex
    literals only. The scanner is literal-aware — strings, templates and
    comments are consumed with the same rules as ``_mask_literals`` so a
    ``/`` inside a string is never mistaken for a regex opener.
    """
    edits: list[tuple[int, int, str]] = []
    i, n = 0, len(code)
    prev = ""
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        # Consume comments (same rules as _mask_literals).
        if ch == "/" and nxt == "/":
            j = code.find("\n", i)
            j = n if j < 0 else j
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = code.find("*/", i)
            j = min(n, i + 2) if j < 0 else j + 2
            i = j
            continue
        # Consume template literals.
        if ch == "`":
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "`":
                    j += 1
                    break
                j += 1
            i = min(j, n)
            prev = "`"
            continue
        # Consume strings.
        if ch in "\"'":
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == ch:
                    j += 1
                    break
                j += 1
            i = min(j, n)
            prev = ch
            continue
        # Regex literal?
        opens_regex = ch == "/" and (
            _prev_ends_keyword(code, i)
            or (prev not in ")]}\"'`" and not (prev.isalnum() or prev in "_$"))
        )
        if opens_regex:
            j = i + 1
            in_class = False
            closed = False
            needs_fix = False
            while j < n:
                c = code[j]
                if c == "\\":
                    nxt2 = code[j + 1] if j + 1 < n else ""
                    if in_class and nxt2 not in _CLASS_ESCAPE_OK and nxt2 not in "[]":
                        needs_fix = True
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    j += 1
                    closed = True
                    break
                elif c == "\n":
                    break
                j += 1
            while closed and j < n and code[j].isalpha():
                j += 1
            if not closed:
                i += 1
                prev = "/"
                continue
            if needs_fix:
                f = j
                while f > i and code[f - 1].isalpha():
                    f -= 1
                body_end = f - 1  # position of the closing "/"
                if body_end > i:
                    new_body = _fix_class_escapes(code[i:body_end])
                    if new_body != code[i:body_end]:
                        edits.append((i, body_end, new_body))
            i = j
            prev = "/"
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    if not edits:
        return code
    # Apply edits back-to-front; indices are original so order is safe.
    out = code
    for start, end, new_body in reversed(edits):
        out = out[:start] + new_body + out[end:]
    return out


def _mask_literals(code: str) -> str:
    """Blank out string, template, comment and regex literals, preserving length.

    Rewriting ``import``/``export`` with plain regexes corrupts real bundles:
    path segments live inside template literals (``() => "/new/import"``), and
    the negated character class in ``import\\s*["']([^"']+)["']`` then runs to
    the *next* quote in the file, swallowing the whole module and replacing it
    with a ``__require`` call. Masking literals first means every rewrite below
    only ever sees real code positions. Indices line up with the original, so a
    match on the mask maps straight back to the source text.
    """
    out = list(code)
    i, n = 0, len(code)
    prev = ""
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = code.find("\n", i)
            j = n if j < 0 else j
            if j - i > 500:
                # A "line comment" hundreds of chars long in minified code is a
                # scanner desync artifact (typically // inside a URL string the
                # loop walked into). Masking to the next newline swallowed real
                # code; cap the damage by masking only the opener.
                out[i] = "\x00"
                out[i + 1] = "\x00"
                i = i + 2
                continue
            for k in range(i, j):
                out[k] = "\x00"
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = code.find("*/", i)
            unterminated = j < 0
            if unterminated:
                # A scanner desync, not real code: an unterminated construct is
                # invalid JavaScript. Masking from here to EOF would silently
                # swallow every real statement that follows, so fall through and
                # keep reading code.
                j = min(n, i + 2)
            else:
                j = j + 2
            for k in range(i, j):
                out[k] = "\x00"
            i = j
            continue
        if ch == "`":
            # Template literals may contain interpolation, nested quotes, and
            # eventually new code, so hide the complete literal. Missing a
            # dynamic import/export embedded in a template is preferable to
            # matching a non-statement inside text.
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == ch:
                    j += 1
                    break
                j += 1
            if j >= n:
                # Desync: treat the backtick as an ordinary character and keep
                # reading code, so real statements after it are still rewritten.
                out[i] = "\x00"
                i = i + 1
                prev = "`"
                continue
            for k in range(i, min(j, n)):
                out[k] = "\x00"
            i = j
            prev = "`"
            continue
        if ch in "\"'":
            # Keep ordinary quote delimiters visible so specifier patterns can
            # still match the literal, while replacing the literal contents.
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == ch:
                    j += 1
                    break
                j += 1
            if j >= n:
                out[i] = "\x00"
                i = i + 1
                prev = ch
                continue
            for k in range(i + 1, j - 1 if j <= n and code[j - 1] == ch else min(j, n)):
                out[k] = "\x00"
            i = j
            prev = ch
            continue
        if ch == "/" and (
            _prev_ends_keyword(code, i)
            or (prev not in ")]}\"'`" and not (prev.isalnum() or prev in "_$"))
        ):
            # A '/' opens a regex only when the previous token cannot end an
            # expression. A string or template delimiter *is* value-ending, so
            # the '/' after `"path:"` is division, not a regex: treating it as a
            # regex scans forward for a closing '/' that never arrives and masks
            # the rest of the file, which silently swallows real statements.
            # regex literal: '/' after a value is division, after anything else it opens
            j = i + 1
            in_class = False
            regex_closed = False
            while j < n:
                c = code[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    j += 1
                    regex_closed = True
                    break
                elif c == "\n":
                    break
                j += 1
            while regex_closed and j < n and code[j].isalpha():
                j += 1
            if j >= n or not regex_closed:
                # Not a terminated regex: desync. Skip the opener only, so the
                # code after it is still visible to the rewrites.
                # NOTE: the terminated check must be the close event itself,
                # NOT code[j-1] == "/" — the flag-eating loop above advances j
                # past /g /gi /u flags, so code[j-1] is the LAST FLAG CHAR, and
                # the old check false-positived on EVERY flagged regex, leaving
                # its body as "code" where quotes then opened phantom strings
                # and swallowed real statements (a real bug on LinkedIn's
                # A2-4UjN7.js: depth never returned to 0 after a /g regex).
                out[i] = "\x00"
                i = i + 1
                prev = "/"
                continue
            for k in range(i, j):
                out[k] = "\x00"
            i = j
            prev = "/"
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out)


def _sub_code(code: str, pattern: str, repl, flags: int = 0):
    """``re.sub`` restricted to real code positions.

    ``repl`` receives the match (against the masked text, so captured groups are
    blank) and the original matched text, and returns the replacement. Matches
    inside literals never happen, because the pattern is applied to the mask.
    """
    masked = _mask_literals(code)
    out: list[str] = []
    last = 0
    for match in re.finditer(pattern, masked, flags):
        original = code[match.start():match.end()]
        out.append(code[last:match.start()])
        replacement = repl(match, original)
        out.append(original if replacement is None else replacement)
        last = match.end()
    out.append(code[last:])
    return "".join(out)


@dataclass(slots=True)
class ModuleSource:
    url: str
    code: str
    imports: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ModuleGraph:
    order: list[ModuleSource] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)

    def urls(self) -> list[str]:
        return [item.url for item in self.order]


def _iter_imports(code: str) -> list[str]:
    """Return raw specifier strings from static and dynamic import statements."""
    specs: list[str] = []
    masked = _mask_literals(code)
    for match in re.finditer(
        r"\b(?:import|export)\b[\s\S]*?\bfrom\s*[\"']([^\"']+)[\"']", masked
    ):
        quote = code[match.start(1):match.end(1)]
        specs.append(quote)
    for match in re.finditer(r"\bimport\s*[\"']([^\"']+)[\"']", masked):
        quote = code[match.start(1):match.end(1)]
        specs.append(quote)
    # Dynamic import("chunk") used by Vite/route-split bundles. Masking hides
    # template-literal call sites, so only literal call sites are discovered.
    for match in re.finditer(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)", masked):
        quote = code[match.start(1):match.end(1)]
        if quote not in specs:
            specs.append(quote)
    return specs


def _resolve(specifier: str, base_url: str, import_map: dict[str, str]) -> str | None:
    if not specifier:
        return None
    if specifier.startswith("data:"):
        return specifier
    if specifier in import_map:
        return import_map[specifier]
    # Import-map prefix mappings ("@/", "~/", bare package prefixes).
    for key, target in import_map.items():
        if key.endswith("/") and specifier.startswith(key):
            return target + specifier[len(key):]
    if specifier.startswith(("./", "../", "/")):
        return urljoin(base_url, specifier)
    if specifier.startswith(("http://", "https://")):
        return specifier
    # Bare specifiers (react, @scope/pkg) without a map entry: leave unresolved
    # but recorded, never throw. Caller logs graph.missing.
    return None


def _rewrite_imports(code: str, module_url: str, import_map: dict[str, str]) -> str:
    """Rewrite import bindings to registry lookups."""
    def resolve_id(spec: str) -> str:
        return _resolve(spec, module_url, import_map) or spec

    def _original_text(match: re.Match[str], original: str, group: int | None = None) -> str:
        if group is None:
            return original
        base = match.start()
        return original[match.start(group) - base:match.end(group) - base]

    def _spec_from(match: re.Match[str], group: int, original: str) -> str:
        """The specifier text, taken from the original (the mask blanks it)."""
        m = re.search(SPECIFIER_RE, _original_text(match, original, group))
        return m.group(1) if m else ""

    # import defaultName, { a as b } from "spec"
    def repl_binding(match: re.Match[str], original: str) -> str:
        default = match.group(1)
        named = match.group(2)
        star = match.group(3)
        spec = _spec_from(match, 4, original)
        mid = resolve_id(spec)
        out: list[str] = []
        if default:
            out.append(f"var {default} = __mods[{mid!r}].default;")
        if named:
            named_text = _original_text(match, original, 2)
            for part in named_text.split(","):
                part = part.strip()
                if not part:
                    continue
                alias_match = re.match(r"^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$", part)
                if alias_match:
                    orig, alias = alias_match.group(1), alias_match.group(2)
                else:
                    orig = alias = part
                out.append(f"var {alias} = __mods[{mid!r}][{orig!r}];")
        if star:
            out.append(f"var {star} = __mods[{mid!r}];")
        return " ".join(out)

    # ``\s*`` rather than ``\s+``: minified modules emit ``import{a as b}from"./x.js"``
    # with no whitespace at all, and a missed match leaves a raw ``import``
    # statement inside a classic script, which QuickJS rejects as a syntax error
    # and the whole module is lost.
    code = _sub_code(
        code,
        r"\bimport\s*(?:([A-Za-z_$][\w$]*)\s*,?\s*)?"
        r"(?:\{([^}]*)\}|\*\s*as\s*([A-Za-z_$][\w$]*))\s*from\s*"
        r"([\"']\x00*[\"']);?",
        repl_binding,
    )
    # import defaultName from "spec" — default-only, no braces and no star.
    # A real pattern from LinkedIn's oO-sjsKw.js: ``import iI from"./B5BL-RZl.js"``.
    # The combined pattern above requires a {..} or * as clause, so this form
    # needs its own rewrite or the raw ``import`` keyword survives and QuickJS
    # rejects the whole module.
    def repl_default_only(match: re.Match[str], original: str) -> str:
        default = match.group(1)
        spec = _spec_from(match, 2, original)
        mid = resolve_id(spec)
        return f"var {default} = __mods[{mid!r}].default;"

    code = _sub_code(
        code,
        r"\bimport\s+([A-Za-z_$][\w$]*)\s*from\s*([\"']\x00*[\"']);?",
        repl_default_only,
    )
    # ``import.meta`` is only legal inside module code, and every module here is
    # evaluated as a classic script, so a surviving ``import.meta`` is a hard
    # syntax error. Point it at the module's own id.
    code = _sub_code(
        code, r"\bimport\s*\.\s*meta\s*\.\s*(?:url|filename)\b", lambda m, o: repr(module_url)
    )
    code = _sub_code(
        code, r"\bimport\s*\.\s*meta\b", lambda m, o: "{ url: " + repr(module_url) + " }"
    )
    # Dynamic import(): QuickJS parses ImportCall in classic scripts but its
    # loader rejects every URL ("could not load module"), which killed como's
    # webpack chunk loading — React lazy chunks never registered and the
    # hydrated app crashed with "not a function". Rewrite every real-code
    # ``import(`` call to ``__wr_import(``, our loader (minidom).
    code = _sub_code(
        code,
        r"\bimport\s*\(",
        lambda m, o: "__wr_import(",
    )
    # Template-literal interpolations are masked as part of the literal, but
    # they are real code at eval time — a real case: LinkedIn's kvZetL-w.js has
    # ``createRequire(import.meta.url)`` in real class code that the FULL mask
    # swallowed via an apostrophe-in-template desync chain
    # (``Error(`Can't find font...`)`` — the apostrophe in ``Can't`` opened a
    # phantom string blanking 12k chars of code, so the masked rewrite never
    # saw the import.meta). This pass uses a template-aware string-only mask:
    # templates are skipped whole (their interpolations stay visible to THIS
    # pass), ordinary strings are blanked, and quotes inside templates or
    # regex bodies never flip the parity.
    def _mask_strings_only(src: str) -> str:
        out = list(src)
        i2, n2 = 0, len(src)
        prev2 = ""
        while i2 < n2:
            c2 = src[i2]
            if c2 == "`":
                # Skip the whole template: interpolations stay visible.
                j2 = i2 + 1
                while j2 < n2:
                    if src[j2] == "\\":
                        j2 += 2
                        continue
                    if src[j2] == "`":
                        j2 += 1
                        break
                    j2 += 1
                i2 = min(j2, n2)
                prev2 = "`"
                continue
            if c2 == "/" and prev2 not in ")]}\"'`" and not (prev2.isalnum() or prev2 in "_$"):
                # Regex body: skip so a quote inside /["']/ never opens a string.
                j2 = i2 + 1
                in_cls = False
                closed = False
                while j2 < n2:
                    c3 = src[j2]
                    if c3 == "\\":
                        j2 += 2
                        continue
                    if c3 == "[":
                        in_cls = True
                    elif c3 == "]":
                        in_cls = False
                    elif c3 == "/" and not in_cls:
                        j2 += 1
                        closed = True
                        break
                    elif c3 == "\n":
                        break
                    j2 += 1
                while closed and j2 < n2 and src[j2].isalpha():
                    j2 += 1
                if not closed:
                    i2 += 1
                    prev2 = "/"
                    continue
                i2 = j2
                prev2 = "/"
                continue
            if c2 in "\"'":
                j2 = i2 + 1
                while j2 < n2:
                    if src[j2] == "\\":
                        j2 += 2
                        continue
                    if src[j2] == c2:
                        j2 += 1
                        break
                    j2 += 1
                if j2 >= n2:
                    break
                for k2 in range(i2 + 1, j2 - 1 if src[j2 - 1] == c2 else min(j2, n2)):
                    out[k2] = "\x00"
                i2 = j2
                prev2 = c2
                continue
            if not c2.isspace():
                prev2 = c2
            i2 += 1
        return "".join(out)

    _smask = _mask_strings_only(code)
    for match in re.finditer(r"\bimport\s*\.\s*meta\s*\.\s*(?:url|filename)\b", _smask):
        code = code[: match.start()] + repr(module_url) + code[match.end():]
        _smask = _mask_strings_only(code)
    for match in re.finditer(r"\bimport\s*\.\s*meta\b", _smask):
        code = code[: match.start()] + "{ url: " + repr(module_url) + " }" + code[match.end():]
        _smask = _mask_strings_only(code)
    # import "spec";  (side effect)
    code = _sub_code(
        code,
        r"\bimport\s*([\"']\x00*[\"']);?",
        lambda m, o: f"__require({resolve_id(_spec_from(m, 1, o))!r});",
    )
    # export { a, b as c } from "spec";
    code = _sub_code(
        code,
        r"\bexport\s*\{([^}]*)\}\s*from\s*([\"']\x00*[\"']);?",
        lambda m, o: _repl_reexport(resolve_id(_spec_from(m, 2, o)), _original_text(m, o, 1)),
    )
    # export * from "spec";
    code = _sub_code(
        code,
        r"\bexport\s*\*\s*from\s*([\"']\x00*[\"']);?",
        lambda m, o: (
            f"Object.keys(__mods[{resolve_id(_spec_from(m, 1, o))!r}] || {{}}).forEach("
            f"function (k) {{ if (k !== 'default') __exports[k] = "
            f"__mods[{resolve_id(_spec_from(m, 1, o))!r}][k]; }});"
        ),
    )
    return code


def _capture(names: list[str], kind: str, name: str) -> str:
    names.append(name)
    return f"{kind} {name}"


def _repl_reexport(module_id: str, list_text: str) -> str:
    parts = [p.strip() for p in list_text.split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        alias_match = re.match(r"^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$", part)
        if alias_match:
            orig, alias = alias_match.group(1), alias_match.group(2)
        else:
            orig = alias = part
        out.append(f"__exports[{alias!r}] = __mods[{module_id!r}][{orig!r}];")
    return " ".join(out)


def _rewrite_exports(code: str) -> str:
    """Rewrite export declarations to assignments on ``__exports``."""
    def _text(match: re.Match[str], original: str, group: int) -> str:
        base = match.start()
        return original[match.start(group) - base:match.end(group) - base]

    # export default function/class name(...) {}
    code = _sub_code(
        code,
        r"\bexport\s+default\s+(function|class)\s+([A-Za-z_$][\w$]*)",
        lambda m, o: f"__exports.default = {m.group(1)} {m.group(2)}",
    )
    # export default <expr>;
    code = _sub_code(
        code,
        r"\bexport\s+default\s+",
        lambda m, o: "__exports.default = ",
    )
    # export const/let/var NAME = ...
    export_names: list[str] = []

    def decl_capture(match: re.Match[str], original: str) -> str:
        kind, name = match.group(1), match.group(2)
        export_names.append(name)
        return f"{kind} {name}"

    code = _sub_code(
        code,
        r"\bexport\s+(const|let|var)\s+([A-Za-z_$][\w$]*)",
        decl_capture,
    )
    # export function/class name
    code = _sub_code(
        code,
        r"\bexport\s+(function|class)\s+([A-Za-z_$][\w$]*)",
        lambda m, o: _capture(export_names, m.group(1), m.group(2)),
    )
    if export_names:
        assigns = " ".join(f"__exports[{n!r}] = {n};" for n in export_names)
        code = f"{code}\n{assigns}"
    # export { a, b as c };
    code = _sub_code(
        code,
        r"\bexport\s*\{([^}]*)\}\s*;?",
        lambda m, o: _repl_export_list("__exports", _text(m, o, 1)),
    )
    return code


def _repl_export_list(target: str, list_text: str) -> str:
    parts = [p.strip() for p in list_text.split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        alias_match = re.match(r"^([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)$", part)
        if alias_match:
            orig, alias = alias_match.group(1), alias_match.group(2)
        else:
            orig = alias = part
        out.append(f"{target}[{alias!r}] = {orig};")
    return " ".join(out)


def _hoisted_export_names(code: str) -> list[str]:
    """Exported function/class declaration names, for circular-import support.

    Real ESM hoists ``export function NAME`` bindings into the module
    environment before evaluation, so a circular importer can call NAME while
    the exporter's body has not run yet. Registry emulation needs the same:
    pre-register the hoisted names right after the module header. Function
    declarations are hoisted within this IIFE, so the values exist there.
    """
    masked = _mask_literals(code)
    names: list[str] = []
    for match in re.finditer(
        r"\bexport\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?)\s+([A-Za-z_$][\w$]*)",
        masked,
    ):
        names.append(match.group(1))
    for match in re.finditer(r"\bexport\s+class\s+([A-Za-z_$][\w$]*)", masked):
        names.append(match.group(1))
    return names


def _fix_class_static_selfref(code: str) -> str:
    """Rewrite class-name self-references in STATIC FIELD INITIALIZERS to ``this``.

    ES2022: inside a static field initializer ``this`` is the class itself, so
    ``class A { static x = 5; static y = A.x + 1; }`` is valid in real browsers
    and engines. QuickJS (this build) rejects it with a TDZ ReferenceError —
    ``A is not initialized`` — because the class binding is not yet live while
    the initializer runs. ``this`` IS live there, so rewriting ``A`` to
    ``this`` inside static initializers is a spec-faithful fix. Only static
    field initializers are rewritten; method bodies run after class
    initialization and keep the class-name reference.
    """
    masked = _mask_literals(code)
    n = len(code)
    edits: list[tuple[int, int]] = []  # (start, end) of each NAME token to swap
    for decl in re.finditer(r"\bclass\s+([A-Za-z_$][\w$]*)\s*(?:extends\s+[({\w$.]+?\s*)?\{", masked):
        cls_name = decl.group(1)
        name_re = re.compile(r"\b" + re.escape(cls_name) + r"\b")
        i = decl.end()  # position right after '{'
        depth = 1  # brace depth inside the class body
        while i < n and depth > 0:
            c = masked[i]
            if c == "{":
                depth += 1
                i += 1
                continue
            if c == "}":
                depth -= 1
                i += 1
                continue
            # at member level: does this member start with 'static'?
            if (
                depth == 1
                and masked.startswith("static", i)
                and (i == 0 or not (masked[i - 1].isalnum() or masked[i - 1] in "_$"))
                and (i + 6 >= n or not (masked[i + 6].isalnum() or masked[i + 6] in "_$"))
            ):
                # scan the initializer; commas/semicolons at ALL-zero relative
                # depth end it (member separator or next member).
                j = i
                par = brk = brace = 0
                while j < n:
                    cj = masked[j]
                    if cj in "([{":
                        if cj == "(":
                            par += 1
                        elif cj == "[":
                            brk += 1
                        else:
                            brace += 1
                    elif cj in ")]}":
                        if cj == ")" and par > 0:
                            par -= 1
                        elif cj == "]" and brk > 0:
                            brk -= 1
                        elif cj == "}":
                            if brace > 0:
                                brace -= 1
                            else:
                                break  # end of class body
                    elif cj in ";," and par == 0 and brk == 0 and brace == 0:
                        break  # member separator
                    if (
                        par == 0
                        and brk == 0
                        and brace == 0
                        and name_re.match(masked, j)
                    ):
                        before = masked[j - 1]
                        after = masked[j + len(cls_name)] if j + len(cls_name) < n else ""
                        # Reject property access (foo.Cls) and private member
                        # names (#Cls) — a real bug: a private method `#tk`
                        # inside class `tk` was rewritten to `#this`, breaking
                        # every use of the private method (SyntaxError:
                        # undefined private field).
                        if (
                            before in "#."
                            or before.isalnum()
                            or before in "_$"
                            or after.isalnum()
                            or after in "_$"
                        ):
                            pass  # not a whole-token class reference
                        else:
                            edits.append((j, j + len(cls_name)))
                    j += 1
                i = j
                continue
            i += 1
    if not edits:
        return code
    out = code
    for start, end in reversed(edits):
        out = out[:start] + "this" + out[end:]
    return out


def _fix_private_in_checks(code: str) -> str:
    """Rewrite ES2022 ergonomic brand checks ``#field in obj`` to instanceof.

    QuickJS (1.19.4) rejects ``#x in obj`` at parse time — ``SyntaxError:
    undefined private field '#x'`` — while real browsers accept it (ES2022
    brand checks). A private instance field exists on an object exactly when
    that object was constructed by the field's class, so inside the class
    body ``#x in obj`` is equivalent to ``obj instanceof ClassName`` for the
    standard brand-check pattern (fields added unconditionally in the
    constructor). This rewrite applies only inside class bodies, where the
    private field and the class name are both in scope.
    """
    masked = _mask_literals(code)
    n = len(code)
    edits: list[tuple[int, int, str]] = []
    # track enclosing classes while walking
    class_stack: list[tuple[str, int]] = []  # (name, brace_depth_at_entry)
    depth = 0
    i = 0
    pending_class_name = ""
    while i < n:
        c = masked[i]
        if c == "{":
            depth += 1
            if pending_class_name:
                class_stack.append((pending_class_name, depth))
                pending_class_name = ""
            i += 1
            continue
        if c == "}":
            if class_stack and class_stack[-1][1] == depth:
                class_stack.pop()
            depth -= 1
            i += 1
            continue
        m = re.match(r"\bclass\s+([A-Za-z_$][\w$]*)", masked[i : i + 64])
        if m and not (masked[i - 1].isalnum() or masked[i - 1] in "_$"):
            pending_class_name = m.group(1)
            i += m.end()
            continue
        pm = re.match(r"#([A-Za-z_$][\w$]*)\s+in\s+", masked[i : i + 64])
        if pm and class_stack:
            cls_name = class_stack[-1][0]
            # expression extent: identifier chain or parenthesized expr
            j = i + pm.end()
            start_expr = j
            par = 0
            while j < n:
                cj = masked[j]
                if cj == "(":
                    par += 1
                elif cj == ")":
                    if par == 0:
                        break
                    par -= 1
                elif par == 0 and (cj in ",;{}" or cj in "=&|?:" or masked.startswith("&&", j) or masked.startswith("||", j)):
                    break
                j += 1
            expr = code[start_expr:j].strip()
            if expr and re.match(r"^[A-Za-z_$][\w$.]*", expr):
                replacement = f"(({expr}) instanceof {cls_name})"
                edits.append((i, j, replacement))
            i = j
            continue
        i += 1
    if not edits:
        return code
    out = code
    for start, end, replacement in reversed(edits):
        out = out[:start] + replacement + out[end:]
    return out


def transform_module(
    code: str, module_id: str, import_map: dict[str, str] | None = None
) -> str:
    """Translate one ES module into registry-registered classic script."""
    code = _quickjs_regex_compat(code)
    code = _fix_class_static_selfref(code)
    code = _fix_private_in_checks(code)
    hoisted = _hoisted_export_names(code)
    body = _rewrite_exports(_rewrite_imports(code, module_id, import_map or {}))
    header = f"__mods[{module_id!r}] = __mods[{module_id!r}] || {{}};"
    # Circular imports: pre-register hoisted function/class exports so an
    # importer that runs mid-cycle (DFS order) sees the real function objects,
    # mirroring ESM hoisting. Non-hoisted bindings stay undefined until the
    # suffix assignment — the same shape real ESM TDZ approximates.
    prereg = ""
    if hoisted:
        prereg = (
            "try {\n"
            + "\n".join(
                f"__mods[{module_id!r}][{name!r}] = typeof {name} !== 'undefined' ? {name} : undefined;"
                for name in hoisted
            )
            + "\n} catch (e) {}"
        )
    suffix = (
        f"__mods[{module_id!r}] = Object.assign("
        f"__mods[{module_id!r}] || {{}}, __exports);"
    )
    return (
        f"(function () {{\nvar __exports = {{}};\n{header}\n{prereg}\n{body}\n{suffix}\n}})();"
    )


def extract_import_map(html: str) -> dict[str, str]:
    """Parse inline <script type="importmap"> blocks if present.

    External maps (<script type="importmap" src="...">) need a fetch; use
    resolve_import_map() from async code paths.
    """
    import json

    out: dict[str, str] = {}
    for match in re.finditer(
        r"<script[^>]*type=[\"']importmap[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        out.update(data.get("imports") or {})
    return out


def importmap_srcs(html: str) -> list[str]:
    """External importmap URLs (<script type=importmap src=...>)."""
    out: list[str] = []
    for match in re.finditer(
        r"<script[^>]*type=[\"']importmap[\"'][^>]*>",
        html,
        flags=re.IGNORECASE,
    ):
        tag = match.group(0)
        m = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", tag, flags=re.IGNORECASE)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


async def resolve_import_map(html: str, base_url: str, client: httpx.AsyncClient) -> dict[str, str]:
    """Inline maps plus best-effort fetch of external importmap src files."""
    import json as _json
    from urllib.parse import urljoin as _join

    merged = extract_import_map(html)
    for src in importmap_srcs(html)[:3]:
        try:
            resp = await client.get(_join(base_url, src), timeout=10.0)
            data = _json.loads(resp.text or "{}")
            merged.update(data.get("imports") or {})
        except Exception:
            continue
    return merged


async def load_module_graph(
    entry_scripts: list[dict[str, str]],
    base_url: str,
    client: httpx.AsyncClient,
    *,
    import_map: dict[str, str] | None = None,
    max_modules: int = 60,
    csp: str | None = None,
    headers: dict | None = None,
) -> ModuleGraph:
    """Fetch and topologically order the module graph for inline/src modules."""
    import_map = import_map or {}
    graph = ModuleGraph()
    seen: set[str] = set()

    async def fetch_code(url: str) -> str | None:
        if not url:
            return None
        if url.startswith("data:"):
            from webresearch.engine.frames import _decode_data_url

            try:
                return _decode_data_url(url)[0]
            except Exception:
                return None
        try:
            from webresearch.engine.trackers import GRAPH as _TRACKER_GRAPH
            decision = _TRACKER_GRAPH.evaluate(url, base_url)
            if decision.status == "BLOCKED":
                if decision.surrogate:
                    return decision.surrogate
                return None
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            from webresearch.engine.cross_origin import check_subresource as _check_xo
            xo_reason, _ = _check_xo(resp.headers, url, base_url, headers)
            if xo_reason is not None:
                graph.blocked.append({"url": url, "reason": xo_reason})
                return None
            return resp.text
        except Exception:
            return None

    async def visit(url: str, inline: str | None, integrity: str = "") -> None:
        if len(graph.order) >= max_modules:
            return
        if url in seen:
            return
        seen.add(url)
        if url and mixed_content_blocked(base_url, url):
            graph.missing.append(url)
            return
        if csp and url and not script_source_allowed(csp, external_url=url, page_url=base_url):
            graph.missing.append(url)
            return
        if csp and inline is not None and not script_source_allowed(csp, inline_code=inline):
            graph.missing.append(url or "inline")
            return
        code = inline if inline is not None else await fetch_code(url)
        if code is None:
            graph.missing.append(url)
            return
        if integrity and not verify_integrity(code, integrity):
            graph.missing.append(url)
            return
        resolved_map: dict[str, str] = {}
        for spec in _iter_imports(code):
            resolved = _resolve(spec, url or base_url, import_map)
            if resolved:
                resolved_map[spec] = resolved
                await visit(resolved, None)
        graph.order.append(ModuleSource(url=url, code=code, imports=resolved_map))

    for script in entry_scripts:
        src = script.get("src") or ""
        code = script.get("code") or ""
        if src:
            await visit(urljoin(base_url, src), None, script.get("integrity", ""))
        elif code.strip():
            await visit("", code)
    return graph