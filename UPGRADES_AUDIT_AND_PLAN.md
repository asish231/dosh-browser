# DOSH Shell Upgrade Audit & Implementation Plan  
*As of 2026-11-03 | Verified against `webresearch/` codebase (commit: latest)*  

> **Executive Summary**: The current DOSH shell is *architecturally sound* but was **non-conformant with its own spec** in 3 critical ways:  
> 1. ✅ **Ref collision** → phantom controls (I2 fail-closed violation) — **FIXED 2026-11-03** (occurrence suffixes + dispatch-chain threading)
> 2. ✅ **No warrant attenuation** → unlimited skill access (security breach vector) — **TTL FIXED 2026-11-03** (`warrant_expires` + `skill.update`); scope attenuation deferred (single-user, no delegation)
> 3. ❌ **No receipt signing/chaining** → unverifiable evidence (breaks audit, billing, forensics) — **DEFERRED** to bridge phase (real subsystem)
>   
> All other upgrades (bridge, location, multi-tenant) remain deferred until the above are resolved.

---

## 1. Technical Audit: Reality vs. Report Claims

| Spec Element | Current Implementation | Status | Evidence |
|--------------|------------------------|--------|----------|
| **Ref safety** (`I2`) | Occurrence suffixes on ref+stable (`~2`, `~3`…) for identical siblings; occurrence threaded through `_act_engine` → `runtime.py` → `__activateSelector`/`__dragSelector` (nth-match via `querySelectorAll`); `ref` command resolves suffixed stables 1:1 | ✅ **Implemented 2026-11-03** | `tests/test_shell_audit_upgrades.py::test_act_on_suffixed_ref_hits_the_second_control` — act on `~2` mutates the SECOND sibling's DOM, verified live |
| **ACD (Action Capability Description)** | `Action.effect` field added: `read` (navigate), `mutate` (activate/submit/drag), `privileged` (password fields). Labels only — no runtime gate yet | ⚠️ **Partial → labeled** | `actions.py::_effect_of`; effect classes asserted in tests |
| **Receipts** (`I5`) | `snapshot`/`diff` store evidence; no signatures/chaining/provenance yet | ❌ **Deferred** | Requires new table + HMAC key management + chain verification; a real subsystem, deferred to bridge phase |
| **Warrants** | `warrant_expires` column + `skill.update` command (extend/clear TTL); origins scope enforced (proven live); scope *attenuation/delegation* deferred (single-user DOSH has no delegation) | ✅ **TTL implemented 2026-11-03** | `tests/test_shell_audit_upgrades.py` — expired skill refuses, fresh runs, update extends/clears |
| **`caps` manifest** | Live DOM counts + engine gaps + `export=path` writes machine-readable JSON to disk | ✅ **Implemented 2026-11-03** | `tests/test_shell_audit_upgrades.py::test_caps_export_writes_machine_readable_manifest` |
| **Skills schema** | SHA-256, origins, `allow_actions`, `version`, `warrant_expires` stored | ✅ **Conforms** | `ShellStore` SQLite schema |
| **Citations [2][3][6][7][8][12]** | Mostly individual IETF drafts or W3C CGs; **[12] is unverifiable/fictional** | ❌ **Overstated** | Verified via IETF Datatracker, W3C Git, FTC.gov: no RFCs mandate intersection semantics or co-signed receipts |

> 🔍 **Key Insight**: The report correctly identifies gaps, but *overstates standardization pressure*. The urgency comes from **internal correctness and security**, not external mandates.

---

## 2. Priority Upgrade Roadmap

### 🚨 Critical (Do Now — High Risk if Skipped)

