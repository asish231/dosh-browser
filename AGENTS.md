# AGENTS.md

Guidance for AI agents and humans working in this repository.

## The rule that overrides everything else

**This project builds a browser from scratch. It must not use an existing one.**

No Chromium. No Playwright, no Puppeteer, no Selenium, no `rebrowser`,
`patchright`, or CDP of any kind. Not as a fallback, not as an escalation lane,
not "just for this hard page". `webresearch/engine/chromium.py` is a disabled
stub that exists only so imports and old call sites fail loudly.

Everything must work through:

- **HTTP fetch** (`webresearch/fetch.py`) — the transport
- **QuickJS mini-DOM** (`webresearch/engine/minidom.js`, `runtime.py`) — our own
  JavaScript runtime and DOM implementation
- **Our own DOM event model** — `dispatchEvent`, listeners, bubbling

If a page cannot be handled this way, the answer is to *implement more of the
engine*, not to reach for a browser binary. A capability gap is a bug report
against `minidom.js`.

`research_repos/` holds third-party projects (`browser-use`, `nodriver`,
`rebrowser-patches`) as **reading material only**. Their conventions do not
apply here. In particular `browser-use/AGENTS.md` describes a Chromium/CDP
architecture — the opposite of this one. Do not follow it.

## Why it matters

The engine exists to be small, inspectable, and free of a 150MB browser
dependency. A single `import playwright` undoes the point of the project.

## Research, reference code, and native compilation

This is research toward an LLM-native browser. External browser source may be
studied to understand loading, parsing, scripting, layout, events, security,
storage, and frame architecture, but it must never become part of this browser.

- External projects stay under `research_repos/` and are **reference material
  only**. Never import, execute, link, vendor, build, or depend on them from
  `webresearch/`, tests, scripts, or the server.
- Prefer architecture documentation, specifications, and narrowly relevant source
  excerpts over whole engine checkouts. Do not paste large foreign subsystems
  into this runtime.
- Attach agent compilation to the normal document lifecycle without changing it:
  owned fetch → owned parse → owned script execution → owned DOM/events → derived
  reading and action projections. The agent representation is an additional
  compiled output, not a replacement document.
- Complexity never justifies a bypass or shortcut. No spoofed trusted events,
  forged tokens, hidden unverifiable behavior, copied engine shortcuts, fake
  layout coordinates, or controls reported as actionable when they are not.
- If a capability is missing, name it explicitly, implement it natively, and add
  a hermetic regression test from real captured markup.
- `webresearch/engine/chromium.py` remains a permanently disabled stub. Do not
  revive it, reintroduce its behavior elsewhere, or create another external
  browser lane.

## Any-website conversion protocol

This is the central ongoing work: every loaded website must be transformed into
our own browser document and then compiled into an agent-native representation.
The browser is not finished when HTML arrives; it is finished when an agent can
read, locate, identify, and manipulate everything important.

Apply this protocol to every website, current and future:

1. **Load our own document first.**
   Fetch the source through our HTTP transport, preserve the final URL and
   response status, run its scripts through QuickJS, and keep the mutated
   document. The loaded page—not the raw byte stream—is the conversion source.

2. **Preserve the full control inventory.**
   Do not discard buttons, inputs, sliders, selects, links, frames, canvas
   challenges, or repeated controls before the agent plans. Noise reduction may
   shape a reading projection, but not the control inventory.

3. **Derive an agent-native action surface.**
   Every operable control must have:
   - a stable, content-addressed reference;
   - a human-readable label, preferring visible text;
   - an explicit action kind: `navigate`, `submit`, `activate`, `drag`,
     `dropzone`, or `embed`;
   - enough routing information to execute it deterministically;
   - a plain statement when only a richer engine capability is required.

4. **Separate reading from driving.**
   Text extraction may omit navigation chrome. The action surface must not.
   Never reuse an article-reading filter for controls.

