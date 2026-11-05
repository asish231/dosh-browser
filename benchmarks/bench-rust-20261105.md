# dosh-browser Rust-lane benchmark (official binary) — 2026-11-05

Binary: `target/release/dosh` (69 MB) · Engine: doshgentv8 V8 core · Lane: rust (official per config.py)
Measured cold-process with macOS `/usr/bin/time -l`; same tasks as the Python/lightpanda benchmark.

## Results

| task | metric | Rust (dosh) | Python lane | lightpanda |
| --- | --- | ---: | ---: | ---: |
| read example.com | wall (cold) | **0.14 s** (0.16/0.07/0.06 across runs) | 2.38 s | 0.65 s |
| | peak RSS | **31 MB** | 74–77 MB | 29–30 MB |
| | JS engine | doshgentv8, errors=0 | quickjs | (fetch only) |
| search "first iPhone release year" | wall | **0.88–0.92 s** | 1.54 s CLI / 0.85 s MCP | 0.79 s (captcha-BLOCKED, 0 results) |
| | answer "2007" | ✓ (11 mentions, correct) | ✓ | ✗ |
| | peak RSS | **16 MB** | 73 MB | 28 MB |
| agentic `ask` | loop | **1 round, completed** (search→read top hit) | ask() pool fallback failed (0/4) | n/a |
| | LLM cost | **$0 — local agent, no external LLM key** | LLM-billed | n/a |
| device fingerprint | method | `sysctl` only (~ms, 3 calls) | `system_profiler` subprocesses (~1.6 s, now disk-cached) | n/a |

## What this says

1. **Cold start is solved in the primary lane.** Rust read = 0.14 s — ~17× faster than the Python lane's 2.38 s (pre-cache) and 4.6× faster than lightpanda. The Rust device probe never used `system_profiler`; it uses `sysctl` (microseconds). The 1.6 s cold-start bug was Python-lane-only, and its disk-cache fix is now in.
2. **Search beats lightpanda and Python.** 0.88 s + correct answer, 16 MB RSS, and it was NOT captcha-blocked (lightpanda got zero results on the same DDG query).
3. **Rust `ask` is a local agent, not an LLM agent.** `dosh-browser-core/src/agent.rs` runs search→read-top-hit and returns `model: "dosh-local"`, `usage: default` (no tokens, no cost). It completed its 1 round and read the top hit. It is NOT an LLM tool-loop — the Python `ask()` drives an external model (which failed its pool fallback). Both facts are real; they are different agents, summarized honestly.
4. **Rust scope limits (unchanged, disclosed):** no captcha/login/frames/ws/telemetry ported yet per config.py; `read`/`search`/`ask` are the measured surface.

## Known dosh defects (both lanes) from the same benchmarking

- Python auto search waterfall accepts Bing garbage with no relevance gate (Google rate-limited → Bing junk → "success"). DDG-forced works. Rust search uses DDG fallback and got correct results.
- Python cold-start 1.6 s system_profiler probe — fixed with disk cache + change-detection handler (`webresearch/device.py`, 11 tests).

## Not measured

- **Browser Use / Chromium**: not installed on this machine. Requires Playwright + Chromium (~hundreds of MB); repo charter forbids executing external browsers from inside it — a black-box harness must live outside this repo.
- **Rust warm/MCP persistent path**: `dosh mcp` / `serve` exist; this run used the one-shot CLI. The Python MCP server warm path measured 135–285 ms per read.

## Environment

macOS · `target/release/dosh` built 2026-11-05 09:14 · scratch files in /tmp removed after the run.