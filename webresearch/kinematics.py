"""Humanized Mouse Kinematics and Keystroke Cadence.

Simulates biological human interaction patterns:
- Non-linear cubic Bézier mouse movement with physiological tremor/jitter.
- Fitts's Law velocity profiles (acceleration phase -> deceleration onto target).
- Variable keystroke intervals with natural inter-word and punctuation hesitations.
"""

from __future__ import annotations

import math
import random
from typing import Tuple

Point = Tuple[float, float]


def _cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Evaluate cubic Bézier curve at parameter t in [0, 1]."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t

    x = uuu * p0[0] + 3.0 * uu * t * p1[0] + 3.0 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3.0 * uu * t * p1[1] + 3.0 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)


def generate_human_trajectory(
    start: Point,
    end: Point,
    steps: int = 20,
    deviation: float = 0.25,
) -> list[Point]:
    """Generate a realistic human-like mouse path from start to end."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    if dist < 4.0:
        return [end]

    steps = max(5, min(int(dist / 12.0), 35))

    # Compute a perpendicular vector to create an arc (human arm curvature)
    perp_x = -dy
    perp_y = dx
    norm = math.hypot(perp_x, perp_y) or 1.0
    perp_x /= norm
    perp_y /= norm

    # Randomized arc direction and magnitude
    arc_mag = dist * deviation * random.uniform(-1.0, 1.0)
    ctrl1_mag = arc_mag * random.uniform(0.6, 1.2)
    ctrl2_mag = arc_mag * random.uniform(0.2, 0.8)

    p0 = start
    p1 = (
        start[0] + dx * 0.3 + perp_x * ctrl1_mag,
        start[1] + dy * 0.3 + perp_y * ctrl1_mag,
    )
    p2 = (
        start[0] + dx * 0.75 + perp_x * ctrl2_mag,
        start[1] + dy * 0.75 + perp_y * ctrl2_mag,
    )
    p3 = end

    trajectory: list[Point] = []
    for i in range(1, steps + 1):
        # Non-linear easing (smooth acceleration, deceleration near target)
        raw_t = i / steps
        # S-curve easing: 3*t^2 - 2*t^3
        t = 3.0 * (raw_t**2) - 2.0 * (raw_t**3)
        pt = _cubic_bezier(p0, p1, p2, p3, t)

        # Add physiological hand micro-tremor (decaying as approaching target)
        tremor_scale = (1.0 - raw_t) * 1.5
        jitter_x = random.gauss(0.0, 0.4) * tremor_scale
        jitter_y = random.gauss(0.0, 0.4) * tremor_scale

        if i == steps:
            # End exactly on target
            trajectory.append(end)
        else:
            trajectory.append((pt[0] + jitter_x, pt[1] + jitter_y))

    return trajectory


def human_keystroke_delays(text: str, mean_ms: float = 65.0, std_ms: float = 18.0) -> list[float]:
    """Generate realistic inter-key delays in seconds."""
    delays: list[float] = []
    for char in text:
        delay = max(0.025, min(random.gauss(mean_ms / 1000.0, std_ms / 1000.0), 0.22))
        # Humans pause slightly longer on spaces and sentence breaks
        if char == " ":
            delay += random.uniform(0.03, 0.08)
        elif char in ".,!?;:":
            delay += random.uniform(0.05, 0.14)
        delays.append(delay)
    return delays


def generate_human_drag_steps(
    dx: float,
    dy: float,
    start_x: float = 0.0,
    start_y: float = 0.0,
) -> list[dict]:
    """Generate high-fidelity human mouse drag packet sequence."""
    pts = generate_human_trajectory((start_x, start_y), (start_x + dx, start_y + dy))
    steps: list[dict] = []
    prev_x, prev_y = start_x, start_y
    for x, y in pts:
        mx = round(x - prev_x, 2)
        my = round(y - prev_y, 2)
        dt = round(random.uniform(9.0, 16.0), 1)
        steps.append({
            "x": round(x, 2),
            "y": round(y, 2),
            "movementX": mx,
            "movementY": my,
            "dt": dt,
        })
        prev_x, prev_y = x, y
    return steps


def generate_human_typing_delays(text: str) -> list[float]:
    """Inter-key flight delays in milliseconds."""
    delays_sec = human_keystroke_delays(text)
    return [round(d * 1000.0, 1) for d in delays_sec]

