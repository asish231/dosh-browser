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


def captcha_view_with_token(report: CaptchaReport, token: str) -> str:
    """The report view, upgraded once a challenge token has been solved.

    The token itself is never echoed — only its presence and length. Whether
    the SITE accepts it is the site's server-side decision.
    """
    base = report.view or captcha_view(report)
    if not token:
        return base
    return base.replace(
        "CAPTCHA: recaptcha (inspection only)",
        f"CAPTCHA: recaptcha (SOLVED: g-recaptcha-response obtained, {len(token)} chars)",
        1,
    )


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


# ---------------------------------------------------------------------------
# reCAPTCHA v2 challenge protocol (anchor -> reload -> payload -> userverify)
#
# The widget's in-VM channel (botguard-keyed MessagePorts) cannot be driven
# from outside a real browser, but the challenge itself rides four documented
# HTTP endpoints (verified against a live widget build):
#   anchor      GET  first session token (recaptcha-token input)
#   reload      POST protobuf {1: version, 2: token, 6: reason, 14: sitekey}
#   payload     GET  the challenge image (p=..., or c=... for multicaptcha)
#   userverify  POST response=<urlsafe-b64 json> -> uvresp token / next rresp
# userverify returning NO embedded rresp means the answer was ACCEPTED and
# uv[1] is a verifiable g-recaptcha-response.
# ---------------------------------------------------------------------------

_RC_BASE = "https://www.google.com/recaptcha/api2"
_RC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Challenge metas sometimes carry only the class ID. The ID→goal table lives in
# the recaptcha JS bundle's string table (each ID sits near its "Select all
# squares with <strong>X</strong>" description). Verified by extraction from
# the live bundle (2026-11); runtime extraction refreshes it per build.
_RC_GOAL_IDS = {
    "/m/0k4j": "car",
    "/m/013xlm": "tractor",
    "/m/014xcs": "crosswalk",
    "/m/014xfs": "crosswalk",
    "/m/015kr": "bridge",
    "/m/015qbp": "parking meter",
    "/m/015qff": "traffic light",
    "/m/0199g": "bicycle",
    "/m/019jd": "boat",
    "/m/01jk_4": "chimney",
    "/m/01jw_1": "bus stop",
    "/m/01lynh": "stairs",
    "/m/01mqdt": "street sign",
    "/m/01pns0": "fire hydrant",
    "/m/01bjv": "bus",
    "/m/02pv19": "stop sign",
    "/m/02yvhj": "school bus",
    "/m/03jm5": "house",
    "/m/04_sv": "motorcycle",
    "/m/06gfj": "road",
    "/m/07j7r": "tree",
    "/m/07jdr": "street sign",
    "/m/07yv9": "vehicle",
    "/m/09d_r": "mountain or hill",
    "/m/0c9ph5": "flower",
    "/m/0cdl1": "palm tree",
    "/m/0cd5v": "palm tree",
    "/m/05b2w": "mountain or hill",
    "/m/0pg52": "taxi",
    "/m/013_1c": "statue",
    "/m/01_m7": "pillar or column",
    "/m/01nblt": "apartment building",
    "/m/03sy7v": "traffic cone",
    "/m/05s2s": "plant",
    "/m/06cnp": "river",
    "/m/0b3yr": "beach",
    "/m/0h8lhkg": "fountain",
}

_RC_GOAL_CACHE: dict[str, dict[str, str]] = {}

# Pacing scale for throttle-recovery waits; tests set it to 0 to run the
# recovery paths instantly instead of sleeping real seconds.
_RC_PACING_SCALE = 1.0


def _pacing(seconds: float) -> float:
    return seconds * _RC_PACING_SCALE


async def _extract_goal_map(client: httpx.AsyncClient, version: str) -> dict[str, str]:
    """Extract class-ID -> goal-name pairs from the current JS bundle.

    The bundle lays each ID near its 'Select all ... with <strong>X</strong>'
    description; the nearest description after an ID (within 3000 chars)
    names that ID's goal. Cached per version.
    """
    if version in _RC_GOAL_CACHE:
        return _RC_GOAL_CACHE[version]
    mapping: dict[str, str] = {}
    try:
        resp = await client.get(
            f"https://www.gstatic.com/recaptcha/releases/{version}/recaptcha__en.js",
            headers={"User-Agent": _RC_UA},
        )
        bundle = resp.text or ""
        ids = [(m.start(), m.group(0)) for m in re.finditer(r"/m/0[a-z0-9_]{3,10}", bundle)]
        descs = [(m.start(), m.group(0)) for m in re.finditer(r"Select all [^\"']{5,60}", bundle)]
        for pos, cid in ids:
            for dpos, d in descs:
                if 0 < dpos - pos < 3000:
                    goal = re.sub(r"<[^>]+>", "", d)
                    goal = re.sub(
                        r"[Ss]elect all (squares|images) of (the )?",
                        "",
                        goal,
                    )
                    goal = re.sub(r"[Ss]elect all (squares|images) with (a |an )?", "", goal)
                    goal = goal.strip().rstrip(".")
                    if cid not in mapping or len(goal) < len(mapping[cid]):
                        mapping[cid] = goal
                    break
    except Exception:
        pass
    _RC_GOAL_CACHE[version] = mapping
    return mapping


