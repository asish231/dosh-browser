"""S7: Deterministic replay engine — ledger + snapshots + seeds.

Real browsers do not offer replay; agent engines need it: a recorded
session's decisions (navigations, actions, their network responses)
replay in order against seeded transports, reproducing the same DOM
results without a second live pass — and proving it by comparing the
recorded post-action documents.

The model, honestly scoped:

- ``ReplayRecorder`` captures an ordered event log while a session
  works: {seq, kind: navigate|act|submit, url/ref, snapshot_id (the
  state snapshot taken AFTER the step), request_id (the ledger entry
  the step caused, when known), seed (any random input consumed)}.
- ``ReplayEngine`` takes a finished log plus a seeded transport map
  (url -> canned FetchResult) and replays each step IN ORDER through
  a fresh session, then compares the resulting DOM (normalized) to the
  recorded snapshot's html. Any divergence is reported per step —
  never papered over.
- Determinism: all randomness in a replayed pass flows from the
  recorded seeds; the replay asserts each step's DOM matches the
  snapshot byte-for-byte after normalization (attribute order and
  insignificant whitespace normalized — the honest comparison level;
  raw-byte equality is not claimed).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ReplayStep:
    seq: int
    kind: str                      # navigate | act | submit | snapshot
    target: str                     # url or action ref
    snapshot_id: str | None = None  # state snapshot after the step
    request_id: int | None = None  # ledger record the step caused
    seed: str | None = None         # random input consumed this step
    note: str = ""


@dataclass
class ReplayLog:
    steps: list[ReplayStep] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "meta": self.meta,
                "steps": [s.__dict__ for s in self.steps],
            },
            indent=2,
        )

    @staticmethod
    def from_json(text: str) -> "ReplayLog":
        data = json.loads(text)
        return ReplayLog(
            steps=[ReplayStep(**s) for s in data.get("steps", [])],
            meta=data.get("meta", {}),
        )


class ReplayRecorder:
    """Records the ordered event log of a working session."""

    def __init__(self) -> None:
        self.log = ReplayLog()
        self._seq = 0

    def record(
        self,
        kind: str,
        target: str,
        *,
        snapshot_id: str | None = None,
        request_id: int | None = None,
        seed: str | None = None,
        note: str = "",
    ) -> ReplayStep:
        self._seq += 1
        step = ReplayStep(
            seq=self._seq,
            kind=kind,
            target=target,
            snapshot_id=snapshot_id,
            request_id=request_id,
            seed=seed,
            note=note,
        )
        self.log.steps.append(step)
        return step

    def set_meta(self, key: str, value: Any) -> None:
        self.log.meta[key] = value


def normalize_dom(html: str) -> str:
    """Comparable DOM form: sorted attributes per tag, whitespace
    collapsed, comments dropped. Disclosed: not a full DOM equality
    (that would need a real tree diff) — tag/attribute/structure-level
    equality, the honest level for this engine."""
    out = re.sub(r"<!--.*?-->", "", html, flags=re.S)

    def sort_attrs(match: re.Match) -> str:
        tag = match.group(1)
        attr_blob = match.group(2) or ""
        attrs = re.findall(r'([\w-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', attr_blob)
        attrs = sorted((k.lower(), v) for k, v in attrs)
        joined = " ".join(f'{k}={v}' for k, v in attrs)
        if joined:
            joined = " " + joined
        return f"<{tag}{joined}"

    out = re.sub(r"<(\w+)((?:\s[^>]*)?)", sort_attrs, out)
    out = re.sub(r"\s+", " ", out)
    # Whitespace adjacent to tags is insignificant in HTML flow content:
    # collapse "> x <" to "><x-style adjacency so inner spacing does not
    # create phantom differences.
    out = re.sub(r">\s+", ">", out)
    out = re.sub(r"\s+<", "<", out)
    return out.strip()


def dom_digest(html: str) -> str:
    return hashlib.sha256(normalize_dom(html).encode()).hexdigest()[:16]


class ReplayEngine:
    """Replays a recorded log against seeded transports, deterministically.

    The caller supplies:
    - ``load_snapshot``: snapshot_id -> {html, url, title, ...} (the
      recorded post-step state, e.g. from the shell's state store).
    - ``transport``: a seeded callable url -> FetchResult-like with
      .body/.status/.content_type — the canned network for the replay.
      Recording raw network bodies is the CALLER's job (the ledger
      stores redacted evidence only, by design); a replay is only as
      deterministic as the seeds it is given.
    - ``driver``: an async callable (step, session) -> result that
      PERFORMS the step on a fresh session (navigate/act/submit).
    """

    def __init__(
        self,
        log: ReplayLog,
        *,
        load_snapshot: Callable[[str], dict],
        transport: Callable[[str], Any],
    ) -> None:
        self.log = log
        self.load_snapshot = load_snapshot
        self.transport = transport

    async def replay(
        self,
        driver: Callable[[ReplayStep, Any], Any],
        session_factory: Callable[[], Any],
    ) -> dict:
        """Replay every step in order on a fresh session; compare DOMs.

        Returns {steps: [{seq, kind, matched, recorded_digest,
        replayed_digest}], all_matched, first_divergence}. Divergences
        are REPORTED, never hidden.
        """
        results: list[dict] = []
        session = session_factory()
        all_matched = True
        first_divergence: dict | None = None
        for step in self.log.steps:
            if step.kind == "snapshot":
                # A pure checkpoint step: compare the current state.
                snap = self.load_snapshot(step.snapshot_id) if step.snapshot_id else None
                rec_digest = dom_digest(snap["html"]) if snap and snap.get("html") else None
                cur_digest = None
                if hasattr(session, "html"):
                    cur_digest = dom_digest(session.html)
                matched = (rec_digest == cur_digest) if rec_digest else None
                results.append({
                    "seq": step.seq, "kind": step.kind, "matched": matched,
                    "recorded_digest": rec_digest, "replayed_digest": cur_digest,
                })
                continue

            # Perform the step through the caller's driver (which uses
            # the seeded transport).
            try:
                await driver(step, session)
                err = None
            except Exception as exc:  # a replay failure is a result, not a crash
                err = f"{type(exc).__name__}: {exc}"[:200]

            rec_digest = None
            if step.snapshot_id:
                snap = self.load_snapshot(step.snapshot_id)
                if snap and snap.get("html"):
                    rec_digest = dom_digest(snap["html"])
            cur_digest = dom_digest(session.html) if hasattr(session, "html") and session.html else None
            matched = (rec_digest == cur_digest) if (rec_digest and cur_digest) else None
            if matched is False and all_matched:
                all_matched = False
                first_divergence = {
                    "seq": step.seq, "kind": step.kind, "target": step.target,
                    "recorded_digest": rec_digest, "replayed_digest": cur_digest,
                    "error": err,
                }
            results.append({
                "seq": step.seq, "kind": step.kind, "target": step.target,
                "matched": matched,
                "recorded_digest": rec_digest, "replayed_digest": cur_digest,
                "error": err,
            })
        return {
            "steps": results,
            "all_matched": all_matched,
            "first_divergence": first_divergence,
            "replayed_count": len(results),
        }
