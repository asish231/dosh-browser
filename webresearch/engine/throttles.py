"""Navigation throttles with Safe-Browsing-style hash-prefix verdicts.

Mirrors the real two-step protocol: canonicalize the URL, expand lookup
expressions (host suffixes x path prefixes), compare 4-byte SHA-256 prefixes
against a local prefix set, and confirm a prefix hit with a full-hash lookup.
Without full-hash confirmation a prefix hit is only suspicious, never a block.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from urllib.parse import urlparse


def canonicalize_url(url: str) -> tuple[str, str] | None:
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in {"http", "https"}:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        return None
    path = p.path or "/"
    if p.query:
        path = path + "?" + p.query
    return host, path


def lookup_expressions(host: str, path: str) -> list[str]:
    parts = host.split(".")
    hosts: list[str] = []
    for i in range(min(len(parts), 5)):
        hosts.append(".".join(parts[i:]))
    path_prefixes = [path]
    idx = path.rfind("/")
    while idx > 0:
        path_prefixes.append(path[:idx])
        idx = path[:idx].rfind("/")
    exprs: list[str] = []
    for h in hosts:
        exprs.append(h)
        for pp in path_prefixes:
            exprs.append(h + pp)
    return exprs


def sha256_prefix(data: str, length: int = 4) -> bytes:
    return hashlib.sha256(data.encode("utf-8")).digest()[:length]


def _hex_set(env: str) -> set[bytes]:
    out: set[bytes] = set()
    for part in os.environ.get(env, "").replace("\n", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        try:
            out.add(bytes.fromhex(part))
        except ValueError:
            continue
    return out


@dataclass
class ThrottleVerdict:
    proceed: bool
    reason: str = ""


class SafeBrowsingThrottle:
    """Prefix-hit then full-hash-confirm, exactly like the wire protocol."""

    def __init__(
        self,
        prefixes: set[bytes] | None = None,
        full_hashes: set[bytes] | None = None,
    ) -> None:
        self._prefixes = prefixes
        self._full_hashes = full_hashes

    @property
    def prefixes(self) -> set[bytes]:
        base = self._prefixes if self._prefixes is not None else _hex_set("WEBRESEARCH_SB_PREFIXES")
        try:
            from webresearch.engine.sbupdate import STORE
            for state in STORE.lists.values():
                base = base | state.prefixes
        except Exception:
            pass
        return base

    @property
    def full_hashes(self) -> set[bytes]:
        return self._full_hashes if self._full_hashes is not None else _hex_set("WEBRESEARCH_SB_FULLHASHES")

    def check(self, url: str) -> ThrottleVerdict:
        canon = canonicalize_url(url)
        if canon is None:
            return ThrottleVerdict(True)
        host, path = canon
        if not self.prefixes:
            return ThrottleVerdict(True)
        for expr in lookup_expressions(host, path):
            if sha256_prefix(expr) in self.prefixes:
                full = hashlib.sha256(expr.encode("utf-8")).digest()
                if full in self.full_hashes:
                    return ThrottleVerdict(False, f"safe-browsing match: {expr}")
                return ThrottleVerdict(True, f"prefix hit without full-hash confirmation: {expr}")
        return ThrottleVerdict(True)


class ThrottleRegistry:
    def __init__(self) -> None:
        self.throttles: list = [SafeBrowsingThrottle()]

    def evaluate(self, url: str) -> ThrottleVerdict:
        for throttle in self.throttles:
            verdict = throttle.check(url)
            if not verdict.proceed:
                return verdict
        return ThrottleVerdict(True)


REGISTRY = ThrottleRegistry()
