# DOSH Browser Repository Research and Target Integration Plan

**Status:** Repository-wide static research findings and revised target plan.

**Date:** 2026-11-05.

**Related document:** `DOSH-AGENT-NATIVE-ARCHITECTURE.md` describes the proposed greenfield VM. This report grounds the migration in the repository's actual implementation.

---

## 1. Scope and evidence standard

The user explicitly authorized reading the full `/Users/asishsharma/dosh-browser` repository.

Research covered the principal executable and packaging surfaces:

- `dosh-browser-core/` — primary Rust browser and `dosh` binary;
- `doshgentv8/core/` — Rust V8 embedding, resident actors, watchdog, Parena, DCP;
- `doshgentv8/py/` — optional PyO3 bridge;
- `doshgentv8/base/` — vendored V8 15.9 source and proposed new-engine architecture;
- `doshgentv8/patches/` — native patch series;
- `webresearch/` — optional Python companion implementation;
- `docs/`, `benchmarks/`, `scripts/`, root manifests/installers, and `.github/workflows/`;
- representative tests in `tests/` and inline Rust test modules.

Parallel read-only audits covered the Rust core, Python companion, native embedding, and docs/tests/packaging. High-impact findings were then checked directly against source.

### Not performed

- No builds, tests, servers, or network calls were run.
- No `.env` or secret values were read.
- `research_repos/` third-party projects were not used as implementation evidence.
- `target/` and other build artifacts were not used as source-of-truth evidence.
- No files outside `doshgentv8/base/` were edited.
- This was a systematic audit of all major lanes, not a line-by-line review of every V8 or third-party file.

Source presence proves implementation exists; it does not prove runtime correctness. Documentation and test counts are treated as claims until checked against code.

---

## 2. Executive verdict

DOSH already contains a substantial browser implementation: owned HTTP/network components, HTML parsing and ACD projections, a shared mini-DOM, a real resident V8 isolate-per-thread embedding, synchronous host calls, an actual watchdog, local neural inference, sessions, SQLite stores, MCP/REST/shell adapters, and a much richer Python companion.

The main problem is **not lack of subsystems**. It is that several public contracts claim behavior that the connected implementation does not provide.

The highest-impact findings are:

1. **The shipped Rust engine is currently a prebuilt `v8` crate archive, not the patched V8 15.9 source in `base/`.**
2. **Public action refs do not reliably resolve to live DOM controls, and missed actions can appear successful.**
3. **DCP has raw-pointer lifetime defects and is not the documented C++↔Rust protocol.**
4. **REST and stdio MCP each own one global session; advertised `session_id` does not provide isolation, bridging, or leases.**
5. **The real watchdog is not armed by the primary browser path, so arbitrary JS can occupy an actor indefinitely.**
6. **Several shell values are hardcoded rather than measured from the live engine.**
7. **Checkpoints restore partial document data, not live VM/session state; fork is not an isolate fork.**
8. **Shell, OpenAI tools, MCP, REST, and dispatch do not share one semantic registry.**
9. **The Python companion has important production capabilities the Rust lane lacks; replacing it by filename parity would regress the product.**
10. **No durable multi-model DAG orchestrator, subagent runtime, TODO system, or RAG memory exists.** Python has an in-memory LLM tool loop; Rust `ask` is a local search/read formatter.

The correct strategy is therefore:

> Build a canonical browser control/evidence plane and an external durable agent orchestrator now, using the working stock-V8 actor as the transitional engine. Develop the greenfield doshgentv8 kernel independently. Do not make the agent runtime wait for the new VM, and do not expose current DCP/Parena scaffolding as a stable external foundation.

---

## 3. The three architectures currently present

The repository describes three different targets that must not be conflated.

### 3.1 Shipping/transitional browser

- Rust `dosh` binary is primary.
- Python `webresearch` is an optional but substantial companion.
- Rust navigation creates per-tab resident V8 runtimes.
- The active V8 features are largely Rust and JavaScript overlays.
- Shell, REST, and MCP expose browser operations.

### 3.2 Experimental State Weave / patched-V8 lane

- Vendored V8 15.9 source and patches.
- Parena host-owned arena.
- DCP C header and Rust dispatcher.
- Startup snapshots, SessionPool, checkpoint/fork/rewind experiments.
- Not the engine archive selected by the current Cargo manifest and not a complete VM-state model.

### 3.3 Proposed greenfield agent VM

- C++20 verified bytecode/interpreter.
- Deterministic actor scheduler and actor-owned heaps.
- Recoverable resource faults and canonical checkpoints.
- Narrow versioned C ABI to a Rust browser host.
- A research target, not a current build target or shipping subsystem.

These lanes need separate names, capability manifests, tests, and release claims.

---

## 4. Actual shipping architecture

### 4.1 Entry points

`dosh-browser-core/src/bin/dosh.rs` constructs different ownership models per command:

- `read`: stateless read lane;
- `search`: search waterfall;
- `ask`: one `Session` plus `agent::ask`;
- `shell`: one `Session` and one `ShellStore`;
- `server`: one global `AppState`;
- `mcp`: one `Arc<Mutex<Session>>` and one `ShellStore`;
- `-p/--python`: launches `python -m webresearch` explicitly.

`dosh-browser-core/src/lib.rs` exports internal modules directly. There is not yet a narrow application-service boundary shared by all adapters.

### 4.2 Navigation and JavaScript

The real primary navigation path is:

```text
Session::goto
  -> NetworkEngine::fetch
  -> per-tab Runtime
  -> resident DoshActor / V8 isolate
  -> inline mini-DOM script execution
  -> serialize mutated DOM
  -> parse/project ACD/actions/links
  -> tab history
```

