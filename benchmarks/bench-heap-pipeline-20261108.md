# Heap Pressure Pipeline — Deep A/B Benchmark (2026-11-08)

**Question asked:** did the heap/cap upgrade (Stages 0-5) actually do any good?

**Method:** apples-to-apples A/B against the pre-pipeline engine. `git worktree`
at `b5d152da` (the last commit before the pipeline core landed) builds the OLD
engine (hard-kill near-heap callback); HEAD builds the NEW one (pressure-graded
ladder, envelope, spill, pool). Identical bench code, identical env, same
machine. RSS via `ps -o rss=`. No synthetic claim without a run behind it.

Repro:
```bash
cargo run -p doshgentv8-core --release --example pressure_bench
cargo run -p doshgentv8-core --release --example spill_bench
WR_AB_POOL=1 cargo run -p doshgentv8-core --release --example ab_pool_bench
```

---

## B1 — Synthetic pressure scenarios (old vs new core)

Same example (`core/examples/pressure_bench.rs`) on both engines:

| scenario | cap | OLD verdict | NEW verdict | NEW epoch | NEW granted |
|---|---|---|---|---|---|
| collectable-64MiB-garbage | 24MB | PASS 0.01s | PASS 0.01s | 0 | 25.1MB |
| mixed-hydration-40MiB | 32MB | PASS 0.01s | PASS 0.01s | 0 | 33.5MB |
| promotion-churn-150MiB | 8MB | PASS 0.04s | PASS 0.08s | 0 | 8.3MB |
| live-near-cap-churn | 8MB | PASS 0.16s | PASS 0.16s | 0 | 8.3MB |
| retained-growth-unbounded | 16MB | **FAIL "out of memory"** 0.03s, RSS 24MB | **FAIL "out of memory"** 0.11s, RSS 41MB | 2 | 44MB |

