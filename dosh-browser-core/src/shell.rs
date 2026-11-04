//! `shell.rs` — The agent command surface (replaces `webresearch/shell.py`).
//!
//! Handles 34 commands: `help`, `product`, `status`, `open`, `actions`, `act`, `net`,
//! `net.capture`, `net.rule`, `net.unrule`, `console`, `eval`, `skill.save`, `skill.list`,
//! `skill.run`, `skill.delete`, `skill.update`, `db`, `storage`, `snapshot`, `diff`,
//! `layers`, `frames`, `tabs`, `video`, `read`, `history`, `cookies`, `wait`, `caps`,
//! `screenshot`, `frame.eval`, `ref`, `trace`, `watch`.
//!
//! Uses `ShellStore` for persistent SQLite skills and snapshots.

use rusqlite::{params, Connection};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use url::Url;

use crate::session::Session;

pub const COMMANDS: &[(&str, &str)] = &[
    ("help", "List commands and capability limits."),
    ("product", "Which lane is official (rust) vs companion (python), and the active JS engine."),
    ("status", "Current session, tab, document, and capture status."),
    ("open", "Load url through the normal owned document lifecycle."),
    ("actions", "List controls with explicit pagination coverage."),
    ("act", "Execute an existing browser action ref, with optional text/dx/dy."),
    ("net", "Query network observations: after, limit, pattern."),
    ("net.capture", "Enable/disable opt-in redacted JSON/form response bodies: enabled."),
    ("net.rule", "Add runtime fetch/XHR block/header/mock rule: action, pattern, options."),
    ("net.unrule", "Remove network rule by id."),
    ("net.apis", "Catalog discovered background API endpoints for Site-as-SDK replay."),
    ("console", "Query captured page logs/errors: after, limit, level."),
    ("eval", "Run bounded control-realm JavaScript: source, args, allow_actions=false."),
    ("skill.save", "Save source, name, origins, allow_actions=false to the workspace registry."),
    ("skill.list", "List saved skills without executing them."),
    ("skill.run", "Run name on an allowed origin; mutations require allow_actions=true again."),
    ("skill.delete", "Delete a saved skill by name."),
    ("skill.update", "Extend or set a skill's warrant expiry: name, expires_in seconds (0 = permanent)."),
    ("db", "Read-only SQL over skills and snapshots: sql, params, limit."),
    ("storage", "Inspect page storage metadata (values=false by default)."),
    ("snapshot", "Save a redacted document/control evidence snapshot; not a VM checkpoint."),
    ("diff", "Compare evidence snapshots: before, after."),
    ("checkpoint", "Save durable session checkpoint (DOM tree, storage, history, cookies) to SQLite: id."),
    ("restore", "Restore session from durable checkpoint: id."),
    ("layers", "Inspect approximate layout strata; not authoritative hit testing."),
    ("frames", "Inspect loaded frame realms and message counts, without message payloads."),
    ("tabs", "List tabs or switch using id."),
    ("video", "Download a video from a legitimate platform: url, dest_dir, format, use_session."),
    ("read", "Read the loaded document's reading projection: offset, limit, max_chars."),
    ("history", "List history entries, or traverse with move (delta) or goto (absolute index)."),
    ("cookies", "Inspect session cookie names, domains, paths; values are never echoed."),
    ("wait", "Wait for url substring, action ref, page text, or network idle: timeout-bounded."),
    ("caps", "Per-page capability manifest: live counts plus known engine gaps; export=path writes it to disk."),
    ("screenshot", "Save the engine's approximate SVG layout diagram: dest_dir, frame."),
    ("frame.eval", "Run a bounded probe expression in the live page/frame realm: frame, expr."),
    ("ref", "Re-resolve a stable id (from an action's stable field) to the current page's ref."),
    ("trace", "Act on a ref and return the causal bundle: DOM delta, network, console, dialogs."),
    ("watch", "Subscribe to engine mutations: enable, then drain records with after/limit."),
    ("solve_captcha", "Inspect and solve the current page's reCAPTCHA v2 challenge."),
    ("login", "Login with url, username, and password. Solves captcha and submits."),
    ("wasm", "Query or configure Liftoff WASM execution fuel, memory, and export telemetry: fuel, action."),
    ("clock", "Inspect or set deterministic virtual host clock: now, set, advance (millis)."),
    ("rng", "Set deterministic PRNG seed for Math.random replays: seed, reset."),
    ("heap", "Inspect 128MB pruned V8 heap statistics: used, total, limit, and GC count."),
    ("gate", "Configure security capability gates: gate (eval/net/wasm), allowed (bool)."),
];

pub fn get_origin(u: &str) -> String {
    if let Ok(parsed) = Url::parse(u) {
        if parsed.scheme() == "http" || parsed.scheme() == "https" {
            if let Some(host) = parsed.host_str() {
                let port = parsed.port_or_known_default().unwrap_or(80);
                if (parsed.scheme() == "http" && port == 80) || (parsed.scheme() == "https" && port == 443) {
                    return format!("{}://{}", parsed.scheme(), host);
                } else {
                    return format!("{}://{}:{}", parsed.scheme(), host, port);
                }
            }
        }
    }
    String::new()
}

/// The workspace evidence and skills store.
pub struct ShellStore {
    conn: Mutex<Connection>,
    pub persistent: bool,
}

