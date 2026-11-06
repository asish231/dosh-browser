"""S7: Deterministic replay engine — hermetic tests.

Pins the replay contract: a recorded step log + per-step state
snapshots + seeded transport replay in ORDER on a fresh session and
reproduce the same DOM (normalized) at every step. Divergences are
reported, never hidden — the negative test proves a divergent replay
is DETECTED.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.replay import (
    ReplayEngine,
    ReplayLog,
    ReplayRecorder,
    ReplayStep,
    dom_digest,
    normalize_dom,
)


@dataclass
class FetchResult:
    body: str
    status: int = 200
    content_type: str = "text/html"


class FakeSession:
    """A minimal session whose html the driver mutates, as a real
    session's DOM mutates per step."""

    def __init__(self, transport):
        self.transport = transport
        self.html = ""
        self.visited: list[str] = field(default_factory=list) if False else []


def _driver_factory(snapshots: dict, transport):
    async def driver(step: ReplayStep, session: FakeSession):
        if step.kind == "navigate":
            res = transport(step.target)
            session.html = res.body
            session.visited.append(step.target)
        elif step.kind == "act":
            # An act re-renders: fetch the same page again with the action
            # applied via the recorded seed.
            res = transport(step.target)
            rendered = res.body
            if step.seed == "click-1":
                rendered = rendered.replace("Counter: 0", "Counter: 1")
            session.html = rendered
        return None

    return driver


def _make_recording():
    """A two-step recorded session: navigate, then act."""
    recorder = ReplayRecorder()
    recorder.set_meta("session", "replay-test")
    snapshots = {
        "snap-1": {"html": "<html><body>Counter: 0</body></html>", "url": "https://x.test/"},
        "snap-2": {"html": "<html><body>Counter: 1</body></html>", "url": "https://x.test/"},
    }
    transport_map = {
        "https://x.test/": FetchResult(body="<html><body>Counter: 0</body></html>"),
    }
    recorder.record("navigate", "https://x.test/", snapshot_id="snap-1", request_id=1)
    recorder.record("act", "https://x.test/", snapshot_id="snap-2", seed="click-1", request_id=2)
    return recorder.log, snapshots, transport_map


def test_replay_reproduces_recorded_doms_in_order():
    log, snapshots, transport_map = _make_recording()

    def transport(url):
        return transport_map[url]

    async def run():
        engine = ReplayEngine(
            log,
            load_snapshot=lambda sid: snapshots[sid],
            transport=transport,
        )
        return await engine.replay(
            driver=_driver_factory(snapshots, transport),
            session_factory=lambda: FakeSession(transport),
        )

    import asyncio

    report = asyncio.run(run())
    assert report["replayed_count"] == 2, report
    assert report["all_matched"] is True, f"every step must reproduce the recorded DOM: {report}"
    assert report["first_divergence"] is None
    # Per-step digests recorded and compared:
    s1, s2 = report["steps"]
    assert s1["matched"] is True and s2["matched"] is True
    assert s1["recorded_digest"] == s1["replayed_digest"]
    assert s2["recorded_digest"] == s2["replayed_digest"]
    # The step order is the recorded order:
    assert [st["seq"] for st in report["steps"]] == [1, 2]


def test_replay_detects_divergence_honestly():
    """A transport that serves a DIFFERENT body than recorded must be
    reported as a divergence — never silently 'matched'."""
    log, snapshots, transport_map = _make_recording()
    # Poison the seed: the served page diverges from the snapshot.
    transport_map["https://x.test/"] = FetchResult(body="<html><body>Counter: 999</body></html>")

    def transport(url):
        return transport_map[url]

    async def run():
        engine = ReplayEngine(
            log,
            load_snapshot=lambda sid: snapshots[sid],
            transport=transport,
        )
        return await engine.replay(
            driver=_driver_factory(snapshots, transport),
            session_factory=lambda: FakeSession(transport),
        )

    import asyncio

    report = asyncio.run(run())
    assert report["all_matched"] is False, "a divergent replay must not claim success"
    div = report["first_divergence"]
    assert div is not None and div["seq"] == 1, report
    assert div["recorded_digest"] != div["replayed_digest"]


def test_log_roundtrips_through_json():
    log, _, _ = _make_recording()
    text = log.to_json()
    parsed = ReplayLog.from_json(text)
    assert [ (s.seq, s.kind, s.target, s.snapshot_id) for s in parsed.steps ] == \
        [ (1, "navigate", "https://x.test/", "snap-1"),
          (2, "act", "https://x.test/", "snap-2") ]


def test_normalize_dom_orders_attributes_and_whitespace():
    a = normalize_dom('<div   class="x" id="a">hi</div>')
    b = normalize_dom('<div id="a"  class="x" > hi </div>')
    assert a == b, "attribute order and whitespace must not affect comparison"
    assert dom_digest('<div id="a" class="x">hi</div>') == dom_digest('<div class="x" id="a"> hi  </div>')
    assert normalize_dom("<!-- secret comment --><p>x</p>") == "<p>x</p>"


def test_snapshot_checkpoint_step_compares_current_state():
    log, snapshots, transport_map = _make_recording()
    recorder_like_steps = list(log.steps)
    # Add a snapshot-only checkpoint after step 1.
    checkpoint = ReplayStep(seq=3, kind="snapshot", target="", snapshot_id="snap-1")
    log.steps = [recorder_like_steps[0], checkpoint, recorder_like_steps[1]]

    def transport(url):
        return transport_map[url]

    class StatefulSession(FakeSession):
        def __init__(self, transport):
            super().__init__(transport)

    async def run():
        engine = ReplayEngine(
            log,
            load_snapshot=lambda sid: snapshots[sid],
            transport=transport,
        )

        async def driver(step, session):
            if step.kind == "navigate":
                res = transport(step.target)
                session.html = res.body
            elif step.kind == "act":
                res = transport(step.target)
                rendered = res.body
                if step.seed == "click-1":
                    rendered = rendered.replace("Counter: 0", "Counter: 1")
                session.html = rendered

        return await engine.replay(driver=driver, session_factory=lambda: StatefulSession(transport))

    import asyncio

    report = asyncio.run(run())
    kinds = [s["kind"] for s in report["steps"]]
    assert kinds == ["navigate", "snapshot", "act"], report
    # The snapshot checkpoint after navigation must match snap-1.
    snap_step = report["steps"][1]
    assert snap_step["matched"] is True, f"checkpoint comparison: {snap_step}"
    assert report["all_matched"] is True, report
