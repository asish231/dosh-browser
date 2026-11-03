# Agentic-Native Audit — VERIFIED (2026-11-03, second pass)

Every claim in this file was re-verified against the tree **in this pass**, not carried over
from the first draft. Three claims from the earlier draft were **wrong** and are corrected
here (see §6). Line numbers are pinned to the current tree state; `minidom.js` is under
active edit (it grew ~65 lines between passes), so anchors may drift — each entry below was
grep-confirmed at the time of writing.

---

## 0. Engine inventory (what runs JS today, exactly)

| Component | Version | Where | Verified by |
|---|---|---|---|
| Product JS engine | QuickJS (python-`quickjs`) | `runtime.py:12` `from quickjs import Context` | grep |
| Phase-0 V8 bridge (test lane) | **V8 14.4.258.23** | mini-racer wheel; `v8_lane/`, `doshgentv8/__init__.py` | `MiniRacer().v8_version` run |
| Fork source tree (what we patch) | **V8 15.9.0 (candidate)** | `doshgentv8/base/include/v8-version.h:12-16` | header read |
| rusty_v8 crate (drop-in target) | V8 15.2 line | `doshgentv8/Cargo.toml` (152.2) | subagent research, cited |
| Reference clone (read-only) | V8 15.9.0 (247 MB with .git) | `research_repos/v8/` | du |

**Correction #1 (from draft):** the smoke test did NOT run V8 15.2. mini-racer embeds
**14.4.258.23**. The "15.2" figure belongs to the rusty_v8 crate we plan to build with, not
the binary we tested with. The smoke result stands (V8 runs minidom.js, 7/8 probes green,
WASM native); only the version attribution changes.

