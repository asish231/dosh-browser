# webresearch

Browser **for LLM agents**, not humans.

No Chromium. HTTP fetch + a small **QuickJS mini-DOM** (own JS runtime). Every action is an API the model can call.

## Agent loop

1. `GET /v1/tools` → put `tools` on the model
2. Model calls a tool → `POST /v1/invoke` `{ "name", "arguments" }`
3. Put `view` back in context (text + `[n]` links + `[eN]` inspect refs)
4. Repeat: `browser_open`, `browser_click`, `browser_type`, `browser_search`
5. If `CAPTCHA OBJECT` appears, call `browser_captcha`

```bash
python -m webresearch serve          # http://127.0.0.1:4747/docs
python -m webresearch search "best hotels in paris"
python -m webresearch read https://example.com
python -m webresearch captcha https://example.com --json
python -m webresearch ask "search for example.com and say what it is"
```

**Google:** do not scrape `google.com` like a person. Call `browser_search`. That is a search *request* (Google HTML, DuckDuckGo if blocked).

**JS sites (TripAdvisor, React, Vue, Nuxt):** classic scripts run in mini-DOM; `type="module"`
bundles are fetched, dependency-ordered and evaluated via our custom ESM translator.
`fetch`/XHR promises are pumped to completion and framework state
(`__NEXT_DATA__`, `__NUXT__`, Apollo, `__PRELOADED_STATE__`) as well as JSON-LD schemas
(`FAQPage`, `HowTo`, `Product`) are cleanly extracted directly into structured ACD blocks.
No Chromium, no Playwright, no browser bloat.

## Install

