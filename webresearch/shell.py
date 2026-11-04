"""Agent command surface over owned browser sessions, not an operating-system shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import tempfile
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
    "product": "Which lane is official (python) vs experimental (rust), and the active JS engine.",
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
    "skill.update": "Extend or set a skill's warrant expiry: name, expires_in seconds (0 = permanent).",
    "db": "Read-only SQL over skills and snapshots: sql, params, limit.",
    "storage": "Inspect page storage metadata (values=false by default).",
    "snapshot": "Save a redacted document/control evidence snapshot; not a VM checkpoint.",
    "diff": "Compare evidence snapshots: before, after.",
    "layers": "Inspect approximate layout strata; not authoritative hit testing.",
    "frames": "Inspect loaded frame realms and message counts, without message payloads.",
    "tabs": "List tabs or switch using id.",
    "video": "Download a video from a legitimate platform: url, dest_dir, format, use_session.",
    "read": "Read the loaded document's reading projection: offset, limit, max_chars.",
    "history": "List history entries, or traverse with move (delta) or goto (absolute index).",
    "cookies": "Inspect session cookie names, domains, paths; values are never echoed.",
    "wait": "Wait for url substring, action ref, page text, or network idle: timeout-bounded.",
    "caps": "Per-page capability manifest: live counts plus known engine gaps; export=path writes it to disk.",
    "screenshot": "Save the engine's approximate SVG layout diagram: dest_dir, frame.",
    "frame.eval": "Run a bounded probe expression in the live page/frame realm: frame, expr.",
    "ref": "Re-resolve a stable id (from an action's stable field) to the current page's ref.",
    "trace": "Act on a ref and return the causal bundle: DOM delta, network, console, dialogs.",
    "watch": "Subscribe to engine mutations: enable, then drain records with after/limit.",
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
                origins TEXT NOT NULL, allow_actions INTEGER NOT NULL, version INTEGER NOT NULL,
                warrant_expires REAL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, tab_id TEXT NOT NULL,
                url TEXT NOT NULL, evidence TEXT NOT NULL
            );
        """)
        # Warrant TTL migration (upgrade audit #2): persistent workspaces
        # created before the column need it added; CREATE TABLE IF NOT
        # EXISTS does not touch existing tables, so ALTER explicitly.
        try:
            self.conn.execute("ALTER TABLE skills ADD COLUMN warrant_expires REAL")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    def save_skill(
        self, name: str, source: str, origins: list[str], allow_actions: bool,
        expires_in: float | None = None,
    ) -> dict:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", name):
            raise ValueError("skill name must be 1-64 letters, digits, dots, underscores or hyphens")
        if not isinstance(source, str) or not source or len(source.encode()) > 65536:
            raise ValueError("source must contain 1-65536 bytes")
        if not isinstance(allow_actions, bool):
            raise ValueError("allow_actions must be boolean")
        if not isinstance(origins, list) or not origins or not all(isinstance(o, str) and _origin(o) == o for o in origins):
            raise ValueError("origins must be explicit HTTP(S) origins without paths or wildcards")
        warrant_expires = self._warrant_expiry(expires_in)
        digest = hashlib.sha256(source.encode()).hexdigest()
        with self.conn:
            self.conn.execute("""
                INSERT INTO skills (name, source, sha256, origins, allow_actions, version, warrant_expires)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(name) DO UPDATE SET source=excluded.source, sha256=excluded.sha256,
                origins=excluded.origins, allow_actions=excluded.allow_actions,
                warrant_expires=excluded.warrant_expires, version=skills.version+1
            """, (name, source, digest, json.dumps(origins), int(allow_actions), warrant_expires))
        return {k: v for k, v in self.skill(name).items() if k != "source"}

    @staticmethod
    def _warrant_expiry(expires_in: float | None) -> float | None:
        """Absolute expiry timestamp from a seconds-from-now TTL.

        None -> permanent (the pre-TTL behavior, unchanged). 0 -> also
        permanent (an explicit clear via skill.update). Positive -> now +
        seconds. Negative/bool/non-numeric are rejected.
        """
        if expires_in is None:
            return None
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)):
            raise ValueError("expires_in must be a nonnegative number of seconds")
        if expires_in < 0:
            raise ValueError("expires_in must be a nonnegative number of seconds")
        if expires_in == 0:
            return None
        return time.time() + float(expires_in)

    def update_skill_warrant(self, name: str, expires_in: float) -> dict:
        """Extend, set, or clear a skill's warrant expiry (audit upgrade #2).

        Scope changes are NOT handled here: origins are the skill's scope and
        re-saving with new origins is the existing versioned path — a
        separate update command would blur which revision authorized what.
        """
        self.skill(name)  # raises unknown skill
        warrant_expires = self._warrant_expiry(expires_in)
        with self.conn:
            self.conn.execute("UPDATE skills SET warrant_expires=? WHERE name=?", (warrant_expires, name))
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