class RecaptchaV2Error(Exception):
    """The v2 challenge protocol failed (network, shape, or no-pass)."""


_VISION_PROMPT = (
    "Does this photograph contain a {goal}, or a clearly recognizable part of one "
    "(even small, distant, or partially visible)? "
    'Answer with ONLY a JSON object: {{"contains": true}} or {{"contains": false}}.'
)


async def vision_tile_contains(
    tile: bytes,
    goal: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    models: list[str] | None = None,
    retries: int = 3,
    client: httpx.AsyncClient | None = None,
    supermajority: bool = True,
) -> bool:
    """Ensemble vote: does one tile contain the goal?

    A wrong tile answer fails the whole challenge round, so accuracy beats
    latency: N vision models vote and a SUPERMAJORITY (strictly more than
    half) must say yes — borderline tiles (a gray utility box read as a
    "hydrant" by one model) stay unselected, which is what live dynamic
    challenges demand. Any model the pool lists can serve; ``models``
    overrides the single ``model`` choice.
    """
    tile_b64 = base64.b64encode(tile).decode("ascii")
    prompt = _VISION_PROMPT.format(goal=goal.upper())
    candidates = models or [model]
    own = client is None
    client = client or httpx.AsyncClient(timeout=120.0)

    async def ask(m: str) -> str:
        last_error = ""
        for attempt in range(max(1, retries)):
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": m,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{tile_b64}", "detail": "high"}},
                            ],
                        }
                    ],
                    "temperature": 0.0,
                    "max_tokens": 100,
                },
            )
            if resp.status_code == 200:
                body = resp.json()
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content
                last_error = f"empty content: {str(body)[:150]}"
            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:150]}"
            await asyncio.sleep(3 * (attempt + 1))
        raise RecaptchaV2Error(f"vision model {m} failed: {last_error}")

    try:
        answers = await asyncio.gather(*[ask(m) for m in candidates], return_exceptions=True)
    finally:
        if own:
            await client.aclose()
    yes = no = 0
    for ans in answers:
        if isinstance(ans, Exception):
            continue
        match = re.search(r"\{[^}]*\}", ans, re.S)
        if not match:
            continue
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if parsed.get("contains") is True:
            yes += 1
        elif parsed.get("contains") is False:
            no += 1
    total = yes + no
    if total == 0:
        return False
    if supermajority:
        # A tile matches when at least 2 models say yes AND the yes votes
        # strictly outnumber the no votes (simple majority with a floor
        # against single-model hallucinations). Dynamic challenges require
        # selecting EVERY matching tile: a false negative guarantees the
        # round fails, while a false positive only costs one replacement
        # click — so recall leads, with the floor screening 1/5 flukes.
        return yes >= 2 and yes > no
    return yes > no


_GRID_PROMPT = (
    "This is a reCAPTCHA image challenge. The photo grid is divided into {count} numbered "
    "cells by yellow lines; each cell has its number (1-{count}) printed in its top-left corner.\n"
    "TASK: Which numbered cells contain a {goal} — including cells where it is small, distant, "
    "partial, or cut off at a cell edge? Look at every cell one by one before deciding.\n"
    "Respond with ONLY a JSON object: {{\"selected\": [cell numbers]}} — every matching cell "
    'number. If none, respond {{"selected": []}}.'
)