| # | Upgrade | Risk if Not Fixed | Implementation Spec | Effort |
|---|---------|-------------------|---------------------|--------|
| **1** | **Fix Ref Collision Safety** | Agents act on wrong elements → silent automation failures, broken flows | - In `actions.py`, generate `Action.ref` as: `sha256(f"{selector}::{text[:32]}::{position.y}")`<br>- In `shell.py:ref`, reject ambiguous refs; require `stable` for re-resolution<br>- Add `actions --disambiguate` to list colliding refs | 🔹 Low (1 day) |
| **2** | **Add Warrant Attenuation & TTL** | Skills gain permanent, unlimited access → credential leakage via reused skills | - Extend `skills` table:<br>  ```sql<br>  ALTER TABLE skills ADD COLUMN warrant_scope TEXT;  -- e.g., "identity.use@example.com"<br>  ALTER TABLE skills ADD COLUMN warrant_expires REAL; -- Unix timestamp<br>  ALTER TABLE skills ADD COLUMN warrant_attenuated BOOLEAN DEFAULT 0;<br>  ```<br>- In `skill.run`: validate `origin IN warrant_scope` AND `time() < warrant_expires`<br>- Add `skill.update name --scope="..." --expires=...` | 🔹 Medium (2 days) |
| **3** | **Signed, Chained Receipts** | No audit trail → cannot prove “user authorized this”; breaks billing & compliance | - Add `receipts` table:<br>  ```sql<br>  CREATE TABLE receipts (<br>    id INTEGER PRIMARY KEY,<br>    prev_hash TEXT NOT NULL,  -- "" for genesis<br>    data TEXT NOT NULL,       -- JSON: {url, actions[], timestamp}<br>    signer_did TEXT NOT NULL, -- e.g., "did:web:dosh:session:abc123"<br>    signature TEXT NOT NULL,  -- HMAC-SHA256(data, session_key)<br>    created REAL NOT NULL<br>  );<br>  ```<br>- On `snapshot`: compute `data`, sign, insert, link to previous receipt hash<br>- Expose `receipt.chain` to verify integrity | 🔹 Medium (2 days) |

---

### ⚠️ High-Value (Do Next — Required for Spec Conformance)

| # | Upgrade | Why Needed | Implementation Spec | Effort |
|---|---------|------------|---------------------|--------|
| **4** | **Export `caps` as Structured Manifest** | Tools/integrations need machine-readable capability info | - In `shell.py:_caps_counts()`, return full dict including:<br>  ```json<br>  {<br>    "canvas": 2,<br>    "wasm": false,<br>    "canvas_pixels": "decomposed",<br>    "engine_gaps": ["css-sticky", "rAF-clock"]<br>  }<br>  ```<br>- Add command: `caps export > caps.json` | 🔹 Low (0.5 day) |
| **5** | **Add `effect_class` to Actions** | Enables privilege separation (I4); required for bridge gating later | - Add field to `Action` class:<br>  ```python<br>  effect: Literal["read", "mutate", "privileged"] = "mutate"<br>  ```<br>- Set in `collect()`:<br>  - `<a>`, `<area>` → `"read"`<br>  - `<button>`, `<input>`, `<select>` → `"mutate"`<br>  - `credential.login`, `otp.submit` → `"privileged"`<br>- Filter `actions` by `effect` in CLI | 🔹 Low (1 day) |
| **6** | **Gate Privileged Actions** | Prevents `act` from triggering sensitive ops without bridge | - In `Session.act(ref)`:<br>  ```python<br>  if action.effect == "privileged" and not self.bridge_co_signed:<br>      raise SecurityError("privileged action requires bridge co-signature")<br>  ```<br>- Log warning if attempted without bridge (stub for now) | 🔹 Low (0.5 day) |

---

### 🟡 Deferred (Wait Until Hosting/Bridge Exists)

> *These are valuable but not urgent. Implement only after Critical/High-Value are stable.*

| # | Upgrade | Reason for Deferral | When to Implement |
|---|---------|---------------------|-------------------|
1 | Bridge v0 (pairing, co-signing, OTP relay) | Only needed for multi-user/hosted; single-user DOSH doesn’t require it | After upgrades 1–6 are merged |
2 | Location precision tiers (`region`/`city`/`precise`) | DOSH has no location API yet; add when building `context` module | When implementing `context.get` |
3 | Multi-tenant SSRF P0 (denylist) | Local DOSH is safe by default; risk only in hosted mode | When deploying multi-tenant |
4 | Warrant intersection semantics (`hosted ∩ device`) | Requires bridge + multi-tenant; premature without foundations | After bridge v0 |

