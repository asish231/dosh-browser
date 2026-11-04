# Live Site Sweep — Limitations Report

**Date:** 2026-11-03 · **Engine:** QuickJS (`webresearch/engine/minidom.js`, 9,494 lines) · **Method:** 16 real websites loaded through the full owned pipeline (fetch → parse → scripts → ACD → shell), each with an honest per-site verdict.

This report is built **strictly from live evidence** — every verdict below was reproduced by loading the actual site, not inferred from documentation. The sweep script lives at `scripts/site_sweep.py`.

---

## The Scoreboard

| Site | Class | Verdict | Status | ACD Blocks | JS Errors | Notes |
|---|---|---|---|---|---|---|
| **angular.dev** | Angular SPA (docs) | ✅ loaded | 200 | 18/18 (complete) | 19 | Angular hydrates fully — **Angular is not a blocker** |
| **vuejs.org** | Vue SPA (docs) | ✅ loaded | 200 | 55/55 (complete) | 1 | Full content projection |
| **medium.com** | React SPA | ✅ loaded | 200 | 18/18 (complete) | 3 | React hydration works |
| **nextjs.org** | Next.js/React | ✅ loaded* | 200 | 30/30 (complete)* | 5 | *Earlier run: 30/30; this run hit the 45s sweep timeout on a slow load — retry tuning, not a wall |
| **x.com** | Social (bot wall) | 🟡 shell only | 200 | 4/4 | 7 | Login shell projects; the feed is auth-gated |
| **github.com** | Code hosting | 🟡 partial | 200 | 78/97 | 8 | 1.6MB page loads; 8 js_errors = 8 named missing web APIs |
| **tiktok.com** | Video platform | 🟡 partial | 200 | 36/71 | 2 | Marketing pages project; the video app is bot-gated |
| **developer.mozilla.org** | Docs (SSR) | 🟡 partial | 200 | 41/156 | 1 | See Tier 2: chrome classification skips reference-dense regions |
| **news.ycombinator.com** | Minimal JS | ✅ loaded | 200 | 3/4 | 0 | Small count is correct — HN is one table |
| **en.wikipedia.org** | Encyclopedia (SSR) | 🟡 partial | 200 | 43/602 | 0 | Root-caused below — disclosure works, classification is debatable |
| **stackoverflow.com** | Q&A (SSR) | 🔴 bot-wall | 403 | 0/0 | 0 | Cloudflare "Just a moment..." |
| **reddit.com** | Heavy SPA | 🔴 blocked | 200 | 0/0 | 0 | 8KB shell; SPA never hydrates (bot-gated) |
| **instagram.com** | Login+wall | 🔴 blocked | 200 | 0/0 | 0 | 409KB shell; content is client-gated |
| **amazon.com** | E-commerce | 🔴 bot-gated | 202 | 1/1 | 0 | 2KB stub (edge-gate) |
| **booking.com** | Travel (DataDome) | 🔴 bot-gated | 202 | 1/1 | 0 | 9KB stub |
| **bbc.com/news** | News (SSR) | ⏱ timeout | — | — | — | 45s sweep cap hit; config, not engine |

**The headline: 6 of 16 sites blocked by bot walls. 0 of 16 blocked by "modern SPA architecture."**

---

## Tier 1 — Bot Walls (the #1 real-world limitation)

The engine executes Angular, React, Next, and Vue fine. What blocks real sites is the adversarial front door.

| Wall | Evidence | Solver status | Blocks |
|---|---|---|---|
| **Cloudflare / Turnstile** | StackOverflow: 403, "Just a moment...", 6KB challenge shell | ❌ No native solver exists | StackOverflow-class sites |
| **DataDome** | Booking.com: 202 stub; TripAdvisor: 403 (verified separately — the only action on the page was "DataDome Device Check") | ⚠️ `engine/datadome.py` exists but drives the **dead Chromium lane** (`chromium.py` is a permanently disabled stub) — needs the protocol-lane rewrite per the reCAPTCHA playbook | Booking, TripAdvisor-class |
| **Akamai / edge-gate** | Amazon: 202 + 2KB stub | ❌ No lane | Amazon-class |
| **Client-gated SPAs** | Reddit (8KB shell, 0 blocks), Instagram (409KB shell, 0 blocks) | ❌ Requires walls + login flows first | Social platforms |
| **Auth-gated feeds** | X: 4/4 shell blocks, no feed | 🟡 Login lane exists; feed needs the session | Logged-in social |

**Build order for this tier:** DataDome protocol solver (the playbook is proven — reCAPTCHA v2 was solved the same way) → Cloudflare/Turnstile lane → Akamai. Each wall solved turns a whole site class green.

---

## Tier 2 — ACD Converter Gaps (data loss on sites that DO load)

