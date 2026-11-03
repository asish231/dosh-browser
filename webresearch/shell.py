"""Agent command surface over owned browser sessions, not an operating-system shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from quickjs import Context

from webresearch.telemetry import NetworkLedger, _redact, _url

if TYPE_CHECKING:
    from webresearch.session import Session

COMMANDS = {
    "help": "List commands and capability limits.",
    "status": "Current session, tab, document, and capture status.",
    "open": "Load url through the normal owned document lifecycle.",
    "actions": "List controls with explicit pagination coverage.",
    "act": "Execute an existing browser action ref, with optional text/dx/dy.",
    "net": "Query network observations: after, limit, pattern.",
    "net.capture": "Enable/disable opt-in redacted JSON/form response bodies: enabled.",
    "net.rule": "Add runtime fetch/XHR block/header/mock rule: action, pattern, options.",
    "net.unrule": "Remove network rule by id.",
    "console": "Query captured page logs/errors: after, limit, level.",
    "eval": "Run bounded control-realm JavaScript: source, args, allow_actions=false.",
    "skill.save": "Save source, name, origins, allow_actions=false to the workspace registry.",
    "skill.list": "List saved skills without executing them.",
    "skill.run": "Run name on an allowed origin; mutations require allow_actions=true again.",
    "skill.delete": "Delete a saved skill by name.",
    "db": "Read-only SQL over skills and snapshots: sql, params, limit.",
    "storage": "Inspect page storage metadata (values=false by default).",
    "snapshot": "Save a redacted document/control evidence snapshot; not a VM checkpoint.",
    "diff": "Compare evidence snapshots: before, after.",
    "layers": "Inspect approximate layout strata; not authoritative hit testing.",
    "frames": "Inspect loaded frame realms and message counts, without message payloads.",
    "tabs": "List tabs or switch using id.",
    "video": "Download a video from a legitimate platform: url, dest_dir, format, use_session.",
}

_SDK = """
'use strict';
var __out = Object.seal({emits: [], actions: []});
var dosh = Object.freeze({
  args: Object.freeze(__input.args),
  text: function () { return __input.text; },
  actions: function () { return JSON.parse(JSON.stringify(__input.actions)); },
  find: function (label) {
    return __input.actions.filter(function (a) { return a.label === String(label); });
  },
  emit: function (key, value) {
    if (__out.emits.length >= 100) throw new Error('emit budget exceeded');
    __out.emits.push({key: String(key), value: value});
  },
  act: function (ref, options) {
    if (!__input.allow_actions) throw new Error('action permission required');
    if (__out.actions.length >= 20) throw new Error('action budget exceeded');
    __out.actions.push({ref: String(ref), options: options || {}});
  }
});
Object.freeze(__input);
"""


class ShellStore:
    """Workspace evidence and skills. Never writes into a browser profile DB."""

    def __init__(self, path: Path | str | None = None):
        self.conn = sqlite3.connect(str(path) if path is not None else ":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4_000_000)
        self.conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
        self.persistent = path is not None
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY, source TEXT NOT NULL, sha256 TEXT NOT NULL,
                origins TEXT NOT NULL, allow_actions INTEGER NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, tab_id TEXT NOT NULL,
                url TEXT NOT NULL, evidence TEXT NOT NULL
            );
        """)

    def save_skill(self, name: str, source: str, origins: list[str], allow_actions: bool) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", name):
            raise ValueError("skill name must be 1-64 letters, digits, dots, underscores or hyphens")
        if not isinstance(source, str) or not source or len(source.encode()) > 65536:
            raise ValueError("source must contain 1-65536 bytes")
        if not isinstance(allow_actions, bool):
            raise ValueError("allow_actions must be boolean")
        if not isinstance(origins, list) or not origins or not all(isinstance(o, str) and _origin(o) == o for o in origins):
            raise ValueError("origins must be explicit HTTP(S) origins without paths or wildcards")
        digest = hashlib.sha256(source.encode()).hexdigest()
        with self.conn:
            self.conn.execute("""
                INSERT INTO skills VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(name) DO UPDATE SET source=excluded.source, sha256=excluded.sha256,
                origins=excluded.origins, allow_actions=excluded.allow_actions, version=skills.version+1
            """, (name, source, digest, json.dumps(origins), int(allow_actions)))
        return {k: v for k, v in self.skill(name).items() if k != "source"}

    def skill(self, name: str) -> dict:
        row = self.conn.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"unknown skill: {name}")
        return dict(row)

    def query(self, sql: str, params: list, limit: int) -> dict:
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

        def authorize(code, arg1, arg2, _db, _trigger):
            if code == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {"load_extension", "writefile", "readfile"}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if code in allowed else sqlite3.SQLITE_DENY

        ticks = 0

        def budget():
            nonlocal ticks
            ticks += 1
            return ticks > 1000

        self.conn.set_authorizer(authorize)
        self.conn.set_progress_handler(budget, 1000)
        try:
            cursor = self.conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchmany(limit + 1)]
            return {"rows": rows[:limit], "returned": min(len(rows), limit), "has_more": len(rows) > limit,
                    "read_only": True, "note": "SQL is a workspace query, not SQL access to the website backend."}
        finally:
            self.conn.set_authorizer(None)
            self.conn.set_progress_handler(None, 0)

    def close(self):
        self.conn.close()


