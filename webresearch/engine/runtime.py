"""QuickJS page runtime. Own DOM, not Chromium."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from quickjs import Context

from webresearch.engine.modules import (
    extract_import_map,
    load_module_graph,
    transform_module,
)
from webresearch.engine.tree import extract_scripts, html_to_tree
from webresearch.fetch import (
    DEFAULT_IMPERSONATE,
    _curl_requests,
    _impersonation_allowed,
)

MINIDOM_JS = Path(__file__).with_name("minidom.js").read_text(encoding="utf-8")

SKIP_SRC = (
    "google-analytics",
    "googletagmanager",
    "doubleclick.net",
    "hotjar",
    "segment.com",
    "facebook.net/signals",
    "scorecardresearch",
)

MAX_SCRIPTS = 120
MAX_SCRIPT_BYTES = 4_000_000
MAX_MODULES = 80
MAX_JOBS = 20_000
MAX_TIMER_PASSES = 25
JS_TIME_LIMIT_SECONDS = 10.0


def _wants_js(html: str) -> bool:
    low = html.lower()
    if "<script" not in low:
        return False
    if 'id="root"' in low or 'id="app"' in low or "data-reactroot" in low:
        return True
    if low.count("<script") >= 2:
        return True
    return "react" in low or "vue" in low or "__next" in low


def _host_parse_html(html: str) -> str:
    tree = html_to_tree(f"<div id='__frag'>{html}</div>")
    for child in tree.get("c") or []:
        if child.get("t") == "div" and (child.get("a") or {}).get("id") == "__frag":
            return json.dumps(child.get("c") or [])
    return json.dumps(tree.get("c") or [])


def _pump_jobs(ctx: Context) -> int:
    done = 0
    for _ in range(MAX_JOBS):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
        done += 1
    return done


async def run_scripts(
    html: str,
    url: str,
    client: httpx.AsyncClient,
    *,
    force: bool = False,
    actions: list[dict] | None = None,
) -> tuple[str, dict]:
    """Execute page scripts against mini-DOM. Returns (html, info)."""
    info: dict = {
        "ran": False,
        "scripts": 0,
        "modules": 0,
        "skipped_modules": 0,
        "errors": 0,
        "error_messages": [],
        "engine": "quickjs",
        "post_messages": [],
        "resource_requests": [],
        "framework_data": {},
        "actions_applied": [],
    }
    if not force and not _wants_js(html):
        return html, info

    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.add_callable("host_log", lambda *_a: None)
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.add_callable(
        "host_post_message",
        lambda data, origin="*": info["post_messages"].append(
            {"data": str(data)[:4000], "origin": str(origin)[:500]}
        ),
    )

    def host_get_cookie() -> str:
        try:
            return "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        except Exception:
            return ""

    def host_set_cookie(raw: str) -> None:
        try:
            pair = str(raw).split(";", 1)[0]
            if "=" in pair:
                name, _, value = pair.partition("=")
                client.cookies.set(name.strip(), value.strip())
        except Exception:
            pass

    ctx.add_callable("host_get_cookie", host_get_cookie)
    ctx.add_callable("host_set_cookie", host_set_cookie)

    sync_kwargs: dict = {
        "timeout": 12.0,
        "follow_redirects": True,
        "headers": dict(client.headers),
        "cookies": client.cookies,
    }
    transport = getattr(client, "_transport", None)
    if isinstance(transport, httpx.MockTransport):
        sync_kwargs["transport"] = transport
    sync = httpx.Client(**sync_kwargs)
    curl_sync = None
    if _curl_requests is not None and _impersonation_allowed(client) and DEFAULT_IMPERSONATE:
        try:
            curl_sync = _curl_requests.Session(
                impersonate=DEFAULT_IMPERSONATE,
                timeout=12,
                headers=dict(client.headers),
                cookies={k: v for k, v in client.cookies.items()},
            )
        except Exception:
            curl_sync = None

    def do_request(method: str, abs_url: str, body: str, headers: dict) -> tuple[int, str, str, str, dict]:
        if curl_sync is not None:
            resp = curl_sync.request(
                method or "GET", abs_url, data=body or None, headers=headers,
                allow_redirects=True,
            )
            return (
                resp.status_code,
                str(resp.url),
                resp.headers.get("content-type", ""),
                resp.text,
                {k.lower(): v for k, v in resp.headers.items()},
            )
        resp = sync.request(method or "GET", abs_url, content=body or None, headers=headers)
        return (
            resp.status_code,
            str(resp.url),
            resp.headers.get("content-type", ""),
            resp.text,
            {k.lower(): v for k, v in resp.headers.items()},
        )

    def _page_origin(page_url: str) -> str:
        p = urlparse(page_url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""

    def _cors_blocked(page_url: str, resp_headers: dict) -> bool:
        """A real browser blocks cross-origin reads lacking a matching ACAO."""
        page_origin = _page_origin(page_url)
        if not page_origin:
            return False
        resp_origin = (resp_headers.get("access-control-allow-origin") or "").strip()
        # Same-origin: no CORS needed. We approximate by checking the request's
        # Origin header against ACAO when the target differs from the page.
        acao_values = {v.strip() for v in resp_origin.split(",")}
        if "*" in acao_values:
            return False
        if page_origin in acao_values:
            return False
        return True

    def host_fetch(req_url: str, method: str, body: str) -> str:
        try:
            abs_url = urljoin(url, req_url)
            parsed = urlparse(abs_url)
            target_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
            page_origin = _page_origin(url)
            headers = {}
            if parsed.scheme and parsed.netloc:
                headers = {
                    "Referer": url,
                    "Origin": target_origin if target_origin == page_origin else page_origin,
                    "Accept": "*/*",
                }
            status, final, ctype, text, resp_headers = do_request(method, abs_url, body, headers)
            info["resource_requests"].append(
                {"url": final[:2000], "status": status, "content_type": ctype[:200]}
            )
            # Enforce CORS for cross-origin reads (like a real browser).
            if (
                target_origin
                and page_origin
                and target_origin != page_origin
                and _cors_blocked(url, resp_headers)
            ):
                info["resource_requests"].append(
                    {"url": abs_url[:2000], "status": 0, "content_type": "cors-blocked"}
                )
                return json.dumps(
                    {
                        "status": 0,
                        "url": abs_url,
                        "content_type": "",
                        "body": "",
                        "cors_error": (
                            f"Cross-Origin Request Blocked: {abs_url} "
                            f"does not allow origin {page_origin}"
                        ),
                    }
                )
            return json.dumps(
                {
                    "status": status,
                    "url": final,
                    "content_type": ctype,
                    "body": text[:800_000],
                }
            )
        except Exception as exc:
            return json.dumps({"status": 599, "body": str(exc)})

    ctx.add_callable("host_fetch", host_fetch)

    ua = client.headers.get("user-agent", "")
    ctx.eval(f"globalThis.__WR_UA = {json.dumps(ua)};")
    ctx.eval("globalThis.__WR_MODS = {};")

    tree = html_to_tree(html)
    payload = json.dumps(tree, ensure_ascii=False)
    ctx.eval(f"__load({payload}, {json.dumps(url)})")

    scripts, _ = extract_scripts(html)
    classic: list[dict[str, str]] = []
    module_scripts: list[dict[str, str]] = []
    for script in scripts:
        stype = (script.get("type") or "").lower()
        if stype in {"application/ld+json", "application/json", "text/html", "importmap", "speculationrules"}:
            continue
        if "module" in stype:
            module_scripts.append(script)
        else:
            classic.append(script)

    def record_error(exc: Exception) -> None:
        info["errors"] += 1
        if len(info["error_messages"]) < 25:
            info["error_messages"].append(f"{type(exc).__name__}: {exc}"[:300])

    # Classic scripts first (many bundles register before modules run).
    ran = 0
    for script in classic:
        if ran >= MAX_SCRIPTS:
            break
        code = script.get("code") or ""
        src = script.get("src") or ""
        if src:
            low = src.lower()
            if any(skip in low for skip in SKIP_SRC):
                continue
            try:
                resp = await client.get(urljoin(url, src))
                code = resp.text
            except Exception as exc:
                record_error(exc)
                continue
        code = code.strip()
        if not code or len(code.encode()) > MAX_SCRIPT_BYTES:
            continue
        try:
            ctx.eval(code)
            ran += 1
            _pump_jobs(ctx)
        except Exception as exc:
            record_error(exc)

    # ES modules via the registry translator.
    if module_scripts:
        import_map = extract_import_map(html)
        graph = await load_module_graph(
            module_scripts,
            url,
            client,
            import_map=import_map,
            max_modules=MAX_MODULES,
        )
        info["skipped_modules"] = len(graph.missing)
        for item in graph.order:
            module_id = item.url or f"inline:{ran}"
            try:
                code = transform_module(item.code, module_id, item.imports)
                ctx.eval(code)
                info["modules"] += 1
                ran += 1
                _pump_jobs(ctx)
            except Exception as exc:
                record_error(exc)
            if ran >= MAX_SCRIPTS + MAX_MODULES:
                break
        if graph.missing and len(info["error_messages"]) < 25:
            info["error_messages"].append(
                "unresolved module(s): " + ", ".join(graph.missing[:5])
            )

    for _ in range(MAX_TIMER_PASSES):
        try:
            left = ctx.eval("__drainTimers()")
        except Exception:
            break
        _pump_jobs(ctx)
        if not left:
            break

    # Apply agent interactions against the live (post-hydration) DOM.
    if actions:
        import json as _json

        for action in actions:
            kind = (action or {}).get("kind") or "click"
            path = (action or {}).get("path") or []
            try:
                if kind == "type":
                    text = (action or {}).get("text") or ""
                    ok = ctx.eval(f"__typePath({_json.dumps(path)}, {_json.dumps(text)})")
                elif kind == "submit":
                    ok = ctx.eval(f"__submitPath({_json.dumps(path)})")
                    info.setdefault("submit_outcome", str(ok))
                elif kind == "set_form_fields":
                    index = int((action or {}).get("index", 0))
                    fields = (action or {}).get("fields") or {}
                    ok = ctx.eval(
                        f"__setFormFields({index}, {_json.dumps(_json.dumps(fields))})"
                    )
                elif kind == "submit_form":
                    index = int((action or {}).get("index", 0))
                    ok = ctx.eval(f"__submitFormByIndex({index})")
                    info.setdefault("submit_outcome", str(ok))
                elif kind == "scroll":
                    x = (action or {}).get("x", 0)
                    y = (action or {}).get("y", 800)
                    ok = ctx.eval(f"scrollTo({_json.dumps(x)}, {_json.dumps(y)}); true")
                else:
                    ok = ctx.eval(f"__clickPath({_json.dumps(path)})")
                info["actions_applied"].append({"kind": kind, "path": path, "ok": bool(ok)})
            except Exception as exc:
                info["actions_applied"].append(
                    {"kind": kind, "path": path, "ok": False, "error": str(exc)[:200]}
                )
            _pump_jobs(ctx)
        for _ in range(MAX_TIMER_PASSES):
            try:
                left = ctx.eval("__drainTimers()")
            except Exception:
                break
            _pump_jobs(ctx)
            if not left:
                break

    try:
        out = str(ctx.eval("__serialize()") or html)
    except Exception:
        out = html

    info["framework_data"] = _extract_framework_data(ctx, html)

    client.cookies.update(sync.cookies)
    sync.close()
    if curl_sync is not None:
        try:
            for key, value in curl_sync.cookies.items():
                client.cookies.set(key, value)
            curl_sync.close()
        except Exception:
            pass
    info["ran"] = ran > 0
    info["scripts"] = ran
    return out, info


def _safe_json(value: object) -> object:
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "json"):
        try:
            return json.loads(value.json())
        except Exception:
            pass
    try:
        return json.loads(str(value))
    except Exception:
        return str(value)[:2000]


def _extract_framework_data(ctx: Context, html: str) -> dict:
    """Pull framework hydration blobs from the live JS context."""
    keys = [
        "__NEXT_DATA__",
        "__NUXT__",
        "__APOLLO_STATE__",
        "__PRELOADED_STATE__",
        "__INITIAL_STATE__",
        "__REDUX_STATE__",
        "__remixContext",
        "Blazor",
    ]
    out: dict[str, object] = {}
    for key in keys:
        try:
            raw = ctx.eval(
                f"(typeof globalThis.{key} !== 'undefined') ? "
                f"JSON.stringify(globalThis.{key}) : null"
            )
        except Exception:
            continue
        if not raw or raw == "null":
            continue
        try:
            out[key] = json.loads(str(raw))
        except Exception:
            out[key] = str(raw)[:4000]
    return out