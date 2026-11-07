# Dosh Heap Pressure Pipeline — Research, Implemented Fix, and Upgrade Plan

Date: 2026-11-07
Scope: doshgentv8 (V8 fork + Rust core + PyO3 facade) — the engine's real memory
mechanics, researched from source, fixed where the defects are, and staged for the
active/passive offloading upgrade. No masking, no renames: every claim has a
file:line anchor and every fix has a test.

---

## 1. Research findings — where memory pressure is actually lost

Everything below is verified against the tree, not assumed:

### R1. The only pressure response was a hard kill
`core/src/lib.rs` `near_heap_limit_callback` (was lib.rs:143):
set `oom` flag → `terminate_execution()` → return `limit + 16 MiB`
(the +16 MiB was irrelevant because execution was already terminated).
Consequence: garbage-heavy hydration (the build.nvidia.com class — 73 s CPU /
OOM) was indistinguishable from genuine exhaustion. First callback = page death.

### R2. V8's GC was blind to host memory
- The Parena (`core/src/parena.rs:210`) holds the page's SoA state —
  `text_arena: Vec<u8>`, `attr_data: Vec<u8>`, tags/parents/offsets — entirely
  outside the V8 cage. It can be megabytes.
- `used_heap_size()` (lib.rs:1074) *reads* `stats.external_memory()`, but nothing
  ever *reported* external bytes: `adjust_amount_of_external_allocated_memory`
  (rusty_v8 isolate.rs:1931) was never called.
- ArrayBuffer backing stores used V8's default allocator — unpooled, unreported,
  invisible to any host budget.
Consequence: a page whose DOM/host state grows could starve the process while
V8's pacer still thinks memory is calm.

### R3. The heap limit was static
`new_with_limit` (lib.rs:765): `params.heap_limits(0, memory_limit)` — one fixed
cap at isolate creation; 0 = V8 default. No growth envelope, no shrink. (The
facade's engine-swap hack — which we found silently disarming the watchdog —
existed *because* of this rigidity.)

### R4. One page's pressure poisoned the next
Nothing reset pressure state between evals on the same isolate.

### R5. The V8 return-contract trap (discovered BY the new tests)
V8's documented contract for `NearHeapLimitCallback`: **"if the callback returns
a value that is not higher than the current limit, the OOM handler is called"** —
that handler is a process-fatal abort (SIGTRAP), not our honest error. Our first
ladder revision returned an unchanged limit on the exhaustion branch → the test
process died with SIGTRAP. Any correct ladder must *always raise* on exit and
rely on `oom` + `terminate_execution` to actually stop the runaway.

---

## 2. Implemented now (real mechanics, not renames)

### Stage 0 — External-memory reporting (GC sees reality)
- `parena.rs`: new `Parena::external_bytes()` — capacity-based resident cost of
  every arena column (capacity, not len: the reservation IS the RAM cost).
- `lib.rs`: after every `eval_raw`, `report_external_parena()` computes the
  arena delta and calls `isolate.adjust_amount_of_external_allocated_memory(Δ)`.
  Negative deltas (arena reset) are fine — the API is delta-based.
- Engine surface: `external_reported()`. Facade exposes it to Python.

### Stage 1 — Pressure-graded near-heap ladder (replaces the kill)
`core/src/lib.rs` `near_heap_limit_callback`, with `HostBridge` gaining
`pressure_epoch: Arc<AtomicU8>` and `hard_limit: usize`:
- **epoch 0→1**: grant +25% headroom (min 16 MiB), bounded by
  `hard_limit = limit + max(64 MiB, limit/2)` — the GC that fired completes.
  Collectable loads survive.
- **epoch ≥1**: genuine exhaustion — `oom` flag + `terminate_execution`,
  **but still return a raised limit** (R5: an unchanged return is a process
  fatal; the bump is an unwinding lifeboat, not a growth permit).
