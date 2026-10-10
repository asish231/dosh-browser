"""Certificate Transparency: SCT extraction + signature verification.

Parses the SignedCertificateTimestampList (OID 1.3.6.1.4.1.11129.2.4.2) out of
a DER certificate, and verifies each SCT's ECDSA P-256 signature (RFC 6962
§3.2 digitally-signed struct) against pinned log keys. Log keys come from
WEBRESEARCH_CT_LOGS as JSON {base64-log-id: PEM-public-key}. Results are
reported per SCT; nothing is assumed verified.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

SCT_LIST_OID = b"\x06\x08\x2b\x06\x01\x04\x01\xd6\x79\x02\x04\x02"


def _der_read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    if pos + 2 > len(data):
        raise ValueError("short DER")
    tag = data[pos]
    first = data[pos + 1]
    pos += 2
    if first & 0x80:
        n = first & 0x7F
        if n == 0 or pos + n > len(data):
            raise ValueError("bad DER length")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    else:
        length = first
    if pos + length > len(data):
        raise ValueError("DER overrun")
    return tag, data[pos:pos + length], pos + length


def _der_children(data: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    pos = 0
    while pos < len(data):
        tag, body, pos = _der_read_tlv(data, pos)
        out.append((tag, body))
    return out


def find_sct_extension(der_cert: bytes) -> bytes | None:
    """Return the raw SignedCertificateTimestampList bytes, or None."""
    try:
        top_tag, top_body, _ = _der_read_tlv(der_cert, 0)
        if top_tag != 0x30:
            return None
        tbs = None
        for tag, body in _der_children(top_body):
            if tag == 0xA0:
                inner = _der_children(body)
                if inner and inner[0][0] == 0x02:
                    tbs = body
                    break
            if tag == 0x30:
                tbs = body
                break
        if tbs is None:
            # Fall back: search whole cert for the OID + OCTET STRING.
            hay = der_cert
        else:
            hay = tbs
        idx = hay.find(SCT_LIST_OID)
        if idx < 0:
            idx = der_cert.find(SCT_LIST_OID)
            hay = der_cert
            if idx < 0:
                return None
        pos = idx + len(SCT_LIST_OID)
        tag, body, _ = _der_read_tlv(hay, pos)
        if tag != 0x04:
            return None
        # OCTET STRING holds the opaque TransItem list (2-byte length prefix).
        if len(body) < 2:
            return None
        (inner_len,) = struct.unpack(">H", body[:2])
        return body[2:2 + inner_len]
    except Exception:
        return None


def parse_sct_list(raw: bytes) -> list[dict]:
    scts: list[dict] = []
    pos = 0
    if len(raw) < 2:
        return scts
    (total,) = struct.unpack(">H", raw[:2])
    pos = 2
    end = pos + total
    while pos + 2 <= min(end, len(raw)):
        (ln,) = struct.unpack(">H", raw[pos:pos + 2])
        pos += 2
        blob = raw[pos:pos + ln]
        pos += ln
        if len(blob) < 43:
            continue
        sig_alg = blob[43:45] if len(blob) >= 45 else b""
        sig_len = struct.unpack(">H", blob[45:47])[0] if len(blob) >= 47 else 0
        signature = blob[47:47 + sig_len]
        scts.append({
            "version": blob[0],
            "log_id": blob[1:33],
            "timestamp": struct.unpack(">Q", blob[33:41])[0],
            "extensions": blob[41:43],
            "sig_alg": sig_alg,
            "signature": signature,
            "raw": blob,
        })
    return scts


# --- P-256 ECDSA (verify only) ---
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_A = _P - 3


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        s = (3 * x1 * x1 + _A) * _inv(2 * y1) % _P
    else:
        s = (y2 - y1) * _inv((x2 - x1) % _P) % _P
    x3 = (s * s - x1 - x2) % _P
    return (x3, (s * (x1 - x3) - y1) % _P)


def _point_mul(k: int, p) -> tuple | None:
    result = None
    addend = p
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def ecdsa_p256_verify(pub_x: int, pub_y: int, msg_hash: bytes, r: int, s: int) -> bool:
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    e = int.from_bytes(msg_hash[:32].ljust(32, b"\0"), "big") % _N
    w = pow(s, _N - 2, _N)
    u1 = e * w % _N
    u2 = r * w % _N
    pt = _point_add(_point_mul(u1, (_GX, _GY)), _point_mul(u2, (pub_x, pub_y)))
    if pt is None:
        return False
    return pt[0] % _N == r


def _der_to_pubkey_xy(der: bytes) -> tuple[int, int]:
    children = _der_children(der)
    if len(children) == 1 and children[0][0] == 0x30:
        # Full SubjectPublicKeyInfo: descend into the outer SEQUENCE.
        children = _der_children(children[0][1])
    bitstring = None
    for tag, body in children:
        if tag == 0x03:
            bitstring = body
    if not bitstring or bitstring[0] != 0x00 or bitstring[1] != 0x04:
        raise ValueError("expected uncompressed P-256 key")
    point = bitstring[2:]
    if len(point) != 64:
        raise ValueError("bad P-256 point length")
    return int.from_bytes(point[:32], "big"), int.from_bytes(point[32:], "big")


def _pem_to_der(pem: str) -> bytes:
    lines = [ln.strip() for ln in pem.splitlines() if ln.strip() and "-----" not in ln]
    return base64.b64decode("".join(lines))


def sct_signed_data(log_id: bytes, timestamp_ms: int, issuer_key_hash: bytes,
                    cert_der: bytes, extensions: bytes = b"") -> bytes:
    return (
        b"\x00"  # version v1
        + b"\x00"  # signature_type = certificate_timestamp
        + struct.pack(">Q", timestamp_ms)
        + b"\x00\x00"  # LogEntryType = x509_entry
        + issuer_key_hash  # 32 bytes
        + len(cert_der).to_bytes(3, "big")
        + cert_der
        + struct.pack(">H", len(extensions))
        + extensions
    )


def verify_sct(sct: dict, log_pubkey_der: bytes, issuer_key_hash: bytes, cert_der: bytes) -> bool:
    try:
        data = sct_signed_data(sct["log_id"], sct["timestamp"], issuer_key_hash, cert_der)
        digest = hashlib.sha256(data).digest()
        sig = sct["signature"]
        if len(sig) < 8:
            return False
        # ECDSA-DER signature
        tag, body, pos = _der_read_tlv(sig, 0)
        if tag != 0x30:
            return False
        t1, b1, p1 = _der_read_tlv(body, 0)
        t2, b2, _ = _der_read_tlv(body, p1)
        if t1 != 0x02 or t2 != 0x02:
            return False
        r, s = int.from_bytes(b1, "big"), int.from_bytes(b2, "big")
        x, y = _der_to_pubkey_xy(log_pubkey_der)
        return ecdsa_p256_verify(x, y, digest, r, s)
    except Exception:
        return False


def pinned_logs() -> dict[bytes, bytes]:
    raw = os.environ.get("WEBRESEARCH_CT_LOGS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out: dict[bytes, bytes] = {}
    for log_id_b64, pem in data.items():
        try:
            out[base64.b64decode(log_id_b64)] = _pem_to_der(str(pem))
        except Exception:
            continue
    return out


def audit_certificate(der_cert: bytes, issuer_key_hash: bytes = b"\x00" * 32) -> dict:
    """Verify every embedded SCT with a pinned log key. Honest output:
    verified count, unverifiable count (no pinned key), invalid count."""
    result: dict = {"scts": 0, "verified": [], "unverified_no_key": 0, "invalid": []}
    logs = pinned_logs()
    raw = find_sct_extension(der_cert)
    if raw is None:
        result["note"] = "no SCT extension present"
        return result
    for sct in parse_sct_list(raw):
        result["scts"] += 1
        key = logs.get(sct["log_id"])
        if key is None:
            result["unverified_no_key"] += 1
            continue
        if verify_sct(sct, key, issuer_key_hash, der_cert):
            result["verified"].append(sct["log_id"].hex())
        else:
            result["invalid"].append(sct["log_id"].hex())
    return result
