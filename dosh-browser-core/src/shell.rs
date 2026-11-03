//! `shell.rs` — The agent command surface (replaces `webresearch/shell.py`).
//!
//! Handles commands like `help`, `open`, `actions`, `act`, `eval`, `skill.*`, etc.
//! Uses `ShellStore` for persistent skills and snapshots.

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::session::Session;
use crate::types::*;

/// The workspace evidence and skills store.
pub struct ShellStore {
    conn: Mutex<Connection>,
    persistent: bool,
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
            "SELECT name, sha256, origins, allow_actions, version, warrant_expires FROM skills WHERE name = ?1",
            params![name],
            |row| {
                Ok(SkillInfo {
                    name: row.get(0)?,
                    sha256: row.get(1)?,
                    origins: serde_json::from_str(&row.get::<_, String>(2)?).unwrap_or_default(),
                    allow_actions: row.get::<_, i32>(3)? != 0,
                    version: row.get(4)?,
                    warrant_expires: row.get(5)?,
                })
            },
        );
        drop(conn);

        skill.map(|s| serde_json::to_value(s).unwrap())
    }

    pub fn list_skills(&self) -> Result<Vec<serde_json::Value>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT name, sha256, origins, allow_actions, version, warrant_expires FROM skills",
        )?;
        let rows: Vec<SkillInfo> = stmt.query_map([], |row| {
            Ok(SkillInfo {
                name: row.get(0)?,
                sha256: row.get(1)?,
                origins: serde_json::from_str(&row.get::<_, String>(2)?).unwrap_or_default(),
                allow_actions: row.get::<_, i32>(3)? != 0,
                version: row.get(4)?,
                warrant_expires: row.get(5)?,
            })
        })?.collect::<Result<Vec<_>, _>>()?;
        
        Ok(rows.iter().map(|sk| serde_json::to_value(sk).unwrap()).collect())
    }

    pub fn delete_skill(&self, name: &str) -> Result<bool, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let changed = conn.execute("DELETE FROM skills WHERE name = ?1", params![name])?;
        drop(conn);
        Ok(changed > 0)
    }

    pub fn save_snapshot(
        &self,
        tab_id: &str,
        url: &str,
        evidence: &str,
    ) -> Result<i64, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let created = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        
        conn.execute(
            "INSERT INTO snapshots (created, tab_id, url, evidence) VALUES (?1, ?2, ?3, ?4)",
            params![created, tab_id, url, evidence],
        )?;
        
        let id = conn.last_insert_rowid();
        drop(conn);
        Ok(id)
    }

    pub fn run_skill(
        &self,
        name: &str,
        session: &mut Session,
        args: &serde_json::Value,
    ) -> Result<serde_json::Value, Box<dyn std::error::Error + Send + Sync>> {
        let skill = self.get_skill(name)?;
        if skill.is_null() {
            return Err(format!("skill not found: {}", name).into());
        }
        
        Ok(serde_json::json!({
            "skill": name,
            "args": args,
            "result": "executed_placeholder" 
        }))
    }
}

#[derive(Serialize, Deserialize)]
pub struct SkillInfo {
    pub name: String,
    pub sha256: String,
    pub origins: Vec<String>,
    pub allow_actions: bool,
    pub version: i64,
    pub warrant_expires: Option<f64>,
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
            Ok(serde_json::json!({
                "commands": [
                    "help", "status", "open", "actions", "act", "net", "net.capture", "net.rule", 
                    "net.unrule", "console", "eval", "skill.save", "skill.list", "skill.run", 
                    "skill.delete", "skill.update", "db", "storage", "snapshot", "diff", "layers", 
                    "frames", "tabs", "video", "read", "history", "cookies", "wait", "caps", 
                    "screenshot", "frame.eval", "ref", "trace", "watch"
                ]
            }))
        }
        "status" => {
            Ok(serde_json::json!({
                "tab": session.current_tab().id,
                "url": session.current_tab().url,
                "history_len": session.current_tab().history.len(),
                "tabs": session.tabs.len(),
            }))
        }
        "open" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let page = session.goto(url, 8000).await?;
            Ok(serde_json::to_value(page)?)
        }
        "actions" => {
            let max = args.get("max_actions").and_then(|v| v.as_u64()).unwrap_or(200) as usize;
            // Placeholder for now
            Ok(serde_json::json!({ "placeholder": "actions extraction requires html access from session" }))
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
            let res = store.run_skill(name, session, args)?;
            Ok(res)
        }
        "skill.delete" => {
            let name = args.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let deleted = store.delete_skill(name)?;
            Ok(serde_json::json!({ "deleted": deleted }))
        }
        "snapshot" => {
            let tab = session.current_tab();
            let evidence = serde_json::to_string(&tab).unwrap_or_default();
            let id = store.save_snapshot(&tab.id, &tab.url, &evidence)?;
            Ok(serde_json::json!({ "id": id, "tab_id": tab.id }))
        }
        "tabs" => {
            let tabs = session.tabs.iter().map(|t| {
                serde_json::json!({
                    "id": t.id,
                    "url": t.url,
                    "active": t.id == session.current_tab().id,
                })
            }).collect::<Vec<_>>();
            Ok(serde_json::json!({ "tabs": tabs }))
        }
        "read" => {
            let tab = session.current_tab();
            Ok(serde_json::json!({ "text": "read_placeholder" }))
        }
        "history" => {
            let tab = session.current_tab();
            let history: Vec<_> = tab.history.iter().map(|p| {
                serde_json::json!({ "url": p.url, "status": p.status })
            }).collect();
            Ok(serde_json::json!({ "current_index": tab.index, "history": history }))
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
    }
}
