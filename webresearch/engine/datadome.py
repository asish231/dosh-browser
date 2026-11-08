"""DataDome challenge detection, HTTP protocol extraction, and native solving.

DataDome guards a site with a JS fingerprint (`c.js` from
`ct.captcha-delivery.com`) that mints a `datadome` cookie, and when it judges
the client suspicious it serves an interstitial from `geo.captcha-delivery.com`.

Two interstitials exist in the wild:

* ``hard-block``  — a dead end (`t: 'bv'`). No widget, nothing to drag. Only a
  different egress IP helps, because DataDome already blacklisted the address.
* ``captcha``     — the ``ddv1`` widget (`t: 'fe'`). A "slide right" slider
  inside a cross-origin iframe (`geo.captcha-delivery.com/captcha/...`).

Per AGENTS.md (no Chromium/CDP of any kind), this module operates strictly
through our native HTTP transport, frame loader (`engine/frames.py`), and
in-house DOM (`minidom.js` + humanized kinematics), reporting outcomes honestly.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

CHALLENGE_HOST = "captcha-delivery.com"
CHALLENGE_IFRAME_MARKER = "geo.captcha-delivery.com"

# Markers that mean "this response is a DataDome interstitial, not content".
_BLOCK_MARKERS = (
    "ct.captcha-delivery.com/c.js",
    CHALLENGE_IFRAME_MARKER,
    "data-dd-captcha-container",
    "dd-response-page",
)

_SLIDER_HANDLE_SELECTOR = ".sliderContainer .slider"
_SLIDER_TARGET_SELECTOR = ".sliderTarget"
_SLIDER_CONTAINER_SELECTOR = ".sliderContainer"

# Evaluated inside the challenge frame; frames detach on submit so every field
# is defensive and returns null rather than throwing.
_GEOMETRY_JS = """
(() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  };
  const handle = document.querySelector('.sliderContainer .slider');
  const target = document.querySelector('.sliderTarget');
  const container = document.querySelector('.sliderContainer');
  const canvas = document.querySelector('#captcha__puzzle canvas');
  const audio = document.querySelector('.audio-captcha-track');
  const pass = document.getElementById('analyticsCaptchaPassed');
  const frame = document.querySelector('#captcha__frame');
  return JSON.stringify({
    handle: box(handle),
    target: box(target),
    container: box(container),
    canvasSize: canvas ? { w: canvas.width, h: canvas.height } : null,
    audioSrc: audio ? audio.src : '',
    passed: pass ? pass.getAttribute('data-analytics-captcha-passed') : null,
    widgetMode: frame ? frame.className : '',
    bodyClass: document.body ? document.body.className : '',
  });
})()
"""

_PASS_JS = """
(() => {
  const pass = document.getElementById('analyticsCaptchaPassed');
  return pass ? pass.getAttribute('data-analytics-captcha-passed') : null;
})()
"""


@dataclass
class DataDomeDetection:
    """What kind of DataDome response this is."""

    present: bool
    kind: str  # 'captcha' | 'hard-block' | ''
    variant: str = ""  # '' | 'fe' | 'bv'
    request_id: str = ""
    hash_key: str = ""
    challenge_url: str = ""
    network_flagged: bool = False
    flagged_ip: str = ""
    detail: str = ""
    frames: list[str] = field(default_factory=list)

    @property
    def solvable(self) -> bool:
        """Only the interactive widget can be solved client-side."""
        return self.kind == "captcha" and self.variant != "bv"


@dataclass
class SolveAttempt:
    """One drag attempt and how the widget responded."""

    dragged: bool
    distance: float = 0.0
    handle_box: dict[str, float] | None = None
    pass_signal: str | None = None
    passed: bool = False
    frame_detached: bool = False
    cookie_set: bool = False
    notes: str = ""


def extract_dd_params(html: str, referer: str = "") -> dict[str, str]:
    """Extract the `var dd = {...}` interstitial configuration object and
    build the canonical `geo.captcha-delivery.com/captcha/` protocol URL."""
    if not html:
        return {}
    params: dict[str, str] = {}
    for key in ("cid", "hsh", "t", "s", "e", "host", "cp"):
        m = re.search(rf"['\"]?{key}['\"]?\s*:\s*['\"]([^'\"]*)['\"]", html)
        if m:
            params[key] = m.group(1)
    if not params.get("s"):
        m_s = re.search(r"['\"]?s['\"]?\s*:\s*(\d+)", html)
        if m_s:
            params["s"] = m_s.group(1)
    if params.get("cid") and params.get("hsh"):
        host = params.get("host") or "geo.captcha-delivery.com"
        q = {
            "initialCid": params.get("cid", ""),
            "hash": params.get("hsh", ""),
            "cid": params.get("cid", ""),
            "t": params.get("t", "fe"),
            "referer": referer,
            "s": params.get("s", ""),
            "e": params.get("e", ""),
        }
        params["challenge_url"] = f"https://{host}/captcha/?{urlencode(q)}"
    return params


def detect(html: str, referer: str = "") -> DataDomeDetection:
    """Classify a page body as clean content, a solvable widget, or a hard block."""
    if not html:
        return DataDomeDetection(present=False, kind="")

    blob = html
    low = blob.lower()
    if not any(marker in low for marker in _BLOCK_MARKERS):
        return DataDomeDetection(present=False, kind="")

    dd_params = extract_dd_params(blob, referer=referer)
    variant = dd_params.get("t", "")
    request_id = dd_params.get("cid", "")
    hash_key = dd_params.get("hsh", "")
    challenge_url = dd_params.get("challenge_url", "")

    is_hard_block = "dd-response-page--hard-block" in low or variant == "bv"
    if "dd-response-page--captcha" in low and variant != "bv":
        is_hard_block = False
    kind = "hard-block" if is_hard_block else "captcha"

    ip_match = re.search(r"IP (\d{1,3}(?:\.\d{1,3}){3})", blob)
    network_flagged = "Automated (bot) activity on your network" in blob

    detail = "widget iframe present" if kind == "captcha" else "no interactive widget"
    if is_hard_block:
        detail = "hard block: no widget to solve"

    return DataDomeDetection(
        present=True,
        kind=kind,
        variant=variant,
        request_id=request_id,
        hash_key=hash_key,
        challenge_url=challenge_url,
        network_flagged=network_flagged,
        flagged_ip=ip_match.group(1) if ip_match else "",
        detail=detail,
        frames=[m for m in _BLOCK_MARKERS if m in low],
    )


def _challenge_frame(session: Any):
    """Return the live challenge frame from our native Session (or None)."""
    tab_fn = getattr(session, "_tab", None)
    if callable(tab_fn):
        tab = tab_fn()
        for fdoc in getattr(tab, "frames", []) or []:
            if CHALLENGE_HOST in (getattr(fdoc, "url", "") or ""):
                return fdoc
    for fdoc in getattr(session, "frames", []) or []:
        if CHALLENGE_HOST in (getattr(fdoc, "url", "") or ""):
            return fdoc
    return None


def _eval_in_frame(frame: Any, js_expr: str) -> Any:
    """Evaluate `js_expr` in a native FrameDocument's live context."""
    ctx = getattr(frame, "ctx", None)
    if ctx is not None and hasattr(ctx, "eval"):
        raw = ctx.eval(js_expr)
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return raw
        return raw
    return None


