# doshgentv8: Agent-Native Execution Engine Architecture

**Status:** Product direction and proposed target architecture; not an implementation claim.

**Document purpose:** Preserve the design motive, make the compatibility boundary explicit, and provide a file-by-file implementation map for a new agent-native JavaScript execution engine.

**Scope of this document:** doshgentv8 and its interfaces to the dosh-browser host. It does not authorize importing third-party source, adding build dependencies, or changing the build in the current tree.

---

## 1. Movement and motive

dosh-browser is an agent-native browser, not a human browser with an automation API attached. Its execution engine must therefore optimize for a different primary user: a machine controller that needs bounded execution, inspectable state, explicit authority, deterministic replay, structured evidence, and safe operation of many long-lived sessions.

The decision is:

> Use ECMAScript and web behavior as the compatibility contract. Use V8 and Apple's JavaScriptCore (JSC) as reference implementations and sources of techniques. Do not preserve V8 or JSC internal architecture merely for continuity. Build doshgentv8 as a new deterministic actor-based execution kernel with a JavaScript frontend.

The existing V8 changes are a **transitional research lane**. They are useful for testing host-clock injection, PRNG control, fuel, capabilities, and heap telemetry. They are not the final execution architecture.

The final engine must be designed around these questions:

- What exactly is executing?
- Which session and security principal owns it?
- How much CPU, memory, DOM work, network work, and wall time may it consume?
- Which external effects is it allowed to cause?
- What state did it observe?
- Can the host pause, inspect, checkpoint, restore, migrate, replay, or terminate it?
- Can another engineer reproduce the same result from the same evidence?

A conventional isolate plus flags cannot answer all of these cleanly. They must be kernel properties.

---

## 2. Architectural decisions

### ADR-0001: Compatibility boundary

**Decision:** Implement ECMAScript behavior and the web integration required by dosh-browser. Do not require V8 ABI compatibility, Node compatibility, Chromium extension compatibility, CDP compatibility, V8 object layout, V8 snapshot format, or V8 internal API compatibility.

**Consequences:**

- V8 and JSC source may be studied as reference material.
- Reusing an algorithm is acceptable after correctness, provenance, and license review.
- Copying an internal object model is not an architectural goal.
- Test262 and captured web behavior are more important compatibility targets than V8 unit tests.

### ADR-0002: Actor kernel, not an isolate-centric kernel

**Decision:** The primary runtime object is a schedulable actor with owned execution state, owned heap state, a mailbox, a policy, and explicit external capabilities.

An isolate, if that term is retained, becomes an implementation detail rather than the public unit of control.

### ADR-0003: Determinism at actor boundaries

**Decision:** JavaScript execution within a turn remains run-to-completion unless a defined trap or suspension occurs. Determinism is provided by explicit turn scheduling, virtual time, seeded random streams, recorded external results, and stable sequence numbers.

The engine does not attempt to make arbitrary native parallel execution deterministic. It makes the delivery of its observable results deterministic.

### ADR-0004: No ambient authority

**Decision:** Page code cannot directly access DOM, network, storage, layout, process state, or host memory. Every effect passes through a typed capability and policy check.

The control realm is not inherently omnipotent. It is an actor holding a different capability set.

### ADR-0005: Memory and execution failure are actor faults, not process failure

**Decision:** Resource exhaustion must produce a structured, recoverable actor fault whenever execution reaches a defined VM boundary. It must not depend on process OOM behavior.

The current transitional V8 allocation-fuel fatal trap does not satisfy this final requirement.

### ADR-0006: Checkpoint and replay are native state model requirements

**Decision:** Persistent state cannot contain raw native pointers, borrowed host addresses, thread IDs, or unversioned resource references. Checkpointing is designed with the heap, bytecode, scheduler, and host protocol, not retrofitted afterward.

### ADR-0007: C++ execution kernel, Rust browser host, narrow C ABI

**Decision:** The initial production architecture uses:

- C++20 for the interpreter, bytecode, JavaScript frontend, actor heap, GC, and deterministic scheduler;
- Rust for browser/session orchestration, DOM ownership, networking, storage, and agent workflows;
- a versioned C ABI between them, wrapped by safe Rust and safe C++ owner types;
- no C++ STL types, C++ exceptions, RTTI requirements, or C++ object ownership crossing the ABI;
- no production Go runtime. Go's goroutine/channel ideas inform the actor model only.

This is a deliberate boundary, not a prohibition on later experiments. Alternative parser, JIT, or verifier implementations must conform to the same bytecode and state contracts.

### ADR-0008: Correct deterministic interpreter before optimizing compiler

**Decision:** The first backend is a verified register-bytecode interpreter. Baseline/optimized native code is a later, optional tier. No JIT tier may bypass fuel, memory accounting, capabilities, GC safepoints, or checkpoint rules.

---

## 3. Non-negotiable runtime invariants

1. **Ownership invariant:** Every mutable heap object has exactly one owning actor.
2. **Reference invariant:** Cross-actor references are opaque IDs, messages, or immutable shared objects, never ordinary mutable heap pointers.
3. **Authority invariant:** Every host effect carries actor identity, capability, operation ID, and causality ID.
4. **Budget invariant:** Every unbounded loop, allocation path, host wait, queue, and external resource class has a policy limit.
5. **Turn invariant:** A turn ends in exactly one explicit outcome: `completed`, `yielded`, `waiting`, `trapped`, or `checkpoint_ready`.
6. **Microtask invariant:** Microtasks are drained according to ECMAScript rules and are included in turn fuel/checkpoint state.
7. **Clock invariant:** No page-visible wall-clock read bypasses the actor clock service.
8. **Randomness invariant:** Page-visible randomness comes from named, seeded actor streams.
9. **Memory invariant:** External resources charged to an actor are included in its memory policy.
10. **Trap invariant:** Resource faults leave valid, inspectable actor state and do not masquerade as ordinary OOM.
11. **Snapshot invariant:** A checkpoint contains no native pointer and no implicit dependency on process address layout.
12. **Replay invariant:** Replaying a checkpoint and event log cannot silently repeat an external side effect.
13. **DOM invariant:** JavaScript-visible DOM reads observe one document epoch; mutations produce explicit transactions and new epochs.
14. **Telemetry invariant:** Observability is structured data and does not require parsing human log strings.
15. **Optimization invariant:** A faster backend may change performance, never observable policy or scheduling semantics.
16. **Host invariant:** Host callbacks cannot reenter the same actor unless a specific reentrant contract says so.
17. **ABI invariant:** ABI structures carry version and size; unsupported versions fail explicitly.
18. **Evidence invariant:** Coverage and unavailable operations are reported honestly; silent truncation or fake success is a bug.