Evidence:

- `dosh-browser-core/src/session.rs:289-397` — `Session::goto`;
- `dosh-browser-core/src/runtime.rs:270-342` — runtime construction;
- `dosh-browser-core/src/runtime.rs:24` — embeds `webresearch/engine/minidom.js`;
- `doshgentv8/core/src/actor.rs:584-618` — resident actor thread;
- `doshgentv8/core/src/lib.rs` — actual V8 isolate/context and host dispatch.

This is a real owned browser pipeline, not Chromium/CDP.

### 4.3 Resident actor

`DoshActor::spawn` creates the V8 isolate on a dedicated OS thread. Calls are sent over `std::sync::mpsc` and synchronously wait for replies. The isolate, JS heap, closures, and mini-DOM survive across calls.

This ownership model is worth keeping and hardening. It is the closest existing implementation to the desired actor kernel.

Current limitations:

- unbounded command channel (`actor.rs:586-589`);
- blocking `recv()` replies without cancellation/deadline;
- shutdown sends a message but does not join the thread (`actor.rs:575-578`);
- a stuck actor cannot process shutdown;
- raw-pointer DCP responses are marked `Send`/`Sync` without a valid lifetime model.

---

## 5. Engine and release truth

### 5.1 Current archive selection

`doshgentv8/core/Cargo.toml:8-14` selects:

```toml
v8 = { version = "152.2.0", features = ["v8_enable_pointer_compression"] }
```

The comments explicitly say the patched fork will drop in later through `RUSTY_V8_ARCHIVE`.

There is no project `build.rs`, GN/Ninja invocation, native C++ shim library, or exported native DCP dispatcher connecting this crate to custom methods in `base/src`.

Therefore:

- current Cargo/CI/release configuration does not compile `doshgentv8/base/`;
- the base tree identifies itself as V8 15.9, while the crate is 152.2.0;
- there is no checked compatibility contract between those sources;
- native base patches must not be described as shipped merely because they exist in the repository.

### 5.2 Active agent features are mostly overlays

`doshgentv8/core/src/lib.rs` contains a large `EXTENSIONS_SCRIPT` implementing JavaScript-level helpers for:

- `Date.now`/`performance.now` overrides;
- seeded `Math.random`;
- cooperative `__dosh_step` fuel;
- namespace-based eval/WASM/action gates;
- a tail-call trampoline;
- Butterfly-like objects and `dosh.*` methods.

These are useful experiments but are not equivalent to native V8 mechanisms:

- normal JS is not automatically instruction-fueled by `__dosh_step`;
- overriding `Date.now` does not cover every native time path;
- deleting/restoring `globalThis.WebAssembly` does not revoke previously obtained capabilities;
- JS-mutable action booleans do not prevent direct DOM mutation;
- function wrapping in `eval_bounded` does not create an isolated V8 realm.

### 5.3 Watchdog exists but product does not arm it

`doshgentv8/core/src/watchdog.rs` implements a real process-wide deadline supervisor using isolate termination.

A repository-wide Rust call-site grep found `set_time_limit` in the actor, engine, PyO3 facade, and engine tests, but no production caller in `dosh-browser-core`.

**Impact:** a non-allocating `while (true) {}` can occupy a primary-lane actor indefinitely. This is a P0 execution-containment defect.

### 5.4 Native patch state

The native patch series and working-tree edits are research, not a reproducible shipping pipeline:

- no complete custom archive build automation;
- no C++ shim exposing custom internal isolate methods to Rust;
- several patch files contain comments or partial implementations rather than their advertised full feature;
- the allocation-fuel experiment in `base/` uses a fatal trap and is unsuitable as the final actor-fault model;
- the Liftoff fuel experiment charges during compilation rather than emitting general runtime metering and requires a real compile audit.

The base allocation-fuel and heap-stat edits are therefore explicitly classified as **unshipped transitional research**, not production capability.

### 5.5 Required engine lanes

Use explicit names:

1. **`stock-v8-actor`** — current supported Rust embedding; harden and ship improvements now.
2. **`patched-v8-research`** — custom base/archive experiments; not released until reproducibly built and tested.
3. **`doshvm-research`** — greenfield C++20 engine; independent vertical slices and conformance gates.

---

## 6. Release-blocking control-plane findings

### 6.1 Public action refs are not a valid execution contract

`dosh-browser-core/src/actions.rs:189-219` computes an eight-character content hash as `Target.ref_id` and emits an empty `path`.

`dosh-browser-core/src/session.rs:492-535` passes that ref as the runtime action `ref`.

`dosh-browser-core/src/runtime.rs:517-553` treats it as a CSS selector/ID and discards the JavaScript return value/errors before serializing the page.

The mini-DOM activation path expects `getElementById` or `querySelector`, not a content hash.

Consequences:

- an advertised ref may not identify any live control;
- a missing control can return an unchanged page as though the action succeeded;
- tests using raw DOM IDs do not prove extracted refs work;
- duplicate/occurrence and stale-document semantics are insufficient.

**Required replacement:** actor-resolved, document-epoch-scoped action references and explicit outcomes.

Proposed reference:

```text
session_id, tab_id, document_epoch, stable_element_id, occurrence
```

Proposed outcome:

```text
action_id, acted | not_found | stale_ref | unsupported | occluded | failed,
old_document_epoch, new_document_epoch, caused_event_range, error
```

The action gateway must distinguish “the click executed” from “the intended site-level result succeeded.”

### 6.2 DCP has unsafe response lifetimes

Evidence:

- `doshgentv8/core/src/protocol.rs:50-59,100-109` — raw pointers in commands/responses;
- `protocol.rs:134-142` — `DoshResponse::error` retains `msg.as_ptr()`;
- `protocol.rs:156-159` — unconditional unsafe `Send`/`Sync`;
- `doshgentv8/core/src/lib.rs:1342-1462` — Rust `match`, not C++ dispatch;
- `lib.rs:1423-1433` — batch response returns a pointer into a local `Vec<u8>` that is dropped;
- `lib.rs:1435-1438` — telemetry events are drained and only their count returned;
- `lib.rs:1439-1444` — forked arena result is discarded;
- `lib.rs:1346-1354` — eval result is discarded.

Additional contract drift:

- C ends opcodes at `0x0D`; Rust adds `0x0E-0x10`;
- C packs structures while Rust uses normal `#[repr(C)]`;
- static LP64 field arithmetic makes packed C `DoshHeapStats` 36 bytes versus 40 bytes with ordinary Rust tail alignment;
- the C comment calls `DoshCommand` 32 bytes although its listed LP64 fields total 24 bytes;
- telemetry ring representations differ.

**Decision:** replace the current DCP implementation before extending or exposing it. Use owned Rust enums/values internally. Introduce a real C ABI only for the future native shim, with fixed-width layout, version/size, explicit buffer ownership, and no unowned cross-thread pointers.

### 6.3 Shell values are not live engine observations

`dosh-browser-core/src/shell.rs:951-1011` returns:

- hardcoded active WASM instance and Liftoff/patch claims;
- configured fuel rather than necessarily live remaining fuel;
- hardcoded heap limit, semi-space size, GC policy, and compiler pruning claims.

`Session::configure_runtime` (`session.rs:256-269`) applies configuration only at selected times and discards all errors. Runtime configuration changes are not a transactional live-actor operation.

**Required replacement:** synchronous `ActorConfig` apply/query methods with errors propagated, and measured heap/fuel/engine capability responses. Unavailable fields must be marked unavailable.

### 6.4 One global session is not multi-client isolation

`dosh-browser-core/src/server.rs:21-40` stores exactly one `Arc<Mutex<Session>>`.

`dosh-browser-core/src/bin/dosh.rs:324-337` constructs one session for stdio MCP.

Tool schemas advertise `session_id`, but command dispatch does not route by it.

The mutex serializes mutation; it does not provide:

- independent client sessions;
- create/list/close lifecycle;
- writer leases or fencing;
- TTL/eviction safety;
- cancellation;
- reconnect/handoff;
- global actor capacity.

`SessionPool` exists but the product stores runtimes in a separate unbounded per-session map (`session.rs:194-199,230-253`).

**Required replacement:** `SessionRegistry` with stable IDs/generations, RAII leases, one writer plus read-only observers initially, explicit close, idle reaping, and bounded global actor capacity.

### 6.5 Shell/MCP/REST/tools do not share one contract

There are separate registries and implementations in:

- `dosh-browser-core/src/mcp.rs`;
- `dosh-browser-core/src/tools.rs`;
- `dosh-browser-core/src/dispatch.rs`;
- `dosh-browser-core/src/shell.rs`;
- `dosh-browser-core/src/server.rs`.

Observed mismatches include advertised tools without dispatch implementations, dispatched tools missing from registries, MCP-only aliases, different page types, different return shapes, and different `ask` behavior.

`Session::execute_shell_command` can create a fresh in-memory `ShellStore`, and dispatched `browser_shell` arguments do not match the normal shell path.

**Decision:** one versioned operation registry and canonical `BrowserControl` implementation. Shell, MCP, REST, and OpenAI schemas are generated/projected from it. Only serialization should differ.

---

## 7. State, checkpoint, telemetry, and network findings

### 7.1 Checkpoint levels are currently conflated

The repository contains:

1. **Evidence snapshots:** URL/action/control evidence, not rollback.
2. **Shell SQLite checkpoints:** partial current-tab HTML/storage/history data.
3. **Parena checkpoints:** host-owned arena state, currently not the real page DOM.
4. **Tab/history snapshots:** document data used for navigation/history.
5. **Proposed VM checkpoints:** heap/frames/closures/microtasks, not implemented.
6. **Proposed orchestrator checkpoints:** runs/DAG/model/tool state, not implemented.

`dosh-browser-core/src/shell.rs:619-662` demonstrates the checkpoint gap:

- `cookies` serializes `tab.headers`, not a real cookie jar;
- only current tab/local storage is saved;
- restore changes stored fields without rebuilding the live actor;
- the response says “session rehydrated” without identifying the limited restore level.

Server and MCP use `ShellStore::new(None)`, so their stores are in-memory by default.

**Required response:** version every checkpoint by level and exact content. Do not claim VM restoration until heap/closure/listener/timer tests prove it.

### 7.2 Parena is not the live page or VM state

`dosh-browser-core/src/runtime.rs` loads the real document into mini-DOM but creates a Parena containing only a placeholder HTML node.

`Session::fork` creates a fresh runtime and clones Parena; it does not clone the V8 heap or live mini-DOM. Rewind reloads saved HTML after Parena rewind.

Parena is a real data structure and useful experiment. It is not currently an authoritative page arena, speculative browsing substrate, or agent memory system.

### 7.3 Telemetry and causal trace are fragmented

There are separate session and network ledgers. Actual network records go to `NetworkEngine.ledger`, while several shell commands inspect the other ledger.

Additional gaps:

- runtime console/postMessage records are not consistently transferred to session logs;
- `trace` does not propagate an action ID through runtime/network work;
- Rust `watch` returns empty records;
- the engine telemetry ring has no production producers in the audited path;
- direct read/search/frame/download lanes can bypass central network policy.

