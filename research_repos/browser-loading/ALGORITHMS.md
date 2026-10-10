# Exact loading algorithms — verified against source

Notation: **[V]** = verified by reading the file in this repo checkout (path + line
given). **[R]** = from the reference subagent survey of the same checkout
(WebKit `loader/`, tor `src/`) or from stable upstream architecture where the
file is outside our sparse checkout. Nothing here was built or executed.

---

## 1. Chromium — navigation, request, checks, commit

Sources: `research_repos/chromium/` (**[V]** `services/network/url_loader.cc`,
`content/browser/ssl/ssl_error_handler.cc`), plus upstream
`content/browser/renderer_host/navigation_request.cc` (**[R]**, file moved out
of `content/browser/` into `renderer_host/` upstream, confirmed via
`renderer_host/README.md` in the mirror).

### 1.1 `NavigationRequest::BeginNavigation()` → `BeginNavigationImpl()` (`navigation_request.cc:2470,2864`)

Exact pre-network check order (each failure destroys the request via
`OnRequestFailedInternal` — "DO NOT ADD CODE after this" pattern throughout):

1. `should_cancel_on_leaving_initial_webui_` → abort (`ERR_ABORTED`).
2. Prerender activation checks (`MaybeStartPrerenderingActivationChecks`).
3. Fenced-frame `urn:uuid` → URL mapping (`ConvertFencedFrameURNToURL`).
4. Android embedder `ShouldOverrideUrlLoading` → abort if overridden.
5. **`CheckContentSecurityPolicy(has_followed_redirect=false, ...)` runs BEFORE
   NavigationThrottles** — comment in code: "This gives CSP a chance to modify
   requests that NavigationThrottles would otherwise block."
   Failure → `OnRequestFailedInternal(net_error)`.
6. `CheckCredentialedSubresource()` → `ERR_ABORTED` on block.
7. `IsAllowedByConnectionAllowlist(is_redirect=false)` → `ERR_NETWORK_ACCESS_REVOKED`.
8. `StartNavigation()` (creates the NavigationHandle observers see).
9. `CheckAboutSrcDoc()` → `ERR_INVALID_URL` on block (skip_throttles=true).
10. Browser-initiated error navigations short-circuit with stored error page.
11. `SetupCSPEmbeddedEnforcement()` + `SetupConnectionAllowlistEmbeddedEnforcement()`.
12. `NeedsUrlLoader()` false (about:blank/srcdoc/MHTML/same-document) → commit
    path without network: `CheckCSPEmbeddedEnforcement()`,
    `CheckResponseAdherenceToCoep()` (`kCoepFrameResourceNeedsCoepHeader`),
    COOP enforcement, then `SelectFrameHostForCrossDocumentNavigationWithNoUrlLoader()`
    or `WillCommitWithoutUrlLoader()` for same-document.
13. Else `WillStartRequest()` → state `WILL_START_REQUEST`.

### 1.2 Throttles — `WillStartRequest()` (`:9228`)

1. Self-referential URL cancel (`IsSelfReferentialURL`).
2. `throttle_registry_->RegisterNavigationThrottles()` (Safe Browsing, etc.
   register here; skipped entirely for back-forward-cache/prerender activation).
3. `ProcessNavigationEvent(kWillStartRequest)` — every throttle votes
   PROCEED / CANCEL / DEFER. Request does not touch the network until all
   throttles proceed.

### 1.3 Network fetch — `URLLoader::ScheduleStart()` (`services/network/url_loader.cc:741`) **[V]**

1. `ResourceScheduler` may defer (`WillStartRequest(&defer)`); else `url_request_->Start()`.
2. Inside `net/` (outside checkout, architecture **[R]**): `URLRequestHttpJob` →
   proxy resolution → `HostResolver` (DNS) → socket pool → TLS handshake →
   `CertVerifyProc` chain validation against platform roots + `Certificate
   Transparency` SCT verification + HSTS/`TransportSecurityState` upgrade →
   HTTP transaction → cache (`HttpCache::Transaction`) → response.
3. `URLLoader::OnConnected` (`:856`): Local Network Access check, then
   Client-Hints (`Accept-CH`) processing before headers are consumed.
