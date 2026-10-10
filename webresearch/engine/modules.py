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
    """Return raw specifier strings from static import/re-export statements."""
    specs: list[str] = []
    for match in re.finditer(
        r"\b(?:import|export)\b[\s\S]*?\bfrom\s*[\"']([^\"']+)[\"']", code
    ):
        specs.append(match.group(1))
    for match in re.finditer(r"\bimport\s*[\"']([^\"']+)[\"']", code):
        specs.append(match.group(1))
    return specs


def _resolve(specifier: str, base_url: str, import_map: dict[str, str]) -> str | None:
    if not specifier:
        return None
    if specifier.startswith("data:"):
        return specifier
    if specifier in import_map:
        return import_map[specifier]
    if specifier.startswith(("./", "../", "/")):
        return urljoin(base_url, specifier)
    if specifier.startswith(("http://", "https://")):
        return specifier
    return None


def _rewrite_imports(code: str, module_url: str, import_map: dict[str, str]) -> str:
    """Rewrite import bindings to registry lookups."""
    def resolve_id(spec: str) -> str:
        return _resolve(spec, module_url, import_map) or spec

    # import defaultName, { a as b } from "spec"
    def repl_binding(match: re.Match[str]) -> str:
        default = match.group(1)
        named = match.group(2)
        star = match.group(3)
        spec = match.group(4)
        mid = resolve_id(spec)
        out: list[str] = []
        if default:
            out.append(f"var {default} = __mods[{mid!r}].default;")
        if named:
            for part in named.split(","):
                part = part.strip()
                if not part:
                    continue
                if " as " in part:
                    orig, alias = [p.strip() for p in part.split(" as ", 1)]
                else:
                    orig = alias = part
                out.append(f"var {alias} = __mods[{mid!r}][{orig!r}];")
        if star:
            out.append(f"var {star} = __mods[{mid!r}];")
        return " ".join(out)

    code = re.sub(
        r"\bimport\s+(?:([A-Za-z_$][\w$]*)\s*,?\s*)?"
        r"(?:\{([^}]*)\}|\*\s*as\s+([A-Za-z_$][\w$]*))\s*from\s*"
        r"[\"']([^\"']+)[\"'];?",
        repl_binding,
        code,
    )
    # import "spec";  (side effect)
    code = re.sub(
        r"\bimport\s*[\"']([^\"']+)[\"'];?",
        lambda m: f"__require({resolve_id(m.group(1))!r});",
        code,
    )
    # export { a, b as c } from "spec";
    code = re.sub(
        r"\bexport\s*\{([^}]*)\}\s*from\s*[\"']([^\"']+)[\"'];?",
        lambda m: _repl_reexport(m, resolve_id(m.group(2))),
        code,
    )
    # export * from "spec";
    code = re.sub(
        r"\bexport\s*\*\s*from\s*[\"']([^\"']+)[\"'];?",
        lambda m: (
            f"Object.keys(__mods[{resolve_id(m.group(1))!r}] || {{}}).forEach("
            f"function (k) {{ if (k !== 'default') __exports[k] = "
            f"__mods[{resolve_id(m.group(1))!r}][k]; }});"
        ),
        code,
    )
    return code


def _capture(names: list[str], kind: str, name: str) -> str:
    names.append(name)
    return f"{kind} {name}"


def _repl_reexport(match: re.Match[str], module_id: str) -> str:
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        if " as " in part:
            orig, alias = [p.strip() for p in part.split(" as ", 1)]
        else:
            orig = alias = part
        out.append(f"__exports[{alias!r}] = __mods[{module_id!r}][{orig!r}];")
    return " ".join(out)


def _rewrite_exports(code: str) -> str:
    """Rewrite export declarations to assignments on ``__exports``."""
    # export default function/class name(...) {}
    code = re.sub(
        r"\bexport\s+default\s+(function|class)\s+([A-Za-z_$][\w$]*)",
        lambda m: f"__exports.default = {m.group(1)} {m.group(2)}",
        code,
    )
    # export default <expr>;
    code = re.sub(
        r"\bexport\s+default\s+",
        "__exports.default = ",
        code,
    )
    # export const/let/var NAME = ...
    export_names: list[str] = []

    def decl_capture(match: re.Match[str]) -> str:
        kind, name = match.group(1), match.group(2)
        export_names.append(name)
        return f"{kind} {name}"

    code = re.sub(
        r"\bexport\s+(const|let|var)\s+([A-Za-z_$][\w$]*)",
        decl_capture,
        code,
    )
    # export function/class name
    code = re.sub(
        r"\bexport\s+(function|class)\s+([A-Za-z_$][\w$]*)",
        lambda m: _capture(export_names, m.group(1), m.group(2)),
        code,
    )
    if export_names:
        assigns = " ".join(f"__exports[{n!r}] = {n};" for n in export_names)
        code = f"{code}\n{assigns}"
    # export { a, b as c };
    code = re.sub(
        r"\bexport\s*\{([^}]*)\}\s*;?",
        lambda m: _repl_export_list(m, "__exports"),
        code,
    )
    return code


def _repl_export_list(match: re.Match[str], target: str) -> str:
    parts = [p.strip() for p in match.group(1).split(",") if p.strip()]
    out: list[str] = []
    for part in parts:
        if " as " in part:
            orig, alias = [p.strip() for p in part.split(" as ", 1)]
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
    """Parse an <script type="importmap"> block if present."""
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