---

## 4. Reference lanes

### 4.1 V8 lane: transitional executable research

Useful concepts already explored in the current tree:

- virtual host clock;
- seeded PRNG;
- execution fuel;
- eval/WASM/action capability gates;
- control/page realm distinction;
- microtask checkpoint callbacks;
- allocation-count tripwire;
- heap statistics marshalling.

Do not carry forward by default:

- process-wide flag soup as policy;
- fatal resource handling;
- implicit platform clocks and random sources;
- ambient native authority;
- raw isolate state as a session snapshot;
- generated-code paths that bypass metering;
- human-oriented stack/error text as the primary telemetry.

### 4.2 JavaScriptCore lane: techniques to study

JSC is a valuable second reference because its architecture differs from V8 in useful ways. Study these areas before implementing the corresponding doshgentv8 subsystem:

| JSC concept | What to learn | doshgentv8 adaptation |
|---|---|---|
| LLInt and offline bytecode design | Low-level interpreter dispatch, bytecode metadata, instruction stream organization | Verified register bytecode with explicit fuel and safepoint metadata |
| Baseline JIT | Low-latency native tier and inline-cache placement | Optional later baseline tier with the same metering contract |
| DFG/FTL tiering | Speculation, OSR, watchpoints, profiling | Deferred; deterministic profiles and explicit OSR safepoints only |
| Bytecode checkpoints and unwind metadata | Mapping native/interpreter state back to bytecode | Mandatory resumable frame descriptions from the first backend |
| IsoHeap/subspace-style segregation | Type/size/security segregation and allocator specialization | Actor-local regions and typed subspaces, not process-wide shared mutable subspaces |
| Generational and constraint-based GC | Write barriers, remembered sets, GC constraints | Actor-local GC with scheduler-visible debt and checkpoint safepoints |
| JIT-less and embedded deployment modes | Reduced native-code attack surface | Interpreter-first production mode |
| Inline caches and structure transitions | Property access optimization | Actor-owned cache state, serializable or deliberately reset at checkpoints |
| WASM tiers and exception integration | Trap boundaries and host transitions | Unified typed VM fault model, not ad hoc traps |

These are study directions, not claims that JSC source was audited for this document. Any actual source import requires a separate file-level provenance and license review. WebKit's DOM, browser process model, and platform policy are not imported as part of the JSC execution-engine study.

### 4.3 Go lane: conceptual only

- Goroutine -> resumable actor turn, not a preemptive JS thread.
- Channel -> typed capability port and mailbox.
- Scheduler -> deterministic host worker scheduling, not nondeterministic actor delivery.
- Panic -> typed actor fault, not a process crash.
- GC -> actor-local ownership and collection, not a universal shared heap.

---

## 5. Process and actor topology

### 5.1 Ownership tree

```text
DoshEngine (process lifetime)
├── DoshHostBinding (C ABI owner; no VM execution on arbitrary threads)
├── DoshScheduler (deterministic ready queues and virtual-time ordering)
├── DoshSharedCodeRegistry (immutable verified code and snapshots)
├── DoshSession[session_id]
│   ├── DoshPolicyManifest
│   ├── DoshVirtualClock
│   ├── DoshRandomStreamRegistry
│   ├── DoshEventLog
│   ├── DoshActor[actor_id]
│   │   ├── DoshMailbox
│   │   ├── DoshHeap
│   │   ├── DoshRealm[realm_id]
│   │   ├── DoshCapabilitySet
│   │   ├── DoshExternalResourceLedger
│   │   └── DoshExecutionStack
│   └── DoshTelemetryRing
└── Host-owned browser services
    ├── authoritative DOM shards
    ├── layout snapshots
    ├── network/storage implementations
    └── agent/session control plane
```

### 5.2 Actor assignment rules

- A top-level agent session has a session ID and independent policy, PRNG, virtual clock, event log, and quotas.
- A control actor owns trusted agent procedures and only the capabilities explicitly granted to it.
- A page actor owns one or more realms that require synchronous same-heap interaction.
- Same-origin related browsing contexts that can synchronously access each other must remain in the same actor unless the integration layer provides a semantics-preserving alternative.
- Cross-origin frames normally use separate actors and structured messages.
- Workers are actors with explicit parent capabilities and message ports.
- An actor may migrate between host worker threads only at a turn/checkpoint boundary.

### 5.3 Actor state machine

```text
Created -> Ready -> Running -> Ready
                    │   ├──> Waiting --host result/message--> Ready
                    │   ├──> Suspended --resume--> Ready
                    │   ├──> Checkpointing -> Suspended
                    │   └──> Faulted -> Inspecting -> Ready | Terminated
                    └──────> Terminating -> Terminated
```

Only the scheduler changes `Ready -> Running`. Only actor-thread code mutates actor heap state. Snapshot readers use immutable checkpoint data, not live actor memory.

---

## 6. Identity and versioning

Use fixed-width IDs in all persistent and cross-language structures:

- `DoshSessionId`: 64-bit;
- `DoshActorId`: 64-bit, unique within the engine;
- `DoshRealmId`: 32-bit within an actor;
- `DoshDocumentEpoch`: 64-bit per document shard;
- `DoshTurnId`: 64-bit per actor;
- `DoshOperationId`: 64-bit per host operation;
- `DoshCauseId`: 128-bit or a fixed 64-bit engine ID plus 64-bit local sequence;
- `DoshCodeId`: content hash plus format version;
- `DoshCheckpointId`: content hash of canonical checkpoint bytes;
- `DoshResourceId`: 64-bit host-owned ID with generation counter.

A resource handle includes a generation so a stale handle cannot silently refer to a newly allocated resource.

Every response and telemetry event identifies the state against which it was produced. `StaleDocumentEpoch` is a normal, actionable result, not a generic error.

---

## 7. Turn execution and deterministic scheduling

### 7.1 Turn lifecycle

1. Scheduler selects the lowest ready key.
2. Actor enters `Running` and receives a turn descriptor.
3. Interpreter starts or resumes at an explicit bytecode PC.
4. Fuel and allocation checks run at their defined points.
5. Host calls either complete synchronously without reentry or suspend the actor.
6. At the JS turn boundary, the microtask queue drains under the same policy.
7. Engine performs checkpoint/GC work if requested and safe.
8. Engine returns one `DoshTurnOutcome` and updated telemetry cursors.

### 7.2 Ready ordering

Recommended key:

```text
(virtual_deadline, scheduling_class, ready_sequence, actor_id)
```

