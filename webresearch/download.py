"""Native downloads: stream to disk with hash verification.

Every download records its SHA-256 (and any expected hash check). There is no
malware verdict engine here — that absence is reported, not hidden.
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Download:
    url: str
    path: str = ""
    bytes: int = 0
    sha256: str = ""
    content_type: str = ""
    expected: str = ""
    verified: bool = False
    error: str = ""
    scanned: bool = False
    # M6: per-download malware verdict (clean/suspicious/unknown/error),
    # the scanner that produced it, and its reasons. No verdict engine
    # is faked — see webresearch/download.py scan_download().
    malware_verdict: str = "unknown"
    malware_scanner: str = ""
    malware_reasons: list = field(default_factory=list)
    malware_signature: str = ""


# ---- M6: download malware verdict (pluggable scanner hook) ----
# Honest scope: no signature engine is implemented here. The built-in
# heuristic checks are structural (magic bytes, extension, size), the
# registry accepts external scanner callables, and every verdict says
# which scanner produced it and why. "clean" NEVER means "safe" — it
# means "no configured scanner flagged it".

@dataclass
class MalwareVerdict:
    verdict: str = "unknown"          # clean | suspicious | unknown | error
    scanner: str = ""                 # which scanner produced it
    reasons: list = field(default_factory=list)
    signature_hit: str = ""           # name of the matched signature, if any


# Well-known malicious byte patterns used by real scanners as quick
# triage (EICAR test string, Windows PE exploits in scripts, etc.).
# These are SIGNATURE-STRINGS, not executable content.
_EICAR = (
    rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

# Executable-magic table: content sniffing independent of the filename.
_EXE_MAGICS: list[tuple[str, bytes]] = [
    ("mz-pe", b"MZ"),                 # Windows PE (check EXE header offset)
    ("elf", b"\x7fELF"),              # Linux ELF
    ("macho-32", b"\xfe\xed\xfa\xce"),
    ("macho-64", b"\xfe\xed\xfa\xcf"),
    ("macho-fat", b"\xca\xfe\xba\xbe"),
]

# Extensions that browsers flag when arriving over http(s)
_RISKY_EXTS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".vbs", ".vbe",
    ".js", ".jar", ".hta", ".apk", ".deb", ".rpm", ".dmg", ".pkg",
}


def _builtin_heuristic(path: str, *, url: str = "", content_type: str = "", size: int = 0) -> MalwareVerdict:
    """Structural triage — honest about being heuristics, not signatures."""
    v = MalwareVerdict(scanner="builtin-heuristic")
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        v.verdict = "error"
        v.reasons.append("file unreadable")
        return v
    # 1. EICAR test file: the industry-standard scanner test pattern.
    if _EICAR in head:
        v.verdict = "suspicious"
        v.signature_hit = "EICAR-Test-File"
        v.reasons.append("EICAR antivirus test pattern present")
        return v
    # 2. Executable content delivered where a browser would not expect it.
    for name, magic in _EXE_MAGICS:
        if head.startswith(magic):
            v.verdict = "suspicious"
            v.signature_hit = f"executable:{name}"
            v.reasons.append(f"executable binary content ({name})")
            return v
    # 3. Risky extension + executable-ish content type.
    ext = os.path.splitext(urlparse(url).path)[1].lower() if url else ""
    if ext in _RISKY_EXTS and ("octet-stream" in (content_type or "").lower() or not content_type):
        v.verdict = "suspicious"
        v.reasons.append(f"risky extension '{ext}' with content-type '{content_type or 'none'}'")
        return v
    # 4. Nothing flagged: clean means "not flagged", never "safe".
    v.verdict = "clean"
    v.reasons.append("no builtin signature/heuristic matched")
    return v


# The pluggable registry. External engines register a callable:
#   fn(path: str) -> MalwareVerdict (or dict with verdict/scanner/reasons)
_SCANNERS: list = []


def register_scanner(fn) -> None:
    """Register an external scanner engine. The engine is consulted
    AFTER the builtin heuristic; the first scanner returning a
    'suspicious'/'error' verdict wins (most-specific result)."""
    if callable(fn) and fn not in _SCANNERS:
        _SCANNERS.append(fn)


def clear_scanners() -> None:
    _SCANNERS.clear()


def scan_download(path: str, *, url: str = "", content_type: str = "", size: int = 0) -> MalwareVerdict:
    """Run every registered scanner + the builtin heuristic and merge
    into one honest verdict. No scanner configured + heuristic clean =>
    verdict 'clean' with an explicit reason saying it is heuristic-only."""
    verdict = _builtin_heuristic(path, url=url, content_type=content_type, size=size)
    for scanner in _SCANNERS:
        try:
            res = scanner(path)
            if isinstance(res, MalwareVerdict):
                other = res
            elif isinstance(res, dict):
                other = MalwareVerdict(
                    verdict=str(res.get("verdict", "unknown")),
                    scanner=str(res.get("scanner", getattr(scanner, "__name__", "external"))),
                    reasons=list(res.get("reasons", [])),
                    signature_hit=str(res.get("signature_hit", "")),
                )
            else:
                continue
        except Exception as exc:
            verdict.verdict = "error"
            verdict.reasons.append(f"scanner {getattr(scanner, '__name__', '?')} failed: {type(exc).__name__}: {exc}"[:200])
            verdict.verdict = "error"
            return verdict
        if other.verdict in ("suspicious", "error"):
            return other
        if other.verdict == "clean" and not other.reasons:
            continue
        if other.verdict == "clean":
            # keep the more specific scanner name
            verdict = other
    return verdict


def _expected_digest(expected: str | None) -> tuple[str, bytes] | None:
    if not expected:
        return None
    token = expected.strip()
    if "-" in token and not token.startswith("sha256-") and len(token.split("-", 1)[0]) <= 7:
        algo, _, b64 = token.partition("-")
        algo = algo.strip().lower()
    elif token.startswith("sha256-"):
        algo, b64 = "sha256", token[len("sha256-"):]
    else:
        algo, b64 = "sha256", token
    # A bare hex string whose length matches a real digest length is hex.
    # Every hex character is also a valid base64 character, so trying base64
    # first would silently "decode" a 64-char sha256 hex digest into garbage.
    hex_lengths = {"40": "sha1", "56": "sha224", "64": "sha256", "96": "sha384", "128": "sha512"}
    try:
        if algo == "sha256" and len(b64) in (40, 56, 64, 96, 128):
            raw = bytes.fromhex(b64)
            if len(raw) * 2 == len(b64):
                return hex_lengths[str(len(b64))], raw
    except ValueError:
        pass
    try:
        return algo, base64.b64decode(b64)
    except Exception:
        try:
            return algo, bytes.fromhex(b64)
        except Exception:
            return None


async def download(
    url: str,
    client,
    *,
    expected: str | None = None,
    dest_dir: str | None = None,
    max_bytes: int = 100 * 1024 * 1024,
) -> Download:
    rec = Download(url=url, expected=expected or "")
    hasher = hashlib.sha256()
    total = 0
    tmp = tempfile.NamedTemporaryFile(
        prefix="wr-download-", delete=False, dir=dest_dir or tempfile.gettempdir()
    )
    try:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                rec.error = f"http-{response.status_code}"
                return rec
            rec.content_type = response.headers.get("content-type", "")
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    rec.error = f"exceeds {max_bytes} bytes"
                    break
                hasher.update(chunk)
                tmp.write(chunk)
    except Exception as exc:
        rec.error = f"{type(exc).__name__}: {exc}"[:300]
        return rec
    finally:
        tmp.close()
    rec.path = tmp.name
    rec.bytes = total
    rec.sha256 = hasher.hexdigest()
    # M6: malware verdict via the pluggable scanner hook. Runs on the
    # real downloaded bytes; recorded per-download with scanner + reasons.
    if not rec.error:
        try:
            v = scan_download(tmp.name, url=url, content_type=rec.content_type, size=total)
            rec.malware_verdict = v.verdict
            rec.malware_scanner = v.scanner
            rec.malware_reasons = v.reasons
            rec.malware_signature = v.signature_hit
            rec.scanned = True
        except Exception as exc:
            rec.malware_verdict = "error"
            rec.malware_scanner = "scan-dispatch"
            rec.malware_reasons = [f"{type(exc).__name__}: {exc}"[:200]]
            rec.scanned = False
    want = _expected_digest(expected)
    if want is not None:
        algo, digest = want
        try:
            actual = hashlib.new(algo, open(tmp.name, "rb").read()).digest()
        except ValueError:
            actual = b""
        rec.verified = bool(actual) and actual == digest
        if not rec.verified:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            rec.path = ""
            rec.error = f"hash mismatch (expected {algo})"
    else:
        rec.verified = True
    return rec


@dataclass
class BackgroundFetchRecord:
    id: str
    urls: list[str] = field(default_factory=list)
    downloads: list[Download] = field(default_factory=list)
    done: bool = False
    error: str = ""

    @property
    def downloaded(self) -> int:
        return sum(d.bytes for d in self.downloads if not d.error)

    @property
    def failures(self) -> int:
        return sum(1 for d in self.downloads if d.error)


async def background_fetch(
    urls: list[str],
    client,
    *,
    tag: str = "",
    max_bytes: int = 100 * 1024 * 1024,
    progress=None,
) -> BackgroundFetchRecord:
    """Fetch a set of URLs in the background (concurrently), storing each to
    disk with hash verification. Progress callback receives (done, total)."""
    import asyncio as _asyncio
    import hashlib as _hashlib

    record_id = tag or f"bg-{_hashlib.sha256(','.join(urls).encode()).hexdigest()[:12]}"
    record = BackgroundFetchRecord(id=record_id, urls=list(urls))
    total = len(urls)
    done = 0

    async def one(u: str) -> Download:
        nonlocal done
        rec = await download(u, client, max_bytes=max_bytes)
        done += 1
        if progress is not None:
            try:
                progress(done, total)
            except Exception:
                pass
        return rec

    try:
        record.downloads = list(await _asyncio.gather(*(one(u) for u in urls)))
        record.done = True
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"[:300]
    return record
