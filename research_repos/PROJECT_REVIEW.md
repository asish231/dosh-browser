# Browser review: confirmed issues and remaining verification

Date: 2026-10-10

## Scope and governing instructions

This report reconsiders the earlier review after reading the applicable
[`../AGENTS.md`](../AGENTS.md) in full. No `agent.md` or `AGENTS.md` was found
at this directory's root; the parent `AGENTS.md` governs this work.

The product is a **from-scratch browser**, using owned HTTP transport,
QuickJS, mini-DOM, and native events. The repositories in this directory are
**reading material only**. They must not be imported, executed, built, linked,
or used as fallback browsers. Chromium, Playwright, Puppeteer, Selenium,
rebrowser, and CDP integration are prohibited (`../AGENTS.md:5–58`).

Evidence here comes from the earlier targeted source review, the supplied
research documents, and the agent instructions. This is **not an exhaustive
audit of every upstream file or the current `../webresearch` runtime**.
No applications, upstream code, or tests were executed for this report.

## Revised conclusion

The project does not need more indiscriminate cookie, token, or human-behavior
collection. It needs trustworthy evidence that its **owned browser** preserves
authorized session state, implements required web semantics, enforces security
boundaries, and reports unsupported actions honestly.

The earlier suggestion of a missing integration layer needs qualification:
**integration with these reference libraries is not a requirement**. Any needed
capability must be implemented natively, not connected through CDP or a browser
binary. Upstream defects below are research findings, not demonstrated product
vulnerabilities.

## Confirmed findings in the reference material

| Finding | Evidence | Meaning for this product |
| --- | --- | --- |
| `nodriver` cookie export ignores its filter: it builds `included_cookies` but serializes `cookies`. | `nodriver/nodriver/core/browser.py:743–755` | A concrete upstream bug; not proof the owned runtime exports cookies incorrectly. |
| `nodriver` logs cookie values during save/load. | Same file, `:747–751`, `:792–796` | Sensitive sessions can enter debug logs. Native code should exclude secret values from logs. |
| `nodriver` loads sessions using `pickle.load`. | Same file, `:779` | Untrusted files can execute code. Do not adopt this persistence pattern. |
| `browser-use` periodic state-change detection compares cookie name/domain/path/value only. | `browser-use/browser_use/browser/watchdogs/storage_state_watchdog.py:119–153` | Storage-only and cookie-attribute-only changes do not trigger this monitor's save path. |
| The inspected `browser-use` snapshot captures cookies and local/session storage, not `IndexedDB`. | `browser-use/browser_use/browser/session.py:3576–3662` | A session snapshot is not a complete browser-profile backup. |
| The inspected state writer stores ordinary JSON, including a backup file. | `browser-use/browser_use/browser/watchdogs/storage_state_watchdog.py:194–204` | File access, backups, deletion, and encryption need explicit protection; this path has no encryption step. |
| Research status documents contradict one another and themselves. | `browser-loading/BROWSER_RESEARCH.md:115–137,278–294`; `browser-loading/ALGORITHMS.md:354–363` | Implementation status cannot be reliably inferred from these documents alone. |

For example, `BROWSER_RESEARCH.md` marks layout, persistent HSTS, service-worker
interception, background fetch, and CT verification implemented, then later
lists them as absent or pending. `ALGORITHMS.md` describes frame-policy
enforcement as missing while the other document marks CORP/COEP/COOP enforced.
These contradictions are confirmed; which runtime claims are current is not.

## Native limitations documented by the agent instructions

| Area | Documented state | What remains |
| --- | --- | --- |
| Layout | Real block-flow layout exists; no floats, position, flex, or grid; limited units and one font metric. | Broader CSS compatibility and truthful disclosure through `document.__layoutInfo`. Do not call all geometry missing. |
| Cross-origin frames | Frames are separate documents without a real cross-frame `postMessage` handshake. | Native frame lifecycle/message semantics and origin enforcement where required. |
| Canvas and iframe pixels | Never reach the agent; decomposed at parse time. | Represent unsupported pixel-dependent controls and their requirements; do not fabricate success. |
| Input trust | Agent dispatches are marked trusted internally; programmatic dispatch is not. No trusted CDP input exists or may be added. | Validate native event semantics and clearly distinguish internal trust from OS/browser input or a website accepting an action. |

Source: `../AGENTS.md:115–128`. The no-spoofing rule at `:51–53` also applies:
an internal `isTrusted` flag alone is not evidence of real human input,
transient user activation, permission approval, or successful authentication.

## Cookies and tokens: verification checklist, not confirmed absences

Inspect the owned transport and runtime before declaring any of these missing:

- Per-profile, per-origin, and partitioned-storage isolation; private-session
  disposal; logout and complete authorized site-data deletion.
- Cookie domain/path/expiry handling, `HttpOnly`, `Secure`, SameSite,
  partition keys, and prefix rules, including cross-origin redirect behavior.
  Research notes already claim some of these exist; verify rather than assume.