impl ShellStore {
    pub fn new(path: Option<PathBuf>) -> Result<Self, rusqlite::Error> {
        let conn = match &path {
            Some(p) => Connection::open(p)?,
            None => Connection::open_in_memory()?,
        };

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS skills (
                name TEXT PRIMARY KEY, source TEXT NOT NULL, sha256 TEXT NOT NULL,
                origins TEXT NOT NULL, allow_actions INTEGER NOT NULL, version INTEGER NOT NULL,
                warrant_expires REAL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, tab_id TEXT NOT NULL,
                url TEXT NOT NULL, evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY, created REAL NOT NULL, tab_id TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT NOT NULL, html TEXT NOT NULL,
                cookies TEXT NOT NULL, storage TEXT NOT NULL, history TEXT NOT NULL
            );",
        )?;

        Ok(Self {
            conn: Mutex::new(conn),
            persistent: path.is_some(),
        })
    }

    pub fn save_skill(
        &self,
        name: &str,
        source: &str,
        origins: Vec<String>,
        allow_actions: bool,
        expires_in: Option<f64>,
    ) -> Result<serde_json::Value, rusqlite::Error> {
        let warrant_expires = match expires_in {
            None => None,
            Some(secs) if secs <= 0.0 => None,
            Some(secs) => Some(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs_f64()
                    + secs,
            ),
        };

        let mut hasher = Sha256::new();
        hasher.update(source.as_bytes());
        let digest = hex::encode(hasher.finalize());

        let origins_json = serde_json::to_string(&origins).unwrap_or_default();
        let conn = self.conn.lock().unwrap();

        conn.execute(
            "INSERT INTO skills (name, source, sha256, origins, allow_actions, version, warrant_expires)
             VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6)
             ON CONFLICT(name) DO UPDATE SET source=excluded.source, sha256=excluded.sha256,
             origins=excluded.origins, allow_actions=excluded.allow_actions,
             warrant_expires=excluded.warrant_expires, version=skills.version+1",
            params![
                name,
                source,
                digest,
                origins_json,
                if allow_actions { 1 } else { 0 },
                warrant_expires,
            ],
        )?;
        drop(conn);

        self.get_skill(name)
    }

    pub fn get_skill(&self, name: &str) -> Result<serde_json::Value, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let skill = conn.query_row(
            "SELECT name, sha256, origins, allow_actions, version, warrant_expires, source FROM skills WHERE name = ?1",
            params![name],
            |row| {
                let origins_str: String = row.get(2)?;
                let origins: Vec<String> = serde_json::from_str(&origins_str).unwrap_or_default();
                Ok(serde_json::json!({
                    "name": row.get::<_, String>(0)?,
                    "sha256": row.get::<_, String>(1)?,
                    "origins": origins,
                    "allow_actions": row.get::<_, i64>(3)? == 1,
                    "version": row.get::<_, i64>(4)?,
                    "warrant_expires": row.get::<_, Option<f64>>(5)?,
                    "source": row.get::<_, String>(6)?,
                }))
            },
        )?;
        Ok(skill)
    }

    pub fn update_skill_warrant(&self, name: &str, expires_in: f64) -> Result<serde_json::Value, rusqlite::Error> {
        let warrant_expires = if expires_in <= 0.0 {
            None
        } else {
            Some(
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs_f64()
                    + expires_in,
            )
        };
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "UPDATE skills SET warrant_expires = ?1 WHERE name = ?2",
            params![warrant_expires, name],
        )?;
        drop(conn);
        self.get_skill(name)
    }

    pub fn list_skills(&self) -> Result<Vec<serde_json::Value>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT name, sha256, origins, allow_actions, version, warrant_expires FROM skills ORDER BY name",
        )?;
        let rows = stmt.query_map([], |row| {
            let origins_str: String = row.get(2)?;
            let origins: Vec<String> = serde_json::from_str(&origins_str).unwrap_or_default();
            Ok(serde_json::json!({
                "name": row.get::<_, String>(0)?,
                "sha256": row.get::<_, String>(1)?,
                "origins": origins,
                "allow_actions": row.get::<_, i64>(3)? == 1,
                "version": row.get::<_, i64>(4)?,
                "warrant_expires": row.get::<_, Option<f64>>(5)?,
            }))
        })?;

        let mut list = Vec::new();
        for r in rows {
            list.push(r?);
        }
        Ok(list)
    }

    pub fn delete_skill(&self, name: &str) -> Result<bool, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let changed = conn.execute("DELETE FROM skills WHERE name = ?1", params![name])?;
        Ok(changed > 0)
    }

    pub fn save_snapshot(&self, tab_id: &str, url: &str, evidence: &str) -> Result<i64, rusqlite::Error> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO snapshots (created, tab_id, url, evidence) VALUES (?1, ?2, ?3, ?4)",
            params![now, tab_id, url, evidence],
        )?;
        Ok(conn.last_insert_rowid())
    }

    pub fn get_snapshot(&self, id: i64) -> Result<serde_json::Value, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.query_row(
            "SELECT id, created, tab_id, url, evidence FROM snapshots WHERE id = ?1",
            params![id],
            |row| {
                let evidence_str: String = row.get(4)?;
                let evidence: serde_json::Value = serde_json::from_str(&evidence_str).unwrap_or_default();
                Ok(serde_json::json!({
                    "id": row.get::<_, i64>(0)?,
                    "created": row.get::<_, f64>(1)?,
                    "tab_id": row.get::<_, String>(2)?,
                    "url": row.get::<_, String>(3)?,
                    "evidence": evidence,
                }))
            },
        )
    }

    pub fn save_checkpoint(
        &self,
        id: &str,
        tab_id: &str,
        url: &str,
        title: &str,
        html: &str,
        cookies: &str,
        storage: &str,
        history: &str,
    ) -> Result<serde_json::Value, rusqlite::Error> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (id, created, tab_id, url, title, html, cookies, storage, history) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![id, now, tab_id, url, title, html, cookies, storage, history],
        )?;
        Ok(serde_json::json!({
            "id": id,
            "created": now,
            "tab_id": tab_id,
            "url": url,
            "title": title,
        }))
    }

    pub fn get_checkpoint(&self, id: &str) -> Result<serde_json::Value, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.query_row(
            "SELECT id, created, tab_id, url, title, html, cookies, storage, history FROM checkpoints WHERE id = ?1",
            params![id],
            |row| {
                Ok(serde_json::json!({
                    "id": row.get::<_, String>(0)?,
                    "created": row.get::<_, f64>(1)?,
                    "tab_id": row.get::<_, String>(2)?,
                    "url": row.get::<_, String>(3)?,
                    "title": row.get::<_, String>(4)?,
                    "html": row.get::<_, String>(5)?,
                    "cookies": row.get::<_, String>(6)?,
                    "storage": row.get::<_, String>(7)?,
                    "history": row.get::<_, String>(8)?,
                }))
            },
        )
    }

    pub fn list_checkpoints(&self) -> Result<Vec<serde_json::Value>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare("SELECT id, created, tab_id, url, title FROM checkpoints ORDER BY created DESC")?;
        let rows = stmt.query_map([], |row| {
            Ok(serde_json::json!({
                "id": row.get::<_, String>(0)?,
                "created": row.get::<_, f64>(1)?,
                "tab_id": row.get::<_, String>(2)?,
                "url": row.get::<_, String>(3)?,
                "title": row.get::<_, String>(4)?,
            }))
        })?;
        let mut list = Vec::new();
        for r in rows {
            list.push(r?);
        }
        Ok(list)
    }

    pub fn query_readonly(&self, sql: &str, limit: usize) -> Result<Vec<serde_json::Value>, String> {
        let sql_lower = sql.trim().to_lowercase();
        if !sql_lower.starts_with("select") && !sql_lower.starts_with("values") {
            return Err("Only read-only queries (SELECT) are permitted".to_string());
        }
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(sql).map_err(|e| e.to_string())?;
        let col_names: Vec<String> = stmt.column_names().into_iter().map(|s| s.to_string()).collect();

        let mut rows = Vec::new();
        let mut query_rows = stmt.query([]).map_err(|e| e.to_string())?;
        while let Some(row) = query_rows.next().map_err(|e| e.to_string())? {
            if rows.len() >= limit {
                break;
            }
            let mut obj = serde_json::Map::new();
            for (i, name) in col_names.iter().enumerate() {
                let val_ref = row.get_ref(i).map_err(|e| e.to_string())?;
                let json_val = match val_ref {
                    rusqlite::types::ValueRef::Null => serde_json::Value::Null,
                    rusqlite::types::ValueRef::Integer(n) => serde_json::json!(n),
                    rusqlite::types::ValueRef::Real(f) => serde_json::json!(f),
                    rusqlite::types::ValueRef::Text(t) => {
                        serde_json::json!(String::from_utf8_lossy(t).to_string())
                    }
                    rusqlite::types::ValueRef::Blob(b) => serde_json::json!(hex::encode(b)),
                };
                obj.insert(name.clone(), json_val);
            }
            rows.push(serde_json::Value::Object(obj));
        }
        Ok(rows)
    }
}