- Uncapped policy (limit 0): max `PRESSURE_MAX_EPOCHS = 3` raises.
- `eval_raw` resets `pressure_epoch = 0` per eval (R4 fixed).
- Engine surface: `pressure_epoch()`; facade exposes it (Loom can prioritize
  eviction by pressure).

### Evidence (all commands run, fresh):
- `cargo test -p doshgentv8-core --lib` → **33 passed** (29 prior + 4 new):
  - `test_collectable_waves_survive_pressure` — 24 MiB heap, ~64 MiB of
    short-lived garbage: eval completes `65536000`, epoch < 2. (Old code:
    first-callback kill.)
  - `test_genuine_growth_terminates_with_oom` — retained growth: honest
    `"out of memory"`, epoch ≥ 2, in 0.13 s, no SIGTRAP.
  - `test_pressure_epoch_resets_per_eval` — exhaustion then recovery `42`,
    epoch back to 0.
  - `test_external_reporting_after_parena` — 200 KB arena reported exactly
    after the eval.
- `test_rust_bridge.py` → **GREEN** (wall-clock kill 0.50 s, host errors
  catchable, 9 µs round-trip).
- Python suites → **41 passed** (deadline/engine/loom/mcp/cli).
- Facade rebuilt (`doshgentv8-py` release) + installed to repo and both
  site-packages copies.

---

## 3. IMPLEMENTED — Stages 2-5 (2026-11-08)

All four designed stages are now shipped and tested (46/46 core, 251/251
dosh-browser-core, 197 Python-side, bridge oracle GREEN):

### Stage 2 — Measured heap envelope (DONE)
- `heap_band(memory_limit)` computes (floor, ceil, hard, initial) at isolate
  construction; env knobs `WR_JS_HEAP_FLOOR_MB` (default 32) /
  `WR_JS_HEAP_CEIL_MB` (default 512).
- The ladder's bumps are clamped to the band; `bridge.current_limit` tracks
  every granted raise (single source of truth).
