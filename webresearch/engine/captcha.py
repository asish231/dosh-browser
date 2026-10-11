"""Structured CAPTCHA visibility and solving for agents.

Inspection: reports challenge structure and resources.
Solving: uses an external vision model to identify tiles matching the prompt,
then returns structured selections the session can actuate.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from webresearch.engine.frames import FrameDocument
from webresearch.engine.tree import extract_scripts, html_to_tree, tree_to_html, walk
from webresearch.types import (
    CaptchaCodeDump,
    CaptchaCodePart,
    CaptchaFrame,
    CaptchaReport,
    CaptchaScript,
    CaptchaSolution,
    CaptchaTile,
    CaptchaTileSelection,
)

MAX_SPRITE_BYTES = 3_000_000
MAX_CODE_HTML = 80_000
MAX_WIDGET_HTML = 12_000
MAX_INLINE_SCRIPT = 8_000
MAX_SPRITE_B64_BYTES = 200_000

_WIDGET_NEEDLES = (
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "h-captcha",
    "turnstile",
    "cf-turnstile",
    "imageselect",
    "captcha-token",
    "rc-image-tile",
    "rc-anchor",
    "grecaptcha",
    "datadome",
    "cf-challenge",
)

_SKIP_CODE_TAGS = {"document", "html", "head", "body", "style", "#text"}

_BUTTON_NAMES = {
    "recaptcha-reload-button": "reload",
    "recaptcha-audio-button": "audio",
    "recaptcha-image-button": "image",
    "recaptcha-help-button": "help",
    "recaptcha-verify-button": "verify",
}


@dataclass(slots=True)
class CaptchaInspection:
    report: CaptchaReport
    sprite: bytes | None = None
    parent_url: str = ""
    parent_html: str = ""
    frames: list[FrameDocument] | None = None


def _text_of(node: dict) -> str:
    if node.get("t") == "#text":
        return node.get("x") or ""
    return " ".join(
        " ".join(_text_of(child) for child in node.get("c") or [] if isinstance(child, dict)).split()
    )


def _descendants(node: dict):
    for child in node.get("c") or []:
        if isinstance(child, dict):
            yield child
            yield from _descendants(child)


_SCRIPT_BLOCK_RE = re.compile(r"(?is)<(script|style|noscript)\b([^>]*)>(.*?)</\1\s*>")


def _markup_only(html: str) -> str:
    """Page markup with the *contents* of script/style elements removed.

    The tags and their attributes survive — ``<script src="https://www.google.com/
    recaptcha/api.js">`` is genuine evidence of a widget — but the inline code does
    not. A provider name that appears only inside inline JavaScript is page
    configuration, not a challenge rendered on the page. Wikipedia ships
    ``wgConfirmEditHCaptchaSiteKey`` in its config blob to gate *anonymous edits*;
    matching that string flagged every article as an hCaptcha challenge.
    """
    return _SCRIPT_BLOCK_RE.sub(lambda m: f"<{m.group(1)}{m.group(2) or ''}></{m.group(1)}>", html)


# A provider is only reported present when the markup carries structural
# evidence: a widget element, a provider asset URL in an attribute, a provider
# iframe, or an interstitial whose *visible text* says so.
_WIDGET_CLASS_TOKENS: dict[str, set[str]] = {
    "recaptcha": {"g-recaptcha", "grecaptcha", "recaptcha", "rc-anchor", "rc-imageselect"},
    "hcaptcha": {"h-captcha", "hcaptcha"},
    "turnstile": {"cf-turnstile", "turnstile"},
}

_ASSET_NEEDLES: dict[str, tuple[str, ...]] = {
    "recaptcha": ("recaptcha", "grecaptcha"),
    "hcaptcha": ("hcaptcha",),
    "turnstile": ("challenges.cloudflare.com",),
    "datadome": ("captcha-delivery.com", "datadome"),
}

_INTERSTITIAL_NEEDLES: dict[str, tuple[str, ...]] = {
    "google_sorry": ("google.com/sorry", "unusual traffic", "our systems have detected"),
    "cloudflare": ("just a moment", "checking your browser", "cf-challenge"),
}

_URL_ATTRS = ("src", "href", "action", "data-src")


def _class_tokens_of(attrs: dict) -> set[str]:
    raw = attrs.get("class") or ""
    if isinstance(raw, (list, tuple)):
        raw = " ".join(str(x) for x in raw)
    return {tok for tok in str(raw).lower().split() if tok}


def _structural_kind(documents: list[tuple[str, str]]) -> str:
    """Return the provider with structural evidence in the markup, else ''."""
    found: dict[str, bool] = {}
    for url, html in documents:
        markup = _markup_only(html)
        tree = html_to_tree(markup)
        for node in walk(tree):
            attrs = node.get("a") or {}
            tokens = _class_tokens_of(attrs)
            for provider, widget_tokens in _WIDGET_CLASS_TOKENS.items():
                if tokens & widget_tokens:
                    found.setdefault(provider, True)
            for attr in _URL_ATTRS:
                value = str(attrs.get(attr) or "").lower()
                if not value:
                    continue
                for provider, needles in _ASSET_NEEDLES.items():
                    if any(needle in value for needle in needles):
                        found.setdefault(provider, True)
        blob = _text_of(tree).lower()
        for provider, needles in _INTERSTITIAL_NEEDLES.items():
            if any(needle in blob for needle in needles):
                found.setdefault(provider, True)
    if "recaptcha" in found:
        return "recaptcha"
    if "hcaptcha" in found:
        return "hcaptcha"
    if "turnstile" in found:
        return "turnstile"
    if "datadome" in found:
        return "datadome"
    if "cloudflare" in found:
        return "cloudflare"
    if "google_sorry" in found:
        return "google_sorry"
    return ""


def _provider(documents: list[tuple[str, str]]) -> str:
    return _structural_kind(documents)


def _frame_kind(url: str, html: str) -> str:
    blob = f"{url} {html[:4000]}".lower()
    if "anchor" in blob:
        return "anchor"
    if "bframe" in blob or "imageselect" in blob or "challenge" in blob:
        return "challenge"
    if "audio" in blob:
        return "audio"
    return "iframe"


def _challenge_excerpt(html: str) -> str:
    tree = html_to_tree(html)
    snippets: list[str] = []
    for node in walk(tree):
        if node.get("t") == "#text":
            continue
        attrs = node.get("a") or {}
        blob = " ".join(
            [
                node.get("t") or "",
                attrs.get("id") or "",
                attrs.get("class") or "",
                attrs.get("role") or "",
                attrs.get("name") or "",
            ]
        ).lower()
        if any(
            needle in blob
            for needle in (
                "recaptcha",
                "hcaptcha",
                "turnstile",
                "imageselect",
                "checkbox",
                "captcha-token",
            )
        ):
            snippet = " ".join(tree_to_html(node).split())
            if snippet and snippet not in snippets:
                snippets.append(snippet[:1800])
        if len(snippets) >= 5:
            break
    return "\n".join(snippets)[:6000] or " ".join(html.split())[:4000]


def _site_key_and_callback(documents: list[tuple[str, str]]) -> tuple[str, str]:
    site_key = ""
    callback = ""
    for url, html in documents:
        tree = html_to_tree(html)
        for node in walk(tree):
            attrs = node.get("a") or {}
            site_key = site_key or attrs.get("data-sitekey") or ""
            callback = callback or attrs.get("data-callback") or ""
        query = parse_qs(urlparse(url).query)
        site_key = site_key or (query.get("k") or query.get("sitekey") or [""])[0]
    return site_key, callback


def _checkbox(documents: list[tuple[str, str]]) -> tuple[str, bool | None]:
    label = ""
    checked: bool | None = None
    for _url, html in documents:
        tree = html_to_tree(html)
        for node in walk(tree):
            attrs = node.get("a") or {}
            node_id = (attrs.get("id") or "").lower()
            role = (attrs.get("role") or "").lower()
            cls = (attrs.get("class") or "").lower()
            text = _text_of(node)
            class_names = cls.split()
            if (
                role == "checkbox"
                or node_id == "recaptcha-anchor"
                or "recaptcha-checkbox" in class_names
            ):
                aria = (attrs.get("aria-checked") or "").lower()
                if aria in {"true", "false"}:
                    checked = aria == "true"
                elif attrs.get("checked") is not None:
                    checked = str(attrs.get("checked")).lower() != "false"
                label = (
                    label
                    or attrs.get("aria-label")
                    or attrs.get("title")
                    or text
                    or "I'm not a robot"
                )
            elif "recaptcha-checkbox-label" in cls and "not a robot" in text.lower():
                label = label or "I'm not a robot"
    return label[:240], checked


def _prompt(documents: list[tuple[str, str]]) -> str:
    for _url, html in documents:
        tree = html_to_tree(html)
        for node in walk(tree):
            attrs = node.get("a") or {}
            blob = f"{attrs.get('id', '')} {attrs.get('class', '')}".lower()
            if any(
                needle in blob
                for needle in ("imageselect-desc", "imageselect-instructions", "challenge-prompt", "prompt-text")
            ):
                text = _text_of(node)
                if text:
                    return text[:500]
        visible = _text_of(tree)
        match = re.search(
            r"(select all (?:images|squares).*?)(?:verify|skip|reload|$)",
            visible,
            flags=re.IGNORECASE,
        )
        if match:
            return " ".join(match.group(1).split())[:500]
    return ""


def _style_value(style: str, name: str) -> str:
    match = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*:\s*([^;]+)", style, flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _sprite_and_tiles(documents: list[tuple[str, str]]) -> tuple[str, str, list[CaptchaTile]]:
    sprite_url = ""
    referer = ""
    tiles: list[CaptchaTile] = []
    for url, html in documents:
        tree = html_to_tree(html)
        tile_nodes: list[dict] = []
        for node in walk(tree):
            attrs = node.get("a") or {}
            cls = (attrs.get("class") or "").lower()
            if "rc-imageselect-tile" in cls and node.get("t") != "img":
                tile_nodes.append(node)
            if node.get("t") == "img" and (
                "rc-image-tile" in cls or "captcha" in (attrs.get("id") or "").lower()
            ):
                src = attrs.get("src") or ""
                if src and not sprite_url:
                    sprite_url = urljoin(url, src)
                    referer = url

        for index, tile_node in enumerate(tile_nodes):
            attrs = tile_node.get("a") or {}
            image = next(
                (
                    child
                    for child in _descendants(tile_node)
                    if child.get("t") == "img"
                    and "rc-image-tile" in ((child.get("a") or {}).get("class") or "").lower()
                ),
                None,
            )
            image_attrs = (image or {}).get("a") or {}
            style = image_attrs.get("style") or attrs.get("style") or ""
            image_src = image_attrs.get("src") or ""
            if image_src and not sprite_url:
                sprite_url = urljoin(url, image_src)
                referer = url
            tiles.append(
                CaptchaTile(
                    id=attrs.get("id") or str(index),
                    top=_style_value(style, "top"),
                    left=_style_value(style, "left"),
                    row=index // 3,
                    column=index % 3,
                    role=attrs.get("role") or "button",
                )
            )
    return sprite_url, referer, tiles[:25]


def _buttons_and_hidden(documents: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    buttons: list[str] = []
    hidden: list[str] = []
    for _url, html in documents:
        tree = html_to_tree(html)
        for node in walk(tree):
            attrs = node.get("a") or {}
            tag = node.get("t") or ""
            node_id = attrs.get("id") or ""
            role = (attrs.get("role") or "").lower()
            if tag == "button" or role == "button" or node_id in _BUTTON_NAMES:
                label = (
                    _BUTTON_NAMES.get(node_id)
                    or attrs.get("aria-label")
                    or attrs.get("title")
                    or _text_of(node)
                    or node_id
                )
                label = " ".join(label.split())[:160]
                if label and label not in buttons:
                    buttons.append(label)

            name = attrs.get("name") or ""
            typ = (attrs.get("type") or "").lower()
            tokenish = "captcha" in f"{node_id} {name}".lower() or "token" in f"{node_id} {name}".lower()
            if tokenish and (typ == "hidden" or tag == "textarea"):
                value = attrs.get("value") or _text_of(node)
                field = (
                    f"{name or node_id or '(unnamed)'} "
                    f"({tag or 'field'}, value_length={len(value)})"
                )
                if field not in hidden:
                    hidden.append(field)
    return buttons[:20], hidden[:30]


async def inspect_captcha(
    html: str,
    url: str,
    client: httpx.AsyncClient,
    frames: list[FrameDocument] | None = None,
    *,
    fetch_sprite: bool = True,
    sprite_endpoint: str = "",
) -> CaptchaInspection:
    frames = frames or []
    documents = [(url, html)] + [(frame.url, frame.html) for frame in frames]
    kind = _provider(documents)
    if not kind:
        report = CaptchaReport(
            present=False,
            note="No supported CAPTCHA or bot challenge was detected.",
            view="CAPTCHA: not detected",
        )
        return CaptchaInspection(
            report=report,
            parent_url=url,
            parent_html=html,
            frames=frames,
        )

    site_key, callback = _site_key_and_callback(documents)
    checkbox_label, checkbox_checked = _checkbox(documents)
    prompt = _prompt(documents)
    sprite_url, sprite_referer, tiles = _sprite_and_tiles(documents)
    buttons, hidden_fields = _buttons_and_hidden(documents)

    captcha_frames: list[CaptchaFrame] = []
    for frame in frames:
        if frame.challenge or kind in f"{frame.url} {frame.html}".lower():
            info = frame.to_info().model_dump()
            info["kind"] = _frame_kind(frame.url, frame.html)
            info["html_excerpt"] = _challenge_excerpt(frame.html)
            captcha_frames.append(CaptchaFrame.model_validate(info))

    if (
        kind == "recaptcha"
        and not checkbox_label
        and any(frame.kind == "anchor" for frame in captcha_frames)
    ):
        checkbox_label = "I'm not a robot (anchor semantics; DOM not materialized)"

    sprite: bytes | None = None
    sprite_content_type = ""
    sprite_sha256 = ""
    sprite_error = ""
    if sprite_url and fetch_sprite:
        try:
            response = await client.get(sprite_url, headers={"Referer": sprite_referer or url})
            raw = response.content[: MAX_SPRITE_BYTES + 1]
            response_type = response.headers.get("content-type", "")
            is_image = response_type.split(";", 1)[0].strip().lower().startswith("image/")
            if response.is_success and is_image and len(raw) <= MAX_SPRITE_BYTES:
                sprite = raw
                sprite_content_type = response_type
                sprite_sha256 = hashlib.sha256(raw).hexdigest()
            elif len(raw) > MAX_SPRITE_BYTES:
                sprite_error = f"sprite exceeded {MAX_SPRITE_BYTES} byte limit"
            elif response.is_success and not is_image:
                sprite_error = f"sprite response was not an image ({response_type or 'unknown'})"
            else:
                sprite_error = f"sprite fetch returned HTTP {response.status_code}"
        except Exception as exc:
            sprite_error = f"sprite fetch failed: {type(exc).__name__}: {exc}"[:500]

    note = (
        "Inspection only: this exposes challenge structure and image metadata. "
        "It does not solve, bypass, submit, or return a valid g-recaptcha-response."
    )
    if sprite_error:
        note += f" {sprite_error}."

    report = CaptchaReport(
        present=True,
        kind=kind,
        site_key=site_key,
        callback=callback,
        prompt=prompt,
        checkbox_label=checkbox_label,
        checkbox_checked=checkbox_checked,
        sprite_url=sprite_url,
        sprite_bytes=len(sprite or b""),
        sprite_content_type=sprite_content_type,
        sprite_sha256=sprite_sha256,
        sprite_endpoint=sprite_endpoint if sprite else "",
        tiles=tiles,
        buttons=buttons,
        frames=captcha_frames,
        hidden_fields=hidden_fields,
        note=note,
    )
    report.view = captcha_view(report)
    return CaptchaInspection(
        report=report,
        sprite=sprite,
        parent_url=url,
        parent_html=html,
        frames=frames,
    )


def captcha_view(report: CaptchaReport) -> str:
    if not report.present:
        return "CAPTCHA: not detected"
    lines = [f"CAPTCHA: {report.kind} (inspection only)"]
    if report.site_key:
        lines.append(f"SITE_KEY: {report.site_key}")
    if report.callback:
        lines.append(f"CALLBACK: {report.callback}")
    for frame in report.frames:
        lines.append(
            f"[{frame.ref}] {frame.kind} status={frame.status} depth={frame.depth} "
            f"url={frame.url}"
        )
    if report.checkbox_label:
        lines.append(
            f"CHECKBOX: {report.checkbox_label} checked={report.checkbox_checked}"
        )
    if report.prompt:
        lines.append(f"PROMPT: {report.prompt}")
    if report.sprite_url:
        lines.append(
            f"SPRITE: {report.sprite_url} bytes={report.sprite_bytes} "
            f"type={report.sprite_content_type or 'unknown'}"
        )
    if report.tiles:
        lines.append(f"TILES: {len(report.tiles)}")
        for tile in report.tiles:
            lines.append(
                f"  [{tile.id}] row={tile.row} col={tile.column} "
                f"top={tile.top or '-'} left={tile.left or '-'}"
            )
    if report.buttons:
        lines.append(f"CONTROLS: {', '.join(report.buttons)}")
    if report.hidden_fields:
        lines.append(f"HIDDEN_FIELDS: {', '.join(report.hidden_fields)}")
    lines.append(f"NOTE: {report.note}")
    return "\n".join(lines)


def _node_blob(node: dict) -> str:
    attrs = node.get("a") or {}
    return " ".join(
        [
            node.get("t") or "",
            attrs.get("id") or "",
            attrs.get("class") or "",
            attrs.get("role") or "",
            attrs.get("name") or "",
            attrs.get("src") or "",
            attrs.get("title") or "",
            attrs.get("data-sitekey") or "",
            attrs.get("data-callback") or "",
        ]
    ).lower()


def _part_name(node: dict) -> str:
    tag = node.get("t") or ""
    attrs = node.get("a") or {}
    blob = _node_blob(node)
    if tag == "iframe":
        return "iframe"
    if attrs.get("data-sitekey") or "g-recaptcha" in blob or "h-captcha" in blob or "cf-turnstile" in blob:
        return "widget"
    if "recaptcha-anchor" in blob or (attrs.get("role") or "").lower() == "checkbox":
        return "checkbox"
    if "token" in blob:
        return "token"
    if "imageselect-desc" in blob or "prompt" in blob:
        return "prompt"
    if "imageselect-tile" in blob or (tag == "table" and "imageselect" in blob):
        return "tiles"
    if tag == "img" and "rc-image-tile" in blob:
        return "sprite-img"
    if tag == "button" or "recaptcha-reload" in blob or "recaptcha-verify" in blob:
        return "button"
    if tag == "script":
        return "script"
    return "markup"


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n<!-- truncated -->", True


def _widget_parts(html: str, *, ref: str, url: str, kind: str) -> list[CaptchaCodePart]:
    tree = html_to_tree(html)
    parts: list[CaptchaCodePart] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for node in walk(tree):
        tag = node.get("t") or ""
        if tag in _SKIP_CODE_TAGS:
            continue
        blob = _node_blob(node)
        if not any(needle in blob for needle in _WIDGET_NEEDLES):
            continue
        name = _part_name(node)
        counts[name] = counts.get(name, 0) + 1
        if name == "tiles" and counts[name] > 1:
            continue
        if name in {"sprite-img", "button", "markup"} and counts[name] > 4:
            continue
        markup = tree_to_html(node)
        key = markup[:500]
        if key in seen:
            continue
        seen.add(key)
        clipped, truncated = _clip(markup, MAX_WIDGET_HTML)
        parts.append(
            CaptchaCodePart(
                name=name,
                ref=ref,
                kind=kind,
                url=url,
                html=clipped,
                truncated=truncated,
            )
        )
        if len(parts) >= 24:
            break
    return parts


def _captcha_scripts(html: str, frame_ref: str, url: str) -> list[CaptchaScript]:
    scripts, _ = extract_scripts(html)
    out: list[CaptchaScript] = []
    for script in scripts:
        src = script.get("src") or ""
        code = script.get("code") or ""
        blob = f"{src} {code[:2000]} {url}".lower()
        if not any(needle in blob for needle in _WIDGET_NEEDLES):
            continue
        clipped, truncated = _clip(code, MAX_INLINE_SCRIPT)
        out.append(
            CaptchaScript(
                src=src,
                type=script.get("type") or "",
                code=clipped,
                frame_ref=frame_ref,
                truncated=truncated,
            )
        )
        if len(out) >= 12:
            break
    return out


def dump_captcha_code(inspection: CaptchaInspection, *, include_sprite_b64: bool = True) -> CaptchaCodeDump:
    """Return the raw challenge HTML/scripts the engine loaded. Inspection only."""
    report = inspection.report
    if not report.present:
        return CaptchaCodeDump(
            present=False,
            note="No supported CAPTCHA or bot challenge was detected.",
            code="CAPTCHA CODE: not detected",
        )

    parts: list[CaptchaCodePart] = []
    scripts: list[CaptchaScript] = []
    parent_html = inspection.parent_html or ""
    parent_url = inspection.parent_url or ""

    parent_widgets = _widget_parts(parent_html, ref="parent", url=parent_url, kind="parent")
    if parent_widgets:
        parts.extend(parent_widgets)
    elif parent_html:
        clipped, truncated = _clip(parent_html, MAX_CODE_HTML)
        parts.append(
            CaptchaCodePart(
                name="parent",
                ref="parent",
                kind="parent",
                url=parent_url,
                html=clipped,
                truncated=truncated,
            )
        )
    scripts.extend(_captcha_scripts(parent_html, "", parent_url))

    for frame in inspection.frames or []:
        clipped, truncated = _clip(frame.html, MAX_CODE_HTML)
        parts.append(
            CaptchaCodePart(
                name="frame",
                ref=frame.ref,
                kind=_frame_kind(frame.url, frame.html),
                url=frame.url,
                html=clipped,
                truncated=truncated,
            )
        )
        parts.extend(
            _widget_parts(
                frame.html,
                ref=frame.ref,
                url=frame.url,
                kind=_frame_kind(frame.url, frame.html),
            )
        )
        scripts.extend(_captcha_scripts(frame.html, frame.ref, frame.url))

    sprite_b64 = ""
    sprite = inspection.sprite
    if include_sprite_b64 and sprite and len(sprite) <= MAX_SPRITE_B64_BYTES:
        sprite_b64 = base64.b64encode(sprite).decode("ascii")

    note = (
        "CAPTCHA source dump: widget HTML, iframe documents, related scripts, "
        "and sprite bytes if fetched. Hidden recaptcha-token values are session "
        "blobs, not solutions. This API does not solve or bypass the challenge."
    )
    dump = CaptchaCodeDump(
        present=True,
        kind=report.kind,
        site_key=report.site_key,
        callback=report.callback,
        parts=parts,
        scripts=scripts,
        sprite_b64=sprite_b64,
        sprite_content_type=report.sprite_content_type if sprite_b64 else "",
        note=note,
    )
    dump.code = _format_captcha_code(dump)
    return dump


def _format_captcha_code(dump: CaptchaCodeDump) -> str:
    lines = [
        "======== CAPTCHA CODE DUMP ========",
        f"kind={dump.kind} site_key={dump.site_key or '-'}",
    ]
    if dump.callback:
        lines.append(f"callback={dump.callback}")
    for part in dump.parts:
        header = f"----- {part.name} [{part.ref or '-'}] {part.kind} {part.url} -----"
        lines.append(header)
        lines.append(part.html or "(empty)")
        if part.truncated:
            lines.append("<!-- truncated -->")
    for script in dump.scripts:
        src = script.src or "(inline)"
        lines.append(f"----- script frame={script.frame_ref or 'parent'} src={src} -----")
        if script.code:
            lines.append(script.code)
        elif script.src:
            lines.append(f"<!-- external {script.src} -->")
    if dump.sprite_b64:
        lines.append(
            f"----- sprite {dump.sprite_content_type} b64_chars={len(dump.sprite_b64)} -----"
        )
        lines.append(dump.sprite_b64)
    lines.append(f"NOTE: {dump.note}")
    lines.append("======== END CAPTCHA CODE ========")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vision-based CAPTCHA solving
# ---------------------------------------------------------------------------

_VISION_PROMPT_TEMPLATE = """You are solving a visual CAPTCHA. Look at the image below and identify which squares in the grid contain objects matching the prompt.