- Correct changes/restoration across supported storage types. Treat
  `sessionStorage` according to its browsing-context lifetime, not as globally
  reusable state. Check whether target applications require `IndexedDB`.
- Safe persistence, restrictive access, secret-free logs, bounded backups,
  corruption handling, and crash-safe writes. Inspect deletion/merge behavior
  so removed credentials cannot reappear from stale snapshots.
- Application-specific refresh/revocation only where an authorized integration
  requires it. A general browser need not implement a universal token-refresh
  service or extract tokens into a separate collector.

Cookies and bearer tokens are credentials, not behavioral telemetry. Store only
what the authorized browsing workflow needs; do not include them in analytics.

## Human interaction and behavioral statistics

Existing reference capabilities must not be confused with a missing product
collector. DuckDuckGo has selected usage/error events; `browser-use` has agent
loop detection and DOM/action fingerprints. These serve different purposes:

- `android/.claude/docs/pixels.md:3–10` describes telemetry constraints against
  PII, URLs, correlation IDs, and exact numeric values.
- `browser-use/browser_use/agent/views.py:95–107,151–199` tracks repeated actions
  and page state; it is not a human-behavior recording system.
- `browser-use/browser_use/actor/mouse.py:44–60,102–124` and
  `nodriver/nodriver/core/tab.py:1922–1985` provide input primitives, not evidence
  of real human input. They remain reference material only.

For the owned engine, prioritize focus/blur order, keyboard composition,
selection, pointer coordinates, drag/drop, default actions, cancellation,
bubbling, and user-activation semantics. Measure observable DOM/state changes
and failure outcomes—not merely whether an event was emitted.

If consented usability statistics are actually required, first define purpose,
minimal event schema, retention, deletion, access controls, and sensitive-field
exclusion. Raw keystrokes, form contents, tokens, and browsing history are not
default requirements. Human-looking trajectories or timing do not establish
compatibility or justify bypassing site challenges.

## Handshakes and hashes: separate requirements

| Mechanism | Purpose | Review conclusion |
| --- | --- | --- |
| Website TLS | Authenticated encrypted transport, certificate checks, protocol negotiation. | Verify the owned transport's validation, hostname checks, failure policy, and available diagnostics. CDP observations do not establish native support. |
| Tor circuit handshake | Onion-routing circuit key establishment. | Not a web-engine feature; Tor core is not Tor Browser. Any privacy-transport claim needs separate verification. |
| Frame `postMessage` | Communication between document contexts. | Explicit native gap in `AGENTS.md`; unrelated to TLS. |
| SRI and CSP hashes | Subresource integrity and permitted script/style execution. | Research notes claim support; require native tests for accepted and rejected cases. |
| DOM/action hashes | Stable references or repeated-action detection. | Not credential protection, TLS fingerprints, or security signatures. |
| Certificate fingerprints | Identification/diagnostics. | Hashing a certificate is not validating its chain or hostname. |

`nodriver/nodriver/cdp/network.py:240–264,565–615` exposes handshake timing and
security metadata, and `browser-use/browser_use/browser/watchdogs/har_recording_watchdog.py:355–362`
copies security details. Those reference APIs are not permitted implementation
paths. Diagnostic records should contain minimal negotiated metadata and error
categories, never session credentials or TLS secrets.

Correction to the older research notes: TLS to a website is end-to-end between
the client and the origin through Tor's transport, not TLS terminating at the
exit. Tor's circuit key exchange is also distinct from its relay TLS links.

## Prioritized next work

1. **Reconcile capability evidence.** Maintain one matrix with capability,
   native implementation path, integration point, hermetic test, result,
   limitations, and verification date. Mark unresolved claims unverified.
2. **Audit owned session security.** Check the cookie/storage/redirect and
   persistence checklist above. Do not fix reference repositories as a
   substitute for verifying the product.
3. **Validate conversion and action honesty.** Preserve every control, stable
   reference, visible label, explicit action kind, routing, and unsupported
   reason; disclose budget omissions (`../AGENTS.md:60–106`).
4. **Extend native compatibility where target pages require it.** Prioritize
   evidenced frame, input, layout, and storage failures. Add hermetic regression
   tests using real captured markup; assert specific resulting element state,
   not a substring somewhere in the page.
5. **Add telemetry only with a defined need.** Keep operational diagnostics
   separate from optional consented usability measurements and credentials.

## Verification and limits

- Read the applicable parent `AGENTS.md` completely and reconsidered the prior
  source findings under its native-only architecture.
- Reused earlier targeted source-review evidence; did not repeat the upstream
  investigations or claim a whole-project audit.
- Created this report only. No runtime or reference source was changed.
- Build/tests are not applicable to this documentation-only change. Current
  native implementation status and runtime correctness remain unverified.