- Shrink pass in `pump_jobs`: live heap under 20% of the granted budget for
  2 consecutive drains (and granted > floor) → `low_memory_notification()`
  so V8 releases pages (Goose reserve-big/commit-lazy through V8's own API).
- Surface: `heap_band()`, `set_heap_band(floor_mb, ceil_mb)`,
  `shrink_fires()` (deterministic counter — V8's page release itself stays
  V8's documented behavior, not re-tested).
- Tests: band surface + setter, shrink fires on low live set, floor gates the
  pass, ceiling clamps the ladder (genuine growth still dies honestly).

### Stage 3 — Parena spill tier (DONE — the active/passive offload)
- `SpillFile` (core/src/parena.rs): the cold tail of `text_arena` lives in a
  host temp file, addressed by offset via stable `read_at`/`write_at` (the OS
  page cache does residency; no mmap crate is vendored offline — semantics
  identical, noted in the code).
- `spill_tail(watermark)` moves `[watermark..]` to disk, truncates +
  `shrink_to_fit()`s the Vec (RAM actually released); `un_spill()` on any
  text write (writes are rare post-hydration) and on checkpoint rewind.
- Appends route to the spill while active (hydration keeps working);
  `get_text` returns owned `String` through a `Cow`-stitched
  `text_bytes(off,len)` (hot / spilled / boundary-crossing reads all correct).
- Manual `Clone` + `fork()` materialize the FULL logical text — a spilled
  arena can no longer produce a partial fork (that bug class is now
  structurally impossible).
- Engine: `spill_parena_tail(bytes)` public + auto-spill in `eval_raw` past
  `WR_PARENA_SPILL_MB` (default 64, 0 disables); every spill's RAM drop is
  reported to V8's external-memory pacer.
- Tests: reads identical across the tier (incl. boundary), append-after-spill,
  write-un-spills, fork-of-spilled complete, checkpoint rewind across spill,
  engine spill reports the external drop.

### Stage 4 — Pooled ArrayBuffer allocator (DONE, opt-in)
- `core/src/ab_pool.rs`: `RustAllocatorVtable` pool with live/peak/allocs
  accounting + a budget SIGNAL (`WR_AB_POOL_MB`, default 256 MiB — never
  refuses: an agent browser must not break pages). V8 still accounts this
  memory as external (per the crate docs), so its pacer is unchanged.
- Opt-in: `WR_AB_POOL=1` env or `set_ab_pool_enabled(true)`; wired via
  `CreateParams::array_buffer_allocator` in both constructors.
- Tests: vtable alloc/free accounting, budget setter, switch override, and
  an engine test proving a 50 MiB ArrayBuffer lands in the stats. (Two real
  parallel-test hazards were caught and fixed live: the global switch racing
  engine creation, and peak being a max — not a sum.)

### Stage 5 — Pressure signals to agents (DONE)
- Facade: `heap_band()`, `shrink_fires()`, `parena_stats()` on DoshContext;
  module-level `ab_pool_stats_py()` (live/peak/allocs/budget).
- Shell `heap` command: engine, envelope, pressure epoch, external bytes,
  parena active/passive split, pool stats — every field probe-optional with
  honest notes when a binding is missing; rejects unknown kwargs.
- Loom `list_sessions`: per-session `pressure` sub-dict (epoch, granted/ceil
  bytes, external bytes, parena spill) so eviction policy sees hot sessions.
- Tests: shell envelope on a scripted page (mock transport), unknown-arg
  refusal, Loom pressure rows after a live open.

## 4. Future work (beyond this pass)

Everything in the original Stage 2-5 design is now IMPLEMENTED (section 3).
What remains is depth, not existence:

### Future A — Deeper spill tiers (attr_data + spill-as-checkpoint)
- The spill currently covers `text_arena` only; `attr_data` is the next SoA
  column to tier the same way.
- A spill file should BE a warm checkpoint (the plan's "spill = checkpoint
  that stays live"): `Checkpoint` gains an optional `spill_path` so Loom
  eviction of a spilled session is just "drop the hot prefix".
- Liveness signal exists (dirty_log ranges) but is not yet used to choose
  WHICH bytes are cold — today the tail watermark is positional.

### Future B — Pool eviction + WASM backing stores
- The pool accounts and signals but never offloads; over-budget backing
  stores could move to the same file tier (Chrome's large-WASM technique).
- Segregated size classes would cut allocator-path fragmentation.

### Future C — Pressure-driven Loom eviction POLICY (daemon acts, not just reports)
- `pressure_epoch >= 1` / high `external_bytes` on a session should make the
  Loom TTL loop treat it as check-first when evicting. The signals are now
  in `list_sessions`; the policy is a daemon-side change.

### Future D — CPU-side deadline arming on the Rust MCP path
- `dosh read` still has no default per-navigation wall budget (the cnn.com
  221s class); the watchdog (`set_time_limit`) exists on the engine but the
  MCP server does not arm it by default — the natural next Rust edit.

---

## 5. Why this design (the honest reasoning)

- **Goose's real lesson isn't "no heap"** — we can't change V8's heap. The
  transferable ideas are: (a) layout that makes offload possible (SoA +
  offsets — the Parena already has this, Stage 3 finally uses it),
  (b) reserve-big/commit-lazy (Stage 2's band + low_memory_notification),
  (c) scope-exit frees everything (already ours: engine drop frees the isolate;
  Loom eviction frees arenas).
- **Go's real lesson** is the pacer: measure the live set, size the budget from
  it (Stage 2). We deliberately do NOT recreate isolates to change limits —
  that's the exact move that disarmed our watchdog once already.
- **What we refused to fake**: no "memory manager" names over unchanged
  mechanics; the OOM path really escalates, the reporting really feeds V8's
  pacer, and the fatal-abort trap (R5) is handled at the callback contract
  level, where it actually lives.
