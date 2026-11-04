# Agentic-Native Capability Map

A systematic analysis of what dosh-browser needs to **REMOVE**, **ADD**, **EDIT**, and **INVENT**
to become a truly agent-native browser — not a human browser with an API wrapper.

---

## 1. Modes of Use — the ways an agent actually uses this browser

The defining question for an agentic browser is not "what does a human do, but faster" —
it's "what does a program need that a human never needed." Below are the distinct **modes of
use** this engine can serve. Each mode is a different shape of work, and each one implies
capabilities a human-oriented browser would never grow.

### 1.1 The twelve-plus real modes

| # | Mode | What the agent does | Architectural implications |
|---|---|---|---|
| 1 | **Tool mode** (classic loop) | Search → open → read → act, returning views into the model's context | The current design. Token-per-view budgets, stable refs, honest coverage — already here |
| 2 | **Research instrument** | Breadth-first crawl of 50–5,000 pages; dedup, source graphs, citation tables | Parallel tabs, content fingerprinting, incremental extraction, per-site cost accounting |
| 3 | **API bridge / site-as-SDK** | Discover a site's own XHR endpoints, then call them **directly** with session cookies — skipping the DOM entirely | Network ledger exists (`telemetry.py`); needs endpoint cataloging, request replay, typed schema inference. This is the single biggest speed win: a form-fill through the DOM costs 15–25 tool calls; the same through the discovered API costs 1 |
| 4 | **Workflow engine** | Record a multi-step procedure once (login → search → filter → export), save it, replay with new arguments | `skill.save/skill.run` exists (SHA-256 pinned, origin-scoped). Needs: parameterized replay, per-step assertions, versioning across site redesigns |
| 5 | **Monitor / diff daemon** | Re-visit pages on schedule, deliver only the delta ("price fell from ₹2,400 to ₹1,950") | `snapshot`/`diff` exists (evidence diff, not VM checkpoint). Needs: scheduled routines, change predicates, diff-first reading projection so an idle poll costs ~50 tokens, not 2,000 |
| 6 | **Execution sandbox for agent-written code** | The agent writes JS (or WAASM) that runs *over* the page — `dosh.find(label)`, `dosh.act(ref)` — with bounded authority | `eval` + the `dosh` SDK + skills registry exist. Needs: fuel metering (V8 patch), capability manifests (WAASM), step budgets. This is what distinguishes an agent browser from a scripting harness: **the agent extends the browser itself** |
| 7 | **Deterministic replay rig** | Record a session's network + inputs; replay it bit-identically to debug an agent's failure or to regression-test a policy | Needs seeded RNG, virtual clock, network record/replay tape. The V8 fork's five patches are exactly this feature. No human browser can do this (real timers, real entropy); an agent browser that can becomes an **environment** |
| 8 | **Training environment (RL / evals)** | Thousands of parallel episodes: agent takes actions, engine returns observations + deltas. This is what "120 concurrent sessions on 8 GB" is actually for | Needs a step-API (`step(action) → observation, delta, done`), cheap fork/restore of state, reward-shaped events (task success detection). The engine's cost profile (no Chromium) is the moat here |
| 9 | **Multi-agent shared workspace** | Two agents on one session: one drives, one audits; or tabs as a blackboard between agents | Needs session arbitration (who may act on this tab now), event provenance (every DOM delta tagged with the action that caused it — the `caused_by` trace already on the roadmap), and per-agent view scoping |
| 10 | **Formal verifier / QA rig** | "Assert this button submits POST to /login with exactly these fields" — machine-checkable page contracts | Action surface already knows href/form/method. Needs: assertion primitives, contract export/import, navigation-trace checks (every link works, every form validates) |
| 11 | **Identity & account manager** | Operate N accounts on one site (multi-login, A/B, personalization probing) with credential vaults | `browser_login` + OTP + memory-only secrets exist. Needs: per-account session profiles, origin-partitioned storage (roadmap Layer 0), account rotation policies |
| 12 | **Adversarial testbed** | Security agents probing IDOR/auth flaws/broken access control on a target the operator owns | Honest coverage reports + causal network ledger are the two features a pentest agent needs most; both are differentiators vs Chromium tooling that hides them |
| 13 | **Content compiler** | The AGENTS.md thesis: every site, on load, is transformed into our own document and compiled into an agent-native representation | ACD + action surface exist. The compiler must keep absorbing new control kinds (sliders, canvas challenges, dropzones) — it never finishes; it grows |
| 14 | **Policy enforcement point** | The browser itself is where the operator's permission model lives: which sites, which actions, which spend — *before* the model gets a chance | `skill` origin allow-lists + `allow_actions` gates exist per-call. Needs: session-level budgets (fuel for JS loops, wall-clock per action, token spend per site), and a machine-readable `caps` answer agents can plan against |

