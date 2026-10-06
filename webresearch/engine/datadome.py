"""DataDome challenge detection and solving.

DataDome guards a site with a JS fingerprint (`c.js` from
`ct.captcha-delivery.com`) that mints a `datadome` cookie, and when it judges
the client suspicious it serves an interstitial from `geo.captcha-delivery.com`.

Two interstitials exist in the wild:

* ``hard-block``  — a dead end. No widget, nothing to drag. Only a different
  egress IP helps, because DataDome already blacklisted the address.
* ``captcha``     — the ``ddv1`` widget. A "slide right" slider inside a
  cross-origin iframe that only accepts trusted input events.

This module drives the real Chromium lane to drag that slider with humanized
kinematics, then verifies the result through the widget's own pass signal
rather than by matching strings in the page body.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

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
() => {
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
  return {
    handle: box(handle),
    target: box(target),
    container: box(container),
    canvasSize: canvas ? { w: canvas.width, h: canvas.height } : null,
    audioSrc: audio ? audio.src : '',
    passed: pass ? pass.getAttribute('data-analytics-captcha-passed') : null,
    widgetMode: frame ? frame.className : '',
    bodyClass: document.body ? document.body.className : '',
  };
}
"""

_PASS_JS = """
() => {
  const pass = document.getElementById('analyticsCaptchaPassed');
  return pass ? pass.getAttribute('data-analytics-captcha-passed') : null;
}
"""


@dataclass
class DataDomeDetection:
    """What kind of DataDome response this is."""

    present: bool
    kind: str  # 'captcha' | 'hard-block' | ''
    variant: str = ""  # '' | 'fe' | 'bv'
    request_id: str = ""
    network_flagged: bool = False
    flagged_ip: str = ""
    detail: str = ""
    frames: list[str] = field(default_factory=list)

    @property
    def solvable(self) -> bool:
        """Only the interactive widget can be solved client-side."""
        return self.kind == "captcha"


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


def detect(html: str) -> DataDomeDetection:
    """Classify a page body as clean content, a solvable widget, or a hard block."""
    if not html:
        return DataDomeDetection(present=False, kind="")

    blob = html
    low = blob.lower()
    if not any(marker in low for marker in _BLOCK_MARKERS):
        return DataDomeDetection(present=False, kind="")

    variant_match = re.search(r"'t'\s*:\s*'([a-z]{2})'", blob)
    variant = variant_match.group(1) if variant_match else ""

    cid_match = re.search(r"'cid'\s*:\s*'([^']+)'", blob)
    request_id = cid_match.group(1) if cid_match else ""

    # The interstitial wrapper and the widget it frames disagree about the
    # variant: the wrapper says 'fe' (first encounter) while the frame body
    # renders a hard block with nothing to solve. The frame is authoritative,
    # because it is what the browser is actually showing, so prefer its class
    # when one is available.
    is_hard_block = "dd-response-page--hard-block" in low
    if "dd-response-page--captcha" in low:
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
        network_flagged=network_flagged,
        flagged_ip=ip_match.group(1) if ip_match else "",
        detail=detail,
        frames=[m for m in _BLOCK_MARKERS if m in low],
    )


def _challenge_frame(chromium_session: Any):
    """Return the live challenge frame, or None."""
    page = getattr(chromium_session, "page", None)
    if page is None:
        return None
    for frame in page.frames:
        if CHALLENGE_HOST in (frame.url or ""):
            return frame
    return None


async def read_geometry(chromium_session: Any) -> dict[str, Any] | None:
    """Measure the slider inside the challenge frame, waiting for it to render.

    DataDome serves more than one challenge shape. Some interstitials render the
    ``ddv1`` slider, others render only the audio or the feedback form, so the
    widget may legitimately never appear. The wait is therefore bounded and the
    caller gets ``None`` rather than a hang.
    """
    for _ in range(25):
        frame = _challenge_frame(chromium_session)
        if frame is not None:
            try:
                data = await frame.evaluate(_GEOMETRY_JS)
            except Exception:
                data = None
            if data and data.get("handle"):
                return data
        await asyncio_sleep(0.4)
    return None


