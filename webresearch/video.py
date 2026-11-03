"""Video downloads: platform extraction via yt-dlp, verified to disk.

Two real lanes, chosen from the URL:

- direct media file (mp4/webm/mp3/...): the existing streaming download lane
  (session cookies, sha256, byte cap) — no subprocess needed;
- anything else (pages, HLS/DASH manifests, platform watch URLs): yt-dlp runs
  as a ``python -m yt_dlp`` subprocess pinned to this interpreter, its final
  filepath is captured via ``--print after_move:filepath``, and the file is
  then hashed and size-checked here.

Policy is enforced before any network work: DRM-protected subscription
services and known piracy mirrors are refused outright. The denylists cover
known entries only; they are a gate, not a guarantee, and the tool contract
says so. yt-dlp absence is reported as a missing capability, never faked.

No malware verdict engine here either — the sha256 is provided instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

# DRM subscription platforms. Downloading their streams requires a licensed
# content module (Widevine-class); that is a permanent "no", stated honestly.
DRM_SERVICES = {
    "netflix.com",
    "primevideo.com",
    "hotstar.com",
    "disneyplus.com",
    "hulu.com",
    "peacocktv.com",
    "max.com",
    "hbomax.com",
    "appletv.com",
    "spotify.com",
    "tidal.com",
    "deezer.com",
    "qobuz.com",
}

# Known piracy mirrors (unlicensed restreams). Known entries only: this list
# is a gate against the named sites, not a claim that everything else is legal.
PIRACY_MIRRORS = {
    "netmirror.to",
    "netmirror.site",
    "netmirror.cc",
    "netmirror.live",
}

DIRECT_MEDIA_EXTS = {
    ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv", ".ogv",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac",
}

DEFAULT_FORMAT = "bv*+ba/b"
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DEFAULT_TIMEOUT = 600.0


@dataclass
class VideoDownload:
    url: str = ""
    page_url: str = ""
    path: str = ""
    bytes: int = 0
    sha256: str = ""
    title: str = ""
    extractor: str = ""
    format: str = ""
    duration: float = 0.0
    content_type: str = ""
    lane: str = ""
    policy: str = "allowed"
    policy_reason: str = ""
    ytdlp_version: str = ""
    ffmpeg_available: bool = False
    verified: bool = False
    scanned: bool = False
    error: str = ""
    stderr_tail: str = field(default="", repr=False)


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower()
    except Exception:
        return ""


def _host_in(url: str, domains: set[str]) -> str | None:
    host = _host(url)
    if not host:
        return None
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


def check_policy(url: str) -> tuple[str, str]:
    """Return ("allowed", "") or ("refused", reason) without network work."""
    try:
        parsed = httpx.URL(url)
    except Exception:
        return "refused", "url is not parseable"
    if parsed.scheme not in ("http", "https"):
        return "refused", f"scheme {parsed.scheme or '(none)'} is not http/https"
    hit = _host_in(url, DRM_SERVICES)
    if hit:
        return "refused", f"DRM-protected platform ({hit}); needs a licensed content module — not supported"
    hit = _host_in(url, PIRACY_MIRRORS)
    if hit:
        return "refused", f"known piracy mirror ({hit}); legitimate platforms only"
    return "allowed", ""


def is_direct_media(url: str) -> bool:
    """A URL whose path ends in a media extension is fetched by the streaming
    lane directly. HLS/DASH manifests (.m3u8/.mpd) are NOT direct: they are
    segment playlists handled by the extractor."""
    try:
        path = httpx.URL(url).path.lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in DIRECT_MEDIA_EXTS)


def _ytdlp_version() -> str:
    """Version of the yt-dlp importable by THIS interpreter (the subprocess
    runs ``sys.executable -m yt_dlp``, so import == what will execute)."""
    try:
        from yt_dlp import version as _v
    except Exception:
        return ""
    return str(getattr(_v, "__version__", "") or "")


def to_netscape_cookies(jar, url: str = "") -> str:
    """Export an httpx/curl_cffi cookie jar as a Netscape cookie file.

    Handles http.cookiejar.Cookie objects (domain cookies, secure flags,
    expiry) and falls back to plain name/value pairs with the URL's host.
    Session cookies get expiry 0; Netscape readers keep them for the run.
    """
    lines = ["# Netscape HTTP Cookie File"]
    seen = 0
    try:
        cookies = list(jar.jar)
    except AttributeError:
        cookies = []
    for c in cookies:
        domain = (getattr(c, "domain", "") or "").strip()
        if not domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if getattr(c, "secure", False) else "FALSE"
        expires = int(getattr(c, "expires", 0) or 0)
        lines.append(f"{domain}\t{include_sub}\t{getattr(c, 'path', '/')}\t{secure}\t{expires}\t{c.name}\t{c.value or ''}")
        seen += 1
    if not seen:
        host = _host(url)
        if host:
            for name, value in (jar.items() if hasattr(jar, "items") else []):
                lines.append(f"{host}\tFALSE\t/\tFALSE\t0\t{name}\t{value or ''}")
                seen += 1
    if not seen:
        return ""
    return "\n".join(lines) + "\n"


def _hash_file(path: str, *, max_bytes: int) -> tuple[int, str, str]:
    """Stream-hash the produced file; enforce the byte cap post-hoc."""
    hasher = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return total, "", f"exceeds {max_bytes} bytes"
            hasher.update(chunk)
    return total, hasher.hexdigest(), ""


def _json_field(line: str) -> str:
    """A %(field)j print arrives as one JSON line; anything else is ''."""
    try:
        value = json.loads(line)
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _duration_field(line: str) -> float:
    try:
        return float(json.loads(line) or 0)
    except Exception:
        return 0.0


def _meta_from_lines(lines: list[str]) -> tuple[dict, str]:
    """Parse stdout of the download pass. --print emits one line per flag in
    flag order (title, extractor, duration, webpage_url, filepath); the last
    line is always the produced filepath."""
    meta = {"title": "", "extractor": "", "duration": 0.0, "page_url": ""}
    if len(lines) >= 5:
        meta["title"] = _json_field(lines[0])
        meta["extractor"] = _json_field(lines[1])
        meta["duration"] = _duration_field(lines[2])
        meta["page_url"] = _json_field(lines[3])
    return meta, (lines[-1] if lines else "")


def _parse_meta(raw: bytes) -> dict:
    """Parse yt-dlp -J metadata JSON into the record fields. Raises
    ValueError on anything that is not a JSON object."""
    import json

    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        raise ValueError(f"yt-dlp metadata is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("yt-dlp metadata is not a JSON object")
    return {
        "title": str(data.get("title") or ""),
        "extractor": str(data.get("extractor") or ""),
        "duration": float(data.get("duration") or 0.0),
        "page_url": str(data.get("webpage_url") or data.get("url") or ""),
    }


async def get_video(
    url: str,
    client=None,
    *,
    dest_dir: str | None = None,
    format: str | None = None,
    use_session: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str | None = None,
) -> VideoDownload:
    """Download a video from a legitimate platform, verified to disk.

    ``client`` supplies cookies and the fingerprint-parity transport for the
    direct lane and cookie export for login-gated platform extraction
    (``use_session=True``). Absent yt-dlp is reported, not faked.
    """
    rec = VideoDownload(url=url)
    rec.ffmpeg_available = shutil.which("ffmpeg") is not None

    rec.policy, rec.policy_reason = check_policy(url)
    if rec.policy != "allowed":
        rec.error = f"policy: {rec.policy_reason}"
        return rec

    if is_direct_media(url):
        rec.lane = "direct"
        if client is None:
            rec.error = "direct media lane needs the session client"
            return rec
        from webresearch.download import download as _download

        got = await _download(url, client, dest_dir=dest_dir, max_bytes=max_bytes)
        rec.path = got.path
        rec.bytes = got.bytes
        rec.sha256 = got.sha256
        rec.content_type = got.content_type
        rec.verified = got.verified
        rec.error = got.error
        if got.error:
            rec.path = ""
        return rec

    rec.lane = "ytdlp"
    rec.format = format or DEFAULT_FORMAT
    rec.ytdlp_version = _ytdlp_version()
    if not rec.ytdlp_version:
        rec.error = "yt-dlp is not installed; install the 'video' extra (pip install webresearch[video])"
        return rec

    cookie_file = ""
    tmp_dir = tempfile.mkdtemp(prefix="wr-video-", dir=dest_dir or None)
    try:
        out_tpl = os.path.join(tmp_dir, "%(title).190B.%(ext)s")
        argv = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--no-simulate", "--no-progress", "--no-warnings",
            "--retries", "3", "--socket-timeout", "30",
            "--max-filesize", str(max_bytes),
            "-f", rec.format,
            "-o", out_tpl,
            "--print", "after_move:%(title)j",
            "--print", "after_move:%(extractor)j",
            "--print", "after_move:%(duration)j",
            "--print", "after_move:%(webpage_url)j",
            "--print", "after_move:filepath",
        ]
        if user_agent:
            argv += ["--user-agent", user_agent]
        if use_session and client is not None:
            try:
                netscape = to_netscape_cookies(client.cookies, url)
            except AttributeError:
                netscape = ""
            if netscape:
                cookie_file = os.path.join(tmp_dir, "cookies.txt")
                Path(cookie_file).write_text(netscape, encoding="utf-8")
                argv += ["--cookies", cookie_file]
        argv += ["--", url]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            rec.error = f"yt-dlp exceeded {timeout:.0f}s timeout"
            rec.stderr_tail = "killed after timeout"
            return rec

        err_text = stderr.decode("utf-8", "replace") if stderr else ""
        rec.stderr_tail = err_text[-400:]
        if proc.returncode != 0:
            rec.error = f"yt-dlp exit {proc.returncode}: {err_text.strip()[-300:] or '(no stderr)'}"
            return rec

        lines = [ln.strip() for ln in (stdout or b"").decode("utf-8", "replace").splitlines() if ln.strip()]
        meta, produced = _meta_from_lines(lines)
        if not produced or not os.path.isfile(produced):
            rec.error = (
                "yt-dlp exited 0 but produced no file "
                "(max-filesize skip, removed format, or unsupported page): "
                + err_text.strip()[-200:]
            ) if not produced else f"yt-dlp reported path that does not exist: {produced}"
            return rec

        rec.title = meta["title"]
        rec.extractor = meta["extractor"]
        rec.duration = meta["duration"]
        rec.page_url = meta["page_url"]
        total, digest, hash_err = _hash_file(produced, max_bytes=max_bytes)
        rec.path = produced
        rec.bytes = total
        rec.sha256 = digest
        if hash_err:
            rec.error = hash_err
            with contextlib.suppress(OSError):
                os.unlink(produced)
            rec.path = ""
            return rec
        rec.verified = True
        return rec
    finally:
        if cookie_file:
            with contextlib.suppress(OSError):
                os.unlink(cookie_file)
        with contextlib.suppress(OSError):
            os.rmdir(tmp_dir)


def video_view(rec: VideoDownload) -> dict:
    """The wire shape every surface (MCP/HTTP/shell) returns: full record,
    secrets-free by construction (no cookie contents ever enter a record)."""
    return asdict(rec)