- `virtual_deadline`: timers and delayed work;
- `scheduling_class`: bounded priority classes, not arbitrary numeric priority;
- `ready_sequence`: monotonic session sequence at readiness;
- `actor_id`: final stable tie-breaker.

The host worker that executes an actor is not part of the ordering decision.

### 7.3 Turn outcome

```cpp
struct DoshTurnOutcome {
  DoshTurnKind kind;
  DoshFault fault;
  uint64_t turn_id;
  uint64_t next_ready_sequence;
  uint64_t fuel_remaining;
  uint64_t allocation_budget_remaining;
  uint64_t host_sequence;
  uint64_t telemetry_sequence;
};
```

This is an architectural structure, not final ABI layout.

### 7.4 Preemption points

- every interpreter instruction, using a dispatch budget cursor;
- loop backedges and function calls;
- allocation slow paths and large-allocation entry;
- GC safepoints;
- blocking host operations;
- long-running builtins with explicit step accounting;
- optional native-tier OSR checks.

Native host work must be bounded or cancellable. Interpreter fuel cannot bound an unbounded C++ or Rust callback.

### 7.5 Waiting and cancellation

A waiting actor stores a typed wait descriptor containing operation ID, wait reason, capability, and cancellation state. Cancellation does not erase the outcome from the evidence log. Late host replies are recorded as stale and cannot resume a terminated actor.

---

## 8. Microtasks, timers, clocks, and randomness

### 8.1 Microtasks

Each realm owns its ECMAScript microtask queue. Actor scheduling does not reorder jobs within that queue. Queue operations record:

- enqueue turn and cause;
- code ID and entry PC;
- target realm;
- sequence number;
- execution status.

A checkpoint taken at a microtask boundary stores remaining jobs in order. A checkpoint inside a job requires resumable frame state and must not silently restart completed host effects.

### 8.2 Virtual time

Maintain separate values:

- wall-like epoch time for `Date`/`Temporal`;
- monotonic time for `performance` and scheduler deadlines;
- optional deterministic time step per turn.

All page-visible time reads go through `DoshClock`. No builtin calls the platform clock directly.

### 8.3 Random streams

Use named streams, for example:

- `math.random`;
- UUID generation;
- layout-insensitive internal IDs;
- fuzz/test-only schedule variation.

A counter-based PRNG is preferred because a stream can be checkpointed as algorithm ID, key, and counter. Derive actor streams from the session seed and stable stream name. Do not share a mutable global RNG between actors.

---

## 9. Capability and host-call model

### 9.1 Capability descriptors

A capability contains:

- operation kind;
- resource kind and resource ID/generation;
- allowed read/write/action bits;
- document/session scope;
- expiration or revocation state;
- quota class.

Capabilities are checked on every host call, not only at actor creation.

### 9.2 Synchronous versus asynchronous calls

Use synchronous capability calls only when all are true:

- required for synchronous JavaScript semantics;
- guaranteed nonblocking;
- no VM reentry;
- bounded work;
- actor-thread DOM/service access is valid;
- result fits an explicit size/work budget.

Examples: DOM attribute read, node identity lookup, small style/layout snapshot read.

Use asynchronous calls for network, storage I/O, large exports, expensive layout, and anything that can block.

### 9.3 Host call evidence

Each call records:

- session/actor/realm;
- turn and operation ID;
- cause ID;
- capability and policy decision;
- request/result schema version;
- document/resource epoch;
- result status and byte counts;
- fuel charged for marshalling/work;
- whether the result came from live execution or replay.

---

## 10. DOM and renderer integration

### 10.1 Authority

The Rust browser host owns the authoritative mutable DOM shard. The C++ engine does not maintain a second independently authoritative DOM.

A JS DOM wrapper stores a `DoshNodeId` and document epoch, not a Rust pointer.

### 10.2 Synchronous page DOM behavior

Web JavaScript requires synchronous DOM access. Therefore an actor-local synchronous host binding is required for bounded DOM reads and mutations. This does not mean all browser services are synchronous.

The host binding must guarantee:

- the DOM shard is accessed only by the actor currently owning its execution lease;
- no Rust callback reenters the actor;
- reads observe the documented epoch;
- mutations produce a transaction record and increment the epoch;
- stale wrapper/resource generations fail explicitly;
- result buffers have explicit lifetimes and size limits.

### 10.3 Layout

Layout runs outside the JS heap and produces immutable, content-addressed snapshots:

```text
document epoch + layout inputs + viewport + style version -> layout snapshot ID
```

A page actor can synchronously read a small bounded view of an already-current layout snapshot. If layout is dirty or too expensive, the engine returns a defined pending/stale result according to the web-compatible layout flush policy. It must not fabricate geometry.

### 10.4 Agent DOM operations

Agent reads use structured queries against explicit epochs. Agent actions use transaction requests containing expected epoch and idempotency key. The result includes the new epoch, affected node IDs, and unsupported controls rather than claiming success for inert elements.

### 10.5 Optional optimization

A per-actor read cache may cache immutable DOM/layout views keyed by epoch. It must be discarded on epoch change and cannot become a second mutable DOM authority.

---

## 11. Bytecode and JavaScript execution pipeline

```text
Source + source ID
  -> lexer
  -> parser
  -> scoped AST
  -> bytecode generator
  -> bytecode verifier
  -> immutable DoshCodeBlock
  -> actor instantiation
  -> interpreter
  -> optional profile-guided baseline/optimized tier
```

### 11.1 Instruction format

Recommended initial format:

- register-based instructions;
- fixed 32-bit primary instruction word;
- 8-bit opcode plus fixed operand fields;
- explicit wide/prefix forms for large registers/constants;
- constant-pool indices instead of embedded pointers;
- source positions and function metadata in separate tables;
- exception, stack-map, safepoint, and host-call metadata side tables;
- canonical encoding for content hashing.

### 11.2 Verification

Before code becomes executable, verify:

- opcode and operand ranges;
- register bounds and frame-size overflow;
- jump targets and alignment;
- exception handler ranges;
- constant-pool types;
- stack depth at every join;
- call/host-call descriptor validity;
- no unverified native pointer fields;
- deterministic canonical encoding.

### 11.3 Frames

A frame stores code ID, bytecode PC, register window, receiver/function references, exception state, and caller index. Native frame pointers are derived runtime data and never persisted.

Every call/host-call/throw site has enough metadata to reconstruct a structured JS stack without parsing a human string.

### 11.4 Inline caches and speculation

Initial implementation can omit inline caches. Later caches are actor-owned and keyed by stable map/structure IDs. Checkpoint behavior must be explicit: serialize portable cache state or discard and warm again. Native code addresses and watchpoint pointers are never serialized.

