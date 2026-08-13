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

**JS sites (TripAdvisor, React):** scripts run in mini-DOM. Classic JS works. ES module / heavy React bundles are skipped until the DOM API grows — you still get SSR HTML + inspect refs.

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
| `browser_frames` | List loaded iframe documents as `[fN]` |
| `browser_frame_source` | Read one frame's post-JS HTML |
| `browser_captcha` | Separate structured challenge inspection |
| `browser_captcha_code` | Dump widget/iframe HTML and related scripts |
| `browser_click` | Click a top-level `[eN]` link/button/field |
| `browser_type` | Type into `[eN]` |
| `browser_submit` | Submit form `[n]` |

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