async def vision_grid_select(
    grid_bytes: bytes,
    goal: str,
    rows: int,
    cols: int,
    *,
    api_key: str,
    base_url: str,
    model: str,
    models: list[str] | None = None,
    retries: int = 4,
    client: httpx.AsyncClient | None = None,
) -> list[int]:
    """0-based cell indices containing the goal, by ensemble majority vote.

    The labeled-grid strategy (see ``_label_grid``): one ensemble call on
    the numbered whole grid — unanimous on true positives, halting
    hallucinations — instead of per-tile crops that mis-count cells.
    Returns 0-based indices, row-major.
    """
    labeled = _label_grid(grid_bytes, rows, cols)
    labeled_b64 = base64.b64encode(labeled).decode("ascii")
    prompt = _GRID_PROMPT.format(count=rows * cols, goal=goal.upper())
    candidates = models or [model]
    own = client is None
    client = client or httpx.AsyncClient(timeout=180.0)

    async def ask(m: str) -> list[int] | None:
        last_error = ""
        for attempt in range(max(1, retries)):
            try:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": m,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{labeled_b64}", "detail": "high"}},
                                ],
                            }
                        ],
                        "temperature": 0.0,
                        "max_tokens": 150,
                    },
                )
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:150]}"
                    await asyncio.sleep(4 * (attempt + 1))
                    continue
                body = resp.json()
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
                match = re.search(r"\{[^}]*\}", content, re.S)
                if match:
                    sel = json.loads(match.group(0)).get("selected") or []
                    return [
                        int(x) - 1
                        for x in sel
                        if isinstance(x, (int, float)) and 1 <= int(x) <= rows * cols
                    ]
                last_error = f"no JSON in reply: {content[:120]}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(4 * (attempt + 1))
        return None

    try:
        answers = await asyncio.gather(*[ask(m) for m in candidates])
    finally:
        if own:
            await client.aclose()
    votes: dict[int, int] = {}
    answered = 0
    for sel in answers:
        if sel is None:
            continue
        answered += 1
        for t in sel:
            votes[t] = votes.get(t, 0) + 1
    need = max(2, (answered + 1) // 2)  # majority of answering models, floor 2
    return sorted(t for t, v in votes.items() if v >= need)


def _varint(n: int, out: bytearray) -> None:
    while True:
        b = n & 127
        n >>= 7
        if n > 0:
            out.append(b | 128)
        else:
            out.append(b)
            break


def _protobuf(fields) -> bytes:
    out = bytearray()
    for num, value in fields:
        _varint((num << 3) | 2, out)
        _varint(len(value), out)
        out += value
    return bytes(out)


def _rc_base64(data: str) -> str:
    return base64.b64encode(data.encode("utf-8"), b"-_").decode().replace("=", ".")


def _rc_json(text: str):
    return json.loads(text.split("\n", 1)[1])


def _is_image(raw: bytes | None) -> bool:
    return bool(raw) and (raw.startswith(b"\xff\xd8") or raw.startswith(b"\x89PNG"))


def _goal_from_meta(meta: list, goal_map: dict[str, str] | None = None) -> tuple[str, int, int]:
    rows = int(meta[3] or 3)
    cols = int(meta[4] or 3)
    goal = meta[6] if len(meta) > 6 and meta[6] else ""
    if not goal and meta and isinstance(meta[0], str):
        goal = (goal_map or {}).get(meta[0]) or _RC_GOAL_IDS.get(meta[0], meta[0])
    return goal, rows, cols


def _crop_tiles(img_bytes: bytes, rows: int, cols: int) -> list[bytes]:
    """Split the grid image into per-tile JPEGs (row-major), upscaled 2x.

    Kept for replacement tiles, which arrive as single-tile images.
    """
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(img_bytes))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    W, H = im.size
    tw, th = W // cols, H // rows
    out: list[bytes] = []
    for r in range(rows):
        for c in range(cols):
            tile = im.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
            tile = tile.resize((tile.width * 2, tile.height * 2), Image.LANCZOS)
            buf = io.BytesIO()
            tile.save(buf, format="JPEG", quality=92)
            out.append(buf.getvalue())
    return out


