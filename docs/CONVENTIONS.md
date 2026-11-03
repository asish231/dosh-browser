# Conventions — how code, patches, and docs are written here

One place for the rules that keep the product, the fork, and the evidence consistent.
Governed by AGENTS.md (the contract); this file is the "how" that AGENTS.md implies but
does not spell out. Last verified against the tree: 2026-11-03.

---

## 1. Python (product code, `webresearch/`, `tests/`)

- Tool: **ruff**, pinned in `pyproject.toml`: `line-length = 140`, `target-version py311`,
  rules `E, F, I, UP, B, SIM, RUF` with the listed ignores. Run
  `.venv/bin/python -m ruff check` before committing.
- Imports: ruff `I` handles ordering; stdlib → third-party → `webresearch`.
- Never import from `doshgentv8/`, `v8_lane/`, or `research_repos/` inside `webresearch/`
  or `tests/`. The product stays engine-neutral; V8 reaches it only through the
  `WR_JS_ENGINE` switch.
- Tests: pytest, `testpaths = ["tests"]`. A new capability ships with a hermetic test
  from real captured markup where applicable (AGENTS.md rule). Tests that need optional
  deps self-skip with a reason string.
- Env is **uv-managed** (`uv pip install ... --python .venv/bin/python`); there is no pip
  in the venv. Python is 3.14 in dev; `requires-python >= 3.11` is the floor.

## 2. JavaScript (`webresearch/engine/minidom.js`)

- ES-subset style for the QuickJS-compatible runtime: `var` declarations, function
  expressions, no build step, no transpiler. Do not "modernize" syntax wholesale.
- One file, engine-agnostic. When behavior must differ per engine, it is
  **engine-conditional** (e.g. the TypedArray Proxy is unnecessary under V8), never a
  fork of the file.
- Comments carry the "why" (real debugging history per AGENTS.md); don't strip them when
  editing. New approximations get a disclosed-limit comment like the existing ones.

## 3. C++ / the V8 fork (`doshgentv8/base/`)

- Style: **Google C++ via V8's own `.clang-format`** (present at the tree root). A patch
  that isn't clang-format-clean will conflict on the next 4-week rebase. Check with:
  `clang-format --style=file doshgentv8/base/src/<file>` — never reformat whole upstream
  files; format only the lines your patch touches.
- License headers stay as-is (BSD-3, V8 authors). Our patch files keep upstream headers;
  our *new* files carry the V8 header plus a line noting the doshgentv8 addition.
- **Patches are a tracked series**, each one small and flag-gated:
  - naming: `doshgentv8/patches/NNNN-short-name.patch` (numbered, applies in order on
    the 15.9.0 base);
  - flags we add follow V8's `--dosh-*` namespace (e.g. `--dosh-wasm-fuel`,
    `--dosh-host-clock`, `--dosh-seed-rng`) and default **OFF**;
  - the overlay contract: **flag OFF = output byte-identical to stock V8.** Vendor wasm
    (Cloudflare/Turnstile etc.) always runs in spec mode. A patch that can't promise
    OFF-identity doesn't land.

## 4. Arch policy (the per-CPU-type dirs)

V8 15.9 keeps arch code inside each subsystem: `src/codegen/{x64,arm64,arm,ia32,riscv,
loong64,mips64,ppc,s390}` and the same pattern in `baseline/`, `wasm/baseline/`,
`maglev/` (no ia32), `deoptimizer/`, `regexp/`, `builtins/`, `execution/`, `diagnostics/`,
`heap/base/asm/`, `compiler/backend/`. Selection is build-time via `v8_current_cpu` in
`gni/v8.gni`.

Rules:

1. **Shared seam first.** A patch goes into the arch-shared file if one exists
   (e.g. wasm fuel metering targets `src/wasm/baseline/liftoff-compiler.cc`, which is
   shared; only the emitted instruction details may need per-arch assembler helpers).
2. **If arch code must change:** either change all arches in the same patch, or gate the
   feature off for untouched arches via a `v8_current_cpu` guard in GN + a runtime
   fallback. Never leave an arch silently broken.
3. **First-class arches** (built, tested, shipped): `x64`, `arm64` (macOS arm64 dev +
   Linux servers). **Build-only:** `riscv64`, `loong64` (compile green, no parity gate).
   **Not ours, keep compiling:** `arm`(32), `ia32`, `mips64`, `ppc`, `s390` — upstream
   code stays untouched so rebases stay clean; we do not test them.