### 11.5 Builtins

Builtins are categorized:

- pure JS builtins compiled to bytecode;
- deterministic native builtins with fuel accounting;
- allocating native builtins with GC/budget checks;
- host builtins requiring capabilities;
- unbounded native builtins forbidden until instrumented.

---

## 12. Actor heap and garbage collection

### 12.1 Heap regions

1. **Shared immutable region:** verified code, constants, immutable snapshots.
2. **Actor young generation:** short-lived JS objects.
3. **Actor old generation:** long-lived JS objects and maps.
4. **External resource ledger:** backing stores, host buffers, DOM/layout views, WASM memories, and native allocations charged to the actor.

The initial implementation should favor a simple actor-local copying young generation and mark-compact old generation over concurrent GC complexity.

### 12.2 Object representation

Use tagged values and actor-local object references. A compressed reference may be an offset into an actor cage or an index into a segmented object table. The representation must support precise tracing and portable serialization.

Do not expose object addresses in telemetry, DCP, DOM handles, snapshots, or error messages.

### 12.3 Root discipline

- Interpreter registers and VM frames are precisely traced.
- C++ heap references across safepoints use handles or rooted owner types.
- GC safepoints require complete stack maps.
- Host calls cannot retain unregistered raw object pointers.
- Cross-actor values are cloned/messaged or immutable shared references.

### 12.4 GC scheduling

GC debt is observable scheduler state. Collection happens at actor-local safepoints and does not stop unrelated actors. Initial GC is deterministic stop-the-actor; incremental/concurrent collection requires a later proof that it preserves checkpoint and replay behavior.

Shared immutable code uses atomic ownership or epoch reclamation, not actor graph traversal.

### 12.5 Memory accounting

Track at minimum:

- committed and live bytes by region;
- external bytes by resource class;
- allocation count and allocation-checkpoint count;
- GC count, GC debt, and last GC epoch;
- session aggregate and per-actor budget;
- emergency reserve state.

A byte budget and an allocation-count budget serve different purposes and both may be enabled.

### 12.6 Recoverable allocation trap

Allocation returns a typed result; it does not immediately abort the process.

At a defined VM boundary:

1. mark actor `Faulted(MemoryExhausted)`;
2. preserve PC, frame metadata, heap epoch, and resource ledger;
3. use preallocated fault storage, not the exhausted JS heap;
4. stop running new page code;
5. return a structured outcome to the host;
6. allow host inspection/checkpoint/termination or an explicit policy-driven recovery path.

Internal C++ operations that cannot return failure must not perform chargeable allocations. Their reserve requirements must be documented.

---

## 13. Unified fault model

Suggested fault classes:

- `FuelExhausted`;
- `MemoryExhausted`;
- `GcDebtExceeded`;
- `CapabilityDenied`;
- `StaleCapability`;
- `StaleDocumentEpoch`;
- `HostWouldBlock`;
- `HostCancelled`;
- `BytecodeInvalid`;
- `StackOverflow`;
- `UnhandledJsException`;
- `SnapshotUnsupported`;
- `ReplayConflict`;
- `PolicyInvalid`;
- `InternalInvariant`.

A JS exception, a WASM trap, a resource fault, a host rejection, and a VM invariant failure are different kinds of evidence and must not be collapsed into one status.

---

## 14. Checkpoint, restore, migration, and replay

### 14.1 Checkpoint contents

- format version and engine compatibility hash;
- session/actor/realm identities;
- actor state and current turn;
- code IDs and resumable bytecode frames;
- serialized heap objects and maps;
- microtask/mailbox queues;
- scheduler cursors and ready sequence;
- virtual clock and PRNG states;
- capabilities and revocation state;
- DOM/layout snapshot IDs and document epochs;
- external resource descriptors and generations;
- pending host calls and their replay policy;
- GC epochs and resource usage;
- event-log cursor.

### 14.2 Checkpoint rules

- Take heap snapshots only at GC-safe points.
- Canonicalize map/object ordering and integer encoding.
- Never serialize native addresses, pthread IDs, STL layout, or borrowed buffer pointers.
- Native optimized frames must return to an interpreter-resumable state or declare the checkpoint unsupported.
- Shared immutable code is referenced by content hash and verified on restore.

### 14.3 Replay policy per host effect

Each operation is marked:

- `ReplayRecordedResult`;
- `ReexecuteIdempotent`;
- `ReconcileWithHost`;
- `NotReplayable`.

The default is not re-execution. A network request must not silently happen twice.

### 14.4 Session migration

Migration is restore on another engine worker/host with capability revalidation. Resource IDs are resolved through the host registry, never assumed valid merely because numeric IDs match.

---

## 15. Structured telemetry

Per-session SPSC event streams are sufficient initially. Each event includes:

- global/session sequence;
- timestamp from the selected host/virtual clock;
- session, actor, realm, turn;
- cause and operation IDs;
- event kind and schema version;
- document/resource epoch;
- bounded payload or payload arena reference.

Core events:

- turn start/end;
- bytecode trap and structured stack;
- allocation budget change/exhaustion;
- GC start/end with region counters;
- host request/result/policy denial;
- DOM transaction/version change;
- microtask enqueue/drain;
- checkpoint/restore/replay decision;
- actor state transition.

An overflow increments a dropped-event counter and records a gap event when possible. It never blocks unbounded page execution or silently pretends the trace is complete.

---

## 16. C++/Rust ABI

### 16.1 ABI rules

- fixed-width integers for lengths, counts, IDs, and budgets;
- `struct_size` and `abi_version` on every versioned structure;
- explicit ownership and buffer lifetime;
- no `size_t` in a stable cross-language persistent/ABI contract;
- no packed compiler-dependent C++ objects;
- no exceptions or STL across the boundary;
- every callback carries an opaque host context;
- invalid handle/version returns an error, not UB.

The existing `include/dosh_protocol.h` is transitional. Its `size_t` statistics fields and pointer-based packets should not become the permanent cross-version ABI merely because they already exist.

### 16.2 Borrowed buffers

A borrowed result includes buffer ID, pointer, length, and release operation. The owner remains valid until release/ack. Checkpoint serialization copies the data; it never stores the borrowed pointer.

### 16.3 Threading

Engine creation/destruction and host callbacks occur through an engine owner with explicit thread rules. Actor methods execute only under an actor execution lease. Rust wrappers encode send/sync constraints where practical.

### 16.4 Command families

