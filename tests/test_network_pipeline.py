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
from webresearch.engine.cors import (
    CorsGate,
    CorsError,
    is_cors_unsafe_byte,
    has_no_cors_unsafe_bytes,
    is_safelisted_header,
    is_safelisted_language_value,
    is_safelisted_content_type,
)
from webresearch.engine.ip_filter import IpFilter, IpFilterError
from webresearch.engine.rate_limiter import RateLimiter
from webresearch.engine.robots import RobotsPolicy, RobotsStore
from webresearch.engine.singleflight import SingleFlight
from webresearch.engine.web_bot_auth import BotAuthConfig, sign_request, sign_web_bot_auth
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


def test_ip_filter_nat64_and_cidr_list_syntax():
    filter_ = IpFilter(block_private=True)

    # NAT64 embedded loopback 64:ff9b::127.0.0.1
    nat64_loopback = ipaddress.ip_address("64:ff9b::7f00:1")
    with pytest.raises(IpFilterError):
        filter_.is_ip_allowed(nat64_loopback)

    # NAT64 embedded metadata 64:ff9b::169.254.169.254
    nat64_metadata = ipaddress.ip_address("64:ff9b::a9fe:a9fe")
    with pytest.raises(IpFilterError, match="cloud metadata"):
        filter_.is_ip_allowed(nat64_metadata)

    # Local-use NAT64 64:ff9b:1::/48
    nat64_local = ipaddress.ip_address("64:ff9b:1::1")
    with pytest.raises(IpFilterError, match="private IP"):
        filter_.is_ip_allowed(nat64_local)

    # Zone ID stripping
    with pytest.raises(IpFilterError):
        filter_.is_host_allowed("[fe80::1%eth0]")

    # CIDR list parsing with '-' allowlist override syntax
    filter2 = IpFilter(block_private=True)
    filter2.parse_cidr_list("10.0.0.0/8,-10.0.0.42/32,192.168.0.0/16")
    # 10.0.0.42 exempted
    filter2.is_ip_allowed(ipaddress.ip_address("10.0.0.42"))
    # 10.0.0.43 blocked
    with pytest.raises(IpFilterError):
        filter2.is_ip_allowed(ipaddress.ip_address("10.0.0.43"))


def test_cors_whatwg_safelist_rules():
    assert is_cors_unsafe_byte(0x00)
    assert is_cors_unsafe_byte(0x08)
    assert is_cors_unsafe_byte(ord('"'))
    assert is_cors_unsafe_byte(ord('<'))
    assert is_cors_unsafe_byte(127)
    assert not is_cors_unsafe_byte(ord('a'))

    assert not has_no_cors_unsafe_bytes('text/html; charset="utf-8"')
    assert has_no_cors_unsafe_bytes('text/html; charset=utf-8')

    # Max length <= 128
    assert is_safelisted_header("accept", "a" * 128)
    assert not is_safelisted_header("accept", "a" * 129)

    # Language tags
    assert is_safelisted_language_value("en-US,en;q=0.9,fr;q=0.8")
    assert not is_safelisted_language_value("en-US\nEvil: 1")

    # Content types
    assert is_safelisted_content_type("text/plain")
    assert is_safelisted_content_type("application/x-www-form-urlencoded; charset=utf-8")
    assert is_safelisted_content_type("multipart/form-data; boundary=123")
    assert not is_safelisted_content_type("application/json")


def test_robots_content_signals_and_utf8_bom():
    text_with_bom = "\ufeffUser-agent: *\nContent-Signal: ai-train=no, search=yes\nDisallow: /private/\n\nUser-agent: Dosh\nContent-Signal: ai-train=yes\nDisallow:\n"
    policy = RobotsPolicy.parse(text_with_bom)

    # Generic bot gets wildcard signals
    gen_signals = policy.get_content_signals("curl/7.68.0")
    assert len(gen_signals) == 2
    assert gen_signals[0].name == "ai-train"
    assert gen_signals[0].value == "no"

    # Dosh gets specific signal
    dosh_signals = policy.get_content_signals("Dosh/1.0")
    assert len(dosh_signals) == 1
    assert dosh_signals[0].name == "ai-train"
    assert dosh_signals[0].value == "yes"

    # Empty Disallow: clears prior disallows
    assert policy.is_allowed("/private/secret", "Dosh/1.0")


def test_rate_limiter_reserve_gcra():
    rl = RateLimiter(interval_ms=100.0, burst=1)
    # 1.0s = 1000ms
    assert rl.reserve("a.test:443", 1.0) == 1.0
    # Second reservation spaced by 100ms
    assert round(rl.reserve("a.test:443", 1.0), 3) == 1.1
    # Third reservation at 1.05s spaced to 1.2s
    assert round(rl.reserve("a.test:443", 1.05), 3) == 1.2
    # Independent host starts now
    assert rl.reserve("b.test", 1.0) == 1.0


def test_web_bot_auth_cloudflare_convention():
    config = BotAuthConfig(
        key_id="cf-bot-key-1",
        agent_identifier="dosh-agent.io",
        secret_key=b"fixed-secret-key-at-least-32-bytes-long!",
        algorithm="hmac-sha256",
    )
    signed = sign_web_bot_auth(config, '"api.example.com"', timestamp=1712000000)
    assert signed["signature-agent"] == '"https://dosh-agent.io"'
    assert "created=1712000000" in signed["signature-input"]
    assert "expires=1712000060" in signed["signature-input"]
    assert 'tag="web-bot-auth"' in signed["signature-input"]
    assert "nonce=" in signed["signature-input"]
    assert signed["signature"].startswith("sig1=:")

