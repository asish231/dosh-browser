"""Persistent Profile and Cookie Management.

Enables saving and loading cookies, headers, and authentication state to disk
so sessions survive process restarts and avoid bot challenges (e.g., Google
hostel Wi-Fi rate limits) by reusing trusted credentials.
"""

from __future__ import annotations

import contextlib
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
                        created_at REAL NOT NULL,
                        PRIMARY KEY(name, domain, path)
                    )
                    """
                )
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
        """Load cookies list from SQLite database, with JSON fallback."""
        if self.db_file.exists():
            try:
                with sqlite3.connect(self.db_file) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT name, value, domain, path, secure, expires FROM cookies")
                    rows = cur.fetchall()
                    if rows:
                        return [
                            {
                                "name": r[0],
                                "value": r[1],
                                "domain": r[2],
                                "path": r[3],
                                "secure": bool(r[4]),
                                "expires": r[5],
                            }
                            for r in rows
                        ]
            except Exception:
                pass

        if not self.cookies_file.exists():
            return []
        try:
            with open(self.cookies_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def save_cookies(self, cookies: list[dict[str, Any]] | httpx.Cookies | Any) -> None:
        """Save a list of cookie dicts or an httpx.Cookies instance to SQLite and disk."""
        out: list[dict[str, Any]] = []
        if isinstance(cookies, list):
            out = cookies
        elif hasattr(cookies, "jar"):
            for c in cookies.jar:
                out.append(
                    {
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain or "",
                        "path": c.path or "/",
                        "secure": bool(c.secure),
                        "expires": c.expires if hasattr(c, "expires") else None,
                    }
                )
        self._ensure_dir()
        # 1. Sync to SQLite database
        now = time.time()
        try:
            with sqlite3.connect(self.db_file) as conn:
                for c in out:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO cookies (name, value, domain, path, secure, expires, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c.get("name", ""),
                            c.get("value", ""),
                            c.get("domain", ""),
                            c.get("path", "/"),
                            1 if c.get("secure") else 0,
                            c.get("expires"),
                            now,
                        ),
                    )
                conn.commit()
        except Exception:
            pass

        # 2. Sync to JSON file
        temp_file = self.cookies_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        temp_file.replace(self.cookies_file)

    def apply_to_client(self, client: httpx.AsyncClient) -> int:
        """Apply stored cookies into an httpx.AsyncClient cookie jar."""
        cookies = self.load_cookies()
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            domain = (c.get("domain") or "").lstrip(".")
            path = c.get("path") or "/"
            if name and value:
                with contextlib.suppress(Exception):
                    client.cookies.set(name, value, domain=domain or None, path=path)
        return len(cookies)

    def save_from_client(self, client: httpx.AsyncClient) -> int:
        """Extract cookies from an httpx.AsyncClient and persist them."""
        out: list[dict[str, Any]] = []
        if hasattr(client, "cookies") and hasattr(client.cookies, "jar"):
            for cookie in client.cookies.jar:
                out.append(
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain or "",
                        "path": cookie.path or "/",
                        "secure": bool(cookie.secure),
                        "expires": cookie.expires if hasattr(cookie, "expires") else None,
                    }
                )
        self.save_cookies(out)
        return len(out)

    def get_domain_cookies(self, domain: str) -> list[dict[str, Any]]:
        """Return all stored cookies matching the given host/domain."""
        d = domain.lower().lstrip(".")
        matched = []
        for c in self.load_cookies():
            cd = (c.get("domain") or "").lower().lstrip(".")
            if not cd or cd == d or d.endswith("." + cd) or cd.endswith("." + d):
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