async def read_geometry(session: Any) -> dict[str, Any] | None:
    """Measure the slider inside the native challenge frame."""
    frame = _challenge_frame(session)
    if frame is None:
        return None
    data = _eval_in_frame(frame, _GEOMETRY_JS)
    if isinstance(data, dict) and data.get("handle"):
        return data
    return None


async def detect_frame(session: Any) -> dict[str, Any]:
    """Classify the challenge by what the framed widget actually rendered."""
    frame = _challenge_frame(session)
    if frame is None:
        return {"present": False, "kind": "", "reason": "no challenge frame"}
    html = getattr(frame, "html", "") or ""
    if not html:
        raw = _eval_in_frame(frame, "document.documentElement.outerHTML")
        html = str(raw or "")
    inner = detect(html)
    if not inner.present:
        return {"present": False, "kind": "", "reason": "frame is not a challenge"}
    return {
        "present": True,
        "kind": inner.kind,
        "solvable": inner.solvable,
        "network_flagged": inner.network_flagged,
        "flagged_ip": inner.flagged_ip,
    }


async def describe_widget(session: Any) -> dict[str, Any]:
    """Report what the live challenge actually rendered."""
    frame = _challenge_frame(session)
    if frame is None:
        return {"rendered": False, "reason": "no challenge frame"}
    data = _eval_in_frame(frame, _GEOMETRY_JS)
    if not isinstance(data, dict):
        return {"rendered": False, "reason": "no widget data"}

    body_class = (data.get("bodyClass") or "").lower()
    if not data.get("handle"):
        if "hard-block" in body_class:
            reason = "hard-block variant: DataDome is not offering a challenge"
        elif data.get("audioSrc"):
            reason = "audio-only variant"
        else:
            reason = "no slider rendered"
        return {
            "rendered": False,
            "reason": reason,
            "body_class": data.get("bodyClass") or "",
            "audio_available": bool(data.get("audioSrc")),
        }
    return {
        "rendered": True,
        "mode": data.get("widgetMode") or "",
        "audio_available": bool(data.get("audioSrc")),
        "passed_signal": data.get("passed"),
        "canvas": data.get("canvasSize"),
    }


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def solve_slider(session: Any, *, overshoot: float = 1.0) -> SolveAttempt:
    """Drag the DataDome slider inside the native frame realm on a humanized
    Bézier trajectory and verify through the widget's pass signal."""
    from webresearch.kinematics import generate_human_trajectory

    frame = _challenge_frame(session)
    if frame is None:
        return SolveAttempt(dragged=False, notes="no native challenge frame loaded")

    geometry = await read_geometry(session)
    if not geometry or not geometry.get("handle"):
        widget = await describe_widget(session)
        if not widget.get("rendered"):
            detail = widget.get("mode") or widget.get("reason") or "unknown"
            extra = " (audio route available)" if widget.get("audio_available") else ""
            return SolveAttempt(
                dragged=False,
                notes=f"no slider in this challenge variant: {detail}{extra}",
            )
        return SolveAttempt(dragged=False, notes="slider handle never rendered")

    handle = geometry["handle"]
    target = geometry.get("target") or geometry.get("container")
    hx = handle["x"] + handle["width"] / 2
    hy = handle["y"] + handle["height"] / 2
    if target:
        tx = target["x"] + target["width"] / 2
    else:
        tx = hx + 222.0
    ty = hy

    distance = tx - hx
    notes: list[str] = []
    if geometry.get("canvasSize") and geometry["canvasSize"].get("h", 0) == 0:
        notes.append("puzzle canvas empty: simple slide-right variant")
    if geometry.get("audioSrc"):
        notes.append("audio fallback available")

    trajectory = generate_human_trajectory((hx, hy), (tx + overshoot, ty), deviation=0.16)
    ctx = getattr(frame, "ctx", None)
    if ctx is not None and hasattr(ctx, "eval"):
        pts_json = json.dumps([[round(x, 2), round(y, 2)] for x, y in trajectory])
        drag_js = f"""
        (() => {{
          const handle = document.querySelector('.sliderContainer .slider');
          if (!handle) return false;
          const pts = {pts_json};
          handle.dispatchEvent(new Event('mousedown', {{ bubbles: true, clientX: pts[0][0], clientY: pts[0][1] }}));
          for (const [cx, cy] of pts) {{
            document.dispatchEvent(new Event('mousemove', {{ bubbles: true, clientX: cx, clientY: cy }}));
          }}
          const last = pts[pts.length - 1];
          handle.dispatchEvent(new Event('mouseup', {{ bubbles: true, clientX: last[0], clientY: last[1] }}));
          if (typeof __drainTimers === 'function') __drainTimers();
          return true;
        }})()
        """
        try:
            ctx.eval(drag_js)
        except Exception as exc:
            notes.append(f"frame drag error: {exc}")

    pass_signal = _eval_in_frame(frame, _PASS_JS)
    passed = str(pass_signal) == "true"

    return SolveAttempt(
        dragged=True,
        distance=distance,
        handle_box={"x": hx, "y": hy},
        pass_signal=str(pass_signal) if pass_signal is not None else None,
        passed=passed,
        frame_detached=False,
        notes="; ".join(notes),
    )


