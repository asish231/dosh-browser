"""tests/test_network_pipeline.py — Verification of Python Network Pipeline.

Tests:
- IpFilter: SSRF prevention, cloud metadata defense, private IP & internal domain blocks.
- SingleFlight: Concurrent request deduplication.
- RateLimiter: GCRA token-bucket per-origin rate limiting.
- RobotsStore: RFC 9309 robots.txt parser and policy gate.
- CorsGate: W3C CORS preflight checks and store.
- WebBotAuth: RFC 9421 HTTP Message Signatures.
- SqliteHttpCache: Persistent SQLite-backed RFC 9111 HTTP caching.
- NetworkEngine: Unified network coordinator.
"""

import asyncio
import ipaddress
import time
import pytest
from urllib.parse import urlparse

from webresearch.engine.cache_sqlite import SqliteHttpCache
from webresearch.engine.cors import CorsGate, CorsError
from webresearch.engine.ip_filter import IpFilter, IpFilterError
from webresearch.engine.rate_limiter import RateLimiter
from webresearch.engine.robots import RobotsPolicy, RobotsStore
from webresearch.engine.singleflight import SingleFlight
from webresearch.engine.web_bot_auth import BotAuthConfig, sign_request
from webresearch.network import NetworkEngine


# ============================================================================
# 1. IpFilter (SSRF Defense)
# ============================================================================

def test_ip_filter_loopback_and_metadata():
    filter_ = IpFilter(block_private=True)

    # 127.0.0.1 blocked
    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("127.0.0.1"))

    # AWS/GCP/Azure instance metadata 169.254.169.254 blocked
    with pytest.raises(IpFilterError, match="cloud metadata"):
        filter_.is_ip_allowed(ipaddress.ip_address("169.254.169.254"))

    # RFC 1918 subnets blocked
    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("10.0.1.25"))

    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("172.16.5.4"))

    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("192.168.1.1"))

    # IPv6 Link-Local and ULA blocked
    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("fe80::1"))

    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(ipaddress.ip_address("fc00::1"))

    # Public IP allowed
    filter_.is_ip_allowed(ipaddress.ip_address("8.8.8.8"))
    filter_.is_ip_allowed(ipaddress.ip_address("1.1.1.1"))


def test_ip_filter_hostnames():
    filter_ = IpFilter(block_private=True)
    with pytest.raises(IpFilterError, match="internal hostname"):
        filter_.is_host_allowed("localhost")

    with pytest.raises(IpFilterError, match="internal hostname"):
        filter_.is_host_allowed("service.local")

    with pytest.raises(IpFilterError, match="internal hostname"):
        filter_.is_host_allowed("api.internal")

    with pytest.raises(IpFilterError, match="internal hostname"):
        filter_.is_host_allowed("metadata.google.internal")

    # Public hosts allowed
    filter_.is_host_allowed("example.com")
    filter_.is_host_allowed("api.github.com")


def test_ip_filter_custom_cidrs():
    filter_ = IpFilter(block_private=True)
    filter_.add_allow_cidr("10.50.0.0/16")
    # Allowed via custom CIDR
    filter_.is_ip_allowed(ipaddress.ip_address("10.50.1.1"))
    # Other 10.x.x.x still blocked
    with pytest.raises(IpFilterError):
        filter_.is_ip_allowed(ipaddress.ip_address("10.51.1.1"))


# ============================================================================
# 2. SingleFlight (Request Coalescing)
# ============================================================================

@pytest.mark.asyncio
async def test_singleflight_coalescing():
    sf = SingleFlight()
    counter = 0

    async def worker():
        nonlocal counter
        counter += 1
        await asyncio.sleep(0.05)
        return "asset_payload"

    # Launch 5 concurrent calls for the exact same key
    results = await asyncio.gather(*(sf.execute("asset.js", worker) for _ in range(5)))

    for r in results:
        assert r == "asset_payload"

    # The actual worker coroutine must have run exactly once!
    assert counter == 1
    assert sf.total_calls == 5
    assert sf.coalesced_calls == 4


# ============================================================================
# 3. RateLimiter (GCRA Leaky Bucket)
# ============================================================================

@pytest.mark.asyncio
async def test_rate_limiter_burst_and_spacing():
    # 50ms interval, burst of 3
    limiter = RateLimiter(interval_ms=50.0, burst=3)
    host = "api.example.com"

    # First 3 should proceed with 0 delay (burst capacity)
    assert (await limiter.check(host)) is None
    assert (await limiter.check(host)) is None
    assert (await limiter.check(host)) is None

    # 4th exceeds burst and must wait
    wait_time = await limiter.check(host)
    assert wait_time is not None
    assert wait_time >= 0.02


@pytest.mark.asyncio
async def test_rate_limiter_host_isolation():
    limiter = RateLimiter(interval_ms=50.0, burst=1)
    host1 = "host1.com"
    host2 = "host2.com"

    assert (await limiter.check(host1)) is None
    # host1 is throttled
    assert (await limiter.check(host1)) is not None

    # host2 is completely unblocked
    assert (await limiter.check(host2)) is None


# ============================================================================
# 4. RobotsStore (RFC 9309 Compliance)
# ============================================================================

ROBOTS_TXT = """
# Test robots.txt
User-agent: Googlebot
Disallow: /nogoogle/

User-agent: dosh-browser
Disallow: /admin/
Allow: /admin/public/
Crawl-delay: 1.5

User-agent: *
Disallow: /secret/
Allow: /secret/open$
"""