**Version consequence for the fork:** our patches must be written against **15.9.0**
source (that's what `doshgentv8/base/` is). rusty_v8 152.2 expects a 15.2-family archive;
when our patched 15.9 static lib is built we either (a) pin to a rusty_v8 release tracking
15.9 when it lands (4-week cadence), or (b) vendor the binding generation. Decide at
fork-build time; do not patch against 15.2 headers.

---

## 1. Crypto & verification — verified state

| Primitive | State | Anchor (verified) |
|---|---|---|
| `crypto.getRandomValues` | works (Math.random fill) | `minidom.js:4709` |
| `crypto.randomUUID` | works (v4 format) | `minidom.js:4713` |
| `crypto.subtle.digest` | **REAL** — hex in, host SHA-family, ArrayBuffer out | `minidom.js:4721-4752`; host call at `:4741` |
| `host_crypto_digest` | **REAL** host callable | `runtime.py:433` def, `:452` registered |
| `subtle.importKey` | **silent stub** — resolves `{}` | `minidom.js:4753` |
| `subtle.exportKey` | **silent stub** — empty ArrayBuffer | `minidom.js:4754` |
| `subtle.encrypt` | **silent stub** — empty ArrayBuffer | `minidom.js:4755` |
| `subtle.decrypt` | **silent stub** — empty ArrayBuffer | `minidom.js:4756` |
| `subtle.sign` / `verify` | **absent** (grep: 0) | — |
| `subtle.deriveBits` / ECDH / HKDF | **absent** (grep: 0) | — |
| WebAuthn / FIDO2 / passkeys | **absent** (grep: 0) | — |
| JWK / PEM key import | **absent** | — |

**The honest-crypto problem, precisely:** a page that calls `crypto.subtle.encrypt(...)`
today gets a **successful empty result** — no error, no data. That is a false-success the
conversion protocol forbids. First fix is one line per stub: reject with
`NotSupportedError` instead of resolving empty. The real capability (INVENT) is the
NativeCrypto suite below.

### Classification

| Class | Item |
|---|---|
| UPGRADE | Error-honest stubs (`importKey/exportKey/encrypt/decrypt` → reject, never silent-empty) |
| INVENT | **NativeCrypto suite** (doshgentv8 host callables): AES-GCM, ChaCha20-Poly1305, HMAC, ECDSA P-256, Ed25519, RSA-PSS sign/verify, X25519, HKDF, PBKDF2 |
| INVENT | Key objects with `extractable:false`, `keyId` fingerprints, JWK/PEM import |
| INVENT | Per-origin **key vault** (memory-only, never persisted to page storage) |
| INVENT | WebAuthn **honest stub**: `navigator.credentials.create/get` returns `NotSupportedError` until a real lane exists — never fake attestation |

---

## 2. Video / file automation — verified state

| Feature | State | Anchor |
|---|---|---|
| Direct media download (cookies, sha256, byte cap) | real | `video.py:62-69,183-196`; cap 500 MB, timeout 600 s |
| yt-dlp lane (pinned `python -m yt_dlp`, metadata via `--print`) | real | `video.py:140-147,215-225` |
| DRM denylist (12 services) | real, pre-network | `video.py:37-51` |
| Piracy-mirror denylist | real, pre-network | `video.py:55-60` |
| `ffmpeg_available` probe | present | `video.py:265` |
| Transcript / subtitle lane (`--write-subs/--write-auto-subs`) | **absent** (grep: 0 in video.py) | — |
| Audio-only extraction | **absent** | — |
| Partial-download honesty (`partial: true` at byte cap) | **absent** — cap aborts with error string only | `video.py:193-194` |
| ffprobe duration verification of the produced file | **absent** | — |

### Classification

| Class | Item |
|---|---|
| INVENT | Transcript/caption lane (`--write-auto-subs` → parse → transcript record with time ranges) |
| INVENT | Audio-only format selector (`-f ba`) |
| INVENT | `video sweep` — queue N URLs, emit audit table (path, bytes, sha256, duration, transcript path, verdict per row) |
| UPGRADE | Aborted-at-cap downloads report `partial: true, bytes_capped` instead of bare error |
| UPGRADE | Post-download ffprobe duration cross-check against yt-dlp metadata (mismatch = honest flag) |

---

## 3. Research / dashboards / login / debugging — verified state

| Capability | State | Verified |
|---|---|---|
| `browser_query` (ACD substring) | present | `tools.py:693` |
| `browser_find` / find-next in page | **absent** | grep: 0 in tools.py |
| `page_query` (JSON/ID traversal over ACD+actions) | **absent** | grep: 0 |
| Research orchestrator (plan/checkpoint/resume/track) | **absent** | grep: 0 |
| `browser_login` + OTP + verdicts | present | tools.py:326-386 |
| Per-origin account profiles / key vault | **absent** | grep: 0 |
| V8 in-process inspector (`engine.debug`) | **absent** (naturally — engine not built) | — |
| Worker/pool session diagnostics tree | **absent** | — |
| `browser_shell` 33 commands incl. `ref`/`trace`/`watch` | present | `shell.py:25-56` + AGENTS.md |

### Classification

| Class | Item |
|---|---|
| INVENT | `browser_find` / `browser_find_next` (match count + position, jump-to-ref) |
| INVENT | `page_query` — structured traversal over ACD blocks + action refs |
| INVENT | Research mode: `research plan` → `checkpoint` (URLs + notes + content hashes) → `resume` → `track` (dedup table) |
| INVENT | Credential/account vault per origin (ties into key vault above) |
| INVENT | In-process inspector lane via the fork (rusty_v8 inspector session exposed as `engine.debug`) |
| UPGRADE | Login verdicts gain `what_next` (which checkpoint the agent should act on) |

---

## 4. Human-heavy → agent-native translators — verified state

| Surface | State | Verified |
|---|---|---|
| ACD blocks (reading) | present | `acd.py` |
| Action surface w/ kinds + honest coverage | present | `actions.py`, `README.md:68-108` |
| SVG layout diagram screenshot | present | `minidom.js` `__screenshotSVG()`; AGENTS.md row |
| Canvas/WebGL stub (ops counter, no pixels) | present | `minidom.js:4127,4149` |
| AudioContext stub | present | `minidom.js:4559,4575-4576` |
| getUserMedia | honest reject | `minidom.js:7245` |
| Service worker | simulated strategies | `minidom.js:7313-7316`, `engine/sw.py`, `runtime.py:696-722` |
| `browser_images` | **KEEP — corrected** | see §6 |
| Canvas→digest translator (ops/palette log for agent analysis) | **absent** | — |

### Classification

| Class | Item |
|---|---|
| INVENT | `canvas2digest` — op log + palette + geometry summary instead of pixels |
| KEEP | ACD, action surface, SVG diagram, canvas/Audio/SW stubs (already agent-native or honestly absent) |

---

## 5. doshgentv8.1 — why `doshgentv8/base/` is a separate folder

This is vendoring, not duplication. The fork package has **three distinct layers**, each
with a different lifecycle:

```
doshgentv8/
├── v8/            ← V8 SOURCE TREE we patch (15.9.0). V8's own GN build
│                    expects its root: BUILD.gn, .gn, DEPS all live here.
│                    Editing happens HERE. This IS our native form in progress.
├── src/lib.rs     ← Rust+PyO3 bridge (rusty_v8 bindings). Engine-agnostic:
│                    it links whatever static V8 archive it's given.
├── __init__.py    ← Phase-0 Python API (mini-racer bridge today)
└── Cargo.toml     ← pulls rusty_v8 152.2; RUSTY_V8_ARCHIVE swaps our build in
```

Why the nesting is correct:

1. **V8's build system demands it.** GN/ninja must run from the tree root where
   `BUILD.gn`, `.gn`, and `DEPS` sit. You cannot merge V8's build into a Rust
   crate layout; every forker (Lightpanda's zig-v8-fork, deno's rusty_v8)
   vendors V8 as a nested tree.
2. **Upstream rebase discipline.** V8 ships every 4 weeks. Keeping the pristine
   upstream in `research_repos/v8/` (247 MB, with .git) as the diff oracle and
   our mutated tree in `doshgentv8/base/` (208 MB, .git stripped) means a rebase
   is `diff -ru` + patch reapplication, not archaeology.
3. **The artifact is the only boundary-crosser.** `webresearch/` never links
   the source tree. The compile output (static `.a`) enters via
   `RUSTY_V8_ARCHIVE` into the Rust bridge, which then exposes the same
   `DoshContext` Python API. Source location is invisible to the product.
4. **Patches must stay portable.** Our five agentic patches (fuel, clock, RNG,
   capability gate, checkpoint) live as a tracked patch series against the
   15.9.0 base so they can be re-based onto 15.10, 16.0, etc. without hand-
   merging a mutated tree.

The conversion to "our native form" = this tree + the five patches + our build
recipe. The folder structure is how a fork stays a fork instead of becoming an
unmaintainable blob.

---

## 6. Corrections from the first draft (the re-verification catch list)

| # | Draft claim | Reality (verified) | Action |
|---|---|---|---|
| 1 | "mini-racer = prebuilt V8 15.2" | mini-racer embeds **14.4.258.23** (runtime query). 15.2 is the rusty_v8 *crate* version | Fixed in §0; smoke results unchanged |
| 2 | "REMOVE browser_images (pixels for humans)" | `browser_images` is an **HTTP image search returning URLs + titles + snippets — no pixels ever** (`tools.py:48-66`). Already agent-native | Reclassified KEEP in §4 |
| 3 | Crypto stub line numbers "4643-4686" | Real block is `minidom.js:4708-4756` (file shifted under active edits) | All anchors re-pinned |
| 4 | "canvas 4062-4087, AudioContext 4494-4511, SW 7247-7251" | Now `4127/4149`, `4559/4575-4576`, `7313-7316` | Re-pinned; note file drift documented |

Also corrected across both docs: the earlier AGENTIC-NATIVE-CAPABILITY-MAP.md
"REMOVE screenshot/screenshot-layer" items need the same nuance — the SVG diagram is a
layout translator (KEEP), pixel rendering never existed (nothing to remove).

---

## 7. Final classification counts (verified)

| Class | Count | Items |
|---|---|---|
| **REMOVE** | **1** | manual captcha tile-click commands (collapse into inspect + solve; the native solver exists) |
| **UPGRADE** | **7** | honest crypto-stub errors; login verdict `what_next`; partial-download honesty; ffprobe cross-check; `net.rule` surface (already WIP); engine-parity A/B flag; README/tool-copy sweep for stale claims |
| **KEEP** | **12** | ACD, action surface, SVG diagram, canvas/Audio/SW stubs, login/OTP, video lanes + policy gates, downloads, WS text frames, storage events, history, skills, snapshots |
| **INVENT** | **15** | NativeCrypto suite; key vault; WebAuthn honest stub; transcript lane; audio-only; video sweep; browser_find(+next); page_query; research mode (4 commands); account profiles; inspector lane; canvas2digest; + the 9 mode-derived inventions from the capability map (step API, replay tape, arbitration, cost ledger, site-SDK, diff-first read, contract assertions, reward events, capability preflight) |

(The 9 mode-derived inventions live in `docs/AGENTIC-NATIVE-CAPABILITY-MAP.md` §5.6 —
verified consistent with the current tree in this pass.)
