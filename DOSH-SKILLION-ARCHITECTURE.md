# Skillion: An Agentic Extension Protocol for Dosh

**Status:** design proposal, not yet implemented (grounded in a live engine audit — see §4).
**Working names:** *Skillion* (the extension unit) and *Dosh Kernel* / `dk`
(the runtime that hosts them). Placeholders, easy to change.

---

## 1. The problem

Chrome extensions are built for humans: a popup, a toolbar button, content
scripts, a permission prompt a person clicks. An agent needs none of that. It needs:

- a typed contract it can read and decide on (inputs, outputs, cost, side effects),
- instructions it can load into its own context (like Chrome DevTools AI's
  `learnSkills('storage')`),
- scoped, auditable access to browser internals (DOM, network, cookies, storage),
- the ability to chain units together,
- the same unit reachable from the shell, MCP, and the remote attach bridge,
- **code in more than one language**, including running a micro-service.

Dosh already has most of the parts: `skill.*` (stored JS, SHA-256 pinned, origin
scoped, warrant expiry), the isolated control realm, per-session capability
gates, **native WASM in doshgentv8 with fuel metering and a `waasm` host
namespace** (confirmed live — §4), `ShellStore` SQLite, MCP, and the `/v1/attach`
bridge. What's missing is a protocol tying them into one extension model.

**A Skillion is a skill, an MCP tool, a shell command, and an event hook at once,
defined by one manifest, run by one kernel, in the language of its choice.**

---

## 2. Comparison

| | Chrome extension | MCP server | Dosh `skill.*` today | **Skillion** |
|---|---|---|---|---|
| Audience | Human | Agent | Agent | Agent |
| Contract | UI + permissions | Tool schema | Source + origins | Manifest: schema, capabilities, cost, instructions |
| Runs | In browser | Separate process | Control realm (JS only) | Kernel-scheduled, **JS / WASM / subprocess** |
| Browser internals | Content scripts, `chrome.*` | None (external) | Read-only projections | Capability-brokered syscalls |
| Triggers | Page/browser events | Called only | Called only | Called, page/network/DOM hooks, pipes |
| Composition | None | None | None | Pipes between Skillions |
| Agent can learn it | No | Tool description | No | `instructions` block loaded into context |
| Trust | Store review | None | SHA-256 + warrant | SHA-256 + Ed25519 sig + warrant + audit ledger |

---

## 3. Architecture

