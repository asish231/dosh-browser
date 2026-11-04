# DOSH: The Autonomous Browser for AI Agents

[![CI](https://github.com/asish231/dosh-browser/actions/workflows/ci.yml/badge.svg)](https://github.com/asish231/dosh-browser/actions/workflows/ci.yml)
[![Release](https://github.com/asish231/dosh-browser/actions/workflows/release.yml/badge.svg)](https://github.com/asish231/dosh-browser/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
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

The installer detects your platform (macOS Apple Silicon/Intel, Linux x86_64/aarch64, Windows x64), installs `dosh` into your local bin directory (`~/.dosh/bin` or `%USERPROFILE%\.dosh\bin`), and automatically configures your `PATH`.

### 2. Install from Cargo

```bash
cargo install --git https://github.com/asish231/dosh-browser.git dosh-browser-core --bin dosh
```

### 3. Install Python Companion Package (For Python Agent Frameworks)

```bash
pip install dosh-browser
# or
uv add dosh-browser
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
│    - 41 shell commands, stdio MCP server, and REST HTTP API             │
│    - Zero Python runtime dependency.                                    │
├────────────────────────────────────────────────────────────────────────┤
│ 2. OPTIONAL COMPANION: Python Companion SDK (webresearch)              │
│    - Agent framework integration (LangChain, LlamaIndex, AutoGen)       │
│    - Native PyO3 V8 bridge (doshgentv8_rust) + QuickJS fallback         │
│    - ReCAPTCHA v2 protocol solver, OTP checkpoints, and video lane      │
│    - Triggered via `dosh -p <cmd>` or `python -m webresearch`           │
└────────────────────────────────────────────────────────────────────────┘
```

### Switching Lanes via CLI Flags

- **`dosh <cmd>`** *(Default)*: Runs the official pure-Rust browser core with embedded V8.
- **`dosh -p <cmd>`** *(or `--python`)*: Invokes the Python companion SDK (`webresearch`).
- **`dosh -d <cmd>`** *(or `--rust`)*: Explicitly forces pure-Rust execution.

```bash
# Read a page using the high-performance pure-Rust pipeline
dosh read https://news.ycombinator.com

# Or read via the Python companion SDK
dosh -p read https://news.ycombinator.com
```

---

## CLI Usage

```bash
dosh — Agent-native browser (pure Rust core, doshgentv8 V8 engine)
Usage: dosh [-p|--python] [-d|--rust] <command> [args...]

Commands:
  read <url> [--json]       Read and project an LLM-ready page model
  search <query> [--json]   Web search with DuckDuckGo fallback
  download <url>            Download file or PDF streaming to disk with SHA-256
  ask <prompt>              Let an agent research and answer using browser tools
  shell [-c <command>]      Agent browser terminal (41 commands)
  server / serve [--port N] Run REST HTTP API & tool invoke endpoints
  mcp                       Run Model Context Protocol server over stdio
  product / status          Show official lane and engine status
```

### Examples

```bash
# 1. Project an LLM-ready reading document (ACD 2.0 with semantic block tags)
dosh read https://en.wikipedia.org/wiki/Rust_(programming_language)

# 2. Autonomous search with waterfall fallback
dosh search "latest AI agent frameworks 2026"

# 3. Stream a PDF or dataset directly to disk with SHA-256 verification
dosh download https://arxiv.org/pdf/2301.00001.pdf

# 4. Multi-step autonomous research loop
dosh ask "Look up the pricing of Cloudflare Workers and summarize tier limits"

# 5. Start the REST API server
dosh serve --port 4747

# 6. Check engine status
dosh status
```

---

## Model Context Protocol (MCP) Setup

DOSH natively supports the **Model Context Protocol (MCP)** over stdio, allowing any AI assistant (Claude Desktop, Cursor, Antigravity) to browse the web autonomously with sub-millisecond control.

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
| `browser_shell` | Execute any of the 41 terminal commands directly. |

---

## DoshShell: 41 Terminal Commands

Run `dosh shell` for an interactive agent terminal or batch commands with `dosh shell -c "<cmd>"`:

```bash
# Set WASM anti-tarpit fuel limit and open page
dosh shell -c "wasm fuel=50000" -c "open https://example.com" -c "actions"
```

| Category | Commands |
| :--- | :--- |
| **Navigation & DOM** | `open`, `read`, `actions`, `act`, `ref`, `wait`, `tabs`, `frames`, `layers`, `screenshot` |
| **V8 Engine Controls** | `wasm` (fuel traps), `clock` (virtual time), `rng` (seeded PRNG), `heap` (128MB limit), `gate` (capabilities) |
| **Network & Security** | `net.rule` (mock/block), `net.ledger` (causal trace), `net.apis` (API discovery), `cookies`, `video` |
| **State & Checkpoints** | `checkpoint` (durable SQLite save), `restore` (rehydrate session), `storage`, `db`, `history` |
| **Evidence & Diff** | `snapshot`, `diff`, `trace` (causal action footprint), `watch` (mutation observer) |
| **Scripting & Automation**| `eval`, `frame.eval`, `skill.save`, `skill.run`, `skill.list`, `skill.delete`, `skill.update`, `caps`, `product` |

---

## Python Companion SDK

For Python agents using LangChain, LlamaIndex, or AutoGen:

```python
import asyncio
from webresearch import Session

async def main():
    async with Session() as session:
        # Navigate and execute scripts
        page = await session.goto("https://news.ycombinator.com")
        print(f"Title: {page.title}")
        print(f"Content:\n{page.view}")

        # List reachable actions
        actions = session.actions()
        print(f"Reachable controls: {len(actions)}")

        # Click first link
        await session.click("e1")

asyncio.run(main())
```

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

---

## DevOps & Release Workflow

### Automated CI/CD
- **Continuous Integration (`.github/workflows/ci.yml`)**: Runs Rust workspace tests (`cargo test --workspace`) and the full Python test suite (`uv run pytest`) on every push to `main`.
- **Automated Releases (`.github/workflows/release.yml`)**: Triggers on tag pushes (`v*.*.*`), cross-compiles release binaries for macOS (ARM64, x86_64) and Linux (x86_64), generates SHA-256 checksums, and publishes assets directly to GitHub Releases.

### Creating a Production Release
To ship a new version:
```bash
git tag v1.0.0
git push origin v1.0.0
```
GitHub Actions will automatically build, package, and publish the release binaries.

---

## License

Licensed under the Apache License, Version 2.0 ([LICENSE](LICENSE)).
