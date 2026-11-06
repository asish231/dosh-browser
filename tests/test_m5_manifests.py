"""M5: native HLS/DASH manifest parsing — hermetic tests.

Pins the parser contract: master-playlist variant selection, media
playlist segments/durations/liveness, EXT-X-KEY encryption detection
(reported, never faked), DASH representation selection + SegmentTemplate
$Number$/$RepresentationID$/$Bandwidth$ expansion, SegmentTimeline
expansion, SegmentList/SegmentURL handling, and ContentProtection
reporting. DRM/encrypted streams are REFUSED downstream; the parser
reports them honestly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.manifests import (
    ParsedStream,
    is_dash_manifest,
    is_hls_manifest,
    parse_m3u8,
    parse_mpd,
)


MASTER_M3U8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720
720/main.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360
360/main.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
1080/main.m3u8
"""

MEDIA_M3U8 = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.009,
seg-1.ts
#EXTINF:9.009,
seg-2.ts
#EXTINF:3.0,
seg-3.ts
#EXT-X-ENDLIST
"""

LIVE_M3U8 = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:5.76,
live-001.ts
#EXTINF:5.76,
live-002.ts
"""

ENCRYPTED_M3U8 = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="https://k.example/key",IV=0x1234
#EXTINF:9.0,
enc-1.ts
#EXT-X-ENDLIST
"""


def test_master_playlist_selects_highest_bandwidth():
    s = parse_m3u8(MASTER_M3U8, "https://cdn.example/hls/index.m3u8")
    assert s.kind == "hls"
    assert len(s.segments) == 1, "a master playlist exposes its selected variant"
    assert s.segments[0].url == "https://cdn.example/hls/1080/main.m3u8"
    assert any("5000000" in n for n in s.notes)


def test_media_playlist_segments_and_durations():
    s = parse_m3u8(MEDIA_M3U8, "https://cdn.example/hls/720/main.m3u8")
    assert [seg.url for seg in s.segments] == [
        "https://cdn.example/hls/720/seg-1.ts",
        "https://cdn.example/hls/720/seg-2.ts",
        "https://cdn.example/hls/720/seg-3.ts",
    ]
    assert [seg.duration for seg in s.segments] == [9.009, 9.009, 3.0]
    assert s.is_live is False, "ENDLIST marks VOD"
    assert abs((s.duration or 0) - 21.018) < 1e-6


def test_live_playlist_detected_and_reported():
    s = parse_m3u8(LIVE_M3U8, "https://cdn.example/live/x.m3u8")
    assert s.is_live is True, "no ENDLIST => live"
    assert any("live" in n for n in s.notes)
    assert len(s.segments) == 2


def test_encrypted_hls_reported_never_faked():
    s = parse_m3u8(ENCRYPTED_M3U8, "https://cdn.example/enc/x.m3u8")
    assert s.encrypted is True
    assert s.encryption == "AES-128"
    # The parser still reports the segment list (the CALLER refuses the
    # download per policy; the parser does not pretend to have decrypted).
    assert len(s.segments) == 1 and s.segments[0].url.endswith("enc-1.ts")


MPD_TEMPLATE = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT12.0S">
  <Period id="p0" start="PT0S">
    <AdaptationSet mimeType="video/mp4">
      <SegmentTemplate initialization="init-$RepresentationID$.mp4"
                       media="seg-$RepresentationID$-$Number$.m4s"
                       startNumber="1">
        <SegmentTimeline>
          <S t="0" d="60000000" r="1" />
          <S d="30000000" />
        </SegmentTimeline>
      </SegmentTemplate>
      <Representation id="400p" bandwidth="800000" />
      <Representation id="1080p" bandwidth="4000000" />
    </AdaptationSet>
  </Period>
</MPD>
"""

MPD_PROTECTED = """<?xml version="1.0"?>
<MPD>
  <Period>
    <AdaptationSet>
      <ContentProtection schemeIdUri="urn:uuid:EDEF8BA9-79D6-4ACE-A3C8-27DCD51D21ED" />
      <Representation id="r1" bandwidth="100000">
        <SegmentTemplate media="seg-$Number$.m4s" startNumber="1" />
        <SegmentTimeline><S d="40000000" r="2" /></SegmentTimeline>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

MPD_SEGMENT_LIST = """<?xml version="1.0"?>
<MPD>
  <Period>
    <AdaptationSet>
      <Representation id="r9" bandwidth="900000">
        <SegmentList duration="10">
          <Initialization sourceURL="init.mp4" />
          <SegmentURL media="chunk-1.m4s" />
          <SegmentURL media="chunk-2.m4s" />
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def test_mpd_template_expansion():
    s = parse_mpd(MPD_TEMPLATE, "https://cdn.example/dash/manifest.mpd")
    assert s.kind == "dash"
    assert s.encrypted is False
    # r=1 with d=60s means TWO segments from the first S, plus one more.
    assert [seg.url for seg in s.segments] == [
        "https://cdn.example/dash/seg-1080p-1.m4s",
        "https://cdn.example/dash/seg-1080p-2.m4s",
        "https://cdn.example/dash/seg-1080p-3.m4s",
    ], f"$RepresentationID$/$Number$ expansion: {[u.url for u in s.segments]}"
    assert s.init_url == "https://cdn.example/dash/init-1080p.mp4"
    assert any("1080p" in n and "4000000" in n for n in s.notes)


def test_mpd_content_protection_reported():
    s = parse_mpd(MPD_PROTECTED, "https://drm.example/x.mpd")
    assert s.encrypted is True
    assert s.encryption == "widevine", f"the widevine scheme is named: {s.encryption}"


def test_mpd_segment_list_and_init():
    s = parse_mpd(MPD_SEGMENT_LIST, "https://cdn.example/slist/manifest.mpd")
    assert [seg.url for seg in s.segments] == [
        "https://cdn.example/slist/chunk-1.m4s",
        "https://cdn.example/slist/chunk-2.m4s",
    ]
    assert s.init_url == "https://cdn.example/slist/init.mp4"


def test_manifest_url_detection():
    assert is_hls_manifest("https://x.test/a.m3u8?tok=1") is True
    assert is_dash_manifest("https://x.test/a.mpd") is True
    assert is_hls_manifest("https://x.test/a.mpd") is False
    assert is_dash_manifest("https://x.test/a.m3u8") is False


def test_bad_inputs_raise():
    import pytest

    with pytest.raises(ValueError):
        parse_m3u8("not a playlist", "https://x.test/a.m3u8")
    with pytest.raises(ValueError):
        parse_mpd("<html>no mpd</html>", "https://x.test/a.mpd")