**Required replacement:** one append-only session event journal with operation/cause IDs and explicit coverage gaps. All host requests and runtime events enter through that journal.

### 7.4 Network policy must become universal

`NetworkEngine` is real and the primary `Session::goto` uses it. However, several alternate lanes use direct fetch/reqwest paths, including stateless read, search, frames, and downloads.

Security/correctness issues found during static review include redirect/filter concerns, default-off checks in some paths, cache `Vary` omissions, full-body reads before truncation, and inaccurate preflight method construction in the reviewed code.

**Decision:** one policy-aware transport interface for navigation, subresources, runtime fetch/XHR, frames, downloads, and search. Different request classes may have different policy, but bypass cannot be accidental.

### 7.5 Frames and script loading are incomplete in Rust

Rust frame code discovers/fetches frame documents but does not provide Python-equivalent live frame realms and MessagePort routing.

Rust runtime currently executes inline scripts but lacks complete external script/module loading; `host_load_module` is a stub and module metadata is not fully wired. Several shell operations create temporary runtimes instead of using the live tab realm.

**Decision:** preserve DTO/discovery helpers, replace runtime/realm lifecycle and host module loading with a single actor-owned document realm manager.

---

## 8. Python companion findings

### 8.1 It is a second implementation, not just an SDK wrapper

The Python lane has its own fetch/parse/session/runtime/network/challenge stacks and independent MCP/REST/shell/tools implementations.

QuickJS is the default Python engine. The PyO3 V8 lane is real but experimental and not packaged by the normal root wheel flow.

Important Python-leading capabilities:

- reCAPTCHA v2 protocol solver;
- live frames and MessagePort routing;
- RFC 6455 WebSocket client;
- login/OTP workflows;
- persistent browser profiles;
- provider/model integration;
- additional service-worker/challenge/WASM/layout work.

Do not delete these merely because same-named Rust files exist. Port or expose them behind the canonical contract only after hermetic parity tests pass.

### 8.2 Python `agent.py` is an actual but ephemeral LLM loop

`webresearch/agent.py:322-469`:

- creates a fresh `Browser`;
- calls an OpenAI-compatible chat endpoint;
- executes tools sequentially;
- supports configured fallback after rate/billing failures;
- records model/tool rounds and usage;
- closes browser sessions at completion.

It has no durable run store, DAG, subagent hierarchy, cross-run memory, resumable event log, or crash-safe idempotency.

Sensitive tool arguments can be included in events/results, so credentials and OTP values need structured redaction.

`webresearch/settings.py` currently centers on GLM, Mercury, and generic OpenAI-compatible configuration. It is not yet the requested Qwen/Baidu/ASI multi-provider orchestrator.

### 8.3 Rust `ask` is not an LLM agent

`dosh-browser-core/src/agent.rs:60-96` performs search, formats results, optionally reads the first hit, and returns `model = "dosh-local"` with one round and zero usage.

This is a useful no-key local convenience operation. It is not planning, synthesis over durable memory, or a general agent loop. HTTP `/v1/ask` differs again and only searches.

**Decision:** externalize agent orchestration while retaining local search/read as an explicitly named browser convenience feature.

### 8.4 Python-specific integration issues

- `NetworkEngine` exists, but normal Python navigation does not consistently route through it.
- MCP/REST/dispatch are separate contracts; generic invocation needs tenant propagation.
- Durable checkpoints omit significant state and do not prove process restart restoration.
- Native V8 selection can fall back in ways that do not accurately report the actual engine.
- The root wheel does not package a reliable native V8 backend.
- `minidom.js` is shared by Rust through a path inside the Python package; move it to neutral engine ownership before making Python truly optional at source-package level.

---

## 9. Agent orchestration and memory reality

### 9.1 What exists

- Python in-memory LLM tool loop and provider fallback;
- Rust local search/read convenience;
- SQLite skills, warrants, evidence snapshots, and partial checkpoints;
- ACD block query/slice/diff and content hashes;
- local network ledgers and artifact/download support;
- browser profiles for authentication state;
- a small local interactivity neural classifier.

### 9.2 What does not exist

- durable run/DAG scheduler;
- subagent lifecycle and nested budgets;
- Qwen/Baidu/ASI provider abstraction as requested;
- long-horizon task/TODO store;
- resumable model-call event log;
- conversation/episodic/semantic memory model;
- embeddings/vector index/RAG service;
- multi-agent tab/session arbitration;
- model/browser unified handoff bundles;
- deterministic model-output replay.

ACD substring query is current-page retrieval, not RAG. Skills are stored procedures, not generalized agent memory. Parena is page-state experimentation, not long-horizon memory.

---

## 10. Revised target architecture