async def detect_frame(chromium_session: Any) -> dict[str, Any]:
    """Classify the challenge by what the framed widget actually rendered.

    The interstitial wrapper always claims a solvable challenge, so the wrapper
    alone is not trustworthy. The frame body is authoritative, because it is what
    the browser is actually showing.
    """
    frame = _challenge_frame(chromium_session)
    if frame is None:
        return {"present": False, "kind": "", "reason": "no challenge frame"}
    try:
        html = await frame.evaluate("() => document.documentElement.outerHTML")
    except Exception:
        return {"present": False, "kind": "", "reason": "frame detached"}
    inner = detect(html or "")
    if not inner.present:
        return {"present": False, "kind": "", "reason": "frame is not a challenge"}
    return {
        "present": True,
        "kind": inner.kind,
        "solvable": inner.solvable,
        "network_flagged": inner.network_flagged,
        "flagged_ip": inner.flagged_ip,
    }


async def describe_widget(chromium_session: Any) -> dict[str, Any]:
    """Report what the live challenge actually rendered, for the run log.

    Distinguishes "this variant has no slider" from "the slider was late",
    because they need different responses: one means take the audio route, the
    other means wait and retry.
    """
    frame = _challenge_frame(chromium_session)
    if frame is None:
        return {"rendered": False, "reason": "no challenge frame"}
    try:
        data = await frame.evaluate(_GEOMETRY_JS)
    except Exception:
        return {"rendered": False, "reason": "frame detached"}
    if not data:
        return {"rendered": False, "reason": "no widget data"}

    body_class = (data.get("bodyClass") or "").lower()
    if not data.get("handle"):
        # Naming the variant makes the log readable: a hard block and an
        # audio-only widget both lack a slider, and they need opposite
        # responses.
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


async def solve_slider(chromium_session: Any, *, overshoot: float = 1.0) -> SolveAttempt:
    """Drag the DataDome slider to its target on a humanized trajectory.

    Uses the same Bézier + tremor generator as ``click_path`` so the pointer
    path carries plausible acceleration and hand shake. Verifies the outcome
    through the widget's ``analyticsCaptchaPassed`` flag.
    """
    from webresearch.kinematics import generate_human_trajectory

    page = getattr(chromium_session, "page", None)
    if page is None:
        return SolveAttempt(dragged=False, notes="no chromium page")

    geometry = await read_geometry(chromium_session)
    if not geometry or not geometry.get("handle"):
        widget = await describe_widget(chromium_session)
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

    # Approach from off-target like a real pointer, then press.
    start_x = max(hx - random.uniform(45.0, 95.0), 4.0)
    start_y = hy + random.uniform(-28.0, 28.0)
    for pt in generate_human_trajectory((start_x, start_y), (hx, hy)):
        await page.mouse.move(pt[0], pt[1])
    await page.wait_for_timeout(random.randint(120, 260))
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(90, 190))

    # Drag with a lower arc than a click so the motion reads as a sustained slide.
    for pt in generate_human_trajectory((hx, hy), (tx + overshoot, ty), deviation=0.16):
        await page.mouse.move(pt[0], pt[1])
        await page.wait_for_timeout(random.randint(7, 20))

    await page.mouse.move(tx + overshoot, ty)
    await page.wait_for_timeout(random.randint(280, 520))
    await page.mouse.up()

    chromium_session._last_mouse_x = tx + overshoot
    chromium_session._last_mouse_y = ty

    pass_signal, detached = await _await_pass_signal(chromium_session, timeout=12.0)
    passed = pass_signal == "true"

    return SolveAttempt(
        dragged=True,
        distance=distance,
        handle_box={"x": hx, "y": hy},
        pass_signal=pass_signal,
        passed=passed,
        frame_detached=detached,
        notes="; ".join(notes),
    )


async def _await_pass_signal(
    chromium_session: Any, *, timeout: float = 12.0
) -> tuple[str | None, bool]:
    """Poll the widget's pass flag until it flips, detaches, or we time out."""
    import asyncio

    page = getattr(chromium_session, "page", None)
    if page is None:
        return None, False
    deadline = asyncio.get_event_loop().time() + timeout
    detached = False
    while asyncio.get_event_loop().time() < deadline:
        frame = _challenge_frame(chromium_session)
        if frame is None:
            # A successful solve tears the widget down; a rejection re-renders it.
            detached = True
            return None, detached
        try:
            signal = await frame.evaluate(_PASS_JS)
        except Exception:
            detached = True
            return None, detached
        if signal == "true":
            return signal, detached
        await asyncio.sleep(0.8)
    frame = _challenge_frame(chromium_session)
    if frame is None:
        return None, True
    try:
        return await frame.evaluate(_PASS_JS), detached
    except Exception:
        return None, True


