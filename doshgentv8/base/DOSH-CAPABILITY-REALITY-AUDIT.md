# DOSH Capability Reality Audit and Work Backlog

**Status:** Static implementation audit; companion to `DOSH-BROWSER-REPO-RESEARCH.md`.

**Purpose:** Distinguish real capabilities from partial implementations, facades, disconnected scaffolding, unsafe contracts, duplicated authority, and documentation overstatement.

**Evidence limit:** No builds, tests, servers, or network calls were run. “Works” below means a substantive implementation path exists in source, not that runtime verification passed.

---

## 1. Is DoshShell really a shell?

### Precise answer

DoshShell is a real interactive/batch **command parser and dispatcher** with 45 registered command names. It is not intended to be a POSIX shell and does not need pipes, subprocess management, or Unix syntax.

As an **agent browser terminal**, it is currently unreliable because commands do not share one truthful live-session control plane:

- some operate on the resident actor;
- some create a fresh actor and lose live state;
- some read a different ledger/store from the one populated by the browser;
- some return hardcoded values;
- some discard the actual action/script result;
- some return partial data under a broader name;
- some are placeholders.

Therefore the correct assessment is:

> Real command-dispatch code and several real capabilities, but not yet a trustworthy agent shell. Command count is not capability proof.

### Real capabilities worth preserving

- Owned navigation and inline JavaScript execution through a real V8 embedding.
- Resident isolate/mini-DOM state across selected operations.
- HTML parsing, ACD/content/action projections, links, forms, provenance.
- Search and download implementations.
- SQLite skill/snapshot/checkpoint CRUD, persistent only when configured with a database.
- Skill origin/hash/warrant data model.
- Local interactivity classifier and structured projections.
- Real watchdog implementation in the engine crate, although the product does not arm it.
- Python companion’s richer frames, WebSockets, CAPTCHA, login/OTP, profiles, and model loop.

The shell should be rebuilt as a thin projection over these capabilities, not treated as the browser architecture itself.

---

## 2. Shell command-by-command reality

Primary evidence: `dosh-browser-core/src/shell.rs`, `session.rs`, `runtime.rs`, `actions.rs`, `identity.rs`, `doshgentv8/core/src/{lib.rs,actor.rs,protocol.rs,watchdog.rs}`.

Status meanings:

- **REAL:** substantive implementation matching the command’s core purpose, with limitations noted.
- **PARTIAL:** useful implementation exists but contract or live-state integration is incomplete.
- **FACADE:** returns illustrative/static/empty data or a success-shaped result without doing the claimed work.
- **UNSAFE:** correctness, lifetime, credential, isolation, or containment risk requiring priority correction.