4. `URLLoader::OnReceivedRedirect` (`:964`) **[V]** — exact order:
   a. Defer redirect (`*defer_redirect = true`); client inspects first.
   b. **CORP check on the redirect response** (`CrossOriginResourcePolicy::IsBlocked`
      with COEP + DocumentIsolationPolicy reporters) → `CompleteBlockedResponse(ERR_BLOCKED_BY_RESPONSE)`.
   c. `SetRequestCredentials` (recompute cookies/credentials for new URL).
   d. Strip `Cookie` header + `cookies_from_browser_` (no credential carryover).
   e. Cross-origin → drop storage-access-grant overrides.
   f. Forward `OnReceiveRedirect` to `NavigationRequest::OnRequestRedirected`.
5. `URLLoader::FollowRedirect` (`:778`) **[V]** — on client approval:
   recompute storage-access status → `MaybeRemoveSecHeaders` → re-add
   `Sec-Fetch-*` metadata → `AreRequestHeadersSafe` gate on modified headers →
   reset Local-Network-Access state → `FollowDeferredRedirect`.
6. `URLLoader::OnSSLCertificateError` (`:1134`) **[V]** → forwards to
   `url_loader_network_observer_->OnSSLCertificateError` with a bound
   `OnSSLCertificateErrorResponse`; browser side decides proceed/deny.

### 1.4 Redirect handling — `NavigationRequest::OnRequestRedirected` (`:3759`)

1. Re-check `IsAllowedByConnectionAllowlist(is_redirect=true)`; fail → `ERR_UNSAFE_REDIRECT`.
2. Android `ShouldOverrideUrlLoading` on the new URL.
3. `ChildProcessSecurityPolicyImpl::CanRedirectToURL` (blocks `javascript:` etc.) → `ERR_ABORTED`.
4. Renderer-initiated: `CanRequestURL` for the source process.
5. `CheckContentSecurityPolicy(has_followed_redirect=true, ...)` (CSP re-evaluated per hop).
6. `throttle_registry_->ProcessNavigationEvent(kWillRedirectRequest)` (`WillRedirectRequest`).
7. New loader created for the redirect target; loop back to §1.3.

### 1.5 Response — `NavigationRequest::OnResponseStarted` (`:4941`)

1. `loader_.reset()`; download detection (`is_download_ = is_download && download_policy().IsDownloadAllowed()`).
2. State → `WILL_PROCESS_RESPONSE`; prefetch-activation beacon validation (must be
   browser-flagged prefetch + same-origin endpoint; anti-spoofing).
3. `ssl_info_` / `auth_challenge_info_` captured from response head.
4. Early-Hints (`103 Early Hints` → `early_hints_manager_` preloads resources ahead of final response).
5. `CheckCSPEmbeddedEnforcement()` → `ERR_BLOCKED_BY_CSP` on block.
6. COOP sanitize + enforce (`coop_status_.EnforceCOOP`).
7. `SelectFrameHostForOnResponseStarted` (process selection / SiteInstance swap).
8. `CheckResponseAdherenceToCoep` (`kCoepFrameResourceNeedsCoepHeader`).
9. `WillProcessResponse()` → throttles vote `kWillProcessResponse`.
10. `ReadyToCommitNavigation` → renderer commit → `DidCommitProvisionalLoad`.

### 1.6 SSL errors — `content/browser/ssl/ssl_error_handler.cc` **[V]**

Tiny state machine: `DenyRequest()` → `CancelSSLRequest(cert_error_)`;
`CancelRequest()` → `CancelSSLRequest(ERR_ABORTED)`; `ContinueRequest()` →
`ContinueSSLRequest()` (only reachable after explicit user interstitial
proceed). Non-overridable (HSTS-pinned) errors never offer Continue.

### 1.7 Subresources (Blink, architecture **[R]**)

`HTMLPreloadScanner` discovers script/css/img ahead of the parser; each fetch
goes through the same `URLLoader` + `CORP/ORB` gates, then CSP directive
evaluation (`ContentSecurityPolicy`), SRI digest compare (`SubresourceIntegrity`),
mixed-content classify (blockable: XHR/fetch/fonts → block; optionally-blockable
img/video → upgrade-or-block). Trusted Types checked at DOM XSS sinks.

