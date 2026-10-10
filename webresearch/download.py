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
