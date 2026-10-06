"""Persistent Profile and Cookie Management.

Enables saving and loading cookies, headers, and authentication state to disk
so sessions survive process restarts and avoid bot challenges (e.g., Google
hostel Wi-Fi rate limits) by reusing trusted credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

import httpx

DEFAULT_PROFILES_DIR = Path(
    os.environ.get("WR_PROFILES_DIR", Path.home() / ".dosh-browser" / "profiles")
)


class Profile:
    """Manages persistent browser profile data (cookies and storage)."""

    def __init__(self, name: str = "default", base_dir: Path | str | None = None) -> None:
        self.name = re.sub(r"[^\w\.-]", "_", name)
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_PROFILES_DIR
        self.profile_dir = self.base_dir / self.name
        self.cookies_file = self.profile_dir / "cookies.json"
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def load_cookies(self) -> list[dict[str, Any]]:
        """Load cookies list from disk."""
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
        """Save a list of cookie dicts or an httpx.Cookies instance to disk."""
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
        temp_file = self.cookies_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        temp_file.replace(self.cookies_file)

    def apply_to_client(self, client: httpx.AsyncClient) -> int:
        """Apply stored cookies into an httpx.AsyncClient cookie jar."""
        cookies = self.load_cookies()
        from webresearch.engine.chromium import sync_cookies_to_client

        sync_cookies_to_client(cookies, client)
        return len(cookies)

    def save_from_client(self, client: httpx.AsyncClient) -> int:
        """Extract cookies from an httpx.AsyncClient and persist them."""
        from webresearch.engine.chromium import extract_cookies_for_chromium

        cookies = extract_cookies_for_chromium(client)
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
        """Clear all stored cookies in this profile."""
        if self.cookies_file.exists():
            self.cookies_file.unlink()


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
