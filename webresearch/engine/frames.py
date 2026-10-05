"""Small frame tree for the QuickJS engine.

Each iframe is fetched into its own HTML document and receives a fresh QuickJS
context. The implementation is intentionally bounded: it is an inspection
engine, not a layout or painting engine.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import unquote_to_bytes, urlencode, urljoin, urlparse

import httpx

from webresearch.engine.runtime import run_scripts
from webresearch.engine.tree import collect_iframes, extract_scripts, html_to_tree, walk
from webresearch.fetch import fetch
from webresearch.types import FrameInfo

MAX_FRAMES = 16
MAX_FRAME_DEPTH = 3
MAX_EXCERPT = 4000

_CHALLENGE_NEEDLES = (
    "recaptcha",
    "hcaptcha",
    "h-captcha",
    "turnstile",
    "cf-challenge",
    "datadome",
)


@dataclass(slots=True)
class FrameDocument:
    ref: str
    parent_ref: str
    url: str
    title: str = ""
    name: str = ""
    depth: int = 1
    status: int = 0
    content_type: str = ""
    html: str = ""
    js_info: dict = field(default_factory=dict)
    error: str = ""

    @property
    def js_ran(self) -> bool:
        return bool(self.js_info.get("ran"))

    @property
    def challenge(self) -> bool:
        blob = f"{self.url} {self.title} {self.html}".lower()
        return any(needle in blob for needle in _CHALLENGE_NEEDLES)

    def to_info(self) -> FrameInfo:
        return FrameInfo(
            ref=self.ref,
            parent_ref=self.parent_ref,
            url=self.url,
            title=self.title,
            name=self.name,
            depth=self.depth,
            status=self.status,
            content_type=self.content_type,
            js_ran=self.js_ran,
            scripts=int(self.js_info.get("scripts") or 0),
            challenge=self.challenge,
            error=self.error,
            html_excerpt=_compact_excerpt(self.html),
        )


@dataclass(slots=True)
class _FrameSeed:
    parent_ref: str
    parent_url: str
    depth: int
    src: str = ""
    srcdoc: str = ""
    title: str = ""
    name: str = ""


def _compact_excerpt(html: str) -> str:
    text = " ".join((html or "").split())
    return text[:MAX_EXCERPT]


def _site_key_nodes(html: str) -> list[tuple[str, str]]:
    """Return (site key, data-s) for reCAPTCHA containers."""
    found: list[tuple[str, str]] = []
    tree = html_to_tree(html)
    for node in walk(tree):
        attrs = node.get("a") or {}
        key = attrs.get("data-sitekey") or ""
        if not key:
            continue
        blob = " ".join(
            [
                node.get("t") or "",
                attrs.get("class") or "",
                attrs.get("id") or "",
                attrs.get("data-callback") or "",
            ]
        ).lower()
        if "captcha" in blob or "sitekey" in str(attrs).lower():
            found.append((key, attrs.get("data-s") or ""))
    return found


def _recaptcha_anchor_url(html: str, parent_url: str, site_key: str, data_s: str) -> str:
    scripts, _ = extract_scripts(html)
    script_urls = [item.get("src") or "" for item in scripts]
    enterprise = any("recaptcha/enterprise" in src.lower() for src in script_urls)
    host = "www.recaptcha.net" if any("recaptcha.net" in src.lower() for src in script_urls) else "www.google.com"
    path = "/recaptcha/enterprise/anchor" if enterprise else "/recaptcha/api2/anchor"

    parsed = urlparse(parent_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    origin = f"{parsed.scheme}://{parsed.hostname or ''}:{port}"
    encoded_origin = base64.urlsafe_b64encode(origin.encode()).decode().rstrip("=") + "."
    params = {
        "ar": "1",
        "k": site_key,
        "co": encoded_origin,
        "hl": "en",
        "size": "normal",
        "cb": "webresearch",
    }
    if data_s:
        params["s"] = data_s
    return f"https://{host}{path}?{urlencode(params)}"


def _discover_seeds(
    html: str,
    parent_url: str,
    *,
    parent_ref: str,
    depth: int,
    synthetic_captcha: bool,
) -> list[_FrameSeed]:
    seeds = [
        _FrameSeed(
            parent_ref=parent_ref,
            parent_url=parent_url,
            depth=depth,
            src=item.get("src") or "",
            srcdoc=unescape(item.get("srcdoc") or ""),
            title=item.get("title") or "",
            name=item.get("name") or "",
        )
        for item in collect_iframes(html)
    ]

    # Large challenge scripts often stop before injecting their iframe in the
    # mini-DOM. Expose a deterministic anchor document from the public site key
    # so an agent can still inspect the widget structure.
    low_url = parent_url.lower()
    actual_urls = {urljoin(parent_url, seed.src) for seed in seeds if seed.src}
    has_captcha_frame = any(
        "captcha" in f"{seed.src} {seed.title} {seed.name}".lower() for seed in seeds
    )
    if synthetic_captcha and "recaptcha/" not in low_url and not has_captcha_frame:
        for site_key, data_s in _site_key_nodes(html):
            anchor = _recaptcha_anchor_url(html, parent_url, site_key, data_s)
            if anchor not in actual_urls:
                seeds.append(
                    _FrameSeed(
                        parent_ref=parent_ref,
                        parent_url=parent_url,
                        depth=depth,
                        src=anchor,
                        title="reCAPTCHA anchor (derived from data-sitekey)",
                        name="recaptcha-anchor",
                    )
                )
                actual_urls.add(anchor)
    return seeds


def _decode_data_url(url: str) -> tuple[str, str]:
    header, _, payload = url.partition(",")
    content_type = header[5:].split(";", 1)[0] or "text/plain"
    raw = base64.b64decode(payload) if ";base64" in header.lower() else unquote_to_bytes(payload)
    return raw.decode("utf-8", errors="replace"), content_type


async def load_frames(
    html: str,
    url: str,
    client: httpx.AsyncClient,
    *,
    js: str = "auto",
    max_frames: int = MAX_FRAMES,
    max_depth: int = MAX_FRAME_DEPTH,
    synthetic_captcha: bool = True,
) -> list[FrameDocument]:
    """Fetch a bounded, flat frame tree with parent refs."""
    queue = _discover_seeds(
        html,
        url,
        parent_ref="",
        depth=1,
        synthetic_captcha=synthetic_captcha,
    )
    documents: list[FrameDocument] = []
    seen: set[tuple[str, str]] = set()

    while queue and len(documents) < max_frames:
        seed = queue.pop(0)
        frame_url = urljoin(seed.parent_url, seed.src) if seed.src else seed.parent_url
        identity = (seed.parent_ref, frame_url if seed.src else f"srcdoc:{hash(seed.srcdoc)}")
        if identity in seen:
            continue
        seen.add(identity)

        ref = f"f{len(documents) + 1}"
        document = FrameDocument(
            ref=ref,
            parent_ref=seed.parent_ref,
            url=frame_url,
            title=seed.title,
            name=seed.name,
            depth=seed.depth,
        )
        try:
            if seed.srcdoc:
                document.status = 200
                document.content_type = "text/html"
                document.html = seed.srcdoc
            elif not seed.src or frame_url.startswith("about:"):
                document.status = 200
                document.content_type = "text/html"
                document.html = ""
            elif frame_url.startswith("data:"):
                document.html, document.content_type = _decode_data_url(frame_url)
                document.status = 200
            elif urlparse(frame_url).scheme in {"http", "https"}:
                result = await fetch(
                    frame_url,
                    client=client,
                    headers={"Referer": seed.parent_url},
                )
                document.url = result.final_url or frame_url
                document.status = result.status
                document.content_type = result.content_type
                document.html = result.body
            else:
                document.error = f"unsupported frame URL scheme: {urlparse(frame_url).scheme}"

            if document.html and "html" in document.content_type.lower() and js != "never":
                document.html, document.js_info = await run_scripts(
                    document.html,
                    document.url,
                    client,
                    force=(js == "always"),
                )
        except Exception as exc:
            document.error = f"{type(exc).__name__}: {exc}"[:500]

        documents.append(document)
        if document.html and seed.depth < max_depth:
            queue.extend(
                _discover_seeds(
                    document.html,
                    document.url,
                    parent_ref=ref,
                    depth=seed.depth + 1,
                    synthetic_captcha=synthetic_captcha,
                )
            )

    return documents


def frame_infos(documents: list[FrameDocument]) -> list[FrameInfo]:
    return [document.to_info() for document in documents]