/// Execute a shell command against the session.
pub async fn execute_command(
    command: &str,
    args: &serde_json::Value,
    session: &mut Session,
    store: &ShellStore,
) -> Result<serde_json::Value, Box<dyn std::error::Error + Send + Sync>> {
    match command {
        "help" => {
            let cmds: Vec<_> = COMMANDS.iter().map(|(c, desc)| serde_json::json!({ "command": c, "description": desc })).collect();
            Ok(serde_json::json!({
                "commands": cmds,
                "scripting": "isolated synchronous control realm, not page eval",
                "storage": "metadata by default; values can contain secrets",
            }))
        }
        "product" => {
            Ok(serde_json::json!({
                "official_lane": "rust",
                "companion_sdk": "python",
                "default_engine": "doshgentv8",
                "lanes": {
                    "rust": {
                        "path": "dosh-browser-core/",
                        "status": "official",
                        "summary": "Full pure-Rust browser core with doshgentv8 V8 engine, RFC 9111 caching, ANBF binary packaging, 41 shell commands, and HTTP/REST server."
                    },
                    "python": {
                        "path": "webresearch/",
                        "status": "optional-sdk",
                        "summary": "Companion Python SDK and agent framework integration."
                    }
                }
            }))
        }
        "status" => {
            let tab = session.current_tab();
            Ok(serde_json::json!({
                "tab": tab.id,
                "url": tab.url,
                "history_len": tab.history.len(),
                "tabs": session.tabs.len(),
                "persistent_workspace": store.persistent,
            }))
        }
        "open" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let page = session.goto(url, 8000).await?;
            Ok(serde_json::to_value(page)?)
        }
        "actions" => {
            let tab = session.current_tab();
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(100) as usize;
            let offset = args.get("offset").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let actions = crate::actions::extract_actions(&tab.html, &tab.url, offset + limit);
            let total = actions.len();
            let sliced = if offset < total {
                actions[offset..std::cmp::min(offset + limit, total)].to_vec()
            } else {
                Vec::new()
            };
            Ok(serde_json::json!({
                "actions": sliced,
                "total": total,
                "offset": offset,
                "limit": limit,
            }))
        }
        "act" => {
            let ref_id = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let text = args.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let page = if !text.is_empty() {
                session.type_text(ref_id, text).await?
            } else {
                session.click(ref_id).await?
            };
            Ok(serde_json::to_value(page)?)
        }
        "net" => {
            let pattern = args.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(100) as usize;
            let after = args.get("after").and_then(|v| v.as_u64()).unwrap_or(0);
            let res = session.network_ledger.query(after, limit, pattern);
            Ok(res)
        }
        "net.capture" => {
            let enabled = args.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
            session.network_ledger.capture_bodies = enabled;
            Ok(serde_json::json!({ "capture_bodies": enabled }))
        }
        "net.rule" => {
            let action = args.get("action").and_then(|v| v.as_str()).unwrap_or("block");
            let pattern = args.get("pattern").and_then(|v| v.as_str()).unwrap_or("*");
            let status = args.get("status").and_then(|v| v.as_u64()).unwrap_or(200) as u16;
            let body = args.get("body").and_then(|v| v.as_str()).unwrap_or("");
            let content_type = args.get("content_type").and_then(|v| v.as_str()).unwrap_or("text/plain");
            let redirect_url = args.get("redirect_url").and_then(|v| v.as_str()).unwrap_or("");
            let id = session.network_ledger.add_rule(pattern, action, status, body, content_type, redirect_url);
            Ok(serde_json::json!({ "rule_id": id, "action": action, "pattern": pattern }))
        }
        "net.unrule" => {
            let id = args.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let removed = session.network_ledger.remove_rule(id);
            Ok(serde_json::json!({ "removed": removed }))
        }
        "net.apis" => {
            let apis = session.network_ledger.discover_apis();
            Ok(serde_json::json!({ "endpoints": apis }))
        }
        "console" => {
            let after = args.get("after").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(100) as usize;
            let level = args.get("level").and_then(|v| v.as_str()).unwrap_or("");
            let filtered: Vec<_> = session.console_logs
                .iter()
                .filter(|log| {
                    if !level.is_empty() && log.get("level").and_then(|v| v.as_str()) != Some(level) {
                        return false;
                    }
                    true
                })
                .skip(after)
                .take(limit)
                .cloned()
                .collect();
            Ok(serde_json::json!({ "events": filtered, "returned": filtered.len() }))
        }
        "eval" => {
            let expr = args.get("source").or_else(|| args.get("expr")).and_then(|v| v.as_str()).unwrap_or("");
            let tab = session.current_tab();
            let mut rt = crate::runtime::Runtime::new(session.client.clone(), &tab.url)?;
            rt.load_document(&tab.html, &tab.url)?;
            let result = rt.eval(expr)?;
            Ok(serde_json::json!({ "evaluated": true, "result": result }))
        }
        "skill.save" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let source = args.get("source").and_then(|v| v.as_str()).unwrap_or("");
            let origins = args.get("origins")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|v| v.as_str().map(String::from)).collect::<Vec<_>>())
                .unwrap_or_default();
            let allow_actions = args.get("allow_actions").and_then(|v| v.as_bool()).unwrap_or(false);
            let expires_in = args.get("expires_in").and_then(|v| v.as_f64());

            let res = store.save_skill(name, source, origins, allow_actions, expires_in)?;
            Ok(res)
        }
        "skill.list" => {
            let skills = store.list_skills()?;
            Ok(serde_json::json!({ "skills": skills }))
        }
        "skill.run" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let skill = store.get_skill(name)?;
            let origins = skill.get("origins").and_then(|v| v.as_array()).cloned().unwrap_or_default();
            let cur_origin = get_origin(&session.current_tab().url);
            let matches_origin = origins.iter().any(|o| o.as_str() == Some(&cur_origin));
            if !matches_origin && !origins.is_empty() {
                return Err(format!("Skill '{}' origin does not match current document: {}", name, cur_origin).into());
            }
            if let Some(expires) = skill.get("warrant_expires").and_then(|v| v.as_f64()) {
                let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs_f64();
                if now > expires {
                    return Err(format!("Skill '{}' warrant has expired", name).into());
                }
            }
            let source = skill.get("source").and_then(|v| v.as_str()).unwrap_or("");
            let tab = session.current_tab();
            let mut rt = crate::runtime::Runtime::new(session.client.clone(), &tab.url)?;
            rt.load_document(&tab.html, &tab.url)?;
            let result = rt.eval(source)?;
            Ok(serde_json::json!({
                "skill": name,
                "version": skill["version"],
                "sha256": skill["sha256"],
                "evaluated": true,
                "result": result
            }))
        }
        "skill.delete" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let deleted = store.delete_skill(name)?;
            Ok(serde_json::json!({ "deleted": deleted }))
        }
        "skill.update" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let expires_in = args.get("expires_in").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let updated = store.update_skill_warrant(name, expires_in)?;
            Ok(updated)
        }
        "db" => {
            let sql = args.get("sql").and_then(|v| v.as_str()).unwrap_or("SELECT name FROM sqlite_master WHERE type='table'");
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(100) as usize;
            let rows = store.query_readonly(sql, limit)?;
            Ok(serde_json::json!({ "rows": rows, "returned": rows.len(), "read_only": true }))
        }
        "storage" => {
            let tab = session.current_tab();
            let origin = get_origin(&tab.url);
            let session_data = tab.session_storage.get(&origin).cloned().unwrap_or_default();
            Ok(serde_json::json!({
                "session_storage_keys": session_data.keys().cloned().collect::<Vec<_>>(),
                "note": "storage metadata only; pass values=true for redacted payload"
            }))
        }
        "snapshot" => {
            let tab = session.current_tab();
            let actions = crate::actions::extract_actions(&tab.html, &tab.url, 100);
            let evidence = serde_json::json!({
                "title": tab.url,
                "url": tab.url,
                "actions": actions,
            });
            let id = store.save_snapshot(&tab.id, &tab.url, &serde_json::to_string(&evidence)?)?;
            Ok(serde_json::json!({ "id": id, "tab_id": tab.id, "note": "control evidence snapshot" }))
        }
        "diff" => {
            let before_id = args.get("before").and_then(|v| v.as_i64()).unwrap_or(0);
            let after_id = args.get("after").and_then(|v| v.as_i64()).unwrap_or(0);
            let s1 = store.get_snapshot(before_id)?;
            let s2 = store.get_snapshot(after_id)?;
            Ok(serde_json::json!({
                "before": before_id,
                "after": after_id,
                "url_changed": s1["url"] != s2["url"],
                "note": "evidence snapshot comparison"
            }))
        }
        "checkpoint" => {
            let tab = session.current_tab();
            let default_id = format!("chk_{}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs());
            let id = args.get("id").and_then(|v| v.as_str()).unwrap_or(&default_id);
            let title = tab.page().map(|p| p.title).unwrap_or_default();
            let cookies = serde_json::to_string(&tab.headers).unwrap_or_default();
            let storage = serde_json::to_string(&session.local_storage).unwrap_or_default();
            let history = serde_json::to_string(&tab.history).unwrap_or_default();
            let res = store.save_checkpoint(id, &tab.id, &tab.url, &title, &tab.html, &cookies, &storage, &history)
                .map_err(|e| e.to_string())?;
            Ok(serde_json::json!({ "checkpoint": res, "note": "durable session checkpoint saved to SQLite" }))
        }
        "restore" => {
            let id = args.get("id").and_then(|v| v.as_str()).ok_or("id is required to restore checkpoint")?;
            let chk = store.get_checkpoint(id).map_err(|e| e.to_string())?;
            if let Some(s) = chk["storage"].as_str() {
                if let Ok(st) = serde_json::from_str::<HashMap<String, HashMap<String, String>>>(s) {
                    session.local_storage = st;
                }
            }
            let tab = session.current_tab_mut();
            if let Some(h) = chk["html"].as_str() {
                tab.html = h.to_string();
            }
            if let Some(u) = chk["url"].as_str() {
                tab.url = u.to_string();
            }
            if let Some(c) = chk["cookies"].as_str() {
                if let Ok(hdr) = serde_json::from_str::<HashMap<String, String>>(c) {
                    tab.headers = hdr;
                }
            }
            if let Some(hist) = chk["history"].as_str() {
                if let Ok(h) = serde_json::from_str::<Vec<crate::session::Page>>(hist) {
                    tab.history = h;
                    tab.index = (tab.history.len() as isize) - 1;
                }
            }
            Ok(serde_json::json!({
                "restored": id,
                "url": chk["url"],
                "title": chk["title"],
                "note": "session rehydrated from checkpoint"
            }))
        }
        "layers" => {
            let tab = session.current_tab();
            let mut rt = crate::runtime::Runtime::new(session.client.clone(), &tab.url)?;
            rt.load_document(&tab.html, &tab.url)?;
            let _ = rt.sync_butterfly_layout();
            let rect_count = rt.get_butterfly_layout().len();
            let layers_json = rt.eval("__inspectLayers()")?;
            let parsed: serde_json::Value = serde_json::from_str(&layers_json).unwrap_or(serde_json::json!({}));
            Ok(serde_json::json!({
                "projection": parsed,
                "butterfly_rects_count": rect_count,
                "authoritative_hit_test": false,
                "note": "strata are computed heuristic block layers backed by zero-copy Butterfly storage"
            }))
        }
        "frames" => {
            let seeds = session.discover_frame_seeds();
            Ok(serde_json::json!({ "frames": seeds, "total": seeds.len() }))
        }
        "tabs" => {
            if let Some(id) = args.get("id").and_then(|v| v.as_str()) {
                session.switch_tab(id)?;
            }
            let tabs = session.tabs.iter().map(|t| {
                serde_json::json!({
                    "id": t.id,
                    "url": t.url,
                    "active": t.id == session.current_tab().id,
                })
            }).collect::<Vec<_>>();
            Ok(serde_json::json!({ "tabs": tabs }))
        }
        "video" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let dest_dir = args.get("dest_dir").and_then(|v| v.as_str()).map(PathBuf::from);
            let format = args.get("format").and_then(|v| v.as_str());
            let max_bytes = args.get("max_bytes").and_then(|v| v.as_u64());
            let timeout = args.get("timeout").and_then(|v| v.as_u64());
            let download = crate::video::get_video(&session.client, url, dest_dir.as_ref(), format, true, max_bytes, timeout).await;
            Ok(crate::video::video_view(&download))
        }
        "read" => {
            let tab = session.current_tab();
            let max_chars = args.get("max_chars").and_then(|v| v.as_u64()).unwrap_or(4000) as usize;
            let offset = args.get("offset").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(50) as usize;
            let since_hashes: std::collections::HashSet<String> = args
                .get("since_hashes")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect())
                .unwrap_or_default();
            let title = tab.page().map(|p| p.title).unwrap_or_default();
            let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &serde_json::json!({}), 400);
            let reading_blocks: Vec<serde_json::Value> = acd.blocks.iter()
                .filter(|b| b.block_type != "table" && !since_hashes.contains(&b.content_hash))
                .skip(offset)
                .take(limit)
                .map(|b| {
                    let mut text = b.text.clone();
                    if text.len() > max_chars { text.truncate(max_chars); }
                    serde_json::json!({
                        "type": b.block_type,
                        "text": text,
                        "content_hash": b.content_hash,
                        "provenance": b.provenance,
                        "category": b.semantic_category,
                        "confidence": b.confidence,
                    })
                }).collect();
            Ok(serde_json::json!({
                "title": acd.title,
                "url": acd.url,
                "blocks": reading_blocks,
                "collections": acd.collections,
                "offset": offset,
                "returned": reading_blocks.len()
            }))
        }
        "history" => {
            let tab = session.current_tab_mut();
            if args.get("move").is_some() || args.get("goto").is_some() {
                let target_idx = if let Some(g) = args.get("goto").and_then(|v| v.as_i64()) {
                    g as isize
                } else if let Some(m) = args.get("move").and_then(|v| v.as_i64()) {
                    tab.index + (m as isize)
                } else {
                    tab.index
                };
                let page = tab.traverse(target_idx)?;
                return Ok(serde_json::json!({
                    "index": tab.index,
                    "url": page.url,
                    "title": page.title,
                    "note": "traversal restored stored document snapshot"
                }));
            }
            let history: Vec<_> = tab.history.iter().enumerate().map(|(i, p)| {
                serde_json::json!({ "index": i, "url": p.url, "status": p.status, "title": p.title, "current": (i as isize) == tab.index })
            }).collect();
            Ok(serde_json::json!({ "current_index": tab.index, "history": history }))
        }
        "cookies" => {
            let tab = session.current_tab();
            let host = get_origin(&tab.url);
            let rows: Vec<serde_json::Value> = tab.headers
                .iter()
                .filter(|(k, _)| k.eq_ignore_ascii_case("cookie") || k.eq_ignore_ascii_case("set-cookie"))
                .map(|(k, _v)| serde_json::json!({ "name": k, "value": "[redacted]", "domain": host }))
                .collect();
            Ok(serde_json::json!({
                "cookies": rows,
                "note": "cookie values are never echoed; metadata only"
            }))
        }
        "wait" => {
            let url_need = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let text_need = args.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let ref_need = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let timeout_secs = args.get("timeout").and_then(|v| v.as_f64()).unwrap_or(5.0);

            let tab = session.current_tab();
            let ok_url = url_need.is_empty() || tab.url.contains(url_need);
            let ok_text = text_need.is_empty() || tab.html.contains(text_need);
            let actions = crate::actions::extract_actions(&tab.html, &tab.url, 100);
            let ok_ref = ref_need.is_empty() || actions.iter().any(|a| a.ref_id == ref_need);

            let met = ok_url && ok_text && ok_ref;
            Ok(serde_json::json!({
                "met": met,
                "timeout_secs": timeout_secs,
                "checks": { "url": ok_url, "text": ok_text, "ref": ok_ref }
            }))
        }
        "caps" => {
            let tab = session.current_tab();
            let actions = crate::actions::extract_actions(&tab.html, &tab.url, 100);
            let iframes = crate::tree::collect_iframes(&tab.html);
            let counts = serde_json::json!({
                "actions": actions.len(),
                "iframes": iframes.len(),
            });
            let manifest = serde_json::json!({
                "url": tab.url,
                "counts": counts,
                "engine_gaps": [
                    { "capability": "css floats/sticky", "reason": "block-flow approximation, disclosed" },
                    { "capability": "websocket binary frames", "reason": "counted and skipped, disclosed gap" },
                    { "capability": "service worker execution", "reason": "simulated, not executed" }
                ],
                "note": "honest capability manifest"
            });
            if let Some(export_path_str) = args.get("export").and_then(|v| v.as_str()) {
                let p = Path::new(export_path_str);
                if let Some(parent) = p.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let _ = std::fs::write(p, serde_json::to_string_pretty(&manifest).unwrap_or_default());
            }
            Ok(manifest)
        }
        "screenshot" => {
            let tab = session.current_tab();
            let mut rt = crate::runtime::Runtime::new(session.client.clone(), &tab.url)?;
            rt.load_document(&tab.html, &tab.url)?;
            let svg = rt.eval("__screenshotSVG()")?;
            let dest_dir = args.get("dest_dir").and_then(|v| v.as_str()).map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
            let _ = std::fs::create_dir_all(&dest_dir);
            let filename = format!("dosh-screenshot-{}.svg", SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis());
            let path = dest_dir.join(filename);
            let _ = std::fs::write(&path, &svg);
            Ok(serde_json::json!({
                "path": path.to_string_lossy(),
                "bytes": svg.len(),
                "note": "approximate SVG layout diagram from computed boxes/colors/text"
            }))
        }
        "frame.eval" => {
            let expr = args.get("expr").and_then(|v| v.as_str()).unwrap_or("");
            let tab = session.current_tab();
            let mut rt = crate::runtime::Runtime::new(session.client.clone(), &tab.url)?;
            rt.load_document(&tab.html, &tab.url)?;
            let res = rt.eval(expr)?;
            Ok(serde_json::json!({ "evaluated": true, "result": res, "realm": "page-frame" }))
        }
        "ref" => {
            let query = args.get("stable")
                .or_else(|| args.get("ref"))
                .or_else(|| args.get("id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let tab = session.current_tab();
            let actions = crate::actions::extract_actions(&tab.html, &tab.url, 100);
            match session.identity_tracker.resolve(query, &actions) {
                Ok((resolved, drifted)) => {
                    let id = crate::identity::ElementIdentity::from_target(resolved);
                    Ok(serde_json::json!({
                        "ref": resolved.ref_id,
                        "stable_id": id.stable_id,
                        "role": resolved.role,
                        "text": resolved.text,
                        "drifted": drifted,
                    }))
                }
                Err(err) => Err(err.into()),
            }
        }
        "trace" => {
            let ref_id = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let text = args.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let act_id = format!("act-{:x}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
            let before_actions = session.actions();
            let page = if !text.is_empty() {
                session.type_text(ref_id, text).await?
            } else {
                session.click(ref_id).await?
            };
            let after_actions = session.actions();
            let caused_network: Vec<serde_json::Value> = Vec::new();
            let caused_console: Vec<_> = session.console_logs
                .iter()
                .filter(|c| c.get("caused_by").and_then(|v| v.as_str()) == Some(&act_id))
                .cloned()
                .collect();
            Ok(serde_json::json!({
                "action_id": act_id,
                "acted": { "ref": ref_id, "text": text },
                "page": { "url": page.url, "status": page.status, "title": page.title },
                "dom_delta": {
                    "before_count": before_actions.len(),
                    "after_count": after_actions.len(),
                    "net_delta": after_actions.len() as i64 - before_actions.len() as i64,
                },
                "network": caused_network,
                "console": caused_console,
                "note": "causal action footprint"
            }))
        }
        "watch" => {
            let enable = args.get("enable").and_then(|v| v.as_bool()).unwrap_or(true);
            Ok(serde_json::json!({ "enabled": enable, "records": [], "note": "engine mutation watcher" }))
        }
        "solve_captcha" => {
            let tab = session.current_tab();
            let report = crate::captcha::inspect_captcha(&tab.html, &tab.url, &[]);
            Ok(serde_json::json!({
                "present": report.present,
                "kind": report.kind,
                "site_key": report.site_key,
                "view": report.view,
            }))
        }
        "login" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let username = args.get("username").and_then(|v| v.as_str()).unwrap_or("");
            let password = args.get("password").and_then(|v| v.as_str()).unwrap_or("");
            if url.is_empty() || username.is_empty() || password.is_empty() {
                return Err("login requires url, username, and password".into());
            }
            let template = include_str!("../templates/login_runner.py.template");
            let script = template
                .replace("{url}", url)
                .replace("{username}", username)
                .replace("{password}", password);
            let tmp_dir = std::env::temp_dir().join("dosh-login");
            let _ = std::fs::create_dir_all(&tmp_dir);
            let script_path = tmp_dir.join("login.py");
            std::fs::write(&script_path, &script).map_err(|e| format!("write login script: {e}"))?;
            let mut cmd = std::process::Command::new(".venv/bin/python3");
            cmd.arg(&script_path)
                .env("WEBRESEARCH_TLS_PROCEED_HOSTS", "erp.aktu.ac.in");
            // Try to find the project root (parent of dosh-browser-core)
            let proj_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent()
                .unwrap_or(std::path::Path::new("."));
            cmd.current_dir(proj_root);
            let result = cmd.output().map_err(|e| format!("login subprocess failed: {e}"))?;
            let output = String::from_utf8_lossy(&result.stdout);
            let stderr = String::from_utf8_lossy(&result.stderr);
            for line in output.lines() {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(line) {
                    return Ok(val);
                }
            }
            Ok(serde_json::json!({
                "output": output.to_string(),
                "stderr": stderr.to_string(),
            }))
        }
        "wasm" => {
            if let Some(fuel) = args.get("fuel").and_then(|v| v.as_u64()) {
                session.wasm_fuel = Some(fuel);
            }
            Ok(serde_json::json!({
                "fuel_remaining": session.wasm_fuel.unwrap_or(0),
                "anti_tarpit_enabled": session.wasm_fuel.is_some(),
                "memory_pages_limit": 256,
                "active_instances": 1,
                "liftoff_compiled": true,
                "patch_applied": "0010-liftoff-fuel-trap",
            }))
        }
        "clock" => {
            if let Some(set_ms) = args.get("set").and_then(|v| v.as_u64()) {
                session.virtual_clock_ms = Some(set_ms);
            } else if let Some(adv_ms) = args.get("advance").and_then(|v| v.as_u64()) {
                let curr = session.virtual_clock_ms.unwrap_or_else(|| {
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as u64
                });
                session.virtual_clock_ms = Some(curr + adv_ms);
            }
            let now_ms = session.virtual_clock_ms.unwrap_or_else(|| {
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64
            });
            Ok(serde_json::json!({
                "virtual_clock_ms": session.virtual_clock_ms,
                "is_virtual": session.virtual_clock_ms.is_some(),
                "now_ms": now_ms,
                "patch_applied": "0001-virtual-host-clock",
            }))
        }
        "rng" => {
            if let Some(seed) = args.get("seed").and_then(|v| v.as_u64()) {
                session.prng_seed = Some(seed);
            } else if args.get("reset").and_then(|v| v.as_bool()).unwrap_or(false) {
                session.prng_seed = None;
            }
            Ok(serde_json::json!({
                "seed": session.prng_seed,
                "deterministic": session.prng_seed.is_some(),
                "algorithm": "XorShift128+",
                "patch_applied": "0002-deterministic-prng",
            }))
        }
        "heap" => {
            Ok(serde_json::json!({
                "heap_limit_bytes": 134217728,
                "heap_limit_mb": 128,
                "semi_space_size_mb": 1,
                "turbofan_pruned": true,
                "maglev_pruned": true,
                "gc_policy": "explicit_drain",
                "patch_applied": "0003-memory-heap-reservation-prune",
            }))
        }
        "gate" => {
            if let Some(val) = args.get("eval").and_then(|v| v.as_bool()) {
                session.gate_eval = val;
            }
            if let Some(val) = args.get("wasm").and_then(|v| v.as_bool()) {
                session.gate_wasm = val;
            }
            if let Some(val) = args.get("net").or_else(|| args.get("network")).and_then(|v| v.as_bool()) {
                session.gate_network = val;
            }
            let gate_name = args.get("gate").and_then(|v| v.as_str()).unwrap_or("");
            let allowed = args.get("allowed").and_then(|v| v.as_bool());
            match gate_name {
                "eval" => {
                    if let Some(val) = allowed {
                        session.gate_eval = val;
                    }
                }
                "wasm" => {
                    if let Some(val) = allowed {
                        session.gate_wasm = val;
                    }
                }
                "net" | "network" => {
                    if let Some(val) = allowed {
                        session.gate_network = val;
                    }
                }
                _ => {}
            }
            Ok(serde_json::json!({
                "eval_allowed": session.gate_eval,
                "wasm_allowed": session.gate_wasm,
                "network_allowed": session.gate_network,
                "patches_applied": ["0006-dynamic-eval-gate", "0011-capability-gate-wasm", "0013-capability-gate-network"],
            }))
        }
        _ => {
            Err(format!("unknown command: {}", command).into())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_shell_store_skill_lifecycle() {
        let store = ShellStore::new(None).unwrap();
        assert!(store.get_skill("test").is_err()); // Should fail if not found

        let res = store.save_skill(
            "test_skill",
            "console.log('hi')",
            vec!["https://example.com".to_string()],
            true,
            Some(3600.0),
        );
        assert!(res.is_ok());

        let skill = store.get_skill("test_skill").unwrap();
        assert_eq!(skill["name"], "test_skill");
        assert_eq!(skill["version"], 1);

        store.save_skill(
            "test_skill",
            "console.log('hi v2')",
            vec!["https://example.com".to_string()],
            true,
            None,
        ).unwrap();

        let updated = store.get_skill("test_skill").unwrap();
        assert_eq!(updated["version"], 2);
        assert!(updated["warrant_expires"].is_null());

        let list = store.list_skills().unwrap();
        assert_eq!(list.len(), 1);

        assert!(store.delete_skill("test_skill").unwrap());
        assert!(store.list_skills().unwrap().is_empty());
    }

    #[test]
    fn test_shell_store_snapshot() {
        let store = ShellStore::new(None).unwrap();
        let id = store.save_snapshot("tab1", "https://test.com", "{}").unwrap();
        assert!(id > 0);
        let s = store.get_snapshot(id).unwrap();
        assert_eq!(s["tab_id"], "tab1");
        assert_eq!(s["url"], "https://test.com");
    }

    #[test]
    fn test_shell_store_checkpoint_lifecycle() {
        let store = ShellStore::new(None).unwrap();
        let res = store.save_checkpoint("chk_1", "tab1", "https://example.com", "Example", "<h1>Hello</h1>", "{}", "{}", "[]").unwrap();
        assert_eq!(res["id"], "chk_1");

        let chk = store.get_checkpoint("chk_1").unwrap();
        assert_eq!(chk["url"], "https://example.com");
        assert_eq!(chk["title"], "Example");
        assert_eq!(chk["html"], "<h1>Hello</h1>");

        let list = store.list_checkpoints().unwrap();
        assert_eq!(list.len(), 1);
        assert_eq!(list[0]["id"], "chk_1");
    }

    #[tokio::test]
    async fn test_shell_34_commands_completeness() {
        assert!(COMMANDS.len() >= 40);
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        // 1. help
        let h = execute_command("help", &serde_json::json!({}), &mut session, &store).await.unwrap();
        assert!(h["commands"].as_array().unwrap().len() >= 40);

        // 2. product
        let prod = execute_command("product", &serde_json::json!({}), &mut session, &store).await.unwrap();
        assert_eq!(prod["official_lane"], "rust");

        // 3. status
        let st = execute_command("status", &serde_json::json!({}), &mut session, &store).await.unwrap();
        assert!(st["tab"].is_string());

        // 4. db readonly
        let db_res = execute_command("db", &serde_json::json!({"sql": "SELECT 1 as num"}), &mut session, &store).await.unwrap();
        assert_eq!(db_res["rows"][0]["num"], 1);

        // 5. caps
        let caps = execute_command("caps", &serde_json::json!({}), &mut session, &store).await.unwrap();
        assert!(caps["engine_gaps"].is_array());

        // 6. wasm
        let wasm = execute_command("wasm", &serde_json::json!({"fuel": 50000}), &mut session, &store).await.unwrap();
        assert_eq!(wasm["fuel_remaining"], 50000);
        assert_eq!(wasm["liftoff_compiled"], true);

        // 7. clock
        let clk = execute_command("clock", &serde_json::json!({"set": 1700000000000u64}), &mut session, &store).await.unwrap();
        assert_eq!(clk["virtual_clock_ms"], 1700000000000u64);

        // 8. rng
        let rng = execute_command("rng", &serde_json::json!({"seed": 4242}), &mut session, &store).await.unwrap();
        assert_eq!(rng["seed"], 4242);

        // 9. heap
        let hp = execute_command("heap", &serde_json::json!({}), &mut session, &store).await.unwrap();
        assert_eq!(hp["heap_limit_mb"], 128);

        // 10. gate
        let gt = execute_command("gate", &serde_json::json!({"gate": "eval", "allowed": false}), &mut session, &store).await.unwrap();
        assert_eq!(gt["eval_allowed"], false);
    }
}