PROMPT: {prompt}

The image shows a grid of tiles (typically 3x3). Each tile is numbered 1-9 (left to right, top to bottom).

Respond with ONLY a valid JSON object in this format:
{{"selected": [list of tile numbers 1-9 that match the prompt]}}

Example: {{"selected": [1, 3, 5]}} means tiles at positions 1, 3, and 5 contain the objects described in the prompt.

Rules:
- Select ALL tiles that contain the described objects
- If uncertain between including or excluding a tile, include it
- Return ONLY the JSON object, no explanation"""


def _vision_tile_grid(prompt: str) -> str:
    """Build a vision model prompt for tile-based CAPTCHAs."""
    return _VISION_PROMPT_TEMPLATE.format(prompt=prompt)


def _parse_vision_response(content: str) -> list[int]:
    """Parse the vision model's response into a list of 1-based tile indices."""
    content = content.strip()

    # Strip markdown code blocks
    content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.MULTILINE)
    content = re.sub(r'\s*```$', '', content, flags=re.MULTILINE)
    content = content.strip()

    # Try to find JSON in the response
    start = content.find("{")
    end = content.rfind("}") + 1
    if start >= 0 and end > start:
        json_str = content[start:end]
    else:
        json_str = content

    try:
        parsed = json.loads(json_str)
        if isinstance(parsed, dict) and "selected" in parsed:
            selected = parsed["selected"]
            if isinstance(selected, list):
                return [int(x) for x in selected if isinstance(x, (int, float)) and 1 <= int(x) <= 9]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: try to extract numbers
    numbers = re.findall(r"\b([1-9])\b", content)
    return [int(n) for n in numbers if 1 <= int(n) <= 9]