_READING_TYPES = {"heading", "text", "list", "quote", "code", "table"}


def _reading_text(block) -> str:
    """Render an ACD block for the reading projection.

    The ACD has six block types; dropping any of them silently loses content:
    code and table blocks are exactly what code-heavy and data-heavy pages are
    made of. Table rows render as pipe-joined lines, one per row.
    """
    if block.type == "table":
        return "\n".join(" | ".join(row) for row in (block.rows or []) if row)
    return block.text or ""


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

    def _caps_counts(self, frame: str) -> tuple[dict, str, str]:
        """Live DOM counts from the page realm, or honest static fallback.

        The live realm reflects the mutated document (post-script), which is
        the conversion source; the static tree is only used when no realm
        exists or the probe fails, and the source label says so.
        """
        probe = """(function(){
          function q(s){ try { return document.querySelectorAll(s).length } catch(e){ return -1 } }
          var inputs = {};
          try {
            var els = document.querySelectorAll('input');
            for (var i = 0; i < els.length; i++) {
              var t = ((els[i].getAttribute('type')||'text')+'').toLowerCase();
              inputs[t] = (inputs[t]||0)+1;
            }
          } catch(e){}
          return JSON.stringify({canvas:q('canvas'), iframe:q('iframe'), img:q('img'),
            video:q('video'), audio:q('audio'), svg:q('svg'), embed:q('embed'),
            object:q('object'), form:q('form'), select:q('select'), textarea:q('textarea'),
            editable:q('[contenteditable]'), draggable:q('[draggable="true"]'),
            dialog:q('dialog'), wasm_script:q('script[type="application/wasm"]'),
            input_types: inputs, elements: q('*')});
        })()"""
        html = ""
        note = ""
        try:
            ctx = self._context(frame)
            return json.loads(ctx.eval(probe)), "live-realm", note
        except ValueError as exc:
            if "no live QuickJS realm" not in str(exc):
                raise
        except Exception as exc:  # engine probe failed: honest static fallback
            note = f"live probe failed ({type(exc).__name__}); static tree reported"
        html = self.session.html
        if not html:
            raise ValueError("no loaded document")
        from selectolax.lexbor import LexborHTMLParser

        tree = LexborHTMLParser(html)

        def q(sel: str) -> int:
            return len(tree.css(sel))

        inputs: dict[str, int] = {}
        for node in tree.css("input"):
            kind = (node.attributes.get("type") or "text").lower()
            inputs[kind] = inputs.get(kind, 0) + 1
        return {"canvas": q("canvas"), "iframe": q("iframe"), "img": q("img"),
                "video": q("video"), "audio": q("audio"), "svg": q("svg"), "embed": q("embed"),
                "object": q("object"), "form": q("form"), "select": q("select"), "textarea": q("textarea"),
                "editable": q("[contenteditable]"), "draggable": q('[draggable="true"]'),
                "dialog": q("dialog"), "wasm_script": q('script[type="application/wasm"]'),
                "input_types": inputs, "elements": q("*")}, "static-tree", note

    async def _execute(self, name: str, a: dict) -> Any:
        s = self.session
        if name == "help":
            return {"commands": COMMANDS, "scripting": "isolated synchronous QuickJS control realm, not page eval",
                    "capture": "starts when the shell attaches; earlier activity cannot be recovered",
                    "storage": "metadata by default; values and console messages can contain secrets",
                    "rollback": "unsupported: evidence snapshots cannot undo requests, VM state, or server effects"}
        if name == "product":
            from webresearch.config import project_status
            return project_status()
        if name == "status":
            from webresearch.config import project_status
            return {"session_id": s.id, "tab_id": s.active_tab_id, "url": _url(s.url),
                    "live_realm": s.current_tab.ctx is not None, "persistent_workspace": self.store.persistent,
                    "network": self.network.query(limit=1)["coverage"], "log_events": self.log_seq,
                    "product": {"official_lane": project_status()["official_lane"],
                                "active_js_engine": project_status()["js_engine"]["active"]}}
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
            return self.store.save_skill(
                a["name"], a["source"], a["origins"], a.get("allow_actions", False),
                expires_in=a.get("expires_in"),
            )
        if name == "skill.list":
            return self.store.query(
                "SELECT name,sha256,origins,allow_actions,version,warrant_expires FROM skills ORDER BY name",
                [], _limit(a.get("limit", 100)))
        if name == "skill.run":
            skill = self.store.skill(a["name"])
            if _origin(s.url) not in json.loads(skill["origins"]):
                raise ValueError("skill origin does not match the loaded document")
            expires = skill.get("warrant_expires")
            if expires and time.time() > float(expires):
                raise ValueError(
                    f"skill warrant expired ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(expires)))} UTC-local); "
                    "extend with skill.update {name, expires_in} or re-save the skill"
                )
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
        if name == "skill.update":
            expires_in = a.get("expires_in", 0)
            if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in < 0:
                raise ValueError("expires_in must be a nonnegative number of seconds (0 = permanent)")
            return self.store.update_skill_warrant(a["name"], expires_in)
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
        if name == "read":
            page = s.page
            if page is None:
                raise ValueError("no loaded document")
            offset = a.get("offset", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("offset must be a nonnegative integer")
            limit = _limit(a.get("limit", 50))
            max_chars = a.get("max_chars", 4000)
            if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 10 <= max_chars <= 200_000:
                raise ValueError("max_chars must be an integer from 10 to 200000")
            all_blocks = []
            if page.acd is not None:
                for b in page.acd.blocks:
                    if b.type not in _READING_TYPES:
                        continue
                    rendered = _reading_text(b)
                    if rendered:
                        entry = {"type": b.type, "text": rendered}
                        if b.provenance is not None:
                            entry["provenance"] = b.provenance.model_dump()
                        if b.semantic_category is not None:
                            entry["category"] = b.semantic_category
                        if b.confidence is not None:
                            entry["confidence"] = b.confidence
                        all_blocks.append(entry)
            if not all_blocks:
                # Form-heavy pages can compile zero reading blocks (the ACD
                # keeps controls in its action projection instead). Fall back
                # to the page view, split on paragraph breaks, and say so.
                source = "page-view"
                all_blocks = [
                    {"type": "text", "text": part}
                    for part in (page.view or "").split("\n\n")
                    if part.strip()
                ]
            else:
                source = "acd-blocks"
            rows = all_blocks[offset:offset + limit]
            for row in rows:
                if len(row["text"]) > max_chars:
                    row["text"] = row["text"][:max_chars]
                    row["truncated"] = True
            res = {"title": page.title, "url": _url(s.url), "source": source, "blocks": rows,
                   "total": len(all_blocks), "returned": len(rows),
                   "omitted_before": min(offset, len(all_blocks)),
                   "omitted_after": max(0, len(all_blocks) - offset - limit),
                   "next_offset": offset + limit if offset + limit < len(all_blocks) else None,
                   "note": "reading projection; navigation chrome is filtered, use actions for controls"}
            if page.acd is not None and page.acd.collections:
                res["collections"] = [c.model_dump() for c in page.acd.collections]
            return res
        if name == "history":
            entries = s.history
            if "move" in a or "goto" in a:
                if "goto" in a:
                    target = a["goto"]
                else:
                    target = a["move"]
                    if isinstance(target, bool) or not isinstance(target, int) or not -1000 <= target <= 1000:
                        raise ValueError("move must be an integer delta")
                    target = s.index + target
                if isinstance(target, bool) or not isinstance(target, int):
                    raise ValueError("goto must be an integer history index")
                s.index = target  # real traversal: same-document where possible, else stored restore
                page = s.page
                return {"index": s.index, "url": _url(s.url), "title": page.title if page else "",
                        "note": "same-document traversal keeps the JS heap; cross-document restore "
                                "rebuilds the stored document and re-runs its scripts (no bfcache)"}
            limit = _limit(a.get("limit", 100))
            offset = a.get("offset", 0)
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("offset must be a nonnegative integer")
            rows = [{"index": i, "url": _url(p.url), "title": p.title, "current": i == s.index}
                    for i, p in enumerate(entries)]
            return {"entries": rows[offset:offset + limit], "total": len(rows), "current_index": s.index,
                    "returned": len(rows[offset:offset + limit]),
                    "next_offset": offset + limit if offset + limit < len(rows) else None}
        if name == "cookies":
            jar = getattr(s.client.cookies, "jar", None)
            rows = []
            if jar is not None:
                for c in jar:
                    rows.append({"name": c.name, "domain": c.domain, "path": c.path,
                                 "secure": bool(getattr(c, "secure", False)),
                                 "expires": int(getattr(c, "expires", 0) or 0)})
            else:
                host = urlsplit(s.url).hostname if s.url else ""
                names = list(s.client.cookies.keys()) if hasattr(s.client.cookies, "keys") else []
                for n in names:
                    rows.append({"name": n, "domain": host, "path": "/", "secure": False, "expires": 0})
            limit = _limit(a.get("limit", 100))
            return {"cookies": rows[:limit], "total": len(rows), "returned": min(len(rows), limit),
                    "note": "cookie values are never echoed; names/domains/paths only"}
        if name == "wait":
            given = {k for k in ("url", "ref", "text", "net_idle_ms") if k in a}
            if not given:
                raise ValueError("wait needs at least one of: url, ref, text, net_idle_ms")
            timeout = a.get("timeout", 15)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 60:
                raise ValueError("timeout must be 0.1-60 seconds")
            interval = a.get("interval", 0.25)
            if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.05 <= interval <= 2:
                raise ValueError("interval must be 0.05-2 seconds")
            idle_ms = a.get("net_idle_ms", 0)
            if isinstance(idle_ms, bool) or not isinstance(idle_ms, int) or not 0 <= idle_ms <= 60_000:
                raise ValueError("net_idle_ms must be 0-60000")
            url_need, ref_need, text_need = a.get("url", ""), a.get("ref", ""), a.get("text", "")
            if not isinstance(url_need, str) or not isinstance(ref_need, str) or not isinstance(text_need, str):
                raise TypeError("url, ref and text must be strings")
            start = time.monotonic()
            deadline = start + float(timeout)
            last_net = self.network.query(limit=1)["total_observed"]
            last_change = start
            while True:
                await asyncio.sleep(interval)
                now = time.monotonic()
                cur_net = self.network.query(limit=1)["total_observed"]
                if cur_net != last_net:
                    last_net, last_change = cur_net, now
                ok_url = (not url_need) or (url_need in (s.url or ""))
                ok_ref = (not ref_need) or ref_need in {
                    x["ref"] for x in self._inventory(0, 1_000_000)["actions"]
                }
                view = s.page.view if (s.page and s.page.view) else ""
                ok_text = (not text_need) or (text_need in view) or (text_need in (s.html or ""))
                ok_idle = (not idle_ms) or ((now - last_change) * 1000 >= idle_ms)
                checks = {"url": ok_url, "ref": ok_ref, "text": ok_text, "net_idle": ok_idle}
                if all(checks.values()):
                    return {"met": True, "elapsed_ms": int((now - start) * 1000), "checks": checks,
                            "text_source": "view" if view else "html",
                            "note": "condition observed on the live session; not verified website success"}
                if now >= deadline:
                    return {"met": False, "elapsed_ms": int((now - start) * 1000), "checks": checks,
                            "text_source": "view" if view else "html",
                            "note": "timeout; latest observation state reported honestly"}
        if name == "caps":
            counts, source, probe_note = self._caps_counts(a.get("frame", ""))
            unavailable = []
            if counts.get("canvas"):
                unavailable.append({"capability": "canvas pixels",
                                    "reason": "decomposed at parse time; no raster output, by design"})
            if counts.get("iframe"):
                unavailable.append({"capability": "iframe bfcache parity",
                                    "reason": "frames are separate documents; restore re-runs scripts"})
            if counts.get("wasm_script"):
                unavailable.append({"capability": "wasm execution",
                                    "reason": "interpreter not implemented; these modules are inert"})
            if counts.get("video") or counts.get("audio"):
                unavailable.append({"capability": "media playback/raster",
                                    "reason": "no pixel or audio output; download via the video command"})
            if counts.get("editable"):
                unavailable.append({"capability": "caret/selection in editors",
                                    "reason": "contenteditable types plain text only; no caret model"})
            manifest = {"url": _url(s.url), "counts": counts, "source": source,
                        "tabs": len(s.tabs), "frames": len(s.frame_documents),
                        "dialogs_pending": len(s.dialogs()),
                        "page_unavailable": unavailable,
                        "engine_gaps": [
                            {"capability": "css floats/sticky/flex-wrap distribution", "reason": "block-flow approximation, disclosed"},
                            {"capability": "text shaping/bidi (non-Latin scripts)", "reason": "proportional advance table only"},
                            {"capability": "websocket binary frames", "reason": "counted and skipped, disclosed gap"},
                            {"capability": "workers as real threads", "reason": "single-threaded emulation; constructors inert"},
                            {"capability": "service worker execution", "reason": "simulated, not executed"},
                            {"capability": "pixel-faithful screenshot", "reason": "SVG layout diagram only (see screenshot command)"},
                        ],
                        "note": ("; ".join(filter(None, [probe_note,
                             "honest manifest: unavailable items state why; this is not a claim of completeness"])))}
            if a.get("export"):
                export_path = Path(str(a["export"]))
                if not export_path.is_absolute():
                    raise ValueError("export path must be absolute")
                export_path.parent.mkdir(parents=True, exist_ok=True)
                payload = json.dumps(manifest, indent=2, ensure_ascii=False)
                export_path.write_text(payload, encoding="utf-8")
                return {**manifest, "exported_to": str(export_path),
                        "exported_bytes": len(payload.encode("utf-8"))}
            return manifest
        if name == "screenshot":
            ctx = self._context(a.get("frame", ""))
            try:
                svg = ctx.eval("__screenshotSVG()")
            except Exception as exc:
                raise ValueError(f"engine screenshot failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
            if not isinstance(svg, str) or not svg.startswith("<svg"):
                raise ValueError("engine returned no SVG layout diagram")
            dest = Path(a.get("dest_dir") or tempfile.gettempdir())
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / f"dosh-screenshot-{int(time.time() * 1000)}.svg"
            path.write_text(svg, encoding="utf-8")
            return {"path": str(path), "bytes": len(svg.encode("utf-8")),
                    "frame": a.get("frame", ""), "url": _url(s.url),
                    "note": "approximate SVG layout diagram from computed boxes/colors/text; "
                            "no canvas pixels; images referenced by URL, not inlined; not pixel-faithful"}
        if name == "frame.eval":
            expr = a.get("expr", "")
            if not isinstance(expr, str) or not expr or len(expr.encode()) > 65_536:
                raise ValueError("expr must contain 1-65536 bytes")
            ctx = self._context(a.get("frame", ""))
            wrapped = ("(function(){ var v = (" + expr + ");"
                       " try { return JSON.stringify(v); } catch (e) { return String(v); } })()")
            ctx.set_time_limit(0.5)
            try:
                raw = ctx.eval(wrapped)
            except Exception as exc:
                return {"evaluated": False, "error": str(exc)[:500], "realm": "page-frame",
                        "note": "probe expression must be a single JavaScript expression; "
                                "page-realm output is untrusted page data"}
            finally:
                ctx.set_time_limit(-1)
            value = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
            return {"evaluated": True, "value": value[:100_000], "truncated": len(value) > 100_000,
                    "realm": "page-frame",
                    "note": "page-realm probe: observes (and can mutate) the live document; "
                            "output is untrusted page data, not engine verification"}
        if name == "ref":
            stable = a.get("stable", "")
            if not isinstance(stable, str) or not stable:
                raise ValueError("ref needs a stable id (from an action's stable field)")
            matches = [x for x in self._inventory(0, 1_000_000)["actions"] if x.get("stable") == stable]
            if not matches:
                raise ValueError(f"no action with stable id {stable} on this document")
            if len(matches) > 1:
                return {"stable": stable, "ambiguous": True, "matches": matches,
                        "note": "repeated identical controls share one content identity; "
                                "identical siblings carry document-order suffixes (~2, ~3…) "
                                "on both ref and stable — address the suffixed ref directly"}
            hit = matches[0]
            return {"stable": stable, "ref": hit["ref"], "kind": hit["kind"], "label": hit["label"],
                    "disabled": hit["disabled"], "hidden": hit["hidden"],
                    "note": "URL-free identity re-resolved onto the current document; "
                            "the ref changes with URL churn, the stable id does not"}
        if name == "trace":
            if not isinstance(a.get("ref"), str) or not a["ref"]:
                raise ValueError("trace needs a ref to act on")
            before = {x["stable"]: x for x in self._inventory(0, 1_000_000)["actions"]}
            before_net = self.network.query(limit=1)["latest_id"]
            before_log = self.log_seq
            page = await s.act(a["ref"], text=a.get("text", ""), dx=a.get("dx"), dy=a.get("dy"))
            after_list = self._inventory(0, 1_000_000)["actions"]
            after = {x["stable"]: x for x in after_list}
            keep = ("ref", "stable", "kind", "label", "disabled", "hidden")
            added = [{k: x[k] for k in keep if k in x} for x in after.values() if x["stable"] not in before]
            removed = [{k: x[k] for k in keep if k in x} for x in before.values() if x["stable"] not in after]
            changed = [{k: x[k] for k in keep if k in x} for x in after.values()
                       if x["stable"] in before and x != before[x["stable"]]]
            net = self.network.query(after=before_net, limit=50)
            log_matches = [e for e in self.logs if e["id"] > before_log]
            log_rows = [dict(e) for e in log_matches[:50]]
            if not a.get("values", False):
                for row in log_rows:
                    row["message"] = "[withheld; pass values=true to inspect free-form page output]"
            return {"acted": {"ref": a["ref"], "text": a.get("text", "")},
                    "page": {"url": _url(page.url), "status": page.status, "title": page.title},
                    "dom_delta": {"added": added, "removed": removed, "changed": changed,
                                  "stable_before": len(before), "stable_after": len(after)},
                    "network": {"records": net["records"], "returned": net["returned"],
                                "omitted_limit": net["omitted_limit"], "latest_id": net["latest_id"]},
                    "console": {"events": log_rows, "omitted": max(0, len(log_matches) - 50)},
                    "dialogs": s.dialogs(),
                    "note": "one action's full causal footprint; delta is identity-keyed (stable ids), "
                            "ref churn shows as changed entries, not lost controls"}
        if name == "watch":
            ctx = self._context(a.get("frame", ""))
            if "enable" in a:
                if not isinstance(a["enable"], bool):
                    raise ValueError("enable must be boolean")
                if a["enable"]:
                    raw = ctx.eval("__wrWatchInstall()")
                    state = json.loads(raw) if isinstance(raw, str) else {}
                    if not state.get("ok"):
                        raise ValueError(state.get("reason", "watch install failed"))
                    return {"enabled": True, "cap": state.get("cap"),
                            "note": "drain with watch {after, limit}; delivery is microtask-based, "
                                    "so records appear after action passes; the log is host-side only"}
                raw = ctx.eval("__wrWatchDisable()")
                state = json.loads(raw) if isinstance(raw, str) else {}
                return {"enabled": False, "retained": state.get("retained"),
                        "note": "observer disconnected; retained records stay until the next enable resets the log"}
            after = a.get("after", 0)
            if isinstance(after, bool) or not isinstance(after, int) or after < 0:
                raise ValueError("after must be a nonnegative integer")
            limit = _limit(a.get("limit", 50), maximum=200)
            raw = ctx.eval(f"__wrWatchDrain({after}, {limit})")
            data = json.loads(raw) if isinstance(raw, str) else {}
            records = data.get("records", [])
            return {"records": records, "latest_seq": data.get("latest_seq", 0),
                    "enabled": data.get("enabled", False), "retained": data.get("retained", 0),
                    "returned": len(records),
                    "next_after": records[-1]["seq"] if records else after,
                    "omitted_retained": max(0, (data.get("retained", 0)) - len(records)),
                    "note": "engine MutationObserver records; compact node descriptors, never live nodes"}
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
                        _reading_text(block)
                        for block in page.acd.blocks
                        if block.type in _READING_TYPES
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
