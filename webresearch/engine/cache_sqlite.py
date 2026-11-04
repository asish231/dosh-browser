"""cache_sqlite.py — Persistent SQLite RFC 9111 HTTP Cache for Dosh-Browser.

Provides durable cross-session HTTP caching using SQLite, supporting
freshness validation, ETags, Last-Modified, 304 Not Modified renewal,
Vary matching, and script bytecode caching.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from enum import Enum
import hashlib
import json
import sqlite3
import time
from typing import Any


class CacheGetStatus(str, Enum):
    HIT = "hit"
    REVALIDATE = "revalidate"
    STALE = "stale"
    MISS = "miss"


def parse_http_date(date_str: str | None) -> float | None:
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.timestamp()
    except Exception:
        return None


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
                    last_modified TEXT,
                    vary_headers TEXT
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_http_cache_stored ON http_cache(stored_at);"
            )
            # Migration check for existing databases without vary_headers
            cur = self._conn.execute("PRAGMA table_info(http_cache);")
            columns = {row[1] for row in cur.fetchall()}
            if "vary_headers" not in columns:
                try:
                    self._conn.execute("ALTER TABLE http_cache ADD COLUMN vary_headers TEXT;")
                except sqlite3.OperationalError:
                    pass

            # Script bytecode/prepared code cache
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS script_cache (
                    url TEXT PRIMARY KEY,
                    bytecode BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    stored_at REAL NOT NULL
                );
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_script_cache_stored ON script_cache(stored_at);"
            )

    def get(
        self,
        url: str,
        req_headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT url, status, headers_json, body, stored_at, max_age, etag, last_modified, vary_headers
            FROM http_cache WHERE url = ?
            """,
            (url,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        u, status, headers_raw, body, stored_at, max_age, etag, last_modified, vary_raw = row
        headers = json.loads(headers_raw)

        # Vary matching per RFC 9111 §4.1
        if vary_raw and req_headers is not None:
            try:
                vary_map = json.loads(vary_raw)
                lowered_req = {k.lower(): v for k, v in req_headers.items()}
                for v_name, v_val in vary_map.items():
                    current_val = lowered_req.get(v_name.lower(), "")
                    if current_val != v_val:
                        return None
            except Exception:
                pass

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

    def get_result(
        self,
        url: str,
        req_headers: dict[str, str] | None = None,
        now: float | None = None,
    ) -> tuple[CacheGetStatus, dict[str, Any] | None]:
        entry = self.get(url, req_headers=req_headers)
        if entry is None:
            return CacheGetStatus.MISS, None

        t = now if now is not None else time.time()
        age = max(0.0, t - entry["stored_at"])
        max_age = entry["max_age"]

        if max_age > 0 and age < max_age:
            return CacheGetStatus.HIT, entry

        if entry.get("etag") or entry.get("last_modified"):
            return CacheGetStatus.REVALIDATE, entry

        return CacheGetStatus.STALE, entry

    def store(
        self,
        url: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
        req_headers: dict[str, str] | None = None,
    ) -> None:
        cc_raw = headers.get("cache-control", "").lower()
        if "no-store" in cc_raw:
            return

        # Check status cacheability per RFC 9111
        if status not in {200, 203, 204, 300, 301, 308, 404, 405, 414, 501}:
            return

        max_age = 0.0
        has_max_age = False
        for part in cc_raw.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = float(part.split("=", 1)[1].strip())
                    has_max_age = True
                except ValueError:
                    pass

        # Heuristic freshness (RFC 9111 §4.2.2): 10% of (Date - Last-Modified)
        etag = headers.get("etag")
        last_modified = headers.get("last-modified")
        if not has_max_age and last_modified:
            date_val = parse_http_date(headers.get("date")) or time.time()
            lm_val = parse_http_date(last_modified)
            if lm_val and date_val > lm_val:
                max_age = min(86400.0, (date_val - lm_val) * 0.10)

        # Vary headers computation
        vary_json = None
        vary_raw = headers.get("vary")
        if vary_raw and req_headers is not None:
            vary_names = [v.strip().lower() for v in vary_raw.split(",") if v.strip()]
            if "*" in vary_names:
                return  # Vary: * cannot be cached
            lowered_req = {k.lower(): v for k, v in req_headers.items()}
            vary_map = {name: lowered_req.get(name, "") for name in vary_names}
            vary_json = json.dumps(vary_map)

        headers_json = json.dumps(headers)
        stored_at = time.time()

        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO http_cache
                (url, status, headers_json, body, stored_at, max_age, etag, last_modified, vary_headers)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (url, status, headers_json, body, stored_at, max_age, etag, last_modified, vary_json),
            )

    def renew(self, url: str, status: int, headers: dict[str, str]) -> bool:
        """304 Not Modified header renewal without rewriting body."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT headers_json, body, stored_at, vary_headers FROM http_cache WHERE url = ?", (url,))
        row = cursor.fetchone()
        if not row:
            return False

        old_headers_raw, body, _, old_vary = row
        old_headers: dict[str, str] = json.loads(old_headers_raw)

        # RFC 9111 §4.3.4: Update headers preserving un-updated representation headers
        skip_headers = {"content-length", "content-encoding", "transfer-encoding"}
        for k, v in headers.items():
            if k.lower() not in skip_headers:
                old_headers[k] = v

        cc_raw = old_headers.get("cache-control", "").lower()
        max_age = 0.0
        for part in cc_raw.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = float(part.split("=", 1)[1].strip())
                except ValueError:
                    pass

        etag = old_headers.get("etag")
        last_modified = old_headers.get("last-modified")
        headers_json = json.dumps(old_headers)
        stored_at = time.time()

        with self._conn:
            self._conn.execute(
                """
                UPDATE http_cache
                SET headers_json = ?, stored_at = ?, max_age = ?, etag = ?, last_modified = ?
                WHERE url = ?
                """,
                (headers_json, stored_at, max_age, etag, last_modified, url),
            )
        return True

    def get_script_cache(self, url: str, sha256: str | None = None) -> bytes | None:
        cursor = self._conn.cursor()
        if sha256:
            cursor.execute("SELECT bytecode FROM script_cache WHERE url = ? AND sha256 = ?", (url, sha256))
        else:
            cursor.execute("SELECT bytecode FROM script_cache WHERE url = ?", (url,))
        row = cursor.fetchone()
        return row[0] if row else None

    def put_script_cache(self, url: str, bytecode: bytes, sha256: str) -> None:
        stored_at = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO script_cache (url, bytecode, sha256, stored_at)
                VALUES (?, ?, ?, ?)
                """,
                (url, bytecode, sha256, stored_at),
            )

    def invalidate(self, url: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM http_cache WHERE url = ?", (url,))
            self._conn.execute("DELETE FROM script_cache WHERE url = ?", (url,))

    def sweep_expired(self) -> int:
        now = time.time()
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM http_cache WHERE (stored_at + max_age) < ?",
                (now,),
            )
            return cur.rowcount
