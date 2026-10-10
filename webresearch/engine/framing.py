"""Frame-embedding enforcement: X-Frame-Options, CSP frame-ancestors, COOP/COEP notes."""

from __future__ import annotations

from urllib.parse import urlparse


def same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    ha, hb = (pa.hostname or "").lower(), (pb.hostname or "").lower()
    port_a = pa.port or (443 if pa.scheme == "https" else 80)
    port_b = pb.port or (443 if pb.scheme == "https" else 80)
    return pa.scheme == pb.scheme and ha == hb and port_a == port_b


def _origin_of(url: str) -> str:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    port = p.port or (443 if p.scheme == "https" else 80)
    default = (p.scheme == "https" and port == 443) or (p.scheme == "http" and port == 80)
    return f"{p.scheme}://{host}" if default else f"{p.scheme}://{host}:{port}"


def xfo_decision(xfo: str | None, parent_url: str, frame_url: str) -> str | None:
    if not xfo:
        return None
    parent_origin = _origin_of(parent_url)
    for directive in xfo.split(","):
        parts = directive.strip().split(None, 1)
        if not parts:
            continue
        name = parts[0].upper()
        if name == "DENY":
            return "blocked by X-Frame-Options: DENY"
        if name == "SAMEORIGIN":
            if not same_origin(parent_url, frame_url):
                return "blocked by X-Frame-Options: SAMEORIGIN (cross-origin embedder)"
        elif name == "ALLOW-FROM":
            allowed = (parts[1].strip() if len(parts) > 1 else "")
            if _origin_of(allowed) != parent_origin:
                return f"blocked by X-Frame-Options: ALLOW-FROM {allowed}"
    return None


def _ancestor_matches(source: str, parent_url: str, frame_url: str) -> bool:
    s = source.strip()
    if not s:
        return False
    low = s.lower()
    if low == "'none'":
        return False
    if low == "'self'":
        return same_origin(parent_url, frame_url)
    if s.endswith(":"):
        return urlparse(parent_url).scheme.lower() + ":" == low
    if low.startswith("http://") or low.startswith("https://") or "://" in s:
        return _origin_of(s) == _origin_of(parent_url)
    host = (urlparse(parent_url).hostname or "").lower()
    if low.startswith("*."):
        suffix = low[1:]
        return host.endswith(suffix) or host == low[2:]
    return host == low


def frame_ancestors_decision(csp: str | None, parent_url: str, frame_url: str = "") -> str | None:
    if not csp:
        return None
    entries: list[str] = []
    for part in csp.split(";"):
        toks = part.strip().split()
        if toks and toks[0].lower() == "frame-ancestors":
            entries = toks[1:]
            break
    if not entries:
        return None
    if any(e.lower() == "'none'" for e in entries):
        return "blocked by CSP frame-ancestors 'none'"
    if any(_ancestor_matches(e, parent_url, frame_url) for e in entries):
        return None
    return "blocked by CSP frame-ancestors (embedder not allowlisted)"


def coop_coep_notes(resp_headers: dict) -> list[str]:
    lowered = {k.lower(): v for k, v in (resp_headers or {}).items()}
    notes: list[str] = []
    coop = lowered.get("cross-origin-opener-policy", "")
    coep = lowered.get("cross-origin-embedder-policy", "")
    corp = lowered.get("cross-origin-resource-policy", "")
    if coop:
        notes.append(f"coop:{coop.split(';')[0].strip()}")
    if coep:
        notes.append(f"coep:{coep.split(';')[0].strip()}")
    if corp:
        notes.append(f"corp:{corp.split(';')[0].strip()}")
    return notes


def check_frame_embedding(
    resp_headers: dict,
    parent_url: str,
    frame_url: str,
    csp: str | None = None,
) -> tuple[str | None, list[str]]:
    lowered = {k.lower(): v for k, v in (resp_headers or {}).items()}
    policy = csp if csp is not None else lowered.get("content-security-policy")
    reason = xfo_decision(lowered.get("x-frame-options"), parent_url, frame_url)
    if reason is None:
        reason = frame_ancestors_decision(policy, parent_url, frame_url)
    return reason, coop_coep_notes(lowered)