**Honest finding #1 (against our own earlier claim):** on these synthetic
JS-only loads the OLD engine is just as good — `pressure_epoch = 0` everywhere
proves the near-heap callback NEVER FIRED. V8's scavenger/major GC paces pure
JS workloads below the cap on its own. The unit-test narrative ("old code
killed garbage-heavy hydration") was wrong for these shapes. The callback
fires on **real framework pages**, not on toy loops.

**Honest finding #2 (cost of the new arch):** at genuine exhaustion the new
ladder grants bounded headroom up to the ceiling before dying honestly
(hard ceiling = `limit + max(64MiB, limit/2)`), so the fatal case uses MORE
RSS before its honest OOM (41MB vs 24MB at a 16MB cap) and takes slightly
longer (0.11s vs 0.03s). That is the measured price of not killing
collectable loads — and it stays bounded.

## B2 — Real-page A/B: huggingface.co (the money benchmark)

Python lane (`webresearch read`), same venv, same flags; OLD = worktree
`PYTHONPATH` override with the old facade staged.

| config | OLD JS errors | NEW JS errors |
|---|---|---|
| 512MB heap (default) | classList gap + 1 interrupted (slow script.js > 10s/eval watchdog) | **identical set** |
| **16MB heap cap** | classList gap + 1 interrupted + **`RuntimeError: out of memory`** (a script HARD-KILLED by the old callback mid-hydration) | classList gap + 1 interrupted — **NO out-of-memory** |

- The `classList.toggle` minidom gap and the slow-script watchdog hit are
  engine-independent (present in both lanes at every cap) — pre-existing,
  not memory behavior.
- **The differentiator is one line: OLD loses a script to `out of memory`
  under pressure; NEW survives it.** That is the R1 defect, on a real page.
- Live proof the ladder engaged (NEW @16MB, `heap` command):
  `granted_bytes = 27262976` (26MB) against a 16MB initial — the callback
  fired, granted GC headroom, hydration completed. Epoch resets per eval,
  so the granted-limit footprint is the persistent evidence.
- `shrink_fires: 3` on a light real page (httpbin) — the Stage-2 envelope's
  shrink pass demonstrably runs on real pages.
- **Honest note (our own test bug, kept for the record):** the first A/B run
  showed identical "interrupted" errors in both lanes — because we set
  `WR_JS_TIME_LIMIT_MS=120` thinking seconds. It is MILLISECONDS: 120ms
  watchdog-killed scripts in both lanes. Corrected to 30000 before drawing
  any conclusion. Units matter.

## B3 — Stage-3 spill tier (200MB arena, 64MB watermark)

`core/examples/spill_bench.rs` (Rust lane, real Parena):

```
nodes,800, text_bytes,200000000
rss_before_spill_kb,200368
spill_bytes,132891136  hot_prefix_bytes,67108864
rss_after_spill_kb,326160        <- RSS high-water GREW (write-time tail copy)
read_first_ok,true  read_last_ok,true  <- byte-identical across the tier
reuse_probe: fresh 130MB allocation -> RSS grew only 7MB (333584KB)
control (2nd full arena, no spill) -> RSS 528992KB (+200MB, as expected)
```

**Honest finding #3:** macOS does not shrink the RSS high-water on free —
`rss_after_spill` is misleading. The REUSE PROBE is the honest proof: after
spilling the 133MB cold tail to disk, a fresh 130MB allocation grew RSS by
only **7MB** — the reclaimed pages were genuinely released and reused
(without release, RSS would have hit ~456MB). Reads stay byte-identical
(hot, spilled, and boundary nodes verified). Active/passive offload: real,
measured.

## B4 — Stage-4 pooled ArrayBuffer allocator (100MB of ArrayBuffers)

| | default | `WR_AB_POOL=1` |
|---|---|---|
| result | Ok("100") | Ok("100") |
| pool_live / peak / allocs | 0 / 0 / 0 (invisible) | **104857600 / 104857600 / 100** |
| RSS | 12.4MB | 12.4MB |

The default allocator is host-INVISIBLE (0/0/0) — that was R2's twin defect.
The pool makes backing stores countable (`live=100MB, allocs=100`) with a
budget signal. RSS is identical because V8/malloc zero-pages are lazily
mapped (untouched buffers never fault in) — the pool's win is observability
and the pressure signal, not raw memory.

## B5 — Stage-2 shrink pass

Counter-level evidence only: `shrink_fires: 3` on a light real page. An
RSS-drop proof is impractical on macOS (the high-water quirk above), so we
claim only what the counter proves: the pass runs and asks V8 for
low-memory GCs when the live set stays far under the granted budget.

---

## Verdict — did the new arch do any good?

**Yes, exactly where it was aimed, and not elsewhere:**

1. **Under real memory pressure, pages survive instead of losing scripts.**
   Measured on huggingface.co @16MB: OLD = `out of memory` script kill;
   NEW = clean. This is the core defect (R1) fixed, with real-page evidence.
2. **Genuine exhaustion stays honest and bounded** — both engines die with
   `out of memory`; the new one spends bounded headroom first (measured
   cost: +17MB RSS, +0.08s at a 16MB cap) and never hits the V8 SIGTRAP
   fatal (R5, which my first ladder revision DID hit until the test caught
   it).
3. **The pipeline is invisible when unneeded** — at default heaps both
   engines are byte-for-byte identical in behavior (B2 row 1).
4. **Synthetic loads never engaged it** (epoch=0 across the board) — my
   earlier unit-test narrative overstated the old engine's weakness on
   toy workloads; the real discriminator is framework pages.
5. **The spill tier releases real RAM** (reuse-probe-measured) and keeps
   reads byte-identical; **the pool turns invisible allocations into
   countable ones.**

**What it did NOT fix (unchanged from the earlier verdict):** cnn.com-class
CPU cost (needs the read-path deadline — plan Future D), the Python shell
`open` hang on ultra-heavy pages, bot walls, and the Parena being
Rust-lane-only (Python navigation builds no arena — spill doesn't apply
there yet).

**Repo-hygiene note found during this A/B:** intermediate commit `b5d152da`
does not build its `py` crate standalone (the facade committed there
references core methods that land in the NEXT commit). HEAD is consistent;
only that historical intermediate is affected — visible only when building
that exact commit, as this worktree did.
