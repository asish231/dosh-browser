# WAASM — Web Agentic WASM

**A spec/plan for an agent-native execution substrate layered on standard WebAssembly.**

Status: PROPOSAL / PLAN (not implemented)
Owner: asish231
Context: companion project to `dosh-browser` (an LLM-native browser, built from scratch)

---

## 1. Problem statement

The web was built for humans. Every layer reflects that:

- **Interfaces are pixel-based** — layout, visual hierarchy, screenshots, 3D viewports.
- **Automation is coordinate/selector-based** — CDP, Playwright, Puppeteer speak in pixels and CSS selectors.
- **Security primitives target human behavior** — CAPTCHAs, bot detection, fingerprinting assume a human is driving.
- **Workflows are linear and single-user** — one person, one tab, one deadline.

LLMs cannot use any of that natively. An LLM sees tokens, not pixels. To act on the web it needs:

- **Structured document projections** (blocks with stable IDs, action inventories with stable refs).
- **Honest coverage** ("244 of 245 controls; 1 occluded; here's why it's unavailable").
- **Causal telemetry** (action → DOM delta → network → console) for audit.
- **Deterministic replay** (re-run an agent's session from a ledger).
- **Capability manifests** (what's available on this page, formally declared).

Current WASM does not provide any of this. WASM is a **compute primitive** — a sandboxed stack machine with linear memory. It has no notion of a document, no agent primitives, no structured I/O, no identity, no mutation history.

**The gap:** We need an execution substrate where the *host API* is agent-shaped, not POSIX-shaped.

---

## 2. Core thesis

> Do not invent a new binary format. Keep standard `.wasm` as the execution layer.
> Build the agent-native layer **above** it as a convention: custom host imports,
> custom sections for capability/metadata, and a document projection as the
> agent's actual interface.

This mirrors how WASI works:

- **WASI** = WebAssembly System Interface = a standard set of host imports (files, clocks, networking) that gives WASM programs portable OS-like access.
- **WAASM** = Web Agentic WASM = a standard set of host imports (`agent.read_dom`, `agent.act`, `agent.net_query`, `agent.emit`) that gives WASM programs portable **agent-like** access to a browser document.

WAASM is "WASI for agents."

### Why not a fork of the binary format

1. **Portability** — every existing WAS binary binary runtime (V8, Wasmtime, Wasmer, WAMR) can execute WAASM modules. Zero engine changes.
2. **Ecosystem reuse** — Rust/C/Go toolchains, `wasm-bindgen`, debuggers, profilers all keep working.
3. **Validation safety** — the existing WASM validator enforces memory safety and control-flow integrity for free.
4. **Incremental adoption** — standard WASM modules still run; WAASM modules are a strict superset (standard WASM + extra host imports + custom sections).

---

## 3. Architecture

```
+----------------------------------------------------------+
|  Agent (LLM)                                             |
|  reads ACD / action surface; emits intent               |
+----------------------------------------------------------+
        | calls tools
        v
+----------------------------------------------------------+
|  WAASM Host (the browser runtime — dosh-browser)         |
|  - provides "waasm" namespace host imports               |
|  - enforces capability permissions per module            |
|  - records host calls for deterministic replay           |
|  - feeds document projection (ACD) into module memory    |
+----------------------------------------------------------+
        | host imports / exports
        v
+----------------------------------------------------------+
|  WAASM module (.wasm, standard binary format)            |
|  - compiled from Rust/C/Go/AssemblyScript                |
|  - imports: waasm.read_dom, waasm.act, waasm.emit, ...   |
|  - exports: run(intent), on_delta(deltas)                |
|  - custom sections: capability manifest, intent schema   |
+----------------------------------------------------------+
        | uses
        v
+----------------------------------------------------------+
|  Standard WASM runtime (V8 / QuickJS-wasm / Wasmtime)    |
+----------------------------------------------------------+
```

Key design choice: **the document is fed in, not scraped by the module.** The host owns the DOM (in dosh: QuickJS + mini-DOM). The WAASM module receives a serialized document projection (ACD) and returns structured actions. The module never touches raw DOM APIs — it operates on the projection, then asks the host to execute actions against the real document.

---

## 4. Binary format

WAASM modules are **standard `.wasm` files**. No new magic number. No new sections required. Conventions live in:

### 4.1 Host import namespace: `"waasm"`

```wat
(module
  ;; Host imports (provided by the WAASM runtime)
  (import "waasm" "read_dom"  (func $read_dom  (param i32 i32) (result i32)))
  (import "waasm" "act"       (func $act       (param i32 i32) (result i32)))
  (import "waasm" "emit"      (func $emit      (param i32 i32 i32 i32) (result i32)))
  (import "waasm" "net_query" (func $net_query (param i32 i32) (result i32)))
  (import "waasm" "log"       (func $log       (param i32 i32 i32) (result i32)))
  ;; Module exports
  (export "run" (func $run))
)
```

All imports take `(offset, len)` pointers into linear memory and return an `(offset, len)` pointer to a result buffer (JSON or a compact binary), following the same convention as WASI's `fd_read`-style APIs.

### 4.2 Custom sections (standard WASM section ID 0)

Custom sections are ignored by standard runtimes — perfect for metadata:

| Custom section name | Purpose |
|---|---|
| `waasm.capabilities` | Declared capabilities the module needs (read_dom, act, net_query, ...) — the host validates this against what it's willing to grant. |
| `waasm.intent` | JSON schema describing the intent format the module accepts (input contract) and emits (output contract). |
| `waasm.version` | WAASM ABI version (host API version the module targets). |
| `waasm.name` | Human-readable module name (also standard `name` section is fine). |
| `waasm.source` | Build provenance: source commit, toolchain, reproducibility hash. |

Example: a module declares `(import "waasm" "act" ...)` but its `waasm.capabilities` section says `["read_dom","act"]`. If the host is configured to deny `act`, it refuses to instantiate the module — **capabilities are declared before execution, not discovered at runtime.**

---

## 5. Host API (the agent syscall surface)

All functions: pointers `(i32 offset, i32 len)` → `(i32 result_offset, i32 result_len)`.
Buffers are allocated inside the module's linear memory; results are JSON (v1) or a compact TLV encoding (v2, TBD).

### 5.1 `waasm.read_dom(spec_offset, spec_len) -> result`

Input JSON:
```json
{
  "projection": "acd" | "actions" | "reading" | "net" | "console" | "layers" | "frames",
  "query": "optional filter term",
  "slice": {"start": "block-id", "end": "block-id"},
  "limit": 500
}
```

Returns the requested projection (ACD blocks, action inventory, network ledger slice, etc.).

### 5.2 `waasm.act(actions_offset, actions_len) -> result`

Input JSON — a list of actions against **stable, content-addressed refs** (not selectors, not coordinates):
```json
{
  "actions": [
    {"ref": "a3f9c2...", "kind": "activate", "options": {"text": "hello"}},
    {"ref": "b71e04...", "kind": "drag", "options": {"dx": 120, "dy": 40}}
  ]
}
```

Returns per-action outcome: `{"ref":"...","dispatched":true}` or `{"ref":"...","dispatched":false,"reason":"stale ref"}`.
**Dispatched ≠ website success** — the host reports honest dispatch status; verification is a separate `read_dom`.

### 5.3 `waasm.emit(key_offset, key_len, val_offset, val_len) -> result`

Structured result emission (like dosh-shell's `dosh.emit`). Bounded (e.g. 100 emits, 1MB total). This is how a WAASM module returns its answer to the agent.

### 5.4 `waasm.net_query(query_offset, query_len) -> result`

Query the causal network ledger: `{"after": 0, "limit": 100, "pattern": "..."}`.

### 5.5 `waasm.log(level, msg_offset, msg_len) -> result`

Structured logging into the host's per-realm console capture.

### 5.6 `waasm.snapshot(kind) -> result`

Request a control-evidence snapshot (what dosh-shell's `snapshot` does) — returns a snapshot id.

### 5.7 Module exports (what the host calls)

```wat
(export "run" (func $run))          ;; (intent_ptr, intent_len) -> (result_ptr, result_len)
(export "on_delta" (func $on_delta)) ;; (delta_ptr, delta_len) -> ()  (optional mutation-stream subscriber)
(export "manifest" (func $manifest)) ;; () -> (manifest_ptr, manifest_len)  (optional: runtime manifest, complement to custom section)
```

---

## 6. What makes it efficient for agents (vs standard WASM + JS glue)

1. **No JS interop layer** — agent calls go host↔module directly. In dosh, this means the WAASM host is written against our QuickJS runtime, but the module never touches JS objects — only linear memory buffers.
2. **Structured host calls** — JSON-in/JSON-out with a fixed schema, not ad-hoc string passing.
3. **Capability declarations up front** — the host knows what a module can do before instantiation (custom section), enabling least-privilege.
4. **Deterministic replay** — every host import call + return is recorded in a ledger with (module, seq, args_hash, result_hash). Replaying the ledger re-runs the session without re-executing non-deterministic host effects.
5. **Memory snapshots** — the host can serialize the module's linear memory + globals for checkpoint/restore (agent "save state here").
6. **Mutation streams** — `on_delta` lets a module subscribe to DOM deltas instead of re-reading the whole document (efficient long-horizon agents).
7. **Stable identity** — actions use dosh's content-addressed refs, so a module doesn't re-resolve selectors after re-render.

---

## 7. Security model

Same three properties WASM already gives, plus host-side enforcement:

1. **Sandbox** — the module cannot touch the OS, filesystem, or raw network. Only `waasm.*` host imports exist.
2. **Memory safety** — standard WASM validator enforces bounds; no buffer overflows.
3. **Least privilege via capabilities** — `waasm.capabilities` custom section declares needs; the host grants a subset. A module with `["read_dom"]` cannot call `act` (host import denied at instantiation).
4. **Audit** — every host call is logged (causal telemetry). An agent session is fully auditable: what was read, what was acted on, what happened on the network.
5. **Code protection** — compiled `.wasm` is harder to reverse than JS (relevant for shipping proprietary agent logic / fraud models).
6. **Honest failure** — if a capability is denied or a ref is stale, the host returns an explicit error, never a silent fake success (matches dosh's "report coverage honestly" rule).

Threat notes (honest limitations):
- Host must correctly enforce the import boundary; a dangerous host import defeats the sandbox.
- Timing attacks / resource exhaustion within the sandbox are possible; the host needs CPU/memory/time quotas.
- A malicious module can still lie in its `emit` output; the host cannot verify semantic truth, only provenance and structure.

---

## 8. How this integrates with dosh-browser

dosh-browser is the natural host for WAASM because it already has:

- **QuickJS + mini-DOM** — the document model and JS runtime.
- **ACD (`webresearch/acd.py`)** — the agent context document, which is exactly what `waasm.read_dom` should return.
- **Action surface (`webresearch/actions.py`)** — stable content-addressed refs, honest coverage counts. Exactly what `waasm.act` consumes.
- **Network ledger (`webresearch/telemetry.py`)** — causal telemetry for `waasm.net_query`.
- **Shell command surface (`webresearch/shell.py`)** — bounded control-realm scripting; WAASM would be a *parallel* execution substrate alongside the QuickJS control realm.
- **Capabilities that generic agents lack** — reCAPTCHA solver, login/OTP, fingerprint parity, drag negotiation. These stay host-side; a WAASM module can *invoke* them via host imports without re-implementing them.

Integration sketch:

```
dosh-browser
├── webresearch/                 (existing Python host)
│   ├── acd.py                   → waasm.read_dom source
│   ├── actions.py               → waasm.act target
│   ├── telemetry.py             → waasm.net_query + replay ledger
│   └── shell.py                 → WAASM command: "run module.wasm with intent {...}"
├── waasm/                       (new)
│   ├── host.zig or host.py      → host import implementations
│   ├── loader                   → .wasm decode, validate, custom-section parse
│   ├── capability               → grant/deny per module
│   ├── ledger                   → deterministic replay record
│   └── stdlib (Rust/C)          → client-side helper crate for module authors
└── examples/                    → sample WAASM modules (task runners)
```

---

## 9. Roadmap (mirrors dosh's INVENT discipline)

Per dosh AGENTS.md rules: implement natively, test hermetically from real captured artifacts, report gaps honestly.

| Phase | Item | Status |
|---|---|---|
| 0 | Spec: host API schema, capability model, custom-section layout | TODO |
| 1 | `.wasm` loader: decode + validate (reuse an existing validator or write one) | TODO |
| 2 | Host imports `read_dom`, `act`, `emit` wired to acd/actions | TODO |
| 3 | Capability enforcement (custom section → grant/deny) | TODO |
| 4 | `net_query` + causal ledger + deterministic replay | TODO |
| 5 | `on_delta` mutation-stream subscription | TODO |
| 6 | Memory snapshot / checkpoint-restore | TODO |
| 7 | Rust/C client crate (`waasm-std`) + example modules | TODO |
| 8 | Hermetic tests from real captured modules (per AGENTS.md: test against real captured markup, not hand-written fixtures) | TODO |
| 9 | WASM interpreter scope decision (if QuickJS lacks WASM: implement scoped interpreter for opcodes real modules use — the roadmap's "biggest engine hole") | TODO |

**Phase 9 is the critical unknown.** QuickJS has no WASM VM. Options:
- (a) Embed a small WASM interpreter (wasm3-like) into the runtime — heavy but native.
- (b) Scope the interpreter to the opcodes real agent modules use (per the roadmap).
- (c) Use WASM only where a full runtime exists (server-side / edge), keep browser-side modules in QuickJS.

Per AGENTS.md: "No shortcuts... The hard path is the job: invent it, implement it natively, test it hermetically."

---

## 10. Honest limitations

1. **Not a new format** — WAASM is a convention (host API + custom sections) on standard WASM. If you need a genuinely new binary format, this isn't it — but a new format loses portability.
2. **QuickJS has no WASM** — until Phase 9 ships, WAASM modules can only run where a WASM runtime exists (Wasmtime/server) or after we build an interpreter. This is a real, disclosed gap.
3. **Host-bound** — efficiency gains come from the host (dosh) feeding projections and executing actions. A WAASM module on a generic runtime (Wasmtime without a document) only gets generic WASI-like behavior.
4. **JSON overhead** — v1 host API is JSON-in/JSON-out; a compact TLV encoding (v2) is needed for high-frequency calls.
5. **Cannot verify agent truthfulness** — the host audits structure and provenance, not semantics. An agent module can emit a confident wrong answer.
6. **Security is only as good as the host** — a bad host import (e.g., unfiltered fetch) defeats the sandbox.
7. **Scope creep risk** — WASI already solves files/network/clocks. WAASM should stay agent-document-focused and not re-implement OS syscalls.

---

## 11. What success looks like

A WAASM module (compiled from Rust) that, given an intent like:

```json
{"task": "download the latest statement", "criteria": {"vendor": "chase", "period": "last-month"}}
```

can:
1. `read_dom("actions")` → get the action surface with stable refs,
2. `act([{"ref":"...", "kind":"activate"}])` → navigate to the statements page,
3. `read_dom("net")` / `net_query` → confirm the download request fired,
4. `emit("result", {"file": "stmt_2026_10.pdf", "sha256": "..."})`,
5. all host calls recorded for replay, capability-checked, and honestly reported (including "1 control occluded, reason: ...").

That is an agent-native execution substrate: standard WASM bytes, agent-shaped host, auditable, portable.

---

## 12. Relationship to the "why not pixels" thesis

The web is human-oriented (pixels, layout, visual hierarchy, screenshots, 3D viewports). LLMs can't use pixels. Every pixel-based interface (Figma canvas, 3D rendering, games) is irrelevant to an agent as an *interface* — what agents need is the **document model** exposed as structured data.

WASM's role in this world is **compute**, not **interaction**:

- Useful: running ML models, crypto, heavy parsing, ported native libraries, sandboxed untrusted code, edge compute.
- Useless for agents: rendering pixels, driving canvas UIs, anything requiring visual understanding.

WAASM makes WASM useful at the **interaction layer** too — not by giving it pixels, but by giving it agent syscalls into the structured document. That's the bridge between "WASM = compute sandbox" and "agent = acts on structured web."

---

## 13. Handoff notes for the implementing agent

1. Read `dosh-browser/AGENTS.md` first — it governs this work (no external browsers, native implementation, honest reporting).
2. Study `webresearch/acd.py`, `webresearch/actions.py`, `webresearch/telemetry.py`, `webresearch/shell.py` — these are the host-side pieces WAASM wires into.
3. Study WASI (`wasi_snapshot_preview1`) as the API design precedent — pointer-in/pointer-out, JSON/TLV results, capability-based.
4. Do NOT fork the `.wasm` binary format. Custom sections + import namespace only.
5. Implement phases in order; test hermetically from real captured artifacts, not hand-written fixtures.
6. Disclose gaps honestly (QuickJS has no WASM VM until Phase 9; JSON overhead; host-bound efficiency).
7. Never fake success — if a capability is missing, report "unavailable and why."