```
          ┌────────── surfaces (auto-projected from each manifest) ───────────┐
          │  dosh shell       MCP tools/list     /v1/attach WS     event hooks │
          │  design.harvest   skx_design_harvest {command:...}     on:page.load│
          └──────────────────────────────┬─────────────────────────────────────┘
                                          │
┌──────────────────────────────────────── ▼ ───────────────────────────────────────────┐
│                               DOSH KERNEL (dk)                                        │
│                                                                                       │
│  Loader      verify sha256 + Ed25519 signature, parse manifest, register surfaces     │
│  Scheduler   fuel/time budget per call, cancellation, one in-flight call per session  │
│  Broker      every capability request checked against the grant set + session gates   │
│  Audit       append-only ledger row per call: who, caps used, bytes, fuel, verdict    │
│                                                                                       │
│   ┌───────────── RUNNERS (a manifest picks one `kind`) ──────────────────────────┐    │
│   │  js      → doshgentv8 isolated control realm (today's skill path)             │    │
│   │  wasm    → doshgentv8 native WebAssembly + `waasm.*` host imports (§4)         │    │
│   │  proc    → OS subprocess over a pipe (any language incl. Python/Node/bash)     │    │
│   └───────────────────────────────────────┬──────────────────────────────────────┘    │
│                                            │ capability syscalls only                   │
│                                            ▼                                            │
│   SYSCALL SURFACE (the only way a Skillion touches the world — brokered)               │
│   dom.read  act  net.query  net.fetch  storage.get/set  cookies.get  emit  log         │
│   snapshot  image.decode  kv.get/set (per-skillion private store)                      │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

The kernel is the "ultra-lightweight OS": the *only* thing a Skillion can do is
make syscalls, each one brokered against its granted capabilities and the
session gates. No ambient authority, in any language.

---

## 4. Engine reality check (live audit, this session)

Measured against the built binary, not docs:

| Capability | Reality | Consequence for Skillion |
|---|---|---|
| Native WebAssembly compile + run | ✅ **Works.** A real `ArrayBuffer` through `WebAssembly.Module`/`Instance` ran `add(2,3)=5`. (`AGENTS.md` saying it's disabled is stale.) | `wasm` runner is viable **today** |
| Fuel metering | ✅ `__dosh_step` / `__dosh_fuel_remaining` + `wasm fuel=` shell cmd | per-call WASM budget |
| Capability gate | ✅ `__setCapabilityGate('wasm'/'eval'/'waasm.<sub>')`, `gate` cmd | broker enforcement point |
| `waasm` host namespace | ✅ Built in `doshgentv8/core/src/lib.rs`: `read_dom, act, emit, net_query, log, snapshot`, each gated, capabilities declared in a `waasm.capabilities` custom section | **the WASM syscall ABI already exists** |
| Control-realm `Uint8Array` → WASM | ❌ It's a JS shim; V8 rejects it as "not a buffer source" | **only real blocker**: marshal bytes to a true `ArrayBuffer` first |

**Net:** the multi-language substrate you asked for is ~80% already in the
engine. Any language that compiles to WASM (Rust, C, Zig, TinyGo, AssemblyScript,
and CPython-on-WASM) runs sandboxed in-engine now, once the one buffer blocker is
fixed. Only languages that *cannot* compile to WASM (full native CPython, Node,
a bash micro-server) need the `proc` runner.

---

## 5. The manifest

```jsonc
{
  "skillion": "1",
  "name": "design.harvest",
  "version": "1.2.0",
  "description": "Collect design assets + palette + type scale from the current page.",
  "instructions": "Run on a shot/gallery page. Returns image refs, dominant colors, font stack. Use design.harvest then image.decode for pixels.",
  "runner": { "kind": "wasm", "entry": "harvest.wasm" },   // js | wasm | proc
  "inputs":  { "type": "object", "properties": { "max_assets": {"type":"integer","default":24} } },
  "outputs": { "type": "object", "properties": { "assets": {"type":"array"}, "palette": {"type":"array"} } },
  "capabilities": ["dom.read", "net.query", "image.decode", "emit"],  // nothing else is reachable
  "origins": ["https://dribbble.com", "https://www.behance.net"],      // where it may run
  "cost": { "fuel": 2000000, "wall_ms": 8000 },
  "triggers": ["call", "on:page.load"],                                 // optional event hooks
  "signature": { "alg": "ed25519", "pub": "…", "sig": "…" }             // optional, required for proc
}
```

The manifest is the whole contract. From it the kernel auto-generates the shell
command, the MCP tool schema, the bridge command, and any event-hook
registrations — one definition, four surfaces.

---

## 6. Runners

### 6.1 `js` — doshgentv8 control realm (exists)
Today's `skill.run` path. Fast, deepest DOM access, fuel-metered, gated. Default
for DOM/automation logic.

### 6.2 `wasm` — doshgentv8 native WebAssembly (new, mostly built)
- Kernel loads `entry.wasm`, marshals bytes into a real `ArrayBuffer`
  (**the §4 fix**), compiles with `WebAssembly.Module`.
- Instantiated with `imports.waasm = {read_dom, act, emit, net_query, log,
  snapshot}` — the existing host namespace — intersected with the manifest's
  `capabilities` and the session gates.
- Fuel budget from `cost.fuel`; wall budget from `cost.wall_ms`.
- Covers Rust, C/C++, Zig, TinyGo, AssemblyScript, and Python-via-WASM.
- Sandbox: linear memory only, no ambient I/O, every effect is a brokered syscall.

### 6.3 `proc` — OS subprocess (new, opt-in, weaker sandbox)
- For languages that can't target WASM, or a real micro-server (a Python/Node
  process listening on a local socket).
- Runs as a child process; the ONLY channel is a length-prefixed JSON pipe
  carrying the same syscall verbs. No inherited env, working dir jailed to the
  skillion's private dir.
- **Off by default.** Requires `proc_allowed` session gate + a valid Ed25519
  signature + explicit per-skillion operator grant. Reported honestly as the
  weakest isolation lane (consistent with the repo's "represent, don't pretend"
  rule).
- "Micro-server" mode: a long-lived `proc` skillion the kernel supervises
  (start/health/stop), reachable as a surface like any other.

---

## 7. Syscall surface (the kernel ABI)

Every runner, in every language, reaches the browser only through these. Each is
checked by the broker against the skillion's `capabilities` and the live session
gates before executing.

| Syscall | Capability | Backed by |
|---|---|---|
| `dom.read(spec)` | `dom.read` | actions/ACD projection |
| `act(actions)` | `act` | session click/type/etc. |
| `net.query(filter)` | `net.query` | NetworkLedger |
| `net.fetch(req)` | `net.fetch` | owned transport (gated by `net`) |
| `storage.get/set` | `storage` | session storage |
| `cookies.get` | `cookies` | live jar (values redacted unless authorized) |
| `image.decode(ref)` | `image.decode` | `image` crate (real pixels — for the Dribbble case) |
| `emit(k,v)` / `log(level,msg)` | `emit` / `log` | result + audit stream |
| `snapshot(kind)` | `snapshot` | state checkpoint |
| `kv.get/set(k,v)` | always | per-skillion private SQLite table |

Nothing outside this list is callable. Adding a capability later = add one
broker-checked verb, in one place.

---

## 8. Storage & surfaces

- New `ShellStore` tables: `skillions` (manifest + artifact + sha256 + sig +
  warrant), `skillion_kv` (private per-skillion key/value), reuse of the audit
  ledger.
- Auto-projected surfaces from each manifest:
  - **Shell:** `skillion.install`, `skillion.list`, `skillion.run <name>`,
    `skillion.remove`, plus the skillion's own name as a direct command.
  - **MCP:** one `tools/list` entry per installed skillion (schema from manifest).
  - **Bridge:** same name over `/v1/attach`, so a remote model gets it too.
  - **Hooks:** `triggers` like `on:page.load`, `on:net.response` fire the
    skillion automatically within budget.

---

## 9. Trust & safety

- **Default-deny capabilities.** A skillion gets exactly its manifest's grant set
  ∩ session gates; empty by default.
- **Pinning:** SHA-256 on artifact (as skills do today) + optional Ed25519
  signature. `proc` runners *require* a signature.
- **Warrant expiry** (reuse existing skill warrants) for time-boxed grants.
- **Fuel + wall budget** per call; the scheduler kills overruns.
- **Audit ledger:** one append-only row per call (caps used, bytes, fuel,
  verdict). Secret values stay redacted unless the audited `allow_secret_reveal`
  gate from the full-access plan is on.
- **Origin scope:** a skillion only runs on its declared `origins`.

---

## 10. The Dribbble example, end to end

1. Agent on `dribbble.com` calls `design.harvest` (a `wasm` skillion, Rust→WASM).
2. Kernel checks caps `[dom.read, net.query, image.decode, emit]` ⊆ grants ∩ gates.
3. WASM calls `waasm.read_dom` → image/card refs; `waasm.net_query` → asset URLs.
4. For each asset the agent calls `image.decode` → real pixels (via `image` crate)
   → dominant palette + type scale.
5. `emit` returns `{assets, palette, fonts}`; audit ledger records the call.
6. Same skillion is now callable by a remote model over `/v1/attach`, unchanged.

---

## 11. Build phases

1. **Manifest + loader + registry** (`skillion.*` shell cmds, SQLite tables, sha256).
   Wrap today's JS skills as `kind: js` skillions — zero behavior change.
2. **`wasm` runner** — fix the `ArrayBuffer` marshalling blocker (§4), instantiate
   with `waasm` imports ∩ caps, fuel/wall budget. First real multi-language win.
3. **Broker + audit** — route every `waasm.*`/syscall through the capability check
   and ledger. Add missing syscalls (`image.decode`, `kv.*`).
4. **Surfaces** — auto-generate MCP tool + bridge command + event hooks from manifest.
5. **`proc` runner** — subprocess pipe, signature-required, `proc_allowed` gate,
   micro-server supervision. Last, because weakest isolation.
6. **Example skillion** `design.harvest` + hermetic test from captured Dribbble markup.

Each phase compiles and tests green before the next; `AGENTS.md` capability
tables updated honestly per phase (including correcting the stale "native WASM
disabled" row).

---

## 12. Out of scope (unchanged)

No Chromium/CDP. No human UI. The `proc` runner is the only lane that leaves the
sandbox, and it stays opt-in, signed, and audited — never the default.