---

## 2. WebKit — loader state machine, cache, enforcement

Checkout scope: `Source/WebCore/loader/` present; `html/`, `page/csp/`,
`platform/network/` absent — CSP engine bodies and preload scanner are **[R]**,
all loader call-sites **[V]** (`FrameLoader.cpp`, `DocumentLoader.cpp`,
`CachedResourceLoader.cpp`, `SubresourceIntegrity.cpp`, `MixedContentChecker.cpp`
names verified present in `loader/`).

### 2.1 Navigation — `FrameLoader::loadWithNavigationAction` (:1891) → `loadWithDocumentLoader` (:1974)

1. `javascript:` URL → `executeJavaScriptURL` (with `allowJavaScriptURLs` CSP gate); else create `DocumentLoader`, attach triggering action.
2. Same-document fragment → synchronous policy check → `continueFragmentScrollAfterNavigationPolicy`.
3. Else `setPolicyDocumentLoader` → async `PolicyChecker::checkNavigationPolicy`
   (dedup by last-checked request; `frame-ancestors` for subframes; content-filter) →
   `continueLoadAfterNavigationPolicy` (:4304): `beforeunload` → stop loaders →
   state `Provisional` → back-forward-cache hit? restore : `prepareForLoadStart` →
   `startLoadingMainResource`.

### 2.2 Main resource — `DocumentLoader::startLoadingMainResource` (:2200) → `loadMainResource` (:2303)

Service-worker match → substitute data → else `CachedResourceLoader::requestMainResource`.
Redirect path `willSendRequest` (:678) exact order: form-action CSP → 3rd-party
iframe redirect block → scheme rejection (about:/data:) → `canDisplay` → port
allowlist → IP allowlist (`isIPAddressDisallowed`, SSRF guard) → cookie
first-party/same-site update → POST-after-redirect cache downgrade →
**subframe mixed-content block** → content-filter → async navigation-policy re-check.
Response path `responseReceived` (:935/:1007): CSP header ingest (clears policy
if no header and not multipart) → COOP → Integrity-Policy → Clear-Site-Data →
ephemeral `allowFrameAncestors` else `X-Frame-Options` (`DENY/SAMEORIGIN`
vs top+ancestors) → MIME-type policy (`canShowMIMEType`, no sniff-then-execute:
`cannotShowMIMEType` on mismatch) → null-body statuses (204/205/304) abort →
`commitIfReady` → `FrameLoader::commitProvisionalLoad` (:2526) → history update
per `FrameLoadType` → state `CommittedPage`.

### 2.3 Cache — `CachedResourceLoader::requestResource` (:1133) → `determineRevalidationPolicy` (:1521)

`Load / Revalidate / Reload / Use` decision precedence (verified order):
no-entry→Load; `DoNotUseAnyCache`→Load; refresh→Reload; SW mismatch→Reload;
in-flight preload→Use; type/vary/charset mismatch→Reload; non-reusable
main/media→Reload; `ForceCache/OnlyIfCached`→Use; preloaded→Use;
history-navigation→Use (except no-store main); cached no-store→Reload;
credentials mismatch→Reload; same-document validated→Use; error/loading→Reload;
else validator present? Revalidate : Reload. Memory cache keyed
`(url-minus-fragment, network-partition)`; `ETag/Last-Modified` revalidation;
live/dead LRU with separate capacities.

### 2.4 Enforcement edges (call-sites **[V]**)

- `allowedByContentSecurityPolicy` (:512): per destination — Script/XSLT→script-src
  (+nonce), CSS→style-src, Image→img-src, Font→font-src, Connect→connect-src,
  Prefetch→prefetch-src; `canRequest` (:610) order: `canDisplay` → CORS mode →
  CSP → **integrity-policy** → SVG-chrome data-only → mixed-content **last**
  (so CSP suppresses the mixed-content warning).
- `SubresourceIntegrity.cpp`: space-split `alg-base64`, strongest-of-set
  (`SHA512>SHA384>SHA256`), empty-metadata fast-path allow; enforced in
  `DocumentThreadableLoader::didFinishLoading` (:497) with callback suppression
  + `reportIntegrityMetadataError`.