def _label_grid(img_bytes: bytes, rows: int, cols: int) -> bytes:
    """The grid image upscaled 3x with drawn cell lines and numbers.

    Vision models MIS-COUNT cells on a plain photo grid (boundary objects
    get attributed to the wrong index). Drawing the cell lines and
    printing each cell's number (row-major, 1-based) pins the geometry:
    verified on a live dynamic challenge — plain per-tile crops gave 4/5
    and 4/5 on the two true positives with a 3/5 hallucination, while the
    labeled grid gave 5/5 and 5/5 unanimity with the hallucination
    collapsing to 2/5. One image, one ensemble call per grid — also ~9x
    fewer requests than per-tile classification, which keeps the
    challenge server's throttle at bay.
    """
    import io

    from PIL import Image, ImageDraw, ImageFont

    im = Image.open(io.BytesIO(img_bytes))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    W, H = im.size
    scale = 3
    big = im.resize((W * scale, H * scale), Image.LANCZOS)
    draw = ImageDraw.Draw(big)
    tw, th = (W * scale) // cols, (H * scale) // rows
    for i in range(1, cols):
        draw.line([(i * tw, 0), (i * tw, H * scale)], fill=(255, 255, 0), width=3)
    for i in range(1, rows):
        draw.line([(0, i * th), (W * scale, i * th)], fill=(255, 255, 0), width=3)
    font = None
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 28)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    for r in range(rows):
        for c in range(cols):
            n = r * cols + c + 1
            x, y = c * tw, r * th
            draw.rectangle([(x + 4, y + 4), (x + 40, y + 38)], fill=(0, 0, 0))
            draw.text((x + 10, y + 8), str(n), fill=(255, 255, 0), font=font)
    buf = io.BytesIO()
    big.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