4. Known asymmetries to respect: maglev has no ia32; the wasm interpreter exists only
   for arm64/riscv/x64; `src/codegen/shared-ia32-x64/` is shared by the x86 family.

## 5. Build & release recipe

- Workspace layout (`doshgentv8/Cargo.toml`, resolver 2): `core/` (pure Rust engine,
  NO pyo3 — grep-enforced) + `py/` (thin PyO3 facade, cdylib `doshgentv8_rust`) +
  `base/` (vendored V8 15.9.0 source). The core/facade split is the rewrite-proof
  design: Phase 5's pure-Rust product consumes `doshgentv8-core` directly.
- Build the bridge (Phase 1+, verified recipe):
  ```bash
  cd doshgentv8
  PYO3_PYTHON=../../.venv/bin/python cargo build --release
  cp target/release/libdoshgentv8_rust.dylib \
     ../.venv/lib/python3.14/site-packages/doshgentv8_rust.cpython-314-darwin.so
  .venv/bin/python doshgentv8/test_rust_bridge.py   # acceptance vs QuickJS oracle
  ```
  macOS link needs `-Wl,-undefined,dynamic_lookup` (CPython symbols resolve from the
  host interpreter) — in `doshgentv8/.cargo/config.toml`.
- Runtime deps: `v8` crate 152.2.0 (prebuilt static, aarch64-apple-darwin asset) +
  `pyo3` 0.30.1. Version pins verified by `cargo search` on 2026-11-03.
- Phase 3+: **our GN build**: `fetch` deps per `doshgentv8/base/DEPS`, `gn gen out/dosh`
  with per-target args (pointer compression ON to match Chrome; minimal ICU; sandbox per
  V8 default for the target), `ninja v8_monolith`, then swap the archive into the Rust
  bridge via `RUSTY_V8_ARCHIVE`. One recipe per first-class arch, recorded in
  `doshgentv8/README.md` when it exists.
- Parity gate before any engine switch becomes default: the full pytest suite on BOTH
  engines plus `v8_lane` probes green.


## 6. Tool & shell naming (agent-facing surface)

- MCP tools: `browser_<verb_noun>` snake_case, one capability per tool, description
  states limits honestly (e.g. "absent yt-dlp is reported as a missing capability,
  never faked" — keep that phrasing pattern).
- Shell commands: short words (`open`, `act`, `wait`, `caps`); dot-namespaces for
  groups (`net.rule`, `skill.save`, `frame.eval`).
- Verdicts are enumerated strings, never vibes: `verified / otp_required / failed /
  account_locked / human_only`. New flows add to the enum and document it in AGENTS.md.
- Coverage language is contractual: `N of M (complete)` or name the budget that hid the
  rest; `needs real engine` for non-actuable controls; never drop controls silently.

## 7. Docs

- `AGENTS.md` = the governing contract (rules, capability table, disclosure). Root-level
  product docs: `README.md`, `WASM_SCOPE.md`, `DOSH_AGENTIC.md`, `DOSH_TERMINAL.md`,
  `OPEN_ISSUES.md` (known gaps live here, honestly).
- `docs/` = planning & analysis (ALL-CAPS-ish names: `DOSHGENTV8-MASTER-PLAN.md`,
  `AGENTIC-VERIFY-CLASSIFY.md`, `AGENTIC-NATIVE-CAPABILITY-MAP.md`,
  `WAASM-AGENTIC-WASM-PLAN.md`, this file).
- Evidence convention for any analysis doc: claims are pinned `file:line` and marked
  "verified by <command/grep>"; corrections to earlier claims are recorded, not silently
  rewritten (see `AGENTIC-VERIFY-CLASSIFY.md` §6).
- `research_repos/` is reference material only — never linked, never copied into product
  code; its conventions do not apply here (AGENTS.md rule).

## 8. Evidence & testing discipline (applies to everything)

- Every new capability: hermetic test first or same-PR. Test against real captured
  markup (`testhtml/test1.html` and friends), not only hand-written HTML.
- Never assert on a substring of a whole page; read the specific element (AGENTS.md
  hard-won rules apply to fork patches too — e.g. don't "prove" fuel by grepping output).
- Distinguish "the action executed" from "the challenge passed" in every report.
- Baseline before edits: full suite is 823 passed / 8 skipped / 1 known flake
  (`test_port_transfer` fails only in the full run, passes solo — pre-existing, tracked
  in OPEN_ISSUES.md when confirmed as pollution). Re-run the suite before claiming an
  edit is safe.