def _origin(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or "*" in p.netloc:
        return ""
    port = p.port
    host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    if port and (p.scheme, port) not in {("http", 80), ("https", 443)}:
        host += f":{port}"
    return f"{p.scheme}://{host}"


def _limit(value: Any, maximum: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"limit must be an integer from 1 to {maximum}")
    return value


class DoshShell:
    """One session's command router, bounded observations, and workspace registry.

    The control realm receives a projection of the loaded page. It has no host
    callbacks, filesystem, credentials, network, or live page globals. All queued
    effects go through Session.act after script evaluation finishes successfully.
    """

    def __init__(self, session: Session, *, database: Path | str | None = None):
        if getattr(session.client, "_dosh_runtime_observer", None) is not None:
            raise ValueError("this session already has a shell observer")
        self.session = session
        self.store = ShellStore(database)
        self.network = NetworkLedger(session.client)
        self.network.attach()
        self.logs: deque = deque(maxlen=1000)
        self.log_seq = 0
        self.closed = False
        self._lock = asyncio.Lock()
        self._observer = self.observe
        setattr(session.client, "_dosh_runtime_observer", self._observer)  # noqa: B010
        session._shell = self

    def observe(self, info: dict, url: str):
        self.network.record_runtime(info, self.session.active_tab_id, url)
        realm = "unknown"
        ctx = info.get("ctx")
        if ctx is not None:
            realm = str(ctx.eval("String(globalThis.__WR_CTX_KEY || '')"))
        for entry in info.get("console", []):
            self.log_seq += 1
            # Free-form logs cannot be reliably scrubbed. Values require explicit opt-in at query time.
            self.logs.append({"id": self.log_seq, "tab_id": self.session.active_tab_id, "realm": realm,
                              "url": _url(url), "level": entry.get("level", "log"),
                              "message": str(entry.get("message", ""))[:4000]})
        for _ in info.get("error_messages", []):
            self.log_seq += 1
            self.logs.append({"id": self.log_seq, "tab_id": self.session.active_tab_id, "realm": realm,
                              "url": _url(url), "level": "error", "message": "page script error (detail withheld)"})

    async def execute(self, command: str, arguments: dict | None = None) -> dict:
        """Accept a named operation plus JSON, or a terminal line: 'open {\"url\":...}'."""
        if self.closed:
            return {"ok": False, "error": "shell is closed"}
        async with self._lock:
            try:
                if arguments is None:
                    name, _, rest = command.strip().partition(" ")
                    arguments = json.loads(rest) if rest.strip() else {}
                else:
                    name = command
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be a JSON object")
                result = await self._execute(name, arguments)
                return {"ok": True, "command": name, "result": result}
            except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                return {"ok": False, "command": command.split(" ", 1)[0], "error": str(exc)}

    def _context(self, frame: str = ""):
        ctx = (
            self.session.frame_source(frame).js_info.get("ctx")
            if frame
            else self.session.current_tab.ctx
        )
        if ctx is None:
            raise ValueError("no live QuickJS realm; load with js=always or select a scripted document")
        return ctx

    def _inventory(self, offset: int, limit: int) -> dict:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        # Collect once, including CSS-hidden controls. action_surface() filters
        # hidden controls out of its returned list; the shell inventory keeps them
        # so the agent can see the full control surface with honest visibility.
        from webresearch.actions import collect

        surface = collect(self.session.html, self.session.url, max_actions=1_000_000)
        items = [asdict(a) for a in surface.actions]
        # Merge live engine visibility annotations when a realm exists: the engine
        # cascade can override inline visibility, so re-check each control.
        live_surface = self.session.action_surface(max_actions=1_000_000)
        live = {a.ref: a for a in live_surface.actions}
        for item in items:
            if item["ref"] in live:
                item.update(asdict(live[item["ref"]]))
        return {"actions": items[offset:offset + limit], "total": len(items), "returned": len(items[offset:offset + limit]),
                "offset": offset, "omitted_before": min(offset, len(items)),
                "omitted_after": max(0, len(items) - offset - limit),
                "next_offset": offset + limit if offset + limit < len(items) else None,
                "coverage": "top-level control inventory; inspect frames separately"}

    async def _execute(self, name: str, a: dict) -> Any:
        s = self.session
        if name == "help":
            return {"commands": COMMANDS, "scripting": "isolated synchronous QuickJS control realm, not page eval",
                    "capture": "starts when the shell attaches; earlier activity cannot be recovered",
                    "storage": "metadata by default; values and console messages can contain secrets",
                    "rollback": "unsupported: evidence snapshots cannot undo requests, VM state, or server effects"}
        if name == "status":
            return {"session_id": s.id, "tab_id": s.active_tab_id, "url": _url(s.url),
                    "live_realm": s.current_tab.ctx is not None, "persistent_workspace": self.store.persistent,
                    "network": self.network.query(limit=1)["coverage"], "log_events": self.log_seq}
        if name == "open":
            page = await s.goto(a["url"], js=a.get("js", "always"))
            return {"url": _url(page.url), "status": page.status, "title": page.title, "view": page.view}
        if name == "actions":
            return self._inventory(a.get("offset", 0), _limit(a.get("limit", 100)))
        if name == "act":
            page = await s.act(a["ref"], text=a.get("text", ""), dx=a.get("dx"), dy=a.get("dy"))
            return {"url": _url(page.url), "status": page.status, "view": page.view,
                    "note": "dispatch completed; website outcome must be verified from the document"}
        if name == "net":
            return self.network.query(a.get("after", 0), _limit(a.get("limit", 100)), a.get("pattern", ""))
        if name == "net.capture":
            if not isinstance(a.get("enabled"), bool):
                raise ValueError("enabled must be boolean")
            self.network.capture_bodies = a["enabled"]
            return {"capture_bodies": a["enabled"], "note": "future responses only; JSON/form, redacted, size limited"}
        if name == "net.rule":
            return self.network.add_rule(a["action"], a["pattern"], a.get("method", "*"), **a.get("options", {}))
        if name == "net.unrule":
            return {"removed": self.network.remove_rule(a["id"])}
        if name == "console":
            after = a.get("after", 0)
            if isinstance(after, bool) or not isinstance(after, int) or after < 0:
                raise ValueError("after must be a nonnegative integer")
            limit = _limit(a.get("limit", 100))
            matches = [e for e in self.logs if e["id"] > after and (not a.get("level") or a["level"] == e["level"])]
            rows = [dict(e) for e in matches[:limit]]
            if not a.get("values", False):
                for row in rows:
                    row["message"] = "[withheld; pass values=true to inspect free-form page output]"
            return {"events": rows, "omitted_limit": max(0, len(matches) - limit),
                    "omitted_retention": max(0, self.log_seq - len(self.logs) - after),
                    "next_after": rows[-1]["id"] if len(matches) > limit else max(after, self.log_seq),
                    "note": "page-controlled output is untrusted; error detail is withheld"}
        if name == "eval":
            return await self.run_script(a["source"], a.get("args", {}), allow_actions=a.get("allow_actions", False))
        if name == "skill.save":
            return self.store.save_skill(a["name"], a["source"], a["origins"], a.get("allow_actions", False))
        if name == "skill.list":
            return self.store.query("SELECT name,sha256,origins,allow_actions,version FROM skills ORDER BY name", [],
                                    _limit(a.get("limit", 100)))
        if name == "skill.run":
            skill = self.store.skill(a["name"])
            if _origin(s.url) not in json.loads(skill["origins"]):
                raise ValueError("skill origin does not match the loaded document")
            if not isinstance(a.get("allow_actions", False), bool):
                raise ValueError("allow_actions must be boolean")
            if skill["allow_actions"] and not a.get("allow_actions", False):
                raise ValueError("this skill requires explicit allow_actions=true")
            result = await self.run_script(skill["source"], a.get("args", {}), allow_actions=bool(skill["allow_actions"]))
            return {"skill": skill["name"], "version": skill["version"], "sha256": skill["sha256"], **result}
        if name == "skill.delete":
            with self.store.conn:
                cur = self.store.conn.execute("DELETE FROM skills WHERE name=?", (a["name"],))
            return {"deleted": bool(cur.rowcount)}
        if name == "db":
            return self.store.query(a.get("sql", "SELECT name FROM sqlite_master WHERE type='table'"),
                                    a.get("params", []), _limit(a.get("limit", 100)))
        if name == "storage":
            ctx = self._context(a.get("frame", ""))
            storage = json.loads(ctx.eval("__drainStorage()"))
            idb = json.loads(ctx.eval("__drainIndexedDB()"))
            if a.get("values", False):
                return {"storage": _redact(storage), "indexedDB": _redact(idb),
                        "warning": "explicit sensitive-data access; key-based redaction cannot detect arbitrary secrets"}
            return {"local_keys": list(storage.get("local", {})), "session_keys": list(storage.get("session", {})),
                    "indexedDB": [{"name": k, "version": v.get("version"), "stores": list(v.get("stores", {}))}
                                  for k, v in idb.items()],
                    "note": "metadata only; IndexedDB is currently per-realm and in-memory"}
        if name == "snapshot":
            evidence = {"title": s.page.title if s.page else "", "url": _url(s.url),
                        "actions": [{"ref": x["ref"], "kind": x["kind"], "label": x["label"]}
                                    for x in self._inventory(0, 1_000_000)["actions"]]}
            with self.store.conn:
                cur = self.store.conn.execute("INSERT INTO snapshots(created,tab_id,url,evidence) VALUES(?,?,?,?)",
                                              (time.time(), s.active_tab_id, _url(s.url), json.dumps(evidence)))
            return {"id": cur.lastrowid, "note": "control evidence only, no DOM text, credentials, heap, or rollback"}
        if name == "diff":
            rows = [self.store.conn.execute("SELECT evidence FROM snapshots WHERE id=?", (a[key],)).fetchone()
                    for key in ("before", "after")]
            if any(row is None for row in rows):
                raise ValueError("snapshot not found")
            before, after = [json.loads(row[0]) for row in rows]
            old = {x["ref"]: x for x in before["actions"]}
            new = {x["ref"]: x for x in after["actions"]}
            return {"added": [v for k, v in new.items() if k not in old],
                    "removed": [v for k, v in old.items() if k not in new],
                    "changed": [v for k, v in new.items() if k in old and v != old[k]],
                    "url_changed": before["url"] != after["url"],
                    "note": "content-addressed refs can change when labels change; not a VM diff"}
        if name == "layers":
            data = json.loads(self._context(a.get("frame", "")).eval("__inspectLayers()"))
            return {"projection": data, "authoritative_hit_test": False,
                    "limitations": ["strata are heuristic classifications, not GPU layers",
                                    "occlusion uses box centers/z-index, not CSS stacking-context semantics",
                                    "hidden controls are not proof of honeypots; aria-hidden does not imply invisible",
                                    "flow/chrome/scroller/overlay/hidden lists cap at 80/50/40/40/60",
                                    "reported counts for capped strata are returned counts, not page totals"]}
        if name == "frames":
            return {"frames": [{"ref": f.ref, "parent": f.parent_ref, "url": _url(f.url), "status": f.status,
                                "live_realm": f.js_info.get("ctx") is not None,
                                "messages_recorded": len(f.post_messages), "messages_delivered": f.delivered_count,
                                "script_errors": f.js_info.get("errors", 0)} for f in s.frame_documents],
                    "limits": {"max_frames": 16, "max_depth": 3},
                    "note": "loaded frame inventory only; no VM stepping; message payloads withheld"}
        if name == "tabs":
            if "id" in a:
                s.switch_tab(a["id"])
            return [{**t, "url": _url(t["url"])} for t in s.tabs]
        if name == "video":
            from webresearch.video import video_view

            rec = await s.get_video(
                a["url"],
                dest_dir=a.get("dest_dir"),
                format=a.get("format"),
                use_session=bool(a.get("use_session", True)),
                max_bytes=a.get("max_bytes"),
                timeout=a.get("timeout"),
            )
            return video_view(rec)
        raise ValueError(f"unknown command: {name}; use help")

    async def run_script(self, source: str, args: dict, *, allow_actions: bool = False) -> dict:
        if not isinstance(source, str) or not source or len(source.encode()) > 65536:
            raise ValueError("source must contain 1-65536 bytes")
        if not isinstance(args, dict) or not isinstance(allow_actions, bool):
            raise ValueError("args must be an object; allow_actions must be boolean")
        if not self.session.html:
            raise ValueError("no loaded document")
        inventory = self._inventory(0, 1_000_000)["actions"]
        # Values, URLs, selectors and hidden form payloads never enter the control realm.
        actions = [{k: x[k] for k in ("ref", "kind", "label", "disabled", "hidden")} for x in inventory]
        page = self.session.page
        reading = "\n".join(
                        block.text
                        for block in page.acd.blocks
                        if block.type in {"heading", "text", "list", "quote"}
                    ) if page and page.acd else ""
        payload = json.dumps({"args": args, "text": reading,
                              "actions": actions, "allow_actions": allow_actions})
        if len(payload.encode()) > 2_000_000:
            raise ValueError("script projection exceeds 2 MB; narrow the document before scripting")
        vm = Context()
        vm.set_memory_limit(16 * 1024 * 1024)
        vm.set_max_stack_size(256 * 1024)
        vm.set_time_limit(0.25)
        try:
            vm.eval(f"var __input = {payload};" + _SDK)
            vm.eval("(function(){\n" + source + "\n})()")
            # No promise pumping: the SDK is deliberately synchronous.
            raw = vm.eval("JSON.stringify(__out)")
            if len(raw.encode()) > 1_000_000:
                raise ValueError("script output exceeds 1 MB")
            output = json.loads(raw)
        except Exception as exc:
            return {"evaluated": False, "effects": [], "error": str(exc)[:500], "realm": "isolated-control"}
        if not isinstance(output, dict) or not isinstance(output.get("actions"), list) or not isinstance(output.get("emits"), list):
            raise ValueError("invalid script output envelope")
        if len(output["actions"]) > 20 or len(output["emits"]) > 100:
            raise ValueError("script output budget exceeded")
        # The VM output is untrusted, even when it used our SDK. Gate effects again on the host.
        if output["actions"] and not allow_actions:
            raise ValueError("action permission required")
        effects = []
        valid_refs = {x["ref"] for x in actions if not x["disabled"] and not x["hidden"]}
        for effect in output["actions"]:
            if not isinstance(effect, dict) or not isinstance(effect.get("ref"), str):
                raise ValueError("invalid queued action")
            if effect["ref"] not in valid_refs or not isinstance(effect.get("options"), dict):
                raise ValueError("script queued an unavailable action")
            if effect["options"].keys() - {"text", "dx", "dy"}:
                raise ValueError("unsupported action options")
        for effect in output["actions"]:
            # Re-resolve after every effect; never drive a stale projection after navigation.
            current = {x["ref"] for x in self._inventory(0, 1_000_000)["actions"] if not x["disabled"] and not x["hidden"]}
            if effect["ref"] not in current:
                effects.append({"ref": effect["ref"], "dispatched": False, "reason": "stale or unavailable ref"})
                break
            try:
                await self.session.act(effect["ref"], **effect["options"])
                effects.append({"ref": effect["ref"], "dispatched": True})
            except Exception as exc:
                effects.append({"ref": effect["ref"], "dispatched": False, "error": type(exc).__name__})
                break
        return {"evaluated": True, "emits": output["emits"], "effects": effects, "realm": "isolated-control",
                "note": "dispatch is not verified website success; effects are sequential, not transactional"}

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.network.detach()
        if getattr(self.session.client, "_dosh_runtime_observer", None) is self._observer:
            delattr(self.session.client, "_dosh_runtime_observer")
        self.store.close()


async def run_terminal(database: str | None = None, commands: list[str] | None = None):
    """Line-delimited JSON output for pipes; prompt only on an interactive terminal."""
    import sys

    from webresearch.session import Browser

    browser = Browser()
    session = await browser.create()
    shell = DoshShell(session, database=database)
    session._shell = shell
    try:
        if commands:
            for command in commands:
                print(json.dumps(await shell.execute(command), ensure_ascii=False))
            return
        while True:
            if sys.stdin.isatty():
                print("dosh> ", end="", flush=True)
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line or line.strip() in {"exit", "quit"}:
                break
            if line.strip():
                print(json.dumps(await shell.execute(line.strip()), ensure_ascii=False), flush=True)
    finally:
        shell.close()
        await browser.close_all()
