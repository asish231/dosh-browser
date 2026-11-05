# DOSH Session Findings — Verbatim Research Record

This file preserves selected findings from this working session. Quoted passages are verbatim from the assigned task, the session's architecture conclusions, or the session's repository/capability audits. It supplements:

- `DOSH-AGENT-NATIVE-ARCHITECTURE.md`
- `DOSH-BROWSER-REPO-RESEARCH.md`
- `DOSH-CAPABILITY-REALITY-AUDIT.md`

---

## 1. Exact assigned task framing

> You are working inside the doshgentv8 fork of V8 (15.9.0), a vendored source tree
> at `doshgentv8/base/` in the dosh-browser repository. This fork converts an
> upstream browser engine into an AGENT-NATIVE engine: bounded, deterministic,
> machine-readable execution of untrusted/ semi-trusted page JavaScript for an AI
> web agent — not for human developers or Chrome-compat debugging.
>
> Your objective: upgrade V8 subsystems that were built for humans into subsystems
> built for agents. Invent new mechanisms, or translate existing human-oriented
> mechanisms into agent-native ones. Do not fake anything. If you cannot verify,
> say so.

The assigned subsystem was:

> YOUR ASSIGNED FOCUS: THE HEAP + GARBAGE COLLECTOR (read this first)

The later repository-audit request was:

> so now you list down things liek isseu and well stuffs like which is not complete or a slop like the shell is it reallt a shell and well does it hae reall capabilitie and stuffs like those think in that manner and well you will get manyhtings to work on

---

## 2. Exact initial verification constraint

> This tree has NO build system (no build/, buildtools/, no ICU, no gn/ninja/depot_tools on PATH). Unless the user authorizes fetching dependencies, you cannot compile this engine. Static verification must be explicit and disciplined: full call-site audits by grep, consistent flag definitions, diff review, and a written list of what still needs a real build to confirm.

---

## 3. Exact engine-architecture verdict

> Yes—this changes the architectural decision completely. **Do not treat V8’s internals as the product contract. Treat the ECMAScript specification as the contract, V8 as reference material, and build doshgentv8 as a new agent-native execution engine.** The allocation-fuel patch I made is useful transitional instrumentation, but it is not the right final architecture.

The central decision was:

> Build doshgentv8 around a **deterministic actor kernel**, not around a conventional browser JS engine with agent features attached.

The Go-inspired mapping was:

> - **Goroutine → resumable actor turn**
> - **Channel → typed capability port**
> - **Go scheduler → deterministic host worker scheduler**
> - **Panic → typed actor fault**
> - **Go GC → actor-local garbage collection**

The execution-boundary distinction was:

> - **JS task:** browser-atomic execution unit.
> - **VM slice:** invisible scheduling quantum inside a task.
> - **Host wait:** promise/task suspension or a specifically supported nested-event-loop operation.

---

## 4. Exact repository-audit scope admission

Before authorization to inspect the host repository, the session stated:

> No—I have **not** read the full `dosh-browser` repository. The original confinement restricted me to `doshgentv8/base/`; I audited selected V8 files there and wrote the architecture document. My knowledge of the packed binary, shell, MCP, and host runtime comes from your description and the provided project instructions, not a repo-wide code audit.

The user subsequently authorized reading the full repository. The resulting audit recorded:

> No builds, tests, servers, or network calls were run.

and:

> No files outside `doshgentv8/base/` were edited.

---

## 5. Exact highest-impact repository findings

1. **The shipped Rust engine is currently a prebuilt `v8` crate archive, not the patched V8 15.9 tree in `doshgentv8/base/`.**
2. **Public action refs do not reliably resolve to live DOM controls, and missed actions can appear successful.**
3. **DCP has raw-pointer lifetime defects and is not the documented C++↔Rust protocol.**
4. **REST and stdio MCP each own one global session; advertised `session_id` does not provide isolation, bridging, or leases.**
5. **The real watchdog is not armed by the primary browser path, so arbitrary JS can occupy an actor indefinitely.**
6. **Several shell values are hardcoded rather than measured from the live engine.**
7. **Checkpoints restore partial document data, not live VM/session state; fork is not an isolate fork.**
8. **Shell, OpenAI tools, MCP, REST, and dispatch do not share one semantic registry.**
9. **The Python companion has important production capabilities the Rust lane lacks; replacing it by filename parity would regress the product.**
10. **No durable multi-model DAG orchestrator, subagent runtime, TODO system, or RAG memory exists.** Python has an in-memory LLM tool loop; Rust `ask` is a local search/read formatter.

The exact engine-archive evidence was:

