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
        if ch == "/" and prev not in ")]}\"'`" and not (prev.isalnum() or prev in "_$"):
            # A '/' opens a regex only when the previous token cannot end an
            # expression. A string or template delimiter *is* value-ending, so
            # the '/' after `"path:"` is division, not a regex: treating it as a
            # regex scans forward for a closing '/' that never arrives and masks
            # the rest of the file, which silently swallows real statements.
            # regex literal: '/' after a value is division, after anything else it opens
            j = i + 1
            in_class = False
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
                    break
                elif c == "\n":
                    break
                j += 1
            while j < n and code[j].isalpha():
                j += 1
            if j >= n or code[j - 1] != "/":
                # Not a terminated regex: desync. Skip the opener only, so the
                # code after it is still visible to the rewrites.
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
    # ``import.meta`` is only legal inside module code, and every module here is
    # evaluated as a classic script, so a surviving ``import.meta`` is a hard
    # syntax error. Point it at the module's own id.
    code = _sub_code(
        code, r"\bimport\s*\.\s*meta\s*\.\s*(?:url|filename)\b", lambda m, o: repr(module_url)
    )
    code = _sub_code(
        code, r"\bimport\s*\.\s*meta\b", lambda m, o: "{ url: " + repr(module_url) + " }"
    )
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


def transform_module(
    code: str, module_id: str, import_map: dict[str, str] | None = None
) -> str:
    """Translate one ES module into registry-registered classic script."""
    body = _rewrite_exports(_rewrite_imports(code, module_id, import_map or {}))
    header = f"__mods[{module_id!r}] = __mods[{module_id!r}] || {{}};"
    suffix = (
        f"__mods[{module_id!r}] = Object.assign("
        f"__mods[{module_id!r}] || {{}}, __exports);"
    )
    return f"(function () {{\nvar __exports = {{}};\n{header}\n{body}\n{suffix}\n}})();"


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