async def _call_vision_model(
    prompt: str,
    sprite_b64: str,
    sprite_content_type: str,
    api_key: str,
    base_url: str,
    model: str,
    client: httpx.AsyncClient | None = None,
    *,
    retries: int = 2,
) -> list[int]:
    """Call an OpenAI-compatible vision model to identify tiles.

    Returns 1-based tile indices that match the CAPTCHA prompt.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=60.0)

    message_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _vision_tile_grid(prompt),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{sprite_content_type};base64,{sprite_b64}",
                "detail": "high",
            },
        },
    ]

    last_error: str = ""
    try:
        for attempt in range(max(1, retries + 1)):
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": message_content}],
                        "temperature": 0.0,
                        "max_tokens": 256,
                    },
                )
            except httpx.TimeoutException:
                last_error = f"vision model timed out (attempt {attempt + 1})"
                await asyncio.sleep(3 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                raise CaptchaSolveError(f"vision model connection failed: {exc}") from exc

            try:
                body = response.json()
            except ValueError:
                body = {"error": response.text[:500]}

            err = body.get("error") if isinstance(body, dict) else None
            if response.status_code == 429:
                last_error = f"rate limited: {err}"
                await asyncio.sleep(5 * (2 ** attempt))
                continue
            if response.status_code >= 400:
                raise CaptchaSolveError(
                    f"vision model HTTP {response.status_code}: {err or body}"
                )

            # Extract content
            try:
                choices = body.get("choices") or []
                if choices and isinstance(choices, list):
                    content = (choices[0].get("message") or {}).get("content") or ""
                    return _parse_vision_response(content)
            except (KeyError, IndexError, TypeError):
                pass

            last_error = f"unexpected response format from vision model"

        raise CaptchaSolveError(f"vision model failed after {retries + 1} attempts: {last_error}")
    finally:
        if own:
            await client.aclose()


@dataclass(slots=True)
class CaptchaSolveResult:
    """Result of an attempt to solve a visual CAPTCHA."""
    solution: CaptchaSolution
    error: str | None = None


class CaptchaSolveError(Exception):
    """Raised when CAPTCHA solving fails."""
    pass


async def solve_captcha(
    inspection: CaptchaInspection,
    *,
    api_key: str,
    base_url: str,
    model: str,
    client: httpx.AsyncClient | None = None,
    max_attempts: int = 1,
) -> CaptchaSolveResult:
    """Use a vision model to solve a tile-based visual CAPTCHA.

    1. Encodes the sprite image as base64
    2. Sends image + prompt to an OpenAI-compatible vision model
    3. Parses the model's response to get tile indices
    4. Returns structured tile selections

    The caller (Session) is responsible for clicking the tiles and hitting verify.
    """
    report = inspection.report
    sprite = inspection.sprite

    if not sprite:
        result = CaptchaSolution(
            solved=False,
            kind=report.kind,
            prompt=report.prompt,
            error="No sprite image available for solving. Call browser_captcha first.",
        )
        result.view = f"CAPTCHA SOLVE FAILED: {result.error}"
        return CaptchaSolveResult(solution=result, error=result.error)

    if not report.prompt:
        result = CaptchaSolution(
            solved=False,
            kind=report.kind,
            prompt=report.prompt,
            error="No CAPTCHA prompt found. Cannot determine what to select.",
        )
        result.view = f"CAPTCHA SOLVE FAILED: {result.error}"
        return CaptchaSolveResult(solution=result, error=result.error)

    if not report.tiles:
        result = CaptchaSolution(
            solved=False,
            kind=report.kind,
            prompt=report.prompt,
            error=f"No tiles found in CAPTCHA (kind={report.kind}). Only image-select captchas are supported.",
        )
        result.view = f"CAPTCHA SOLVE FAILED: {result.error}"
        return CaptchaSolveResult(solution=result, error=result.error)

    sprite_b64 = base64.b64encode(sprite).decode("ascii")
    sprite_ct = report.sprite_content_type or "image/jpeg"

    for attempt in range(1, max_attempts + 1):
        try:
            tile_indices = await _call_vision_model(
                prompt=report.prompt,
                sprite_b64=sprite_b64,
                sprite_content_type=sprite_ct,
                api_key=api_key,
                base_url=base_url,
                model=model,
                client=client,
                retries=2,
            )

            if not tile_indices:
                raise CaptchaSolveError("Vision model returned empty selection")

            selections = []
            for idx in tile_indices:
                tile_zero = idx - 1
                if 0 <= tile_zero < len(report.tiles):
                    tile = report.tiles[tile_zero]
                    selections.append(CaptchaTileSelection(
                        tile_id=tile.id,
                        row=tile.row,
                        column=tile.column,
                        confidence=0.8,
                        reason=f"Vision model selected tile {idx}",
                    ))

            solution = CaptchaSolution(
                solved=True,
                kind=report.kind,
                prompt=report.prompt,
                tiles_selected=selections,
                model_used=model,
                attempt=attempt,
            )
            solution.view = _format_solution_view(solution)
            return CaptchaSolveResult(solution=solution)

        except CaptchaSolveError as exc:
            if attempt == max_attempts:
                solution = CaptchaSolution(
                    solved=False,
                    kind=report.kind,
                    prompt=report.prompt,
                    model_used=model,
                    attempt=attempt,
                    error=str(exc),
                )
                solution.view = f"CAPTCHA SOLVE FAILED (attempt {attempt}): {solution.error}"
                return CaptchaSolveResult(solution=solution, error=str(exc))


def _format_solution_view(solution: CaptchaSolution) -> str:
    if not solution.solved:
        return f"CAPTCHA SOLVE FAILED: {solution.error}"

    lines = [
        f"CAPTCHA SOLVE: {solution.kind}",
        f"PROMPT: {solution.prompt}",
        f"MODEL: {solution.model_used}",
        f"ATTEMPT: {solution.attempt}",
        f"TILES_SELECTED: {len(solution.tiles_selected)}",
    ]
    for sel in solution.tiles_selected:
        lines.append(
            f"  [{sel.tile_id}] row={sel.row} col={sel.column} "
            f"(confidence={sel.confidence:.1f})"
        )
    return "\n".join(lines)