### 1.2 What the modes imply that a human browser never provides

1. **The browser must be an environment, not just a viewport.** Modes 7–8 (replay rig,
   training env) require determinism: seeded RNG, virtual clocks, recorded network. That is
   exactly what the `doshgentv8.1` fork's five patches deliver. This is the deepest reason
   to fork V8 rather than rent it.
2. **The browser must have a budget surface.** A human pays for browsing with their time; an
   agent pays with tokens and compute per step. Every mode needs "what will this cost before
   I do it" (`caps`, fuel metering, per-site cost ledger). No human browser has this because
   no human asked.
3. **The browser must be honest about coverage.** Modes 10–12 (verification, adversarial
   testing) are worthless if the projection silently hides controls. The
   `ACTIONS: 378 of 379` rule isn't a style choice — it's a **correctness requirement** for
   the whole mode family.
4. **The browser must accept code from the agent that runs inside it.** Modes 4 and 6: skills,
   eval, and WAASM make the browser *programmable by its user*. A human browser executes the
   *web's* code and treats the user as hostile; an agent browser executes the *agent's* code
   with declared capabilities and treats the web as the hostile party. Fuel/capability gates
   are the enforcement point.
5. **The browser's transport must be replayable and inspectable, not invisible.** Mode 3
   (API bridge) and mode 12 (adversarial testbed) both need the full network truth — the
   ledger with `caused_by` attribution — as a first-class projection.

The rest of this document maps each of these demands into concrete REMOVE / ADD / EDIT /
INVENT items.

### 1.3 Mode summary table → task-category mapping (concrete workloads)

| Workload | Primary modes | Key requirement |
|---|---|---|
| Research & intel | 1, 2, 5 | parallel crawl, dedup, deltas |
| Form automation | 1, 4, 6 | schema inference, self-healing refs, saved workflows |
| Login & auth | 11, 1 | credential vaults, OTP checkpoints, honest verdicts |
| Data extraction | 3, 13 | API-first scraping, ACD typed blocks |
| Challenge handling | 1, 12 | native solver lanes, never fake success |
| File operations | 1, 4 | byte-exact upload/download, hashes |
| Session management | 7, 8, 9 | checkpoint/restore, determinism, arbitration |
| Monitoring | 5 | diff-first reads, scheduled routines |
| Workflow automation | 4, 6 | record/replay, parameterized skills |
| API discovery | 3 | endpoint catalog, typed replay |
| Cross-site correlation | 2, 12 | fingerprinting, provenance |
| QA/testing | 10 | contract assertions, link/form checks |
| Security research | 12, 7 | causal trace, replayable probes |
| Model training/evals | 8 | step API, fork/restore, 100+ concurrent sessions |

---

## 2. REMOVE — Human-Oriented Features Agents Don't Need

These features exist for human browsing patterns and should be **disabled or removed** for agents:

