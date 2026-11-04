"""QuickJS page runtime. Own DOM, not Chromium."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from quickjs import Context
from webresearch.config import js_engine


def _create_context():
    engine = js_engine()
    if engine == "doshgentv8":
        try:
            from doshgentv8 import DoshContext
            return DoshContext()
        except ImportError:
            return Context()
    return Context()

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
# Upload payloads are embedded into a JS eval call; bound them so a large file
# cannot exhaust QuickJS or balloon the eval string. 16M b64 chars ≈ 12 MB raw.
MAX_UPLOAD_B64_CHARS = 16_000_000


def _wants_js(html: str) -> bool:
    low = html.lower()
    if "<script" not in low:
        return False
    # Any inline or src script can register listeners, fetch data, or mutate
    # the document — a one-script page is still a live page. The old heuristic
    # (>=2 script tags or a framework marker) skipped single-script pages, so
    # goto() never built the engine ctx: sitework (infinite scroll, JS fetch,
    # event handlers) only materialised after the first forced action, at the
    # wrong scroll position. Any page with a script gets its scripts run.
    return True


def _extract_origin(url: str) -> str:
    try:
        p = urlparse(url)
        scheme = p.scheme.lower()
        if not scheme or not p.hostname:
            return ""
        host = p.hostname.lower()
        port = p.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        if port:
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{host}"
    except Exception:
        return ""


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


def _pump_websockets(ctx: Context) -> None:
    """Deliver any WebSocket frames the host sockets have received."""
    try:
        from webresearch.engine import ws as _ws

        raw = _ws.poll_all()
        payload = json.loads(str(raw))
        for msg in payload.get("messages", []):
            ctx.eval(
                f"if (typeof globalThis.__wsDeliver === 'function') "
                f"globalThis.__wsDeliver({int(msg['id'])}, {json.dumps(str(msg['data']))});"
            )
        for fin in payload.get("closed", []):
            ctx.eval(
                f"if (typeof globalThis.__wsClosed === 'function') "
                f"globalThis.__wsClosed({int(fin['id'])}, {int(fin.get('code') or 1006)}, {json.dumps(str(fin.get('reason') or ''))});"
            )
    except Exception:
        pass


def _drain_injected_scripts(ctx, url, client, info, curl_sync, sync, record_error):
    """Run any script elements that were injected at runtime (createElement + appendChild).

    Many SPA frameworks (React, Vue, Angular) inject chunks dynamically. The
    static extract_scripts can never see them, so we drain this queue after
    every pump pass to give those scripts a chance to execute.
    """
    import json as _json
    try:
        raw = ctx.eval(
            "JSON.stringify(globalThis.__WR_INJECTED_SCRIPTS || [])"
        )
        scripts = _json.loads(str(raw)) if raw and str(raw) != "null" else []
    except Exception:
        scripts = []

    for script in scripts[:40]:  # bound in-flight
        src = str(script.get("src") or "").strip()
        text = str(script.get("text") or "").strip()
        stype = str(script.get("type") or "").lower()
        if not src and not text:
            continue
        try:
            if src:
                abs_src = urljoin(url, src)
                from webresearch.fetch import _curl_requests
                if curl_sync is not None and _curl_requests is not None:
                    resp = curl_sync.get(abs_src, headers={"Referer": url})
                    code = resp.text if resp.status_code < 400 else ""
                else:
                    resp = sync.get(abs_src, headers={"Referer": url})
                    code = resp.text if resp.status_code < 400 else ""
                if code:
                    if "module" in stype or "export " in code or "import " in code:
                        from webresearch.engine.modules import transform_module
                        try:
                            # Empty import map: relative specifiers resolve via
                            # urljoin in _resolve. The old call passed a LIST
                            # as the map — import_map.items() threw, injected
                            # module chunks never transformed, and como's
                            # chunk-loaded React crashed with "not a function".
                            code = transform_module(code, abs_src, {})
                            ctx.eval(code)
                            info["modules"] = info.get("modules", 0) + 1
                            info["scripts"] = info.get("scripts", 0) + 1
                        except Exception as exc:
                            record_error(exc)
                    else:
                        ctx.eval(code)
                        info["scripts"] = info.get("scripts", 0) + 1
            elif text:
                if "module" in stype or "export " in text or "import " in text:
                    from webresearch.engine.modules import transform_module
                    try:
                        code = transform_module(text, url, {})
                        ctx.eval(code)
                        info["modules"] = info.get("modules", 0) + 1
                        info["scripts"] = info.get("scripts", 0) + 1
                    except Exception as exc:
                        record_error(exc)
                else:
                    ctx.eval(text)
                    info["scripts"] = info.get("scripts", 0) + 1
        except Exception as exc:
            record_error(exc)

    try:
        ctx.eval("globalThis.__WR_INJECTED_SCRIPTS = [];")
    except Exception:
        pass


def _pump_jobs(ctx: Context) -> int:
    done = 0
    for _ in range(MAX_JOBS):
        try:
            if not ctx.execute_pending_job():
                break
        except Exception:
            break
        done += 1
    _pump_websockets(ctx)
    return done


# Per-context postMessage queue, keyed by id(ctx). A real browser's queue
# belongs to the window, not to whichever script happened to run; the runtime
# mirror survives across run_scripts passes. Entries are the SAME dicts that
# run_scripts records in its info["post_messages"]; a pass removes the ones
# it produced, so what remains is traffic from DIRECT evals (OTP fills,
# history ops, probes) that used to vanish into a stale info dict.
_CTX_POST_INBOX: dict[int, list[dict]] = {}
# Monotonic postMessage ids, per context. Uid-based dedup in the routing
# layers needs ids unique across ALL passes on a context, not per pass.
_CTX_POST_UID: dict[int, int] = {}


def _post_queue_key(ctx) -> str:
    """Stable per-CONTEXT key for the durable postMessage queues.

    The context nonce (uuid4, injected once per JS context, kept across
    passes) — NOT id(ctx): CPython reuses id()s after GC, and this process
    creates hundreds of short-lived contexts. An id()-keyed durable queue
    lets a fresh context inherit a dead context's pending posts (ghost
    traffic delivered into a realm that never produced it) — the same
    address-reuse bug class as the _ctx_nonce cache removed from frames.py.
    """
    try:
        nonce = str(ctx.eval("String(globalThis.__WR_CTX_KEY || '')") or "")
    except Exception:
        nonce = ""
    return nonce if nonce else f"id:{id(ctx)}"


def take_pending_posts(ctx) -> list[dict]:
    """Take the postMessages this context produced outside any run_scripts
    pass. Callers own routing them; entries leave the queue exactly once."""
    inbox = _CTX_POST_INBOX.get(_post_queue_key(ctx))
    if not inbox:
        return []
    taken = inbox[:]
    # Mutate in place: the host bridge closure holds this exact list.
    inbox.clear()
    return taken


def _sync_resource_entries(ctx: Context, info: dict) -> None:
    """Push one performance resource-timing entry per subresource this pass
    actually fetched (scripts, in-page fetch/XHR, SW-served): real URLs and
    real byte sizes. Timing values stay 0 — no fabricated timing data.
    Real stats collectors reduce() over getEntriesByType('resource') without
    an initial value; an always-empty timeline made that reduce undefined."""
    try:
        entries = [
            {
                "url": r.get("url", ""),
                "transferSize": int(r.get("bytes") or len(r.get("body") or "")),
                "initiatorType": r.get("initiatorType") or "fetch",
            }
            for r in info.get("resource_requests", [])
            if r.get("url")
        ]
        ctx.eval(f"performance.__setResourceEntries({json.dumps(entries)}); true")
    except Exception:
        pass


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
    storage_seed: dict | None = None,
    history_length: int = 1,
    frames: list | None = None,
    frame_contexts: dict | None = None,
) -> tuple[str, dict]:
    """Execute page scripts against mini-DOM. Returns (html, info).
    
    Args:
        frames: Optional list of FrameDocument objects whose content will be
                injected into corresponding iframe elements.
        frame_contexts: Live QuickJS contexts for already-loaded frames, keyed
                by frame ref. A click_frame action runs in that frame's
                document, the one that executed the frame's scripts.
    """
    info: dict = {
        "ran": False,
        "scripts": 0,
        "modules": 0,
        "skipped_modules": 0,
        "errors": 0,
        "error_messages": [],
        "console": [],
        "dosh_emits": [],
        "engine": js_engine(),
        "post_messages": [],
        "resource_requests": [],
        "framework_data": {},
        "actions_applied": [],
    }
    if not force and not _wants_js(html):
        return html, info

    reusing_ctx = False
    if ctx is None:
        ctx = _create_context()
        active_engine = js_engine()
        try:
            ctx.eval(f"globalThis.__WR_ENGINE = {json.dumps(active_engine)}; true")
        except Exception:
            pass
        # Web storage seed must be set before MINIDOM_JS evaluates: the
        # storage closures read __WR_STORAGE_SEED at definition time. (json is
        # the module-level import; _json is function-local elsewhere.)
        if storage_seed:
            try:
                ctx.eval(f"globalThis.__WR_STORAGE_SEED = {json.dumps(storage_seed)}; true")
                if isinstance(storage_seed.get("idb"), dict) and storage_seed["idb"]:
                    ctx.eval(f"globalThis.__WR_IDB_SEED = {json.dumps(storage_seed['idb'])}; true")
            except Exception:
                pass
        if history_length > 1:
            try:
                ctx.eval(f"globalThis.__WR_HISTORY_LENGTH = {int(history_length)}; true")
            except Exception:
                pass
        ctx.eval(MINIDOM_JS)
        ctx.add_callable("host_parse_html", _host_parse_html)
    else:
        reusing_ctx = True

    def _host_log(msg: str = "", level: str = "log", *_extra) -> None:
        if len(info["console"]) < 500:
            info["console"].append(
                {
                    "level": str(level or "log"),
                    "message": str(msg or "")[:4000],
                    "url": url,
                }
            )

    ctx.add_callable("host_log", _host_log)
    # This context's host callable accepts the log LEVEL as a second
    # argument. Raw contexts (tests, embedders) may register a 1-arg
    # host_log; QuickJS raises a deferred SystemError on the extra
    # argument that no JS try/catch can intercept, so the JS side must
    # only pass the level when the host declared it can receive it.
    try:
        ctx.eval("globalThis.__WR_HOST_LOG_TAKES_LEVEL = true")
    except Exception:
        pass

    # WebSocket host bridge: own-transport RFC 6455 sockets in engine/ws.py.
    from webresearch.engine import ws as _ws

    ctx.add_callable("host_ws_connect", _ws.connect)
    ctx.add_callable("host_ws_send", _ws.send)
    ctx.add_callable("host_ws_send_binary", _ws.send_binary)
    ctx.add_callable("host_ws_close", _ws.close)

    def record_error(exc: Exception, source: str = "") -> None:
        info["errors"] += 1
        if len(info["error_messages"]) < 25:
            msg = f"{type(exc).__name__}: {exc}"[:300]
            if source:
                msg = f"{msg} (in {source[:160]})"
            info["error_messages"].append(msg)

    sw_records: list[dict] = []
    sw_revalidate_queue: list[str] = []

    ctx_nonce_holder = {"v": ""}
    # Per-context postMessage queue. A real browser's postMessage queue
    # belongs to the WINDOW, not to whichever script happened to run; this
    # engine's transport used to record posts into the info dict of whatever
    # run_scripts call registered the bridge — so posts made during any
    # DIRECT eval (OTP fills, history ops, probes) landed in a stale dict and
    # vanished. The queue now lives on the context: run_scripts still sees it
    # through its own info dict, and drain_pending_posts() hands the rest to
    # the routing layers.
    _this_inbox = _CTX_POST_INBOX.setdefault(_post_queue_key(ctx), [])

    def host_post_message_handler(data, origin="*", target="parent", sender_origin="", ports_json="") -> None:
        # Binary channel payloads (typed arrays) hex-encode inside the clone
        # envelope. 16KB cut those envelopes in half and the far realm
        # received a truncated string it could not decode.
        data_str = str(data)[:262144]
        s_orig = str(sender_origin).strip() if sender_origin else _extract_origin(url)
        # Monotonic per-CONTEXT id (not per-call: restarts made entries from
        # different passes collide, and uid-based dedup dropped live traffic).
        _qkey = _post_queue_key(ctx)
        _CTX_POST_UID[_qkey] = _CTX_POST_UID.get(_qkey, 0) + 1
        entry = {
            "uid": _CTX_POST_UID[_qkey],
            "data": data_str,
            "origin": s_orig,
            "target_origin": str(origin)[:500],
            "target": str(target),
            # Sender context nonce (one per QuickJS context). Cross-realm port
            # transfers key off it: it identifies which realm owns an endpoint.
            "ctx": ctx_nonce_holder["v"],
        }
        if ports_json:
            try:
                metas = json.loads(str(ports_json))
                if isinstance(metas, list) and metas:
                    entry["ports"] = metas
            except Exception:
                pass
        _this_inbox.append(entry)
        info["post_messages"].append(entry)
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

    # Dynamic import() bridge: __wr_import (minidom) calls this to load
    # lazily-discovered ES modules (como/webpack chunks). Resolves the
    # specifier (absolute URL, CDN-rooted path, or page-relative), fetches,
    # transforms, and returns {url, code} — the JS side evals the code, which
    # registers the module into __mods synchronously.
    _dyn_loaded: dict[str, str] = {}

    def host_load_module(specifier: str) -> str:
        import json as _json

        try:
            spec = str(specifier or "").strip()
            candidates: list[str] = []
            if spec.startswith(("http://", "https://")):
                candidates = [spec]
            elif spec.startswith("/"):
                candidates = [
                    f"https://static.licdn.com{spec}",
                    urljoin(url, spec),
                ]
            else:
                candidates = [
                    urljoin(url, spec),
                    f"https://static.licdn.com/aero-v1/sc/h/assets/{spec.lstrip('./')}",
                ]
            for abs_url in candidates:
                if not abs_url.startswith(("http://", "https://")):
                    continue
                if abs_url in _dyn_loaded:
                    return _json.dumps({"url": abs_url, "code": ""})
                try:
                    if curl_sync is not None:
                        resp = curl_sync.get(abs_url, headers={"Referer": url})
                    else:
                        resp = sync.get(abs_url, headers={"Referer": url})
                    if resp.status_code >= 400:
                        continue
                    raw = resp.text
                except Exception:
                    continue
                if not raw or not raw.strip():
                    continue
                from webresearch.engine.modules import transform_module

                try:
                    transformed = transform_module(raw, abs_url, {})
                except Exception as exc:
                    record_error(exc)
                    continue
                _dyn_loaded[abs_url] = abs_url
                info["modules"] = info.get("modules", 0) + 1
                return _json.dumps({"url": abs_url, "code": transformed})
            return _json.dumps({"url": "", "code": ""})
        except Exception:
            return _json.dumps({"url": "", "code": ""})

    ctx.add_callable("host_load_module", host_load_module)

    # (await_client_get was a placeholder removed in favor of sync fetching)

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

    def _drain_injected(ctx):
        """Convenience: drain injected scripts with the closure's context."""
        _drain_injected_scripts(ctx, url, client, info, curl_sync, sync, record_error)

    def do_request(method: str, abs_url: str, body: str, headers: dict, body_b64: str = "") -> tuple[int, str, str, str, dict, bytes]:
        # Binary request bodies (multipart uploads with real file bytes) arrive
        # base64-encoded so arbitrary bytes survive the JS->Python boundary.
        content = base64.b64decode(body_b64) if body_b64 else (body or None)
        if curl_sync is not None:
            resp = curl_sync.request(
                method or "GET", abs_url, data=content, headers=headers,
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
        resp = sync.request(method or "GET", abs_url, content=content, headers=headers)
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

    def host_fetch(req_url: str, method: str, body: str, headers_json: str = "{}", body_b64: str = "") -> str:
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
            # Sec-Fetch: deterministic, computed from the request-page origin
            # relationship — the same way a real Chrome populates them.
            # LinkedIn and other sites score these for risk assessment.
            if target_origin == page_origin:
                headers["Sec-Fetch-Site"] = "same-origin"
            elif page_origin and not target_origin:
                headers["Sec-Fetch-Site"] = "none"
            elif page_origin and target_origin != page_origin:
                headers["Sec-Fetch-Site"] = "cross-site"
            else:
                headers["Sec-Fetch-Site"] = "none"
            headers["Sec-Fetch-Mode"] = "cors" if page_origin and target_origin != page_origin else "no-cors"
            headers["Sec-Fetch-Dest"] = "empty"
            if headers_json:
                try:
                    custom_hdrs = json.loads(headers_json)
                    if isinstance(custom_hdrs, dict):
                        headers.update(custom_hdrs)
                except Exception:
                    pass
            # Selective network rules (block / header / mock) configured by DOSH shell.
            net_rules = getattr(client, "_dosh_network_rules", None) or []
            req_method = (method or "GET").upper()
            for rule in net_rules:
                if not isinstance(rule, dict) or not rule.get("enabled", True):
                    continue
                pat = str(rule.get("pattern") or "")
                rule_method = str(rule.get("method") or "*").upper()
                if rule_method not in {"*", "ANY", req_method}:
                    continue
                if pat and pat not in abs_url and not re.search(pat, abs_url):
                    continue
                r_action = str(rule.get("action") or "").lower()
                if r_action == "block":
                    info["resource_requests"].append(
                        {
                            "url": abs_url[:2000],
                            "status": 0,
                            "content_type": "blocked-by-rule",
                            "method": req_method,
                            "request_headers": dict(headers),
                            "response_headers": {},
                            "request_body": body[:20000] if body else "",
                            "body": "",
                            "bytes": 0,
                            "initiatorType": "fetch",
                            "disposition": f"blocked:{rule.get('id', pat)}",
                        }
                    )
                    return json.dumps(
                        {
                            "status": 0,
                            "url": abs_url,
                            "content_type": "",
                            "body": "",
                            "blocked_by_rule": rule.get("id", pat),
                        }
                    )
                if r_action == "header":
                    extra_h = rule.get("headers") or {}
                    if isinstance(extra_h, dict):
                        for hk, hv in extra_h.items():
                            headers[str(hk)] = str(hv)
                if r_action == "mock":
                    m_status = int(rule.get("status", 200))
                    m_ctype = str(rule.get("content_type") or "application/json")
                    m_body = str(rule.get("body") or "")
                    m_data = None
                    if "json" in m_ctype.lower() or m_body.strip().startswith(("{", "[")):
                        try:
                            m_data = json.loads(m_body)
                        except Exception:
                            pass
                    info["resource_requests"].append(
                        {
                            "url": abs_url[:2000],
                            "status": m_status,
                            "content_type": m_ctype[:200],
                            "method": req_method,
                            "request_headers": dict(headers),
                            "response_headers": {"content-type": m_ctype, "x-dosh-mock": "1"},
                            "request_body": body[:20000] if body else "",
                            "data": m_data,
                            "body": m_body[:50000],
                            "bytes": len(m_body),
                            "initiatorType": "fetch",
                            "disposition": f"mocked:{rule.get('id', pat)}",
                        }
                    )
                    return json.dumps(
                        {
                            "status": m_status,
                            "url": abs_url,
                            "content_type": m_ctype,
                            "body": m_body[:800_000],
                        }
                    )
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
            status, final, ctype, text, resp_headers, raw_bytes = do_request(method, abs_url, body, headers, body_b64)
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
                    "request_headers": dict(headers),
                    "response_headers": dict(resp_headers),
                    "request_body": body[:20000] if body else "",
                    "response_body": text[:50000] if text else "",
                    "data": data_payload,
                    "body": body[:50000] if body else "",
                    "bytes": len(raw_bytes) if raw_bytes else len(text or body or ""),
                    "initiatorType": "fetch",
                    "disposition": f"sw:{sw_tag}" if sw_tag else "network",
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
            ctx.eval(
                "globalThis.__WR_NAVIGATED = ''; globalThis.__WR_FORM_SUBMIT = null;"
                "if (typeof globalThis.__clearDialogState === 'function') globalThis.__clearDialogState();"
                "globalThis.__WR_POPUPS = [];"
                "globalThis.__WR_PUSHED = null; globalThis.__WR_REPLACED = null; globalThis.__WR_HISTORY_MOVE = 0;"
            )
        except Exception:
            pass
        # A reused context keeps its nonce: transferred port endpoints in
        # flight must stay addressable across action passes.
        try:
            ctx_nonce_holder["v"] = str(ctx.eval("String(globalThis.__WR_CTX_KEY || '')") or "")
        except Exception:
            pass
    else:
        ua = client.headers.get("user-agent", "")
        ctx.eval(f"globalThis.__WR_UA = {json.dumps(ua)};")
        ctx.eval("globalThis.__WR_MODS = {};")
        ctx.eval(f"globalThis.__WR_DEVICE = {json.dumps(_device_block())};")
        # One nonce per JavaScript context. Cross-realm MessagePort transfers
        # address endpoints as "<nonce>:<localId>"; the port tables are
        # realm-local, so the nonce qualifies them globally.
        import uuid as _uuid
        _nonce = _uuid.uuid4().hex
        ctx_nonce_holder["v"] = _nonce
        ctx.eval(f"globalThis.__WR_CTX_KEY = {json.dumps(_nonce)};")
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
    
        # Inject frame content into iframe elements
        if frames:
            try:
                for frame in frames:
                    if hasattr(frame, 'ref') and hasattr(frame, 'html') and frame.html:
                        # Escape the HTML for JS
                        frame_html_escaped = json.dumps(frame.html)
                        # Try to inject by ref attribute
                        ctx.eval(f"""(function() {{
                            var iframes = document.querySelectorAll('iframe');
                            for (var i = 0; i < iframes.length; i++) {{
                                var ref = iframes[i].getAttribute('data-ref') || iframes[i].getAttribute('ref') || iframes[i].name;
                                if (ref === {json.dumps(frame.ref)}) {{
                                    if (typeof iframes[i].__injectFrameContent === 'function') {{
                                        iframes[i].__injectFrameContent({frame_html_escaped});
                                    }}
                                    break;
                                }}
                            }}
                        }})()""")
            except Exception as exc:
                info["errors"] += 1
                info["error_messages"].append(f"Frame injection failed: {exc}"[:200])
    
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
    
        def record_error(exc: Exception, source: str = "") -> None:
            info["errors"] += 1
            if len(info["error_messages"]) < 25:
                msg = f"{type(exc).__name__}: {exc}"[:300]
                if source:
                    msg = f"{msg} (in {source[:160]})"
                info["error_messages"].append(msg)
    
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
                    # An external <script src> whose element body is only
                    # whitespace (very common: the tag and </script> on
                    # separate lines) must still be fetched. Testing the raw
                    # string treated "\n      \n    " as inline code and
                    # silently skipped the real bundle.
                    if not code.strip():
                        code = ""
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
                        # Real subresource record for the performance
                        # resource-timing entries (and the network log).
                        info["resource_requests"].append(
                            {"url": abs_src[:2000], "status": resp.status_code,
                             "content_type": ctype[:200], "method": "GET",
                             "body": "", "bytes": len(code), "initiatorType": "script"}
                        )
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
            if csp and not src and not script_source_allowed(
                csp, inline_code=code, nonce=script.get("nonce") or ""
            ):
                info.setdefault("blocked_csp", 0)
                info["blocked_csp"] += 1
                continue
            code = re.sub(r"\bstatic\s*\{([^{}]*)\}", r"static _ = (() => { \1 })();", code)
            # Dynamic import(): classic scripts may call import(url) (como's
            # webpack chunk loader does). QuickJS's loader rejects every URL,
            # so rewrite to our loader — same as the module transform does.
            if "import(" in code:
                from webresearch.engine.modules import _sub_code as _sub_rewrite

                code = _sub_rewrite(code, r"\bimport\s*\(", lambda m, o: "__wr_import(")
            try:
                if src:
                    sel = f'script[src="{src}"]'
                    ctx.eval(f"if (typeof document !== 'undefined') document.currentScript = document.querySelector({json.dumps(sel)});")
                else:
                    ctx.eval("if (typeof document !== 'undefined') document.currentScript = null;")
                ctx.eval(code)
                ran += 1
                _pump_jobs(ctx)
                _drain_injected(ctx)
            except Exception as exc:
                record_error(exc, source=src or f"inline script #{ran + 1}")
            finally:
                try:
                    ctx.eval("if (typeof document !== 'undefined') document.currentScript = null;")
                except Exception:
                    pass
    
        # ES modules via the registry translator.
        if module_scripts:
            # Modules' own stats collectors read the resource timeline; give
            # them the real entries collected so far (classic scripts).
            _sync_resource_entries(ctx, info)
            from webresearch.engine.modules import resolve_import_map as _resolve_map

            try:
                import_map = await _resolve_map(html, url, client)
            except Exception:
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
            # Module fetches are real subresources: record them on the
            # resource timeline (real URL + byte size) and sync before the
            # first module executes — module code that reduce()s over
            # getEntriesByType('resource') needs a non-empty, honest timeline.
            for _m_item in graph.order:
                if getattr(_m_item, "url", "") and getattr(_m_item, "code", ""):
                    info["resource_requests"].append(
                        {"url": str(_m_item.url)[:2000], "status": 200,
                         "content_type": "text/javascript", "method": "GET",
                         "body": "", "bytes": len(_m_item.code), "initiatorType": "script"}
                    )
            _sync_resource_entries(ctx, info)
            # Circular imports: registry emulation cannot give live bindings, so a
            # module whose cyclic dependency has not run yet snapshots undefined
            # and throws. Real ESM resolves this with hoisted bindings; the
            # honest approximation here is a retry pass — modules that failed
            # re-execute once their dependencies have all had a chance to load.
            # (Verified on LinkedIn's graph: Bc3GGTrS ↔ CldM-cQ- cycle.)
            failed: list = []
            for item in graph.order:
                module_id = item.url or f"inline:{ran}"
                try:
                    code = transform_module(item.code, module_id, item.imports)
                    ctx.eval(code)
                    info["modules"] += 1
                    ran += 1
                    _pump_jobs(ctx)
                    _drain_injected(ctx)
                except Exception:
                    failed.append((item, module_id))
                if ran >= MAX_SCRIPTS + MAX_MODULES:
                    break
            for _retry_pass in range(2):
                if not failed:
                    break
                still_failed: list = []
                for item, module_id in failed:
                    try:
                        code = transform_module(item.code, module_id, item.imports)
                        ctx.eval(code)
                        info["modules"] += 1
                        _pump_jobs(ctx)
                        _drain_injected(ctx)
                    except Exception:
                        still_failed.append((item, module_id))
                failed = still_failed
            # Errors are recorded only for the final still-failing set, so the
            # count reflects the end state after cycle retries.
            for item, module_id in failed:
                try:
                    code = transform_module(item.code, module_id, item.imports)
                    ctx.eval(code)
                except Exception as exc:
                    record_error(exc, source=f"module {getattr(item, 'src', '') or module_id}")
            if graph.missing and len(info["error_messages"]) < 25:
                info["error_messages"].append(
                    "unresolved module(s): " + ", ".join(graph.missing[:5])
                )
    
        try:
            # Final sync before load events: module fetches and any classic
            # subresources are all in the resource timeline by now.
            _sync_resource_entries(ctx, info)
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
            _drain_injected(ctx)
            if not left:
                break
        for _ in range(4):
            try:
                ctx.eval("__advanceTimerClock(100)")
            except Exception:
                break
            _pump_jobs(ctx)
            _drain_injected(ctx)
        # End-of-pass intersection evaluation. The engine evaluates IO entries
        # on scroll only, but mutations (lazy-feed appends) also move targets
        # in/out of the viewport between scrolls; real observers re-evaluate
        # once per frame after any change. Missing this pass-end check made a
        # growing feed register its sentinel's state at observe time and never
        # transition again — the feed stalled after one batch.
        try:
            ctx.eval("__notifyIntersections(); true")
            _pump_jobs(ctx)
        except Exception:
            pass
    
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
            # The third argument of __receiveMessage is the targetOrigin
            # constraint, not the routing target. Passing msg["target"] (a
            # frame URL like https://host/page.html) made the child's
            # origin check compare URL-vs-origin and silently drop every
            # parent-to-frame and reply message.
            m_target_origin = json.dumps(msg.get("target_origin") or "*")
            # Fourth argument: the sender's Window identity. Nested widgets
            # verify event.source against their own iframe contentWindow;
            # without a hint the engine hands over an anonymous proxy and
            # the handshake declines silently (recaptcha-setup died here).
            m_source = msg.get("source_window")
            m_source_json = json.dumps(m_source) if m_source is not None else "null"
            # Fifth argument: transferred MessagePort metadata. The receiver
            # materializes proxy ends and publishes their ids; the caller then
            # rewires the sender's kept ends so both directions of the split
            # channel can talk (reCAPTCHA answers its handshake via a port).
            m_ports_json = json.dumps(msg.get("ports") or [])
            m_target = str(msg.get("target") or "")
            try:
                if m_target.startswith("port:"):
                    # Port-channel traffic: deliver straight into the owning
                    # realm's port inbox, not the window lane. A port message
                    # that went through __receiveMessage would fail every
                    # listener's event.source identity check and handshake
                    # channels would fall silent after one hop.
                    _, _, m_local = m_target.rpartition(":")
                    try:
                        m_local_id = int(m_local)
                    except ValueError:
                        m_local_id = 0
                    if m_local_id > 0:
                        m_sender_nonce = json.dumps(str(msg.get("ctx") or ""))
                        ctx.eval(
                            f"if (typeof globalThis.__wrPortReceive === 'function') globalThis.__wrPortReceive({m_local_id}, {m_data}, {m_sender_nonce}, {m_ports_json});"
                        )
                else:
                    ctx.eval(
                        f"if (typeof window !== 'undefined' && typeof window.__receiveMessage === 'function') window.__receiveMessage({m_data}, {m_origin}, {m_target_origin}, {m_source_json}, {m_ports_json});"
                    )
                if msg.get("ports"):
                    try:
                        recv_ids = json.loads(ctx.eval("JSON.stringify(globalThis.__WR_LAST_PORTS || [])"))
                        msg["recv_ports"] = recv_ids
                    except Exception:
                        pass
            except Exception as exc:
                record_error(exc)
        _pump_jobs(ctx)
        _drain_injected(ctx)
        # A delivered message's handler may schedule timers (widget reply
        # chains, async init). The main pass drains timers before this block,
        # so without a drain here those callbacks stay pending until some
        # later pass — from the message receiver's perspective the sender
        # never replied. Bounded like the main loop.
        for _ in range(MAX_TIMER_PASSES):
            try:
                left = ctx.eval("__drainTimers()")
            except Exception:
                break
            _pump_jobs(ctx)
            _drain_injected(ctx)
            if not left:
                break
        for _ in range(4):
            try:
                ctx.eval("__advanceTimerClock(100)")
            except Exception:
                break
            _pump_jobs(ctx)
            _drain_injected(ctx)

    # Apply agent interactions against the live (post-hydration) DOM.
    if actions:
        import json as _json
        from webresearch.kinematics import generate_human_drag_steps, generate_human_typing_delays

        try:
            ctx.eval("__startDeltaRecording()")
        except Exception:
            pass

        for action in actions:
            kind = (action or {}).get("kind") or "click"
            path = (action or {}).get("path") or []
            selector = (action or {}).get("selector") or ""
            try:
                stealth = bool((action or {}).get("stealth", False))
                try:
                    ctx.eval(f"__setStealthMode({str(stealth).lower()})")
                except Exception:
                    pass
                if kind == "type":
                    text = (action or {}).get("text") or ""
                    delays = generate_human_typing_delays(text) if stealth else False
                    nth = int((action or {}).get("occurrence", 1) or 1)
                    if selector:
                        ok = ctx.eval(
                            f"__activateSelector({_json.dumps(selector)}, {_json.dumps(text)}, {_json.dumps(delays)}, {str(stealth).lower()}, {nth})"
                        )
                    else:
                        ok = ctx.eval(
                            f"__typePath({_json.dumps(path)}, {_json.dumps(text)}, {_json.dumps(delays)}, {str(stealth).lower()})"
                        )
                elif kind == "drag":
                    dx = (action or {}).get("dx", 220.0)
                    dy = (action or {}).get("dy", 0.0)
                    steps = int((action or {}).get("steps", 16 if stealth else 3))
                    nth = int((action or {}).get("occurrence", 1) or 1)
                    trajectory = generate_human_drag_steps(float(dx), float(dy)) if stealth else None
                    target = selector or path
                    ok = ctx.eval(
                        f"__dragSelector({_json.dumps(target)}, {dx}, {dy}, {steps}, {_json.dumps(trajectory)}, {str(stealth).lower()}, {nth})"
                        if isinstance(target, str)
                        else f"__dragPath({_json.dumps(target)}, {dx}, {dy}, {steps}, {_json.dumps(trajectory)}, {str(stealth).lower()})"
                    )
                elif kind == "activate":
                    target = selector or path
                    nth = int((action or {}).get("occurrence", 1) or 1)
                    ok = ctx.eval(
                        f"__activateSelector({_json.dumps(target)}, null, false, {str(stealth).lower()}, {nth})"
                        if isinstance(target, str)
                        else f"__clickPath({_json.dumps(target)}, {str(stealth).lower()})"
                    )
                elif kind == "click_frame":
                    # The fetched frame is its own realm. Clicking the parent
                    # iframe's empty contentDocument never reaches the script
                    # that loaded with the frame.
                    frame_ref = (action or {}).get("frame_ref") or ""
                    frame_path = (action or {}).get("frame_path") or []
                    frame_selector = (action or {}).get("frame_selector") or ""
                    fctx = (frame_contexts or {}).get(frame_ref)
                    if fctx is not None:
                        if frame_selector:
                            ok = fctx.eval(
                                f"__click({_json.dumps(frame_selector)})"
                            )
                        else:
                            ok = fctx.eval(
                                f"__clickPath({_json.dumps(frame_path)}, {str(stealth).lower()})"
                            )
                        # Jobs, then a short timer step. The anchor reports the click
                        # from a 20ms timer. Firing every queued timer here also
                        # runs the 5s abort and the checkbox leaves loading
                        # before the parent has answered.
                        _pump_jobs(fctx)
                        try:
                            # Several short ticks. The anchor's click report is a
                            # chain of 20ms timers. One tick leaves the next 20ms
                            # queued, so the parent never sees the click. Eight
                            # ticks stay under the 5s abort.
                            for _ in range(8):
                                fctx.eval("__advanceTimerClock(100)")
                                _pump_jobs(fctx)
                        except Exception:
                            pass
                    else:
                        ok = ctx.eval(
                            f"__clickInFrame({_json.dumps(frame_ref)}, {_json.dumps(frame_path)}, {str(stealth).lower()})"
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
                elif kind in {"upload", "file"}:
                    target = selector or path
                    files = (action or {}).get("files") or []
                    if not files and (action or {}).get("file"):
                        files = [(action or {}).get("file")]
                    payload_chars = sum(
                        len(str(f.get("contentB64") or f.get("content_b64") or f.get("content") or ""))
                        if isinstance(f, dict)
                        else len(str(f))
                        for f in files
                    )
                    if payload_chars > MAX_UPLOAD_B64_CHARS:
                        raise ValueError(
                            f"upload payload {payload_chars} chars exceeds engine limit "
                            f"{MAX_UPLOAD_B64_CHARS} (approx {MAX_UPLOAD_B64_CHARS * 3 // 4} raw bytes)"
                        )
                    ok = ctx.eval(
                        f"__uploadFiles({_json.dumps(target)}, {_json.dumps(files)})"
                    )
                elif kind == "press":
                    target = selector or path
                    key_name = (action or {}).get("key") or "Enter"
                    ok = ctx.eval(
                        f"__pressKey({_json.dumps(target)}, {_json.dumps(key_name)}, {str(stealth).lower()})"
                    )
                elif kind == "scroll":
                    x = (action or {}).get("x", 0)
                    y = (action or {}).get("y", 800)
                    smooth = bool((action or {}).get("smooth", stealth))
                    # Wheel-delta semantics (like a real mouse wheel): the
                    # values add onto the current scroll position. The old
                    # scrollTo already kept a persistent scrollState, so a
                    # repeated scroll(y=800) landed on the same absolute
                    # target after the first call and infinite feeds never
                    # re-triggered their IntersectionObserver.
                    if smooth:
                        ok = ctx.eval(
                            f"__scrollSmooth(scrollX + {_json.dumps(x)}, scrollY + {_json.dumps(y)}); true"
                        )
                    else:
                        ok = ctx.eval(f"scrollBy({_json.dumps(x)}, {_json.dumps(y)}); true")
                else:
                    ok = ctx.eval(f"__clickPath({_json.dumps(path)}, {str(stealth).lower()})")
                applied_item = {
                    "kind": kind,
                    "path": path or selector,
                    "ok": bool(ok),
                }
                if kind in {"upload", "file"}:
                    applied_item["files"] = [f.get("name") if isinstance(f, dict) else str(f) for f in files]
                info["actions_applied"].append(applied_item)
            except Exception as exc:
                info["actions_applied"].append(
                    {"kind": kind, "path": path or selector, "ok": False, "error": str(exc)[:200]}
                )
            _pump_jobs(ctx)
            _drain_injected(ctx)
        # Action passes fetch subresources too (in-page fetch during click
        # handlers); keep the resource timeline real before the drains.
        _sync_resource_entries(ctx, info)
        for _ in range(MAX_TIMER_PASSES):
            try:
                left = ctx.eval("__drainTimers()")
            except Exception:
                break
            _pump_jobs(ctx)
            _drain_injected(ctx)
            if not left:
                break
        # A short virtual tick so a widget's 20ms follow-up (the anchor
        # reporting a click) still runs. The multi-second abort timers stay
        # queued so they cannot clear the checkbox before a reply arrives.
        for _ in range(4):
            try:
                ctx.eval("__advanceTimerClock(100)")
            except Exception:
                break
            _pump_jobs(ctx)
            _drain_injected(ctx)
        # Same end-of-pass intersection evaluation for action passes.
        try:
            ctx.eval("__notifyIntersections(); true")
            _pump_jobs(ctx)
        except Exception:
            pass
        try:
            deltas_raw = ctx.eval("__drainDeltaRecords()")
            info["deltas"] = json.loads(str(deltas_raw)) if deltas_raw else []
        except Exception:
            info["deltas"] = []

    try:
        out = str(ctx.eval("__serialize()") or html)
    except Exception as exc:
        # A poisoned tree (e.g. a raw string in childNodes) must not silently
        # discard every DOM mutation of the pass: fall back to the source
        # html but SURFACE the failure so the agent knows the view is stale.
        out = html
        record_error(exc, source="serializer (page mutations discarded; view is pre-pass HTML)")

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
    try:
        dialogs_raw = ctx.eval("JSON.stringify(globalThis.__WR_DIALOGS || [])")
        dialogs = json.loads(str(dialogs_raw)) if dialogs_raw else []
        if dialogs:
            info["dialogs"] = [
                {"type": d.get("type", ""), "message": str(d.get("message", ""))[:400], "answered": d.get("answered", "default")}
                for d in dialogs
            ]
    except Exception:
        pass
    try:
        popups_raw = ctx.eval("JSON.stringify(globalThis.__WR_POPUPS || [])")
        popups = json.loads(str(popups_raw)) if popups_raw else []
        if popups:
            info["popups"] = [
                {"url": str(p.get("url", ""))[:2000], "name": str(p.get("name", ""))[:120]}
                for p in popups
                if p.get("url")
            ]
    except Exception:
        pass
    # History API drains: the engine records pushState requests as a LIST
    # (several pushes in one script pass each create an entry) and
    # replaceState requests; the session owns the real history entries
    # (the engine context cannot outlive a navigation).
    try:
        pushed_raw = ctx.eval("JSON.stringify(globalThis.__WR_PUSHED || null)")
        pushed = json.loads(str(pushed_raw)) if pushed_raw else None
        pushes = [p for p in pushed if isinstance(p, dict) and p.get("url")] if isinstance(pushed, list) else (
            [pushed] if isinstance(pushed, dict) and pushed.get("url") else []
        )
        if pushes:
            info["history_pushes"] = [
                {"url": str(p.get("url", ""))[:2000], "state": p.get("state")}
                for p in pushes
            ]
    except Exception:
        pass
    try:
        replaced_raw = ctx.eval("JSON.stringify(globalThis.__WR_REPLACED || null)")
        replaced = json.loads(str(replaced_raw)) if replaced_raw else None
        if isinstance(replaced, dict) and replaced.get("url"):
            info["history_replace"] = {
                "url": str(replaced.get("url", ""))[:2000],
                "state": replaced.get("state"),
            }
    except Exception:
        pass
    try:
        move_raw = ctx.eval("Number(globalThis.__WR_HISTORY_MOVE || 0)")
        move = int(float(str(move_raw))) if move_raw else 0
        if move:
            info["history_move"] = move
            # Consume: the counter is a "moves since last drain" queue. The
            # live ctx keeps it after the read, so a same-document traversal
            # (which drains the live ctx directly) would re-consume the same
            # move and traverse twice.
            try:
                ctx.eval("globalThis.__WR_HISTORY_MOVE = 0; true")
            except Exception:
                pass
    except Exception:
        pass
    # Login wall detection: surface the wall as a first-class object so the
    # agent asks the user for credentials instead of planning page content.
    try:
        from webresearch.engine.inspect import inspect_html as _inspect
        from webresearch.engine.login import detect_login_wall, detect_otp_page

        targets, _ = _inspect(html, url)
        wall = detect_login_wall(html, url, targets, status=200)
        if wall is None:
            wall = detect_otp_page(html, url, targets)
        if wall is not None:
            wall_obj = {
                "kind": wall.kind,
                "url": wall.url,
                "confidence": wall.confidence,
                "detail": wall.detail,
            }
            if wall.kind == "form":
                wall_obj.update({
                    "username_ref": wall.username_ref,
                    "password_ref": wall.password_ref,
                    "submit_ref": wall.submit_ref,
                })
            if wall.kind == "otp":
                wall_obj.update({
                    "refs": wall.refs,
                    "expected_length": wall.expected_length,
                    "split_boxes": wall.split_boxes,
                })
            info.setdefault("login", {})["wall"] = wall_obj
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
    # Drain console logs, dosh.emit telemetry, and IndexedDB databases.
    try:
        c_raw = ctx.eval("typeof __drainConsoleLogs === 'function' ? __drainConsoleLogs() : '[]'")
        if c_raw:
            c_items = json.loads(str(c_raw))
            if isinstance(c_items, list) and c_items and not info.get("console"):
                info["console"] = c_items
    except Exception:
        pass
    try:
        e_raw = ctx.eval("typeof __drainDoshEmits === 'function' ? __drainDoshEmits() : '[]'")
        if e_raw:
            e_items = json.loads(str(e_raw))
            if isinstance(e_items, list) and e_items:
                info["dosh_emits"] = e_items
    except Exception:
        pass
    try:
        idb_raw = ctx.eval("typeof __drainIndexedDB === 'function' ? __drainIndexedDB() : '{}'")
        if idb_raw:
            idb_parsed = json.loads(str(idb_raw))
            if isinstance(idb_parsed, dict) and idb_parsed:
                info["indexed_db"] = idb_parsed
    except Exception:
        pass
    # Drain web storage so the caller can persist it at session level; the
    # engine context dies on the next navigation, but the values must not.
    try:
        drained = ctx.eval("__drainStorage()")
        if drained:
            info["storage"] = json.loads(str(drained))
    except Exception:
        pass
    # Drain the recorded storage MUTATIONS too (ops with old/new values), so
    # the session can broadcast `storage` events to other same-origin tabs —
    # the plain drain above loses which keys changed and what they used to be.
    try:
        changes = ctx.eval("__takeStorageChanges()")
        if changes:
            parsed = json.loads(str(changes))
            if isinstance(parsed, dict) and (parsed.get("local") or parsed.get("session")):
                info["storage_changes"] = parsed
    except Exception:
        pass
    # Hand off this pass's postMessages: they travel in info["post_messages"],
    # so strike them from the context's durable queue. What remains is traffic
    # from DIRECT evals, which take_pending_posts() hands to the routers.
    try:
        inbox = _CTX_POST_INBOX.get(_post_queue_key(ctx))
        if inbox:
            produced_ids = {id(m) for m in info["post_messages"]}
            inbox[:] = [m for m in inbox if id(m) not in produced_ids]
    except Exception:
        pass
    observer = getattr(client, "_dosh_runtime_observer", None)
    if observer is not None:
        try:
            observer(info, url)
        except Exception as exc:
            info["observer_error"] = type(exc).__name__
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