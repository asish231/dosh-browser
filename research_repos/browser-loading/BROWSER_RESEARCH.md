# Browser Loading & Security Research (reference clones)

Cloned reference material (read-only research, never imported/executed/linked/built):

| Family | Local path | Upstream |
| --- | --- | --- |
| DuckDuckGo (Android) | `research_repos/android` | github.com/duckduckgo/android |
| DuckDuckGo (macOS) | `research_repos/macos-browser` | github.com/duckduckgo/macos-browser |
| Chromium | `research_repos/chromium` (sparse: `content/browser/renderer`, `services/network`) | github.com/chromium/chromium |
| Safari/WebKit | `research_repos/WebKit` (sparse: `Source/WebCore/loader`, `Source/WebKit`) | github.com/WebKit/WebKit |
| Tor | `research_repos/tor` (git mirror) | git.torproject.org/tor.git (GitHub stub; gitlab blocked) |

## 1. DuckDuckGo

- Browser shells: `DuckDuckGo/` (macOS), `android/app/`. Loading delegated to the
  platform WebView (WKWebView / Android WebView) — DDG adds privacy layers, not a
  new engine.
- Own components: tracker blocking content blockers (`ContentBlocker/`,
  `Autofill/`, `BrokenSitePrompt/`), HTTPS upgrades, Duck Player, Fire/burn data
  on tab close, grade/privacy dashboard in `NewTabPage`.
- Security: TLS enforced by the platform WebView; DDG's own "Smarter Encryption"
  auto-upgrades http→https; tracker blocking rules from the Tracker Radar list;
  cookie pop-up blocking (Autoconsent); `DBP` (data breach protection) checks
  emailed credentials against a local hash prefix database.

## 2. Chromium loading pipeline (from source)

1. URL parsing/normalization (`url/gurl.h`, `net::SchemeRegistry`), scheme
   upgrade rules (HTTPS-First).
2. Navigation scheduling in the browser process; renderer process isolation;
   `content/browser/renderer` hosts the render frame decomposition.
3. Network service (`services/network/public/cpp`): `url_loader.cc`,
   `url_loader_context.h`, `network_service.cc`. DNS via `host_resolver`,
   proxy (`proxy_service_mojo`), TLS via `tls_client_socket` /
   `ssl_config_service_mojo`, HSTS, cert verification, certificate transparency
   enforcement, CT logs, safe browsing checks.
4. Cookies/partitioning: `cookie_manager`, `cookie_settings`, restricted
   cookie manager for cross-site isolation.
5. Security headers: CSP enforcement in renderer (`content_security_policy`),
   CORP/COOP/COEP isolation (`cross_origin_opener_policy`, document-policy),
   X-Content-Type-Options, Referrer-Policy.
6. Cache: HTTP cache in network service, prefetch cache (`prefetch_cache`),
   back/forward cache bookkeeping.
7. Renderer: HTML parser → DOM → CSSOM → style/layout (no geometry exposed to
   us yet) → script execution (V8). Events/input dispatched with trusted
   events, `isTrusted` exposed.

Security checks/hashes to mirror natively: HSTS upgrade, HTTPS-First fallback,
certificate validation, CT, Safe Browsing hash-prefix lookups (URL/domain hash
prefixes), CSP evaluation + hash-source (`'sha256-...'`) for scripts/styles,
SRI (`integrity=`) subresource hash check, cookie SameSite/partitioned
attribute evaluation, CORB/CORP blocking of nosniff responses, mixed-content
blocking/auto-upgrade, and download-scan hashing.

## 3. WebKit (Safari) loading pipeline

- `Source/WebCore/loader/FrameLoader.cpp` drives document load: provisional
  navigation → redirect chain → commit → subresources (images/scripts/css) →
  DOMContentLoaded → load.
- `ResourceLoader`/`CachedResourceLoader`: cache keyed on URL + Vary + CSP;
  `ResourceRequest`/`ResourceResponse` carry security headers.
- CSP: `ContentSecurityPolicy` engine in WebCore; script/style hash-sources.
- Network: `NetworkProcess` with per-origin partitioning, HSTS, Intelligent
  Tracking Prevention (ITP) cookie partitioning.
- Event model: real trusted events, `isTrusted` flag, passive listeners
  semantics, Timer throttling in background tabs.

## 4. Tor

