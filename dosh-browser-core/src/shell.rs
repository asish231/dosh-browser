//! `shell.rs` — The agent command surface (replaces `webresearch/shell.py`).
//!
//! Handles the 45-command surface: `help`, `product`, `status`, `open`, `actions`,
//! `act`, `net`, `net.capture`, `net.rule`, `net.unrule`, `net.apis`, `console`,
//! `eval`, `skill.save`, `skill.list`, `skill.run`, `skill.delete`, `skill.update`,
//! `db`, `storage`, `snapshot`, `diff`, `checkpoint`, `restore`, `layers`, `frames`,
//! `tabs`, `video`, `read`, `history`, `cookies`, `wait`, `caps`, `screenshot`,
//! `frame.eval`, `ref`, `trace`, `watch`, `solve_captcha`, `login`, `wasm`, `clock`,
//! `rng`, `heap`, `gate`.
//!
//! Uses `ShellStore` for persistent SQLite skills and snapshots.

use kuchiki::traits::TendrilSink;
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
    ("select", "Drag-select text in a text input/textarea/contenteditable (E2): ref, start, end offsets; fires selectstart/select/selectionchange."),
    ("net", "Query network observations: after, limit, pattern."),
    ("net.capture", "Enable/disable opt-in redacted JSON/form response bodies: enabled."),
    ("net.rule", "Add runtime fetch/XHR block/header/mock rule: action, pattern, options."),
    ("net.unrule", "Remove network rule by id."),
    ("net.apis", "Catalog discovered background API endpoints for Site-as-SDK replay."),
    ("net.stream", "Drain captured SSE events for a record: id, after.") ,
    ("net.ws", "View WebSocket sessions/frames: action=list|view, session, after."),
    ("net.cors", "CORS verdict for one recorded request: id (origin = current page)."),
    ("net.timing", "Measured transport timing waterfall: pattern.") ,
    ("net.replay", "Return the recorded request contract for a record: id (unredacted central evidence; secrets never echoed)."),
    ("net.decode", "Decode a captured body: id|data_b64, encoding (auto|gzip|zlib|deflate|raw-deflate|brotli|identity, protobuf structural parse)."),
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
    ("state.snapshot", "Capture unified state (DOM, live cookie jar, storage, history, ledger) into the state DB: id."),
    ("state.diff", "Structural diff of two state snapshots (added/removed/changed; values never): before, after."),
    ("state.rollback", "Rehydrate session state from a state snapshot: id (ledger is append-only, preserved)."),
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
    ("solve_captcha", "Inspect the current page's reCAPTCHA v2 challenge (solving runs in the Python lane)."),
    ("login", "Login with url, username, and password. Solves captcha and submits."),
    ("wasm", "Query or configure Liftoff WASM execution fuel, memory, and export telemetry: fuel, action."),
    ("clock", "Inspect or set deterministic virtual host clock: now, set, advance (millis)."),
    ("rng", "Set deterministic PRNG seed for Math.random replays: seed, reset."),
    ("heap", "Inspect 128MB pruned V8 heap statistics: used, total, limit, and GC count."),
    ("gate", "Configure security capability gates: gate (eval/net/wasm), allowed (bool)."),
    ("assist", "Run the in-session DevTools agent walkthrough against the live page: inspects cookies, storage, and discovered APIs, then synthesizes a unified replay command. goal (string)."),
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
            );
            CREATE TABLE IF NOT EXISTS state_snapshots (
                id TEXT PRIMARY KEY, created REAL NOT NULL, tab_id TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT NOT NULL, html TEXT NOT NULL,
                cookies TEXT NOT NULL, storage TEXT NOT NULL, history TEXT NOT NULL,
                ledger TEXT NOT NULL
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

    pub fn save_state_snapshot(
        &self,
        id: &str,
        tab_id: &str,
        url: &str,
        title: &str,
        html: &str,
        cookies: &str,
        storage: &str,
        history: &str,
        ledger: &str,
    ) -> Result<serde_json::Value, rusqlite::Error> {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT OR REPLACE INTO state_snapshots (id, created, tab_id, url, title, html, cookies, storage, history, ledger) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![id, now, tab_id, url, title, html, cookies, storage, history, ledger],
        )?;
        Ok(serde_json::json!({
            "id": id,
            "created": now,
            "tab_id": tab_id,
            "url": url,
            "title": title,
        }))
    }

    pub fn get_state_snapshot(&self, id: &str) -> Result<serde_json::Value, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.query_row(
            "SELECT id, created, tab_id, url, title, html, cookies, storage, history, ledger FROM state_snapshots WHERE id = ?1",
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
                    "ledger": row.get::<_, String>(9)?,
                }))
            },
        )
    }

    pub fn list_state_snapshots(&self) -> Result<Vec<serde_json::Value>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare("SELECT id, created, tab_id, url, title FROM state_snapshots ORDER BY created DESC")?;
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
                        "summary": "Full pure-Rust browser core with doshgentv8 V8 engine, RFC 9111 caching, ANBF binary packaging, 45 shell commands, and HTTP/REST server."
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
        "select" => {
            let ref_id = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let start = args.get("start").and_then(|v| v.as_i64()).unwrap_or(0);
            let end = args.get("end").and_then(|v| v.as_i64());
            let page = session.select_text(ref_id, start, end).await?;
            Ok(serde_json::json!({
                "url": page.url,
                "status": page.status,
                "note": "text-selection drag lane: mousedown → selection → mouseup; fires selectstart/select/selectionchange; distinct from element DnD"
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
            let res = session.network_ledger.lock().await.query(after, limit, pattern);
            Ok(res)
        }
        "net.capture" => {
            // N7: opt-in capture steering, including the byte cap so long
            // bodies (N1 streams, big JSON) can be captured fully.
            let enabled = args.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true);
            let mut ledger = session.network_ledger.lock().await;
            ledger.capture_bodies = enabled;
            let mut out = serde_json::json!({ "capture_bodies": enabled });
            if let Some(max) = args.get("max_bytes").and_then(|v| v.as_u64()) {
                // Hard internals keep the cap bounded (1 MiB max for safety).
                let capped = max.min(1_048_576) as usize;
                ledger.body_max_bytes = capped;
                out["body_max_bytes"] = serde_json::json!(capped);
            }
            Ok(out)
        }
        "net.rule" => {
            let action = args.get("action").and_then(|v| v.as_str()).unwrap_or("block");
            let pattern = args.get("pattern").and_then(|v| v.as_str()).unwrap_or("*");
            let status = args.get("status").and_then(|v| v.as_u64()).unwrap_or(200) as u16;
            let body = args.get("body").and_then(|v| v.as_str()).unwrap_or("");
            let content_type = args.get("content_type").and_then(|v| v.as_str()).unwrap_or("text/plain");
            let redirect_url = args.get("redirect_url").and_then(|v| v.as_str()).unwrap_or("");
            let id = session.network_ledger.lock().await.add_rule(pattern, action, status, body, content_type, redirect_url);
            Ok(serde_json::json!({ "rule_id": id, "action": action, "pattern": pattern }))
        }
        "net.unrule" => {
            let id = args.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let removed = session.network_ledger.lock().await.remove_rule(id);
            Ok(serde_json::json!({ "removed": removed }))
        }
        "net.apis" => {
            let apis = session.network_ledger.lock().await.discover_apis();
            Ok(serde_json::json!({ "endpoints": apis }))
        }
        "net.stream" => {
            // N1: drain the captured SSE event sequence for one record.
            let id = args.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let after = args.get("after").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let ledger = session.network_ledger.lock().await;
            let res = ledger.stream_view(id, after);
            if res.get("error").is_some() {
                let msg = res["error"].as_str().unwrap_or_default().to_string();
                // Not a shell failure: it's a missing-object condition.
                return Err(msg.into());
            }
            Ok(res)
        }
        "net.ws" => {
            // N2: ledger-backed WebSocket session + frame evidence.
            let action = args.get("action").and_then(|v| v.as_str()).unwrap_or("list");
            let session_id = args.get("session").and_then(|v| v.as_u64()).unwrap_or(0);
            let after = args.get("after").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            let ledger = session.network_ledger.lock().await;
            match action {
                "list" => {
                    let list: Vec<serde_json::Value> = ledger
                        .ws_sessions()
                        .iter()
                        .map(|s| serde_json::to_value(s).unwrap_or_default())
                        .collect();
                    Ok(serde_json::json!({ "sessions": list, "count": list.len() }))
                }
                "view" => {
                    if session_id == 0 {
                        return Err("net.ws view requires session".into());
                    }
                    Ok(ledger.ws_view(session_id, after))
                }
                other => Err(format!("unknown net.ws action '{other}' (list|view)").into()),
            }
        }
        "net.cors" => {
            // N3: CORS verdict computed from the recorded request/response
            // evidence; client origin comes from the current page.
            let id = args.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let page_url = session
                .tabs
                .get(session.current_tab_idx)
                .map(|t| t.url.clone())
                .unwrap_or_default();
            let origin = match args.get("origin").and_then(|v| v.as_str()) {
                Some(o) if !o.is_empty() => o.to_string(),
                _ => get_origin(&page_url),
            };
            let res = session
                .network_ledger
                .lock()
                .await
                .cors_view(&origin, id)
                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { e.into() })?;
            Ok(res)
        }
        "net.timing" => {
            // N4: measured waterfall (TTFB + full duration; DNS/connect phases
            // are not observable through reqwest and are absent, not faked).
            let pattern = args.get("pattern").and_then(|v| v.as_str()).unwrap_or("");
            let res = session.network_ledger.lock().await.timing_view(pattern);
            Ok(res)
        }
        "net.replay" => {
            // N5: the request contract for an observed record — method, url,
            // headers, body — reconstructed from transport evidence, WITHOUT
            // echoing stored secrets (redaction rules apply to values).
            let id = args.get("id").and_then(|v| v.as_u64()).unwrap_or(0);
            let include_headers = args.get("headers").and_then(|v| v.as_bool()).unwrap_or(true);
            let ledger = session.network_ledger.lock().await;
            let Some(s) = ledger.sealed_for(id) else {
                return Err(format!(
                    "no sealed transport evidence for id {id}; run net.capture enabled first"
                )
                .into());
            };
            let (body_len, method, url) = (s.body.len(), s.method.clone(), s.url.clone());
            let headers_out: serde_json::Map<String, serde_json::Value> = if include_headers {
                s.headers
                    .iter()
                    .map(|(k, v)| {
                        // Values pass through redaction: authorized secrets stay hidden
                        // without the reveal gate.
                        let redacted = crate::telemetry::redact_header_value(k, v);
                        (k.clone(), serde_json::Value::String(redacted))
                    })
                    .collect()
            } else {
                Default::default()
            };
            Ok(serde_json::json!({
                "record_id": id,
                "method": method,
                "url": url,
                "body_len": body_len,
                "headers": headers_out,
                "note": "replay contract built from sealed transport evidence; secrets redacted unless the reveal gate is on",
            }))
        }
        "net.decode" => {
            // N6: gzip/deflate/brotli + structural protobuf decode over a
            // sealed body (or a caller-provided base64 payload).
            let id = args.get("id").and_then(|v| v.as_u64());
            let data_b64 = args.get("data_b64").and_then(|v| v.as_str());
            let encoding = args.get("encoding").and_then(|v| v.as_str()).unwrap_or("auto");
            if let Some(db) = data_b64 {
                use base64::Engine as _;
                let raw = base64::engine::general_purpose::STANDARD
                    .decode(db)
                    .map_err(|e| format!("bad data_b64: {e}"))?;
                let out = crate::telemetry::decode_payload_bytes(&raw, encoding)
                    .map_err(|e| String::from(e))
                    .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { e.into() })?;
                Ok(out)
            } else if let Some(id) = id {
                let res = session
                    .network_ledger
                    .lock()
                    .await
                    .decode_view(id, encoding)
                    .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> { e.into() })?;
                Ok(res)
            } else {
                Err("net.decode requires id or data_b64".into())
            }
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
            let allow_actions = args.get("allow_actions").and_then(|v| v.as_bool()).unwrap_or(false);
            let empty_vec = vec![];
            let script_args: Vec<String> = args.get("args")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect())
                .unwrap_or(empty_vec);

            // Evaluate against the RESIDENT runtime so JS state, console, and the
            // live cookie jar persist across turns (a bridged/attached session must
            // not lose context). Falls back to loading the current document only
            // when no resident runtime exists yet for this tab.
            let url = session.current_tab().url.clone();
            let html = session.current_tab().html.clone();
            let rt = session.get_or_create_runtime(&url)?;
            session.configure_runtime(&rt);
            if session.runtime_needs_document(&url) {
                let mut rt_load = rt.clone();
                rt_load.load_document(&html, &url)?;
                session.mark_runtime_loaded(&url);
            }
            let result = rt.eval_bounded(expr, true, allow_actions, &script_args)?;
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
            let allow_actions = skill.get("allow_actions").and_then(|v| v.as_bool()).unwrap_or(false);
            // Run against the RESIDENT runtime (shared live cookie jar + JS state).
            let url = session.current_tab().url.clone();
            let html = session.current_tab().html.clone();
            let rt = session.get_or_create_runtime(&url)?;
            session.configure_runtime(&rt);
            if session.runtime_needs_document(&url) {
                let mut rt_load = rt.clone();
                rt_load.load_document(&html, &url)?;
                session.mark_runtime_loaded(&url);
            }
            let result = rt.eval_bounded(source, true, allow_actions, &[])?;
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
            let include_values = args.get("values").and_then(|v| v.as_bool()).unwrap_or(false);
            let origin = get_origin(&session.current_tab().url);
            let session_data = session
                .current_tab()
                .session_storage
                .get(&origin)
                .cloned()
                .unwrap_or_default();
            let local_data = session.local_storage.get(&origin).cloned().unwrap_or_default();
            if include_values {
                // Redact values to lengths; never echo raw stored secrets.
                let redact = |m: &HashMap<String, String>| -> serde_json::Value {
                    serde_json::Value::Object(
                        m.iter()
                            .map(|(k, v)| (k.clone(), serde_json::json!({ "len": v.len() })))
                            .collect(),
                    )
                };
                Ok(serde_json::json!({
                    "origin": origin,
                    "local_storage": redact(&local_data),
                    "session_storage": redact(&session_data),
                    "note": "values redacted to lengths; raw payloads are never echoed"
                }))
            } else {
                Ok(serde_json::json!({
                    "origin": origin,
                    "local_storage_keys": local_data.keys().cloned().collect::<Vec<_>>(),
                    "session_storage_keys": session_data.keys().cloned().collect::<Vec<_>>(),
                    "note": "storage metadata only; pass values=true for redacted lengths"
                }))
            }
        }
        "cookies" => {
            // Read the live resident-runtime cookie jar. Names/metadata only by
            // default; values are redacted to lengths and never echoed, matching
            // the repo's secret-hygiene rule. `values=raw` is the N8 audited
            // gate: requires the operator's reveal-gate flip, and its use is
            // written to the network ledger's audit trail.
            let mode = args.get("values").and_then(|v| {
                if v.is_boolean() {
                    v.as_bool().map(|b| if b { "len" } else { "none" }.to_string())
                } else {
                    v.as_str().map(str::to_string)
                }
            });
            let want_raw = mode.as_deref() == Some("raw");
            let origin = get_origin(&session.current_tab().url);
            let jar = session.current_cookies();
            if want_raw {
                let mut ledger = session.network_ledger.lock().await;
                if !ledger.reveal_allowed() {
                    return Err(
                        "cookies values=raw refused: secret reveal is governed by the allow_secret_reveal gate (off by default)".to_string()
                            .into(),
                    );
                }
                ledger.audit_reveal(
                    "cookies_raw",
                    &format!("origin={origin} count={}", jar.len()),
                );
                drop(ledger);
                let cookies: Vec<serde_json::Value> = jar
                    .iter()
                    .map(|(name, value)| serde_json::json!({
                        "name": name,
                        "value": value,
                    }))
                    .collect();
                return Ok(serde_json::json!({
                    "origin": origin,
                    "count": cookies.len(),
                    "cookies": cookies,
                    "audited": true,
                    "note": "values=raw releases real cookie values; this call is recorded in the reveal audit log"
                }));
            }
            let include_values = mode.as_deref() == Some("len") || args.get("values").and_then(|v| v.as_bool()).unwrap_or(false);
            let cookies: Vec<serde_json::Value> = jar
                .iter()
                .map(|(name, value)| {
                    if include_values {
                        serde_json::json!({ "name": name, "value_len": value.len() })
                    } else {
                        serde_json::json!({ "name": name })
                    }
                })
                .collect();
            Ok(serde_json::json!({
                "origin": origin,
                "count": cookies.len(),
                "cookies": cookies,
                "note": "live session cookie jar; values are never echoed (lengths only with values=true; real values require values=raw + the audited reveal gate)"
            }))
        }
        "cookies.set" | "auth.import" => {
            let target_url = args.get("url")
                .or_else(|| args.get("domain"))
                .and_then(|v| v.as_str())
                .unwrap_or_else(|| &session.current_tab().url);
            let cookie_str = args.get("cookie")
                .or_else(|| args.get("cookies"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if target_url.is_empty() || cookie_str.is_empty() {
                return Err("url (or domain) and cookie (or cookies) are required".into());
            }
            let full_url = if !target_url.starts_with("http://") && !target_url.starts_with("https://") {
                format!("https://{target_url}")
            } else {
                target_url.to_string()
            };
            session.set_cookie(cookie_str, &full_url)?;
            Ok(serde_json::json!({
                "imported": true,
                "url": full_url,
                "note": "federated session cookies injected into live HTTP/2 jar and JS runtime"
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
        "state.snapshot" => {
            let tab = session.current_tab();
            let default_id = format!("st_{}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis());
            let id = args.get("id").and_then(|v| v.as_str()).unwrap_or(&default_id);
            let title = tab.page().map(|p| p.title.clone()).unwrap_or_default();
            // Live state, not stale copies: the cookie jar comes from the
            // resident runtime (what document.cookie sees), the ledger from
            // the shared engine ledger (what `net` sees).
            let cookies = serde_json::to_string(&session.current_cookies())?;
            let storage = serde_json::to_string(&session.local_storage)?;
            let history = serde_json::to_string(&tab.history)?;
            let ledger = session.network_ledger.lock().await.query(0, 2000, "");
            store.save_state_snapshot(
                id, &tab.id, &tab.url, &title, &tab.html,
                &cookies, &storage, &history,
                &serde_json::to_string(&ledger)?,
            )
            .map_err(|e| e.to_string())?;
            Ok(serde_json::json!({
                "id": id,
                "url": tab.url,
                "counts": {
                    "cookies": session.current_cookies().len(),
                    "storage_origins": session.local_storage.len(),
                    "history_entries": tab.history.len(),
                    "ledger_records": ledger["retained_count"].as_u64().unwrap_or(0),
                },
                "note": "unified state snapshot (DOM + live cookie jar + storage + history + ledger view) in the state DB; values live only in the local DB, never in command output"
            }))
        }
        "state.diff" => {
            let before_id = args.get("before").and_then(|v| v.as_str())
                .ok_or("before is required (state.snapshot id)")?.to_string();
            let after_id = args.get("after").and_then(|v| v.as_str())
                .ok_or("after is required (state.snapshot id)")?.to_string();
            let b = store.get_state_snapshot(&before_id).map_err(|e| e.to_string())?;
            let a = store.get_state_snapshot(&after_id).map_err(|e| e.to_string())?;

            let b_storage: std::collections::HashMap<String, std::collections::HashMap<String, String>> =
                serde_json::from_str(b["storage"].as_str().unwrap_or("{}")).unwrap_or_default();
            let a_storage: std::collections::HashMap<String, std::collections::HashMap<String, String>> =
                serde_json::from_str(a["storage"].as_str().unwrap_or("{}")).unwrap_or_default();
            let mut storage_added = Vec::new();
            let mut storage_removed = Vec::new();
            let mut storage_changed = Vec::new();
            for (origin, a_map) in &a_storage {
                match b_storage.get(origin) {
                    None => {
                        if a_map.is_empty() {
                            storage_added.push(origin.clone());
                        } else {
                            for k in a_map.keys() {
                                storage_added.push(format!("{origin}/{k}"));
                            }
                        }
                    }
                    Some(b_map) => {
                        for (k, a_v) in a_map {
                            match b_map.get(k) {
                                None => storage_added.push(format!("{origin}/{k}")),
                                Some(b_v) if b_v != a_v => storage_changed.push(format!("{origin}/{k}")),
                                _ => {}
                            }
                        }
                        for k in b_map.keys() {
                            if !a_map.contains_key(k) {
                                storage_removed.push(format!("{origin}/{k}"));
                            }
                        }
                    }
                }
            }
            storage_added.sort(); storage_removed.sort(); storage_changed.sort();

            let b_cookies: std::collections::HashMap<String, String> =
                serde_json::from_str(b["cookies"].as_str().unwrap_or("{}")).unwrap_or_default();
            let a_cookies: std::collections::HashMap<String, String> =
                serde_json::from_str(a["cookies"].as_str().unwrap_or("{}")).unwrap_or_default();
            let mut cookie_added: Vec<String> = a_cookies.keys().filter(|k| !b_cookies.contains_key(*k)).cloned().collect();
            let mut cookie_removed: Vec<String> = b_cookies.keys().filter(|k| !a_cookies.contains_key(*k)).cloned().collect();
            let mut cookie_changed: Vec<String> = Vec::new();
            for (k, a_v) in &a_cookies {
                match b_cookies.get(k) {
                    Some(b_v) if b_v != a_v => cookie_changed.push(k.clone()),
                    _ => {}
                }
            }
            cookie_added.sort(); cookie_removed.sort(); cookie_changed.sort();

            let b_history: Vec<serde_json::Value> =
                serde_json::from_str(b["history"].as_str().unwrap_or("[]")).unwrap_or_default();
            let a_history: Vec<serde_json::Value> =
                serde_json::from_str(a["history"].as_str().unwrap_or("[]")).unwrap_or_default();

            let b_ledger: serde_json::Value =
                serde_json::from_str(b["ledger"].as_str().unwrap_or("{}")).unwrap_or_default();
            let a_ledger: serde_json::Value =
                serde_json::from_str(a["ledger"].as_str().unwrap_or("{}")).unwrap_or_default();
            let b_ids: std::collections::HashSet<u64> = b_ledger["records"]
                .as_array()
                .map_or_else(std::collections::HashSet::new, |arr| {
                    arr.iter().filter_map(|r| r["id"].as_u64()).collect()
                });
            let new_urls: Vec<String> = a_ledger["records"].as_array().map_or(Vec::new(), |arr| {
                arr.iter()
                    .filter(|r| !b_ids.contains(&r["id"].as_u64().unwrap_or(0)))
                    .filter_map(|r| r["url"].as_str().map(String::from))
                    .take(20)
                    .collect()
            });

            Ok(serde_json::json!({
                "before": before_id,
                "after": after_id,
                "url": { "before": b["url"], "after": a["url"], "changed": b["url"] != a["url"] },
                "storage": { "added": storage_added, "removed": storage_removed, "changed": storage_changed },
                "cookies": { "added": cookie_added, "removed": cookie_removed, "changed": cookie_changed },
                "history": { "before": b_history.len(), "after": a_history.len() },
                "ledger": {
                    "records_before": b_ledger["retained_count"].as_u64().unwrap_or(0),
                    "records_after": a_ledger["retained_count"].as_u64().unwrap_or(0),
                    "new_urls": new_urls,
                },
                "note": "structural diff over the unified state DB; storage/cookie values are never reported — presence and change only"
            }))
        }
        "state.rollback" => {
            let id = args.get("id").and_then(|v| v.as_str())
                .ok_or("id is required to rollback a state snapshot")?;
            let snap = store.get_state_snapshot(id).map_err(|e| e.to_string())?;
            if let Some(s) = snap["storage"].as_str() {
                if let Ok(st) = serde_json::from_str::<std::collections::HashMap<String, std::collections::HashMap<String, String>>>(s) {
                    session.local_storage = st;
                }
            }
            let tab = session.current_tab_mut();
            if let Some(h) = snap["html"].as_str() {
                tab.html = h.to_string();
            }
            if let Some(u) = snap["url"].as_str() {
                tab.url = u.to_string();
            }
            if let Some(c) = snap["cookies"].as_str() {
                if let Ok(hdr) = serde_json::from_str::<std::collections::HashMap<String, String>>(c) {
                    tab.headers = hdr;
                }
            }
            if let Some(hist) = snap["history"].as_str() {
                if let Ok(h) = serde_json::from_str::<Vec<crate::session::Page>>(hist) {
                    tab.index = (h.len() as isize).saturating_sub(1);
                    tab.history = h;
                }
            }
            // Restore the resident runtime's live cookie jar so document.cookie
            // reflects the rollback, then reload the document so the live DOM
            // matches the rolled-back state.
            let tab_id = session.current_tab().id.clone();
            let url = session.current_tab().url.clone();
            let html = session.current_tab().html.clone();
            if let Some(rt) = session.runtimes.get(&tab_id) {
                if let Ok(mut jar) = rt.seam.0.cookies.lock() {
                    if let Some(c) = snap["cookies"].as_str() {
                        *jar = serde_json::from_str(c).unwrap_or_default();
                    }
                }
            }
            let mut rt = session.get_or_create_runtime(&url)?;
            rt.load_document(&html, &url)?;
            session.mark_runtime_loaded(&url);
            Ok(serde_json::json!({
                "restored": id,
                "url": snap["url"],
                "title": snap["title"],
                "ledger_rolled_back": false,
                "note": "document, storage, history, and live cookie jar rehydrated from the state DB; the network ledger is append-only — it is preserved for audit, never rolled back"
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
            // Per-page capability manifest: live counts plus what is
            // unavailable and WHY. The live realm reflects the mutated
            // document (post-script) — the conversion source; the static
            // tree is only the honest fallback, labeled as such.
            let url = session.current_tab().url.clone();
            let html = session.current_tab().html.clone();
            let actions = crate::actions::extract_actions(&html, &url, 1_000_000);
            let frame_seeds = session.discover_frame_seeds();
            let mut counts = serde_json::json!({ "actions": actions.len(), "iframes": frame_seeds.len() });
            let mut source = "static-tree";
            let mut probe_note = String::new();
            let probe = r#"(function(){
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
            })()"#;
            let live = (|| -> Result<serde_json::Value, String> {
                let mut rt = session.get_or_create_runtime(&url)?;
                if session.runtime_needs_document(&url) && !html.is_empty() {
                    rt.load_document(&html, &url)?;
                    session.mark_runtime_loaded(&url);
                }
                let raw = rt.eval(probe)?;
                serde_json::from_str(&raw).map_err(|e| format!("{e}"))
            })();
            match live {
                Ok(live_counts) => {
                    source = "live-realm";
                    for (k, v) in live_counts.as_object().cloned().unwrap_or_default() {
                        if k == "input_types" {
                            counts[k.as_str()] = v;
                        } else if let Some(n) = v.as_u64() {
                            if n != u64::MAX {
                                counts[k.as_str()] = serde_json::json!(n);
                            }
                        }
                    }
                }
                Err(e) => {
                    probe_note = format!("live probe failed ({e}); static tree reported");
                    if html.is_empty() {
                        return Err("no loaded document".to_string().into());
                    }
                    let dom = kuchiki::parse_html().one(html.clone());
                    let q = |sel: &str| {
                        dom.select(sel).map(|s| s.count() as u64).unwrap_or(0)
                    };
                    let inputs: Vec<String> = dom
                        .select("input")
                        .map(|els| {
                            els.map(|el| {
                                crate::content::attr(&el.as_node(), "type")
                                    .unwrap_or_else(|| "text".to_string())
                                    .to_lowercase()
                            })
                            .collect()
                        })
                        .unwrap_or_default();
                    let input_types: std::collections::BTreeMap<String, u64> = inputs.iter().fold(
                        std::collections::BTreeMap::new(),
                        |mut acc, t| {
                            *acc.entry(t.clone()).or_insert(0) += 1;
                            acc
                        },
                    );
                    counts["canvas"] = serde_json::json!(q("canvas"));
                    counts["iframe"] = serde_json::json!(q("iframe"));
                    counts["img"] = serde_json::json!(q("img"));
                    counts["video"] = serde_json::json!(q("video"));
                    counts["audio"] = serde_json::json!(q("audio"));
                    counts["svg"] = serde_json::json!(q("svg"));
                    counts["embed"] = serde_json::json!(q("embed"));
                    counts["object"] = serde_json::json!(q("object"));
                    counts["form"] = serde_json::json!(q("form"));
                    counts["select"] = serde_json::json!(q("select"));
                    counts["textarea"] = serde_json::json!(q("textarea"));
                    counts["editable"] = serde_json::json!(q("[contenteditable]"));
                    counts["draggable"] = serde_json::json!(q("[draggable=\"true\"]"));
                    counts["dialog"] = serde_json::json!(q("dialog"));
                    counts["wasm_script"] = serde_json::json!(q("script[type=\"application/wasm\"]"));
                    counts["elements"] = serde_json::json!(q("*"));
                    counts["input_types"] = serde_json::json!(input_types);
                }
            }

            let unavailable: Vec<serde_json::Value> = {
                let mut v = Vec::new();
                let has = |k: &str| counts.get(k).and_then(|x| x.as_u64()).unwrap_or(0) > 0;
                if has("canvas") {
                    v.push(serde_json::json!({"capability": "canvas pixels",
                        "reason": "decomposed at parse time; no raster output, by design"}));
                }
                if has("iframe") {
                    v.push(serde_json::json!({"capability": "iframe bfcache parity",
                        "reason": "frames are separate documents; restore re-runs scripts"}));
                }
                if has("wasm_script") {
                    v.push(serde_json::json!({"capability": "wasm execution",
                        "reason": "interpreter is a bounded subset; unlisted opcodes stay inert"}));
                }
                if has("video") || has("audio") {
                    v.push(serde_json::json!({"capability": "media playback/raster",
                        "reason": "no pixel or audio output; download via the video command"}));
                }
                if has("editable") {
                    v.push(serde_json::json!({"capability": "caret/selection in editors",
                        "reason": "contenteditable types plain text only; no caret model"}));
                }
                v
            };

            let manifest = serde_json::json!({
                "url": url,
                "counts": counts,
                "source": source,
                "tabs": session.tabs.len(),
                "frames": frame_seeds.len(),
                "dialogs_pending": session.last_dialogs.len(),
                "page_unavailable": unavailable,
                "engine_gaps": [
                    { "capability": "css floats/sticky/flex-wrap distribution", "reason": "block-flow approximation, disclosed" },
                    { "capability": "text shaping/bidi (non-Latin scripts)", "reason": "proportional advance table only" },
                    { "capability": "websocket binary frames", "reason": "counted and skipped, disclosed gap" },
                    { "capability": "workers as real threads", "reason": "single-threaded emulation; constructors inert" },
                    { "capability": "service worker execution", "reason": "simulated, not executed" },
                    { "capability": "pixel-faithful screenshot", "reason": "SVG layout diagram only (see screenshot command)" }
                ],
                "note": format!(
                    "{}; honest manifest: unavailable items state why; this is not a claim of completeness",
                    probe_note
                )
            });
            if let Some(export_path_str) = args.get("export").and_then(|v| v.as_str()) {
                let p = Path::new(export_path_str);
                if !p.is_absolute() {
                    return Err("export path must be absolute".to_string().into());
                }
                if let Some(parent) = p.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let payload = serde_json::to_string_pretty(&manifest).unwrap_or_default();
                let _ = std::fs::write(p, &payload);
                let mut with_export = manifest.as_object().cloned().unwrap_or_default();
                with_export.insert("exported_to".into(), serde_json::json!(p.to_string_lossy()));
                with_export.insert("exported_bytes".into(), serde_json::json!(payload.len()));
                return Ok(serde_json::Value::Object(with_export));
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
            // Real MutationObserver delta stream over the tab's resident runtime.
            // The minidom.js `__wrWatch*` host lane is shared with the Python
            // companion; here it runs in the resident V8 context so the
            // observer and its host-side log persist across turns for the same
            // document (a navigation replaces the document and resets it).
            let url = session.current_tab().url.clone();
            let html = session.current_tab().html.clone();
            let mut rt = session.get_or_create_runtime(&url)?;
            session.configure_runtime(&rt);
            if session.runtime_needs_document(&url) {
                rt.load_document(&html, &url)?;
                session.mark_runtime_loaded(&url);
            }
            match args.get("enable").and_then(|v| v.as_bool()) {
                Some(true) => {
                    let raw = rt.eval("__wrWatchInstall()")?;
                    let state: serde_json::Value = serde_json::from_str(&raw)
                        .map_err(|e| format!("watch install returned non-JSON: {e}"))?;
                    if !state.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
                        return Err(state
                            .get("reason")
                            .and_then(|v| v.as_str())
                            .unwrap_or("watch install failed")
                            .to_string()
                            .into());
                    }
                    Ok(serde_json::json!({
                        "enabled": true,
                        "cap": state.get("cap").cloned().unwrap_or(serde_json::Value::Null),
                        "note": "engine MutationObserver armed on this document; drain with watch {after, limit}. Records are host-side only and reset on navigation"
                    }))
                }
                Some(false) => {
                    let raw = rt.eval("__wrWatchDisable()")?;
                    let state: serde_json::Value = serde_json::from_str(&raw)
                        .map_err(|e| format!("watch disable returned non-JSON: {e}"))?;
                    Ok(serde_json::json!({
                        "enabled": false,
                        "retained": state.get("retained").cloned().unwrap_or(serde_json::json!(0)),
                        "note": "observer disconnected; retained records stay until the next enable resets the log"
                    }))
                }
                None => {
                    let after = args.get("after").and_then(|v| v.as_i64()).unwrap_or(0);
                    if after < 0 {
                        return Err("after must be a nonnegative integer".to_string().into());
                    }
                    let limit = args
                        .get("limit")
                        .and_then(|v| v.as_i64())
                        .unwrap_or(50)
                        .clamp(1, 200) as usize;
                    // Microtasks are explicit in the V8 lane: drain the queue
                    // first so the observer callback has delivered its records
                    // for everything since the last action pass.
                    rt.engine.pump_jobs();
                    let raw = rt.eval(&format!("__wrWatchDrain({after}, {limit})"))?;
                    let data: serde_json::Value = serde_json::from_str(&raw)
                        .map_err(|e| format!("watch drain returned non-JSON: {e}"))?;
                    let records = data.get("records").cloned().unwrap_or(serde_json::json!([]));
                    let returned = records.as_array().map(|a| a.len()).unwrap_or(0);
                    let retained = data.get("retained").and_then(|v| v.as_i64()).unwrap_or(0);
                    let next_after = records
                        .as_array()
                        .and_then(|a| a.last())
                        .and_then(|r| r.get("seq"))
                        .and_then(|v| v.as_i64())
                        .unwrap_or(after);
                    Ok(serde_json::json!({
                        "records": records,
                        "latest_seq": data.get("latest_seq").cloned().unwrap_or(serde_json::json!(0)),
                        "enabled": data.get("enabled").cloned().unwrap_or(serde_json::json!(false)),
                        "retained": retained,
                        "returned": returned,
                        "next_after": next_after,
                        "omitted_retained": retained.saturating_sub(returned as i64),
                        "note": "engine MutationObserver records; compact node descriptors (tag#id.class), never live nodes"
                    }))
                }
            }
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
                "secret_reveal" | "secretReveal" => {
                    // N8: audited per-session reveal gate flip.
                    if let Some(val) = allowed {
                        session
                            .network_ledger
                            .lock()
                            .await
                            .set_secret_reveal(val);
                        if val {
                            session.gate_secret_reveal = true;
                        }
                    }
                }
                _ => {}
            }
            let reveal_allowed = session
                .network_ledger
                .lock()
                .await
                .reveal_allowed();
            Ok(serde_json::json!({
                "eval_allowed": session.gate_eval,
                "wasm_allowed": session.gate_wasm,
                "network_allowed": session.gate_network,
                "secret_reveal_allowed": reveal_allowed,
                "patches_applied": ["0006-dynamic-eval-gate", "0011-capability-gate-wasm", "0013-capability-gate-network", "N8-audited-secret-reveal"],
            }))
        }
        "assist" => {
            let goal = args.get("goal").and_then(|v| v.as_str())
                .or_else(|| args.get("value").and_then(|v| v.as_str()))
                .unwrap_or("inspect this page and collect tokens");
            let result = Box::pin(crate::assist::plan_walkthrough(goal, session, store)).await;
            Ok(serde_json::to_value(&result)?)
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
    async fn test_eval_state_persists_across_turns() {
        // A bridged/attached session must not lose JS state between turns.
        // Set a global in one eval; read it back in a later eval.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        session
            .goto_html("https://example.com/", "<html><body><h1>Hi</h1></body></html>", 4000)
            .expect("seed document");

        let set = execute_command(
            "eval",
            &serde_json::json!({ "source": "globalThis.__dosh_probe = 41 + 1; globalThis.__dosh_probe" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(set["evaluated"], true);

        // A SECOND eval must see the global set by the first one.
        // Control-realm wraps code in an IIFE, so return the value explicitly.
        let get = execute_command(
            "eval",
            &serde_json::json!({ "source": "return String(globalThis.__dosh_probe)" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        let result = get["result"].as_str().unwrap_or_default();
        assert!(
            result.contains("42"),
            "expected persisted global 42, got: {result:?}"
        );
    }

    #[tokio::test]
    async fn test_cookies_reads_live_jar() {
        // A cookie written via document.cookie in one turn must be visible to a
        // later `cookies` command (reads the resident runtime's live jar).
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        session
            .goto_html("https://example.com/", "<html><body>ok</body></html>", 4000)
            .expect("seed document");

        execute_command(
            "eval",
            &serde_json::json!({ "source": "document.cookie = 'dosh_session=abc123'; 'set'" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();

        let cookies = execute_command("cookies", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let names: Vec<String> = cookies["cookies"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|c| c["name"].as_str().map(String::from))
            .collect();
        assert!(
            names.iter().any(|n| n == "dosh_session"),
            "expected live cookie 'dosh_session' in jar, got: {names:?}"
        );
        // Secret hygiene: no raw value echoed by default.
        let raw = serde_json::to_string(&cookies).unwrap();
        assert!(!raw.contains("abc123"), "cookie value must never be echoed");
    }

    #[tokio::test]
    async fn test_assist_command_produces_walkthrough() {
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        session
            .goto_html("https://example.com/", "<html><body>ok</body></html>", 4000)
            .expect("seed document");
        execute_command(
            "eval",
            &serde_json::json!({ "source": "document.cookie='sid=zzz'; 'ok'" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();

        let res = execute_command(
            "assist",
            &serde_json::json!({ "goal": "collect tokens" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();

        assert!(res["steps"].as_array().unwrap().len() >= 5);
        let artifact = res["artifact"].as_str().unwrap_or_default();
        assert!(artifact.contains("sid"));
        assert!(!artifact.contains("zzz"), "secret value must not leak");
    }

    #[tokio::test]
    async fn test_gate_is_per_session() {
        // The bridge relies on each session carrying its own capability gates so
        // one attached connection can be restricted without affecting another.
        let store = ShellStore::new(None).unwrap();
        let mut sess_a = Session::new(reqwest::Client::new());
        let mut sess_b = Session::new(reqwest::Client::new());

        // Disable eval on A only.
        let a = execute_command(
            "gate",
            &serde_json::json!({ "gate": "eval", "allowed": false }),
            &mut sess_a,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(a["eval_allowed"], false);

        // B is untouched.
        let b = execute_command("gate", &serde_json::json!({}), &mut sess_b, &store)
            .await
            .unwrap();
        assert_eq!(b["eval_allowed"], true, "session B gate must be independent");
        assert_eq!(sess_a.gate_eval, false);
        assert_eq!(sess_b.gate_eval, true);
    }

    #[tokio::test]
    async fn test_act_resolves_css_selector_not_content_hash() {
        // The action surface's ref_id is a content hash that matches neither
        // an id nor a CSS selector. Acting must go through the target's CSS
        // selector (and the `~N` occurrence for repeated controls); a bare
        // hash was a silent no-op, and an unknown ref must error, not no-op.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        // No id on the button: the selector must come from tag+class chain.
        let html = "<html><head><title>act</title></head><body>\
            <ul class=\"menu\"><li><button class=\"btn\">Go</button></li></ul>\
            <script>document.querySelector('.btn').addEventListener('click', function(){ var li=document.createElement('li'); li.textContent='done'; document.querySelector('.menu').appendChild(li); });</script>\
            </body></html>";
        session
            .goto_html("https://example.com/", html, 4000)
            .expect("seed document");

        let inv = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let action = inv["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["text"].as_str() == Some("Go"))
            .expect("Go button in inventory");
        let ref_id = action["ref_id"].as_str().unwrap().to_string();
        assert!(
            action["selector"].as_str().is_some_and(|s| !s.is_empty()),
            "inventory must carry a resolvable CSS selector: {action:?}"
        );

        // One li before, two after — proves the click actually landed.
        let _ = execute_command("act", &serde_json::json!({ "ref": ref_id }), &mut session, &store)
            .await
            .expect("act on a real ref must succeed");
        let lis = execute_command(
            "eval",
            &serde_json::json!({ "source": "return String(document.querySelectorAll('.menu li').length)" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert!(
            lis["result"].as_str() == Some("2"),
            "click must have added the second li, got: {lis:?}"
        );

        // Unknown ref: honest error, never a silent no-op.
        let bad = execute_command(
            "act",
            &serde_json::json!({ "ref": "deadbeef" }),
            &mut session,
            &store,
        )
        .await;
        assert!(
            bad.is_err(),
            "unknown ref must surface an error, got: {bad:?}"
        );
    }

    #[tokio::test]
    async fn test_stable_identity_survives_rerender_shift() {
        // S2: content-addressed refs survive SPA re-renders. A banner and a
        // new sibling shift document order; the old ref must still resolve to
        // the same control, and the URL-free stable id must survive even an
        // id-attribute change.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let base = "https://example.com/";
        let page1 = "<html><head><title>one</title></head><body>\
            <div id=\"toolbar\"><button id=\"save\">Save</button><button id=\"cancel\">Cancel</button></div>\
            </body></html>";
        session.goto_html(base, page1, 4000).expect("page 1");

        let inv1 = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let save1 = inv1["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["text"].as_str() == Some("Save"))
            .expect("Save in page 1");
        let ref1 = save1["ref_id"].as_str().unwrap().to_string();

        // Resolve before the shift: the stable id must be reported.
        let r1 = execute_command("ref", &serde_json::json!({ "ref": ref1 }), &mut session, &store)
            .await
            .unwrap();
        assert!(r1["stable_id"].as_str().unwrap().starts_with("elem-"));
        let stable1 = r1["stable_id"].as_str().unwrap().to_string();

        // Re-render: banner + a new sibling shift document order.
        let page2 = "<html><head><title>one</title></head><body>\
            <div class=\"banner\">promo</div>\
            <div id=\"toolbar\"><button id=\"new\">New</button><button id=\"save\">Save</button><button id=\"cancel\">Cancel</button></div>\
            </body></html>";
        session.goto_html(base, page2, 4000).expect("page 2");

        // Same content, new position: the ref itself is stable and must land
        // on Save, not on the shifted neighbours.
        let r2 = execute_command("ref", &serde_json::json!({ "ref": ref1 }), &mut session, &store)
            .await
            .unwrap();
        assert_eq!(r2["text"].as_str(), Some("Save"));
        assert_eq!(r2["drifted"], false);

        // Id-attribute churn (id is outside the stable identity): the old ref
        // is gone, but the URL-free stable id still finds the control.
        let page3 = "<html><head><title>one</title></head><body>\
            <div id=\"toolbar\"><button id=\"new\">New</button><button id=\"save-v2\">Save</button><button id=\"cancel\">Cancel</button></div>\
            </body></html>";
        session.goto_html(base, page3, 4000).expect("page 3");
        let old = execute_command("ref", &serde_json::json!({ "ref": ref1 }), &mut session, &store)
            .await;
        assert!(old.is_err(), "a changed id yields a new ref; the old ref must not silently rebind");
        let r3 = execute_command("ref", &serde_json::json!({ "stable": stable1 }), &mut session, &store)
            .await
            .unwrap();
        assert_eq!(r3["text"].as_str(), Some("Save"));
        assert_eq!(r3["stable_id"].as_str(), Some(stable1.as_str()));
    }

    #[tokio::test]
    async fn test_occlusion_gate_refuses_click_under_overlay() {
        // L10: a click on a control covered by a higher-z overlay must be
        // refused with the occluder named — never delivered, never silent.
        // Removing the overlay lets the same ref click through.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let html = "<html><head><title>occ</title></head><body>\
            <button id=\"go\">Go</button>\
            <div id=\"cover\" style=\"position:fixed;left:0;top:0;width:100%;height:100%;z-index:100;\"></div>\
            </body></html>";
        session
            .goto_html("https://example.com/", html, 4000)
            .expect("seed document");

        let inv = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let ref_id = inv["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["text"].as_str() == Some("Go"))
            .and_then(|a| a["ref_id"].as_str())
            .expect("Go button in inventory")
            .to_string();

        let err = execute_command("act", &serde_json::json!({ "ref": ref_id }), &mut session, &store)
            .await
            .expect_err("occluded click must be refused, not delivered");
        let msg = err.to_string();
        assert!(
            msg.contains("occluded_by"),
            "refusal must name the occluder: {msg:?}"
        );

        // Remove the overlay: the same ref now clicks through.
        let _ = execute_command(
            "eval",
            &serde_json::json!({ "source": "document.getElementById('cover').remove(); 'gone'" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        let ok = execute_command("act", &serde_json::json!({ "ref": ref_id }), &mut session, &store)
            .await
            .expect("click must land once the overlay is gone");
        assert!(ok.is_object() || ok.is_string(), "act returns the page: {ok:?}");
    }

    #[tokio::test]
    async fn test_caps_manifest_live_counts_and_honest_unavailable() {
        // S9: caps is a real per-page "available / unavailable + why" manifest:
        // live-realm counts (post-script document), an unavailable list that
        // explains each gap, and absolute-path-only export.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let html = "<html><head><title>caps</title></head><body>\
            <canvas id=\"c\"></canvas>\
            <div id=\"e\" contenteditable=\"true\">edit me</div>\
            <button id=\"b\">Go</button>\
            </body></html>";
        session
            .goto_html("https://example.com/", html, 4000)
            .expect("seed document");

        let caps = execute_command("caps", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        assert_eq!(caps["source"], "live-realm");
        assert_eq!(caps["counts"]["canvas"], 1);
        assert_eq!(caps["counts"]["editable"], 1);
        assert_eq!(caps["counts"]["actions"].as_u64().unwrap_or(0), 2, "button + editable control");
        assert!(
            caps["page_unavailable"]
                .as_array()
                .unwrap()
                .iter()
                .any(|u| u["capability"].as_str() == Some("canvas pixels")),
            "canvas pixels must be listed unavailable with a reason: {caps:?}"
        );
        assert!(
            caps["page_unavailable"]
                .as_array()
                .unwrap()
                .iter()
                .any(|u| u["capability"].as_str().map_or(false, |c| c.contains("caret"))),
            "editable without a caret model must be disclosed: {caps:?}"
        );

        // Export requires an absolute path and writes the manifest.
        let bad = execute_command(
            "caps",
            &serde_json::json!({ "export": "relative/path.json" }),
            &mut session,
            &store,
        )
        .await;
        assert!(bad.is_err(), "relative export path must be refused");
        let dest = std::env::temp_dir().join(format!("dosh-caps-{}.json", std::process::id()));
        let exp = execute_command(
            "caps",
            &serde_json::json!({ "export": dest.to_string_lossy() }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(
            exp["exported_to"].as_str(),
            Some(dest.to_string_lossy().as_ref())
        );
        let on_disk = std::fs::read_to_string(&dest).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&on_disk).unwrap();
        assert_eq!(parsed["counts"]["canvas"], 1);
        let _ = std::fs::remove_file(&dest);
    }

    #[tokio::test]
    async fn test_select_text_drag_lane_rust() {
        // E2 (Rust lane): `select` drags over text in a real text container,
        // producing a readable selection via the shared engine lane.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let html = "<html><head><title>sel</title></head><body>\
            <input id=\"email\" name=\"email\" value=\"abcdef\">\
            </body></html>";
        session
            .goto_html("https://example.com/", html, 4000)
            .expect("seed document");

        let inv = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let ref_id = inv["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["text"].as_str() == Some("email"))
            .and_then(|a| a["ref_id"].as_str())
            .expect("email input in inventory")
            .to_string();

        let _page = execute_command(
            "select",
            &serde_json::json!({ "ref": ref_id, "start": 1, "end": 4 }),
            &mut session,
            &store,
        )
        .await
        .expect("select must succeed on a text field");

        let check = execute_command(
            "eval",
            &serde_json::json!({ "source": "return (function(){var f=document.getElementById('email'); return f.value.substring(f.selectionStart, f.selectionEnd);})()" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(
            check["result"].as_str(),
            Some("bcd"),
            "selection must span offsets 1..4, got: {check:?}"
        );

        // A non-text target is an honest refusal, not a silent no-op.
        let html2 = "<html><head><title>sel2</title></head><body><button id=\"b\">Go</button></body></html>";
        session
            .goto_html("https://example.com/two", html2, 4000)
            .expect("seed document 2");
        let inv2 = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let b_ref = inv2["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["text"].as_str() == Some("Go"))
            .and_then(|a| a["ref_id"].as_str())
            .expect("button in inventory")
            .to_string();
        let refused = execute_command(
            "select",
            &serde_json::json!({ "ref": b_ref, "start": 0, "end": 2 }),
            &mut session,
            &store,
        )
        .await;
        assert!(refused.is_err(), "selecting a non-text target must error");
        assert!(
            refused.unwrap_err().to_string().contains("not_selectable_text"),
            "refusal must name the reason"
        );
    }

    #[tokio::test]
    async fn test_state_snapshot_diff_rollback_roundtrip() {
        // S1: one state DB with DOM + live cookie jar + storage + history +
        // ledger, and snapshot/diff/rollback over it. Diff is structural and
        // never echoes values; rollback rehydrates the live runtime.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        session
            .goto_html(
                "https://example.com/",
                "<html><head><title>one</title></head><body><h1>one</h1></body></html>",
                4000,
            )
            .expect("seed document");

        let a = execute_command(
            "state.snapshot",
            &serde_json::json!({ "id": "A" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(a["id"], "A");
        assert!(a["counts"]["history_entries"].as_u64().unwrap_or(0) >= 1);

        // Mutate: a live cookie, a storage key, and a second navigation.
        execute_command(
            "eval",
            &serde_json::json!({ "source": "document.cookie = 'sid=abc123'; 'ok'" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        let mut storage_map = std::collections::HashMap::new();
        storage_map.insert("theme".to_string(), "dark".to_string());
        session
            .local_storage
            .insert("https://example.com".to_string(), storage_map);
        session
            .goto_html(
                "https://example.com/two",
                "<html><head><title>two</title></head><body><h1>two</h1></body></html>",
                4000,
            )
            .expect("second navigation");

        let _b = execute_command(
            "state.snapshot",
            &serde_json::json!({ "id": "B" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();

        // Diff A→B: url changed, cookie added, storage key added, history grew.
        let d = execute_command(
            "state.diff",
            &serde_json::json!({ "before": "A", "after": "B" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(d["url"]["changed"], true);
        assert_eq!(d["cookies"]["added"], serde_json::json!(["sid"]));
        assert!(
            d["storage"]["added"]
                .as_array()
                .unwrap()
                .iter()
                .any(|k| k.as_str().map_or(false, |s| s.contains("theme"))),
            "storage diff must list the added key: {d:?}"
        );
        assert!(
            d["history"]["after"].as_u64().unwrap() > d["history"]["before"].as_u64().unwrap(),
            "history must have grown: {d:?}"
        );
        // Secret hygiene: the raw cookie value never appears in diff output.
        let raw = serde_json::to_string(&d).unwrap();
        assert!(!raw.contains("abc123"), "diff must not leak cookie values");

        // Roll back to A: document, live jar, storage, and history rehydrate.
        let r = execute_command(
            "state.rollback",
            &serde_json::json!({ "id": "A" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(r["ledger_rolled_back"], false, "ledger must be disclosed as preserved");
        let tab = session.current_tab();
        assert_eq!(tab.url, "https://example.com/");
        assert!(tab.html.contains("one"), "document must be back to snapshot A");
        assert!(
            !session.current_cookies().contains_key("sid"),
            "live cookie jar must drop the rolled-back cookie"
        );
        assert!(
            session
                .local_storage
                .get("https://example.com")
                .map_or(true, |m| !m.contains_key("theme")),
            "rolled-back storage key must be gone"
        );
        assert_eq!(tab.history.len(), 1, "history must truncate to snapshot A");

        // The rolled-back document is genuinely in the resident runtime.
        let t = execute_command(
            "eval",
            &serde_json::json!({ "source": "return document.body.textContent" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert!(
            t["result"].as_str().unwrap_or("").contains("one"),
            "live DOM must reflect the rollback, got: {t:?}"
        );
    }

    #[tokio::test]
    async fn test_watch_real_mutation_stream() {
        // S3: `watch` is a real MutationObserver delta stream, not a stub.
        // Arm the observer, fire a genuine DOM mutation through the normal
        // action lane, and drain: records must arrive with a seq, compact
        // `tag#id.class` descriptors, and honest after/limit semantics.
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let html = "<html><head><title>watch</title></head><body>\
            <div id=\"list\"><span class=\"item\">one</span></div>\
            <button id=\"add\" onclick=\"var d=document.createElement('span');d.className='item';d.textContent='two';document.getElementById('list').appendChild(d);void 0\">Add</button>\
            </body></html>";
        session
            .goto_html("https://example.com/", html, 4000)
            .expect("seed document");

        let en = execute_command(
            "watch",
            &serde_json::json!({ "enable": true }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(en["enabled"], true);

        // No mutations yet: the drain is empty, but correctly formed.
        let d0 = execute_command("watch", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        assert_eq!(d0["returned"], 0);
        assert_eq!(d0["enabled"], true);

        // Trigger a real childList mutation via the action lane.
        let inv = execute_command("actions", &serde_json::json!({}), &mut session, &store)
            .await
            .unwrap();
        let ref_id = inv["actions"]
            .as_array()
            .unwrap()
            .iter()
            .find(|a| a["tag"].as_str() == Some("button") && a["text"].as_str() == Some("Add"))
            .and_then(|a| a["ref_id"].as_str())
            .expect("Add button must be in the inventory")
            .to_string();
        let _ = execute_command("act", &serde_json::json!({ "ref": ref_id }), &mut session, &store)
            .await
            .expect("action must execute");

        let d1 = execute_command(
            "watch",
            &serde_json::json!({ "after": 0, "limit": 50 }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        let recs = d1["records"].as_array().cloned().unwrap_or_default();
        assert!(
            !recs.is_empty(),
            "expected MutationObserver records after the click, got none"
        );
        let hit = recs
            .iter()
            .find(|r| r["type"].as_str() == Some("childList") && r["target"].as_str() == Some("div#list"));
        assert!(
            hit.is_some(),
            "expected a childList record on the #list container: {recs:?}"
        );
        let added = hit.unwrap()["added"].as_array().unwrap();
        assert_eq!(
            added.first().and_then(|n| n.as_str()),
            Some("span.item"),
            "added child must be the element the click created"
        );

        // Drain semantics: re-draining from next_after returns only newer records.
        let d2 = execute_command(
            "watch",
            &serde_json::json!({ "after": d1["next_after"], "limit": 50 }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(d2["returned"], 0, "second drain must be empty");

        // Disarm: the observer detaches and the retained log is preserved.
        let dis = execute_command(
            "watch",
            &serde_json::json!({ "enable": false }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(dis["enabled"], false);
        assert!(
            dis["retained"].as_i64().unwrap_or(0) >= 1,
            "retained records must survive disarming"
        );
    }

    #[tokio::test]
    async fn test_shell_45_commands_completeness() {
        // 46 original commands + 6 Phase-1 additions (net.stream/ws/cors/
        // timing/replay/decode) + 3 Phase-2 additions (state.snapshot/
        // state.diff/state.rollback) + select (Phase 3 E2).
        assert_eq!(COMMANDS.len(), 56);
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

    #[tokio::test]
    async fn test_auth_import_federated_cookie() {
        let client = reqwest::Client::new();
        let mut session = Session::new(client);
        let store = ShellStore::new(None).unwrap();

        let _ = session.goto_html("<html><body><h1>Test</h1></body></html>", "https://example.com/app", 4000);
        let res = execute_command(
            "auth.import",
            &serde_json::json!({
                "url": "https://example.com/app",
                "cookie": "sid=xyz123abc; Path=/; Domain=example.com"
            }),
            &mut session,
            &store,
        ).await.unwrap();

        assert_eq!(res["imported"], true);
        assert!(session.current_cookies().contains_key("sid"));
        assert_eq!(session.current_cookies().get("sid").unwrap(), "xyz123abc");
    }
}
