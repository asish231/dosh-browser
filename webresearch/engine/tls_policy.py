"""TLS interstitial model: classify transport TLS failures, decide explicitly.

Default is deny. Proceeding past an overridable certificate error requires an
explicit operator decision (WEBRESEARCH_TLS_PROCEED_HOSTS) and is recorded.
Non-overridable errors (wrong host, HSTS-pinned host) can never proceed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DECISIONS: list[dict] = []


def _env_hosts(name: str) -> set[str]:
    out: set[str] = set()
    for part in os.environ.get(name, "").replace("\n", ",").split(","):
        host = part.strip().lower().lstrip(".")
        if host:
            out.add(host)
    return out


def classify_tls_error(exc: BaseException) -> str | None:
    text = f"{type(exc).__name__}: {exc}".lower()
    if not any(k in text for k in ("ssl", "tls", "certificate", "certificat", "handshake")):
        return None
    if "hostname" in text or "idna" in text or "doesn't match" in text or "mismatch" in text:
        return "wrong-host"
    if "expired" in text:
        return "expired"
    if "self-signed" in text or "self signed" in text or "unknown ca" in text or "unable to get local issuer" in text or "verify failed" in text:
        return "untrusted-chain"
    return "generic"


def is_overridable(kind: str, host: str) -> bool:
    if kind == "wrong-host":
        return False
    pinned = _env_hosts("WEBRESEARCH_HSTS_PRELOAD")
    h = (host or "").lower()
    for p in pinned:
        if h == p or h.endswith("." + p):
            return False
    return True


def decision_for_host(host: str, kind: str) -> str:
    if not is_overridable(kind, host):
        return "deny"
    allowed = _env_hosts("WEBRESEARCH_TLS_PROCEED_HOSTS")
    h = (host or "").lower()
    for a in allowed:
        if h == a or h.endswith("." + a):
            return "proceed"
    return "deny"


@dataclass
class TlsInterstitial:
    host: str
    kind: str
    overridable: bool
    decision: str
    log: list[dict] = field(default_factory=list)

    def record(self) -> dict:
        entry = {"host": self.host, "error": self.kind, "overridable": self.overridable, "decision": self.decision}
        DECISIONS.append(entry)
        return entry


def handle_tls_error(host: str, exc: BaseException) -> TlsInterstitial | None:
    kind = classify_tls_error(exc)
    if kind is None:
        return None
    overridable = is_overridable(kind, host)
    decision = decision_for_host(host, kind)
    interstitial = TlsInterstitial(host=host, kind=kind, overridable=overridable, decision=decision)
    interstitial.record()
    return interstitial
