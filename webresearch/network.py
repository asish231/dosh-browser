"""network.py — Central Network Coordinator Pipeline for Dosh-Browser (Python).

Integrates all network capabilities into an orchestrated, agent-grade pipeline:
1. SSRF & Private IP Guard (IpFilter)
2. SafeBrowsing & Malicious Host Filtering
3. Tor / .onion Address Refusal
4. Third-Party Tracker & Ad Blocking (TrackerGraph)
5. RFC 9309 Robots.txt Compliance (RobotsStore)
6. Origin Rate Limiting via GCRA Leaky Bucket (RateLimiter)
7. W3C Cross-Origin Resource Sharing (CorsGate & CorsStore)
8. RFC 9111 HTTP Cache with SQLite Persistence (SqliteHttpCache)
9. Concurrent Request Coalescing (SingleFlight)
10. RFC 9421 Cryptographic Bot Authentication (WebBotAuth)
11. Chrome 124 TLS matching transport (curl_cffi / httpx)
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from webresearch.engine.cache_sqlite import SqliteHttpCache
from webresearch.engine.cors import CorsGate
from webresearch.engine.ip_filter import IpFilter, IpFilterError
from webresearch.engine.onion import onion_refusal
from webresearch.engine.rate_limiter import RateLimiter
from webresearch.engine.robots import RobotsStore
from webresearch.engine.safebrowsing import blocked_result, is_blocked_host
from webresearch.engine.singleflight import SingleFlight
from webresearch.engine.trackers import TrackerGraph
from webresearch.engine.web_bot_auth import BotAuthConfig, sign_request
from webresearch.types import FetchResult

logger = logging.getLogger("webresearch.network")


class NetworkEngine:
    """Unified network pipeline coordinator for the Python browser lane."""

    def __init__(
        self,
        *,
        block_private_ips: bool = True,
        enable_rate_limiting: bool = True,
        rate_limit_interval_ms: float = 50.0,
        rate_limit_burst: int = 5,
        enable_cache: bool = True,
        enable_robots: bool = False,
        enable_trackers_blocking: bool = True,
        bot_auth: BotAuthConfig | None = None,
        db_path: str | None = None,
    ) -> None:
        self.block_private_ips = block_private_ips
        self.enable_rate_limiting = enable_rate_limiting
        self.enable_cache = enable_cache
        self.enable_robots = enable_robots
        self.enable_trackers_blocking = enable_trackers_blocking
        self.bot_auth = bot_auth

        self.ip_filter = IpFilter(block_private=block_private_ips)
        self.rate_limiter = RateLimiter(interval_ms=rate_limit_interval_ms, burst=rate_limit_burst)
        self.singleflight = SingleFlight()
        self.robots = RobotsStore()
        self.cors = CorsGate()
        self.cache = SqliteHttpCache(db_path)
        self.trackers = TrackerGraph()

    async def fetch(
        self,
        url: str,
        *,
        client: httpx.AsyncClient,
        method: str = "GET",
        data: Any = None,
        json_body: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        client_origin: str | None = None,
        is_subresource: bool = False,
        max_bytes: int = 8_000_000,
        user_agent: str | None = None,
    ) -> FetchResult:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        # 1. Tor .onion check
        if hostname.endswith(".onion"):
            status, reason = onion_refusal(hostname)
            return FetchResult(
                url=url,
                final_url=url,
                status=status,
                content_type="text/plain",
                kind="other",
                body=reason,
                headers={"x-webresearch-blocked": "onion"},
            )

        # 2. SafeBrowsing check
        if is_blocked_host(hostname):
            return blocked_result(url)

        # 3. SSRF & Private IP Guard
        try:
            self.ip_filter.check_url(url)
        except IpFilterError as err:
            return FetchResult(
                url=url,
                final_url=url,
                status=403,
                content_type="text/plain",
                kind="other",
                body=f"Request blocked by SSRF filter: {err}",
                headers={"x-webresearch-blocked": "ssrf"},
            )

        # 4. Third-Party Tracker / Ad Blocking (subresources)
        if is_subresource and self.enable_trackers_blocking:
            context_url = client_origin or url
            decision = self.trackers.evaluate(context_url, url, is_subresource=True)
            if decision.status == "BLOCKED":
                if decision.surrogate:
                    return FetchResult(
                        url=url,
                        final_url=url,
                        status=200,
                        content_type="application/javascript",
                        kind="other",
                        body=decision.surrogate,
                        headers={"x-webresearch-surrogate": decision.entity},
                    )
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=204,
                    content_type="text/plain",
                    kind="other",
                    body="",
                    headers={"x-webresearch-blocked": "tracker"},
                )

        # 5. Robots.txt Compliance Check
        if self.enable_robots:
            ua_str = user_agent or "dosh-browser"
            if not self.robots.check_url(url, ua_str):
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=403,
                    content_type="text/plain",
                    kind="other",
                    body=f"Path {parsed.path} disallowed by robots.txt",
                    headers={"x-webresearch-blocked": "robots.txt"},
                )

        # 6. Origin Rate Limiter (GCRA)
        if self.enable_rate_limiting and hostname:
            await self.rate_limiter.acquire(hostname)

        # 7. W3C CORS Check
        if client_origin:
            req_headers = headers or {}
            decision = self.cors.evaluate_request(client_origin, url, method, req_headers)
            # In simple or cached mode, continue; preflight logic would send OPTIONS
            if decision == "preflight_required":
                # Send preflight OPTIONS
                await self._execute_preflight(client, client_origin, url)

        # 8. RFC 9111 HTTP Cache Lookup
        req_headers_dict = dict(headers or {})
        stale_cached = None
        if self.enable_cache and method.upper() == "GET":
            cached = self.cache.get(url)
            if cached:
                if cached["is_fresh"]:
                    ct = cached["headers"].get("content-type", "text/html")
                    from webresearch.fetch import classify
                    body_str = cached["body"].decode("utf-8", errors="replace")
                    return FetchResult(
                        url=url,
                        final_url=cached["url"],
                        status=cached["status"],
                        content_type=ct,
                        kind=classify(ct, cached["body"]),
                        body=body_str,
                        raw=cached["body"],
                        headers={**cached["headers"], "x-dosh-cache": "HIT"},
                    )
                else:
                    stale_cached = cached
                    if cached.get("etag"):
                        req_headers_dict["if-none-match"] = cached["etag"]
                    if cached.get("last_modified"):
                        req_headers_dict["if-modified-since"] = cached["last_modified"]

        # 9. SingleFlight Request Coalescing
        sf_key = f"{method.upper()}:{url}"

        async def _dispatch() -> FetchResult:
            # Apply RFC 9421 Bot Authentication Signatures
            if self.bot_auth:
                signed = sign_request(self.bot_auth, method, url, authority=hostname)
                req_headers_dict.update(signed)

            from webresearch.fetch import fetch as raw_fetch
            res = await raw_fetch(
                url,
                client=client,
                method=method,
                data=data,
                json_body=json_body,
                files=files,
                headers=req_headers_dict,
                max_bytes=max_bytes,
            )

            # Handle 304 Not Modified
            if res.status == 304 and stale_cached:
                self.cache.store(
                    url,
                    stale_cached["status"],
                    stale_cached["headers"],
                    stale_cached["body"],
                )
                ct = stale_cached["headers"].get("content-type", "text/html")
                from webresearch.fetch import classify
                body_str = stale_cached["body"].decode("utf-8", errors="replace")
                return FetchResult(
                    url=url,
                    final_url=res.final_url,
                    status=200,
                    content_type=ct,
                    kind=classify(ct, stale_cached["body"]),
                    body=body_str,
                    raw=stale_cached["body"],
                    headers={**stale_cached["headers"], "x-dosh-cache": "REVALIDATED"},
                )

            # Cache eligible 200 OK responses
            if self.enable_cache and res.status == 200 and method.upper() == "GET" and not res.truncated:
                raw_bytes = res.raw if res.raw is not None else res.body.encode("utf-8")
                self.cache.store(url, res.status, res.headers, raw_bytes)

            # Validate CORS headers if client origin provided
            if client_origin and not self.cors.is_same_origin(client_origin, url):
                try:
                    self.cors.validate_response(client_origin, False, res.headers)
                except Exception as cors_err:
                    return FetchResult(
                        url=url,
                        final_url=res.final_url,
                        status=0,
                        content_type="text/plain",
                        kind="other",
                        body=f"CORS validation failed: {cors_err}",
                        headers={"x-webresearch-blocked": "cors"},
                    )

            return res

        return await self.singleflight.execute(sf_key, _dispatch)

    async def _execute_preflight(
        self, client: httpx.AsyncClient, client_origin: str, target_url: str
    ) -> None:
        try:
            resp = await client.request(
                "OPTIONS",
                target_url,
                headers={
                    "origin": client_origin,
                    "access-control-request-method": "GET",
                },
            )
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            target_origin = f"{urlparse(target_url).scheme}://{urlparse(target_url).netloc}"
            self.cors.store.record_preflight(client_origin, target_origin, resp_headers)
        except Exception as e:
            logger.debug("CORS preflight request failed: %s", e)


# Global default engine instance
DEFAULT_NETWORK_ENGINE = NetworkEngine()

