"""Signed Exchanges and Web Bundles: validate, never blindly execute.

SXG (application/signed-exchange): validates the Signature header field —
validity-url same-origin to the request, date/expires window covering now,
cert-sha256 against the fetched cert-url DER, and body integrity against the
integrity attribute. The exchange payload is never executed as a page; inner
content is reported, not trusted.

WebBundle (application/webbundle): verifies the magic + version, parses the
CBOR envelope to list sections/exchanges, and reports them. Bundled bodies
are not executed.
"""

from __future__ import annotations

import base64
import hashlib
import re
import struct
from urllib.parse import urlparse

SXG_MAGIC = b"sxg1-b3\x00"
BUNDLE_MAGIC = bytes([0xF0, 0x9F, 0x8C, 0x90, 0xF0, 0x9F, 0x93, 0xA6])  # U+1F310 U+1F4E6


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    porta = pa.port or (443 if pa.scheme == "https" else 80)
    portb = pb.port or (443 if pb.scheme == "https" else 80)
    return pa.scheme == pb.scheme and (pa.hostname or "").lower() == (pb.hostname or "").lower() and porta == portb


def parse_signature_header(value: str) -> list[dict]:
    """Parse the SXG Signature header field into label->params dicts."""
    out: list[dict] = []
    for m in re.finditer(r'([A-Za-z0-9*_/-]+)\s*=\s*(:[^:]*:|"[^"]*"|[^,;]+)\s*((?:;\s*[^,]+)*)', value or ""):
        label = m.group(1)
        params: dict[str, str] = {}
        for pm in re.finditer(r'([A-Za-z0-9*_/-]+)\s*=\s*(:[^:]*:|"[^"]*"|[^;,]+)', m.group(3) or ""):
            raw = pm.group(2).strip()
            if raw.startswith(":") and raw.endswith(":"):
                params[pm.group(1)] = raw[1:-1]
            elif raw.startswith('"') and raw.endswith('"'):
                params[pm.group(1)] = raw[1:-1]
            else:
                try:
                    params[pm.group(1)] = str(int(raw))
                except ValueError:
                    params[pm.group(1)] = raw
        out.append({"label": label, "params": params})
    return out


def _b64_any(s: str) -> bytes | None:
    s = (s or "").strip()
    try:
        padded = s + "=" * (-len(s) % 4)
        return base64.b64decode(padded)
    except Exception:
        return None


def check_integrity(integrity_attr: str, body: bytes) -> tuple[bool, str]:
    """Validate digest/mi-sha256 and plain sha256 integrity values."""
    attr = (integrity_attr or "").strip()
    m = re.match(r"^(?:digest/)?mi-sha256=(.+)$", attr)
    if m:
        want = _b64_any(m.group(1))
        if want is None:
            return False, "bad mi-sha256 encoding"
        # Single-record content: digest over 0x00 || record.
        got = hashlib.sha256(b"\x00" + body).digest()
        return (got == want, "mi-sha256 match" if got == want else "mi-sha256 mismatch")
    m = re.match(r"^sha256[-=](.+)$", attr)
    if m:
        want = _b64_any(m.group(1))
        if want is None:
            return False, "bad sha256 encoding"
        got = hashlib.sha256(body).digest()
        return (got == want, "sha256 match" if got == want else "sha256 mismatch")
    return False, f"unsupported integrity expression: {attr[:40]}"


