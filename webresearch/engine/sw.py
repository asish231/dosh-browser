"""Service-worker awareness: registration detection, script analysis, precache priming."""

from __future__ import annotations

import re
from urllib.parse import urljoin

_REGISTER_RE = re.compile(
    r"serviceWorker\s*\.\s*register\s*\(\s*['\"]([^'\"]+)['\"]", re.IGNORECASE
)
_PRECACHE_RE = re.compile(
    r"addAll\s*\(\s*\[(.*?)\]\s*\)|precacheAndRoute\s*\(\s*\[(.*?)\]\s*\)",
    re.DOTALL,
)
_URL_RE = re.compile(r"['\"](/[^'\"]*|https?://[^'\"]+)['\"]")
_FETCH_HANDLER_RE = re.compile(r"addEventListener\s*\(\s*['\"]fetch['\"]", re.IGNORECASE)
_CACHE_STRATEGY_RE = re.compile(
    r"CacheFirst|NetworkFirst|StaleWhileRevalidate|CacheOnly|NetworkOnly"
)
_SKIP_WAITING_RE = re.compile(r"skipWaiting\s*\(\s*\)", re.IGNORECASE)


def find_registrations(code: str, base_url: str) -> list[str]:
    out: list[str] = []
    for match in _REGISTER_RE.finditer(code or ""):
        raw = match.group(1).strip()
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            continue
        abs_url = urljoin(base_url, raw)
        if abs_url not in out:
            out.append(abs_url)
    return out


def analyze_sw_script(code: str) -> dict:
    code = code or ""
    precache: list[str] = []
    for match in _PRECACHE_RE.finditer(code):
        blob = match.group(1) if match.group(1) is not None else match.group(2)
        for um in _URL_RE.finditer(blob or ""):
            u = um.group(1)
            if u not in precache:
                precache.append(u)
    return {
        "precache": precache,
        "has_fetch_handler": bool(_FETCH_HANDLER_RE.search(code)),
        "strategies": sorted(set(_CACHE_STRATEGY_RE.findall(code))),
        "skip_waiting": bool(_SKIP_WAITING_RE.search(code)),
        "bytes": len(code.encode("utf-8", "ignore")),
    }


async def install_service_workers(
    codes: list[str],
    base_url: str,
    client,
    *,
    max_precache: int = 10,
) -> list[dict]:
    """Fetch registered SW scripts, analyze them, and prime the HTTP cache
    with their precache manifests (same-origin only)."""
    from webresearch.cache import CACHE

    seen: set[str] = set()
    installed: list[dict] = []
    for code in codes:
        for sw_url in find_registrations(code, base_url):
            if sw_url in seen:
                continue
            seen.add(sw_url)
            record: dict = {"url": sw_url, "scope": sw_url.rsplit("/", 1)[0] + "/"}
            try:
                resp = await client.get(sw_url)
                if resp.status_code >= 400:
                    record["error"] = f"http-{resp.status_code}"
                    installed.append(record)
                    continue
                analysis = analyze_sw_script(resp.text)
                record.update(analysis)
                primed = 0
                for raw in analysis["precache"][:max_precache]:
                    target = urljoin(sw_url, raw)
                    from urllib.parse import urlparse as _up
                    if _up(target).hostname != _up(sw_url).hostname:
                        continue
                    try:
                        presp = await client.get(target)
                        if presp.status_code == 200:
                            CACHE.store(
                                "GET", target, {},
                                presp.status_code, dict(presp.headers),
                                presp.content,
                            )
                            primed += 1
                    except Exception:
                        continue
                record["primed"] = primed
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"[:200]
            installed.append(record)
    return installed


def sw_for_url(records, url):
    """Return the installed worker whose scope covers url and has a fetch
    handler, preferring the longest (most specific) scope."""
    best = None
    for rec in records or []:
        if rec.get("error") or not rec.get("has_fetch_handler"):
            continue
        scope = rec.get("scope") or ""
        if scope and url.startswith(scope):
            if best is None or len(scope) > len(best.get("scope") or ""):
                best = rec
    return best


def sw_strategy(record):
    strategies = record.get("strategies") or []
    for name in ("CacheFirst", "StaleWhileRevalidate", "NetworkFirst", "CacheOnly", "NetworkOnly"):
        if name in strategies:
            return name
    return "NetworkFirst"
