"""Hermetic tests for the video lane: policy gate, cookie export, hashing,
metadata parsing (real captured fixture), and honest-absence behavior.

No test here touches the network or requires yt-dlp to be installed —
absence paths are monkeypatched, and the metadata parser runs against
tests/fixtures/ytdlp_archive_org.json, captured from a real yt-dlp -J run
on a public-domain archive.org item.
"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from webresearch.video import (
    DEFAULT_FORMAT,
    VideoDownload,
    _hash_file,
    _meta_from_lines,
    _parse_meta,
    _ytdlp_version,
    check_policy,
    get_video,
    is_direct_media,
    to_netscape_cookies,
    video_view,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ytdlp_archive_org.json"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.netflix.com/watch/81234",
        "https://www.hotstar.com/in/shows/x/123",
        "https://www.disneyplus.com/video/x",
        "https://open.spotify.com/track/xyz",
    ],
)
def test_policy_refuses_drm_platforms(url):
    policy, reason = check_policy(url)
    assert policy == "refused"
    assert "DRM" in reason


@pytest.mark.parametrize(
    "url",
    [
        "https://netmirror.to/watch/123",
        "https://netmirror.site/x",
        "https://sub.netmirror.cc/y",
    ],
)
def test_policy_refuses_known_piracy_mirrors(url):
    policy, reason = check_policy(url)
    assert policy == "refused"
    assert "piracy" in reason


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.dailymotion.com/video/x123",
        "https://archive.org/details/BigBuckBunny_124",
        "https://commons.wikimedia.org/wiki/File:X.webm",
    ],
)
def test_policy_allows_legitimate_platforms(url):
    assert check_policy(url) == ("allowed", "")


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://host/file.mp4", "about:blank", "javascript:alert(1)"],
)
def test_policy_refuses_non_http_schemes(url):
    policy, reason = check_policy(url)
    assert policy == "refused"
    assert "scheme" in reason


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/clip.mp4",
        "https://example.com/media/video.webm",
        "https://example.com/audio/track.mp3",
        "https://example.com/clip.MP4",
    ],
)
def test_direct_media_detection(url):
    assert is_direct_media(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=x",
        "https://example.com/stream/master.m3u8",
        "https://example.com/stream/manifest.mpd",
        "https://example.com/watch/123",
    ],
)
def test_manifests_and_pages_are_not_direct(url):
    assert is_direct_media(url) is False


def test_netscape_cookie_export_has_header_and_domain_flags():
    jar = httpx.Cookies()
    jar.set("sid", "abc123", domain=".example.com", path="/")
    jar.set("uid", "42", domain="cdn.example.com", path="/")
    out = to_netscape_cookies(jar, "https://example.com/")
    lines = out.strip().splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    body = [ln for ln in lines[1:] if ln]
    assert len(body) == 2
    by_name = {ln.split("\t")[5]: ln.split("\t") for ln in body}
    # domain cookie -> include_subdomains TRUE; host cookie -> FALSE
    assert by_name["sid"][0] == ".example.com"
    assert by_name["sid"][1] == "TRUE"
    assert by_name["uid"][0] == "cdn.example.com"
    assert by_name["uid"][1] == "FALSE"
    # every data line is 7 tab-separated fields
    for ln in body:
        assert len(ln.split("\t")) == 7


def test_netscape_cookie_export_empty_jar_returns_empty():
    jar = httpx.Cookies()
    assert to_netscape_cookies(jar, "https://example.com/") == ""


def test_hash_file_sha256_matches_hashlib(tmp_path):
    payload = b"dosh-video-lane" * 1000
    p = tmp_path / "clip.mp4"
    p.write_bytes(payload)
    total, digest, err = _hash_file(str(p), max_bytes=len(payload) + 1)
    assert total == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert err == ""


def test_hash_file_enforces_byte_cap(tmp_path):
    p = tmp_path / "big.mp4"
    p.write_bytes(b"x" * 1024)
    total, digest, err = _hash_file(str(p), max_bytes=512)
    assert total == 1024
    assert digest == ""
    assert "exceeds" in err


def test_parse_meta_on_real_captured_fixture():
    raw = FIXTURE.read_bytes()
    meta = _parse_meta(raw)
    assert meta["title"] == "Big Buck Bunny"
    assert meta["extractor"] == "archive.org"
    assert meta["duration"] > 500
    assert meta["page_url"].startswith("https://archive.org/")


def test_meta_from_lines_parses_real_captured_stdout():
    # Captured from a real yt-dlp download pass (archive.org, public domain).
    lines = [
        '"Big Buck Bunny"',
        '"archive.org"',
        "596.46",
        '"https://archive.org/details/BigBuckBunny_124"',
        "/tmp/wr-video-x/Big Buck Bunny.mp4",
    ]
    meta, path = _meta_from_lines(lines)
    assert meta["title"] == "Big Buck Bunny"
    assert meta["extractor"] == "archive.org"
    assert meta["duration"] == 596.46
    assert meta["page_url"] == "https://archive.org/details/BigBuckBunny_124"
    assert path == "/tmp/wr-video-x/Big Buck Bunny.mp4"


def test_meta_from_lines_tolerates_missing_meta():
    meta, path = _meta_from_lines(["/tmp/only.mp4"])
    assert meta["title"] == ""
    assert path == "/tmp/only.mp4"


def test_meta_from_lines_handles_na_and_null_duration():
    meta, _ = _meta_from_lines(['"T"', '"x"', "NA", '"u"', "/tmp/a.mp4"])
    assert meta["duration"] == 0.0
    meta, _ = _meta_from_lines(['"T"', '"x"', "null", '"u"', "/tmp/a.mp4"])
    assert meta["duration"] == 0.0


def test_parse_meta_rejects_non_json():
    with pytest.raises(ValueError):
        _parse_meta(b"not json at all")
    with pytest.raises(ValueError):
        _parse_meta(json.dumps(["a", "list"]).encode())


@pytest.mark.asyncio
async def test_get_video_refusal_runs_no_subprocess(monkeypatch):
    async def _fail(*a, **kw):
        raise AssertionError("policy refusal must not spawn a subprocess")

    monkeypatch.setattr("webresearch.video.asyncio.create_subprocess_exec", _fail)
    rec = await get_video("https://www.netflix.com/watch/1")
    assert rec.policy == "refused"
    assert rec.path == ""
    assert "DRM" in rec.error


@pytest.mark.asyncio
async def test_ytdlp_absence_reported_honestly(monkeypatch):
    async def _fail(*a, **kw):
        raise AssertionError("absent yt-dlp must not spawn a subprocess")

    monkeypatch.setattr("webresearch.video._ytdlp_version", lambda: "")
    monkeypatch.setattr("webresearch.video.asyncio.create_subprocess_exec", _fail)
    rec = await get_video("https://www.youtube.com/watch?v=never-runs")
    assert rec.lane == "ytdlp"
    assert rec.path == ""
    assert rec.ytdlp_version == ""
    assert "yt-dlp is not installed" in rec.error
    assert rec.verified is False


@pytest.mark.asyncio
async def test_direct_lane_requires_client_without_faking():
    rec = await get_video("https://cdn.example.com/clip.mp4", client=None)
    assert rec.lane == "direct"
    assert rec.path == ""
    assert "needs the session client" in rec.error


def test_video_view_exposes_wire_fields():
    rec = VideoDownload(
        url="https://example.com/w", title="T", extractor="archive.org",
        path="/tmp/x.mp4", bytes=10, sha256="ab" * 32, verified=True,
    )
    view = video_view(rec)
    assert view["url"] == "https://example.com/w"
    assert view["sha256"] == "ab" * 32
    assert view["title"] == "T"
    assert "cookies" not in json.dumps(view)


def test_shell_video_command_registered():
    from webresearch.shell import COMMANDS

    assert "video" in COMMANDS


def test_default_format_prefers_muxed_overall():
    # bv*+ba/b: best video+audio merge, falling back to best single file.
    assert DEFAULT_FORMAT == "bv*+ba/b"


@pytest.mark.skipif(_ytdlp_version() == "", reason="yt-dlp (video extra) not installed")
def test_ytdlp_version_detectable_when_installed():
    assert _ytdlp_version().count(".") >= 1
