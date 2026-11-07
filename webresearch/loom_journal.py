import os
import sqlite3
from datetime import datetime

DEFAULT_DB_PATH = os.path.expanduser('~/.dosh/loom.db')

def _get_db_path(db_path=None):
    """Resolve the DB path at call time (not import time)."""
    if db_path is not None:
        return db_path
    return DEFAULT_DB_PATH


def _ensure_table(conn):
    """Auto-initialize the sessions_journal table if it doesn't exist."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL
        )
    ''')
    conn.commit()


def record_journal_entry(session_id, kind, payload, db_path=None):
    """Record a journal entry."""
    path = _get_db_path(db_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    _ensure_table(conn)
    c = conn.cursor()
    c.execute('''
        INSERT INTO sessions_journal (session_id, timestamp, kind, payload)
        VALUES (?, ?, ?, ?)
    ''', (session_id, datetime.now().timestamp(), kind, payload))
    conn.commit()
    conn.close()


def get_journal_entries(session_id, db_path=None):
    """Retrieve all journal entries for a specific session."""
    path = _get_db_path(db_path)
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    _ensure_table(conn)
    c = conn.cursor()
    c.execute('''
        SELECT timestamp, kind, payload
        FROM sessions_journal
        WHERE session_id = ?
        ORDER BY timestamp ASC
    ''', (session_id,))
    entries = c.fetchall()
    conn.close()
    return entries


def get_all_journal_entries(db_path=None):
    """Retrieve all journal entries."""
    path = _get_db_path(db_path)
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    _ensure_table(conn)
    c = conn.cursor()
    c.execute('''
        SELECT session_id, timestamp, kind, payload
        FROM sessions_journal
        ORDER BY timestamp ASC
    ''')
    entries = c.fetchall()
    conn.close()
    return entries