```toml
v8 = { version = "152.2.0", features = ["v8_enable_pointer_compression"] }
```

from `doshgentv8/core/Cargo.toml`.

The audit's engine-lane conclusion was:

> The sound near-term foundation is the **stock rusty_v8 + Rust actor/host-seam implementation**. The native-fork, DCP, automatic-fuel, zero-copy-layout, and VM-state checkpoint claims should not be treated as shipped capabilities until their missing integration and safety work is completed.

---

## 6. Exact DoshShell verdict

> **DoshShell is a real command dispatcher, but not yet a reliable agent shell.** Forty-five registered commands do not equal forty-five verified capabilities.

The more detailed conclusion was:

> Real command-dispatch code and several real capabilities, but not yet a trustworthy agent shell. Command count is not capability proof.

The session's capability summary stated:

> Some commands exercise real browser state; others query the wrong store, create a fresh runtime, return constants, or report success without proving an action occurred.

The architectural problem was summarized as:

> The largest architectural “slop” is not any single stub. It is multiple authorities for the same state.

Examples recorded in the audit included:

- DOM/page state split across `tab.html`, live mini-DOM, Parena, and history HTML;
- incompatible action and identity-tracker refs;
- session configuration versus actual actor state;
- separate session and NetworkEngine ledgers;
- separate MCP, OpenAI tools, dispatch, shell, and REST contracts;
- evidence snapshots, SQLite checkpoints, Parena, and tab history using “checkpoint” with different meanings.

---

## 7. Exact real-capability assessment

> DOSH already contains a substantial browser implementation: owned HTTP/network components, HTML parsing and ACD projections, a shared mini-DOM, a real resident V8 isolate-per-thread embedding, synchronous host calls, an actual watchdog, local neural inference, sessions, SQLite stores, MCP/REST/shell adapters, and a much richer Python companion.

The audit also found:

> The main problem is **not lack of subsystems**. It is that several public contracts claim behavior that the connected implementation does not provide.

The bottom-line assessment was:

> The project is not empty and it is not all slop. The low-level engine, parsing/projections, resident actor, watchdog, Python browser lanes, and storage primitives contain substantial real work.
>
> The immature layer is the **public agent contract**: the place where names such as `act`, `eval`, `trace`, `checkpoint`, `restore`, `heap`, `frames`, `login`, and `session_id` are expected to mean deterministic, safe operations.

---

## 8. Exact implementation strategy

> Build a canonical browser control/evidence plane and an external durable agent orchestrator now, using the working stock-V8 actor as the transitional engine. Develop the greenfield doshgentv8 kernel independently. Do not make the agent runtime wait for the new VM, and do not expose current DCP/Parena scaffolding as a stable external foundation.

The immediate priorities were:

> 1. Fix action-ref resolution and explicit action outcomes.
> 2. Arm execution deadlines for eval, script, timer, and microtask paths.
> 3. Replace DCP raw-pointer responses with owned values; remove unsafe external claims.
> 4. Replace hardcoded heap/WASM/engine responses with measured values/unavailable markers.
> 5. Make live actor configuration transactional and propagate errors.
> 6. Mark checkpoint types honestly and add live-runtime invalidation/rebuild where required.
> 7. Add a capability manifest that distinguishes shipping, partial, experimental, and proposed features.

The first vertical proof was specified as:

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

The capability audit concluded:

> This single slice will force the correct architecture for sessions, actors, identity, events, persistence, and adapter parity. It is more valuable than another twenty shell commands or another completion claim.

---

## 9. Exact provider and memory conclusions

Provider/model selection rules were:

> Provider/model selection is **pinned at subagent spawn**. “Current model” is resolved to a concrete descriptor at that point.
>
> No silent provider fallback. Switching models changes behavior and potentially data egress.

The memory layers were:

> | Layer | Contents | Authority |
> |---|---|---|
> | C0 prompt context | Objective, selected notes, tool outputs, cited excerpts | Rebuilt from context manifest |
> | C1 run state | DAG, attempts, notes/todos, approvals, budgets | External orchestrator durable DB |
> | C2 browser evidence | Events, DOM/network artifacts, checkpoints | Browser/evidence store |
> | C3 semantic memory | Embeddings, hybrid indexes, retrieval history | Rebuildable optional service |

The required boundary was:

> The browser must work with no LLM, orchestrator, or RAG service configured.

---

## 10. Exact verification statement

> No claim in this document should be read as “the tests pass,” “the engine builds,” or “the release contains the patched V8.” Those require separate execution. The findings here are static code/contract evidence and architectural recommendations. Runtime reproduction, memory-safety dynamic checks, and release-artifact inspection remain follow-up work.
