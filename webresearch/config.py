"""Project configuration: lanes, engines, and their status.

One source of truth for WHICH implementation is official and which is
experimental, so every surface (CLI, MCP, server, docs) answers the
question the same way instead of by inference.

Current status (2026-11-04):
- PYTHON (webresearch/) is the OFFICIAL product: full agent browser —
  script execution, captcha solver, login/OTP, frame messaging, video
  lane, 35 shell commands; 859-test suite green.
- RUST (dosh-browser-core/) is EXPERIMENTAL: the pipeline is ported and
  unit-tested (fetch → parse → project → session → shell → server,
  169/169 tests) with the doshgentv8 V8 core, ACD 2.0, champion INT8 neural
  interactivity model, unified network pipeline (SSRF IpFilter, SingleFlight,
  RateLimiter, Robots, CORS, WebBotAuth, RFC 9111 SQLite Cache), but page script
  execution is not yet wired into goto(), and the adversarial/agent layers (captcha, login,
  frames, ws, ESM, MCP, telemetry) are not ported. It is not behind any agent surface.
- The V8 engine core (doshgentv8/) is proven against the QuickJS oracle
  (bridge GREEN: 27.5ms boot, 38µs host calls, native WASM) and will
  become selectable via WR_JS_ENGINE once Phase 2 seam parity passes.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Implementation lanes
# ---------------------------------------------------------------------------

LANES = {
    "python": {
        "path": "webresearch/",
        "status": "official",
        "summary": (
            "The product. Full agent browser: QuickJS engine with live DOM, "
            "reCAPTCHA v2 protocol solver, login/OTP, frame messaging, "
            "WebSocket, ESM translator, video lane, MCP/HTTP/CLI surfaces, "
            "ACD 2.0, champion INT8 neural model, 35 shell commands. 859-test suite green."
        ),
    },
    "rust": {
        "path": "dosh-browser-core/",
        "status": "experimental",
        "summary": (
            "The pure-Rust rewrite, in progress. Pipeline ported and unit-tested "
            "(fetch/parse/project/session/shell/server, 169/169 tests) with the "
            "doshgentv8 V8 core, ACD 2.0, champion INT8 neural model, unified network pipeline, "
            "and HTTP cache, but page script execution is not yet wired into "
            "goto() and the adversarial/agent layers are not ported. Not behind "
            "any agent surface; do not route production traffic to it."
        ),
        "migration_gate": (
            "Lanes flip only after Phase 2 (WR_JS_ENGINE dual-engine parity, "
            "full suite green on both engines) AND the goto() script-execution "
            "wiring is verified against the Python oracle."
        ),
    },
}

OFFICIAL_LANE = "python"


def lane_status(name: str) -> dict:
    """The configured status of a lane; unknown lanes say so."""
    lane = LANES.get(name)
    if lane is None:
        return {"lane": name, "status": "unknown",
                "error": f"unknown lane: {name}; known: {', '.join(sorted(LANES))}"}
    out = {"lane": name, "path": lane["path"], "status": lane["status"],
           "summary": lane["summary"]}
    if "migration_gate" in lane:
        out["migration_gate"] = lane["migration_gate"]
    return out


# ---------------------------------------------------------------------------
# JavaScript engine selection (the V8 lane is proven but not yet default;
# default stays QuickJS until Phase 2 parity passes the full suite)
# ---------------------------------------------------------------------------

JS_ENGINE_DEFAULT = "quickjs"
JS_ENGINES = {
    "quickjs": {
        "status": "official",
        "summary": "Default engine. The full engine/ layer (runtime, frames, "
                   "ws, modules, captcha support) is built and tested on it.",
    },
    "doshgentv8": {
        "status": "experimental",
        "summary": "Our V8 lane (core/ pure-Rust + py/ facade). Bridge proven "
                   "against the QuickJS oracle (native WASM, 3.8x faster boot); "
                   "selectable via WR_JS_ENGINE only after Phase 2 seam parity "
                   "(13 host callables + timer/clock pump) passes the suite.",
    },
}


def js_engine() -> str:
    """The active JS engine for THIS process.

    WR_JS_ENGINE=doshgentv8 selects the experimental V8 lane; anything else
    (or unset) is the official QuickJS default. The selection is honest by
    construction: the V8 facade mirrors quickjs.Context, so the seam is the
    same — but until Phase 2 lands, callers should treat a V8 selection as
    experimental and not verified by the full suite.
    """
    choice = (os.environ.get("WR_JS_ENGINE") or "").strip().lower()
    if choice in JS_ENGINES:
        return choice
    return JS_ENGINE_DEFAULT


def js_engine_status() -> dict:
    active = js_engine()
    return {
        "active": active,
        "default": JS_ENGINE_DEFAULT,
        "requested": (os.environ.get("WR_JS_ENGINE") or "").strip(),
        "engines": {name: {"status": meta["status"], "summary": meta["summary"]}
                    for name, meta in JS_ENGINES.items()},
        "note": (
            f"'{active}' is active. " +
            ("The experimental V8 lane was requested via WR_JS_ENGINE and is "
             "in use; it is proven at the bridge level but not yet validated "
             "by the full test suite (Phase 2 pending)."
             if active == "doshgentv8" else
             "The official QuickJS engine is in use.")
        ),
    }


# ---------------------------------------------------------------------------
# Surfacing: one honest answer everywhere
# ---------------------------------------------------------------------------

def project_status() -> dict:
    """The machine-readable answer to 'which version is official?'."""
    return {
        "official_lane": OFFICIAL_LANE,
        "lanes": {name: lane_status(name) for name in LANES},
        "js_engine": js_engine_status(),
        "policy": (
            "Python (webresearch/) is the official product until the Rust "
            "migration completes its gate: full-suite green on both engines "
            "and goto() script execution verified against the Python oracle. "
            "All agent surfaces (MCP, HTTP, CLI) serve the official lane."
        ),
    }
