"""Persistent Profile and Cookie Management.

Enables saving and loading cookies, headers, and authentication state to disk
so sessions survive process restarts and avoid bot challenges (e.g., Google
hostel Wi-Fi rate limits) by reusing trusted credentials.
"""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

import httpx

DEFAULT_PROFILES_DIR = Path(
    os.environ.get("WR_PROFILES_DIR", Path.home() / ".dosh-browser" / "profiles")
)

WAF_TOKEN_NAMES = {
    "cf_clearance",
    "__cf_bm",
    "cf_chl_2",
    "cf_chl_rc_m",
    "datadome",
    "_abck",
    "bm_sz",
    "bm_sv",
    "ak_bmsc",
    "incap_ses",
    "visid_incap",
    "nlbi",
    "_px3",
    "_pxhd",
    "_pxde",
    "aws-waf-token",
}


def _cookie_to_dict(c: Any) -> dict[str, Any]:
    """Convert an http.cookiejar.Cookie or dictionary into a normalized cookie dict."""
    if isinstance(c, dict):
        return {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", "") or "",
            "path": c.get("path", "/") or "/",
            "secure": bool(c.get("secure")),
            "expires": c.get("expires"),
            "http_only": bool(c.get("http_only")),
            "same_site": c.get("same_site"),
            "host_only": bool(c.get("host_only")),
            "partition_key": c.get("partition_key"),
        }
    rest_lower = {str(k).lower(): v for k, v in getattr(c, "_rest", {}).items()}
    http_only = "httponly" in rest_lower
    same_site = rest_lower.get("samesite")
    if same_site is not None:
        same_site = str(same_site)
    partition_key = rest_lower.get("partitionkey")
    if not partition_key and "partitioned" in rest_lower:
        partition_key = "true"
    host_only = not getattr(c, "domain_specified", True)
    return {
        "name": getattr(c, "name", ""),
        "value": getattr(c, "value", ""),
        "domain": getattr(c, "domain", "") or "",
        "path": getattr(c, "path", "/") or "/",
        "secure": bool(getattr(c, "secure", False)),
        "expires": getattr(c, "expires", None),
        "http_only": http_only,
        "same_site": same_site,
        "host_only": host_only,
        "partition_key": partition_key,
    }


