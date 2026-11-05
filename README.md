# DOSH: The Autonomous Browser for AI Agents

[![CI](https://github.com/asish231/dosh-browser/actions/workflows/ci.yml/badge.svg)](https://github.com/asish231/dosh-browser/actions/workflows/ci.yml)
[![Release](https://github.com/asish231/dosh-browser/actions/workflows/release.yml/badge.svg)](https://github.com/asish231/dosh-browser/releases)
[![Version](https://img.shields.io/github/v/release/asish231/dosh-browser)](https://github.com/asish231/dosh-browser/releases)
[![License](https://img.shields.io/badge/license-research%20only-lightgrey.svg)](LICENSE)
[![Engine](https://img.shields.io/badge/engine-doshgentv8%20(V8%2015.9)-orange.svg)](doshgentv8/)
[![Pure Rust](https://img.shields.io/badge/core-pure--rust%20(zero--python)-black.svg)](dosh-browser-core/)

> **The browser built from scratch for autonomous AI agents, not human eyes.**  
> **No Chromium. No Playwright. No Puppeteer. No Selenium. No CDP.**

DOSH is a standalone agent browser engineered from the ground up: an owned HTTP/2 transport, our own DOM event model (`minidom.js`), an agent-tuned V8 engine (`doshgentv8`), an LLM-native action compiler, and an in-memory control plane (DOSH Control Protocol).

```
┌────────────────────────────────────────────────────────────────────────┐
│ TOTAL TIME TAKEN                                                       │
│ DOSH Browser:  ███ 3.3s                                                │
│ Browser-Use:   █████████████████████████████ 29.0s (8.7x slower)       │
├────────────────────────────────────────────────────────────────────────┤
│ TOTAL TOKENS BURNED                                                    │
│ DOSH Browser:  █ 1,204 tokens                                          │
│ Browser-Use:   █████████████████████████████ ~30,000 tokens (25x more)  │
├────────────────────────────────────────────────────────────────────────┤
│ RAM CONSUMPTION                                                        │
│ DOSH Browser:  ██ 48 MB                                                │
│ Browser-Use:   █████████████████████████████ 620 MB (13x heavier)      │
└────────────────────────────────────────────────────────────────────────┘
```

*Browser-Use figures in this chart are from the original 2026-10-10 comparison (commit `ca7e6b46`); Browser-Use and Chromium are not installed on the benchmark machine, so those numbers were not re-measured in the 2026-11-05 audit — see the Scope honesty note under [Benchmarks](#benchmarks-dosh-vs-browser-use-playwright-vs-scrapers).*

---

## ⚡ Quick Install

### 1. One-Line Installer (Recommended)

**macOS & Linux:**
```bash
# Default: Official Standalone Pure-Rust Browser (Zero Python dependencies)
curl -fsSL https://raw.githubusercontent.com/asish231/dosh-browser/main/install.sh | bash

# Optional: Install Standalone Browser AND Python Companion SDK (-p / --python)
curl -fsSL https://raw.githubusercontent.com/asish231/dosh-browser/main/install.sh | bash -s -- -p
```

**Windows (PowerShell):**
```powershell
irm https://raw.githubusercontent.com/asish231/dosh-browser/main/install.ps1 | iex
```

The installer detects your platform, installs `dosh` into your local bin directory (`~/.dosh/bin` or `%USERPROFILE%\.dosh\bin`), verifies the release archive against its published SHA-256 checksum, and configures your `PATH`.

### 2. Verify Your Download

Every release asset ships with a `.sha256` sibling plus a combined `SHA256SUMS.txt`. Example (macOS Apple Silicon):

```bash
curl -fLO https://github.com/asish231/dosh-browser/releases/latest/download/dosh-aarch64-apple-darwin.tar.gz
curl -fLO https://github.com/asish231/dosh-browser/releases/latest/download/dosh-aarch64-apple-darwin.tar.gz.sha256
shasum -a 256 -c dosh-aarch64-apple-darwin.tar.gz.sha256   # → OK
```

On Linux use `sha256sum -c`; on Windows compare against `SHA256SUMS.txt` from the release page.

### Supported Platforms

| Platform | Release asset |
| :--- | :--- |
| macOS (Apple Silicon) | `dosh-aarch64-apple-darwin.tar.gz` |
| macOS (Intel) | `dosh-x86_64-apple-darwin.tar.gz` |
| Linux (x86_64) | `dosh-x86_64-unknown-linux-gnu.tar.gz` |
| Windows (x64) | `dosh-x86_64-pc-windows-msvc.zip` |
| Linux (aarch64) | No prebuilt asset yet — [build from source](#3-build-from-source) |

### 3. Build From Source

```bash
git clone https://github.com/asish231/dosh-browser.git
cd dosh-browser
cargo build --release --bin dosh    # → target/release/dosh (a few minutes; prebuilt V8 downloads on first build)
./target/release/dosh product       # smoke test: lane + engine status
```

> **Note:** `cargo install --git …` is not usable yet: the repository carries `research_repos/` submodule placeholders (`browser-use`, `chromium`) with no registered submodule URLs, so Cargo's submodule-update step fails. A plain `git clone` builds fine — that is the supported source path for now.

### 4. Install Python Companion Package (For Python Agent Frameworks)

Not yet on PyPI — install from the repository:

```bash
pip install "webresearch @ git+https://github.com/asish231/dosh-browser.git"

# Or pin the latest tagged release:
pip install "webresearch @ git+https://github.com/asish231/dosh-browser.git@v1.0.1"

# Or with uv (inside an activated virtual environment):
uv pip install "webresearch @ git+https://github.com/asish231/dosh-browser.git"
```

---

## Architecture: Dual-Lane Model

Every surface respects our two-lane architecture:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DOSH ARCHITECTURE                               │
├────────────────────────────────────────────────────────────────────────┤
│ 1. OFFICIAL PRIMARY: Standalone Rust Core & Binary (dosh)              │
│    - Pure Rust pipeline: fetch → parse → project → session → shell      │
│    - Custom agentic V8 engine (doshgentv8) with Liftoff fuel traps      │
│    - In-Memory C-ABI Control Protocol (DCP) with zero-copy dispatch     │
│    - Unified NetworkEngine: SSRF IpFilter, RFC 9111 Cache, SingleFlight │
│    - 45 shell commands, stdio MCP server, and REST HTTP API             │
│    - Zero Python runtime dependency.                                    │
├────────────────────────────────────────────────────────────────────────┤
│ 2. OPTIONAL COMPANION: Python Companion SDK (webresearch)              │
│    - OpenAI-style tool schemas + MCP server; any tool-calling agent     │
│    - Native PyO3 V8 bridge (doshgentv8_rust) + QuickJS fallback         │
│    - ReCAPTCHA v2 protocol solver, OTP checkpoints, and video lane      │
│    - Triggered via `dosh -p <cmd>` or `python -m webresearch`           │
└────────────────────────────────────────────────────────────────────────┘
```

### Switching Lanes via CLI Flags

- **`dosh <cmd>`** *(Default)*: Runs the official pure-Rust browser core with embedded V8.
- **`dosh -p <cmd>`** *(or `--python`)*: Runs the Python companion SDK CLI (`webresearch` subcommands: `read`, `search`, `images`, `captcha`, `ask`, `models`, `mcp`, `serve`, `shell`, `profile`).
- **`dosh -d <cmd>`** *(or `--rust`)*: Explicitly forces pure-Rust execution.

```bash
# Read a page using the high-performance pure-Rust pipeline
dosh read https://news.ycombinator.com

# Or read via the Python companion SDK
dosh -p read https://news.ycombinator.com
```

---

## CLI Usage

`dosh --help` output:

```bash
dosh — Agent-native browser (pure Rust core, doshgentv8 V8 engine)
Usage: dosh [-p|--python] [-d|--rust] <command> [args...]

Lanes:
  -d, --rust, --default     Official standalone pure-Rust browser (default)
  -p, --py, --python        Optional Python companion SDK (webresearch)

Commands:
  read <url> [--json]       Read and project an LLM-ready page model
  search <query> [--json] [--engine <auto|bing|google|ddg|yahoo|lite>]
                            Web search with multi-engine waterfall fallback
  download <url>            Download file or PDF streaming to disk with SHA-256
  ask <prompt>              Let an agent research and answer using browser tools
  shell [-c <command>]      Agent browser terminal (45 commands)
  server / serve [--port N] Run REST HTTP API & tool invoke endpoints
  mcp                       Run Model Context Protocol server over stdio
  product / status          Show official lane and engine status
  version / --version / -v  Show binary and engine version
```

### Examples

```bash
# 1. Project an LLM-ready reading document (ACD 2.0 with semantic block tags)
dosh read https://en.wikipedia.org/wiki/Rust_(programming_language)

# 2. Autonomous search with waterfall fallback
dosh search "latest AI agent frameworks 2026"

# 3. Stream a PDF or dataset directly to disk with SHA-256 verification
dosh download https://arxiv.org/pdf/2301.00001.pdf

# 4. Local research agent: search + read the top hit (no LLM key required)
dosh ask "Cloudflare Workers pricing and tier limits"

# 5. Start the REST API server
dosh serve --port 4747

# 6. Check engine status
dosh status
```

---

## Model Context Protocol (MCP) Setup

DOSH natively supports the **Model Context Protocol (MCP)** over stdio: `dosh mcp` exposes the browser to any MCP-capable assistant (Claude Desktop, Cursor, and others).

### Configuration (`claude_desktop_config.json` or `cursor_config.json`)

```json
{
  "mcpServers": {
    "dosh": {
      "command": "dosh",
      "args": ["mcp"]
    }
  }
}
```

### Available MCP Tools

| MCP Tool | Description |
| :--- | :--- |
| `browser_navigate` | Navigate to a URL, execute client-side JS in V8, and return structured Page view. |
| `browser_actions` | Retrieve the complete operable control surface with content-addressed `ref` markers. |
| `browser_click` | Click an element by reference (`ref: "e1"`). |
| `browser_type` | Type text into an input or contenteditable element with humanized cadence. |
| `browser_search` | Search the web with automatic fallback; returns clean markdown hits. |
| `browser_acd` | Extract the full Agent Content Document (ACD 2.0) with micro-ML semantic tags. |
| `browser_query` | Query page content blocks by semantic category or keyword. |
| `browser_slice` | Slice a specific range of blocks (e.g. `b1` to `b20`) for focused token budgets. |
| `browser_wasm` | Inspect and throttle Liftoff WASM fuel traps (`wasm.fuel`), anti-tarpit defense. |
| `browser_clock` | Freeze or advance the deterministic virtual host clock (`clock.set`, `clock.advance`). |
| `browser_gate` | Dynamically toggle runtime capability gates (`eval`, `wasm`, `net`). |
| `browser_heap` | Inspect V8 128MB pruned heap metrics and GC pauses. |
| `browser_shell` | Execute any of the 45 terminal commands directly. |

The Python companion lane runs its own MCP server (`dosh -p mcp`) with an extended tool set — uploads, downloads, video, CAPTCHA, OTP, tab management, and stealth state.

---

## DoshShell: 45 Terminal Commands

Run `dosh shell` for an interactive agent terminal or batch commands with `dosh shell -c "<cmd>"`:

```bash
# Set WASM anti-tarpit fuel limit and open page
dosh shell -c "wasm fuel=50000" -c "open https://example.com" -c "actions"
```

| Category | Commands |
| :--- | :--- |
| **Navigation & DOM** | `open`, `read`, `actions`, `act`, `ref`, `wait`, `tabs`, `frames`, `layers`, `screenshot` |
| **V8 Engine Controls** | `wasm` (fuel traps), `clock` (virtual time), `rng` (seeded PRNG), `heap` (128MB limit), `gate` (capabilities) |
| **Network & Security** | `net` (observations), `net.capture` (redacted bodies), `net.rule` / `net.unrule` (block/mock), `net.apis` (API discovery), `cookies`, `video` |
| **State & Checkpoints** | `checkpoint` (durable SQLite save), `restore` (rehydrate session), `storage`, `db`, `history` |
| **Evidence & Diff** | `snapshot`, `diff`, `trace` (causal action footprint), `watch` (mutation observer), `console` |
| **Captcha & Login** | `solve_captcha` (challenge inspection in the Rust lane), `login` (delegates to the Python runner) |
| **Scripting & Automation** | `eval`, `frame.eval`, `skill.save`, `skill.run`, `skill.list`, `skill.delete`, `skill.update`, `caps` |
| **Help & Status** | `help`, `product`, `status` |

---

## Python Companion SDK

For Python agents, and any framework that consumes OpenAI-style tool schemas (`webresearch.tools.TOOLS`, `{"type": "function", ...}`) or speaks MCP:

```python
import asyncio
from webresearch import Browser

async def main():
    browser = Browser()
    session = await browser.create()

    # Navigate and run page scripts
    page = await session.goto("https://news.ycombinator.com")
    print(f"Title: {page.title}")
    print(f"Content:\n{page.view[:300]}")

    # List reachable controls
    actions = session.actions()
    print(f"Reachable controls: {len(actions)}")

    await browser.close_all()

asyncio.run(main())
```

This exact script was run against the companion package; it prints `Title: Hacker News` and a reachable-controls count (200 on Hacker News at the time of writing).

---

## Benchmarks: DOSH vs. Browser-Use (Playwright) vs. Scrapers

| Dimension | **DOSH Browser** *(Pure Rust + doshgentv8)* | **Browser-Use** *(Chromium + CDP + Vision)* | **Traditional Scrapers** *(Requests + BS4)* |
| :--- | :--- | :--- | :--- |
| **Architecture** | Pure Rust core + agent-tuned V8 | Headless Chromium via CDP | HTTP client + regex / lxml |
| **Average Task Duration** | **2.5 – 4.5 seconds** | **25 – 60 seconds** | **0.5 – 1.5 seconds** |
| **Tokens per Task (3 steps)** | **~1,200 – 2,500 tokens** | **~25,000 – 60,000 tokens** | *N/A (Rule-based scripts)* |
| **RAM Footprint per Tab** | **~35 – 55 MB** | **450 – 900 MB** | **~15 – 25 MB** |
| **Cold-Start Time** | **< 10 ms** | **2,500 – 4,500 ms** | **< 50 ms** |
| **Client-Side JS Execution** | **Yes** (doshgentv8 + QuickJS) | **Yes** (Full V8 engine) | **No** (Static HTML only) |
| **Modern SPAs (React 19, Vue)**| **Supported** | **Supported** | **Fails completely** |
| **Action Resolution** | **Content-Addressed `ref`** | Bounding boxes / Coordinates / JS eval | CSS selectors / XPath |
| **Anti-Tarpit WASM Metering** | **Native Liftoff CPU Traps** | None (Hangs on infinite WASM loops) | None |

*The ranges in this table (DOSH, Browser-Use, and scrapers) are from the original 2026-10-10 comparison; Browser-Use/Chromium were not installed on the benchmark machine and were not re-measured in the 2026-11-05 audit — today's measured numbers are under [Verified Benchmarks](#verified-benchmarks--real-runs-2026-11-05).*

### Verified Benchmarks — Real Runs (2026-11-05)

Cold-process wall time and peak RSS measured on macOS with `/usr/bin/time -l`; warm-path figures are labeled as such. Raw data and run logs: [`benchmarks/`](benchmarks/).

**Dual-lane, same task** — read `https://example.com`, both official paths:

| Metric | **Rust lane** (`dosh read`) | Python companion (`webresearch`) |
| :--- | ---: | ---: |
| Cold wall time | **0.14 s median** (0.11–0.22 s across 5 runs) | 1.1–1.5 s (3 runs; device probe disk-cached — 2.38 s pre-cache) |
| Peak RSS | **~31 MB** | ~75–78 MB |
| JS execution | doshgentv8 (V8), 0 errors | QuickJS |
| Web search (DDG, "first iPhone release year") | **0.88 s / 16 MB — correct result** | 1.54 s CLI / 0.85 s MCP warm |
| Warm re-reads | — | **135–285 ms** (persistent MCP server) |
| Agentic `ask` | 1 round, **$0** (local agent, no LLM key) | LLM-driven tool loop |

**vs. Reference Headless C++ Engine** (`official CLI, same tasks):

| Task | dosh (Rust) | Reference Engine | Result |
| :--- | ---: | ---: | :--- |
| Read example.com | **0.14 s / 31 MB** | 0.65 s / 29.9 MB | dosh **4.6× faster** |
| Title extraction | ✓ structured `TITLE:` field | ✗ heading dropped from dump | dosh |
| Search (DDG) | **0.88 s — correct result** | 0.79 s — **captcha-blocked, 0 results** | dosh completes; Reference fails the task |

**LLM pool benchmark** (models driving browser tools via `llm-pool-proxy`; cost from a dated provider price table in `webresearch/settings.py`):

| Model | Success | Tokens p50 | Wall p50 | Cost / success |
| :--- | ---: | ---: | ---: | ---: |
| mercury-2 | 100% | 164 | 1.9 s | ~$0.0001 |
| deepseek-v4.1-flash | 67% | 95 | 3.4 s | ~$0.0001 |
| asi1-ultra | 100% | 201 | 4.6 s | ~$0.0006 |

**Scope honesty:** the Rust lane is the faster engine but not at full parity yet: CAPTCHA solving is inspection-only (the `solve_captcha` probe), the shell's `login` command delegates to the Python runner, and the Rust core has no WebSocket client yet. Neither lane sends telemetry. The multi-engine search waterfall has no query-relevance gate yet — junk hits can win, as recorded in `benchmarks/`. Browser-Use and Chromium were not installed on the benchmark machine — **no numbers were invented for them**; a black-box harness outside this repo is required to measure them.

---

## DevOps & Release Workflow

### Automated CI/CD
- **Continuous Integration (`.github/workflows/ci.yml`)**: Runs Rust workspace tests (`cargo test --workspace`) and the full Python test suite (`uv run pytest`) on every push to `main` and every pull request.
- **Automated Releases (`.github/workflows/release.yml`)**: Triggers on version tag pushes (`v*.*.*`), cross-compiles release binaries for macOS (arm64, x86_64), Linux (x86_64), and Windows (x86_64 MSVC), generates SHA-256 checksums, and publishes them to GitHub Releases.

### Creating a Production Release
To ship a new version:
```bash
git tag v1.1.0
git push origin v1.1.0
```
GitHub Actions will automatically build, package, and publish the release binaries.

---

## Testing

```bash
cargo test --workspace                       # Rust core + doshgentv8 engine
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest tests/            # Python companion (or: uv sync --all-extras && uv run pytest tests/)
```

As of 2026-11-05: **228 Rust tests** (199 core + 29 engine) and **926 Python tests** pass; 8 Python tests self-skip when optional dependencies are absent.

## Repository Layout

| Path | What it is |
| :--- | :--- |
| `dosh-browser-core/` | Official pure-Rust browser core and the `dosh` binary |
| `doshgentv8/` | Agent-tuned V8 engine — Rust embedding (`core/`, `py/`) + patched V8 source (`base/`) |
| `webresearch/` | Optional Python companion SDK + MCP and REST servers |
| `benchmarks/`, `scripts/` | Benchmark harness and committed raw results |
| `research_repos/` | Third-party reading material only — never built, imported, or shipped |

## Privacy & Telemetry

No analytics and no telemetry: the browser reports nothing beyond the pages you ask it to fetch. The Python companion's `webresearch/telemetry.py` is a local, in-process network ledger for the owned HTTP client — it is not attached on import and has no remote endpoint.

## Project Docs

- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup, tests, and the PR checklist
- [SECURITY.md](SECURITY.md) — private vulnerability reporting
- [CHANGELOG.md](CHANGELOG.md) — releases and unreleased changes
- [AGENTS.md](AGENTS.md) — the working rules for agents in this repo

## License

Copyright (c) 2026 Asish Kumar Sharma. All rights reserved.

Licensed under the DOSH Browser software license ([LICENSE](LICENSE)): **research and study only; no commercial usage**. Any other use requires a prior written grant from the copyright holder — see [LICENSE](LICENSE) for the exact terms.
