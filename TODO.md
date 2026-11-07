# DOSH-LOOM Implementation TODO — ALL PHASES COMPLETE

## Phase 1 — Unix socket daemon ✅
- [x] `webresearch/loom.py`: SessionRegistry (LRU+TTL), LoomDaemon, JSON frame protocol
- [x] `serve --daemon` CLI mode
- [x] Tests: `tests/test_loom.py` — 11 passed

## Phase 2 — MCP thin-client adapter ✅
- [x] `webresearch/mcp_daemon_client.py` (LoomClient)
- [x] `webresearch/mcp.py` `_handle_via_loom` routing + in-process fallback
- [x] Tests: `tests/test_mcp_thin_client.py` — 6 passed

## Phase 3 — CLI thin-client ✅
- [x] `webresearch attach <session_id>`
- [x] `webresearch sessions`
- [x] Tests: `tests/test_cli_thin_client.py`; live E2E verified

## Phase 4 — Event journal ✅
- [x] `webresearch/loom_journal.py` (`sessions_journal` table, record/get)
- [x] Daemon records attach/command/detach in dispatch
- [x] `webresearch journal [--session]`
- [x] Tests: journal record/retrieve/filter/in-daemon

## Phase 5 — Rust lane integration ✅
- [x] `dosh-browser-core/src/daemon_client.rs`: persistent `Session`, `DaemonClient`, `read_journal`
- [x] `dosh attach / sessions / journal` wired to the Python daemon
- [x] Updated `lib.rs` daemon test to persistent-session API
- [x] Rust tests: `cargo test -p dosh-browser-core` — 251 passed
- [x] Live cross-lane: Rust sees Python session + journal and attaches to it

## Final verification
- Python: 194 passed across loom/mcp/cli/shell/engine/deadline suites
- Rust: 251 passed
- Doc: `docs/DOSH-LOOM-SESSION-DAEMON-PLAN.md` updated with status table + known limitation