5. **Report coverage honestly.**
   A complete page can still exceed a response budget. In that case, report the
   total, what was returned, what was omitted, and how to retrieve the rest.
   Silent truncation is a conversion failure.

6. **Represent—not pretend—unsupported controls.**
   If mini-DOM cannot move a control, expose its structure, label, action
   requirements, and the missing capability. Never let an unavailable action
   appear successful.

7. **Keep extending the compiler.**
   New websites will expose unsupported controls. Treat each newly discovered
   control as a requirement against the conversion protocol, implement the
   missing mini-DOM/parser/event behavior natively, and add a regression test
   from real captured markup.

A website is successfully converted only when its agent projection can answer:

- What can I read?
- What can I do?
- Where does each action go?
- What is still unavailable, and why?

## Current capabilities and known gaps

Be honest about these rather than working around them silently.

| Area | State |
| --- | --- |
| Layout / geometry | Real block-flow engine in minidom.js: CSS cascade (tag<class<id<inline), vertical stacking with margin collapsing, text wrap by monospace advance, CSS-spec 300x150 replaced defaults. Disclosed approximations: no floats/position/flex/grid, px/%/em only, single font metric. Reported via `document.__layoutInfo`. |
| File upload | Native end to end: `browser_upload`/`browser_act(files=)` sets `el.files` and fires trusted input/change; multipart submits carry the exact bytes. Binary/video/any files travel as base64 (`content_b64`) so payloads are byte-exact; payload cap ~12 MB raw. |
| Dialogs (alert/confirm/prompt) | Recorded per script pass, reported in the view and via `browser_respond_dialog`. QuickJS is synchronous, so a dialog cannot block: an agent stages an answer, then re-runs the triggering action. Unanswered dialogs honestly report `answered: default`. |
| Named keys | `browser_press_key` (enter/tab/escape/backspace/delete/space/arrows/home/end/pageup/pagedown) with real key/code/keyCode. Enter performs implicit form submission, Tab moves focus, arrows change selects, Backspace deletes. |
| window.open / popups | Recorded and loaded as new session tabs (recursion depth-limited); page scripts receive an inert stub window so they do not crash. The agent stays on the opening page and can `browser_tabs` switch. |
| File downloads | `browser_download` streams to disk with sha256 and optional hash verification (hex or base64); a navigation that lands on an attachment/binary is saved and reported (path/bytes/hash) instead of parsed as a page. No malware verdict engine — that absence is reported, not hidden. |
| contenteditable | Detected as `editable` in both inspect and action surfaces; typing lands in textContent with input events carrying `data`/`inputType` so MutationObserver-based editors (Quill/ProseMirror) observe it. Approximation: no caret, plain text only. |
| Login / OTP | `browser_login` detects the wall (password role in both surfaces), fills+submits via the normal lanes, and returns an honest verdict: verified / otp_required / failed / account_locked / human_only. OTP pages (single input or split digit boxes) become checkpoints: `browser_submit_otp` fills, submits, verifies; max 3 attempts, ~10 min expiry, never guesses codes. `browser_login_state` probes without credentials. Secrets are memory-only: never persisted, never echoed in views/results. Engine never ticks "trust this device". |
| WebSocket | Hand-rolled RFC 6455 client (`engine/ws.py`, no third-party lib): sync handshake, masked client frames, text frames both ways, ping/pong and close handled; frames deliver on runtime pump passes. Binary frames are counted and skipped (disclosed gap). |
| Trusted events | `Event` carries `isTrusted`; agent-driven dispatches (click/type/activate/drag/submit/press) are trusted, programmatic `el.dispatchEvent` is not. |
| Inline `onclick=` attributes | Wired: constructor compiles `on*` attributes into listeners (minidom.js Element). |
| Canvas / iframe pixels | Never reach the agent. Both are decomposed at parse time (`parse.py`). |
| Trusted CDP input | Does not exist and must not be added. |
| Cross-origin frame messaging | Frames are fetched as separate documents; there is no real `postMessage` handshake between them. |
| Fingerprint parity | TLS JA3/JA4 matches Chrome 124 via `curl_cffi` (both page fetch and subresource `host_fetch` lanes); Sec-Fetch-* headers computed from origin relationship; Sec-CH-UA client hints; enriched `navigator.*` device block (hardwareConcurrency, deviceMemory, screen, timezone, platform); inter-submit delay in login flows mimics human timing. |
| Injected script execution | Scripts added to the DOM at runtime via `appendChild` (SPA chunk injection) are detected and executed: `src` scripts are fetched and evaled, inline scripts evaled directly, ESM auto-detected for both. Failures are surfaced as JS console errors. |