async def datadome_cookie(session: Any) -> str:
    """Current ``datadome`` cookie value from the native session client, if any."""
    client = getattr(session, "_client", None) or getattr(session, "client", None)
    if client is not None and hasattr(client, "cookies"):
        try:
            return str(client.cookies.get("datadome") or "")
        except Exception:
            return ""
    return ""


async def solve(session: Any, *, retries: int = 2) -> dict[str, Any]:
    """Native protocol + frame solver loop: inspect interstitial, classify
    hard-block vs widget, execute humanized slider drag in the native frame
    realm if present, and report honestly (never faking a token or pass)."""
    tab_fn = getattr(session, "_tab", None)
    tab = tab_fn() if callable(tab_fn) else None
    html = getattr(tab, "html", "") if tab is not None else getattr(session, "html", "")
    target_url = getattr(tab, "url", "") if tab is not None else getattr(session, "url", "")

    if not html:
        return {"ok": False, "reason": "no document loaded in session"}

    detection = detect(html, referer=target_url)
    report: dict[str, Any] = {
        "ok": False,
        "attempts": [],
        "target_url": target_url,
        "detection": {
            "present": detection.present,
            "kind": detection.kind,
            "variant": detection.variant,
            "request_id": detection.request_id,
            "challenge_url": detection.challenge_url,
            "solvable": detection.solvable,
            "network_flagged": detection.network_flagged,
            "flagged_ip": detection.flagged_ip,
            "detail": detection.detail,
        },
    }
    if not detection.present:
        report["ok"] = True
        report["reason"] = "no DataDome challenge present"
        return report

    if not detection.solvable:
        report["reason"] = (
            "hard block: DataDome blacklisted the egress IP (t='bv') and serves no widget; "
            "only a different IP or proxy can clear this"
        )
        report["needs_new_ip"] = True
        return report

    for attempt_index in range(1, retries + 1):
        inner = await detect_frame(session)
        report["frame"] = inner
        if inner.get("present") and not inner.get("solvable"):
            report["reason"] = (
                "widget is a hard block, not a challenge: DataDome is not offering "
                "anything to solve on this network"
            )
            report["needs_new_ip"] = True
            break

        result = await solve_slider(session, overshoot=random.uniform(0.0, 1.6))
        report["attempts"].append(
            {
                "attempt": attempt_index,
                "dragged": result.dragged,
                "distance": round(result.distance, 1),
                "passed": result.passed,
                "pass_signal": result.pass_signal,
                "notes": result.notes,
            }
        )
        if result.passed:
            report["ok"] = True
            report["reason"] = f"solved on attempt {attempt_index}"
            report["datadome_cookie"] = bool(await datadome_cookie(session))
            break
        if not result.dragged:
            report["reason"] = result.notes or "challenge frame slider not actionable in current realm"
            report["needs_human"] = True
            break

    return report