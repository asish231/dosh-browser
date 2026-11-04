"""cache_sqlite.py — Persistent SQLite RFC 9111 HTTP Cache for Dosh-Browser.

Provides durable cross-session HTTP caching using SQLite, supporting
freshness validation, ETags, Last-Modified, and 304 Not Modified reuse.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class SqliteHttpCache:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    url TEXT PRIMARY KEY,
                    status INTEGER NOT NULL,
                    headers_json TEXT NOT NULL,
                    body BLOB NOT NULL,
                    stored_at REAL NOT NULL,
                    max_age REAL NOT NULL,
                    etag TEXT,
                    last_modified TEXT
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_http_cache_stored ON http_cache(stored_at);"
            )

    def get(self, url: str) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT url, status, headers_json, body, stored_at, max_age, etag, last_modified FROM http_cache WHERE url = ?",
            (url,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        u, status, headers_raw, body, stored_at, max_age, etag, last_modified = row
        headers = json.loads(headers_raw)
        now = time.time()
        is_fresh = (now - stored_at) < max_age if max_age > 0 else False

        return {
            "url": u,
            "status": status,
            "headers": headers,
            "body": body,
            "stored_at": stored_at,
            "max_age": max_age,
            "etag": etag,
            "last_modified": last_modified,
            "is_fresh": is_fresh,
        }

    def store(
        self,
        url: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        cc_raw = headers.get("cache-control", "").lower()
        if "no-store" in cc_raw:
            return

        max_age = 0.0
        for part in cc_raw.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = float(part.split("=", 1)[1].strip())
                except ValueError:
                    pass

        etag = headers.get("etag")
        last_modified = headers.get("last-modified")
        headers_json = json.dumps(headers)
        stored_at = time.time()

        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO http_cache
                (url, status, headers_json, body, stored_at, max_age, etag, last_modified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (url, status, headers_json, body, stored_at, max_age, etag, last_modified),
            )

    def invalidate(self, url: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM http_cache WHERE url = ?", (url,))

    def sweep_expired(self) -> int:
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM http_cache WHERE (stored_at + max_age) < ?",
                (now,),
            )
            return cur.rowcount