Say "this needs a real browser" plainly when it is true. Do not paper over it.

## Engineering rules learned the hard way

These cost real debugging time. Please don't rediscover them.

**Never assert on a substring of a whole page.** `assert "clicked" in html`
passes when the string occurs in a script or an attribute, whether or not the
code ran. Read the specific element's content. This produced a genuinely false
passing test here.

**Match CSS class tokens exactly, never by substring.** `slider` is a prefix of
`sliderContainer`, `sliderMask`, `sliderbg` and `sliderIcon`. Substring matching
turns one slider handle into six phantom controls.

**Prefer visible text over machine identifiers when labelling a control.**
`<button id="go">Go</button>` is labelled `Go`, not `go`. Check `aria-label`,
`title`, `alt`, `placeholder`, then text; only then `name`/`value`/`id`.

**Never drop controls silently.** If a budget hides part of the action surface,
say how many. The agent cannot plan against a surface whose shape it cannot see.
`actions.py` reports `ACTIONS: 244 of 245 (complete)` for this reason.

**Reading a page and driving a page need different filters.**
`content.py`'s `inside_chrome()` correctly drops nav/footer when you want an
article, and would be wrong for driving a page — "Open menu" and breadcrumbs are
exactly what an agent needs. Do not reuse the reading filter for the action
surface.

**Escape CSS identifiers.** Tailwind emits `lg:tw-flex` and `tw-flex[2]`; unescaped
they produce selectors the engine rejects.

**Copy every field from an event's `init` object.** `Event` dropping `clientX`
made every drag arrive as a click at `(0,0)`.

**Don't toggle twice.** `Element.click()` already flips checkbox state. Setting
`checked` in addition lands you back where you started.

**Make tests hermetic.** `settings.load_dotenv()` uses `setdefault`, so a test
that clears an env var gets it repopulated from `.env` on the next call. Patch
`load_dotenv` in tests that assert on configuration.

## Secrets

Secrets live in `.env` only, which is gitignored. Never write a key into source,
a test, a doc, or a commit. `.env.example` documents the variable names with
empty values.

## Secrets and providers

`settings.py` holds the model registry. `FAMILIES` maps a family to an
`api_key_env` and `base_url_env`; `MODELS` maps a registry `id` to a wire
`model`. These differ whenever an endpoint names its own deployments — `--model
pool-auto` sends `auto`. Keep them separate.

For an OpenAI-compatible pool, both `OPENAI_COMPAT_BASE_URL` and
`OPENAI_COMPAT_API_KEY` are required. The registry deliberately refuses to guess
a host from a key, because a wrong guess surfaces as a 401 that looks like a bad
key.

## Verifying a change

```bash
.venv/bin/python -m pytest tests/ -q     # full suite
.venv/bin/python -m webresearch models   # what is configured
```

Some tests self-skip when an optional dependency is absent; read the skip
reason before assuming coverage.

Test against **real captured markup**, not only hand-written HTML. Hand-written
fixtures only prove the assumptions you already had. `testhtml/test1.html` is a
captured page (205 links, 14 buttons, Nuxt/Vue) and found several real bugs that
invented HTML never would have.

## Reporting results

State what was verified and how. If something did not work, say so plainly and
show the evidence. Distinguish "the drag executed" from "the captcha passed" —
they are very different claims, and conflating them is how a false success gets
reported.