| Command | Status | What it actually does / missing behavior |
|---|---|---|
| `help` | REAL | Lists command metadata. It does not prove each command is implemented. |
| `product` | PARTIAL | Reports configured lane/product status, not proof of the actual linked engine/archive or runtime backend. |
| `status` | PARTIAL | Reports session/tab fields; not a complete live actor, network-capture, deadline, or capability state. |
| `open` | PARTIAL | Real NetworkEngine navigation and resident inline mini-DOM execution. External scripts/modules, frames, and full document lifecycle are incomplete. |
| `actions` | PARTIAL | Real inventory heuristics, but emitted refs are not a reliable live-node execution contract and total/coverage can be calculated after capping. |
| `act` | UNSAFE/PARTIAL | Click/type paths exist. Hash refs can be passed as selectors, evaluation outcomes are discarded, unsupported controls can look successful, and documented `dx`/`dy`/stealth behavior is not fully honored. |
| `net` | FACADE/PARTIAL | Real ledger primitives exist, but shell reads the session ledger while actual NetworkEngine records go elsewhere. It can omit the requests it claims to query. |
| `net.capture` | PARTIAL | Changes capture configuration on a ledger that is not the universal populated engine ledger; does not prove body capture for real traffic. |
| `net.rule` | FACADE/PARTIAL | Rule storage/matching code exists, but the central production fetch path does not consistently consult the shell’s ledger/rules. |
| `net.unrule` | FACADE/PARTIAL | Removes rules from the same disconnected rule store; not proof that active network behavior changes. |
| `net.apis` | PARTIAL | API discovery implementation exists, but depends on complete records reaching the queried ledger. It cannot be a reliable Site-as-SDK catalog until transport/evidence is unified. |
| `console` | FACADE/PARTIAL | Runtime captures console data in its seam, but session console logs are not consistently populated. Shell output can be empty despite page logs. |
| `eval` | PARTIAL/UNSAFE | Executes real JS, but shell creates a fresh runtime in the reviewed path. It is not evaluation in the live tab realm, and the control-realm wrapper is not a security boundary. |
| `skill.save` | REAL/PARTIAL | Real source/name/origin/hash/action/warrant persistence. Persistence is in-memory unless a DB path is supplied. |
| `skill.list` | REAL | Lists saved skills from `ShellStore`; does not perform semantic skill discovery. |
| `skill.run` | PARTIAL/UNSAFE | Real stored-source execution machinery, but uses a separate runtime rather than reliably operating on the live tab; authority/warrant semantics need fresh host enforcement. |
| `skill.delete` | REAL | Deletes stored skill records. |
| `skill.update` | REAL | Updates warrant expiry/version data; does not by itself prove invocation policy. |
| `db` | REAL/PARTIAL | Read-only SQL over shell stores. It is not a unified browser/session/event database and defaults to in-memory in server/MCP paths. |
| `storage` | PARTIAL | Returns limited storage keys/metadata. Does not expose complete local/session/IndexedDB state or consistently drain live runtime storage; `values=true` is not fully honored in Rust shell. |
| `snapshot` | PARTIAL | Saves URL/action evidence. It is not a complete redacted DOM snapshot and is explicitly not a VM checkpoint. |
| `diff` | FACADE | Compares whether snapshot URLs differ; it is not DOM, ACD, control, or content diffing. |
| `checkpoint` | PARTIAL | Writes HTML/current-tab fields/history/storage to SQLite when persistent. Cookies are actually headers; runtime/clock/PRNG/gates/all tabs are not captured. |
| `restore` | UNSAFE/PARTIAL | Restores selected stored fields but does not restore/rebuild the live actor correctly. “Session rehydrated” overstates the operation. |
| `layers` | PARTIAL | Computes approximate layout strata, but creates a fresh runtime in shell. Not live-realm inspection and not authoritative hit testing. |
| `frames` | FACADE/PARTIAL | Returns discovered frame seeds rather than loaded live frame realms/message counts. No Python-equivalent live frame architecture in Rust. |
| `tabs` | PARTIAL | Lists/switches existing tabs. No complete create/close/lease/eviction lifecycle. |
| `video` | PARTIAL/UNSAFE | Real yt-dlp/ffmpeg subprocess integration, but returned temporary-file lifetime/destination handling and session-cookie behavior have defects. Not a pure-Rust video implementation. |
| `read` | PARTIAL | Real reading projection exists. Fidelity, content metadata, and truncation behavior vary by lane; some byte slicing paths can panic on UTF-8 boundaries. |
| `history` | PARTIAL | Stored document history exists. Traversal does not consistently restore the live actor/realm, and same-document actions can create excessive entries. |
| `cookies` | FACADE/PARTIAL | Uses response-header names in the reviewed Rust shell rather than a real cookie jar with name/domain/path metadata. Runtime/network cookies are not unified. |
| `wait` | FACADE | Performs an instantaneous check and echoes timeout-related data rather than providing timeout-bounded polling/network-idle behavior. |
| `caps` | PARTIAL | Generates a manifest from available page/configuration data. It is not proof that every reported engine capability is live or tested. |
| `screenshot` | PARTIAL | Produces an approximate SVG layout diagram, not pixels; shell uses a fresh runtime and frame selection is not a real frame-realm operation. |
| `frame.eval` | FACADE/UNSAFE | Creates a fresh top-level runtime and ignores the selected frame. It is not live frame evaluation. |
| `ref` | PARTIAL | Attempts stable-ID resolution, but action and identity trackers use incompatible identities; collisions/generation semantics remain. |
| `trace` | FACADE/PARTIAL | Produces before/after action counts and empty network/causal data. No action ID is propagated through DOM/network/console events. |
| `watch` | FACADE | Rust shell returns empty records rather than a real MutationObserver subscription stream. |
| `solve_captcha` | REAL/PARTIAL | Honest inspection-only Rust probe. Actual reCAPTCHA solving is in the Python lane; do not describe inspection as solving. |
| `login` | UNSAFE/PARTIAL | Generates and runs a Python login script with substituted credentials, does not merge resulting session state into Rust, and can leave plaintext credentials in a predictable temporary source file. |
| `wasm` | FACADE/PARTIAL | Returns configured fuel plus hardcoded active-instance/Liftoff/patch data. Current cooperative fuel is not automatic Liftoff execution metering. |
| `clock` | PARTIAL | Stores session configuration and JS overlay overrides. Not a complete native clock, not always applied live, and responses can imply a native patch that is not linked. |
| `rng` | PARTIAL | Real seeded JS `Math.random` overlay, but reset/live propagation is incomplete and it is not a named per-actor random-stream system. |
| `heap` | FACADE | Returns constants for limit/semi-space/GC/compiler state rather than `get_heap_stats` from the current actor. |
| `gate` | PARTIAL/UNSAFE | Changes configuration and JS overlay gates. It is not a complete security capability boundary for eval, WASM, direct DOM mutation, or previously captured references. |