- `MixedContentChecker`: `shouldUpgradeInsecureContent` (rewrite when
  target-loopback-safe, image/audio/video only) else `shouldBlockRequest`
  (secure doc + http target + public address space).
- `Upgrade-Insecure-Requests:1` stamped on Document/Iframe requests
  (`requestResource` :1169).

---

## 3. DuckDuckGo Android — exact interception chain

Files **[V]**: `BrowserWebViewClient.kt`, `WebViewRequestInterceptor.kt`.

### 3.1 Navigation decision — `shouldOverride(view, url, isForMainFrame, isRedirect, hasGesture)`

1. Gesture anchoring (`mainFrameGestureOriginUrl`) for redirect chains.
2. `requestInterceptor.shouldOverrideUrlLoading(...)` → malicious-site check
   (block → interstitial, may wait for confirmation).
3. DoS-URL detector → `ABOUT_BLANK`.
4. Special-URL routing (`SpecialUrlDetector.determineType`): subscription links,
   `mailto:`/`tel:`/`sms:` intents, app-links (gesture-gated), Duck Chat,
   Duck Player (redirects re-forced through `shouldInterceptRequest` so the
   YouTube URL is seen), email/phone handlers.

### 3.2 Per-resource pipeline — `WebViewRequestInterceptor.shouldIntercept` (:144)

Exact order, each `return WebResourceResponse(null,null,null)` = block:

1. Malicious-site check (position controlled by `checkMaliciousAfterHttpsUpgrade`
   flag: before upgrade when false, after when true) — async confirmation
   callback, `WaitForConfirmation/Ignored` = allow.
2. `requestFilterer.shouldFilterOutRequest` (ad/known-bad patterns).
3. Ad-click detection (`adClickManager.detectAdClick` — attribution, not blocking).
4. User-agent override reload loop.
5. `appUrlPixel` passthrough.
6. **HTTPS upgrade**: `shouldUpgrade` = main-frame + `httpsUpgrader.shouldUpgrade(url)`
   → `webView.loadUrl(https…)` + original blocked + upgrade counter++.
7. Duck Player interception (YouTube → privacy player).
8. GPC (Global Privacy Control) header reload loop.
9. `TrustedSites` bypass; record `pageHasHttpResources` for http subresources.
10. **Blocklist**: `requestBlocklist.containedInBlocklist(document, url)` for
    non-main-frame → blocked UNLESS content-blocking exception OR tracker
    allowlist OR user allowlist.