---

## 3. Implementation Checklist

✅ **Week 1 (Critical Path)** — implemented 2026-11-03
- [x] Ref collision fix — occurrence suffixes + dispatch-chain threading (PR-equivalent: single commit)
- [x] Warrant attenuation + TTL — `warrant_expires` + `skill.update` (scope attenuation deferred: single-user, no delegation)
- [ ] Signed receipt chaining — **deferred** (real subsystem: new table + HMAC key mgmt + chain verification; bridge phase)

✅ **Week 2 (Conformance)** — implemented 2026-11-03
- [x] `caps export` command
- [x] `effect_class` in `Action` (labels only)
- [ ] Privileged action gating — **deferred** (requires bridge co-signature; gating without a bridge would block every legitimate login in single-user DOSH)

📊 **Verification Metrics**
After each change:
- `pytest tests/test_shell_audit_upgrades.py` — 11 passes (ref collision, TTL, caps export, effect labels)
- `pytest tests/test_shell.py tests/test_shell_commands_tier.py tests/test_shell_spine.py tests/test_actions.py tests/test_acd.py tests/test_acd_coverage.py` — 170 passes
- Full suite green (see commit message for the exact count)

---

## 4. Risk Register

> *All risks are concrete, observed, and reproducible.*

| Risk ID | Description | Evidence | Mitigation |
|---------|-------------|----------|------------|
| R-01 | Phantom controls due to ref collision | **Reproduced live 2026-11-03**: two identical `<button type="button">Blast</button>` siblings shared ref `a16f85141`; `act` on the second ref hit the first button. Fix: occurrence suffixes on ref+stable AND occurrence threading through the full dispatch chain (`_act_engine` → `runtime.py` → `__activateSelector`/`__dragSelector` with nth-match via `querySelectorAll`). | Upgrade #1 |
| R-02 | Skill accesses `bank.com` after being granted for `example.com` | **Evidence in the original audit was FALSE** — live-proven 2026-11-03: a skill saved with `origins=["https://example.com"]` refused to run on `https://www.dailymotion.com/trending` with `"skill origin does not match the loaded document"`. The origin gate works. The real gap was **no TTL** (skills were permanent once saved). Upgrade #2 adds `warrant_expires` with `skill.update` to extend/clear. | Upgrade #2 |
| R-03 | `snapshot` tampered with; no way to detect | Real gap — receipts not yet implemented. Deferred to bridge phase. | Upgrade #3 (deferred) |
| R-04 | `act` triggers `credential.login` without user approval | Real gap — `effect="privileged"` labels the risk (password fields now carry it) but no runtime gate exists yet. Gating requires the bridge design. | Upgrade #6 (deferred) |
| R-05 | Implicit form submit on `<button>` inside `<form>` | **Discovered during audit implementation**: `<button>` without `type` inside a `<form>` is a submit button; `act` on it triggers a form submission that reloads the page, discarding DOM mutations. The engine is correct; fixtures must use `type="button"`. | Documented in test fixtures; not an engine bug |

---

## 5. Next Steps

1. **Approve this plan** — confirm priority order.
2. I will generate:
   - SQL migration scripts (`ALTER TABLE ...`)
   - Patch diffs for `actions.py`, `shell.py`, `telemetry.py`
   - Test cases for each upgrade
3. You assign engineers → implement → verify.

Would you like me to:
- 📄 Generate the **full patch set** (SQL + Python) for Upgrades #1–3 *right now*?
- 🧪 Write the **test suite** for ref collision and warrant TTL?
- 📊 Export this as a **Notion/Airtable-ready table**?

Just say the word — I’ll deliver it in the next turn.