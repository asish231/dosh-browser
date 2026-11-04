"""robots.py — RFC 9309 Robots Exclusion Protocol Parser and Store for Dosh-Browser.

Evaluates whether an agent or crawler is permitted to access a given URL path
based on the site's `robots.txt` specifications.
"""

from __future__ import annotations

import fnmatch
import time
from urllib.parse import urlparse


class ContentSignal:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value

    def __repr__(self) -> str:
        return f"ContentSignal({self.name}={self.value})"


def extract_product_token(user_agent: str) -> str:
    trimmed = user_agent.strip()
    for i, c in enumerate(trimmed):
        if not c.isalnum() and c not in ("-", "_"):
            return trimmed[:i]
    return trimmed


class PathRule:
    def __init__(self, pattern: str, rule_type: str) -> None:
        self.pattern = pattern
        self.rule_type = rule_type  # "allow" | "disallow"

    def matches(self, path: str) -> bool:
        if not self.pattern:
            return False

        if self.pattern.endswith("$"):
            return path == self.pattern[:-1]
        elif "*" in self.pattern:
            # RFC 9309 pattern matching
            pat = self.pattern if self.pattern.endswith("*") else self.pattern + "*"
            return fnmatch.fnmatch(path, pat)
        else:
            return path.startswith(self.pattern)


class UserAgentGroup:
    def __init__(self) -> None:
        self.user_agents: list[str] = []
        self.rules: list[PathRule] = []
        self.crawl_delay: float | None = None
        self.content_signals: list[ContentSignal] = []


class RobotsPolicy:
    def __init__(
        self,
        groups: list[UserAgentGroup],
        sitemaps: list[str],
        global_content_signals: list[ContentSignal] | None = None,
        ttl_seconds: float = 86400.0,
    ) -> None:
        self.groups = groups
        self.sitemaps = sitemaps
        self.global_content_signals = global_content_signals or []
        self.cached_at = time.time()
        self.ttl = ttl_seconds

    @classmethod
    def parse(cls, text: str) -> RobotsPolicy:
        clean_text = text.lstrip("\ufeff")
        groups: list[UserAgentGroup] = []
        sitemaps: list[str] = []
        global_content_signals: list[ContentSignal] = []

        cur_uas: list[str] = []
        cur_rules: list[PathRule] = []
        cur_delay: float | None = None
        cur_signals: list[ContentSignal] = []

        for raw_line in clean_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue

            key, val = (x.strip() for x in line.split(":", 1))
            k_lower = key.lower()

            if k_lower == "user-agent":
                if cur_rules or cur_delay is not None or cur_signals:
                    if cur_uas:
                        grp = UserAgentGroup()
                        grp.user_agents = list(cur_uas)
                        grp.rules = list(cur_rules)
                        grp.crawl_delay = cur_delay
                        grp.content_signals = list(cur_signals)
                        groups.append(grp)
                    cur_uas.clear()
                    cur_rules.clear()
                    cur_delay = None
                    cur_signals.clear()
                cur_uas.append(val.lower())
            elif k_lower == "disallow":
                if not val:
                    # Empty disallow clears previous disallows per RFC 9309
                    cur_rules = [r for r in cur_rules if r.rule_type != "disallow"]
                else:
                    cur_rules.append(PathRule(val, "disallow"))
            elif k_lower == "allow":
                if val:
                    cur_rules.append(PathRule(val, "allow"))
            elif k_lower == "content-signal":
                for pair in val.split(","):
                    trimmed = pair.strip()
                    if "=" in trimmed:
                        k, v = (x.strip().lower() for x in trimmed.split("=", 1))
                        if k:
                            sig = ContentSignal(k, v)
                            cur_signals.append(sig)
                            global_content_signals.append(sig)
            elif k_lower == "crawl-delay":
                try:
                    cur_delay = float(val)
                except ValueError:
                    pass
            elif k_lower == "sitemap":
                if val:
                    sitemaps.append(val)

        if cur_uas:
            grp = UserAgentGroup()
            grp.user_agents = list(cur_uas)
            grp.rules = list(cur_rules)
            grp.crawl_delay = cur_delay
            grp.content_signals = list(cur_signals)
            groups.append(grp)

        return cls(groups=groups, sitemaps=sitemaps, global_content_signals=global_content_signals)

    def get_content_signals(self, user_agent: str) -> list[ContentSignal]:
        ua_clean = user_agent.lower()
        token = extract_product_token(ua_clean).lower()

        # Check if agent-specific group defined signals
        for g in self.groups:
            if any(u != "*" and (u in ua_clean or token in u) for u in g.user_agents):
                if g.content_signals:
                    return list(g.content_signals)

        # Fallback to wildcard group signals
        for g in self.groups:
            if any(u == "*" for u in g.user_agents):
                if g.content_signals:
                    return list(g.content_signals)

        return list(self.global_content_signals)

    def is_allowed(self, path: str, user_agent: str) -> bool:
        ua_clean = user_agent.lower()
        token = extract_product_token(ua_clean).lower()

        # Find matching group: exact or token match first, fallback to wildcard '*'
        matched_group: UserAgentGroup | None = None
        for g in self.groups:
            if any(u != "*" and (u in ua_clean or token in u) for u in g.user_agents):
                matched_group = g
                break

        if matched_group is None:
            for g in self.groups:
                if any(u == "*" for u in g.user_agents):
                    matched_group = g
                    break

        if matched_group is None:
            return True

        best_len = 0
        decision = True

        for rule in matched_group.rules:
            if rule.matches(path):
                rlen = len(rule.pattern)
                # Longest match wins. On tie, Allow wins per RFC 9309.
                if rlen > best_len:
                    best_len = rlen
                    decision = rule.rule_type == "allow"
                elif rlen == best_len and rule.rule_type == "allow":
                    decision = True

        return decision

    def is_fresh(self) -> bool:
        return (time.time() - self.cached_at) < self.ttl


class RobotsStore:
    def __init__(self) -> None:
        self._policies: dict[str, RobotsPolicy] = {}

    def set(self, origin: str, policy: RobotsPolicy) -> None:
        self._policies[origin.rstrip("/")] = policy

    def get(self, origin: str) -> RobotsPolicy | None:
        key = origin.rstrip("/")
        policy = self._policies.get(key)
        if policy and policy.is_fresh():
            return policy
        elif policy:
            self._policies.pop(key, None)
        return None

    def check_url(self, url: str, user_agent: str = "dosh-browser") -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        policy = self.get(origin)
        if policy is None:
            return True
        return policy.is_allowed(parsed.path or "/", user_agent)