### Command-surface conclusion

Only a subset should currently be presented as reliable agent primitives. The rest need either implementation completion or an honest `partial`, `unsupported`, or `unavailable` response.

A command returning JSON is not evidence that the operation happened.

---

## 3. The deeper problem: duplicated sources of truth

The largest architectural “slop” is not any single stub. It is multiple authorities for the same state.

| State | Competing representations | Result |
|---|---|---|
| DOM/page | `tab.html`, live mini-DOM, Parena, stored history HTML | Restore/fork/action can operate on different documents |
| Element identity | `_stable_hash` action refs versus `IdentityTracker` `elem-*` IDs | Public ref cannot reliably execute; duplicate schemes diverge |
| Engine configuration | `Session` clock/PRNG/fuel/gates versus live actor state | Shell reports configuration, not necessarily engine truth |
| Network evidence | `Session.network_ledger` versus `NetworkEngine.ledger` | Shell queries can miss real traffic |
| Network transport | Central NetworkEngine versus direct read/search/frame/download paths | Policy depends on entry point |
| Tool registry | MCP, OpenAI tools, dispatch, shell, hand-written REST | Advertised and executable capabilities diverge |
| Page result | `session::Page` versus `types::Page` versus view-only MCP text | Clients cannot rely on one schema |
| Checkpoints | Evidence snapshots, SQLite checkpoints, Parena, tab history | “Restore/checkpoint” has incompatible meanings |
| V8 engine | Prebuilt crate, `base/` patches, JS overlays | Documentation can claim native features that are not linked |
| WASM | Stock V8, JS overlay fuel, base Liftoff experiment, Python interpreter/minidom | No single capability or metering truth |
| Agent | Rust local search/read versus Python ephemeral LLM loop | `ask` semantics depend on adapter, not just model choice |
| Persistence | In-memory ShellStore, optional SQLite, Python Profile, tab history | Durability depends on entry command rather than session policy |
| Documentation | README, AGENTS, roadmap completion labels, code | “Complete” has no generated evidence basis |

### Architectural rule required

Every state class must have exactly one authoritative owner:

- live document: actor/document realm;
- action identity: actor-scoped identity table;
- engine configuration: actor query/configuration transaction;
- network requests: policy-aware NetworkEngine;
- evidence: session EventJournal;
- tool operations: BrowserControl registry;
- checkpoint level: versioned manifest;
- provider/model state: external orchestrator;
- memory: canonical run store plus derived indexes.

---

## 4. MCP, REST, OpenAI tools, and dispatch issues

### 4.1 Not one product API

The following surfaces advertise and implement different operations:

