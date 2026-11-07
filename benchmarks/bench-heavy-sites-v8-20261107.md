# Heavy-Site Benchmark — doshgentv8 (V8) lane, 2026-11-07

## Setup

- Binary: `target/debug/dosh` (dosh-browser-core, doshgentv8 V8 engine, pointer compression on)
- MCP: opencode wired to `dosh mcp` (Rust stdio server) — QuickJS **banned** from this date (see below)
- Loom daemon (`webresearch serve --daemon`, Python) running for session persistence; V8 lane for JS
- Method: `dosh read <url>` batch, 45s wall cap per page; live MCP `browser_navigate` for spot-checks
- No login used on any site

## QuickJS ban (same date)

- `webresearch/config.py`: `JS_ENGINE_DEFAULT = "doshgentv8"`; `quickjs` registry status → `banned`; `WR_JS_ENGINE=quickjs` now raises `ValueError`
- `webresearch/engine/runtime.py::_create_context`: no QuickJS fallback — missing facade raises with build instructions
- `webresearch/shell.py` skill VM: same refusal
- Reason: the `_quickjs` C extension's time limit is incompatible with host callables ("Can not call into Python with a time limit set") and cannot interrupt CPU loops

## Facade bug found & fixed during the ban (doshgentv8/py/src/lib.rs)

- Bug: `DoshContext.set_memory_limit` swaps in a fresh `DoshEngine` (heap limits are fixed at isolate creation) when no evals ran — the swap **silently disarmed a previously-armed watchdog** (`wall_limit` reset to 0)
- Fix: facade tracks `wall_limit` and re-applies it to the new engine after the swap
- Evidence: `set_time_limit(0.5)` then `set_memory_limit(4MB)` then `while(true){}` → interrupted at 0.50s (previously hung forever)
- Note: the loaded module lives in site-packages (`doshgentv8_rust.cpython-314-darwin.so`), not the repo dir — both copies updated

## Batch results (dosh read, V8)

| Site | Time | Status | Title |
|---|---|---|---|
| reddit.com | 0.1s | 200 | Reddit |
| medium.com | 0.1s | 200 | Medium |
| docs.python.org/3/ | 0.1s | 200 | Python 3.15.2 documentation |
| amazon.com | 0.1s | **202 (bot wall)** | — |
| nvidia.com/en-us/about-nvidia/ | 0.3s | 200 | About Us: Company Leadership… \| NVIDIA |
| build.nvidia.com | 0.4s | 200 | Try NVIDIA NIM APIs |
| huggingface.co | 0.5s | 200 | Hugging Face – The AI community… |
| openrouter.ai | 0.5s | 200 | OpenRouter |
| vercel.com | 0.6s | 200 | Agentic Infrastructure - Vercel |
| youtube.com | 0.78s | 200 | (36 actions, 1 frame) |
| fireworks.ai | 1.1s | 200 | Own your specialized intelligence \| Fireworks |
| github.com | 1.0s | 200 | GitHub · Change is constant… |
| tiktok.com | 1.0s | 200 | (100 actions) |
| x.com | 1.2s | 200 | Happening now (landing) |
| figma.com | 1.2s | 200 | (100 actions) |
| news.ycombinator.com | 1.3s | 200 | Hacker News |
| stripe.com | 1.5s | 200 | Stripe \| Financial Infrastructure… |
| booking.com | 1.5s | **bot wall** | 0 blocks |
| together.ai | 1.9s | 200 | Together AI \| The AI Native Cloud |
| linear.app | 2.1s | 200 | Linear – The system for product development |
| replicate.com | 2.2s | 200 | Replicate - Run AI with an API |
| cloudflare.com | 2.4s | 200 | Cloudflare: Build for the agent era |
| sentry.io | 3.2s | 200 | Application Performance Monitoring… |
| en.wikipedia.org/wiki/Nvidia | 3.5s | 200 | Nvidia - Wikipedia |
| notion.com | 0.48s | 200 | (13 blocks, 75 actions) |
| discord.com | 0.35s | 200 | (13 blocks, 100 actions) |
| airbnb.com | 0.49s | **bot wall** | 0 blocks |
| **cnn.com** | **221s** | 200 | loads eventually (16 blocks, 100 actions) — ad-stack extreme |

### Headline deltas vs the QuickJS lane (measured this week)

| Page | QuickJS lane (Python) | V8 lane (Rust `dosh`) | Speedup |
|---|---|---|---|
| build.nvidia.com | **73s CPU, 90s+ timeout** | **0.4s** | ~180x |
| nvidia.com/en-us/about-nvidia/ | 70.5s | **0.3s** | ~235x |
| build.nvidia.com via MCP navigate | 515s (old process, no caps) | **0.402s** | ~1280x |

## Live V8 state (MCP evidence, after heavy batch)

```
heap:  heap_limit_mb: 128, gc_policy: explicit_drain, semi_space: 1MB,
       maglev_pruned: true, turbofan_pruned: true (patch 0003)
wasm:  liftoff fuel 10,000,000 remaining, memory_pages_limit: 256,
       anti_tarpit: enabled (patch 0010)
gate:  eval/network/wasm allowed, secret_reveal: false
```

## Failures & honest limits

- **Bot walls** (not engine limits): amazon.com (HTTP 202), airbnb.com, booking.com — 0 content blocks; need the challenge-solver lane, not rendering
- **cnn.com**: 221s — no per-navigation wall-clock deadline on the Rust MCP `read` path yet; the V8 watchdog exists (`set_time_limit` / `wall_limit`) but the Rust MCP server does not arm a default budget per navigation
- **No raster**: canvas/WebGL pixels are structurally decomposed, never rendered (figma landing loads; canvas content is invisible to us)
- **Approximate layout**: mini-DOM block-flow, not Blink-exact hit testing

## Test status under the QuickJS ban (V8-only)

- tests/test_quickjs_deadline.py — **7 passed** (watchdog now provably armed; memory-bomb bounded by wall-clock)
- tests/test_engine.py, test_loom.py, test_cli_thin_client.py, test_mcp_thin_client.py, test_mcp_session_persist.py — **37 passed**
- tests/test_shell_commands_tier.py, test_shell_spine.py, test_shell_audit_upgrades.py, test_mcp_layer_fixes.py — **67 passed**
- Legacy unit-test files that import the `quickjs` package directly (minidom-internals tests) were not migrated — that remains the repo's deferred Phase-2 parity work
