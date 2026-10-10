"""Persistent HSTS state: dynamic entries, preload seed, upgrade enforcement.

Mirrors TransportSecurityState: Strict-Transport-Security headers observed on
HTTPS responses create entries (max-age, includeSubDomains); entries expire;
preload entries never expire. HTTP navigations to covered hosts are upgraded
to HTTPS before any network happens.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

# Curated preload seed: apexes universally on the Chromium preload list.
PRELOAD_SEED: list[tuple[str, bool]] = [
    ("google.com", True), ("youtube.com", True), ("facebook.com", True),
    ("twitter.com", True), ("instagram.com", True), ("linkedin.com", True),
    ("microsoft.com", True), ("apple.com", True), ("amazon.com", True),
    ("cloudflare.com", True), ("github.com", True), ("gmail.com", True),
    ("paypal.com", True), ("bankofamerica.com", True), ("chase.com", True),
    ("wellsfargo.com", True), ("citi.com", True), ("amex.com", True),
    ("americanexpress.com", True), ("dropbox.com", True), ("slack.com", True),
    ("stripe.com", True), ("duckduckgo.com", True), ("torproject.org", True),
    ("wikipedia.org", True), ("mozilla.org", True), ("ubuntu.com", True),
    ("netflix.com", True), ("spotify.com", True), ("ebay.com", True),
    ("wordpress.com", True), ("tumblr.com", True), ("pinterest.com", True),
    ("reddit.com", True), ("quora.com", True), ("stackoverflow.com", True),
    ("bbc.co.uk", True), ("theguardian.com", True), ("nytimes.com", True),
    ("washingtonpost.com", True), ("cnn.com", True),
]


@dataclass
class HstsEntry:
    host: str
    include_subdomains: bool
    expires_at: float
    preload: bool = False

    def live(self, now: float) -> bool:
        return self.preload or self.expires_at > now

    def covers(self, host: str, now: float) -> bool:
        if not self.live(now):
            return False
        host = host.lower()
        if host == self.host:
            return True
        return self.include_subdomains and host.endswith("." + self.host)


def parse_sts(value: str) -> tuple[int, bool] | None:
    max_age: int | None = None
    include = False
    for part in (value or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip().lower() == "max-age":
                try:
                    max_age = int(v.strip().strip('"'))
                except ValueError:
                    return None
        elif part.lower() == "includesubdomains":
            include = True
        elif part.lower() == "preload":
            continue
        else:
            return None
    if max_age is None or max_age < 0:
        return None
    return max_age, include


class HstsState:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("WEBRESEARCH_HSTS_FILE", "")
        self.entries: dict[str, HstsEntry] = {}
        for host, sub in PRELOAD_SEED:
            self.entries[host] = HstsEntry(host, sub, float("inf"), preload=True)
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            for item in data.get("entries", []):
                entry = HstsEntry(item["host"], bool(item.get("include_subdomains")),
                                  float(item.get("expires_at", 0)))
                if entry.live(now):
                    self.entries[entry.host] = entry
        except Exception:
            pass

    def _save(self) -> None:
        if not self.path:
            return
        try:
            now = time.time()
            data = {"entries": [
                {"host": e.host, "include_subdomains": e.include_subdomains, "expires_at": e.expires_at}
                for e in self.entries.values() if not e.preload and e.live(now)
            ]}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def observe(self, host: str, sts_value: str, *, secure: bool) -> bool:
        """Record STS from an HTTPS response. Returns True if stored."""
        if not secure:
            return False
        parsed = parse_sts(sts_value)
        if parsed is None:
            return False
        max_age, include = parsed
        host = (host or "").lower()
        if max_age == 0:
            self.entries.pop(host, None)
            self._save()
            return True
        self.entries[host] = HstsEntry(host, include, time.time() + max_age)
        self._save()
        return True

    def should_upgrade(self, host: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        host = (host or "").lower()
        return any(e.covers(host, now) for e in self.entries.values())

    def upgrade_url(self, url: str) -> str | None:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        if p.scheme != "http" or not p.hostname:
            return None
        if self.should_upgrade(p.hostname):
            return urlunparse(p._replace(scheme="https"))
        return None


STATE = HstsState()
