# Dosh State Weave — agentic-native engine architecture plan

> **Status:** design plan + verified substrate receipts. No code written yet for the Weave.
> Everything labeled "verified" below was checked against the actual sources in this repo
> (`base/`, `core/`, `py/`, `patches/`) and the `v8 152.2.0` crate on disk this session.

---

## 0. Honesty ledger — real invention, not renaming

An architecture you ship must be able to answer: *what did you invent, and what did you
reuse?* Here is the full accounting.

### 0.1 Components with prior art (and the prior art, named)

| Component | Prior art | What we do NOT claim |
|---|---|---|
| Bump/incremental allocation | Rust `typed-arena`, `bumpalo`; classic compiler arenas | Inventing bump allocation |
| Struct-of-Arrays data layout | Game-engine ECS, columnar databases, Servo's layout work | Inventing SoA |
| Copy-on-write memory | `fork()`, virtual memory, GC CoW heaps (e.g. mimalloc tricks) | Inventing CoW |
| External strings | `v8::String::ExternalOneByteStringResource` (V8's own API) | Inventing external strings |
| Actor model | Erlang/Akka/Rust actor crates; Chrome's per-renderer isolates | Inventing actors |
| Code fuel metering | Patch 0007 (already ours, in the patch series) | Inventing fuel traps |
| Snapshot boot | `v8::SnapshotCreator` (Node.js, Chrome embedders) | Inventing snapshots |
| Pointer compression + shared cage | Upstream V8 (`base/BUILD.gn:691`, `isolate-group.h`) | Inventing the shared cage |

### 0.2 What is genuinely ours to invent (the Web's agentic gap)

None of the single components above serve agentic browsing as a *system*. The agents'
needs — branch page state, compare outcomes, roll back, retain nothing, predictably, at
sub-millisecond cost — do not exist as engine primitives in V8, WebKit, or Blink. We
claim invention on these four **system contracts**:

1. **Ownership inversion as the contract.** Page state lives in host-owned arenas; the
   JS engine holds *borrowed views* only (index handles + external string slices). No
   browser engine (nor any known headless runtime, incl. Playwright/CDP-based stacks)
   treats the engine as a reader over host-owned page memory. This is the defining
   architectural decision and everything below depends on it.
2. **Fork/branch-verdict as an engine verb for browsing.** `dosh.fork()` produces
   independent, cheap-to-create page states obtainable in parallel across resident
   actors — closest prior art (`fork()` / snapshot VMs / CoW heaps) is general-purpose,
   browsing-agnostic, and either kernel-level (whole process) or whole-heap (V8 has no
   such primitive — verified). Ours forks *page state only* (the arena), keeps the
   isolate warm, and is measured in microseconds-to-milliseconds.
3. **Dirty-diff checkpoints as a state format.** Because the fused SoA frames are
   contiguous, a checkpoint is a byte-range diff log over the frames — not
   serialization of an object graph (the only formats that exist in browsers today:
   pickle/mhtml, CDP snapshot trees, protobuf DOM dumps). Restore = truncate ranges
   backwards. Cost scales with *mutation bytes*, not page size.
4. **The verb surface.** `dosh.fork / checkpoint / join / rewind / emit / act / text`
   as first-class, fuel-metered realm capabilities (`patches/0014` control realm) — a
   programming surface for agents that no runtime currently exports.

### 0.3 The one-line test for "real arch"

> Any piece of the plan that only exists to mirror a human-browser feature was either
> cut (see §7 non-goals) or upgraded (DOM storage → fused arena; boot → snapshot; GC →
> epoch-free; teardown → resident).

---

## 1. Verified substrate (receipts from this session)

| Substrate | Evidence | Use |
|---|---|---|
| Shared pointer cage + RO-heap sharing | `base/BUILD.gn:691` (`v8_enable_pointer_compression_shared_cage` defaults on), `base/src/init/isolate-group.h:55` | All isolates share one cage in-process |
| Pointer compression currently OFF in our crate build | `py/Cargo.toml` (no features) + `v8` crate `build.rs:416-432` (feature-gated) | **Step 0: flip feature, ~40% smaller objects, enables the above** |
| Full SnapshotCreator API | `v8` crate `isolate.rs:1028,1036`, `snapshot.rs:20-40` (SetDefaultContext/AddContext/AddData/CreateBlob/can_be_rehashed) | Warm boot of shims (Phase C) |
| Snapshot at isolate creation | `CreateParams::snapshot_blob(StartupData)`, `isolate_create_params.rs:45` | Restore baked blob per session |
| External one-byte strings | `v8` crate `string.rs:235` (`ExternalOneByteStringResource`), `string.rs:827` (`new_external_onebyte_static`) | Arena-backed JS text — the borrowed-view enabler (Phase D) |
| Idle memory pressure APIs | `v8` crate `isolate.rs:1433` (`low_memory_notification`), `1408` (`memory_pressure_notification`) | Pool eviction sweep |
| `IsolateGroup::Create()` public C++ API | `base/include/v8-isolate.h:232` | Long-horizon multi-cage; NOT yet exposed by rusty_v8 |
| Fuel metering (Liftoff/checkpoint) | `patches/0007`, `patches/0013` (checkpoint callback in `isolate.h`) | Cooperative bounds |
| Current bridge defects | `core/src/lib.rs` — per-eval watchdog `thread::spawn` in `eval_raw`; no re-entrancy guard (JS→host→Python→eval re-enters an entered isolate); `EXTENSIONS_SCRIPT` re-compiled per context | Fixed in Phase A |

---

## 2. Architecture — Dosh State Weave

### 2.1 Layer 1 — Parena (page arena)

One linear arena per navigation epoch, host-owned, never inside the V8 cage:

```
Parena (per page load)
├── tags:        Vec<u16>          // SoA: tag id per node
├── parents:     Vec<u32>          // SoA: parent index
├── attr_data:   Vec<u8>           // packed attrs, attr_offsets indexes in
├── attr_offsets: Vec<u32>
├── text_arena:  Vec<u8>           // ONE contiguous UTF-8 blob
├── text_offsets: Vec<u32>         // node -> span in text_arena
└── actions:     Vec<PackedAction> // clickable/submit descriptors
```

- Allocation = bump pointer; **free = drop (O(1))** at navigation end. GC never sees a page.
- Epoch = navigation; a session holds 1 live arena + optional retained snapshots.

### 2.2 Layer 2 — Borrowed views (JS side)

- **Text:** `ExternalOneByteStringResource` slices directly into `text_arena`. DOM text
  costs zero JS-heap bytes. (Verified API, §1.)
- **Structure:** index-handle objects — native getters resolve `node ref → Parena
  frames`. Reading `node.tag`, `node.attr(k)`, `node.text` walks no JS object graph.
- **Reads** flow over the zero-copy binary lane (Phase B opcodes + `DoshTelemetryRing`),
  not per-node JSON strings.

### 2.3 Layer 3 — Weave forking (branch-verb)

- **v1 (forges):** fork = `memcpy` of arena frames. 5,000-node page ≈ 0.5–2MB SoA →
  fork in µs–ms. Branch A/B/C run on three resident actors (Phase A), compare extracted
  verdicts, keep one, drop two. Zero network, no reload.
- **v2 (true COW):** arenas `mmap`-backed (`memmap2`); forks share the file; writes
  copied on demand via mprotect+handler. macOS has no `userfaultfd`, so v2 uses the
  portable mprotect/SIGSEGV technique (as mimalloc/CoW heaps do). Fork cost → O(1)
  regardless of page size.
- **Engine-side cost of a fork:** re-bind handles to the new arena. Never clone an isolate.

### 2.4 Layer 4 — Checkpoints (dirty-diff)

- Checkpoint = ordered log of `(frame_id, offset_range, new_bytes)` captured as JS
  fires events through the ring (patch 0013's microtask hook provides the capture point).
- Restore = walk the log backwards and truncate/restore ranges. Rewinding a CAPTCHA
  dead-end costs mutation-bytes, not page-bytes.

### 2.5 Layer 5 — Runtime (resident + metered)

- `DoshActor` (`core/src/actor.rs`): one pinned thread owning `(isolate, session, live
  arena)`; command channel in, reply out. Session state = virtual clock + RNG seed +
  fuel budget + cookie namespace (in `HostSeam`).
- `SessionPool` (`core/src/session.rs`): `session_id → actor`, LRU cap (default 8),
  `low_memory_notification()` sweep before eviction.
- Shared watchdog: one deadline thread process-wide; replaces per-eval `thread::spawn`.
- Re-entrancy guard: refuses nested `eval` while the isolate is entered (fixes the
  real CHECK-fail hazard).
- Verbs (`patches/0014` + `EXTENSIONS_SCRIPT`): `dosh.fork/checkpoint/join/rewind/act/
  emit/text/call/args`, gated by native `dosh_control_realm` / `dosh_allow_actions`
  flags in `isolate.h` + `builtins.cc` — no JS-side monkey-patch gating.

---

## 3. Files

### New (Rust, `core/`)
| File | Purpose |
|---|---|
| `core/src/parena.rs` | Arena, SoA frames, epochs, diffs, forking |
| `core/src/actor.rs` | Resident actors, command/reply channels |
| `core/src/session.rs` | Session pool, LRU, carried state |
| `core/src/watchdog.rs` | Shared deadline supervisor |
| `core/tools/mksnapshot.rs` | Bakes shims into `dosh_snapshot.bin` |

### Edited (Rust)
| File | Change |
|---|---|
| `core/src/lib.rs` | Re-entrancy guard, watchdog wiring, arena/handle glue, new `dosh.*` plumbing |
| `core/src/protocol.rs` | Opcodes: `SET_ALLOW_ACTIONS 0x0A`, `SET_CONTROL_REALM 0x0B`, `ARENA_FORK/CHECKPOINT/BATCH_READ…`; expose telemetry ring |
| `py/Cargo.toml` | `v8` feature flip: `v8_enable_pointer_compression` |
| `py/src/lib.rs` | Session/fork/rewind surface for Python seam |

### New (C++, `base/`, patch 0014)
`src/parsing/parser-base.h` (top-level return), `src/flags/flag-definitions.h`,
`src/execution/isolate.h/.cc` (action_allowed_, control_realm_),
`src/builtins/builtins.cc` (native gate), `include/dosh_protocol.h` (new opcodes).
Recorded as `patches/0014-dosh-control-realm-top-level-return-and-action-gate.patch`.

### Deprecated
- `patches/0004` (butterfly) — dead code in V8; marked deprecated in the series header.
- Per-node JSON DOM reads through `HostSeam` — superseded by the binary lane.

---

## 4. Phases

| Phase | Deliverable | Pre-req verified? |
|---|---|---|
| 0 | Pointer-compression feature flip; rebuild; before/after heap benchmark | yes |
| A | Actors, session pool, watchdog, re-entrancy guard + tests | yes |
| B | Binary lane: ring events + batch opcodes end-to-end from Python | yes |
| C | `mksnapshot` tool + snapshot-blob boot; boot-time KPI | yes |
| D | **Parena + borrowed views** (first Weave slice in JS: text handles + tag/attr reads via binary lane) | yes |
| E | Fork/checkpoint/join/rewind verbs + C++ patch 0014 control realm | D |
| F (long horizon) | v2 true-COW mmap forks; multi-cage `IsolateGroup` binding | E |

## 5. KPIs / acceptance

| KPI | Today | Target |
|---|---|---|
| Session boot | 10.4ms full isolate boot | ≤2ms snapshot restore |
| Isolate reboots per 100 evals / 10 sessions | 10 | 1 (resident actors) |
| Watchdog threads | 1 per guarded eval | ≤1 process-wide |
| JS-heap cost of a 5,000-node page DOM | full object graph (×4 duplication) | ~0.1× (handles + external text only) |
| Fork A/B/C of a 5k-node page | — (3 network reloads) | ≤1ms each, no network |
| Rewind of a branch | — | = mutation bytes |
| `test_rust_bridge.py` oracle parity | identical | identical at every phase |

## 6. Risks & honest limitations

1. **mprotect/SIGSEGV fork handlers** (v2) are exotic-but-proven; portability to macOS is
   fine, but they make the arena allocator single-threaded per region — a constraint the
   actor model already aligns with.
2. **Borrowed-view handles** must never outlive their epoch: handles are validated
   against `epoch_id`; a stale handle is an honest error, not UB.
3. **Snapshot blobs** are build/version-tied: hash invalidation + on-mismatch fallback
   to compile-from-source. Fast boot must never mean stale shims.
4. **Pointer compression flip** changes ABI; `RUSTY_V8_ARCHIVE` consistency must be
   re-verified after the rebuild (README's drop-in path).
5. **Multi-cage `IsolateGroup`** requires a rusty_v8 binding extension — parked (Phase F),
   not needed for realistic session counts.

## 7. Non-goals (rejected reuses of human-browser patterns)

- ❌ Materializing the DOM as JS objects per page (JSON ×4 duplication).
- ❌ Bump arenas for live JS objects inside the V8 cage (impossible; pointers must be
  cage-resident — the legit version is the host arena, §2.1).
- ❌ COW forking of the V8 heap (no primitive exists; fork = arena-level, §2.3).
- ❌ Fibers/green-threads interleaving JS in one isolate (conservative stack scanning).
- ❌ Replacing the scavenger/young-gen (1MB semi-spaces @ 100ms pages is already cheap).
- ❌ SIMD/NEON "DMA" scanners without a measured bottleneck.

---

## 8. Addendum — cross-review deltas (absorbed after verifying against the tree)

Two items from an external review were verified real and folded in:

1. **`--single-threaded` agent-mode flag** — exists (`base/src/flags/flag-definitions.h`, with
   implications disabling concurrent Sparkplug/WASM tasks). Added to the agent-mode flag set
   (Phase A/C) for short-lived isolates: collapses the ~6–10 background worker threads per
   isolate onto the driver thread. Measured per workload, not blanket: it also disables
   concurrent GC/compilation, so it is toggleable.
2. **Shared `ReadOnlyArtifacts` boot** — real and group-scoped (`base/src/heap/read-only-heap.cc:49-66`):
   first isolate deserializes the RO snapshot, every later isolate in the group copies roots
   instead (<1ms, multi-MB saved each). Sequencing note: this sharing only materializes AFTER
   the pointer-compression feature flip (Phase 0) — it rides on the shared-cage default that
   pointer compression brings.

**Rejected from the same review:** a `--lite-mode` flag (does not exist in
`flag-definitions.h` — invented name; `optimize_for_size` is the real one, already ours via
patch 0003), an `isolate-group.cc` edit to "enable" cage sharing (already the default via
`api.cc:10373`'s `Isolate::New(IsolateGroup::GetDefault(), params)` — the actual action is
the Phase 0 Cargo feature flip), MMU-COW fork of the V8 heap (impossible; arena-level only,
§2.3), and a claim about 5× kuchiki re-parsing in `dosh-browser-core` (parent product repo,
outside this folder — parked for verification there).

---

*Everything above that says "verified" cites file:line evidence gathered from this repo
and the pinned `v8 152.2.0` crate this session. Phases 0–C are unblocked today; Phase D
depends only on APIs already confirmed to exist.*

---

## 9. Execution log

### Step 0 — pointer-compression flip: DONE (2026-11-05)

- `core/Cargo.toml:14` — `v8 = { version = "152.2.0", features = ["v8_enable_pointer_compression"] }`.
  (The v8 dep lives in `core`, not `py` as earlier assumed — `py/Cargo.toml` carries no direct v8 dep.)
- Prebuilt availability pre-verified against `denoland/rusty_v8` release `v152.2.0`:
  `librusty_v8_ptrcomp_release_aarch64-apple-darwin.a.gz` exists — no from-source V8 build needed.
  The crate's `build.rs` (`prebuilt_features_suffix()`, `static_lib_name()`) maps the feature to a
  `_ptrcomp` archive name; the sha256 receipt in `target/release/gn_out/obj/librusty_v8.sum` confirms
  the ptrcomp archive was actually downloaded and linked (archive-sha256
  `ca501de5e99c…589` recorded there).
- `bench_phase0.py` (checked in, N=30, median): A/B on this machine —

  | build | boot (rust-v8, median) | boot p10—p90 | ΔRSS per minidom context |
  |---|---|---|---|
  | no ptrcomp | 9.7ms | 9.0–16.8ms | +2312KB |
  | ptrcomp | 12.1ms | 9.1–44.5ms | +2536KB |

  Honest cost statement: ptrcomp pays ~2.4ms boot tax and the RSS *win did not show up at
  this tiny heap size* (minidom contexts are ~2.3–2.5MB either way). The real payoff is the
  features it unlocks, not objects saved at 2MB scale: full old-gen address space headroom,
  and the shared RO-heap + shared-cage path (§8 item 2) — which only activates under
  ptrcomp and matters at Phase A structure (N isolates, N sessions).
- `test_rust_bridge.py` final: GREEN — all probes identical to the QuickJS oracle
  (documented WebAssembly/Proxy divergences only), wall-clock kill 0.50s, sync host
  round-trip 23µs/call, boot sampled 17.3ms single-shot.
- Committed state: feature ON, ptrcomp dylib installed in the venv (`doshgentv8_rust.cpython-314-darwin.so`),
  build reproducible via the README recipe. `RUSTY_V8_ARCHIVE` is still unset — when the
  patched fork `libdosh.a` drops in (Branch B) it must be a ptrcomp build or the link breaks.
