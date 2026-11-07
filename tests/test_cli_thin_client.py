"""Tests for CLI thin-client (Phase 3) and event journal (Phase 4)."""

import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager

import pytest

from webresearch.loom import LoomDaemon
from webresearch.loom_journal import (
    record_journal_entry,
    get_journal_entries,
    get_all_journal_entries,
)


@pytest.fixture
def daemon_factory(tmp_path):
    short_id = secrets.token_hex(4)
    sock = f"/tmp/dosh_cli_test_{short_id}.sock"
    db = str(tmp_path / "loom.sqlite")

    @asynccontextmanager
    async def make():
        daemon = LoomDaemon(socket_path=sock, db_path=db, max_live=4, ttl_seconds=300)
        await daemon.start()
        try:
            yield daemon
        finally:
            await daemon.stop()

    return make


@pytest.fixture
def journal_db(tmp_path):
    """A fresh journal database."""
    return str(tmp_path / "journal.sqlite")


# --- Phase 4: Event Journal ---


@pytest.mark.asyncio
async def test_journal_record_and_retrieve(journal_db):
    """Journal entries are recorded and retrieved correctly."""
    sid = "test_session_1"
    record_journal_entry(sid, "attach", json.dumps({"session_id": sid}), db_path=journal_db)
    record_journal_entry(sid, "command", json.dumps({"command": "open", "arguments": {"url": "https://example.com"}}), db_path=journal_db)
    record_journal_entry(sid, "detach", json.dumps({"session_id": sid}), db_path=journal_db)
    
    entries = get_journal_entries(sid, db_path=journal_db)
    assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"
    # Verify order
    assert entries[0][1] == "attach"
    assert entries[1][1] == "command"
    assert entries[2][1] == "detach"


@pytest.mark.asyncio
async def test_journal_filter_by_session(journal_db):
    """Journal entries are filtered by session ID."""
    sid1 = "test_session_1"
    sid2 = "test_session_2"
    record_journal_entry(sid1, "attach", json.dumps({"session_id": sid1}), db_path=journal_db)
    record_journal_entry(sid2, "attach", json.dumps({"session_id": sid2}), db_path=journal_db)
    
    entries1 = get_journal_entries(sid1, db_path=journal_db)
    assert len(entries1) == 1
    assert entries1[0][2] == '{"session_id": "test_session_1"}'
    
    entries2 = get_journal_entries(sid2, db_path=journal_db)
    assert len(entries2) == 1
    assert entries2[0][2] == '{"session_id": "test_session_2"}'


@pytest.mark.asyncio
async def test_journal_all_entries(journal_db):
    """All journal entries can be retrieved."""
    sid1 = "test_session_1"
    sid2 = "test_session_2"
    record_journal_entry(sid1, "attach", json.dumps({"session_id": sid1}), db_path=journal_db)
    record_journal_entry(sid2, "attach", json.dumps({"session_id": sid2}), db_path=journal_db)
    
    entries = get_all_journal_entries(db_path=journal_db)
    assert len(entries) == 2
    assert entries[0][0] == sid1
    assert entries[1][0] == sid2


# --- Phase 3: CLI thin-client integration with daemon ---


@pytest.mark.asyncio
async def test_journal_recording_in_daemon(daemon_factory, tmp_path):
    """Journal entries are recorded when using the daemon."""
    # Use a separate journal DB for this test
    journal_db_path = str(tmp_path / "journal.sqlite")
    
    try:
        async with daemon_factory() as daemon:
            # Point the journal module at this test's DB (daemon now uses the default path)
            import webresearch.loom_journal as journal_mod
            journal_mod.DEFAULT_DB_PATH = journal_db_path
            
            # Attach to a session via the daemon protocol
            import asyncio
            reader, writer = await asyncio.open_unix_connection(daemon.socket_path)
            writer.write((json.dumps({"op": "attach", "session_id": "journal_test_session"}) + "\n").encode())
            await writer.drain()
            r = json.loads((await reader.readline()).decode())
            assert r["ok"], f"attach failed: {r}"
            sid = r["session_id"]
            
            # Send a command
            writer.write((json.dumps({"op": "command", "command": "status", "arguments": {}}) + "\n").encode())
            await writer.drain()
            r = json.loads((await reader.readline()).decode())
            assert r.get("ok", True), f"command failed: {r}"
            
            # Detach
            writer.write((json.dumps({"op": "detach"}) + "\n").encode())
            await writer.drain()
            r = json.loads((await reader.readline()).decode())
            assert r["ok"], f"detach failed: {r}"
            
            writer.close()
            await writer.wait_closed()
            
            # Small delay to ensure journal writes complete
            await asyncio.sleep(0.1)

            # The journal DB MUST exist and MUST contain the three events.
            import os as _os
            assert _os.path.exists(journal_db_path), "journal DB was never created"
            entries = get_journal_entries(sid, db_path=journal_db_path)
            assert len(entries) >= 3, f"Expected at least 3 journal entries, got {len(entries)}: {entries}"
            kinds = [e[1] for e in entries]
            assert "attach" in kinds, f"No 'attach' entry in kinds: {kinds}"
            assert "command" in kinds, f"No 'command' entry in kinds: {kinds}"
            assert "detach" in kinds, f"No 'detach' entry in kinds: {kinds}"
    finally:
        pass  # No cleanup needed


# --- Phase 3: CLI commands (unit tests) ---


@pytest.mark.asyncio
async def test_cli_attach_and_sessions_commands(daemon_factory):
    """CLI attach and sessions commands work correctly."""
    async with daemon_factory() as daemon:
        # Simulate CLI attach command
        import asyncio
        reader, writer = await asyncio.open_unix_connection(daemon.socket_path)
        
        # Attach
        writer.write((json.dumps({"op": "attach", "session_id": "cli_test_session"}) + "\n").encode())
        await writer.drain()
        r = json.loads((await reader.readline()).decode())
        assert r["ok"]
        assert r["session_id"] == "cli_test_session"
        
        # List sessions
        writer.write((json.dumps({"op": "list"}) + "\n").encode())
        await writer.drain()
        r = json.loads((await reader.readline()).decode())
        assert r["ok"]
        assert any(s["session_id"] == "cli_test_session" for s in r["sessions"])
        
        writer.close()
        await writer.wait_closed()