- session create/configure/close;
- actor create/suspend/resume/terminate;
- compile/instantiate/invoke/step;
- capability grant/revoke;
- DOM read/transaction;
- host result delivery/cancellation;
- checkpoint/restore/replay;
- telemetry read/ack;
- resource release.

`Eval` is a convenience, not the complete agent protocol. Structured `Query`, `Act`, `Step`, `Checkpoint`, and `GetEvidence` operations are first-class.

---

## 17. File-by-file target architecture

The following is the **proposed new-engine tree**. These files do not exist merely because they are listed here. Implementation should create them in vertical slices, not as empty scaffolding.

Root: `doshgentv8/base/dosh/`.

### 17.1 Public C++ owner API and C ABI

| File | Responsibility | Must not contain |
|---|---|---|
| `include/dosh/engine.h` | Safe C++ `DoshEngine` owner and creation parameters | JS object internals |
| `include/dosh/session.h` | Session owner and explicit thread/lease API | Direct mutable actor pointers |
| `include/dosh/types.h` | Fixed-width IDs, status, epochs, buffers | ABI-unstable STL in C ABI |
| `include/dosh/abi.h` | Versioned C ABI declarations used by Rust | C++ classes/exceptions |
| `include/dosh/capabilities.h` | Public capability and policy schema | Page-accessible authority |
| `include/dosh/checkpoint.h` | Checkpoint import/export owner API | Raw heap format internals |
| `include/dosh/telemetry.h` | Event schemas, cursors, gap reporting | Human log parsing contract |
| `abi/abi.cc` | C++-to-C thunks, validation, exception barrier | Scheduler/heap logic |
| `abi/host_binding.h/.cc` | Host function-table validation and callback adapters | DOM/network implementation |
| `abi/buffer.h/.cc` | Buffer ownership, release, lifetime accounting | Borrowed-pointer persistence |
| `abi/version.h` | ABI versions, minimum supported versions, feature bits | Runtime mutable policy |

### 17.2 Engine and scheduler

| File | Responsibility | Key dependency rule |
|---|---|---|
| `core/engine.h/.cc` | Engine singleton ownership and worker coordination | Does not execute JS inline |
| `core/engine_config.h` | Immutable engine creation configuration | No process-global mutable flags |
| `core/scheduler.h/.cc` | Deterministic ready selection and turn dispatch | No DOM/network calls |
| `core/ready_queue.h/.cc` | Stable ready keys and cancellation | No actor heap mutation |
| `core/worker_pool.h/.cc` | Host worker execution and lease handoff | Scheduling decisions remain deterministic |
| `core/turn.h` | Turn descriptor/outcome and end-state definitions | No ABI packing assumptions |
| `core/actor_lease.h/.cc` | Proves exclusive actor execution access | No copyable lease |
| `core/wait_registry.h/.cc` | Pending waits, late replies, cancellation | Host results validated before enqueue |
| `core/operation_id.h` | Operation/cause sequencing | No pointer-derived IDs |

### 17.3 Sessions and actors

| File | Responsibility | Key dependency rule |
|---|---|---|
| `session/session.h/.cc` | Session ownership, quotas, policy, clocks, log | No direct page heap access |
| `session/session_registry.h/.cc` | Stable session lookup and teardown | No raw pointer persistence |
| `session/policy.h/.cc` | Validated per-session resource/capability policy | No hidden global defaults |
| `session/policy_schema.h` | Versioned policy schema and limits | Unknown fields rejected/versioned |
| `session/clock.h/.cc` | Virtual epoch/monotonic time | No direct OS clock in builtins |
| `session/random.h/.cc` | Named counter-based PRNG streams | No global RNG state |
| `session/event_log.h/.cc` | Ordered external decisions and replay cursor | No unbounded memory-only log |
| `actor/actor.h/.cc` | Actor state machine and owned subsystems | Only actor thread mutates heap |
| `actor/mailbox.h/.cc` | Ordered structured messages and backpressure | No mutable object sharing |
| `actor/realm.h/.cc` | Global object, realm policy, microtask association | No implicit control privileges |
| `actor/capability_set.h/.cc` | Capability storage/check/revocation | Every host call rechecks |
| `actor/resource_ledger.h/.cc` | External byte/resource quotas and generations | External memory included in budget |
| `actor/fault.h/.cc` | Typed actor faults and preallocated evidence | No allocation required to report OOM |
| `actor/execution_stack.h/.cc` | Resumable frame stack owner | Native pointers not persisted |

### 17.4 Bytecode and code registry

| File | Responsibility | Key dependency rule |
|---|---|---|
| `bytecode/opcode.h` | Opcode enum and semantic version | No engine-dependent numeric drift |
| `bytecode/instruction.h` | 32-bit instruction encoding/decoding | Overflow-safe operand access |
| `bytecode/code_block.h/.cc` | Immutable instructions/constants/metadata | No native pointers in persistent form |
| `bytecode/constant_pool.h/.cc` | Typed constants and canonical references | Bounds checked by verifier |
| `bytecode/metadata.h` | Source, exception, stack-map, safepoint tables | Required for resume/stack evidence |
| `bytecode/verifier.h/.cc` | Control-flow, type, bounds, join verification | Unverified code never executes |
| `bytecode/canonical.h/.cc` | Canonical bytes and code content hashing | Platform endianness not implicit |
| `bytecode/code_registry.h/.cc` | Shared immutable code lifetime | Mutable IC state stays in actor |
| `bytecode/disassembler.h/.cc` | Agent-readable structured disassembly | Not required for execution |

### 17.5 JavaScript frontend

| File | Responsibility | Notes |
|---|---|---|
| `js/source.h/.cc` | Source text, origin, source ID, line mapping | No ambient filesystem access |
| `js/token.h` | Token kinds and fixed-width spans | No locale-dependent behavior |
| `js/lexer.h/.cc` | ECMAScript lexical grammar | Bounded input and diagnostics |
| `js/ast.h` | Scoped AST nodes and source positions | Arena lifetime explicit |
| `js/parser.h/.cc` | Parser and early errors | No runtime side effects |
| `js/scope.h/.cc` | Binding/module/realm scope resolution | Capability separation preserved |
| `js/bytecode_generator.h/.cc` | AST to register bytecode | Emits resumable metadata |
| `js/module_record.h/.cc` | Module graph and evaluation state | Loader is a host capability |
| `js/eval_gate.h/.cc` | Direct/indirect eval and Function policy | One mandatory choke point |
| `js/structured_stack.h/.cc` | Structured exception stack records | Human string is derived only |
| `js/builtins/builtin_registry.h/.cc` | Builtin IDs, fuel class, capability class | No unclassified native builtin |
| `js/builtins/array.cc` | Array builtins | Allocation/fuel checks |
| `js/builtins/object.cc` | Object builtins | Property semantics |
| `js/builtins/string.cc` | String builtins | Large-work accounting |
| `js/builtins/promise.cc` | Promise reactions and microtasks | Queue ordering preserved |
| `js/builtins/date.cc` | Date implementation | Actor clock only |
| `js/builtins/temporal.cc` | Temporal implementation | Actor clock/calendar capabilities only |
| `js/builtins/math.cc` | Math builtins | Named PRNG stream for random |
| `js/builtins/json.cc` | JSON parse/stringify | Depth/byte budgets |
| `js/builtins/typed_array.cc` | TypedArray/DataView | External bytes charged |

