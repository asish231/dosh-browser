# dosh-browser vs Lightpanda — measured head-to-head (2026-11-05)

Same tasks, each tool's official CLI path, cold process, macOS `/usr/bin/time -l`.
Browser Use and Chromium are NOT installed on this machine — they have **no numbers** here and none were invented.

## Tasks (same as the MCP benchmark matrix)

### Read: https://example.com

| metric | dosh-browser (Python lane CLI) | lightpanda `fetch --dump markdown` |
| --- | ---: | ---: |
| wall (cold) | 2.38 s | **0.65 s** |
| peak RSS | ~74–77 MB | **~29–30 MB** |
| title extracted | ✓ `TITLE: Example Domain` | ✗ h1 "Example Domain" omitted from markdown dump (body text present) |
| warm path | **135–285 ms** (persistent MCP server, measured) | not measured (`serve`/`mcp` modes exist, untested) |

### Search: "first iPhone release year" (DuckDuckGo)

| metric | dosh-browser | lightpanda `fetch` on duckduckgo.com/html |
| --- | ---: | ---: |
| wall | 1.54 s CLI / **854 ms** MCP warm | **0.79 s** |
| peak RSS | ~73 MB | **~28 MB** |
| answer present ("2007") | **✓ 14 mentions** | **✗ 0 — DDG served a bot challenge** ("Select all squares containing a duck") |

## What the numbers actually say

1. **Cold start: Lightpanda wins.** 3.7× faster (0.65 s vs 2.38 s) and ~2.5× lighter (29 MB vs 74 MB peak RSS). A compiled binary against Python + QuickJS boot — honest and expected.
2. **Warm/agentic path: dosh measured, lightpanda not.** Dosh's persistent MCP server answers re-reads in 135–285 ms. Lightpanda's persistent modes (`serve`, `mcp`) were not measured.
3. **Adversarial web: dosh won the only real contest.** DDG challenged Lightpanda's fetcher with a captcha and it got zero results; dosh's fingerprint-parity HTTP transport (JA3/JA4 Chrome 124, curl_cffi) got the actual answer. This is the project's core differentiator, and here it is measured, not claimed.
4. **Structured output: dosh returns `TITLE:` as a field; Lightpanda's markdown dump dropped the h1.**

## Known dosh defects surfaced by the same benchmarking (unchanged)

- Auto search waterfall accepts Bing's garbage results with no relevance gate (Google rate-limited → Bing returns irrelevant hits → waterfall reports success). DDG forced works. Fix: minimum query-relevance check before an engine is declared good.

## Not measured — and why

- **Browser Use, Chromium**: not installed on this machine (verified: no binary, no pip package). Measuring them requires installing Playwright + a Chromium build (~hundreds of MB). This repo's charter also forbids executing them from inside it; a black-box harness outside the repo + explicit install approval is required first.
- **Lightpanda warm path** (`serve` CDP / `mcp` server): exists, untested here.

## Run environment

dosh: `.venv/bin/python -m webresearch <cmd>` (Python lane; the live MCP server PID 24492 is this lane) · lightpanda: `~/.local/bin/lightpanda` v— (installed 2026-09-05) · macOS, `/usr/bin/time -l`, /tmp scratch files cleaned after the run.
