"""Cross-origin isolation enforcement: CORP, COEP, COOP notes.

CORP (Cross-Origin-Resource-Policy) is enforced on responses: a resource that
says `same-origin`/`same-site` is blocked for cross-origin/site consumers.
COEP `require-corp` on the embedding document means CORP-less cross-origin
subresources are blocked. COOP is recorded (browsing-context-group switches
are not modelled here) rather than faked.
"""

from __future__ import annotations

from urllib.parse import urlparse


def _origin(url: str) -> tuple[str, str, int]:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    port = p.port or (443 if p.scheme == "https" else 80)
    return p.scheme.lower(), host, port


def same_origin(a: str, b: str) -> bool:
    return _origin(a) == _origin(b)


def same_site(a: str, b: str) -> bool:
    from webresearch.engine.trackers import registrable_domain
    sa, sb = _origin(a), _origin(b)
    return sa[0] == sb[0] and registrable_domain(sa[1]) == registrable_domain(sb[1])


def corp_decision(resp_headers: dict, resource_url: str, requester_url: str) -> str | None:
    lowered = {k.lower(): v for k, v in (resp_headers or {}).items()}
    corp = lowered.get("cross-origin-resource-policy", "").split(";")[0].strip().lower()
    if not corp or corp in {"cross-origin", "unsafe-none"}:
        return None
    if corp == "same-origin":
        if not same_origin(resource_url, requester_url):
            return "blocked by Cross-Origin-Resource-Policy: same-origin"
        return None
    if corp == "same-site":
        if not same_site(resource_url, requester_url):
            return "blocked by Cross-Origin-Resource-Policy: same-site"
        return None
    return None


def coep_requires_corp(page_headers: dict | None) -> bool:
    lowered = {k.lower(): v for k, v in (page_headers or {}).items()}
    coep = lowered.get("cross-origin-embedder-policy", "").split(";")[0].strip().lower()
    return coep == "require-corp"


def coep_decision(
    resource_resp_headers: dict,
    resource_url: str,
    requester_url: str,
    page_headers: dict | None,
) -> str | None:
    if not coep_requires_corp(page_headers):
        return None
    if same_origin(resource_url, requester_url):
        return None
    lowered = {k.lower(): v for k, v in (resource_resp_headers or {}).items()}
    if "cross-origin-resource-policy" in lowered:
        return None
    return "blocked by COEP require-corp: cross-origin subresource without CORP"


def coop_notes(resp_headers: dict) -> list[str]:
    lowered = {k.lower(): v for k, v in (resp_headers or {}).items()}
    coop = lowered.get("cross-origin-opener-policy", "")
    report_only = lowered.get("cross-origin-opener-policy-report-only", "")
    notes: list[str] = []
    if coop:
        notes.append(f"coop:{coop.split(';')[0].strip()}")
    if report_only:
        notes.append(f"coop-report-only:{report_only.split(';')[0].strip()}")
    return notes


def check_subresource(
    resource_resp_headers: dict,
    resource_url: str,
    requester_url: str,
    page_headers: dict | None,
) -> tuple[str | None, list[str]]:
    reason = corp_decision(resource_resp_headers, resource_url, requester_url)
    if reason is None:
        reason = coep_decision(resource_resp_headers, resource_url, requester_url, page_headers)
    return reason, coop_notes(resource_resp_headers)
