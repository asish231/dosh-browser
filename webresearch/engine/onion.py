"""Onion address handling, following tor's validation rules.

v3 addresses are base32(PUBKEY(32) || CHECKSUM(2) || VERSION(1)), 56 chars,
with CHECKSUM = SHA3-256(".onion checksum" || PUBKEY || VERSION)[0:2]
(see tor src/feature/hs/hs_common.c: build_hs_checksum/hs_address_is_valid).

This engine has no Tor transport, so .onion hosts are never fetched: resolving
them over clearnet DNS would leak the destination. Malformed addresses are
rejected as invalid; well-formed ones are refused with an explicit
requires-tor-transport reason.
"""

from __future__ import annotations

import base64
import hashlib

_VERSION = b"\x03"
_CHECKSUM_PREFIX = b".onion checksum"


def parse_v3_onion(host: str) -> bytes | None:
    """Return the 32-byte ed25519 pubkey if the v3 address is well-formed
    with a valid checksum, else None."""
    host = (host or "").lower().rstrip(".")
    if host.endswith(".onion"):
        host = host[: -len(".onion")]
    if len(host) != 56:
        return None
    try:
        raw = base64.b32decode(host.upper())
    except Exception:
        return None
    if len(raw) != 35 or raw[34:35] != _VERSION:
        return None
    pubkey, checksum = raw[:32], raw[32:34]
    expected = hashlib.sha3_256(_CHECKSUM_PREFIX + pubkey + _VERSION).digest()[:2]
    if checksum != expected:
        return None
    return pubkey


def onion_refusal(host: str) -> tuple[int, str]:
    if parse_v3_onion(host) is None:
        return 403, f"invalid .onion address: {host}"
    return 403, (
        f".onion host requires a Tor transport, which this engine does not "
        f"have; refusing to resolve {host} over clearnet DNS"
    )