11. `getWebResourceResponse` (:348): `trackerDetector.evaluate(url, document,
    checkFirstParty, headers)` (Tracker Radar entity graph: third-party +
    known-tracker-domain + same-entity check) → `BLOCKED` → surrogate swap
    (`resourceSurrogates`: serve local stub JS so pages don't break) else empty
    block; `ALLOWED` → second pass with CNAME-uncloaked host
    (`cloakedCnameDetector.detectCnameCloakedHost`) to catch CNAME cloaking.

### 3.3 TLS / errors — `BrowserWebViewClient`

- `onReceivedSslError`: `SSL_UNTRUSTED` → `trustedCertificateStore.validateSslCertificateChain`
  → TrustedChain? `handler.proceed()` : interstitial (`UNTRUSTED_HOST/EXPIRED/WRONG_HOST/GENERIC` mapping).
- `onReceivedError`/`onReceivedHttpError`: error-page routing (incl. malicious-site
  token-protected `duck://error` pages); `onRenderProcessGone`: crash pixel +
  renderer recovery.
- `onPageStarted` → `requestFilterer.registerOnPageCreated` + malicious-site
  `onPageLoadStarted`; per-request logging of interception decisions.

## 4. DuckDuckGo macOS — responder-chain design

Framework: DDG `Navigation` package — ordered responder chain, each responder
returns `.allow / .cancel / .redirect / .next / .download` from
`decidePolicy(for navigationAction:)` and `decidePolicy(for navigationResponse:)`.

Verified chain links (**[V]** file:line for the hooks):

- `Tab.decidePolicy(for navigationAction:)` (`Tab/Model/Tab.swift:1200`):
  file URLs + `about:blank` → allow; basic-auth credential URLs → strip +
  redirect-without-credentials; main-frame → per-URL user agent; else `.next`.
- `HTTPSUpgradeTabExtension` (`:41`): main-frame only → `.next` or
  `.redirectInvalidatingBackItemIfNeeded` (redirect + pop the wrong history item —
  client-redirect back-list hygiene).
- `NavigationProtectionTabExtension` (`:64`): async query-parameter stripping /
  tracker protection; `Task.isCancelled → .cancel`; cancellations use delayed
  `navigationDidFinish` semantics.
- `ContentBlockingTabExtension` (`:101`): first-party vs tracker-list decision
  point (`.next` = allow through to WebKit content-blocker compiled rules in
  `WKWebViewConfiguration`).
- `RedirectNavigationResponder`, `ExternalAppSchemeHandler`,
  `SerpHeadersNavigationResponder`, `SearchNonexistentDomainNavigationResponder`
  (`Tab/Navigation/`): scheme handling, SERP header injection, NXDOMAIN search
  fallback.
- Response side `Tab.decidePolicy(for navigationResponse:)` → `.next` after
  internal-user marking; `willStart` rewrites non-blank `about:` URLs to
  `duck://` internal pages; `navigationDidFinish` fires retention/statistics
  pixels; failures route to error pages unless already handled or a malicious-site
  token redirect.

Design note: policy is a **chain of small async voters**, not one switch —
matches how our engine splits fetch / security / CSP / cookie / safebrowsing
into composable stages.

## 5. Tor — consensus → guards → circuits → streams

Checkout: `research_repos/tor` (`src/core`, `src/feature`) **[R]** (subagent
file:line survey, no build).

### 5.1 Consensus (`feature/nodelist/networkstatus.c`, `feature/dirclient/`, `feature/dirparse/`)

1. URL: `GET /tor/status-vote/current/consensus[-microdesc]/<fpr3>+…​.z`
   (3-byte authority fingerprints, sorted) with `If-Modified-Since`.
2. `handle_response_fetch_consensus` (:2226): non-200 → `networkstatus_consensus_download_failed`;
   diff → `consensus_diff_apply` else direct body.
3. `networkstatus_set_current_consensus` (:1934): parse vote tokens
   (`valid-after/fresh-until/valid-until/voting-delay/dir-source/r/s/a/v/w/m/pr`),
   flavor check, dedup by digest, reject stale/expired (`valid_after` monotonic,
   `valid_until` vs `now - OLD_ROUTER_DESC_MAX_AGE`).
4. `networkstatus_check_consensus_signature` (:518): need `n_v3_authorities/2+1`
   valid RSA signatures over the signed prefix (`network-status-version…` →
   `directory-signature`) with `SHA1-20B` or `SHA256/SHA3`; results
   `1=all, 0=quorum, -1=needs-certs, -2=never`; missing certs held in
   `consensus_waiting_for_certs` pending `authority_certs_fetch_missing`.
5. Freshness window (`NETWORKSTATUS_ALLOW_SKEW 24h`), required-protocol check,
   persist `cached-[microdesc-]consensus`, fan out to routerlist/microdescs.

### 5.2 Guards (`feature/client/entrynodes.c`, prop271)

`SAMPLED → FILTERED/USABLE_FILTERED → CONFIRMED → PRIMARY(3)`, ordered by
`confirmed_idx`. Expansion is bandwidth-weighted
(`node_sl_choose_by_bandwidth` + guardfraction weights). Pick order:
reachable PRIMARY (uniform among usable) → CONFIRMED non-pending →
FILTERED in sample order → mark-all-maybe-reachable → fail. Success sets
`is_reachable=YES/confirmed`; failure sets `NO/failing_since` + path-bias
accounting (`circpathbias.c`).

### 5.3 Circuits (`core/or/circuitbuild.c`, `command.c`, `onion.c`, trunnel)

`circuit_establish_circuit` → `origin_circuit_init` → `onion_pick_cpath_exit`
(`DEFAULT_ROUTE_LEN=3`, family/subnet exclusion, `cpath_append_hop` with
`package/deliver_window`) → `circuit_handle_first_hop` (channel or
`channel_connect_for_circuit`) → `circuit_send_next_onion_skin` loop.
Always `CREATE2/EXTEND2` (TAP/`CREATE` rejected); handshake `NTOR_V3` when
congestion-control negotiated else `NTOR`; `CREATE_FAST` only for bootstrap
one-hop. Skin dispatch: first hop `circuit_send_first_onion_skin`
(`onion_skin_create` → `circuit_deliver_create_cell`, `CPATH_AWAITING_KEYS`);
later hops `circuit_send_intermediate_onion_skin` (`extend_cell_format`,
`LS_IPV4/LS_IPV6/LS_LEGACY_ID/LS_ED25519_ID` identity legs). Reply:
`command_process_created_cell` → `circuit_finish_handshake`
(`onion_skin_client_handshake` → `cpath_init_circuit_crypto`) → next skin →
`circuit_build_no_more_hops` → `OPEN`. Relay side: `command_process_create_cell`
→ DoS check → `onion_pending_add` → cpuworker `onion_skin_server_handshake` →
`CELL_CREATED2`; `EXTENDED2` → next skin; `circuit_extend_by_extend_cell`
decrypts EXTEND2 → downstream CREATE2.

### 5.4 ntor (`core/crypto/onion_ntor.c`, `onion_crypto.c`)

`PROTOID="ntor-curve25519-sha256-1"`, skin `ID(20)||B(32)||X(32)`, reply
`Y(32)||MAC(32)`. `secret_input = EXP(X,y)||EXP(X,b)||ID||B||X||Y||PROTOID`
(X25519); `verify=H(secret)`; `auth=HMAC(secret, verify||ID||B||Y||X||PROTOID||"Server")`;
keys = `HKDF-SHA256(secret, salt=t_key, info=m_expand)`. Junk-key lookup on
identity miss (timing-safe); zero-output fail. v3 variant:
`ntor3-curve25519-sha3_256-1` + ed25519 identity + subprotocol negotiation.

### 5.5 Relay crypto (`core/crypto/relay_crypto*.c`, `core/or/relay.c`, `crypt_path.c`)

Tor1 default: 72B material (`f_digest|b_digest|f_crypto|b_crypto` =
SHA1/SHA1/AES-128-CTR/AES-128-CTR). Outbound: innermost (exit) sets
`recognized=0,digest` at offset 5, then each outer layer AES-encrypts.
Inbound: peel AES per hop, first `recognized==0` + SHA1-digest match wins
(checkpoint/restore on miss; save `sendme_digest` on hit). CGO variant uses
explicit tags instead of SHA digests.

### 5.6 Streams (`core/or/connection_edge.c`)

Client `connection_ap_handshake_send_begin` (:3239): unique stream id,
`host:port\0[flags]` (`BEGIN_FLAG_IPV6_OK/PREFERRED/IPV4_NOT_OK`),
`assert_circ_anonymity_ok`, onion-encrypt BEGIN, windows `STREAMWINDOW_START=500`
/ `CIRCUITWINDOW_START=1000`. Exit `connection_exit_begin_conn` (:3978):
NUL/port validation → refuse single-hop → `IPv6Exit`/begindir policy →
`dns_resolve` → nonblocking TCP → `CONNECTED` or `END_STREAM_REASON_*`.
DATA: window accounting → buffer → `SENDME` flow control both directions.

---

## 6. What this means for our engine (mapping)

| Their stage | Our native equivalent | Status |
|---|---|---|
| Chromium pre-network CSP/credential/allowlist gates | `run_scripts` CSP + SRI + safebrowsing denylist before fetch | done |
| URLLoader CORP-on-redirect, credential stripping | mixed-content + cookie policy on redirect chain | done |
| SSL interstitial proceed/deny state machine | curl_cffi/httpx TLS (no interstitial) | documented gap |
| WebKit revalidation precedence | no HTTP cache at all | documented gap |
| WebKit X-Frame-Options/SAMEORIGIN, COOP/COEP | frames fetched as separate docs, no enforcement | documented gap |
| DDG responder chain | our staged fetch→parse→scripts→security pipeline mirrors it | done |
| DDG tracker Radar + surrogates | no tracker graph; env denylist only | documented gap |
| Tor circuit crypto | out of scope (transport privacy, not page semantics) | n/a |
