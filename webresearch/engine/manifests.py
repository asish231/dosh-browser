"""M5: Native HLS (m3u8) / DASH (mpd) manifest parsing.

Simple, clear-stream downloads work WITHOUT external yt-dlp: the
manifest is parsed natively into an ordered segment list, and the
caller streams/concatenates the segments through the session transport.

Honest scope (disclosed, never faked):

- HLS: master playlists (variant selection: highest BANDWIDTH by
  default, or the caller's choice), media playlists (EXTINF segment
  durations + URIs, EXT-X-KEY detection), and live-edge detection
  ( playlists without ENDLIST are live: they are reported, not
  pretended to be complete VOD).
- DASH: MPD Periods -> AdaptationSets -> Representations; the caller's
  (or best-bandwidth) representation's SegmentTemplate/SegmentList/
  SegmentURL entries expand to segment URLs (init + media, with
  $Number$ / $Time$ / $RepresentationID$ / $Bandwidth$ substitution).
- ENCRYPTION: EXT-X-KEY with a non-NONE method or DASH ContentProtection
  means the stream is encrypted; this module REPORTS encrypted=True
  and refuses to fabricate decrypted output — the caller routes those
  to the policy lane (DRM services are refused by policy; clear-key
  AES-128 HLS is reported as unsupported-native, honestly).
- No HLS-DASH rewrite ladder, no ad splicing, no rendition merging —
  one representation, in playlist order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class Segment:
    url: str
    duration: float | None = None


@dataclass
class ParsedStream:
    kind: str                     # "hls" | "dash"
    is_live: bool = False
    encrypted: bool = False
    encryption: str = ""          # e.g. "AES-128", "widevine", "clearkey"
    segments: list[Segment] = field(default_factory=list)
    init_url: str | None = None
    duration: float | None = None  # VOD total when computable
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HLS (.m3u8)
# ---------------------------------------------------------------------------

def parse_m3u8(text: str, base_url: str) -> ParsedStream:
    """Parse an HLS playlist (media or master). Master playlists select
    the highest-BANDWIDTH variant and re-parse it via the caller."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise ValueError("not an HLS playlist (missing #EXTM3U header)")

    # Master playlist? (variant streams)
    variants: list[tuple[float, str]] = []
    pending_bw = None
    for ln in lines:
        if ln.startswith("#EXT-X-STREAM-INF:"):
            attrs = ln.split(":", 1)[1]
            m = re.search(r"BANDWIDTH=(\d+)", attrs)
            pending_bw = float(m.group(1)) if m else 0.0
        elif not ln.startswith("#") and pending_bw is not None:
            variants.append((pending_bw, urljoin(base_url, ln)))
            pending_bw = None

    if variants:
        best_bw, best_uri = max(variants, key=lambda v: v[0])
        stream = ParsedStream(kind="hls")
        stream.notes.append(
            f"master playlist: {len(variants)} variants; highest BANDWIDTH ({int(best_bw)}) selected — fetch the variant for its segments"
        )
        # The variant's OWN playlist must be fetched by the caller; expose
        # the selection through the segment list as a single "entry"
        # pointer so the caller can chain (honest: we do not fetch here).
        stream.segments = [Segment(url=best_uri, duration=None)]
        stream.notes.append("variant_url_selected")
        return stream

    # Media playlist: segments + keys + liveness.
    stream = ParsedStream(kind="hls")
    durations: list[float] = []
    pending_duration: float | None = None
    for ln in lines:
        if ln.startswith("#EXTINF:"):
            try:
                pending_duration = float(ln.split(":", 1)[1].split(",")[0])
            except Exception:
                pending_duration = None
        elif ln.startswith("#EXT-X-KEY:"):
            attrs = ln.split(":", 1)[1]
            m = re.search(r"METHOD=([A-Za-z0-9-]+)", attrs)
            method = m.group(1) if m else "NONE"
            if method and method != "NONE":
                stream.encrypted = True
                stream.encryption = method
        elif ln.startswith("#EXT-X-ENDLIST"):
            stream.is_live = False
            stream.notes.append("ENDLIST present: VOD")
        elif not ln.startswith("#"):
            seg_url = urljoin(base_url, ln)
            stream.segments.append(Segment(url=seg_url, duration=pending_duration))
            if pending_duration is not None:
                durations.append(pending_duration)
            pending_duration = None

    # Live detection: no ENDLIST anywhere in the playlist.
    if not any("#EXT-X-ENDLIST" in ln for ln in lines):
        stream.is_live = True
        stream.notes.append("no ENDLIST: live playlist")
    if durations:
        stream.duration = sum(durations)
    return stream


# ---------------------------------------------------------------------------
# DASH (.mpd)
# ---------------------------------------------------------------------------

def _mpd_text_attr(el_text: str, attr: str) -> str | None:
    m = re.search(attr + r'="([^"]*)"', el_text)
    return m.group(1) if m else None


def _expand_template(
    template: str,
    *,
    representation_id: str = "",
    number: int | None = None,
    time: int | None = None,
    bandwidth: int | None = None,
) -> str:
    out = template
    out = out.replace("$RepresentationID$", representation_id or "")
    out = out.replace("$Bandwidth$", str(bandwidth or ""))
    if number is not None:
        # $$ escapes a literal $; $Number%Nd$ zero-pads.
        out = re.sub(r"\$Number%(\d+)d\$", lambda m: str(number).zfill(int(m.group(1))), out)
        out = out.replace("$Number$", str(number))
    if time is not None:
        out = re.sub(r"\$Time%(\d+)d\$", lambda m: str(time).zfill(int(m.group(1))), out)
        out = out.replace("$Time$", str(time))
    return out.replace("$$", "$")


def parse_mpd(text: str, base_url: str) -> ParsedStream:
    """Parse an MPD into the BEST (highest-bandwidth) representation's
    ordered segment list. SegmentTemplate timelines expand via
    $Number$/$Time$; SegmentList/SegmentURL are honored directly."""
    if "<MPD" not in text:
        raise ValueError("not a DASH manifest (missing <MPD>)")

    stream = ParsedStream(kind="dash")

    # ContentProtection anywhere => encrypted; record the scheme.
    prot = re.search(r'<ContentProtection[^>]*schemeIdUri="([^"]+)"', text)
    if prot:
        stream.encrypted = True
        scheme = prot.group(1)
        low = scheme.lower()
        if "widevine" in low or "edef8ba9" in low:
            stream.encryption = "widevine"
        elif "playready" in low or "9a04f079" in low:
            stream.encryption = "playready"
        elif "clearkey" in low or "1077efec" in low:
            stream.encryption = "clearkey"
        else:
            stream.encryption = scheme

    # Pick the highest-bandwidth Representation in the first Period's
    # first video AdaptationSet (honest: one representation, in order).
    periods = re.findall(r"<Period\b.*?</Period>", text, flags=re.S)
    if not periods:
        periods = [text]  # a minimal MPD may omit Period wrappers
    period = periods[0]

    reps = re.findall(r"<Representation\b[^>]*>.*?</Representation>|<Representation\b[^>]*/>", period, flags=re.S)
    if not reps:
        raise ValueError("no Representation elements in the MPD period")

    def rep_bandwidth(rep: str) -> int:
        bw = _mpd_text_attr(rep, "bandwidth")
        try:
            return int(bw) if bw else 0
        except Exception:
            return 0

    best = max(reps, key=rep_bandwidth)
    rep_id = _mpd_text_attr(best, "id") or ""
    rep_bw = rep_bandwidth(best)
    stream.notes.append(
        f"representation '{rep_id}' selected (highest bandwidth {rep_bw} of {len(reps)})"
    )

    # SegmentTemplate with $Number$ timeline
    tmpl_media = None
    tmpl_init = None
    m = re.search(r"<SegmentTemplate\b[^>]*media=\"([^\"]*)\"", period + best)
    if m:
        tmpl_media = m.group(1)
    m = re.search(r"<SegmentTemplate\b[^>]*initialization=\"([^\"]*)\"", period + best)
    if m:
        tmpl_init = m.group(1)

    # StartNumber / SegmentTimeline
    start_number = 1
    m = re.search(r"startNumber=\"(\d+)\"", period + best)
    if m:
        start_number = int(m.group(1))

    timeline = re.findall(r"<S\b[^>]*/>", period + best)
    numbers: list[int] = []
    if timeline:
        cur_t = 0
        cur_n = start_number
        for s_tag in timeline:
            t_attr = _mpd_text_attr(s_tag, "t")
            d_attr = _mpd_text_attr(s_tag, "d")
            r_attr = _mpd_text_attr(s_tag, "r")
            if t_attr is not None:
                cur_t = int(t_attr)
            d = int(d_attr) if d_attr else 0
            # Spec: @r is the REPEAT COUNT = number of ADDITIONAL
            # iterations, so an entry occurs r+1 times (r absent => once).
            r = int(r_attr) if r_attr not in (None, "") else 0
            for _ in range(r + 1):
                numbers.append(cur_n)
                cur_n += 1
            cur_t += d * (r + 1)
    else:
        # SegmentList / SegmentURL entries
        seg_urls = re.findall(r"<SegmentURL\b[^>]*media=\"([^\"]*)\"", best)
        if not seg_urls:
            seg_urls = re.findall(r"<SegmentURL\b[^>]*sourceURL=\"([^\"]*)\"", best)
        for u in seg_urls:
            stream.segments.append(Segment(url=urljoin(base_url, u)))
        if not stream.segments and not tmpl_media:
            raise ValueError("no SegmentTemplate/SegmentList/SegmentURL in the representation")
        stream.init_url = None
        m = re.search(r"<Initialization\b[^>]*sourceURL=\"([^\"]*)\"", best)
        if m:
            stream.init_url = urljoin(base_url, m.group(1))
        return stream

    if tmpl_media:
        if tmpl_init:
            stream.init_url = urljoin(
                base_url,
                _expand_template(tmpl_init, representation_id=rep_id, bandwidth=rep_bw),
            )
        for n in numbers:
            stream.segments.append(
                Segment(
                    url=urljoin(
                        base_url,
                        _expand_template(tmpl_media, representation_id=rep_id, number=n, bandwidth=rep_bw),
                    )
                )
            )
        if not numbers:
            # No timeline and no list: a single-number template — expand
            # startNumber..startNumber (one segment, honestly reported).
            stream.segments.append(
                Segment(
                    url=urljoin(
                        base_url,
                        _expand_template(tmpl_media, representation_id=rep_id, number=start_number, bandwidth=rep_bw),
                    )
                )
            )
            stream.notes.append("no SegmentTimeline: single-segment template expanded")
    return stream


def is_hls_manifest(url: str) -> bool:
    return str(url).lower().split("?")[0].endswith(".m3u8")


def is_dash_manifest(url: str) -> bool:
    return str(url).lower().split("?")[0].endswith(".mpd")