- `dosh-browser-core/src/mcp.rs`;
- `dosh-browser-core/src/tools.rs`;
- `dosh-browser-core/src/dispatch.rs`;
- `dosh-browser-core/src/shell.rs`;
- `dosh-browser-core/src/server.rs`;
- corresponding Python files.

Examples found:

- login/OTP advertised without dispatch implementation;
- extract/read/goto/frame-related operations missing from one registry or another;
- `browser_navigate` as MCP-only alias;
- `browser_shell` argument shape and persistence differing by path;
- MCP navigate returning view text while dispatch returns a structured page;
- errors embedded as successful JSON-RPC text rather than consistent tool errors;
- malformed stdio JSON silently ignored in the binary loop.

### 4.2 Global session and tenant problems

- Rust REST and stdio MCP each create one global session.
- Advertised `session_id` is not a routing/isolation boundary.
- Python direct session routes perform tenant checks, but generic `/v1/invoke` does not consistently propagate tenant identity into `Browser.get`.
- Mutexes serialize access but do not provide leases, write ownership, handoff, or eviction safety.

### 4.3 `ask` is three different products

- Rust CLI: local search plus optional first-hit read, `model=dosh-local`.
- Rust HTTP `/v1/ask`: search only.
- Python: actual OpenAI-compatible LLM tool loop with ephemeral browser and fallback.

This must become either one explicitly versioned external orchestrator API or differently named operations.

---

## 5. Engine and execution containment issues

### 5.1 Real engine, incomplete bounds

- V8 execution is real through the prebuilt crate.
- Watchdog termination is real.
- Primary browser does not call `set_time_limit`.
- Microtask draining also needs deadline/error propagation.
- Actor command queues are unbounded and replies block indefinitely.
- Shutdown is not joinable and cannot interrupt a stuck actor by itself.
- No consistent per-operation cancellation/fencing exists.

### 5.2 Fuel claims

- `__dosh_step` is cooperative code that must be called explicitly.
- It is not automatic interpreter bytecode fuel.
- Base Liftoff experimentation is not linked into the current crate archive and does not constitute emitted per-block runtime metering.
- Shell `wasm` responses are not live engine observations.

### 5.3 Security gates

- Namespace hiding is not capability revocation.
- Function wrapping is not realm isolation.
- JS action booleans do not prevent direct DOM writes.
- Existing compiled WASM or saved eval/Function references are not comprehensively covered by namespace gates.
- A future model-supplied eval must be treated as untrusted code, not trusted control-plane syntax.

### 5.4 Heap and memory

- Construction-time V8 heap limits are real.
- Near-heap callback behavior can increase the limit, so this is not a strict total-memory cap.
- V8 heap is not total process RSS: Rust DOM, network buffers, caches, artifacts, snapshots, telemetry, and native allocations need separate budgets/reservations.
- Shell heap values are currently constants.

---

## 6. DCP and State Weave: scaffolding presented too strongly

### DCP issues

- Pure Rust dispatcher, not demonstrated native C++↔Rust protocol.
- Dangling error-string and batch-vector pointers.
- Unjustified raw-pointer `Send`/`Sync`.
- C/Rust opcode and layout drift.
- Eval/fork/telemetry results discarded or reduced to counts.
- No version negotiation, stable buffer release, external transport, or authentication.
- “Zero-copy” layout paths often JSON serialize/parse/clone.

**Disposition:** replace, not extend.

### State Weave / Parena issues

- Production Parena is a placeholder HTML node, not the parsed page.
- Checkpoint capture/restore does not prove JS heap or mini-DOM fidelity.
- Fork clones host vectors and creates a fresh runtime, not an isolate fork.
- SessionPool is not the actual product runtime registry.
- Startup snapshots are real API usage but not wired as actor checkpoints.
- COW/speculation plans should not start before one real document vertical slice works.

**Disposition:** retain as research only with explicit capability labels.

---

## 7. Browser correctness and fidelity backlog

### 7.1 JavaScript/document lifecycle