async def solve_recaptcha_v2(
    client: httpx.AsyncClient,
    *,
    site_key: str,
    site_url: str,
    grid_select,
    tile_contains,
    max_rounds: int = 8,
    progress=None,
    debug_dir: str | None = None,
) -> str:
    """Solve a reCAPTCHA v2 challenge and return a g-recaptcha-response token.

    ``grid_select(grid_jpeg, goal, rows, cols) -> list[int]`` returns the
    0-based cell indices containing the goal (driver-supplied vision; the
    engine ships an ensemble labeled-grid implementation — see
    ``vision_grid_select``). ``tile_contains(tile_jpeg, goal) -> bool``
    classifies one replacement tile. Raises RecaptchaV2Error when the
    challenge is not passed within ``max_rounds``. ``progress(event)``
    observes each round. ``debug_dir`` saves each round's images.
    """

    def _emit(event: dict) -> None:
        if progress is not None:
            try:
                progress(event)
            except Exception:
                pass

    def _save(tag: str, data: bytes) -> None:
        if not debug_dir:
            return
        try:
            from pathlib import Path as _P

            _P(debug_dir).mkdir(parents=True, exist_ok=True)
            (_P(debug_dir) / f"{tag}.jpg").write_bytes(data)
        except Exception:
            pass

    async def hget(url: str, referer: str):
        return await client.get(url, headers={"User-Agent": _RC_UA, "Referer": referer})

    async def hpost(url: str, content, referer: str, content_type: str):
        return await client.post(
            url,
            content=content if isinstance(content, bytes) else None,
            data=None if isinstance(content, bytes) else content,
            headers={
                "User-Agent": _RC_UA,
                "Content-Type": content_type,
                "Origin": "https://www.google.com",
                "Referer": referer,
            },
        )

    parsed = urlparse(site_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    origin = f"{parsed.scheme}://{parsed.hostname}:{port}"
    co = _rc_base64(origin)
    bframe_ref = f"{_RC_BASE}/bframe?hl=en&k={site_key}"

    # Version from the loader.
    resp = await hget("https://www.google.com/recaptcha/api.js", site_url + "/")
    match = re.search(r"/recaptcha/releases/([^/]+)/", resp.text or "")
    if not match:
        raise RecaptchaV2Error("could not extract recaptcha version from api.js")
    version = match.group(1)

    # anchor -> first token
    anchor_url = (
        f"{_RC_BASE}/anchor?ar=1&k={site_key}&co={co}&hl=en&v={version}"
        "&size=normal&sa=action"
    )
    resp = await hget(anchor_url, site_url + "/")
    match = re.search(r'id="recaptcha-token"[^>]*value="([^"]+)"', resp.text or "")
    if not match:
        raise RecaptchaV2Error("anchor did not return a recaptcha-token")
    token = match.group(1)

    # reload (protobuf, reason=fi) -> first challenge
    body = _protobuf(
        [
            (1, version.encode()),
            (2, token.encode()),
            (6, b"fi"),
            (14, site_key.encode()),
        ]
    )
    resp = await hpost(f"{_RC_BASE}/reload?k={site_key}", body, anchor_url, "application/x-protobuffer")
    if resp.status_code != 200:
        raise RecaptchaV2Error(f"reload HTTP {resp.status_code}")
    rresp = _rc_json(resp.text)

    for round_no in range(1, max_rounds + 1):
        if not (isinstance(rresp, list) and len(rresp) > 5):
            raise RecaptchaV2Error(f"degenerate rresp at round {round_no} (len={len(rresp) if isinstance(rresp, list) else 'non-list'})")
        current_token = rresp[1]
        ctype = rresp[5]
        pmeta = rresp[4]
        current_p = rresp[9] if len(rresp) > 9 else None

        if ctype not in {"dynamic", "multicaptcha", "imageselect"}:
            raise RecaptchaV2Error(f"unsupported challenge type {ctype!r}")

        if ctype == "multicaptcha":
            payload_url = f"{_RC_BASE}/payload?c={current_token}&k={site_key}&v={version}&p={current_p}"
        else:
            payload_url = f"{_RC_BASE}/payload?p={current_p}&k={site_key}&v={version}"
        img = b""
        # Payloads occasionally come back empty right after a long solve
        # (server-side throttle): patient backoff before failing.
        for attempt in range(5):
            resp = await hget(payload_url, bframe_ref)
            img = resp.content
            if _is_image(img):
                break
            await asyncio.sleep(_pacing(5 * (attempt + 1)))
        if not _is_image(img):
            # Throttled at the grid fetch: request a FRESH challenge via
            # reload (the bframe's reload button, reason=r) instead of
            # aborting the solve. The wait also lets the throttle clear.
            await asyncio.sleep(_pacing(30))
            reload_body = _protobuf(
                [
                    (1, version.encode()),
                    (2, current_token.encode()),
                    (6, b"r"),
                    (14, site_key.encode()),
                ]
            )
            resp = await hpost(
                f"{_RC_BASE}/reload?k={site_key}",
                reload_body,
                bframe_ref,
                "application/x-protobuffer",
            )
            if resp.status_code != 200:
                raise RecaptchaV2Error(f"payload empty and fresh-challenge reload HTTP {resp.status_code}")
            fresh = _rc_json(resp.text)
            if not (isinstance(fresh, list) and fresh and fresh[0] == "rresp"):
                raise RecaptchaV2Error("payload empty and fresh-challenge reload returned no rresp")
            rresp = fresh
            _emit({"round": round_no, "type": ctype, "note": "grid throttled; fresh challenge requested"})
            continue
        _save(f"round{round_no}_grid", img)

        if ctype == "dynamic":
            meta = pmeta[1]
            goal, rows, cols = _goal_from_meta(meta)
            n_tiles = rows * cols
            # One ensemble call on the labeled whole grid.
            selections = await grid_select(img, goal, rows, cols)
            _emit(
                {
                    "round": round_no,
                    "type": ctype,
                    "goal": goal,
                    "grid": f"{rows}x{cols}",
                    "selected": [i + 1 for i in selections],
                }
            )
            # Dynamic replacements: each clicked slot's tile is replaced; the
            # slot's real id grows by one per replacement.
            tile_map = {i: i for i in range(n_tiles)}
            latest = n_tiles - 1
            queue = list(selections)
            response: list[int] = []
            steps = 0
            while queue and steps < 30:
                steps += 1
                slot = queue.pop(0)
                real = tile_map[slot]
                response.append(real)
                resp = await hpost(
                    f"{_RC_BASE}/replaceimage?k={site_key}",
                    {"v": version, "c": current_token, "ds": f"[{real}]"},
                    bframe_ref,
                    "application/x-www-form-urlencoded",
                )
                data = _rc_json(resp.text)
                if not isinstance(data, list) or len(data) < 2 or not data[1]:
                    # Degenerate dresp (server hiccup / throttle): salvage the
                    # round with selections so far instead of crashing.
                    _emit(
                        {
                            "round": round_no,
                            "type": "dynamic",
                            "note": f"degenerate replaceimage response (len={len(data) if isinstance(data, list) else 'non-list'}); verifying selections so far",
                            "selected": [i + 1 for i in response],
                        }
                    )
                    break
                current_token = data[1]
                current_p = data[5] if len(data) > 5 else current_p
                replacement_id = (data[2][0] if len(data) > 2 and isinstance(data[2], list) and data[2] else None)
                if replacement_id is None:
                    break
                latest += 1
                tile_map[slot] = latest
                rep = b""
                for attempt in range(3):
                    resp = await hget(
                        f"{_RC_BASE}/payload?p={current_p}&k={site_key}&v={version}&id={replacement_id}",
                        bframe_ref,
                    )
                    rep = resp.content
                    if _is_image(rep):
                        break
                    await asyncio.sleep(_pacing(5 * (attempt + 1)))
                _save(f"round{round_no}_rep{latest}", rep)
                if not _is_image(rep):
                    # Throttled mid-round: salvage what is selected so far
                    # and let userverify judge it, instead of dying. The
                    # answer may be incomplete (round fails, next round
                    # comes) but the solve survives to try again.
                    _emit(
                        {
                            "round": round_no,
                            "type": "dynamic",
                            "note": "replacement payload throttled; verifying with selections so far",
                            "selected": [i + 1 for i in response],
                        }
                    )
                    break
                if await tile_contains(rep, goal):
                    queue.append(slot)
        elif ctype == "imageselect":
            # The classic STATIC grid: select matching cells, verify. No
            # tile replacements — the simplest challenge type.
            meta = pmeta[1] if isinstance(pmeta, list) and len(pmeta) > 1 and isinstance(pmeta[1], list) else pmeta
            goal, rows, cols = _goal_from_meta(meta if isinstance(meta, list) else [None, None, None, 3, 3])
            selections = await grid_select(img, goal, rows, cols)
            _emit(
                {
                    "round": round_no,
                    "type": ctype,
                    "goal": goal,
                    "grid": f"{rows}x{cols}",
                    "selected": [i + 1 for i in selections],
                }
            )
            response = sorted(selections)
        else:
            metas = list(pmeta[5][0])
            # librecaptcha semantics: the payload after each replaceimage is
            # addressed by the PREVIOUS grid's id (starting at "2"), and the
            # response's data[2] supplies the id for the NEXT round.
            goal_map = await _extract_goal_map(client, version)
            cur_img = img
            grid_id = "2"
            groups: list[list[int]] = []
            for gi, g in enumerate(metas):
                g_goal, g_rows, g_cols = _goal_from_meta(g, goal_map)
                sel = await grid_select(cur_img, g_goal, g_rows, g_cols)
                groups.append(sorted(sel))
                _emit(
                    {
                        "round": round_no,
                        "type": ctype,
                        "sub_grid": gi + 1,
                        "goal": g_goal,
                        "grid": f"{g_rows}x{g_cols}",
                        "selected": [i + 1 for i in sel],
                    }
                )
                _save(f"round{round_no}_grid{gi+1}", cur_img)
                if gi < len(metas) - 1:
                    resp = await hpost(
                        f"{_RC_BASE}/replaceimage?k={site_key}",
                        {"v": version, "c": current_token, "ds": json.dumps([groups[-1]], separators=",:")},
                        bframe_ref,
                        "application/x-www-form-urlencoded",
                    )
                    data = _rc_json(resp.text)
                    if not isinstance(data, list) or len(data) < 2 or not data[1]:
                        # Degenerate dresp: keep the current image and continue
                        # with what the next meta describes, instead of dying.
                        _emit(
                            {
                                "round": round_no,
                                "type": ctype,
                                "sub_grid": gi + 1,
                                "note": "degenerate replaceimage response; continuing with current grid image",
                            }
                        )
                        continue
                    current_token = data[1]
                    prev_p = data[5] if len(data) > 5 else current_p
                    prev_id = grid_id
                    grid_id = (
                        (data[2][0] if len(data) > 2 and isinstance(data[2], list) and data[2] else None)
                        or grid_id
                    )
                    if prev_id:
                        resp = await hget(
                            f"{_RC_BASE}/payload?p={prev_p}&k={site_key}&v={version}&id={prev_id}",
                            bframe_ref,
                        )
                        if _is_image(resp.content):
                            cur_img = resp.content
            response = groups

        # userverify: an embedded rresp means the answer was REJECTED.
        response_json = json.dumps({"response": response}, separators=",:")
        resp = await hpost(
            f"{_RC_BASE}/userverify?k={site_key}",
            {"v": version, "c": current_token, "response": _rc_base64(response_json)},
            bframe_ref,
            "application/x-www-form-urlencoded",
        )
        uv = _rc_json(resp.text)
        new_rresp = next(
            (c for c in uv if isinstance(c, list) and c and c[0] == "rresp"),
            None,
        )
        if new_rresp is None:
            token = uv[1]
            if not token:
                raise RecaptchaV2Error("userverify passed but returned no token")
            return token
        rresp = new_rresp
        # Patient pacing between rejected rounds: rapid-fire challenges trip
        # the server's throttle (payloads start returning empty).
        await asyncio.sleep(_pacing(3))

    raise RecaptchaV2Error(f"challenge not passed within {max_rounds} rounds")