```text
┌──────────── External agent runtime (separate process/crate) ────────────┐
│ Provider adapters: Qwen Cloud / Baidu / ASI / current-model bridge      │
│ Durable runs + DAG + subagents + approvals + budgets + cancellation     │
│ Canonical notes/todos + context compiler + optional RAG client          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ authenticated versioned control API/MCP
┌───────────────────────────────▼─────────────────────────────────────────┐
│ DOSH BrowserControl (Rust)                                             │
│ SessionRegistry + bridge leases + ActionGateway + EventJournal          │
│ EvidenceStore + checkpoint/replay + policy + artifact references        │
│ Shell / MCP / REST / OpenAI adapters generated from one registry        │
├─────────────────────────────────────────────────────────────────────────┤
│ Browser services                                                       │
│ Policy-aware NetworkEngine + DOM/frames/layout/storage/vault            │
├─────────────────────────────────────────────────────────────────────────┤
│ Engine adapter                                                         │
│ stock-v8-actor now; patched-v8-research and doshvm-research separately  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Ownership rules

**Browser owns:**

- browser sessions/tabs/documents/actors;
- action execution and idempotency records;
- website capabilities and secrets vault;
- network/DOM/console evidence;
- document/page/VM checkpoints according to exact supported level;
- event sequencing and artifact references;
- browser resource limits.

**External orchestrator owns:**

- provider adapters and model credentials;
- runs, DAGs, tasks, retries, and subagents;
- prompts/context manifests;
- canonical notes/todos;
- approvals and global agent budgets;
- provider/model replay records;
- long-horizon handoff.

**Optional memory service owns:**

- embeddings and hybrid indexes;
- derived retrieval chunks and compaction proposals;
- no tool authority, canonical task state, or original evidence.

The browser must work with no LLM, orchestrator, or RAG service configured.

---

## 11. Concrete host file integration plan

These are proposed changes mapped to real current files. New paths are target names, not claims that they exist.

### 11.1 Canonical browser application service

| Target file | Responsibility | Existing files it consolidates |
|---|---|---|
| `dosh-browser-core/src/control.rs` | `BrowserControl` application API | Direct adapter calls into internal modules |
| `dosh-browser-core/src/control_types.rs` | Versioned requests/results/errors/capabilities | Divergent page/tool response types |
| `dosh-browser-core/src/operation_registry.rs` | Single operation/schema registry | `tools.rs`, MCP tool list, dispatch table, shell command metadata |
| `dosh-browser-core/src/session_registry.rs` | Session lifecycle/capacity/generations | One global session in `server.rs` and `bin/dosh.rs` |
| `dosh-browser-core/src/bridge.rs` | Attach/detach/heartbeat/transfer/fencing | No current multi-client bridge |
| `dosh-browser-core/src/lease.rs` | RAII actor/session read-write leases | Mutex-only exclusion and unsafe eviction |
| `dosh-browser-core/src/action_gateway.rs` | Idempotent action lifecycle and outcomes | `session.rs` + `runtime.rs` action calls |
| `dosh-browser-core/src/event_journal.rs` | Ordered causal events and cursors | Split ledgers, trace/watch/console gaps |
| `dosh-browser-core/src/evidence_store.rs` | Content-addressed artifacts/manifests | Snapshots/download artifacts/provenance |
| `dosh-browser-core/src/checkpoint.rs` | Versioned checkpoint levels and manifests | Shell/Parena/document checkpoint conflation |
| `dosh-browser-core/src/replay.rs` | Recorded playback/live restore distinction | No general replay controller |
| `dosh-browser-core/src/capability_policy.rs` | Host-enforced capabilities and approvals | JS-mutable gates and adapter-specific checks |

### 11.2 Adapter changes

- `bin/dosh.rs`: construct a `BrowserControl` and durable configured stores; do not create independent global session semantics per adapter.
- `shell.rs`: parse shell syntax into canonical operations; remove direct placeholder implementations.
- `mcp.rs`: project canonical schemas/results/errors; retain JSON-RPC framing only.
- `server.rs`: route authenticated tenant/session IDs into `BrowserControl`.
- `tools.rs`: generate OpenAI-style schemas from `operation_registry.rs`.
- `dispatch.rs`: become a thin registry invoker, not another implementation registry.
- `agent.rs`: retain local search/read under an honest name or delegate to external orchestrator API.

### 11.3 Session/runtime changes

- `session.rs`: reduce to session/tab/document coordination; remove global configuration fields that are not live actor truth.
- `runtime.rs`: resolve live-node actions transactionally, return actual script outcomes, propagate console/network/mutation events, and never discard action evaluation errors.
- `actions.rs`: preserve inventory heuristics; emit stable element identity plus occurrence/epoch metadata.
- `identity.rs`: own generation/duplicate/stable-ref resolution and remove the second incompatible hash identity scheme.
- `frames.rs`: retain discovery DTOs; integrate with the actor realm manager.
- `network.rs`: become the mandatory policy-aware transport for every request class.
- `telemetry.rs`: become event-journal encoding/redaction support, not an independent shadow ledger.
- `shell.rs` checkpoint branches: delegate to `checkpoint.rs` with explicit restore level.

A later structural split may introduce `document.rs`, `tab.rs`, and `runtime_registry.rs`; do not perform cosmetic moves before behavioral contracts are tested.

### 11.4 Engine crate changes

- `doshgentv8/core/src/actor.rs`: bounded queues, operation IDs, cancellation/deadlines, joinable shutdown, actor generation, and lease-safe eviction.
- `doshgentv8/core/src/watchdog.rs`: retain supervisor; arm it from every execution path including microtask drains.
- `doshgentv8/core/src/lib.rs`: replace raw DCP dispatch with owned engine methods/results; RAII for host bridge and heap callbacks; truthful capability reporting.
- `doshgentv8/core/src/protocol.rs`: replace pointer packets with owned internal request/response/event types. A future native ABI gets a new versioned definition rather than patching this one silently.
- `doshgentv8/core/src/session.rs`: make pool eviction lease-aware and actually use it, or remove the unused product field.
- `doshgentv8/core/src/parena.rs`: retain as experimental host-side storage until a real document vertical slice exists.
- `doshgentv8/core/tools/mksnapshot.rs`: retain startup snapshot tooling with compatibility manifests; do not confuse startup snapshots with actor checkpoints.

### 11.5 Shared browser assets

Move `webresearch/engine/minidom.js` to a neutral shared engine-asset location once both lanes use the same canonical file. Until then, the full repository is required for the Rust source build because `runtime.rs` uses `include_str!` on that Python-package path.

---

## 12. External agent runtime design

Create a separate optional runtime, potentially another workspace package but not a dependency of the basic `dosh` binary.

### 12.1 Proposed module map

```text
agent-runtime/
  contracts/
    run.schema.json
    task.schema.json
    action.schema.json
    provider.schema.json
    event.schema.json
    handoff.schema.json
    memory.schema.json
  src/
    api
    run_store
    event_store
    dag_validator
    dag_scheduler
    task_lease_manager
    cancellation_controller
    budget_ledger
    approval_gate
    subagent_manager
    context_compiler
    handoff_manager
    browser_client
    providers/
      registry
      base_adapter
      qwen_cloud_adapter
      baidu_adapter
      asi_adapter
      current_model_bridge
    memory/
      notes_store
      todo_store
      artifact_index
      rag_client
  tests/
    crash_recovery
    stale_writer
    duplicate_effect
    provider_capability
    memory_acl
    long_horizon