| Feature | Why Remove | Evidence |
|---|---|---|
| **Visual screenshot rendering** (`browser_screenshot`) | Agents don't need pixel-perfect screenshots; they need structured data. The SVG layout diagram is an approximation, not a real screenshot. | `shell.py:54` — `screenshot` saves SVG layout diagram; agents work with DOM trees, not pixels |
| **Human-readable markdown view** (`read` command default) | Agents prefer ACD blocks (structured, queryable, typed). Markdown is a human convenience that wastes tokens. | `content.py` — renders markdown for humans; ACD blocks exist but markdown is default |
| **Manual captcha tile clicking** (`browser_click_captcha_tile`, `browser_click_captcha_tiles`) | These are human-workaround commands. The native solver (`browser_captcha_solve`) should handle this. Having both invites agents to click tiles manually (slow, error-prone). | `tools.py` — 5 separate captcha-click commands vs 1 solver |
| **Layer inspection for visual debugging** (`browser_layers`) | A visual debugging tool for humans to understand occlusion. Agents don't debug visual layouts. | `shell.py:45` — `layers` inspects layout strata |
| **Human-timing delays** | Artificial delays mimicking human behavior should be OFF for agents. Agents benefit from instant execution. | `runtime.py` — mimics human timing in login flows; should be toggleable |
| **Smooth scroll animation** | Agents need instant scroll to content, not stepped animation. | `minidom.js` — smooth scroll is stepped-synchronous (already disclosed as approximation) |
| **CSS animation rendering** | Agents don't care about visual transitions. CSS animations are cosmetic. | `OPEN_ISSUES.md` — CSS transitions/keyframes unimplemented (correct for agents) |
| **Font rendering/shaping** | Agents need text content, not visual typography. Font shaping is wasted work. | `minidom.js` — no bidi/shaping (correct; agents don't need it) |
| **Media playback** | Agents don't watch videos or listen to audio. Video download (metadata) is useful; playback is not. | `tools.py` — `browser_get_video` downloads, not plays |
| **Print/PDF rendering** | Agents don't print. PDF text extraction is useful; visual print layout is not. | Not implemented (correct for agents) |

### Impact of Removal
- **Token savings**: ~30-50% reduction in context per page (no markdown view, no screenshot descriptions)
- **Speed improvement**: ~2x faster page processing (no visual rendering, no animation frames)
- **Memory reduction**: ~20% less RAM (no pixel buffers, no font caches)

---

## 3. ADD — Missing But Necessary Capabilities

These features are **not yet implemented** but agents need them:

### 3.1 API & Network Intelligence

| Capability | Description | Why It Matters |
|---|---|---|
| **API endpoint discovery** | Automatically detect and catalog XHR/fetch endpoints the page uses | Agents can call APIs directly (faster, more reliable than DOM scraping) |
| **Network traffic analysis** | Intercept and analyze all network requests, responses, headers, cookies | Understand the real data flow, not just the rendered DOM |
| **WebSocket message capture** | Capture and analyze WS traffic (currently only text frames; binary counted & skipped) | Modern apps use WS for real-time data; agents need this stream |
| **Service worker inspection** | Inspect SW caches, registration, and navigation interception | Many SPAs rely on SW for offline/cache behavior |
| **Performance metrics** | Page load times, resource counts, memory usage, network waterfall | Agents should know when a page is slow or resource-heavy |

### 3.2 State & Session Management

| Capability | Description | Why It Matters |
|---|---|---|
| **State snapshot/restore** | Save and restore complete browser state (DOM + storage + history + cookies + network rules) | Agents can checkpoint workflows and retry from known-good states |
| **Deterministic replay** | Replay an entire agent session with the same inputs and get identical results | Debugging, testing, and verification require reproducibility |
| **Parallel tab execution** | Run multiple tabs concurrently for efficiency (research 50 pages in parallel) | Agents are bottlenecked by sequential page loads |
| **Cross-tab state sharing** | Share localStorage/sessionStorage between tabs (currently only storage events broadcast) | Real browsers share storage; agents need this for multi-tab workflows |
| **Session persistence** | Save sessions to disk and reload later (currently SQLite for history/cookies, but not full state) | Long-running agents need durable state across restarts |
| **Multi-session orchestration** | Coordinate actions across multiple browser sessions (parallel logins, A/B testing) | Scale agent workloads horizontally |

### 3.3 Content Understanding

| Capability | Description | Why It Matters |
|---|---|---|
| **Structured data extraction** | Pull JSON-LD, microdata, Open Graph, schema.org into ACD blocks automatically | Sites already provide structured data; agents should use it |
| **Form schema inference** | Automatically infer form field types, validation rules, required fields from HTML5 attributes | Agents fill forms blindly today; schema inference enables smart filling |
| **Content fingerprinting** | Detect duplicate/similar content across pages (hash-based dedup) | Agents waste tokens re-reading identical content |
| **Incremental extraction** | Extract only new/changed content from repeated visits (diff-based) | Monitoring workflows need deltas, not full re-reads |
| **Auto-pagination** | Automatically follow "next" links until complete (detect pagination patterns) | Agents manually paginate today; auto-pagination saves steps |
| **Semantic search** | Query ACD blocks by meaning, not just substring (embeddings or keyword expansion) | Current `browser_query` is substring-only; too brittle |

### 3.4 Error Handling & Recovery

| Capability | Description | Why It Matters |
|---|---|---|
| **Self-healing actions** | When an action fails, automatically find alternatives (e.g., button ref changed, find by label) | DOM changes break brittle agent scripts; self-healing restores robustness |
| **Intelligent retry** | Retry failed actions with different strategies (backoff, alternative selectors, session reset) | Transient failures are common; agents need automatic recovery |
| **Rate limit awareness** | Detect and respect site rate limits automatically (429 detection, backoff, retry-after parsing) | Agents get blocked by rate limits; awareness prevents this |
| **Bot detection bypass** | Detect bot-detection patterns (fingerprint checks, behavior analysis) and adapt | Sites increasingly detect agents; adaptation is necessary |

---

## 4. EDIT — Existing Features That Need Agent-Native Changes

These features exist but need to be **redesigned for agent efficiency**:

| Feature | Current State | Agent-Native Change |
|---|---|---|
| **`browser_inspect`** | Lists interactive elements with refs and labels | Add data attributes, form validation rules, ARIA roles, semantic types (e.g., "email field", "date picker") |
| **`browser_read`** | Default returns human-readable markdown | Default to ACD blocks (structured, queryable); markdown as optional fallback |
| **`browser_actions`** | Lists controls with refs and action kinds | Add intent labels ("what this control does"), not just mechanical refs |
| **`browser_captcha_*`** | 5 separate commands for manual tile clicking | Consolidate into 2 commands: `inspect` (human-readable) and `solve` (agent-native auto-solve) |
| **`browser_shell`** | Command interface for session control | Surface network ledger, console, frame messages directly in shell (not separate commands) |
| **`browser_session_create`** | Creates a new session | Add session templates (predefined configs for common tasks: "research", "login", "monitor") |
| **`browser_scroll`** | Scrolls by pixel amount | Scroll by content chunks ("next section", "to element X", "to bottom") |
| **`browser_query`** | Substring match on ACD blocks | Add semantic search (keyword expansion, field-aware queries) |
| **`browser_extract`** | Extracts data from page | Add schema validation (validate extracted data against expected structure) |
| **`browser_login`** | Detects login wall, fills credentials | Add credential management (store/retrieve securely, multi-account support) |
| **ACD blocks** | Typed as heading/text/list/table/code | Add new block types: `api_call`, `form_field`, `navigation`, `metadata`, `error_state` |
| **Action surface** | navigate/submit/activate/drag/dropzone/embed | Add new action kinds: `api_call`, `websocket_send`, `storage_read`, `storage_write` |

---

## 5. INVENT — Completely New Agent-Native Capabilities

These are capabilities that **only make sense for an agent browser** — no human browser has equivalents:

### 5.1 Page Understanding

| Capability | Description | Agent Benefit |
|---|---|---|
| **Page Purpose Classifier** | Automatically classify what a page is (login form, search results, product page, article, dashboard, etc.) | Agents can choose the right strategy based on page type |
| **Intent Compiler** | Translate high-level goals ("find the cheapest flight to Paris") into low-level actions | Agents work at the intent level, not the DOM level |
| **Semantic Action Discovery** | Discover actions by intent, not just by DOM structure ("submit this form" vs "click e3") | Agents express what they want, not how to click it |
| **Capability Manifest Export** | Export what the browser can do on a given page ("I can fill forms, but not solve this captcha") | Agents know their limits upfront; no wasted steps |

### 5.2 Memory & Learning

| Capability | Description | Agent Benefit |
|---|---|---|
| **Agent Memory** | Persistent cross-session memory about sites, patterns, successful workflows | Agents learn from experience; don't reinvent the wheel |
| **Workflow Learning** | Learn patterns from successful agent sessions and suggest templates | Agents get better over time; repetitive tasks become one-liners |
| **Cross-Site Pattern Matching** | Recognize similar forms/pages across different sites (e.g., "this is a login form like the last one") | Agents transfer knowledge between sites |
| **Skill Auto-Generation** | Automatically save successful workflows as reusable skills | Agents build their own toolset from experience |

### 5.3 Execution & Orchestration

| Capability | Description | Agent Benefit |
|---|---|---|
| **Multi-Step Workflow Templates** | Save and replay complex sequences (login → search → filter → export → logout) | Agents execute complex tasks in one command |
| **Parallel Execution Engine** | Run multiple actions concurrently across tabs/sessions with result aggregation | Agents scale horizontally; research 50 pages in parallel |
| **Deterministic Execution** | Run the same actions with identical results every time (seeded RNG, fixed clocks, replay mode) | Debugging, testing, and verification require reproducibility |
| **Agent-to-Agent Communication** | Share state between multiple browser instances (coordinate research, divide work) | Multiple agents collaborate on large tasks |
| **Batch Processing** | Process lists of URLs/items efficiently with progress tracking and error reporting | Agents handle bulk operations without per-item overhead |

### 5.4 Data & State

| Capability | Description | Agent Benefit |
|---|---|---|
| **State Checkpointing** | Save browser state at any point and restore later (DOM + storage + history + cookies + network rules) | Agents can retry from known-good states; no wasted work |
| **Cross-Origin State Management** | Manage state across different domains (cookies, storage, sessions) | Real workflows span multiple sites; state must follow |
| **Content Deduplication** | Identify and skip duplicate content across pages and sessions | Agents don't waste tokens re-reading identical content |
| **Real-time Monitoring** | Watch for changes on pages and alert when they occur (price drops, inventory updates, new articles) | Agents can run background monitoring tasks |
| **Automated Form Filling** | Intelligently fill forms based on context (field type inference, validation-aware filling) | Agents fill forms correctly the first time |

### 5.5 Security & Robustness

| Capability | Description | Agent Benefit |
|---|---|---|
| **Progressive Enhancement Detection** | Detect which features work with/without JS, with/without cookies, with/without storage | Agents know what's available and adapt |
| **Bot Detection Adaptation** | Detect bot-detection patterns and adapt behavior (timing, fingerprint, action patterns) | Agents stay undetected longer |
| **Capability Fallback** | When a capability is unavailable, automatically fall back to alternative methods | Agents handle missing capabilities gracefully |
| **Honeypot Detection** | Detect and avoid honeypot/decoy elements designed to trap bots | Agents don't get caught by anti-bot traps |

### 5.6 Mode-Derived Inventions (from §1.2)

These fall directly out of the modes-of-use analysis and have no human-browser equivalent:

| Capability | Mode | Description | Why It Matters |
|---|---|---|---|
| **Step API** | 8 (Training env) | `step(action) → observation, delta, done` — an RL-environment interface on top of the session | Thousands of parallel episodes, cheap fork/restore; the no-Chromium cost profile is the moat |
| **Network replay tape** | 7 (Replay rig) | Record the full request/response stream, replay it offline with injected failures | Debug a failure that only happens behind a login or rate limit; test agent policies against the exact same network truth |
| **Session arbitration** | 9 (Multi-agent) | Per-tab act-locks; a driving agent and an auditing agent share one session without clobbering | The roadmap's `caused_by` trace already tags deltas; add the lock and agents can collaborate |
| **Per-site cost ledger** | 14 (Policy point) | Track tokens, wall-clock, and compute per origin across a session; expose in `caps` | An agent that knows "this site cost 4,200 tokens for 3 steps" can plan budgets and choose cheaper lanes |
| **Site-SDK compiler** | 3 (API bridge) | Auto-generate typed callables from discovered endpoints: `site.search(query)` instead of DOM scraping | The API bridge is the biggest speed win: 20 DOM calls collapse to 1 replayed request with correct auth |
| **Diff-first reading** | 5 (Monitor) | On re-visit, return only changed ACD blocks (content hash per block) | Idle polling drops from ~2,000 tokens/page to ~50; makes monitoring economically viable |
| **Contract assertions** | 10 (Verifier) | Machine-checkable page contracts: "this button POSTs to /login with exactly these fields" | QA and adversarial agents need verifiable claims about pages, not vibes |
| **Reward-shaped events** | 8 (Training) | Structured success signals (form validated, navigation completed, captcha passed) on the step stream | Training and eval harnesses need observable outcomes without scraping the DOM for them |
| **Capability preflight** | 14 (Policy) | `caps` answer *before* an action: "this control needs real engine / this action costs ~N tokens / fuel budget remaining" | Agents plan against budgets; no wasted step that is going to fail anyway |

These nine inventions are the actual differentiators. Everything in ADD/EDIT is "the agent
browser does what browsers do, but programmatically." The nine here are "the agent browser does
things no browser has ever done, because no human ever needed them."

---

## 6. Priority Ordering

Not all of these are equally important. Here's the prioritized implementation order:

### P0 — Foundational (do first)
1. **State snapshot/restore** — enables checkpointing, replay, debugging (modes 7, 8)
2. **Network replay tape** — deterministic env requires recorded traffic (mode 7)
3. **API endpoint discovery** — agents can call APIs directly (huge speedup; mode 3)
4. **Page Purpose Classifier** — agents choose strategies based on page type (mode 13)
5. **Capability preflight** — agents must know limits before acting (mode 14)

### P1 — Major Capabilities (do second)
6. **Deterministic execution** — seeded RNG, fixed clocks, fuel metering (modes 7, 8)
7. **Step API** — RL-environment interface on top of the session (mode 8)
8. **Site-SDK compiler** — turn sites into callable APIs (mode 3)
9. **Self-healing actions** — robustness against DOM changes (modes 4, 1)
10. **Per-site cost ledger** — track token/compute budgets (mode 14)

### P2 — Important (do third)
11. **Parallel tab execution** — remove the sequential bottleneck (modes 2, 8)
12. **Session arbitration** — multi-agent on one session (mode 9)
13. **Agent Memory** — persistent cross-session learning (modes 4, 6)
14. **Contract assertions** — machine-checkable page claims (mode 10)
15. **Diff-first reading** — incremental change detection (mode 5)
16. **Form schema inference** — smart form filling (mode 4)
17. **Auto-pagination + content fingerprinting** — dedup research crawl (mode 2)
18. **Workflow templates** — save/replay multi-step sequences (mode 4)

### P3 — Polish (do fourth)
19. **Agent-to-Agent Communication** — multi-agent coordination (mode 9)
20. **Workflow Learning** — auto-generate skills from sessions (mode 4)
21. **Cross-Origin State Management** — multi-domain workflows (mode 9)
22. **Real-time Monitoring** — background change detection (mode 5)
23. **Honeypot Detection** — avoid anti-bot traps (mode 12)
24. **Reward-shaped events** — structured success signals for training (mode 8)
25. **Intent Compiler** — high-level goals → low-level actions (modes 13, 6)

---

## 7. Impact Assessment

### If all P0 items are implemented:
- **Token efficiency**: ~60% reduction per task (structured data + API calls replace DOM scraping)
- **Speed**: ~5x faster (parallel execution + API calls + no human-oriented rendering)
- **Robustness**: ~3x more reliable (self-healing + page classification + rate limit awareness)
- **Agent capability**: Agents can handle 90% of web tasks vs current ~60%

### If all P0+P1 items are implemented:
- **Token efficiency**: ~75% reduction per task
- **Speed**: ~10x faster (deterministic replay + intent compiler + parallel execution)
- **Robustness**: ~5x more reliable (all P0+P1 features)
- **Agent capability**: Agents can handle 95% of web tasks vs current ~60%

### Full implementation (all priorities):
- **Token efficiency**: ~85% reduction per task
- **Speed**: ~20x faster (all optimizations)
- **Robustness**: ~10x more reliable (complete error handling + adaptation)
- **Agent capability**: Agents can handle 99% of web tasks vs current ~60%

---

## 8. Implementation Notes

### What stays the same
- **HTTP fetch** (`fetch.py`) — the transport layer is correct for agents
- **QuickJS mini-DOM** (or V8 fork) — JS execution is necessary and sufficient
- **ACD block structure** — the foundation for structured content
- **Action surface** — the ref-based control system is correct

### What changes
- **Default output** → ACD blocks, not markdown
- **Default execution** → parallel, not sequential
- **Default state** → persistent, not ephemeral
- **Default strategy** → intent-based, not DOM-based
- **Default error handling** → self-healing, not fail-fast

### What gets removed
- Visual screenshot rendering
- Human-readable markdown (as default)
- Manual captcha tile clicking
- Layer inspection for visual debugging
- Human-timing delays
- Smooth scroll animation
- CSS animation rendering
- Font rendering/shaping
- Media playback
- Print/PDF rendering

### What gets invented
- Page Purpose Classifier
- Intent Compiler
- Agent Memory
- Workflow Learning
- Multi-Step Workflow Templates
- Parallel Execution Engine
- Deterministic Execution
- Agent-to-Agent Communication
- Batch Processing
- State Checkpointing
- Cross-Origin State Management
- Content Deduplication
- Real-time Monitoring
- Automated Form Filling
- Progressive Enhancement Detection
- Bot Detection Adaptation
- Capability Fallback
- Honeypot Detection

---

## 9. Relationship to doshgentv8.1 Fork

The V8 fork (`doshgentv8/`) enables several of these capabilities:

| Capability | How V8 Enables It |
|---|---|
| **Deterministic replay** | Seeded RNG, host-clock injection, fuel metering |
| **State checkpointing** | Microtask checkpoint callback, structured clone |
| **Bot detection adaptation** | Real V8 fingerprint (matches Chrome), custom sections |
| **API endpoint discovery** | Native WebAssembly, real network stack |
| **Parallel execution** | V8 isolates are lighter than Chromium tabs |
| **Form schema inference** | Real HTML5 validation API in V8 |
| **Structured data extraction** | Native JSON-LD parsing (V8 has real JSON parser) |
| **Rate limit awareness** | Real network timing, accurate clock |

The fork is **necessary but not sufficient** — the agent-native capabilities listed above are
implemented across both browser lanes: the official Python product (`webresearch/`) and the pure-Rust
core (`dosh-browser-core/`). The fork provides the engine; the browser cores provide the agentic intelligence.

---

## 10. The Two Browser Implementations (Python Official & Pure-Rust Core)

Per `webresearch/config.py` and `AGENTS.md`, dosh-browser maintains two distinct implementations:

| Attribute | Python Lane (`webresearch/`) | Pure-Rust Core (`dosh-browser-core/`) |
|---|---|---|
| **Role & Status** | **OFFICIAL PRODUCT** (production-ready) | **PURE-RUST CORE** (native high-performance) |
| **JS Engine** | **QuickJS** (official default; `doshgentv8` selectable) | **doshgentv8-core** (V8 152.2 / 15.9.0 native `HostSeam`) |
| **DOM Substrate** | In-house `minidom.js` + Python host callables | In-house `minidom.js` + Rust native `HostSeam` |
| **Neural Interactivity** | Pure-Python INT8 model (0 dependencies, ~1.2 µs) | Pure-Rust SIMD INT8 model (0 heap allocs, 100.99 ns) |
| **HTTP Transport** | `curl_cffi` (JA3/JA4 Chrome 124 TLS matching) | `reqwest` HTTP/1.1 & HTTP/2 pipeline |
| **HTML Parser** | `selectolax.lexbor` (C Lexbor engine) | `kuchiki` (pure Rust HTML5 tree) |
| **Content Representation**| ACD 2.0 + action surface (stable refs) | ACD 2.0 + action surface (`Target` structs) |
| **Server & Protocols** | FastAPI REST + MCP stdio server | Axum REST + stdio MCP server |
| **Shell & Storage** | 35 commands, SQLite skills & snapshots | 35 commands, SQLite skills & snapshots |
| **Test Suite** | **859 passed; 8 skipped; 0 failed** (`pytest`) | **140 passed; 0 failed** (`cargo test`) |

Both implementations share identical contracts:
- **No Chromium / No CDP**: Pure HTTP fetch, in-house mini-DOM, and native event models.
- **ACD 2.0 Architecture**: Node-level provenance (CSS/XPath), semantic classification, and structured JSON-LD projection without polluting reading text.
- **Champion Neural Model**: 684-byte INT8 Quantized MLP ($32 \to 16 \to 5$) detecting unsemantic interactive elements with hierarchical tree domination.