async def datadome_cookie(chromium_session: Any) -> str:
    """Current ``datadome`` cookie value from the browser context, if any."""
    context = getattr(chromium_session, "_context", None)
    if context is None:
        return ""
    try:
        for cookie in await context.cookies():
            if cookie.get("name") == "datadome":
                return str(cookie.get("value") or "")
    except Exception:
        return ""
    return ""


async def solve(chromium_session: Any, *, retries: int = 3) -> dict[str, Any]:
    """Full loop: load, classify, drag, confirm, and re-fetch the real page.

    Returns a report describing what was detected and whether the content
    ultimately became reachable. Three outcomes are possible and the report
    distinguishes them, because they need different responses from the caller:

    * ``ok``                - content reached
    * ``needs_human``       - a widget exists but has no client-side route
    * ``needs_new_ip``      - DataDome hard-blocked the egress address
    """
    import asyncio

    page = getattr(chromium_session, "page", None)
    if page is None:
        return {"ok": False, "reason": "no chromium page"}

    report: dict[str, Any] = {
        "ok": False,
        "attempts": [],
        "target_url": getattr(chromium_session, "url", ""),
    }
    saw_variant_without_slider = False

    for attempt_index in range(1, retries + 1):
        detection = detect(await page.content())
        report["detection"] = {
            "kind": detection.kind,
            "variant": detection.variant,
            "solvable": detection.solvable,
            "network_flagged": detection.network_flagged,
            "flagged_ip": detection.flagged_ip,
            "detail": detection.detail,
        }
        if not detection.present:
            report["ok"] = True
            report["reason"] = "no DataDome challenge present"
            break
        if not detection.solvable:
            report["reason"] = (
                "hard block: DataDome blacklisted the egress IP and serves no widget; "
                "only a different IP or proxy can clear this"
            )
            report["needs_new_ip"] = True
            break

        await page.wait_for_timeout(1200)
        # The wrapper is not authoritative; ask the framed widget what it is.
        inner = await detect_frame(chromium_session)
        report["frame"] = inner
        if inner.get("present") and not inner.get("solvable"):
            report["reason"] = (
                "widget is a hard block, not a challenge: DataDome is not offering "
                "anything to solve on this network"
            )
            report["needs_new_ip"] = True
            break

        result = await solve_slider(chromium_session, overshoot=random.uniform(0.0, 1.6))
        report["attempts"].append(
            {
                "attempt": attempt_index,
                "dragged": result.dragged,
                "distance": round(result.distance, 1),
                "passed": result.passed,
                "pass_signal": result.pass_signal,
                "frame_detached": result.frame_detached,
                "notes": result.notes,
            }
        )
        if not result.dragged and "no slider in this challenge variant" in result.notes:
            saw_variant_without_slider = True

        # Re-fetch the protected URL and let the browser replay its cookies.
        try:
            await chromium_session.goto(chromium_session.url, timeout_ms=45000)
            await page.wait_for_timeout(2500)
        except Exception:
            pass

        after = detect(await page.content())
        if not after.present:
            report["ok"] = True
            report["reason"] = f"solved on attempt {attempt_index}"
            report["datadome_cookie"] = bool(await datadome_cookie(chromium_session))
            break

        # A rejected drag on a flagged network will keep failing. Stop early
        # rather than retrying a route that cannot succeed.
        if detection.network_flagged and attempt_index >= 2:
            report["reason"] = (
                "drag executed but the challenge was reissued; DataDome names the "
                f"egress IP ({detection.flagged_ip}) as automated, which a "
                "client-side solve cannot clear"
            )
            report["needs_new_ip"] = True
            break

        # Widget re-issued: cool off, then try again with a fresh handle.
        await asyncio.sleep(random.uniform(2.0, 4.0))

    if not report["ok"] and saw_variant_without_slider:
        report.setdefault(
            "reason",
            "challenge variant has no slider; use the audio route or a real solver",
        )
        report["needs_human"] = True
    return report