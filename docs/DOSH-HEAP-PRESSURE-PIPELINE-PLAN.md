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

## 3. Designed next (the active/passive offloading upgrade)

Ordered by leverage-per-risk. Each stage is independently shippable and testable.

### Stage 2 — Measured heap envelope (Go-pacer, Goose reserve-committed-lazy)
Replace the static cap with a **band** `[floor, ceil]` (default 32–512 MiB, env
`WR_JS_HEAP_FLOOR_MB` / `WR_JS_HEAP_CEIL_MB`):
- **Grow**: Stage 1's ladder already grows the limit — keep it, but clamp bumps
  to `ceil` instead of `limit + 50%`.
- **Shrink**: after `pump_jobs` GC drain, read `get_heap_statistics()`
  (lib.rs:1074-1081 already reads it). If
  `used_heap_size < current_limit × 0.4` for 2 consecutive drains, call
  `isolate.low_memory_notification()` (rusty_v8 isolate.rs:1433) so V8
  releases pages back — Goose's "reserve big, commit lazily, return pages"
  translated to V8's own machinery.
- Pacer target (Go GOGC=100 style): after each drain, the *desired* limit is
  `2 × live_set`, clamped to the band; growth happens only via the Stage-1
  ladder (per V8's rule that limits rise through the callback).
- Test: allocate 200 MiB in waves at a 64 MiB ceiling → peak stays ≤ ceiling ×
  (1 + 25%); after the waves, `total_heap_size` shrinks back toward the band
  floor. Honest note: V8 releases pages asynchronously — the test asserts the
  trend, not an exact number.

### Stage 3 — Parena spill tier (the real offload: active/passive inside a session)
The Parena's SoA layout is what makes offloading possible at all — a normal heap
could never do this, because JS-side pointers pin everything. JS holds *offsets*,
not pointers, so the host can move the backing bytes freely:
- `Parena` gains a spill backend: past a watermark (`WR_PARENA_SPILL_MB`,
  default 64), the **cold tail** of `text_arena`/`attr_data` moves to an
  mmap-backed file (Goose-style: position-independent blocks, save = one write,
  load = one read + verify). Hot prefix stays resident.
- Cold = bytes not touched since the last dirty-diff checkpoint region
  (`dirty_log` at parena.rs:221 already tracks ranges — the liveness signal
  exists).
- Reads crossing the spill boundary fault the region back in (mmap does this
  for free; on hit, mark the region hot again).
- Checkpoint integration: a spill IS a warm checkpoint that stays live —
  `save_checkpoint` (parena.rs:Checkpoints) gains `spill_path`.
- Loom integration: eviction of a spilled session becomes near-free (drop the
  hot prefix; the spill file IS the checkpoint).
- Test: 100 MB page text at a 32 MB arena watermark → RSS stays bounded, all
  reads still return correct bytes, spill file size ≈ cold bytes.

### Stage 4 — Pooled ArrayBuffer allocator (make WASM/blobs evictable)
- Replace V8's default allocator with a host pool via `CreateParams`
  (rusty_v8 `Isolate::new(params)` accepts a custom `v8::ArrayBuffer::Allocator`).
- Pool: segregated size classes; allocations reported to the pacer (Stage 0
  pattern); over-budget backing stores move to mmap and become candidates for
  the same spill tier as Stage 3 (V8 externalizes backing stores — this is
  Chrome's technique for large WASM memories).
- Test: allocate 100 MB of ArrayBuffers at a 48 MB pool → allocations succeed
  (mmap-backed), `external_reported()` grows, V8 GC reacts to the reported
  pressure.

### Stage 5 — Pressure signals into Loom (daemon policy)
- `pressure_epoch ≥ 1` on a session → Loom's TTL loop treats it as
  "check-first" when evicting (the daemon already has `list_sessions` /
  eviction in `webresearch/loom.py`).
- `external_reported()` joins `browser_heap` MCP output so agents can reason
  about host+heap budget together.

---

## 4. Why this design (the honest reasoning)

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