Additional builtin files are split by specification area as coverage grows; no monolithic `builtins.cc` dumping ground.

### 17.6 Interpreter and runtime semantics

| File | Responsibility | Key dependency rule |
|---|---|---|
| `vm/interpreter.h/.cc` | Dispatch loop and turn return | Every dispatch observes fuel policy |
| `vm/dispatch.h` | Portable dispatch helpers | Computed goto is optional optimization |
| `vm/handlers.h` | Generated/shared opcode handler declarations | Handlers end at explicit continuation/trap |
| `vm/handlers_control.cc` | Calls, returns, jumps, exceptions | PC/frame invariants |
| `vm/handlers_object.cc` | Property and allocation operations | Heap and map access only through owners |
| `vm/handlers_host.cc` | Host-call entry/suspend/resume | Capability and reentry checks |
| `vm/register_file.h/.cc` | Tagged register windows | Precisely traceable |
| `vm/frame.h` | Portable frame record and native-frame mapping | Native SP/FP not persisted |
| `vm/fuel.h/.cc` | Fuel accounting and exhaustion | Shared by all backends |
| `vm/safepoint.h/.cc` | Poll reasons and safe VM boundaries | GC/checkpoint/termination coordination |
| `vm/exception.h/.cc` | JS throw/unwind distinct from actor fault | No conflation with process OOM |
| `vm/microtask_queue.h/.cc` | ECMAScript queue and checkpoint state | Per-realm FIFO semantics |
| `vm/property_lookup.h/.cc` | Ordinary property semantics | No hidden host access |
| `vm/map.h/.cc` | Object shapes/transitions | Actor-owned mutable maps |
| `vm/inline_cache.h/.cc` | Optional actor-owned property caches | Portable/resettable checkpoint behavior |
| `vm/wasm_bridge.h/.cc` | Later WASM trap/fuel integration | WASM cannot bypass actor policy |

### 17.7 Heap and GC

| File | Responsibility | Key dependency rule |
|---|---|---|
| `heap/tagged.h` | Tagged value representation | Host cannot forge trusted tags |
| `heap/object.h` | Object header and layout descriptors | No native address exposure |
| `heap/object_ref.h` | Actor-local compressed/indexed reference | Generation/validity rules explicit |
| `heap/heap.h/.cc` | Actor heap owner and region accounting | No global mutable heap |
| `heap/allocator.h/.cc` | Checked allocation entry points | Returns typed failure |
| `heap/young_space.h/.cc` | Copying nursery | Actor-local safepoint only |
| `heap/old_space.h/.cc` | Mark-compact old generation | Precise tracing |
| `heap/large_object_space.h/.cc` | Large objects and budget checks | External/committed bytes included |
| `heap/handle.h` | C++ rooted handle scopes | No unrooted reference across safepoint |
| `heap/marking_visitor.h/.cc` | Object graph tracing | Cross-actor IDs not traced as pointers |
| `heap/write_barrier.h/.cc` | Generational remembered-set updates | Required in every backend |
| `heap/card_table.h/.cc` | Old-to-young tracking | Bounded accounting |
| `heap/gc.h/.cc` | Collection phases and epoch counters | Actor-local deterministic scheduling |
| `heap/gc_debt.h/.cc` | Debt metrics and scheduler request | No invisible runaway GC work |
| `heap/serializer.h/.cc` | Canonical heap checkpoint encoding | No native pointers |
| `heap/deserializer.h/.cc` | Verified heap restoration | Validates sizes/ref targets before allocation |
| `heap/emergency_reserve.h/.cc` | Preallocated fault/unwind support | Not available to page allocations |

### 17.8 Capabilities and browser bindings

| File | Responsibility | Key dependency rule |
|---|---|---|
| `capabilities/capability.h` | Typed capability and generation | Forgery-resistant owner checks |
| `capabilities/host_call.h/.cc` | Request validation and evidence | No direct arbitrary callback from JS |
| `capabilities/host_result.h/.cc` | Synchronous result or suspended wait | Lifetime and epoch validation |
| `capabilities/replay_policy.h` | Effect replay classification | Default not re-execute |
| `bindings/dom/node_ref.h` | Node ID + generation + document epoch | No Rust/DOM pointer |
| `bindings/dom/dom_objects.h/.cc` | JS wrappers and property semantics | Calls bounded host DOM API |
| `bindings/dom/dom_snapshot.h/.cc` | Borrowed immutable read views | Explicit release and byte cap |
| `bindings/dom/dom_transaction.h/.cc` | Mutation transactions/idempotency | Expected epoch required |
| `bindings/layout/layout_view.h/.cc` | Immutable layout snapshot access | No fabricated geometry |
| `bindings/network/network_port.h/.cc` | Async network requests | Policy, cancellation, replay evidence |
| `bindings/storage/storage_port.h/.cc` | Async storage access | Session namespace and quotas |
| `bindings/worker/worker_port.h/.cc` | Worker actor creation/messages | Capability attenuation |

### 17.9 Checkpoint, replay, and telemetry

| File | Responsibility | Key dependency rule |
|---|---|---|
| `checkpoint/manifest.h/.cc` | Checkpoint identity/version/compatibility | No implicit engine-version match |
| `checkpoint/writer.h/.cc` | Canonical chunk writer | Overflow/length checks |
| `checkpoint/reader.h/.cc` | Bounded validated chunk reader | Reject malformed input before mutation |
| `checkpoint/checkpoint.h/.cc` | Actor checkpoint orchestration | Requires safe point and host epoch |
| `checkpoint/restore.h/.cc` | Transactional actor restoration | Failure leaves original state intact |
| `checkpoint/replay.h/.cc` | Recorded-result/reconciliation policy | No silent duplicate effects |
| `telemetry/event.h` | Structured event schemas | Fixed schema versions |
| `telemetry/ring.h/.cc` | SPSC bounded event ring | Gap/drop reporting |
| `telemetry/trace.h/.cc` | Causality chain and turn attribution | No human-string primary interface |
| `telemetry/heap_stats.h/.cc` | DoshHeapStats-like structured snapshots | Values sourced from real ledgers |
| `telemetry/stack_record.h/.cc` | Structured source/function/PC frames | String rendering derived |

