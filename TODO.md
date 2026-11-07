# Goal
Commit all updates in clustered commits with detailed messages, push, then implement the DOSH-HEAP-PRESSURE-PIPELINE plan fully.

## Acceptance criteria
- [x] AC1: All changes committed in themed clusters (not one giant commit, not per-file), detailed messages, pushed to origin/main
- [ ] AC2: Stage 2 — measured heap envelope: band (floor/ceil), ladder bumps clamped to ceil, shrink via low_memory_notification; tested
- [ ] AC3: Stage 3 — Parena spill tier: cold tail to mmap-backed file, hot prefix resident, transparent reads, un-spill on write; tested
- [ ] AC4: Stage 4 — pooled ArrayBuffer allocator: vtable-based pool, accounting, opt-in env; tested
- [ ] AC5: Stage 5 — pressure signals surfaced: Python shell `heap` command, Loom list includes pressure, facade exposure; tested
- [ ] AC6: All existing suites stay green (33 core / 251 dosh-browser-core / Python slices)
- [ ] AC7: Final clustered commits for the pipeline work + push

## Non-goals
- Committing doshgentv8/.perch (pre-existing untracked, not ours)
- Changing V8's internal GC (we drive it through its documented API only)

## Plan
### Phase C - commits (DONE)
- [x] C1..C7 clustered commits + push  (evidence: git log 7 commits, push 925b1db2..bfe148b4)

### Phase S2 - measured heap envelope
- [~] S2.1 current_limit tracking in HostBridge + band fields on engine | tests: cargo lib
- [ ] S2.2 shrink pass in pump_jobs (2-drain streak -> low_memory_notification) | test: waves then drain, heap shrinks
- [ ] S2.3 env knobs WR_JS_HEAP_FLOOR_MB/WR_JS_HEAP_CEIL_MB + set_heap_band | test

### Phase S3 - Parena spill tier
- [ ] S3.1 add memmap2 dep; SpillStore in parena.rs
- [ ] S3.2 text accessor (Cow) + read/write sites through it; un-spill on write/append
- [ ] S3.3 engine spill_parena_tail + auto via WR_PARENA_SPILL_MB | tests: bytes intact across boundary, RAM released

### Phase S4 - pooled ArrayBuffer allocator
- [ ] S4.1 ab_pool.rs: PoolStats + vtable (alloc_zeroed/free), static global
- [ ] S4.2 wire via CreateParams (env WR_AB_POOL=1 opt-in) + stats accessor | test: allocate 50MB, live/peak grow, free drops

### Phase S5 - pressure signals to agents
- [ ] S5.1 Python shell `heap` command (pressure_epoch, external_reported, heap stats)
- [ ] S5.2 Loom list_sessions includes pressure info | tests

### Phase F - final
- [ ] F.1 full suites re-run fresh; commit clusters; push

## Log
- start: 7 commits pushed (b5d152da..bfe148b4). Stages 0+1 already shipped in commit 1d7801ad.

## Blocked / needs user
- (none)