```

Names are logical modules; choose Rust source conventions during implementation.

### 12.2 Provider contract

Each adapter reports actual support for:

- tool calling;
- structured output;
- streaming;
- vision;
- cancellation;
- seeds;
- context/output limits;
- measured versus estimated cost/usage;
- data-egress policy.

Provider/model is pinned when a subagent starts. “Current model” resolves to an immutable descriptor at that point. There is no silent cross-provider fallback.

### 12.3 Durable DAG and subagents

Task states:

```text
blocked -> ready -> leased -> running
                         -> waiting_tool / waiting_approval
                         -> succeeded / failed / cancelled
```

Use transactional leases, heartbeats, and fencing. Assume at-least-once task execution and make model/tool attempts idempotent where possible.

A subagent spawn record includes:

- parent task and nesting depth;
- pinned provider/model;
- objective and expected output schema;
- explicit context manifest;
- browser origin/action capabilities;
- read-only or writer lease;
- memory namespaces;
- token/cost/tool/browser budgets;
- deadline and cancellation lineage.

Subagents may propose DAG changes; an authorized parent commits them. Their outputs remain untrusted until validated.

### 12.4 Budgets

Track hierarchically:

- input/output tokens and monetary cost;
- model/tool retries;
- browser actions/network bytes;
- subagent depth/count/concurrency;
- artifacts and storage;
- RAG/embedding calls;
- deadlines and cancellation.

Browser quotas remain mandatory because direct MCP clients can bypass the orchestrator.

---

## 13. Memory architecture

### 13.1 Layers

| Layer | Contents | Authority |
|---|---|---|
| C0 prompt context | Objective, selected notes, tool outputs, cited excerpts | Rebuilt from context manifest |
| C1 run state | DAG, attempts, notes/todos, approvals, budgets | External orchestrator durable DB |
| C2 browser evidence | Events, DOM/network artifacts, checkpoints | Browser/evidence store |
| C3 semantic memory | Embeddings, hybrid indexes, retrieval history | Rebuildable optional service |

### 13.2 Durable record classes

- working run state;
- episodic evidence history;
- canonical notes and TODOs;
- procedural skills with origin/warrant/version;
- verified site/API knowledge;
- derived summaries and embeddings.

Each memory record includes:

- tenant/session/run scope;
- source evidence and citations;
- producer model/agent;
- trust label and verification status;
- created/valid/expiry sequence;
- `supersedes` / `contradicted_by` relationships;
- content hash and schema version.

### 13.3 RAG rules

- Notes/todos and direct evidence retrieval work without RAG.
- ACL filtering occurs before ranking.
- Web-derived text cannot grant capabilities or alter policy.
- Derived summaries never overwrite original evidence.
- Sensitive content sent to an embedding provider follows model data-egress policy.
- The memory service cannot execute browser actions.

---

## 14. Session bridging and long-horizon handoff

### 14.1 Browser bridge

Initially allow multiple read-only observers and one writer per browser session.

A bridge records:

- session ID and generation;
- external run/task/subagent attribution;
- lease epoch and fencing token;
- read/write mode and capabilities;
- event acknowledgment cursor;
- allowed origins/action classes.

MCP transport connection IDs are not session IDs.

### 14.2 Handoff bundle

Include:

- run/DAG versions and task states;
- canonical notes/todos and context manifests;
- provider/model descriptors without secrets;
- budget/approval ledgers;
- browser checkpoint IDs and event cursors;
- pending and `outcome_unknown` actions;
- evidence/artifact manifest and hashes;
- secret references only.

Handoff sequence:

1. quiesce new writes;
2. reconcile in-flight actions;
3. create checkpoint at an event barrier;
4. verify manifest;
5. revoke old bridge and increment lease epoch;
6. attach new owner;
7. resume from recorded cursors.

### 14.3 Action idempotency

Every consequential browser action has an idempotency key and persistent result state:

- `running`;
- `succeeded`;
- `failed`;
- `rejected_precondition`;
- `cancelled`;
- `outcome_unknown`.

After a crash, query before retrying. Never blindly repeat a timed-out POST, login, purchase, or submission.

---

## 15. Security and trust boundaries

1. Model-proposed tool calls are requests, not authority.
2. Webpage, subagent, and RAG content is untrusted data.
3. Host-enforced policy checks action kind, origin, actor, arguments, budget, and approvals.
4. JavaScript booleans and “control realm” wrappers are not security boundaries.
5. Credentials remain in a broker/vault and never enter ordinary notes, RAG, checkpoints, or model events.
6. Replace the shell login path that generates a temporary Python source containing credentials; it is neither safe secret handling nor session bridging.
7. Use origin-scoped, revocable capabilities and bridge fencing.
8. Require explicit policy/approval for payments, posting, uploads, secret use, and cross-origin exfiltration.
9. Record unsupported operations honestly; do not convert missing tools into success.
10. Treat provider/model output as untrusted until schema and evidence validation.

---

## 16. Keep / upgrade / replace / prune

| Component | Decision |
|---|---|
| Resident isolate-per-thread actor | **KEEP + UPGRADE** with bounded queues, deadlines, cancellation, generations, leases |
| Watchdog supervisor | **KEEP and wire immediately** |
| Rust parser/tree/content/links/forms/ACD/provenance | **KEEP**, repair evidence inputs and totals |
| Local INT8 interactivity model | **KEEP** as assistive signal, not orchestrator |
| Action extraction heuristics | **KEEP**, replace ref resolution and outcome semantics |
| Session DTO concepts | **KEEP**, replace multi-client/state orchestration |
| NetworkEngine | **UPGRADE**, make universal and close policy gaps |
| Telemetry/redaction primitives | **KEEP + UPGRADE** into one causal journal |
| Axum/stdio MCP transports | **KEEP**, replace routing and contracts |
| Python frames/WebSockets/reCAPTCHA/login/profile lanes | **KEEP until parity**, expose behind canonical contract |
| `minidom.js` | **KEEP**, move to neutral shared ownership |
| Current DCP | **REPLACE** due ownership, lifetime, ABI, and semantic defects |
| Parena as authoritative page state | **REPLACE integration; KEEP experiment** |
| Shell checkpoint claims | **REPLACE with explicit checkpoint levels** |
| Shell/MCP/tools/dispatch registries | **REPLACE with generated canonical registry** |
| JS boolean action/control gates | **REPLACE with host-enforced capabilities** |
| Python ephemeral agent loop as final orchestrator | **EXTRACT/REPLACE with durable external runtime** |
| Rust local `ask` | **KEEP as named local search/read convenience**, not an LLM agent |
| Optimizing JIT/native fork claims | **PRUNE from shipping claims until built/tested** |
| State Weave COW/speculation | **DEFER until real DOM/Parena integration exists** |
| Greenfield doshvm | **BUILD INDEPENDENTLY**, do not block BrowserControl/orchestrator |

---

## 17. Revised implementation sequence

### Phase 0: Truth and safety corrections

1. Fix action-ref resolution and explicit action outcomes.
2. Arm execution deadlines for eval, script, timer, and microtask paths.
3. Replace DCP raw-pointer responses with owned values; remove unsafe external claims.
4. Replace hardcoded heap/WASM/engine responses with measured values/unavailable markers.
5. Make live actor configuration transactional and propagate errors.
6. Mark checkpoint types honestly and add live-runtime invalidation/rebuild where required.
7. Add a capability manifest that distinguishes shipping, partial, experimental, and proposed features.

**Gate:** hermetic tests prove advertised refs act, infinite JS is interrupted, engine values are measured, and no raw DCP buffer outlives its owner.

### Phase 1: Canonical BrowserControl

1. Add operation registry/types and `BrowserControl`.
2. Route shell/MCP/REST/dispatch through it.
3. Add SessionRegistry, leases, fencing, capacity, cancellation.
4. Add ActionGateway and idempotency store.
5. Add EventJournal and EvidenceStore with durable SQLite/WAL defaults.
6. Route all network request classes through policy-aware transport.

**Gate:** one scenario has identical semantics through shell, REST, and MCP; two clients cannot leak sessions or write with stale leases.

### Phase 2: External durable orchestrator

1. Implement run/event store and provider adapters.
2. Add DAG scheduler, task leases, cancellation, and hierarchical budgets.
3. Add canonical notes/todos and context manifests.
4. Add subagent lifecycle and provider/model pinning.
5. Add handoff bundle joining run/browser cursors.
6. Add optional RAG only after provenance/ACL tests pass.

**Gate:** `create run -> model call -> browser read/action -> evidence -> checkpoint -> process restart -> resume without repetition`.

### Phase 3: Checkpoint and replay semantics

1. Implement versioned evidence/document/page checkpoint levels.
2. Add recorded host-result tapes and explicit effect policies.
3. Separate live attach, live restore, recorded playback, and live replay.
4. Add actor incarnation fencing and `outcome_unknown` reconciliation.
5. Keep VM checkpoints marked unavailable until the engine proves them.

**Gate:** crash after effect commit but before acknowledgement never causes an unreviewed duplicate effect.

### Phase 4: Python/Rust parity and browser fidelity

1. Generate conformance suites for both host implementations.
2. Preserve Python-leading lanes behind the canonical contract.
3. Complete external scripts/modules, live frames/messaging, and storage/runtime synchronization.
4. Move mini-DOM to neutral ownership.
5. Replace duplicated implementations only after feature parity.

**Gate:** no advertised Python capability silently disappears when selecting Rust, and backend metadata reports the actual engine.

### Phase 5: Engine lane hardening

For `stock-v8-actor`:

- bounded channels, joinable lifecycle, RAII bridge ownership;
- actual deadlines and resource limits;
- truthful capabilities and startup snapshot compatibility.

For `patched-v8-research`:

- reproducible GN/Ninja toolchain and pinned archive;
- version compatibility with Rust bindings;
- narrow C++ shim with safe ownership;
- real emitted fuel and recoverable resource traps;
- no fatal allocation-budget trap in the product.

For `doshvm-research`:

- follow `DOSH-AGENT-NATIVE-ARCHITECTURE.md`;
- prove minimal bytecode/actor/heap/scheduler first;
- quiescent checkpoints and recorded playback before broad JS;
- interpreter before JIT;
- integrate through the same BrowserControl engine-adapter contract only after parity gates.

---

## 18. Verification matrix

### P0 hermetic tests

- extracted action ref clicks/types the intended live element;
- duplicate and stale refs return precise outcomes;
- infinite JS and infinite WASM loops are terminated;
- every public shell engine statistic is measured or unavailable;
- DCP/ABI buffers remain valid until explicit release;
- checkpoint restore identifies exact restored state and rebuilds/invalidates runtime correctly;
- no secrets appear in tool traces or temporary scripts.

### Control-plane tests

- every advertised operation is dispatchable;
- shell/MCP/REST request and outcome parity;
- session create/isolation/close and tenant propagation;
- writer handoff rejects stale fencing tokens;
- actor eviction cannot occur under a live lease;
- cancellation and late replies cannot mutate superseded runs.

### Orchestration tests

- crash after model response but before persistence;
- crash after browser effect but before result recording;
- provider failover during a multi-tool sequence;
- duplicate model response and late tool completion;
- two writers and one auditor contending for a tab;
- memory poisoning/contradiction handling;
- multi-hundred-step run with bounded context/storage;
- complete run with RAG disabled.

### Engine tests

- actual backend selection and fallback reporting;
- C/Rust ABI golden layout/version/buffer tests if a real ABI is introduced;
- fuel exact boundaries and automatic infinite-loop coverage;
- checkpoint closure/listener/timer/clock/PRNG fidelity where claimed;
- startup snapshot version/configuration rejection;
- differential QuickJS/V8/doshvm behavior on shared hermetic fixtures.

### Packaging/release tests

- record actual linked engine/archive hash in `dosh product/version`;
- test the exact release artifact on all targets;
- mandatory Unix and Windows checksum verification;
- source fallback pinned to release tag;
- one authoritative workspace lockfile;
- Python native engine installation and console-entry shadowing resolved;
- license/notice inclusion reviewed without modifying legal files in this task.

---

## 19. Documentation and packaging corrections

The docs frequently mix shipped, partial, and proposed states. Introduce one generated capability ledger rather than competing prose totals.

Specific corrections required:

- README engine claims must identify actual linked V8 versus `base/` research.
- DCP plan must not call the current implementation completed/stable C++ zero-copy IPC.
- State Weave must distinguish placeholder Parena from live page/VM state.
- Shell command counts must not imply semantic completeness.
- Test counts must be generated and labeled by collection/execution environment.
- CI must run the native PyO3 extension tests if that lane is advertised; self-skips are not coverage.
- Installers must enforce the checksum behavior claimed by README on both Unix and Windows.
- The root Python package and native V8 backend need an explicit supported packaging story.
- The basic Rust binary must retain no LLM dependency; Python `ask` remains optional and externally configured.

---

## 20. Immediate next artifacts

1. **`BrowserControl` contract ADR** — operation IDs, session/lease model, action outcomes, error taxonomy, event journal, checkpoint levels.
2. **P0 correctness patch series** — action refs, watchdog wiring, DCP ownership, live engine stats, transactional runtime configuration.
3. **Durable run/handoff schema** — external orchestrator and browser evidence joined by stable IDs/cursors.
4. **Conformance harness** — one hermetic scenario executed through shell, REST, MCP, and both host lanes where supported.
5. **Engine lane ADR** — stock actor as supported now; custom archive build requirements; greenfield VM independence.
6. **Update `DOSH-AGENT-NATIVE-ARCHITECTURE.md`** — incorporate the corrected task/slice/checkpoint semantics and reference this repository research.

The key product decision is now evidence-based:

> The fastest route to a useful agentic browser is not to pause everything for a new JS engine. First make the current browser truthful, safe, deterministic at its control boundaries, durable, and accessible to external multi-model agents. Then replace the execution engine behind a stable adapter when the new kernel can prove stronger semantics.

---

## Appendix A: High-signal source references

- Engine archive selection: `doshgentv8/core/Cargo.toml:8-14`.
- Resident actor: `doshgentv8/core/src/actor.rs:584-664`.
- DCP pointer types: `doshgentv8/core/src/protocol.rs:50-159`.
- DCP dispatcher: `doshgentv8/core/src/lib.rs:1342-1462`.
- Runtime action handling: `dosh-browser-core/src/runtime.rs:515-558`.
- Action ref construction: `dosh-browser-core/src/actions.rs:189-219`.
- Session click path: `dosh-browser-core/src/session.rs:492-535`.
- Global server session: `dosh-browser-core/src/server.rs:21-40`.
- Global MCP session/store: `dosh-browser-core/src/bin/dosh.rs:324-340`.
- Runtime map versus pool: `dosh-browser-core/src/session.rs:194-199,230-253`.
- Hardcoded shell engine results: `dosh-browser-core/src/shell.rs:951-1011`.
- Shell checkpoint/restore: `dosh-browser-core/src/shell.rs:619-662`.
- Shared mini-DOM source dependency: `dosh-browser-core/src/runtime.rs:23-24`.
- Python LLM loop: `webresearch/agent.py:322-469`.
- Rust local ask: `dosh-browser-core/src/agent.rs:58-96`.
- Greenfield VM plan: `doshgentv8/base/DOSH-AGENT-NATIVE-ARCHITECTURE.md`.

## Appendix B: Non-verification statement

No claim in this document should be read as “the tests pass,” “the engine builds,” or “the release contains the patched V8.” Those require separate execution. The findings here are static code/contract evidence and architectural recommendations. Runtime reproduction, memory-safety dynamic checks, and release-artifact inspection remain follow-up work.
