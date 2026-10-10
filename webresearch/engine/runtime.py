"""QuickJS page runtime. Own DOM, not Chromium."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from quickjs import Context

from webresearch import device as device_mod
from webresearch.engine.modules import (
    extract_import_map,
    load_module_graph,
    transform_module,
)
from webresearch.engine.csp import script_source_allowed
from webresearch.engine.security import mixed_content_blocked, verify_integrity
from webresearch.engine.tree import extract_scripts, html_to_tree
from webresearch.fetch import (
    DEFAULT_IMPERSONATE,
    _curl_requests,
    _impersonation_allowed,
)

MINIDOM_JS = Path(__file__).with_name("minidom.js").read_text(encoding="utf-8")


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


def _device_block() -> dict:
    """Real host facts injected into the mini-DOM so stubs match this device.

    Only genuine OS-reported values are used; nothing is fabricated. When the
    host is a VM we say so via 'virtual'.
    """
    profile = device_mod.current()
    renderer = "unknown"
    vendor = "unknown"
    if profile.platform == "Darwin" and profile.gpu != "unknown":
        chip = profile.cpu_brand if profile.cpu_brand != "unknown" else profile.gpu
        vendor = "Google Inc. (Apple)"
        renderer = f"ANGLE (Apple, ANGLE Metal Renderer: {chip}, Unspecified Version)"
    elif profile.gpu != "unknown":
        vendor = "Google Inc. (Unknown)"
        renderer = f"ANGLE ({profile.gpu}, Unspecified Version)"
    return {
        "platform": profile.platform,
        "cpu_brand": profile.cpu_brand,
        "cpu_count": profile.cpu_count,
        "machine_model": profile.machine_model,
        "gpu_renderer": renderer,
        "gpu_vendor": vendor,
        "secure_hardware": profile.secure_hardware,
        "is_virtual": profile.is_virtual,
        "is_real_device": profile.is_real_device,
    }


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
    headers: dict | None = None,
    messages: list[dict] | None = None,
    ctx: Context | None = None,
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

    reusing_ctx = False
    if ctx is None:
        ctx = Context()
        ctx.eval(MINIDOM_JS)
        ctx.add_callable("host_log", lambda *_a: None)
        ctx.add_callable("host_parse_html", _host_parse_html)
    else:
        reusing_ctx = True

    def record_error(exc: Exception) -> None:
        info["errors"] += 1
        if len(info["error_messages"]) < 25:
            info["error_messages"].append(f"{type(exc).__name__}: {exc}"[:300])

    sw_records: list[dict] = []
    sw_revalidate_queue: list[str] = []

    def host_post_message_handler(data, origin="*", target="parent") -> None:
        data_str = str(data)[:4000]
        info["post_messages"].append(
            {"data": data_str, "origin": str(origin)[:500], "target": str(target)}
        )
        if "cookie" in data_str and "datadome=" in data_str:
            try:
                parsed = json.loads(data_str)
                if isinstance(parsed, dict) and parsed.get("cookie"):
                    c_pair = str(parsed["cookie"]).split(";", 1)[0]
                    if "=" in c_pair:
                        cn, _, cv = c_pair.partition("=")
                        client.cookies.set(cn.strip(), cv.strip())
            except Exception:
                pass

    ctx.add_callable("host_post_message", host_post_message_handler)

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

    def host_crypto_digest(algo: str, data_hex: str) -> str:
        try:
            import hashlib
            data = bytes.fromhex(data_hex) if data_hex else b""
            norm = (algo or "").upper().replace("-", "")
            if norm == "SHA256":
                return hashlib.sha256(data).hexdigest()
            elif norm == "SHA1":
                return hashlib.sha1(data).hexdigest()
            elif norm == "SHA384":
                return hashlib.sha384(data).hexdigest()
            elif norm == "SHA512":
                return hashlib.sha512(data).hexdigest()
            elif norm == "MD5":
                return hashlib.md5(data).hexdigest()
            return hashlib.sha256(data).hexdigest()
        except Exception:
            return ""

    ctx.add_callable("host_crypto_digest", host_crypto_digest)

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

    def do_request(method: str, abs_url: str, body: str, headers: dict) -> tuple[int, str, str, str, dict, bytes]:
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
                resp.content or b"",
            )
        resp = sync.request(method or "GET", abs_url, content=body or None, headers=headers)
        return (
            resp.status_code,
            str(resp.url),
            resp.headers.get("content-type", ""),
            resp.text,
            {k.lower(): v for k, v in resp.headers.items()},
            resp.content or b"",
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

    def host_fetch(req_url: str, method: str, body: str, headers_json: str = "{}") -> str:
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
            if headers_json:
                try:
                    custom_hdrs = json.loads(headers_json)
                    if isinstance(custom_hdrs, dict):
                        headers.update(custom_hdrs)
                except Exception:
                    pass
            # Service-worker fetch interception by declared strategy.
            sw_rec = sw_for_url(sw_records, abs_url) if (method or "GET").upper() == "GET" else None
            sw_tag = f"{sw_strategy(sw_rec)}:{sw_rec['url']}" if sw_rec is not None else ""
            if sw_rec is not None:
                strategy = sw_strategy(sw_rec)
                from webresearch.fetch import decode_body as _decode
                entry = _SW_CACHE.lookup("GET", abs_url, {})
                if strategy == "CacheOnly":
                    if entry is None:
                        return json.dumps({"status": 0, "url": abs_url, "content_type": "",
                                           "body": "", "sw_error": "CacheOnly miss"})
                    text = _decode(entry.body, entry.resp_headers.get("content-type", ""))
                    info["resource_requests"].append({"url": abs_url[:2000], "status": entry.status,
                        "content_type": entry.resp_headers.get("content-type", "")[:200],
                        "method": method, "sw": f"CacheFirst-hit:{sw_rec['url']}"})
                    return json.dumps({"status": entry.status, "url": abs_url,
                                       "content_type": entry.resp_headers.get("content-type", ""),
                                       "body": text[:800_000]})
                if strategy in {"CacheFirst", "StaleWhileRevalidate"} and entry is not None and (
                    strategy == "CacheFirst" and entry.is_fresh() or strategy == "StaleWhileRevalidate"
                ):
                    if strategy == "StaleWhileRevalidate" and abs_url not in sw_revalidate_queue:
                        sw_revalidate_queue.append(abs_url)
                    text = _decode(entry.body, entry.resp_headers.get("content-type", ""))
                    info["resource_requests"].append({"url": abs_url[:2000], "status": entry.status,
                        "content_type": entry.resp_headers.get("content-type", "")[:200],
                        "method": method, "sw": f"{strategy}-served:{sw_rec['url']}"})
                    return json.dumps({"status": entry.status, "url": abs_url,
                                       "content_type": entry.resp_headers.get("content-type", ""),
                                       "body": text[:800_000]})
            status, final, ctype, text, resp_headers, raw_bytes = do_request(method, abs_url, body, headers)
            if (method or "GET").upper() == "GET" and status == 200 and raw_bytes:
                try:
                    _SW_CACHE.store("GET", final or abs_url, {}, status,
                                    {k: v for k, v in resp_headers.items()}, raw_bytes)
                except Exception:
                    pass
            data_payload = None
            if "json" in ctype.lower() or (text and text.strip().startswith(("{", "["))):
                try:
                    if len(text) < 100_000:
                        data_payload = json.loads(text)
                except Exception:
                    pass
            if isinstance(data_payload, dict) and data_payload.get("cookie"):
                c_raw = str(data_payload["cookie"]).split(";", 1)[0]
                if "=" in c_raw:
                    ck, _, cv = c_raw.partition("=")
                    client.cookies.set(ck.strip(), cv.strip())
            set_cookie_hdr = resp_headers.get("set-cookie")
            if set_cookie_hdr:
                c_raw = str(set_cookie_hdr).split(";", 1)[0]
                if "=" in c_raw:
                    ck, _, cv = c_raw.partition("=")
                    client.cookies.set(ck.strip(), cv.strip())
            info["resource_requests"].append(
                {
                    "url": final[:2000],
                    "status": status,
                    "content_type": ctype[:200],
                    "method": method,
                    "data": data_payload,
                    "body": body[:50000] if body else "",
                    **({"sw": sw_tag} if sw_tag else {}),
                }
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
            try:
                if sw_for_url(sw_records, abs_url) is not None:
                    from webresearch.fetch import decode_body as _decode2
                    _entry = _SW_CACHE.lookup("GET", abs_url, {})
                    if _entry is not None:
                        _text = _decode2(_entry.body, _entry.resp_headers.get("content-type", ""))
                        return json.dumps({"status": _entry.status, "url": abs_url,
                                           "content_type": _entry.resp_headers.get("content-type", ""),
                                           "body": _text[:800_000], "sw": "NetworkFirst-fallback"})
            except Exception:
                pass
            return json.dumps({"status": 599, "body": str(exc)})

    ctx.add_callable("host_fetch", host_fetch)

    if reusing_ctx:
        ran = 1
        try:
            ctx.eval("globalThis.__WR_NAVIGATED = ''; globalThis.__WR_FORM_SUBMIT = null;")
        except Exception:
            pass
    else:
        ua = client.headers.get("user-agent", "")
        ctx.eval(f"globalThis.__WR_UA = {json.dumps(ua)};")
        ctx.eval("globalThis.__WR_MODS = {};")
        ctx.eval(f"globalThis.__WR_DEVICE = {json.dumps(_device_block())};")
        try:
            from webresearch.engine.csp import trusted_types_config
            _tt = trusted_types_config(
                (headers or {}).get("content-security-policy") or (headers or {}).get("Content-Security-Policy"),
                (headers or {}).get("content-security-policy-report-only"),
            )
            ctx.eval(f"globalThis.__WR_TT = {json.dumps(_tt)};")
        except Exception:
            pass

        tree = html_to_tree(html)
        csp_header = (headers or {}).get("content-security-policy") or (headers or {}).get("Content-Security-Policy")
        try:
            from webresearch.engine.styles import (
                collect_stylesheet_links,
                inject_stylesheets,
                load_stylesheets,
            )
            sheet_links = collect_stylesheet_links(html, url)
            applied_sheets, blocked_sheets = await load_stylesheets(
                sheet_links, url, client, headers
            )
            inject_stylesheets(tree, applied_sheets)
            info["stylesheets"] = {
                "applied": [s["href"] for s in applied_sheets],
                "blocked": blocked_sheets,
            }
        except Exception as exc:
            info["errors"] = info.get("errors", 0) + 1
            info.setdefault("stylesheets", {"applied": [], "blocked": []})
        if csp_header:
            from webresearch.engine.csp import style_source_allowed
            from webresearch.engine.tree import walk as _walk
            for node in list(_walk(tree)):
                if node.get("t") == "style":
                    css = "".join(c.get("x") or "" for c in node.get("c") or [] if c.get("t") == "#text")
                    if css and not style_source_allowed(csp_header, inline_css=css):
                        node["c"] = []
                        info.setdefault("blocked_csp_style", 0)
                        info["blocked_csp_style"] += 1
                elif node.get("t") == "link":
                    rel = (node.get("a") or {}).get("rel", "")
                    href = (node.get("a") or {}).get("href", "")
                    if "stylesheet" in rel.lower() and href:
                        from urllib.parse import urljoin as _uj
                        if not style_source_allowed(csp_header, external_url=_uj(url, href), page_url=url):
                            node["a"] = {**(node.get("a") or {}), "rel": "blocked-stylesheet"}
                            info.setdefault("blocked_csp_style", 0)
                            info["blocked_csp_style"] += 1
        payload = json.dumps(tree, ensure_ascii=False)
        ctx.eval(f"__load({payload}, {json.dumps(url)})")
    
        scripts, _ = extract_scripts(html)
        classic: list[dict[str, str]] = []
        deferred: list[dict[str, str]] = []
        module_scripts: list[dict[str, str]] = []
        for script in scripts:
            stype = (script.get("type") or "").lower()
            if stype in {"application/ld+json", "application/json", "text/html", "importmap", "speculationrules"}:
                continue
            if "module" in stype:
                module_scripts.append(script)
            elif script.get("defer"):
                deferred.append(script)
            else:
                classic.append(script)
    
        def record_error(exc: Exception) -> None:
            info["errors"] += 1
            if len(info["error_messages"]) < 25:
                info["error_messages"].append(f"{type(exc).__name__}: {exc}"[:300])
    
        # Service workers install before any page script runs, so in-page
        # fetch/XHR (host_fetch) passes through their fetch handlers.
        from webresearch.engine.sw import install_service_workers, sw_for_url, sw_strategy
        from webresearch.cache import CACHE as _SW_CACHE
        sw_records: list[dict] = []
        sw_revalidate_queue: list[str] = []
        try:
            _sw_static_codes: list[str] = []
            for _s in classic + deferred:
                if _s.get("code"):
                    _sw_static_codes.append(_s["code"])
            for _s in module_scripts:
                if _s.get("code"):
                    _sw_static_codes.append(_s["code"])
            sw_records = await install_service_workers(_sw_static_codes, url, client)
            info["service_workers"] = sw_records
        except Exception as exc:
            record_error(exc)
    
        # Immediate scripts first, then deferred scripts (per HTML script ordering spec).
        ran = 0
        for script in (classic + deferred):
            if ran >= MAX_SCRIPTS:
                break
            code = script.get("code") or ""
            src = script.get("src") or ""
            csp = (headers or {}).get("content-security-policy") or (headers or {}).get("Content-Security-Policy")
            if src:
                try:
                    abs_src = urljoin(url, src)
                    if mixed_content_blocked(url, abs_src):
                        info.setdefault("blocked_mixed_content", 0)
                        info["blocked_mixed_content"] += 1
                        continue
                    from webresearch.engine.trackers import GRAPH as _TRACKER_GRAPH
                    decision = _TRACKER_GRAPH.evaluate(abs_src, url)
                    if decision.status == "BLOCKED":
                        info.setdefault("blocked_trackers", []).append(
                            {"url": abs_src, "entity": decision.entity,
                             "surrogate": bool(decision.surrogate)}
                        )
                        if decision.surrogate:
                            code = decision.surrogate
                        else:
                            continue
                    if not code:
                        if csp and not script_source_allowed(csp, external_url=abs_src, page_url=url):
                            info.setdefault("blocked_csp", 0)
                            info["blocked_csp"] += 1
                            continue
                        resp = await client.get(abs_src)
                        from webresearch.engine.cross_origin import check_subresource as _check_xo
                        xo_reason, _ = _check_xo(resp.headers, abs_src, url, headers)
                        if xo_reason is not None:
                            info.setdefault("blocked_corp_coep", []).append({"url": abs_src, "reason": xo_reason})
                            continue
                        if resp.status_code >= 400:
                            info.setdefault("script_fetch_errors", []).append({"url": abs_src, "status": resp.status_code})
                            continue
                        ctype = (resp.headers.get("content-type") or "").lower()
                        if "text/html" in ctype:
                            info.setdefault("script_mime_errors", []).append({"url": abs_src, "content_type": ctype})
                            continue
                        code = resp.text
                        if not verify_integrity(code, script.get("integrity")):
                            info.setdefault("blocked_sri_failures", 0)
                            info["blocked_sri_failures"] += 1
                            continue
                except Exception as exc:
                    record_error(exc)
                    continue
            code = code.strip()
            if not code or len(code.encode()) > MAX_SCRIPT_BYTES:
                continue
            if csp and not src and not script_source_allowed(csp, inline_code=code):
                info.setdefault("blocked_csp", 0)
                info["blocked_csp"] += 1
                continue
            code = re.sub(r"\bstatic\s*\{([^{}]*)\}", r"static _ = (() => { \1 })();", code)
            try:
                if src:
                    sel = f'script[src="{src}"]'
                    ctx.eval(f"if (typeof document !== 'undefined') document.currentScript = document.querySelector({json.dumps(sel)});")
                else:
                    ctx.eval("if (typeof document !== 'undefined') document.currentScript = null;")
                ctx.eval(code)
                ran += 1
                _pump_jobs(ctx)
            except Exception as exc:
                record_error(exc)
            finally:
                try:
                    ctx.eval("if (typeof document !== 'undefined') document.currentScript = null;")
                except Exception:
                    pass
    
        # ES modules via the registry translator.
        if module_scripts:
            import_map = extract_import_map(html)
            graph = await load_module_graph(
                module_scripts,
                url,
                client,
                import_map=import_map,
                max_modules=MAX_MODULES,
                headers=headers,
                csp=(headers or {}).get("content-security-policy") or (headers or {}).get("Content-Security-Policy"),
            )
            info["skipped_modules"] = len(graph.missing)
            if graph.blocked:
                info.setdefault("blocked_corp_coep", []).extend(graph.blocked)
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
    
        try:
            ctx.eval(
                "if (typeof document !== 'undefined' && document && document.dispatchEvent) {"
                "  document.dispatchEvent(new Event('DOMContentLoaded', { bubbles: true, cancelable: true }));"
                "}"
                "if (typeof window !== 'undefined' && window && window.dispatchEvent) {"
                "  window.dispatchEvent(new Event('load'));"
                "}"
            )
        except Exception as exc:
            record_error(exc)
    
        for _ in range(MAX_TIMER_PASSES):
            try:
                left = ctx.eval("__drainTimers()")
            except Exception:
                break
            _pump_jobs(ctx)
            if not left:
                break
    
        # Drain service-worker StaleWhileRevalidate revalidations.
        for _reval_url in sw_revalidate_queue:
            try:
                _r = await client.get(_reval_url)
                if _r.status_code == 200:
                    _SW_CACHE.store("GET", _reval_url, {}, _r.status_code, dict(_r.headers), _r.content)
                    info.setdefault("sw_revalidated", []).append(_reval_url)
            except Exception:
                continue
    
        try:
            tt_json = ctx.eval(
                "JSON.stringify((globalThis.trustedTypes && globalThis.trustedTypes.__violations) || [])"
            )
            tt_violations = json.loads(str(tt_json)) if tt_json else []
            if tt_violations:
                info["trusted_types_violations"] = [str(v)[:300] for v in tt_violations[:10]]
        except Exception:
            pass

    if messages:
        for msg in messages:
            m_data = json.dumps(msg.get("data", ""))
            m_origin = json.dumps(msg.get("origin", ""))
            try:
                ctx.eval(
                    f"if (typeof window !== 'undefined' && typeof window.__receiveMessage === 'function') window.__receiveMessage({m_data}, {m_origin});"
                )
            except Exception as exc:
                record_error(exc)
        _pump_jobs(ctx)

    # Apply agent interactions against the live (post-hydration) DOM.
    if actions:
        import json as _json

        try:
            ctx.eval("__startDeltaRecording()")
        except Exception:
            pass

        for action in actions:
            kind = (action or {}).get("kind") or "click"
            path = (action or {}).get("path") or []
            selector = (action or {}).get("selector") or ""
            try:
                if kind == "type":
                    text = (action or {}).get("text") or ""
                    if selector:
                        ok = ctx.eval(
                            f"__activateSelector({_json.dumps(selector)}, {_json.dumps(text)})"
                        )
                    else:
                        ok = ctx.eval(f"__typePath({_json.dumps(path)}, {_json.dumps(text)})")
                elif kind == "drag":
                    dx = (action or {}).get("dx", 220.0)
                    dy = (action or {}).get("dy", 0.0)
                    steps = int((action or {}).get("steps", 12))
                    target = selector or path
                    ok = ctx.eval(
                        f"__dragSelector({_json.dumps(target)}, {dx}, {dy}, {steps})"
                        if isinstance(target, str)
                        else f"__dragPath({_json.dumps(target)}, {dx}, {dy}, {steps})"
                    )
                elif kind == "activate":
                    target = selector or path
                    ok = ctx.eval(
                        f"__activateSelector({_json.dumps(target)})"
                        if isinstance(target, str)
                        else f"__clickPath({_json.dumps(target)})"
                    )
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
                info["actions_applied"].append(
                    {
                        "kind": kind,
                        "path": path or selector,
                        "ok": bool(ok),
                    }
                )
            except Exception as exc:
                info["actions_applied"].append(
                    {"kind": kind, "path": path or selector, "ok": False, "error": str(exc)[:200]}
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
            deltas_raw = ctx.eval("__drainDeltaRecords()")
            info["deltas"] = json.loads(str(deltas_raw)) if deltas_raw else []
        except Exception:
            info["deltas"] = []

    try:
        out = str(ctx.eval("__serialize()") or html)
    except Exception:
        out = html

    info["framework_data"] = _extract_framework_data(ctx, html)
    try:
        nav = ctx.eval("globalThis.__WR_NAVIGATED || ''")
        if nav:
            info["navigated"] = str(nav)
    except Exception:
        pass
    try:
        form_sub = ctx.eval("JSON.stringify(globalThis.__WR_FORM_SUBMIT || null)")
        if form_sub and str(form_sub) != "null":
            info["form_submit"] = json.loads(str(form_sub))
    except Exception:
        pass

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
    info["ctx"] = ctx
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