- Tor itself (`tor/`) is a circuit-building onion-routing transport, not a web
  engine: directory auth via consensus documents signed by directory
  authorities (Ed25519 identity keys), descriptors, circuit construction
  (CREATE/CREATE2, CREATE_FAST, extend), AES-CTR relay
  encryption/decryption, ntor handshake, guard persistence, and end-to-end
  TLS inside the exit.
- Browser use (Tor Browser) is Firefox-based and adds: all traffic through
  SOCKS5 to local tor daemon, per-tab first-party isolation, letterboxing,
  fingerprint defenses, .onion-only mode, HTTPS-Only mode. For our purposes:
  every request carries the privacy transport; pages are fetched over
  TLS-to-exit as usual.

## 5. Extraction → native engine (webresearch) implications

Extracted requirements for our from-scratch engine
(`webresearch/fetch.py` + `engine/minidom.js` + `engine/runtime.py`):

1. **Transport hardening already owned**: HTTPS enforcement, redirect
   preservation, final URL tracking. Needed natively: HSTS-style https
   upgrade for known-downgraded http URLs, SRI `integrity=` hash verification
   (sha256/sha384/sha512) for script/link[rel=stylesheet] subresources, CSP
   `default-src/script-src/style-src` evaluation including `'sha256-...'`
   sources, SRI failure = drop subresource (never silently allow).
2. **Cookie security model**: SameSite=Lax default semantics, Secure
   attribute, partitioned/`__Host-`/`__Secure-` prefix checks, third-party
   cookie blocking toggle to mirror Tor/ITP.
3. **Mixed content**: block or auto-upgrade `http://` subresources on an
   `https://` document.
4. **Events**: mirror WebKit/Chromium — trusted-event flag internally
   distinct from synthetic dispatch; inline `on*` handler attributes must be
   compiled into listeners (currently not wired per AGENTS.md); passive
   listener semantics; focus/blur traversal.
5. **Subresource security**: cross-origin script/style requires no sniff
   violations; enforce `X-Content-Type-Options: nosniff` → refuse script
   execution.
6. **Safe-browsing / hash prefix**: out of scope for now, but structure
   fetch.py so a denylist of exact host hashes can be enforced before
   fetching.
7. **Security honesty in projections**: `actions.py`/`snapshot.py` must
   represent unsupported controls (trusted events, geometry, postMessage)
   rather than faking success — already required by AGENTS.md; reference
   findings confirm these are the exact gaps a real browser covers.

## 6. Gap log against `webresearch` today

| Reference behavior | Native state | Action |
| --- | --- | --- |
| SRI integrity hashes | implemented in runtime+modules | verified passing |
| CSP hash-sources | implemented (script-src/default-src) | verified passing |
| Mixed content blocking | implemented | verified passing |
| Inline `onclick=` | wired in minidom.js Element ctor | verified passing |
| Trusted events / isTrusted | implemented | Event has isTrusted; agent actions trusted, synthetic not |
| Cookie SameSite/prefixes | implemented | verified passing |
| CSP style-src hash-sources | implemented (inline + <link>) | verified passing |
| HTTPS upgrade | none | https upgrade + SameSite defaults |
| Cert Transparency / geometry / getBoundingClientRect | none (documented gap) | remains documented gap |
| HTTPS-First | n/a (fetch does https) | keep |
| Safe-browsing hash-prefix | env denylist hook implemented | verified passing |
| Layout geometry | genuine block-flow engine in minidom.js (disclosed approximations) | verified passing |
| External stylesheets | gated fetch (SRI/CSP/trackers/mixed-content) injected into cascade | verified passing |
| HSTS | persistent state + preload seed + upgrade enforcement | verified passing |
| CORP/COEP/COOP | enforced on scripts/modules/styles/frames | verified passing |
| Trusted Types | createPolicy + require-trusted-types-for on owned sinks | verified passing |
| SB list updates | versioned add/remove deltas with checksum fail-closed | verified passing |
| SW fetch interception | strategy engine (CacheFirst/SWR/NetworkFirst/CacheOnly) + revalidation drain | verified passing |
| Background fetch | concurrent hashed downloads with progress | verified passing |
| CT SCTs | P-256 ECDSA verification against pinned logs | verified passing |
| SXG/WebBundle | signature-field validation + CBOR section listing, never executed | verified passing |

This document is the durable output of the three-family research; the clones
under `research_repos/` remain read-only reference material.

---

# Deep dive (exhaustive working notes)

