"""Safe-Browsing list state with versioned add/remove updates.

Mirrors the wire protocol's shape: the client holds a versioned set of
hash prefixes; updates arrive as add/remove deltas with a SHA-256 checksum
over the canonical add-set. A checksum mismatch rejects the whole update and
the previous state keeps enforcing (fail-closed on state, never fail-open).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field


def _canonical_adds(adds: list[str]) -> bytes:
    return "\n".join(sorted(a.lower() for a in adds)).encode("utf-8")


def update_checksum(adds: list[str]) -> str:
    return hashlib.sha256(_canonical_adds(adds)).hexdigest()


@dataclass
class PrefixListState:
    name: str
    version: int = 0
    prefixes: set[bytes] = field(default_factory=set)

    def apply_update(self, update: dict) -> tuple[bool, str]:
        try:
            version = int(update.get("version", -1))
            adds = [str(a) for a in (update.get("add") or [])]
            removes = [str(r) for r in (update.get("remove") or [])]
            checksum = str(update.get("checksum") or "")
        except Exception:
            return False, "malformed update"
        if version <= self.version:
            return False, f"stale version {version} <= {self.version}"
        add_bytes: set[bytes] = set()
        for a in adds:
            try:
                add_bytes.add(bytes.fromhex(a.strip().lower()))
            except ValueError:
                return False, f"bad prefix hex: {a[:16]}"
        if checksum != update_checksum(adds):
            return False, "checksum mismatch: update rejected, previous state kept"
        remove_bytes: set[bytes] = set()
        for r in removes:
            try:
                remove_bytes.add(bytes.fromhex(r.strip().lower()))
            except ValueError:
                return False, f"bad prefix hex: {r[:16]}"
        self.prefixes |= add_bytes
        self.prefixes -= remove_bytes
        self.version = version
        return True, f"applied v{version}: +{len(add_bytes)} -{len(remove_bytes)}"


class ListStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.environ.get("WEBRESEARCH_SB_STATE_FILE", "")
        self.lists: dict[str, PrefixListState] = {}
        self._load()

    def get(self, name: str) -> PrefixListState:
        return self.lists.setdefault(name, PrefixListState(name))

    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            for name, item in (data.get("lists") or {}).items():
                state = PrefixListState(str(name), int(item.get("version", 0)))
                for p in item.get("prefixes", []):
                    try:
                        state.prefixes.add(bytes.fromhex(str(p)))
                    except ValueError:
                        continue
                self.lists[state.name] = state
        except Exception:
            pass

    def save(self) -> None:
        if not self.path:
            return
        try:
            data = {"lists": {
                name: {"version": st.version, "prefixes": sorted(p.hex() for p in st.prefixes)}
                for name, st in self.lists.items()
            }}
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def apply_update(self, name: str, update: dict) -> tuple[bool, str]:
        ok, msg = self.get(name).apply_update(update)
        if ok:
            self.save()
        return ok, msg


STORE = ListStore()