### 17.10 Tests and tools

| File/directory | Purpose |
|---|---|
| `tests/bytecode_verifier_test.cc` | Malformed opcode, jump, stack, constant, overflow rejection |
| `tests/interpreter_test.cc` | Hand-assembled bytecode semantics and frame resumes |
| `tests/fuel_test.cc` | Exact N-unit/N+1 trap boundaries in every backend |
| `tests/heap_budget_test.cc` | Recoverable exhaustion, external bytes, emergency reserve |
| `tests/gc_test.cc` | Root tracing, barriers, object movement, GC epochs |
| `tests/scheduler_test.cc` | Stable ready ordering, cancellation, late replies |
| `tests/microtask_test.cc` | ECMAScript queue ordering and checkpoint resume |
| `tests/capability_test.cc` | Denial, revocation, stale generations, reentry rejection |
| `tests/dom_binding_test.cc` | Epoch reads, transactions, stale wrappers, bounded buffers |
| `tests/checkpoint_test.cc` | Canonical snapshots, malformed restore, no native pointers |
| `tests/replay_test.cc` | Duplicate-effect prevention and reconciliation |
| `tests/abi_test.cc` | Fixed layout/version/size/buffer-lifetime contracts |
| `tests/determinism_test.cc` | Same seed + input + event log = same structured trace |
| `tests/fuzz/bytecode_fuzzer.cc` | Bytecode verifier/interpreter fuzzing |
| `tests/fuzz/parser_fuzzer.cc` | Parser bounds and early-error robustness |
| `tests/fuzz/checkpoint_fuzzer.cc` | Snapshot parser validation |
| `tests/test262/` | ECMAScript conformance harness and expected-failure ledger |
| `tools/doshvm.cc` | Minimal host-independent deterministic VM runner |
| `tools/disassemble.cc` | Structured bytecode inspection |
| `tools/replay.cc` | Checkpoint/event-log replay and trace diff |
| `tools/trace_diff.cc` | Machine-readable determinism comparison |

---

## 18. Existing-file migration map

| Current file/subsystem | Transitional use | Final destination |
|---|---|---|
| `src/execution/isolate.h/.cc` | Experiments with clock, PRNG, fuel, capabilities | Split among `core/engine`, `session/session`, `actor/actor`, `session/policy` |
| `src/execution/microtask-queue.*` | Microtask checkpoint behavior | `vm/microtask_queue.*` plus scheduler outcome |
| `src/heap/heap.h/.cc` | Heap/GC statistics and lifecycle reference | `heap/heap`, `heap/gc`, `telemetry/heap_stats` |
| `src/heap/heap-allocator-inl.h` | Existing allocation checkpoint experiment | `heap/allocator.*` with typed recoverable failure |
| `src/heap/local-heap.*` | Thread/local heap and safepoint study | `core/actor_lease`, `heap/*`, `vm/safepoint` |
| `src/interpreter/*` | Ignition bytecode/interrupt reference | `bytecode/*`, `vm/interpreter`, `vm/fuel` |
| `src/parsing/*` | JavaScript parser/early-error reference | `js/lexer`, `js/parser`, `js/scope` |
| `src/objects/js-temporal-objects.cc` | Temporal wall-clock leak evidence | `js/builtins/temporal.cc` through `session/clock` |
| `src/numbers/math-random.cc` | Seeded PRNG experiment | `session/random` and `js/builtins/math` |
| `src/wasm/baseline/liftoff-compiler.cc` | Fuel-at-tier-check experiment | Unified `vm/fuel` and later `vm/wasm_bridge` |
| `src/builtins/*` | Builtin behavior reference | Per-area `js/builtins/*` with fuel/capability classes |
| `src/objects/call-site-info.*` | Structured stack reference | `js/structured_stack`, `telemetry/stack_record` |
| `src/flags/flag-definitions.h` | Development overrides only | Validated `session/policy_schema`; no production global policy |
| `include/dosh_protocol.h` | Transitional DCP sketch | Versioned fixed-width `include/dosh/abi.h` and `abi/*` |

No wholesale rename should be performed. New engine files are implemented against new contracts, with conformance tests proving behavior independently.

---

## 19. Build and dependency integration

No build-system change is authorized by this document.

When implementation begins:

1. Keep `dosh/` independently compilable as a small static library with no browser dependency.
2. The VM core must not include Rust, DOM, network, UI, or platform browser headers.
3. Only `abi/` and `bindings/` may depend on host declarations.
4. C++ unit tests link a mock host, not dosh-browser.
5. Rust integration uses one safe wrapper crate/module around `include/dosh/abi.h`.
6. The browser host supplies DOM/network/storage implementations through host function tables.
7. JIT, WASM, ICU/alternate internationalization, and platform optimizations remain separately selectable capabilities.
8. Do not introduce a dependency merely for convenience; canonical encoding, PRNG, hashing, and concurrency choices are recorded before implementation.

Suggested initial compile feature flags (future, not current V8 flags):

- `DOSH_WITH_JIT=0`;
- `DOSH_WITH_WASM=0` until the core is stable;
- `DOSH_WITH_BROWSER_HOST=0` for hermetic tests;
- sanitizers and deterministic test scheduler enabled in test builds.

---

## 20. Implementation sequence and acceptance gates

### Phase 0: Contract freeze

Deliver:

- final ID and fault schemas;
- actor ownership and same-origin rules;
- turn outcome state machine;
- bytecode version 0 specification;
- host ABI threading/buffer rules;
- checkpoint effect-replay taxonomy.

Gate: no unresolved ambiguity about ownership, reentry, or who may mutate state.

### Phase 1: Minimal deterministic kernel

Implement only:

- IDs, actor state, mailbox, scheduler;
- hand-assembled bytecode with constants, arithmetic, jumps, calls, return, host call, trap;
- minimal tagged heap with checked allocation;
- mock synchronous/asynchronous host;
- fuel and structured traces.

Gate:

- identical bytecode and host results produce identical traces across worker counts;
- N fuel permits exactly N defined units and N+1 traps;
- no parser/JIT/browser dependency exists.

### Phase 2: Actor heap, GC, and recoverable resource faults

Implement:

- precise roots, young/old regions, actor-local GC;
- external resource ledger;
- byte/allocation budgets;
- emergency fault storage;
- structured memory fault return.

