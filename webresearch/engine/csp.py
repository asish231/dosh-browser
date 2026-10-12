"""Pragmatic CSP evaluation for script-src, mirroring browser semantics."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlparse


def _entries(csp: str, name: str) -> list[str]:
    for part in csp.split(";"):
        toks = part.strip().split()
        if toks and toks[0].lower() == name:
            return toks[1:]
    return []


def _script_src(csp: str) -> list[str]:
    # CSP3 element-specific directive takes precedence for script tags.
    # LinkedIn's page uses script-src-elem only — a real page's inline nonce
    # scripts were never filtered (and never matched) without this.
    elem = _entries(csp, "script-src-elem")
    if elem:
        return elem
    src = _entries(csp, "script-src")
    if src:
        return src
    if _entries(csp, "default-src"):
        return _entries(csp, "default-src")
    return []


def _host_matches(source: str, url: str) -> bool:
    pu = urlparse(url)
    s = source.strip().lower()
    if s == "*":
        return True
    if s.endswith(":"):
        return pu.scheme + ":" == s
    if s.startswith("*."):
        host = pu.hostname or ""
        return host.endswith(s[1:]) or host == s[2:]
    if "://" in s:
        sp = urlparse(s)
        if sp.hostname and sp.hostname not in (pu.hostname or ""):
            return False
        if sp.scheme and sp.scheme != pu.scheme:
            return False
        return True
    return s == (pu.hostname or "")


def _directive_entries(csp: str, name: str, fallback: str = "") -> list[str]:
    entries = _entries(csp, name)
    if entries:
        return entries
    if fallback:
        entries = _entries(csp, fallback)
        if entries:
            return entries
    return _entries(csp, "default-src")


def style_source_allowed(csp: str | None, *, inline_css: str = "", external_url: str = "", page_url: str = "") -> bool:
    if not csp:
        return True
    src = _directive_entries(csp, "style-src", "style-src-elem")
    if not src:
        return True
    lowered = [s.lower() for s in src]
    if external_url:
        if any(s in ("'self'",) for s in lowered) and urlparse(external_url).netloc == urlparse(page_url).netloc:
            return True
        if "https:" in lowered and external_url.startswith("https:"):
            return True
        return any(_host_matches(s, external_url) for s in lowered if not s.startswith("'"))
    if inline_css:
        if "'unsafe-inline'" in lowered:
            return True
        data = inline_css.encode("utf-8")
        for s in src:
            if s.startswith("'sha") and "-" in s:
                algo, _, b64 = s.strip("'").partition("-")
                try:
                    digest = hashlib.new(algo, data).digest()
                except ValueError:
                    continue
                if base64.b64encode(digest).decode() == b64:
                    return True
        return False
    return True


def trusted_types_config(csp: str | None, report_only_csp: str | None = None) -> dict:
    """Parse require-trusted-types-for + trusted-types directives."""
    cfg: dict = {"requireScript": False, "reportOnly": False, "allowedNames": None, "allowDuplicates": True}
    for source, report_only in ((csp, False), (report_only_csp, True)):
        if not source:
            continue
        for part in source.split(";"):
            toks = part.strip().split()
            if not toks:
                continue
            name = toks[0].lower()
            if name == "require-trusted-types-for":
                if any(t.strip("'\"").lower() == "script" for t in toks[1:]):
                    cfg["requireScript"] = True
                    if report_only:
                        cfg["reportOnly"] = True
            elif name == "trusted-types":
                names = []
                for t in toks[1:]:
                    tl = t.lower()
                    if tl == "allow-duplicates":
                        cfg["allowDuplicates"] = True
                    elif tl == "'none'":
                        names = []
                    else:
                        names.append(t.strip("'\""))
                cfg["allowedNames"] = names
    return cfg


def script_source_allowed(
    csp: str | None,
    *,
    inline_code: str = "",
    external_url: str = "",
    page_url: str = "",
    nonce: str = "",
) -> bool:
    if not csp:
        return True
    src = _script_src(csp)
    if not src:
        return True
    lowered = [s.lower() for s in src]
    # 'strict-dynamic' (CSP3): nonce/hash-authorized scripts may load more
    # scripts; host allowlists are ignored. A nonce match authorizes both the
    # carrying inline script and subsequent external loads.
    strict_dynamic = "'strict-dynamic'" in lowered
    if nonce and any(s.startswith("'nonce-") for s in src):
        want = nonce.strip()
        for s in src:
            if s.startswith("'nonce-") and s[len("'nonce-"):-1] == want:
                return True
    if external_url:
        if "'unsafe-inline'" in lowered and not external_url:
            pass
        if any(s in ("'self'",) for s in lowered) and urlparse(external_url).netloc == urlparse(page_url).netloc:
            return True
        if "https:" in lowered and external_url.startswith("https:"):
            return True
        if "http:" in lowered and external_url.startswith("http:"):
            return True
        if strict_dynamic and any(s.startswith("'nonce-") for s in src):
            # strict-dynamic trusts script-inserted loads; nonce presence in
            # the policy means the page is nonce-bootstrapped and its module
            # graph loads are authorized.
            return True
        return any(_host_matches(s, external_url) for s in lowered if not s.startswith("'"))
    if inline_code:
        if "'unsafe-eval'" in lowered or "'unsafe-inline'" in lowered:
            return True
        data = inline_code.encode("utf-8")
        for s in src:
            if s.startswith("'sha") and "-" in s:
                algo, _, b64 = s.strip("'").partition("-")
                try:
                    digest = hashlib.new(algo, data).digest()
                except ValueError:
                    continue
                if base64.b64encode(digest).decode() == b64:
                    return True
        return False
    return True