class Profile:
    """Manages persistent browser profile data (cookies and storage) via SQLite and JSON."""

    def __init__(self, name: str = "default", base_dir: Path | str | None = None) -> None:
        self.name = re.sub(r"[^\w\.-]", "_", name)
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_PROFILES_DIR
        self.profile_dir = self.base_dir / self.name
        self.cookies_file = self.profile_dir / "cookies.json"
        self.storage_file = self.profile_dir / "storage.json"
        self.db_file = self.profile_dir / "profile.sqlite"
        self._ensure_dir()
        self._init_sqlite()

    def _ensure_dir(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def _init_sqlite(self) -> None:
        """Initialize the SQLite schema for cookies and session storage."""
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cookies (
                        name TEXT NOT NULL,
                        value TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        path TEXT NOT NULL DEFAULT '/',
                        secure INTEGER NOT NULL DEFAULT 0,
                        expires REAL,
                        http_only INTEGER NOT NULL DEFAULT 0,
                        same_site TEXT,
                        host_only INTEGER NOT NULL DEFAULT 0,
                        partition_key TEXT,
                        created_at REAL NOT NULL,
                        PRIMARY KEY(name, domain, path)
                    )
                    """
                )
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(cookies)")
                existing_cols = {row[1].lower() for row in cur.fetchall()}
                for col_name, col_type in [
                    ("http_only", "INTEGER NOT NULL DEFAULT 0"),
                    ("same_site", "TEXT"),
                    ("host_only", "INTEGER NOT NULL DEFAULT 0"),
                    ("partition_key", "TEXT"),
                ]:
                    if col_name not in existing_cols:
                        conn.execute(f"ALTER TABLE cookies ADD COLUMN {col_name} {col_type}")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS storage (
                        origin TEXT NOT NULL,
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY(origin, key)
                    )
                    """
                )
                conn.commit()
        except Exception:
            pass

    def load_cookies(self) -> list[dict[str, Any]]:
        """Load cookies list from SQLite database, with JSON fallback.
        
        Filters expired entries and maintains strict synchronization with disk.
        """
        now = time.time()
        if self.db_file.exists():
            try:
                with sqlite3.connect(self.db_file) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT name, value, domain, path, secure, expires,
                               http_only, same_site, host_only, partition_key
                        FROM cookies
                        """
                    )
                    rows = cur.fetchall()
                    valid_cookies = []
                    expired_names = []
                    for r in rows:
                        exp = r[5]
                        if exp is not None and isinstance(exp, (int, float)) and exp > 0 and exp < now:
                            expired_names.append((r[0], r[2], r[3]))
                            continue
                        valid_cookies.append(
                            {
                                "name": r[0],
                                "value": r[1],
                                "domain": r[2],
                                "path": r[3],
                                "secure": bool(r[4]),
                                "expires": exp,
                                "http_only": bool(r[6]),
                                "same_site": r[7],
                                "host_only": bool(r[8]),
                                "partition_key": r[9],
                            }
                        )
                    if expired_names:
                        for n, d, p in expired_names:
                            conn.execute(
                                "DELETE FROM cookies WHERE name = ? AND domain = ? AND path = ?",
                                (n, d, p),
                            )
                        conn.commit()
                    return valid_cookies
            except Exception:
                pass

        if not self.cookies_file.exists():
            return []
        try:
            with open(self.cookies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    res = []
                    for c in data:
                        exp = c.get("expires")
                        if exp is not None and isinstance(exp, (int, float)) and exp > 0 and exp < now:
                            continue
                        res.append(_cookie_to_dict(c))
                    return res
        except Exception:
            pass
        return []

    def save_cookies(self, cookies: list[dict[str, Any]] | httpx.Cookies | Any) -> None:
        """Save a list of cookie dicts or an httpx.Cookies instance to SQLite and disk.
        
        Synchronizes deletions: cookies absent in input are removed from SQLite.
        """
        out: list[dict[str, Any]] = []
        if isinstance(cookies, list):
            out = [_cookie_to_dict(c) for c in cookies]
        elif hasattr(cookies, "jar"):
            for c in cookies.jar:
                out.append(_cookie_to_dict(c))
        elif hasattr(cookies, "__iter__"):
            for c in cookies:
                out.append(_cookie_to_dict(c))

        now = time.time()
        active_cookies = [
            c for c in out
            if not (
                c.get("expires")
                and isinstance(c.get("expires"), (int, float))
                and c["expires"] > 0
                and c["expires"] < now
            )
        ]

        self._ensure_dir()
        # 1. Sync to SQLite database atomically: wipe old active rows so deletions persist
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.execute("DELETE FROM cookies")
                for c in active_cookies:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO cookies (
                            name, value, domain, path, secure, expires,
                            http_only, same_site, host_only, partition_key, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c.get("name", ""),
                            c.get("value", ""),
                            c.get("domain", ""),
                            c.get("path", "/"),
                            1 if c.get("secure") else 0,
                            c.get("expires"),
                            1 if c.get("http_only") else 0,
                            c.get("same_site"),
                            1 if c.get("host_only") else 0,
                            c.get("partition_key"),
                            now,
                        ),
                    )
                conn.commit()
        except Exception:
            pass

        # 2. Sync to JSON file
        temp_file = self.cookies_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(active_cookies, f, indent=2)
        temp_file.replace(self.cookies_file)

    def delete_cookie(self, name: str, domain: str = "", path: str = "/") -> bool:
        """Explicitly delete a cookie by name, domain, and path."""
        cookies = self.load_cookies()
        d = domain.lower().lstrip(".")
        p = path or "/"
        new_cookies = [
            c for c in cookies
            if not (
                c.get("name") == name
                and (not d or (c.get("domain") or "").lower().lstrip(".") == d)
                and (p == "/" or c.get("path") == p)
            )
        ]
        self.save_cookies(new_cookies)
        return len(new_cookies) < len(cookies)

    def apply_to_client(self, client: httpx.AsyncClient) -> int:
        """Apply stored cookies into an httpx.AsyncClient cookie jar with full security attributes."""
        cookies = self.load_cookies()
        restored = 0
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue

            rest: dict[str, Any] = {}
            if c.get("http_only"):
                rest["HttpOnly"] = None
            if c.get("same_site"):
                rest["SameSite"] = str(c.get("same_site"))
            if c.get("partition_key"):
                rest["Partitioned"] = None
                rest["PartitionKey"] = str(c.get("partition_key"))

            domain = c.get("domain") or ""
            host_only = bool(c.get("host_only"))
            domain_specified = bool(domain) and not host_only
            domain_initial_dot = domain.startswith(".")
            path = c.get("path") or "/"

            try:
                cookie_obj = http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=str(value),
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=domain_specified,
                    domain_initial_dot=domain_initial_dot,
                    path=path,
                    path_specified=bool(c.get("path")),
                    secure=bool(c.get("secure")),
                    expires=c.get("expires"),
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest=rest,
                    rfc2109=False,
                )
                client.cookies.jar.set_cookie(cookie_obj)
                restored += 1
            except Exception:
                with contextlib.suppress(Exception):
                    client.cookies.set(name, str(value), domain=domain.lstrip(".") or None, path=path)
                    restored += 1
        return restored

    def save_from_client(self, client: httpx.AsyncClient) -> int:
        """Extract cookies from an httpx.AsyncClient and persist them."""
        out: list[dict[str, Any]] = []
        if hasattr(client, "cookies") and hasattr(client.cookies, "jar"):
            for cookie in client.cookies.jar:
                out.append(_cookie_to_dict(cookie))
        self.save_cookies(out)
        return len(out)

    def get_domain_cookies(self, domain: str, secure: bool | None = None) -> list[dict[str, Any]]:
        """Return stored cookies matching the given host/domain without subdomain leakage."""
        d = domain.lower().lstrip(".")
        now = time.time()
        matched = []
        for c in self.load_cookies():
            exp = c.get("expires")
            if exp is not None and isinstance(exp, (int, float)) and exp > 0 and exp < now:
                continue
            if secure is False and c.get("secure"):
                continue

            cd = (c.get("domain") or "").lower().lstrip(".")
            is_host_only = bool(c.get("host_only"))

            if is_host_only or not cd:
                if cd == d:
                    matched.append(c)
            else:
                # Domain-scoped: matches exact domain or subdomains of cd.
                # Subdomain cookies never leak to parent domain.
                if cd == d or d.endswith("." + cd):
                    matched.append(c)
        return matched

    def get_waf_tokens(self, domain: str) -> dict[str, str]:
        """Detect and return anti-bot / WAF clearance tokens for domain."""
        tokens: dict[str, str] = {}
        for c in self.get_domain_cookies(domain):
            name = c.get("name", "")
            val = c.get("value", "")
            if not name or not val:
                continue
            low = name.lower()
            if any(low == marker or low.startswith(marker + "_") for marker in WAF_TOKEN_NAMES):
                tokens[name] = val
        return tokens

    def is_warmed(self, domain: str) -> bool:
        """Check if profile contains unexpired anti-bot clearance for domain."""
        waf = self.get_waf_tokens(domain)
        if not waf:
            return False
        now = time.time()
        for c in self.get_domain_cookies(domain):
            if c.get("name") in waf:
                exp = c.get("expires")
                if exp is not None and isinstance(exp, (int, float)) and exp > 0 and exp < now:
                    continue
                return True
        return False

    def load_storage(self, origin: str) -> dict[str, str]:
        """Load persistent localStorage key-value pairs for origin from SQLite or JSON."""
        if self.db_file.exists():
            try:
                with sqlite3.connect(self.db_file) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT key, value FROM storage WHERE origin = ?", (origin,))
                    rows = cur.fetchall()
                    if rows:
                        return {r[0]: r[1] for r in rows}
            except Exception:
                pass

        if not self.storage_file.exists():
            return {}
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data.get(origin, {})
        except Exception:
            pass
        return {}

    def save_storage(self, origin: str, key: str, value: str) -> None:
        """Save a key-value pair to origin localStorage in SQLite and JSON."""
        self._ensure_dir()
        now = time.time()
        # 1. Sync to SQLite
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO storage (origin, key, value, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (origin, key, str(value), now),
                )
                conn.commit()
        except Exception:
            pass

        # 2. Sync to JSON file
        data: dict[str, dict[str, str]] = {}
        if self.storage_file.exists():
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                pass
        data.setdefault(origin, {})[key] = str(value)
        temp_file = self.storage_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(self.storage_file)

    def delete_storage(self, origin: str, key: str) -> None:
        """Remove one key of origin localStorage from SQLite and JSON."""
        self._ensure_dir()
        try:
            with sqlite3.connect(self.db_file) as conn:
                conn.execute("DELETE FROM storage WHERE origin = ? AND key = ?", (origin, key))
                conn.commit()
        except Exception:
            pass
        if not self.storage_file.exists():
            return
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get(origin), dict):
                data[origin].pop(key, None)
                temp_file = self.storage_file.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                temp_file.replace(self.storage_file)
        except Exception:
            pass

    def import_browser_sqlite(self, sqlite_path: str | Path) -> int:
        """Import cookies from external browser SQLite database (Firefox or Chrome/Chromium)."""
        p = Path(sqlite_path)
        if not p.exists():
            return 0
        cookies: list[dict[str, Any]] = []
        try:
            with sqlite3.connect(p) as conn:
                cur = conn.cursor()
                # Check available tables
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = {r[0].lower() for r in cur.fetchall()}

                if "moz_cookies" in tables:
                    # Firefox format
                    cur.execute("SELECT name, value, host, path, isSecure, expiry FROM moz_cookies")
                    for name, val, host, path, sec, exp in cur.fetchall():
                        if name and val:
                            cookies.append({
                                "name": name,
                                "value": val,
                                "domain": host.lstrip("."),
                                "path": path or "/",
                                "secure": bool(sec),
                                "expires": exp,
                            })
                elif "cookies" in tables:
                    # Chrome / Chromium format
                    cur.execute("PRAGMA table_info(cookies)")
                    col_names = [col[1].lower() for col in cur.fetchall()]
                    host_col = "host_key" if "host_key" in col_names else "domain"
                    sec_col = "is_secure" if "is_secure" in col_names else "secure"
                    exp_col = "expires_utc" if "expires_utc" in col_names else "expires"
                    query = f"SELECT name, value, {host_col}, path, {sec_col}, {exp_col} FROM cookies"
                    cur.execute(query)
                    for name, val, host, path, sec, exp in cur.fetchall():
                        if name and val:
                            # Convert Chrome Windows Epoch (microseconds since 1601) if applicable
                            exp_ts = None
                            if exp and exp > 11644473600000000:
                                exp_ts = (exp - 11644473600000000) / 1000000.0
                            elif exp:
                                exp_ts = exp
                            cookies.append({
                                "name": name,
                                "value": val,
                                "domain": (host or "").lstrip("."),
                                "path": path or "/",
                                "secure": bool(sec),
                                "expires": exp_ts,
                            })
        except Exception:
            return 0

        if cookies:
            self.save_cookies(cookies)
        return len(cookies)

    def import_netscape(self, content: str) -> int:
        """Import cookies in Netscape format (standard 'cookies.txt' export)."""
        cookies: list[dict[str, Any]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                domain, flag, path, secure, expires, name, value = parts[:7]
                cookies.append(
                    {
                        "domain": domain.lstrip("."),
                        "path": path,
                        "secure": secure.upper() == "TRUE",
                        "expires": int(expires) if expires.isdigit() else None,
                        "name": name,
                        "value": value,
                    }
                )
        if cookies:
            self.save_cookies(cookies)
        return len(cookies)

    def export_netscape(self) -> str:
        """Export cookies in Netscape format."""
        cookies = self.load_cookies()
        lines = [
            "# Netscape HTTP Cookie File",
            "# http://curl.haxx.se/rfc/cookie_spec.html",
            "# This is a generated file! Do not edit.",
            "",
        ]
        for c in cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(c.get("expires") or 0)
            name = c.get("name", "")
            value = c.get("value", "")
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        """Clear all stored cookies and storage in this profile."""
        if self.cookies_file.exists():
            self.cookies_file.unlink()
        if self.storage_file.exists():
            self.storage_file.unlink()
        if self.db_file.exists():
            try:
                with sqlite3.connect(self.db_file) as conn:
                    conn.execute("DELETE FROM cookies")
                    conn.execute("DELETE FROM storage")
                    conn.commit()
            except Exception:
                pass


def get_profile(name: str = "default") -> Profile:
    return Profile(name=name)


def list_profiles() -> list[str]:
    """List available profile names."""
    if not DEFAULT_PROFILES_DIR.exists():
        return []
    return [
        d.name
        for d in DEFAULT_PROFILES_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]