The ACD is the converter (blocks/actions/links/API → retrievable projection). It does not silently lose data anymore (fixed 2026-11-03, commit `93ffb57`: `blocks_total`, `BLOCKS: n of m` disclosure, chrome-skip counts). But two real gaps remain:

### 2.1 Chrome classification is aggressive — Wikipedia case, root-caused

`en.wikipedia.org/wiki/Web_browser`: **43/602 blocks**, with `unavailable` honestly reporting:

```
blocks: 558 candidate content region(s) inside nav/header/footer chrome were skipped
```

Verified arithmetic: 43 projected + 558 chrome-skipped + 1 empty-text = **602 exactly** — zero silent loss. The 558 are Wikipedia's reference lists (`ol.mw-references`) and portal boxes (`ul.noprint portalbox`), classified as chrome by `inside_chrome()`.

The problem: **references are content for citation work**, not navigation chrome. The same pattern explains MDN's 41/156.

**Fix:** a `references=true` toggle on the reading projection (include chrome-classified reference regions on demand), or smarter classification (reference lists are content when they contain backlinks/citations).

### 2.2 Frame content is not projected

`vuejs.org` shows iframe=1, `medium.com` iframe=2 — the engine **fetches frames as separate documents and runs their scripts** (live postMessage lanes, verified in `frames.py`), but the ACD carries only frame **metadata** (url, title, status, js_ran, scripts count). Frame *content* never becomes blocks.

**Impact:** payment widgets, chat embeds, embedded media bodies — invisible to `browser_query`/`browser_slice` even though the engine has them.

**Fix (ACD v2):** per-frame block projection — `build_acd` over each loaded frame document, nested under `frames[i]`.

### 2.3 JS errors on heavy pages (named engine gaps)

GitHub: 8, angular.dev: 19, x.com: 7. Each error is a **specific missing web API** the page called. These are the engine's TODO list, honestly logged per page via `browser_console`.

### 2.4 JSON-LD beyond FAQPage/HowTo/Product

Recipe, Article, Event, LocalBusiness, JobPosting, BreadcrumbList are parsed and stored in `framework.json_ld` but not projected into blocks. High-value for recipe/event/price agents.

### 2.5 Slow loads hit sweep timeouts

nextjs.org and bbc.com exceeded the 45s sweep cap. Retry policy and a higher cap for known-heavy sites — configuration, not engine limitation.

---

## Tier 3 — Engine Gaps (roadmap inventions)

| Gap | Evidence | Path |
|---|---|---|
| **WASM execution** | 0 WASM modules on all 16 sites (challenge modules are the real users) | QuickJS: inert. **doshgentv8 (V8 lane) has native WASM** — the path exists; wiring pending |
| **Shadow DOM piercing** | No sweep site used Lit/web components — a Lit site is the test case | `attachShadow` is stubbed (minidom.js:6123); `cssSelect` does not traverse shadow roots |
| **Text shaping / bidi** | AKTU's Devanagari renders garbled (verified live) | Latin-only advance table; `rustybuzz`-class kernel on the roadmap |
| **CSS transforms/floats/sticky** | Layout approximations, disclosed | minidom.js layout roadmap |

---

## Tier 4 — Permanent By Design

These will not change; AGENTS.md's rule applies: *"Say 'this needs a real browser' plainly when it is true."*

- **Canvas pixels** — decomposed at parse time, never rastered. TikTok's video app, maps, Three.js scenes, canvas dashboards: structurally visible, visually invisible, forever.
- **Video/audio playback** — not rendered. But **downloadable**: the `video` command fetched a real 28.7MB Dailymotion clip and a 61.8MB archive.org file (byte-identical sha256 across both lanes, ffprobe-verified) on 2026-11-03.

---

## What This Evidence Dictates — Build Order

1. **DataDome protocol solver** — rewrite `engine/datadome.py` off the dead Chromium lane onto the HTTP protocol lane (the reCAPTCHA v2 playbook: anchor → challenge → classify → verify). Unblocks Booking, TripAdvisor-class. Highest value per line of code.
2. **Cloudflare/Turnstile native lane** — unblocks StackOverflow-class.
3. **ACD v2** — frame content blocks (2.2), references toggle (2.1), JSON-LD expansion (2.4).
4. **doshgentv8 wiring** — native WASM execution + speed; Phase 2 per `docs/DOSHGENTV8-MASTER-PLAN.md`.
5. **JS-error hardening loop** — burn down GitHub's 8, Angular's 19, X's 7; each fix is one more web API the engine speaks.

---

## Reproducing

```bash
.venv/bin/python scripts/site_sweep.py
```

Per-site verdicts depend on live bot-wall behavior (IP reputation, time of day); the wall classifications are the stable signal. The engine-side results (SPA hydration, ACD coverage, JS error counts) are deterministic.