def test_robots_policy_evaluation():
    policy = RobotsPolicy.parse(ROBOTS_TXT)

    # Client identifies as dosh-browser
    ua = "dosh-browser/1.0"
    assert not policy.is_allowed("/admin/dashboard", ua)
    # Most specific rule wins
    assert policy.is_allowed("/admin/public/page", ua)
    assert policy.is_allowed("/unrestricted", ua)

    # Generic client falls back to wildcard
    assert not policy.is_allowed("/secret/file.pdf", "GenericBot")
    assert policy.is_allowed("/secret/open", "GenericBot")


# ============================================================================
# 5. CorsGate (W3C CORS Preflight & Evaluation)
# ============================================================================

def test_cors_simple_vs_preflight():
    gate = CorsGate()
    client = "https://app.com"
    target = "https://api.partner.com/items"

    # Simple GET with text/plain
    d1 = gate.evaluate_request(client, target, "GET", {"accept": "text/plain"})
    assert d1 == "simple"

    # Complex request with custom authorization header
    d2 = gate.evaluate_request(client, target, "GET", {"authorization": "Bearer token"})
    assert d2 == "preflight_required"

    # PUT always requires preflight
    d3 = gate.evaluate_request(client, target, "PUT", {})
    assert d3 == "preflight_required"


def test_cors_preflight_caching():
    gate = CorsGate()
    client = "https://app.com"
    target = "https://api.partner.com/items"

    preflight_headers = {
        "access-control-allow-methods": "GET, POST, PUT",
        "access-control-allow-headers": "authorization",
        "access-control-max-age": "60",
    }
    gate.store.record_preflight(client, "https://api.partner.com", preflight_headers)

    d = gate.evaluate_request(client, target, "PUT", {"authorization": "Bearer token"})
    assert d == "preflight_cached"


def test_cors_response_validation():
    client = "https://app.com"

    # Wildcard allowed without credentials
    gate = CorsGate()
    gate.validate_response(client, False, {"access-control-allow-origin": "*"})

    # Wildcard rejected with credentials
    with pytest.raises(CorsError, match="Cannot use wildcard"):
        gate.validate_response(client, True, {"access-control-allow-origin": "*"})

    # Exact origin match
    gate.validate_response(client, True, {"access-control-allow-origin": "https://app.com"})


# ============================================================================
# 6. WebBotAuth (RFC 9421 Signatures)
# ============================================================================

def test_web_bot_auth_hmac_signing():
    config = BotAuthConfig(
        key_id="agent-key-1",
        agent_identifier="dosh-browser-agent/1.0",
        secret_key=b"secret-shared-key-12345",
        algorithm="hmac-sha256",
    )

    signed = sign_request(
        config,
        "GET",
        "https://api.example.com/feed",
        authority="api.example.com",
        timestamp=1712000000,
    )

    assert signed["signature-agent"] == "dosh-browser-agent/1.0"
    assert 'created=1712000000' in signed["signature-input"]
    assert 'keyid="agent-key-1"' in signed["signature-input"]
    assert signed["signature"].startswith("sig1=:")


# ============================================================================
# 7. SqliteHttpCache (RFC 9111 Persistent Caching)
# ============================================================================

def test_sqlite_cache_lifecycle():
    cache = SqliteHttpCache()
    url = "https://example.com/api/v1/profile"

    headers = {
        "cache-control": "max-age=3600",
        "etag": '"rev-123"',
        "content-type": "application/json",
    }
    cache.store(url, 200, headers, b'{"user": "alice"}')

    cached = cache.get(url)
    assert cached is not None
    assert cached["status"] == 200
    assert cached["body"] == b'{"user": "alice"}'
    assert cached["is_fresh"] is True

    cache.invalidate(url)
    assert cache.get(url) is None


# ============================================================================
# 8. NetworkEngine (Central Pipeline Coordination)
# ============================================================================

@pytest.mark.asyncio
async def test_network_engine_ssrf_blocked():
    engine = NetworkEngine(block_private_ips=True)

    # Metadata service blocked
    res1 = await engine.fetch("http://169.254.169.254/latest/meta-data/", client=None)
    assert res1.status == 403
    assert res1.headers.get("x-webresearch-blocked") == "ssrf"

    # Localhost blocked
    res2 = await engine.fetch("http://localhost:8080/admin", client=None)
    assert res2.status == 403
    assert res2.headers.get("x-webresearch-blocked") == "ssrf"


@pytest.mark.asyncio
async def test_network_engine_onion_blocked():
    engine = NetworkEngine()
    res = await engine.fetch(
        "http://mkxyobdwj6xy5kbpyyootrgdscfwzol5iy5ggtu6lb6xzcc5wdxynwad.onion/index.html",
        client=None,
    )
    assert res.status == 403
    assert res.headers.get("x-webresearch-blocked") == "onion"


@pytest.mark.asyncio
async def test_network_engine_robots_disallow():
    engine = NetworkEngine(enable_robots=True, block_private_ips=False)
    origin = "https://restricted.example.com"
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /disallowed/\n")
    engine.robots.set(origin, policy)

    res = await engine.fetch("https://restricted.example.com/disallowed/doc.html", client=None)
    assert res.status == 403
    assert res.headers.get("x-webresearch-blocked") == "robots.txt"
