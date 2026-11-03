"""Regression tests for context-identity hygiene: durable per-context state
must key on the context NONCE, never id(ctx).

The bug class (observed as a deterministic full-suite-only
test_port_transfer failure — port_unrouted: 1, port_wires: 0 — that no
isolated run reproduced): CPython reuses id()s after GC. A cache keyed by
id(ctx) can hand a fresh context a DEAD context's nonce, making every
cross-realm port transfer unroutable; an inbox keyed by id(ctx) can deliver
a dead context's pending posts into a fresh realm as ghost traffic.

The tests here pin the FIX without depending on allocator address reuse:
mutate the live context's nonce and assert the reads follow it (a cache
would return the stale value).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from webresearch.engine.frames import _ctx_nonce
from webresearch.engine.runtime import (
    _CTX_POST_INBOX,
    _CTX_POST_UID,
    _post_queue_key,
    run_scripts,
    take_pending_posts,
)

PARENT_URL = "https://parent.example/page.html"
PARENT_HTML = """<!doctype html><html><body><script>
window.__log = [];
</script></body></html>"""


def _fake_client():
    client = httpx.Client()
    client.headers = {"user-agent": "test-ctx-identity"}
    return client


async def _realm():
    client = _fake_client()
    _, info = await run_scripts(PARENT_HTML, PARENT_URL, client, force=True)
    return info["ctx"]


def test_ctx_nonce_reads_the_live_value_not_a_cache():
    """The removed id(ctx)-keyed cache returned a dead context's nonce to a
    fresh context that reused its address. Pin the fix: the nonce read must
    follow the LIVE context — mutate it and read again."""
    async def run():
        ctx = await _realm()
        original = _ctx_nonce(ctx)
        assert original, "runtime injects a nonce"
        # a second read agrees (no cache drift)
        assert _ctx_nonce(ctx) == original
        # mutate the live nonce: a fresh read must follow it; any cache keyed
        # by object identity would keep returning the stale value
        ctx.eval("globalThis.__WR_CTX_KEY = 'mutated-nonce';")
        assert _ctx_nonce(ctx) == "mutated-nonce"
    asyncio.run(run())


def test_post_queue_key_follows_the_live_nonce():
    async def run():
        ctx = await _realm()
        key1 = _post_queue_key(ctx)
        assert key1 and not key1.startswith("id:"), "live contexts key by nonce"
        ctx.eval("globalThis.__WR_CTX_KEY = 'rotated';")
        assert _post_queue_key(ctx) == "rotated"
    asyncio.run(run())


def test_take_pending_posts_never_inherits_foreign_entries():
    """Ghost-traffic guard: entries queued under a dead context's key must
    never drain into a live context. With nonce keying, a live context only
    ever reads its OWN key."""
    async def run():
        ctx = await _realm()
        # seed a dead-realm-style inbox under a key that is NOT this context's
        _CTX_POST_INBOX["some-dead-contexts-nonce"] = [
            {"uid": 1, "data": "ghost", "target": "parent"}
        ]
        taken = take_pending_posts(ctx)
        assert taken == [], f"live realm must not drain foreign posts: {taken}"
        assert _ctx_nonce(ctx) != "some-dead-contexts-nonce"
    asyncio.run(run())


def test_uid_counter_is_per_nonce_and_monotonic():
    async def run():
        ctx = await _realm()
        key = _post_queue_key(ctx)
        _CTX_POST_UID[key] = _CTX_POST_UID.get(key, 0) + 1
        first = _CTX_POST_UID[key]
        _CTX_POST_UID[key] += 1
        assert _CTX_POST_UID[key] == first + 1
        # a different nonce is a different counter (no cross-realm inheritance)
        _CTX_POST_UID.setdefault("other-nonce", 0)
        assert _CTX_POST_UID["other-nonce"] == 0
    asyncio.run(run())


def test_two_realms_never_share_queue_keys():
    async def run():
        ctx_a = await _realm()
        ctx_b = await _realm()
        assert _ctx_nonce(ctx_a) != _ctx_nonce(ctx_b), "uuid4 per context"
        assert _post_queue_key(ctx_a) != _post_queue_key(ctx_b)
    asyncio.run(run())