- External scripts are not fully fetched/executed in Rust.
- Module loader is a stub; modules can be treated like classic scripts.
- Injected external scripts are ignored.
- Async/defer distinctions are simplified.
- Several shell commands create new runtimes, losing closures/listeners/state.
- Navigation outcomes/popups/history/postMessage are not fully propagated.
- Main-thread JS fuel is not automatic.

### 7.2 Actions

- Public refs and runtime selectors use incompatible identity.
- Errors are discarded.
- Drag/drop/upload/select/key/frame/embed behavior is missing or incomplete in Rust `apply_action`.
- Occlusion/layers are approximate and not an action gate.
- ACD reconstructs some action semantics heuristically rather than preserving extraction truth.
- Duplicate controls and stale document versions need robust occurrence/epoch contracts.

### 7.3 Frames

- Discovery/fetch is not a live realm tree.
- No production-equivalent Python MessagePort/window messaging in Rust.
- `frame.eval` ignores frame selection.
- Frame content is not consistently represented in ACD/action state.

### 7.4 Storage/history/cookies

- Runtime storage is not drained into the shell consistently.
- Network cookie jar, runtime cookie map, and shell cookie view diverge.
- Session/local/IndexedDB coverage is incomplete.
- History traversal changes stored HTML without reliably restoring actor state.
- Checkpoint cookie data can actually be response headers with attributes lost.

### 7.5 Network/security

- Alternate fetch lanes bypass central policy.
- Redirect/DNS filtering needs end-to-end verification.
- Cache `Vary`, revalidation, and privacy semantics are incomplete in reviewed paths.
- Some response limits truncate after full-body read.
- CORS preflight method is hardcoded in reviewed code.
- Python NetworkEngine presence does not prove `Session.goto` uses it.
- Search relevance waterfall can accept junk results without a relevance gate.

### 7.6 Text/file robustness

- Several byte-index truncation paths can panic on non-ASCII UTF-8.
- Video result path can reference a dropped temporary directory.
- Non-SHA-256 expected-digest comparison is inconsistent in reviewed download code.
- Some live-network tests are non-hermetic or have vacuous assertions.

---

## 8. Agent runtime and memory gaps

### Missing entirely as durable systems

- run/DAG persistence;
- task leases and retries;
- subagent spawning/cancellation;
- provider/model pinning and auditable routing;
- hierarchical token/cost/tool/browser budgets;
- canonical notes and TODOs;
- long-horizon handoff;
- reconnectable event cursors;
- model/browser idempotency;
- RAG ingestion/index/ACL/retrieval;
- memory contradiction/supersession/expiry;
- multi-agent writer arbitration.

### Existing pieces that can be reused

- Python provider configuration/fallback as adapter reference;
- browser ACD/ANBF/content hashes as context artifacts;
- skills/warrants as procedural memory seed;
- profiles as origin-scoped auth state, not semantic memory;
- telemetry and snapshots as evidence seeds;
- resident actor as future browser session boundary.

### Not memory

- Parena;
- a page HTML string;
- ACD substring search;
- a SQLite skill row by itself;
- a model summary without citations;
- a MCP connection;
- an in-memory `Arc<Mutex<Session>>`.

---

## 9. Test and documentation slop

### 9.1 Tests that prove less than their names imply

- Shell completeness checks count 45 names and exercise only a subset.
- Heap/WASM tests can assert hardcoded responses.
- Rewind tests check HTML, not closures/listeners/heap state.
- Fork tests do not compare live JS state.
- Checkpoint tests use in-memory stores and do not prove process restart.
- Some tests use whole-page substring assertions, which AGENTS explicitly warns against.
- Some tests accept `is_ok() || is_err()`, which is vacuous.
- Native V8 Python tests self-skip when the extension is absent.
- `doshgentv8/test_rust_bridge.py` and `test_integration.py` are script-style programs outside normal pytest collection and contain weak assertion paths.
- No comprehensive shell↔REST↔MCP semantic parity test exists.
- No multi-client isolation, stale lease, duplicate effect, or long-horizon recovery suite exists.

### 9.2 Documentation overstatement patterns