Gate:

- fault injection at every allocation helper leaves valid actor state;
- GC at every legal safepoint preserves objects;
- unrelated actors continue when one actor collects or faults.

### Phase 3: Checkpoint and replay before broad language coverage

Implement:

- canonical heap/frame/mailbox serialization;
- virtual clock/PRNG/scheduler state;
- host-result replay classifications;
- transactional restore.

Gate:

- checkpoint after every legal turn in a model program;
- restore produces identical subsequent trace;
- malformed snapshots fail without partial mutation;
- external effects are not duplicated.

### Phase 4: JavaScript frontend vertical slice

Implement source/lexer/parser/AST/scopes/bytecode generation for a deliberately small ES subset, then promises/microtasks and structured exceptions.

Gate:

- Test262 subset and targeted semantic tests;
- direct eval, indirect eval, and Function constructor share one policy gate;
- clock and PRNG tests prove no platform bypass.

### Phase 5: DOM capability integration

Implement node handles, synchronous bounded reads, mutation transactions, layout snapshot views, and agent query/action evidence.

Gate:

- no raw DOM pointer enters JS/heap/checkpoint;
- stale epoch/handle behavior is explicit;
- real captured pages exercise controls and report unavailable behavior honestly.

### Phase 6: Broad ECMAScript and multi-session operation

Expand builtins/modules/workers and session lifecycle.

Gate:

- long-lived independent sessions remain isolated;
- quotas include external resources and queues;
- cancellation, teardown, and late replies cannot resurrect actors.

### Phase 7: Optional optimized backend and WASM

Only after interpreter evidence is strong:

- actor-owned inline caches;
- baseline native code with identical fuel/safepoint/write barriers;
- interpreter-resumable OSR/checkpoint rules;
- WASM memory/fuel/capability integration.

Gate: differential tests require identical traces/faults across interpreter and optimized tiers.

---

## 21. Verification strategy

### Static invariants

- include/dependency layering checks;
- no platform clock or RNG calls outside their services;
- no unchecked host callback invocation;
- no raw resource pointers in serializable types;
- fixed-width ABI layouts and version checks;
- every allocation helper has a failure result;
- every native builtin has fuel/capability classification.

### Dynamic correctness

- unit tests for every opcode and frame transition;
- GC stress at every legal safepoint;
- allocation/fuel fault injection;
- scheduler model checking;
- microtask ordering suites;
- Test262 with an explicit expected-failure ledger;
- captured web-page integration tests;
- ASan/UBSan/TSan where applicable;
- parser/bytecode/checkpoint/ABI fuzzing.

### Determinism

Run the same program with:

- different host worker counts;
- different OS thread timing;
- repeated checkpoint/restore at every legal boundary;
- different code cache warmth;
- interpreter versus future optimized tiers.

Compare structured traces, heap outcomes, DOM epochs, faults, and external operations. Wall timestamps and pointer values are excluded unless virtualized.

### Security and containment

- malformed bytecode cannot escape verifier checks;
- stale/forged resource IDs fail closed;
- control/page capability separation is tested directly;
- actor memory exhaustion cannot corrupt another actor;
- host reentry attempts are rejected;
- untrusted checkpoints cannot allocate before length/ref validation.

---

## 22. Principal design risks

| Risk | Required response |
|---|---|
| Full ECMAScript correctness is much larger than a toy interpreter | Test262 from the first parser slice; publish exact coverage |
| Same-origin synchronous access conflicts with fine-grained actor isolation | Actor per related synchronous realm group, not blindly per frame |
| DOM through C ABI becomes a performance bottleneck | Batch/zero-copy immutable views after correctness; no second mutable DOM |
| Checkpointing arbitrary JS/native state proves impossible in a backend | Return to interpreter-resumable safepoint or report unsupported |
| External memory bypasses heap quotas | Resource ledger and session aggregate budgets are mandatory |
| Replay repeats real side effects | Explicit per-effect replay policy and idempotency/reconciliation |
| Cross-language ABI becomes unsafe | Fixed-width versioned ABI, safe owner wrappers, callback/thread contracts |
| JIT reintroduces unmetered execution | Interpreter-first; tier parity tests and mandatory safepoints |
| Actor scheduling becomes unfair | Scheduling classes, aging policy, and queue budgets specified/tested |
| Shared code introduces cross-session mutation | Code registry objects immutable after verification |
| Reference-engine copying imports human-oriented assumptions | Borrow techniques selectively; contracts and invariants take precedence |

---

## 23. Decisions still requiring explicit approval

1. Whether the permanent public engine API is C++ owner types plus C ABI, with no promise of V8 API compatibility.
2. Whether the Rust host remains the sole authoritative owner of mutable DOM state.
3. Whether actor-local compressed heap references use a bounded cage or segmented object table; prototype both only after measuring checkpoint and property-access costs.
4. Whether checkpoint format uses a custom canonical binary encoding or an existing dependency after dependency review.
5. Which ECMAScript subset defines the first public conformance milestone.
6. Whether same-origin related browsing contexts always share an actor or a more complex synchronous shard is justified.
7. Which host effects are idempotent enough for re-execution during replay.
8. Whether WASM is part of the first browser milestone or deferred until after the JS/checkpoint kernel.
9. Which JSC/V8 source files, if any, justify direct reuse after provenance and license review.

These decisions should be resolved by short ADRs with experiments where necessary, not by accidental implementation choices.

---

## 24. Immediate next artifact

The next implementation design should be **ADR-0009: Minimal Turn, Bytecode, Actor Heap, and Host ABI Vertical Slice**, covering only Phases 0–2.

It should define the exact C++ structs and function signatures for:

- `DoshEngine::step`;
- `DoshActor::run_turn`;
- `DoshHeap::try_allocate`;
- `DoshInterpreter::dispatch`;
- `DoshHostBinding::call`;
- `DoshCheckpointWriter::write_actor`;
- structured `DoshTurnOutcome` and `DoshFault`.

The first proof is deliberately small: execute hand-written bytecode, charge fuel, allocate actor objects, perform actor-local GC, suspend on a host call, resume with a recorded result, exhaust memory without process failure, and checkpoint/restore to an identical structured trace.

Only after that proof should broad JavaScript parsing, DOM integration, optimizing compilation, or large reference-engine reuse become the main work.

---

## Change log

- Initial architecture record: established the actor-native product thesis, V8/JSC reference lanes, C++20/Rust boundary, deterministic scheduler, actor-owned heaps, capability model, DOM epochs, checkpoint/replay requirements, file-by-file target tree, and phased acceptance gates.
