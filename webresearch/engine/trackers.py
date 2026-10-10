"""Tracker entity graph with surrogate responses.

Third-party requests whose host belongs to a known tracking entity are
blocked; where a stub keeps the page working, a surrogate script is served
instead of the network body. First-party and same-entity requests pass.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlparse

# Multi-level public suffixes (subset of the Public Suffix List) so
# registrable-domain computation is correct for common cases.
_TWO_LEVEL_SUFFIXES = frozenset(
    """
    co.uk org.uk me.uk ac.uk gov.uk net.uk sch.uk
    co.jp ne.jp or.jp go.jp ac.jp
    com.au net.au org.au id.au gov.au edu.au
    co.nz net.nz org.nz govt.nz
    co.kr or.kr go.kr ac.kr ne.kr
    com.br net.br org.br gov.br edu.br
    co.in net.in org.in gov.in ac.in
    com.mx net.mx org.mx gob.mx edu.mx
    co.za net.za org.za gov.za ac.za
    com.ar net.ar org.ar gov.ar edu.ar
    com.cn net.cn org.cn gov.cn edu.cn
    co.il org.il net.il gov.il ac.il
    com.tr net.tr org.tr gov.tr edu.tr
    com.sg net.sg org.sg gov.sg edu.sg
    com.hk net.hk org.hk gov.hk edu.hk
    com.tw net.tw org.tw gov.tw edu.tw
    co.th or.th go.th ac.th net.th
    com.ph net.ph org.ph gov.ph edu.ph
    com.my net.my org.my gov.my edu.my
    co.id or.id go.id ac.id net.id
    com.vn net.vn org.vn gov.vn edu.vn
    """.split()
)


def registrable_domain(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) < 2:
        return host
    candidate = ".".join(parts[-2:])
    if candidate in _TWO_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return candidate


# Seed entity graph: entity -> member domains (registrable domains).
SEED_ENTITIES: dict[str, list[str]] = {
    "Google": ["google-analytics.com", "googletagmanager.com", "doubleclick.net", "googlesyndication.com", "googleadservices.com", "google.com", "googletagservices.com", "googleapis.com", "gstatic.com", "youtube.com"],
    "Meta": ["facebook.net", "facebook.com", "fbcdn.net", "fbsbx.com"],
    "Microsoft": ["bing.com", "clarity.ms", "linkedin.com", "microsoft.com"],
    "Amazon": ["amazon-adsystem.com", "amazon.com", "aax.amazon-adsystem.com"],
    "TikTok": ["tiktok.com", "tiktokv.com", "tiktokcdn.com"],
    "Adobe": ["adobe.com", "demdex.net", "omtrdc.net"],
    "Oracle": ["oracle.com", "bluekai.com", "eloqua.com"],
    "Salesforce": ["salesforce.com", "pardot.com", "evergage.com"],
    "Hotjar": ["hotjar.com", "hotjar.io"],
    "Mixpanel": ["mixpanel.com"],
    "Segment": ["segment.com", "segment.io"],
    "Amplitude": ["amplitude.com"],
    "New Relic": ["newrelic.com", "nr-data.net"],
    "Cloudflare Insights": ["cloudflareinsights.com"],
    "Yandex": ["yandex.ru", "yandex.net", "mc.yandex.ru"],
    "Baidu": ["baidu.com", "bdstatic.com"],
    "Criteo": ["criteo.com", "criteo.net"],
    "Outbrain": ["outbrain.com"],
    "Taboola": ["taboola.com", "taboolasyndication.com"],
    "Quantcast": ["quantcast.com", "quantserve.com"],
    "Comscore": ["comscore.com", "scorecardresearch.com"],
    "Nielsen": ["nielsen.com", "imrworldwide.com"],
    "Akamai mPulse": ["akstat.io", "go-mpulse.net"],
    "Datadog RUM": ["datadoghq-browser-agent.com"],
    "Sentry": ["sentry.io"],
    "FullStory": ["fullstory.com"],
    "Snowplow": ["snowplowanalytics.com"],
}

# Surrogate stubs keyed by script-host substring: minimal no-op shims that keep
# common call sites from throwing, served instead of the tracker body.
SURROGATES: dict[str, str] = {
    "google-analytics.com": "/* surrogate: google-analytics */\nvar _gaq=_gaq||[];function ga(){}\n",
    "googletagmanager.com": "/* surrogate: gtm */\nwindow.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}\n",
    "connect.facebook.net": "/* surrogate: facebook pixel */\nfunction fbq(){}\n",
    "hotjar.com": "/* surrogate: hotjar */\nwindow.hj=window.hj||function(){};\n",
    "platform.twitter.com": "/* surrogate: twitter widgets */\nwindow.twttr=window.twttr||{widgets:{load:function(){}}};\n",
}


@dataclass
class TrackingDecision:
    status: str  # ALLOWED, SAME_ENTITY_ALLOWED, BLOCKED
    entity: str = ""
    surrogate: str = ""


class TrackerGraph:
    def __init__(self, entities: dict[str, list[str]] | None = None) -> None:
        merged: dict[str, list[str]] = {k: list(v) for k, v in SEED_ENTITIES.items()}
        extra = os.environ.get("WEBRESEARCH_TRACKER_GRAPH", "")
        if extra:
            try:
                custom = json.loads(extra)
                for entity, domains in custom.items():
                    if isinstance(domains, list):
                        merged.setdefault(str(entity), []).extend(str(d) for d in domains)
            except Exception:
                pass
        remote_error = self._merge_remote_list(merged)
        self.remote_error = remote_error
        if entities is not None:
            merged = {k: list(v) for k, v in entities.items()}
        self.domain_to_entity: dict[str, str] = {}
        for entity, domains in merged.items():
            for d in domains:
                self.domain_to_entity[d.lower()] = entity

    @staticmethod
    def _merge_remote_list(merged: dict[str, list[str]]) -> str:
        """Fetch a hash-verified remote tracker list (DDG-style remote config).

        WEBRESEARCH_TRACKER_LIST_URL -> JSON {entity: [domains]}. The body must
        match WEBRESEARCH_TRACKER_LIST_SHA256 (hex or base64) or it is rejected
        entirely. Synchronous fetch via urllib so graph construction never needs
        the async client. Failures are reported, never silently swallowed into
        an empty merge.
        """
        import hashlib as _hashlib
        import urllib.request as _urllib

        url = os.environ.get("WEBRESEARCH_TRACKER_LIST_URL", "").strip()
        if not url:
            return ""
        want = os.environ.get("WEBRESEARCH_TRACKER_LIST_SHA256", "").strip()
        if not want:
            return "remote list refused: no WEBRESEARCH_TRACKER_LIST_SHA256 pinned"
        try:
            with _urllib.urlopen(url, timeout=15) as resp:
                body = resp.read(2 * 1024 * 1024)
        except Exception as exc:
            return f"remote list fetch failed: {type(exc).__name__}"
        digest = _hashlib.sha256(body).digest()
        ok = False
        for encoding in ("hex", "base64"):
            try:
                if encoding == "hex":
                    ok = digest.hex() == want.lower()
                else:
                    import base64 as _b64
                    ok = _b64.b64encode(digest).decode() == want
                if ok:
                    break
            except Exception:
                continue
        if not ok:
            return "remote list refused: sha256 mismatch"
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return "remote list refused: not JSON"
        if not isinstance(data, dict):
            return "remote list refused: top-level JSON is not an object"
        count = 0
        for entity, domains in data.items():
            if isinstance(domains, list):
                clean = [str(d) for d in domains if str(d).strip()]
                if clean:
                    merged.setdefault(str(entity), []).extend(clean)
                    count += len(clean)
        return "" if count else "remote list merged 0 domains"
        self.domain_to_entity: dict[str, str] = {}
        for entity, domains in merged.items():
            for d in domains:
                self.domain_to_entity[d.lower()] = entity

    def entity_of(self, host: str) -> str:
        host = (host or "").lower()
        if host in self.domain_to_entity:
            return self.domain_to_entity[host]
        for domain, entity in self.domain_to_entity.items():
            if host.endswith("." + domain):
                return entity
        return ""

    def evaluate(self, request_url: str, document_url: str) -> TrackingDecision:
        try:
            req_host = (urlparse(request_url).hostname or "").lower()
            doc_host = (urlparse(document_url).hostname or "").lower()
        except Exception:
            return TrackingDecision("ALLOWED")
        if not req_host or not doc_host:
            return TrackingDecision("ALLOWED")
        entity = self.entity_of(req_host)
        if not entity:
            return TrackingDecision("ALLOWED")
        if registrable_domain(req_host) == registrable_domain(doc_host):
            return TrackingDecision("ALLOWED", entity)
        doc_entity = self.entity_of(doc_host)
        if doc_entity and doc_entity == entity:
            return TrackingDecision("SAME_ENTITY_ALLOWED", entity)
        surrogate = ""
        for marker, stub in SURROGATES.items():
            if marker in req_host:
                surrogate = stub
                break
        return TrackingDecision("BLOCKED", entity, surrogate)


GRAPH = TrackerGraph()