## A. DuckDuckGo

### macOS (`research_repos/macos-browser`)
- App entry: `DuckDuckGo/Application/` with `AppDelegate`/`WindowControllers`.
- Tab system: tab bar + tab view controllers per tab; each tab owns a
  `WKWebView` and a `Tab` model in `Tab/`.
- Content blocking: `ContentBlocker/` — loads DDG Tracker Blocklist rules into
  `WKContentRuleList` (compiled WebKit content blocker rules), applied per
  `WKWebViewConfiguration`. Third-party requests matching the list fail with
  blocked errors; first-party and same-page trackers differ.
- HTTPS upgrade: `BrokenSitePrompt/` + Smarter Encryption component upgrades
  `http://` Latin/any request to `https://`; failures surface a "not secure"
  interstitial rather than silently downgrading.
- Fire: `Fire/` clears site data via `WKWebsiteDataStore`.
- Email protection: `Email/` rewrites to a relayed address; DBP (`DBP/`)
  monitors breaches via k-anonymity hash-prefix queries.
- Pixels/telemetry: `Statistics/` — anonymous "pixels" events.

### Android (`research_repos/android`)
- `app/src/main/java` WebView-based: `BrowserWebView` (custom `WebView`) with
  `WebViewClient` subclass handling URL loading, error pages, and custom
  schemes.
- Tracker blocking: `ContentBlocker/` compiled lists injected as a JS interface
  + request interception (`shouldInterceptRequest`).
- HTTPS upgrade via `WebViewClient.shouldInterceptRequest` rewriting http→https.
- Fire button analogous to macOS; tab swiping, downloads, find-in-page,
  autocomplete, and Fireproof sites to skip wipe.

### Page-load behavior (what we can mirror)
1. User enters URL → upgrade to https first.
2. Load in WebView; resource requests checked against compiled tracker list;
   blocked requests never leave the device.
3. On response with HTTP error / cert failure → error page; optionally prompt
   to allow broken site.
4. Fire clears cookies/cache/site data; no persistent tracking storage.
5. Privacy dashboard grades the page from trackers seen, HTTPS, and encryption
   coverage.

## B. Chromium

### Process model
- Browser process: UI, navigation decisions, policy checks.
- Renderer processes: one (or more) per site instance; V8/Blink isolated.
- Network service process: sockets, DNS, TLS, caching (sandboxed).
- GPU, audio, utility processes.

### Navigation pipeline (content/browser, content_common)
1. `NavigationController` posts `NavigationRequest`.
2. URL normalization via `url::GURL`; scheme checks (`url` module whitelists).
3. HTTPS-First / HTTPS-Upgrades: `content_settings` + `HttpsUpgradesService`
   rewrite http→https before the request is issued.
4. Navigation policy: Safe Browsing verdict check (full URL hash-prefix lookup
   against blocklists), enterprise policies, download policies, prerender
   eligibility.
5. Network service starts the request (`NavigationURLLoader` →
   `URLLoaderNetworkService`): DNS (`HostResolver`), proxy, connection
   pooling, TLS (`TLSClientSocket`), certificate verification with CT
   enforcement (`CertificateTransparencyVerificationService`), HSTS check
   (`TransportSecurityState`) upgrades http→https on recorded hosts.
6. Early hints / redirect chain recorded as `NavigationHandle` events.
7. Commit: renderer starts parsing (`DocumentLoader`), creates `RenderFrame`.
8. Subresource discovery: HTMLPreloadScanner precedes parser; script/css/img
   fetches subject to CSP (`ContentSecurityPolicy` evaluation per directive),
   SRI (`SubresourceIntegrity`), CORB/ORB checks, mixed-content classifier
   (blockable vs optionally-blockable → upgrade/block).
9. Script execution: V8 context; trusted-types enforcement on DOM XSS sinks;
   `isTrusted` events.
10. DOMContentLoaded / load / first paint; frame lifecycle (load committed →
    DOMContentLoaded → load event → interactive).

### Security checks & hashes
- TLS: certificate chain validation to a pinned root store, CT SCT presence,
  EV indicators, HPKP-deprecated. SHA-256 fingerprints; no MD5.
- HSTS: host+includeSubDomains preload list compiled in.
- Safe Browsing: URL hash of domain + path prefixes, full hash after bloom;
  page-level warning interstitial; download verdict scans.