- “Complete” based on files/opcode names rather than vertical integration.
- “Native V8 15.9” implied while Cargo selects a prebuilt different archive.
- “Zero-copy” used for JSON/copy/clone paths.
- “Durable” used for default in-memory stores or no reopen test.
- “Checkpoint/fork” used for HTML/vector copies rather than VM state.
- “45 commands” used as capability proof.
- “Full SPA support” broader than external module/script/frame support.
- “No Python dependency” needs login/video/source-package qualifications.
- Test counts from different dates presented without a generated current ledger.

### Required documentation rule

Every capability gets one state:

- `verified_working` — with test evidence;
- `implemented_partial` — exact limitations;
- `experimental` — research-only;
- `unavailable` — no implementation;
- `proposed` — design only.

No “complete” without a linked hermetic acceptance test.

---

## 10. Prioritized work backlog

### P0: Stop false success and unsafe execution

| ID | Work item | Acceptance evidence |
|---|---|---|
| P0-01 | Replace action ref routing with actor-resolved stable IDs, occurrence, document epoch | Extracted refs click intended nodes; duplicates/stale/missing refs return distinct outcomes |
| P0-02 | Propagate action evaluation results/errors | Missing selector or JS exception cannot produce success-shaped page result |
| P0-03 | Arm watchdog/deadlines on eval, navigation scripts, timers, microtasks | Infinite JS/WASM fixtures terminate and actor can be disposed safely |
| P0-04 | Replace DCP raw-pointer responses with owned values | No dangling error/batch pointers; lifetime tests and owned event results |
| P0-05 | Remove hardcoded heap/WASM/engine responses | Every value is measured from current actor or explicitly unavailable |
| P0-06 | Make live actor configuration transactional | Clock/PRNG/fuel/gates apply to existing actor or return error; reset works |
| P0-07 | Replace plaintext Python login runner | No credentials in temp source; session/cookie bridge or explicit separate-lane result |
| P0-08 | Enforce tenant/session identity in generic dispatch | Cross-tenant `/v1/invoke` and MCP/session access tests fail closed |
| P0-09 | Fix checkpoint restore/runtime invalidation | Post-restore action cannot use stale actor; response states exact restore level |
| P0-10 | Fix unsafe UTF-8/file/digest lifetimes | Unicode/property tests, durable video output, all digest algorithms verified |
| P0-11 | Add network redirect/all-lane policy enforcement | SSRF tests through redirects, frames, downloads, search, XHR |
| P0-12 | Generate truthful engine/capability manifest | Reports actual linked archive/backend and shipping/partial/research status |

### P1: Build one real browser control plane

| ID | Work item | Acceptance evidence |
|---|---|---|
| P1-01 | Add `BrowserControl` and versioned operation registry | One implementation used by shell/MCP/REST/dispatch |
| P1-02 | Add SessionRegistry with lifecycle/capacity/generations | Independent clients cannot leak state; eviction respects leases |
| P1-03 | Add bridge attach/heartbeat/transfer/fencing | Stale writer rejected after reconnect/handoff |
| P1-04 | Add ActionGateway/idempotency | Retry returns original record; `outcome_unknown` is explicit |
| P1-05 | Add durable EventJournal | Action→DOM/network/console events share causality and cursors |
| P1-06 | Unify network ledger and policy-aware transport | `net` shows actual requests; rules affect runtime/navigation as documented |
| P1-07 | Route eval/layers/screenshot/frame.eval through live realm registry | Commands observe current closures/DOM/frame, not a new actor |
| P1-08 | Implement real wait/watch/diff/trace | Timeout polling, mutation stream, content diff, causal bundle tests |
| P1-09 | Repair storage/cookies/history state machine | Live runtime/network/storage state agrees after navigation/restore |
| P1-10 | Define checkpoint levels/manifests | Evidence/document/page/VM/orchestrator states are never conflated |
| P1-11 | Make configured persistence durable by default for servers | Store reopen/process restart recovers advertised state |
| P1-12 | Add actor bounded queues/cancellation/join | Backpressure, late-reply fencing, clean teardown under load |

### P2: Complete browser capabilities

