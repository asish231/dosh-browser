"""Project configuration: lanes, engines, and their status.

One source of truth for WHICH implementation is official and which is
experimental, so every surface (CLI, MCP, server, docs) answers the
question the same way instead of by inference.

Current status (2026-11-04):
- PYTHON (webresearch/) is the OFFICIAL product: full agent browser —
  script execution, captcha solver, login/OTP, frame messaging, video
  lane, unified network pipeline (SSRF IpFilter, SingleFlight, RateLimiter,
  Robots, CORS, WebBotAuth, SQLite Cache), 35 shell commands; 874-test suite green.
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
    "rust": {
        "path": "dosh-browser-core/",
        "status": "primary",
        "summary": (
            "The primary product. Pure-Rust standalone browser binary (dosh) and core "
            "crate with doshgentv8 V8 engine, ACD 2.0, champion INT8 neural model, "
            "unified network pipeline, 41 shell commands, REST server, and MCP server. "
            "Zero Python runtime dependency."
        ),
    },
    "python": {
        "path": "webresearch/",
        "status": "optional-sdk",
        "summary": (
            "The companion Python package and SDK. Full agent browser support: "
            "PyO3 native bindings via doshgentv8_rust, QuickJS fallback, "
            "reCAPTCHA v2 protocol solver, login/OTP, frame messaging, "
            "video lane, MCP/HTTP/CLI surfaces, ACD 2.0, and 41 shell commands."
        ),
    },
}

OFFICIAL_LANE = "rust"


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
# JavaScript engine selection — QuickJS is BANNED on this build (2026-11-07).
# The doshgentv8 V8 lane is the only engine: the Rust MCP server runs it
# natively, and the Python lane defaults to the doshgentv8 facade. QuickJS
# stays in the registry as "banned" so the refusal is explicit, not silent.
# ---------------------------------------------------------------------------

JS_ENGINE_DEFAULT = "doshgentv8"
JS_ENGINES = {
    "quickjs": {
        "status": "banned",
        "summary": "BANNED on this build. The _quickjs C extension's time "
                   "limit is incompatible with host callables and cannot "
                   "interrupt CPU loops; the V8 lane supersedes it. Any "
                   "explicit selection is refused.",
    },
    "doshgentv8": {
        "status": "official",
        "summary": "The only engine: our V8 lane (core/ pure-Rust + py/ "
                   "facade). Bridge proven against the QuickJS oracle "
                   "(native WASM, 3.8x faster boot), wall-clock watchdog "
                   "host-callable safe, fuel metering, capability gates.",
    },
}


def js_engine() -> str:
    """The active JS engine for THIS process.

    doshgentv8 (V8) is the default and the only permitted engine.
    WR_JS_ENGINE=quickjs is refused loudly — QuickJS is banned on this build
    (the _quickjs time limit breaks host callables and cannot bound CPU
    loops). An explicit WR_JS_ENGINE=doshgentv8 is accepted (and is the
    default anyway). Unset/unknown values fall back to doshgentv8.
    """
    choice = (os.environ.get("WR_JS_ENGINE") or "").strip().lower()
    if choice == "quickjs":
        raise ValueError(
            "QuickJS is banned on this build: the _quickjs time limit is "
            "incompatible with host callables and cannot interrupt CPU loops. "
            "Use the doshgentv8 V8 lane (the default)."
        )
    if choice == "doshgentv8":
        return choice
    return JS_ENGINE_DEFAULT


def js_time_limit_ms() -> int:
    """Wall-clock budget per eval (ms), armed on every doshgentv8 context via
    the Rust bridge watchdog (host-callable safe, unlike banned QuickJS).
    0 disables. Env: WR_JS_TIME_LIMIT_MS (default 10000)."""
    try:
        v = int(os.environ.get("WR_JS_TIME_LIMIT_MS", "10000"))
        return max(0, v)
    except ValueError:
        return 10000


def js_memory_limit_mb() -> int:
    """Memory cap (MiB) armed on every QuickJS/DoshContext to bound runaway
    page-JS allocation. Env: WR_JS_MEMORY_LIMIT_MB (default 512)."""
    try:
        return max(1, int(os.environ.get("WR_JS_MEMORY_LIMIT_MB", "512")))
    except ValueError:
        return 512


def js_stack_limit_mb() -> int:
    """Stack cap (MiB) armed on every context. Env: WR_JS_STACK_LIMIT_MB (default 8)."""
    try:
        return max(1, int(os.environ.get("WR_JS_STACK_LIMIT_MB", "8")))
    except ValueError:
        return 8


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
            "Rust (dosh-browser-core/) is the primary standalone browser binary. "
            "Python (webresearch/) is the optional package and SDK for Python agent workflows."
        ),
    }