```bash
cd webresearch
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Tools that matter

| Tool | What the agent does |
| --- | --- |
| `browser_search` | Google results as numbered hits |
| `browser_read` / `browser_goto` | Open URL, run JS, get `view` |
| `browser_open` | Follow link `[n]` |
| `browser_inspect` | List `[eN]` buttons/inputs/captcha widgets |
| `browser_actions` | List the **complete** action surface with stable refs |
| `browser_act` | Execute one action by ref (navigate / fill / click / drag) |
| `browser_frames` | List loaded iframe documents as `[fN]` |
| `browser_frame_source` | Read one frame's post-JS HTML |
| `browser_captcha` | Separate structured challenge inspection |
| `browser_captcha_code` | Dump widget/iframe HTML and related scripts |
| `browser_click` | Click a top-level `[eN]` link/button/field |
| `browser_type` | Type into `[eN]` |
| `browser_submit` | Submit form `[n]` |
| `browser_query` | Retrieve ACD blocks by substring (structured context) |
| `browser_slice` | Read ACD block range `b12`→`b40` |

## The action surface — human page → agent API

Reading a page and driving a page need different surfaces. The reading view
(`browser_inspect`, numbered `[eN]`) is capped and filtered, because nav chrome
and footer links are noise when you want an article. Those same links are
exactly what you need in order to *go somewhere*.

`browser_actions` enumerates everything, and every control is labelled with how
it can actually be actuated:

| kind | what it means | how the agent acts |
| --- | --- | --- |
| `navigate` | has an href | plain HTTP GET |
| `submit` | belongs to a form | fill `field_name`, POST/GET |
| `activate` | bare button/checkbox/field, no href and no form | **real browser required** |
| `drag` | slider or handle | **real browser required**, with `dx`/`dy` |
| `dropzone` | the goal a handle is dragged onto | reached by dragging, not clicked |
| `embed` | iframe/canvas/video | read its own document |

Two properties matter for trust:

- **Nothing is dropped silently.** The surface reports
  `ACTIONS: 378 of 379 (complete)` or names the budget that hid the rest.
  The reading view does the same for links (`[48] ... 136 more link(s) not
  shown`). An agent that sees 47 of 183 links must not conclude the page has
  no other exits.
- **Engine-only controls say so.** A control with no href and no form cannot be
  actuated over HTTP at all, and the surface labels it `needs real engine`
  instead of pretending `browser_click` will work.

Refs are content-addressed (`a1b2c3d4`), so they survive reordering of
unrelated elements between page loads.

Hidden inputs are deliberately excluded from the surface: their values are
session blobs (CSRF tokens, captcha hashes, DataDome cids) and listing them as
fillable fields invites an agent to invent them. They still ride along on a real
submit, read from the document at submit time.

Drag discovery matches class tokens exactly, never by substring — in real
markup `slider` is a prefix of `sliderContainer`, `sliderMask`, `sliderbg` and
`sliderIcon`, so a substring test turns one handle into five phantom controls.

## ACD — Agent Context Document

Every page is projected into a structured `ACD` (alongside the human-readable
`view`): ordered blocks with stable ids (`b1`, `b2`, …), typed as heading / text /
list / table / code, plus `actions` (`[eN]`), `links`, observed `api` calls,
`frames`, `framework` hydration data and `errors`. Agents can retrieve context
with `browser_query` / `browser_slice` instead of re-reading a flat markdown blob.
Tables are rendered as markdown in `view` and as `rows` in `acd.blocks`.

## Challenges / captcha

Will you hit them? **Yes** on Google sorry pages, Cloudflare, TripAdvisor WAF, recaptcha.

What the engine now exposes:

- Every iframe is fetched into its own document and isolated QuickJS context.
- Nested frame refs preserve their parent (`f1` → `f2`).
- Parent and frame scripts have `fetch`, XHR, timers, geometry stubs, and
  minimal `postMessage` capture.
- `browser_captcha` reports provider, site key, callback, anchor/challenge
  frames, checkbox state, prompt, sprite URL/hash/size, tile crop offsets,
  controls, and hidden-field metadata.
- `browser_captcha_code` / `GET .../captcha/code` returns the loaded source:
  parent widget HTML, iframe documents, checkbox/token/tile markup, scripts,
  and sprite bytes as base64.
- `GET /v1/sessions/{id}/captcha/code.txt` is the same dump as plain text.
- `GET /v1/sessions/{id}/frames/{ref}/source` returns the loaded frame HTML.
- If a sprite was fetched with the session cookies,
  `GET /v1/sessions/{id}/captcha/sprite` returns the image bytes.

This is an **inspection surface, not a CAPTCHA solver**. It never selects
tiles, submits a challenge, or returns a valid `g-recaptcha-response`.
Cloudflare, DataDome, and Google integrity checks can still block navigation.
Prefer `browser_search` over loading `google.com`.

## Captcha API

One-shot:

```bash
curl -s http://127.0.0.1:4747/v1/captcha \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com","js":"auto"}'

curl -s http://127.0.0.1:4747/v1/captcha/code \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com","js":"auto"}'
```

Session:

```text
GET /v1/sessions/{id}/captcha
GET /v1/sessions/{id}/captcha/code
GET /v1/sessions/{id}/captcha/code.txt
GET /v1/sessions/{id}/captcha/sprite
GET /v1/sessions/{id}/frames
GET /v1/sessions/{id}/frames/{fN}/source
```

## Python

```python
from webresearch import Browser

b = Browser()
s = await b.create()
await s.search("tripadvisor paris hotels")   # hits, not google.com
await s.goto("https://example.com")
await s.click("e1")
```

## LLM agent

Copy `.env.example` to `.env` and set keys. The default model is **GLM-4.7-Flash** (z.ai, free tier).

```bash
python -m webresearch models --ping                  # what is configured and reachable
python -m webresearch ask "What is example.com? Search, then answer in one sentence."
python -m webresearch ask --model mercury "..."      # Mercury 2 (Inception)
python -m webresearch ask --model auto "..."         # fail over if GLM is rate limited
```

Or `POST /v1/ask` `{ "q": "...", "model": "glm" }`, and `GET /v1/models`.

Notes:

- GLM-4.7-Flash is free but allows roughly **one request at a time**; bursts return `1302` / `1305`.
- Paid GLM tiers (`glm-4.7`, `glm-4.7-flashx`) are not enabled on this account (`1113`), so they are not offered.
- Model reasoning is off by default for speed. Add `--think` for harder tasks.