| ID | Work item | Acceptance evidence |
|---|---|---|
| P2-01 | External classic/module script loader | Real captured module graph executes with correct ordering/errors |
| P2-02 | Live frame realm and postMessage manager | Cross-frame action/eval/message tests with identity checks |
| P2-03 | Full action kinds and honest unsupported controls | Drag/drop/upload/key/select outcomes match actual mini-DOM support |
| P2-04 | Storage/IndexedDB/cookie unification | Navigation/history/profile restart preserves scoped state |
| P2-05 | Python/Rust conformance suite | Feature selection cannot silently reduce capability |
| P2-06 | Move mini-DOM to neutral shared asset ownership | Rust source package builds without Python package source dependency |
| P2-07 | Repair DataDome/Chromium-dead paths honestly | No hidden browser lane; detection versus solving clearly reported |
| P2-08 | Consolidate WASM strategy | Backend-specific capabilities and metering are explicit |
| P2-09 | Layout/occlusion/action integration | Geometry evidence supports action gating or limitation is returned |
| P2-10 | Search relevance and network fidelity | Hermetic relevance tests and unified request policy |

### P3: External durable agent runtime

| ID | Work item | Acceptance evidence |
|---|---|---|
| P3-01 | Durable run/DAG/event store | Restart after every transition preserves state |
| P3-02 | Provider adapters for Qwen/Baidu/ASI/current bridge | Capability descriptors and no silent fallback |
| P3-03 | Subagent lifecycle and nested budgets | Child cannot exceed parent authority/budget; cancellation fences replies |
| P3-04 | Canonical notes/TODO/context manifests | Long run works with no embeddings/RAG |
| P3-05 | Unified browser+orchestrator handoff | Resume from run and browser cursors without repeating actions |
| P3-06 | Provenance-first memory records | Contradiction/supersession/expiry/citations verified |
| P3-07 | Optional RAG service | ACL before ranking; poisoning tests; run works with service disabled |
| P3-08 | Model/network recorded playback | Replay does not repeat model charges or website effects |

### P4: Engine replacement research

| ID | Work item | Acceptance evidence |
|---|---|---|
| P4-01 | Reproducible patched-V8 archive lane, if retained | Versioned GN/archive/hash/binding compatibility in CI |
| P4-02 | Narrow safe native shim | No internal V8 pointers cross unowned boundaries |
| P4-03 | Greenfield verified bytecode/interpreter | Hand-written bytecode and deterministic traces |
| P4-04 | Actor-owned heap/GC/resource faults | Fault injection preserves other actors/process |
| P4-05 | Quiescent checkpoint and recorded playback | Same inputs produce same structured trace without duplicate effects |
| P4-06 | ECMAScript/DOM vertical slice | Test262 subset and captured-page acceptance |
| P4-07 | Optional baseline/JIT/WASM | Parity gates; no bypass of policy/fuel/safepoints |

---

## 11. Recommended first implementation slice

Do not start by adding more commands or more design documents. Prove one reliable vertical path:

```text
MCP/session.attach
  -> open hermetic page
  -> actions returns real stable refs
  -> action.start(ref, expected_epoch, idempotency_key)
  -> resident actor resolves and executes control
  -> EventJournal records action + DOM + network + console causality
  -> action result returns new epoch and evidence range
  -> retry with same idempotency key returns original result
  -> checkpoint at named level
  -> process restart/attach restores supported state
```

Run that exact scenario through shell, MCP, and REST and require matching outcomes.

This single slice will force the correct architecture for sessions, actors, identity, events, persistence, and adapter parity. It is more valuable than another twenty shell commands or another completion claim.

---

## 12. Bottom line

The project is not empty and it is not all slop. The low-level engine, parsing/projections, resident actor, watchdog, Python browser lanes, and storage primitives contain substantial real work.

The immature layer is the **public agent contract**: the place where names such as `act`, `eval`, `trace`, `checkpoint`, `restore`, `heap`, `frames`, `login`, and `session_id` are expected to mean deterministic, safe operations.

Fix that contract before building higher-level multi-agent intelligence on top of it.