- CSP: per-directive allowlist; `'nonce-'`, `'sha256-'` for inline; report-uri.
- SRI: `integrity="sha384-..."` on script/link; mismatch blocks.
- Cookies: SameSite (None implies Secure), partitioned (CHIPS), `__Host-`
  prefix pinning to path=/ and no Domain.
- CORP/COEP/COOP: cross-origin isolation; response headers enforced at fetch.
- Mixed content: block or upgrade-blockable.
- Sandbox: renderer sandbox (SECCOMP/win job objects), network sandbox.

## C. WebKit

### Architecture
- `Source/WebCore`: DOM, HTML parser (`HTMLTokenizer`/`HTMLTreeBuilder`), CSS,
  layout (`RenderTree`, `RenderStyle`), JS bindings.
- `Source/WebKit`: UIProcess ⇄ WebProcess IPC (`WebPage`/`WebPageProxy`),
  networking in `NetworkProcess`, GPU process for WebGL/compositing.

### Loader
- `FrameLoader` (`WebCore/loader`): state machine
  `Provisional → Committed → Finished`; subframes, history, error recovery.
- `DocumentLoader`: request/response, data-URL policy, redirect handling,
  `ResourceLoadPriority` ordering.
- `CachedResourceLoader` + `HTTPCache`: ETag/Last-Modified/Vary, memory (LRU)
  + disk cache; revalidation with If-None-Match → 304.
- Preload scanner finds images/scripts/styles early; `<link rel=preload>` hints
  upgrade priority.

### Security
- Same-origin policy in `SecurityOrigin`; cookie partitioning/ITP for
  cross-site trackers; first-party sets.
- CSP (`ContentSecurityPolicy`): same directive model; `report-only` mode.
- Upgrade insecure requests, block-mixed-content.
- TLS mirrors platform (no bundled cert store on Apple platforms); CT.
- HSTS preload; `HTTPS-Only` mode optional per-tab.
- Subresource Integrity on script/link; CORS for fetch/XHR with `Access-Control-*`.
- Sandboxing: WebProcess entitlement allowlist; IPC is the only escape.

## D. Tor

### `tor/` (core)
- Directory authorities sign a consensus of relay descriptors
  (Ed25519 identity keys; ntor onion service descriptors).
- Clients fetch the consensus, build paths of 3 hops (guard/middle/exit), and
  create circuits via `CREATE2/CREATE_FAST` + `EXTEND2` cells.
- Each hop's TLS-style handshake uses ntor (Curve25519) key exchange; relay
  cells are multi-layer encrypted (AES-CTR per hop); stream data
  (RELAY_BEGIN/DATA) exits as ordinary TCP. All web traffic reaches the exit
  as TLS to the origin.
- Onion services: rendezvous protocol; introduction points; descriptor
  cryptography; v3 addresses = hash of public key.
- Bridges/pluggable transports for censored regions; guard pinning;
  circuit isolation by stream ID.

### Tor Browser (Firefox-based, not cloned here) behavior worth mirroring
- All requests SOCKS5 → local tor; per-tab first-party isolation; letterboxing;
  fingerprint resistance (resist letterboxing, no canvas readback); HTTPS-only.

## E. What our mini-DOM still does NOT do (honest list, mirrored in AGENTS.md)
- No layout (`getBoundingClientRect`, IntersectionObserver estimates only).
- No real trusted input from OS — all trusted events are agent-driven.
- Canvas/IFRAME pixels off-limits; frames are separate documents, no live
  postMessage handshake between two live frames.
- WebGL/Canvas2D fingerprints are stubbed deterministically.
- No downloads, no service workers, no background fetch.
- No DNS/HSTS/cert-transparency state — TLS is whatever curl_cffi/httpx yields.
- Safe-browsing reduced to env denylist, not real hash-prefix lookup.

## F. Extraction status vs current `webresearch`
Implemented (with tests): SRI, mixed-content, CSP script-src + style-src
hash-sources, cookie SameSite/prefix enforcement, trusted-event flags, inline
`on*` handlers, safe-browsing denylist, honest ACD action kinds/routing/coverage, content-addressed stable refs (sha1 role|tag|name|text|href), .onion v3 checksum validation with never-clearnet refusal, hash-pinned remote tracker lists.
Still pending (native, no browser binary): layout geometry; real cert
validation/CT/HSTS state persistence; iframe postMessage bridging;
service-worker/cache lifecycle; download handling; canvas/WebGL exposure policy.