async def validate_sxg(url: str, headers: dict, body: bytes, client) -> dict:
    report: dict = {"format": "signed-exchange", "valid": False, "checks": []}
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    if not body.startswith(SXG_MAGIC):
        report["checks"].append("bad magic: not an sxg1-b3 exchange")
        return report
    report["checks"].append("magic ok")
    sigs = parse_signature_header(lowered.get("signature", ""))
    if not sigs:
        report["checks"].append("missing Signature header")
        return report
    import time
    now = int(time.time())
    overall = True
    for sig in sigs:
        p = sig["params"]
        label = sig["label"]
        validity_url = p.get("validity-url", "")
        if not validity_url or not _same_origin(validity_url, url):
            report["checks"].append(f"{label}: validity-url not same-origin with request")
            overall = False
            continue
        try:
            date = int(p.get("date", "-1"))
            expires = int(p.get("expires", "-1"))
        except ValueError:
            report["checks"].append(f"{label}: bad date/expires")
            overall = False
            continue
        if not (date <= now <= expires):
            report["checks"].append(f"{label}: outside validity window")
            overall = False
            continue
        report["checks"].append(f"{label}: validity window ok")
        cert_sha = p.get("cert-sha256")
        cert_url = p.get("cert-url")
        if cert_sha and cert_url:
            try:
                resp = await client.get(cert_url)
                der = resp.content if hasattr(resp, "content") else b""
                got = hashlib.sha256(der).digest()
                want = _b64_any(cert_sha)
                if want is None or got != want:
                    report["checks"].append(f"{label}: cert-sha256 mismatch")
                    overall = False
                else:
                    report["checks"].append(f"{label}: cert-sha256 match")
            except Exception as exc:
                report["checks"].append(f"{label}: cert fetch failed: {type(exc).__name__}")
                overall = False
        integrity = p.get("integrity", "")
        if integrity:
            ok, msg = check_integrity(integrity, body)
            report["checks"].append(f"{label}: {msg}")
            overall = overall and ok
        if not p.get("sig"):
            report["checks"].append(f"{label}: no sig bytes present")
        else:
            report["checks"].append(f"{label}: sig bytes present (ed25519 math not verified here)")
    report["valid"] = overall
    return report


# --- Minimal CBOR reader (definite lengths only) ---
def _cbor_read(data: bytes, pos: int):
    if pos >= len(data):
        raise ValueError("cbor overrun")
    initial = data[pos]
    pos += 1
    major, info = initial >> 5, initial & 0x1F
    if info < 24:
        arg = info
    elif info == 24:
        arg, pos = data[pos], pos + 1
    elif info == 25:
        arg, pos = struct.unpack(">H", data[pos:pos + 2])[0], pos + 2
    elif info == 26:
        arg, pos = struct.unpack(">I", data[pos:pos + 4])[0], pos + 4
    elif info == 27:
        arg, pos = struct.unpack(">Q", data[pos:pos + 8])[0], pos + 8
    else:
        raise ValueError(f"indefinite CBOR not supported (major {major})")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major == 2:
        return data[pos:pos + arg], pos + arg
    if major == 3:
        return data[pos:pos + arg].decode("utf-8", "replace"), pos + arg
    if major == 4:
        arr = []
        for _ in range(arg):
            v, pos = _cbor_read(data, pos)
            arr.append(v)
        return arr, pos
    if major == 5:
        obj = {}
        for _ in range(arg):
            k, pos = _cbor_read(data, pos)
            v, pos = _cbor_read(data, pos)
            obj[k] = v
        return obj, pos
    if major == 6:
        return _cbor_read(data, pos)
    if major == 7:
        if info in (20, 21):
            return info == 21, pos
        if info == 22:
            return None, pos
        raise ValueError("unsupported simple value")
    raise ValueError("bad major type")


def inspect_webbundle(body: bytes) -> dict:
    report: dict = {"format": "webbundle", "sections": [], "exchanges": 0, "executed": False}
    if not body.startswith(BUNDLE_MAGIC):
        report["error"] = "bad magic"
        return report
    report["checks"] = ["magic ok"]
    try:
        pos = len(BUNDLE_MAGIC)
        version, pos = _cbor_read(body, pos)
        report["version"] = version
        section_lengths, pos = _cbor_read(body, pos)
        sections, pos = _cbor_read(body, pos)
        if isinstance(sections, list):
            for section in sections:
                name = section[0] if isinstance(section, list) and section else section
                report["sections"].append(name if isinstance(name, str) else repr(name)[:40])
        report["exchanges"] = report["sections"].count("index")
        report["checks"].append(f"{len(report['sections'])} sections listed; contents not executed")
    except Exception as exc:
        report["error"] = f"bundle parse failed: {type(exc).__name__}"
    return report
