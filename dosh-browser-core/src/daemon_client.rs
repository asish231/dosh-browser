//! `daemon_client.rs` — Thin client for connecting to the Python Dosh Loom daemon.
//!
//! Allows the Rust CLI/server to act as a thin client when a Python daemon is
//! running, enabling session sharing between Rust and Python clients.
//!
//! IMPORTANT: the daemon ties a session to the CONNECTION that attached. So
//! attach + command + detach must all travel over ONE persistent socket. The
//! `Session` type below holds that connection open; the older one-shot helpers
//! are kept only for ping/list/attach (which are meaningful on their own).

use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

fn default_socket_path() -> String {
    std::env::var("WR_LOOM_SOCKET").unwrap_or_else(|_| {
        let home = std::env::var("HOME").unwrap_or_default();
        format!("{}/.dosh/loom.sock", home)
    })
}

/// Request frame sent to the daemon.
#[derive(Serialize)]
struct DaemonRequest {
    op: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    command: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    arguments: Option<serde_json::Value>,
}

/// Response frame from the daemon.
#[derive(Deserialize, Serialize, Debug, Default)]
pub struct DaemonResponse {
    pub ok: bool,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub restored: Option<bool>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub pong: Option<bool>,
    #[serde(default)]
    pub sessions: Option<Vec<serde_json::Value>>,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub result: Option<serde_json::Value>,
}

/// A persistent daemon connection bound to one attached session.
///
/// `attach` sends the handshake, then every `command` reuses the same socket —
/// which is what the daemon requires to route the command to the right session.
pub struct Session {
    stream: UnixStream,
    pub session_id: String,
}

impl Session {
    /// Connect to the daemon and attach to `session_id` (or create one).
    pub fn attach(socket_path: Option<&str>, session_id: Option<&str>) -> Result<Self, String> {
        let path = socket_path.map(|s| s.to_string()).unwrap_or_else(default_socket_path);
        if !std::path::Path::new(&path).exists() {
            return Err(format!("daemon not available at {}", path));
        }
        let stream = UnixStream::connect(&path).map_err(|e| format!("connect failed: {}", e))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(120)))
            .map_err(|e| format!("set timeout failed: {}", e))?;
        let mut sess = Session { stream, session_id: String::new() };
        let req = DaemonRequest {
            op: "attach".to_string(),
            session_id: session_id.map(|s| s.to_string()),
            command: None,
            arguments: None,
        };
        let resp = sess.request(&req)?;
        if !resp.ok {
            return Err(resp.error.unwrap_or_else(|| "attach failed".to_string()));
        }
        sess.session_id = resp.session_id.unwrap_or_default();
        Ok(sess)
    }

    /// Execute a shell command in this attached session.
    pub fn command(
        &mut self,
        command: &str,
        arguments: Option<serde_json::Value>,
    ) -> Result<DaemonResponse, String> {
        let req = DaemonRequest {
            op: "command".to_string(),
            session_id: None,
            command: Some(command.to_string()),
            arguments,
        };
        self.request(&req)
    }

    /// Detach (best effort). The socket closes on drop.
    pub fn detach(&mut self) -> Result<DaemonResponse, String> {
        let req = DaemonRequest {
            op: "detach".to_string(),
            session_id: None,
            command: None,
            arguments: None,
        };
        self.request(&req)
    }

    fn request(&mut self, req: &DaemonRequest) -> Result<DaemonResponse, String> {
        let payload = serde_json::to_string(req).map_err(|e| format!("serialize failed: {}", e))?;
        self.stream
            .write_all(payload.as_bytes())
            .map_err(|e| format!("write failed: {}", e))?;
        self.stream
            .write_all(b"\n")
            .map_err(|e| format!("write newline failed: {}", e))?;
        self.stream.flush().map_err(|e| format!("flush failed: {}", e))?;

        let mut line = String::new();
        let n = {
            let mut reader = BufReader::new(&self.stream);
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read failed: {}", e))?
        };
        if n == 0 {
            return Err("daemon closed the connection".to_string());
        }
        serde_json::from_str(&line).map_err(|e| format!("deserialize failed: {}: {}", e, line.trim()))
    }
}

/// One-shot helpers for the stateless ops (ping / list / attach-inspect).
pub struct DaemonClient {
    socket_path: String,
}

impl DaemonClient {
    pub fn new(socket_path: Option<&str>) -> Self {
        Self {
            socket_path: socket_path.map(|s| s.to_string()).unwrap_or_else(default_socket_path),
        }
    }

    pub fn socket_path(&self) -> &str {
        &self.socket_path
    }

    pub fn is_available(&self) -> bool {
        std::path::Path::new(&self.socket_path).exists()
    }

    pub fn ping(&self) -> Result<bool, String> {
        let resp = self.one_shot(DaemonRequest {
            op: "ping".to_string(),
            session_id: None,
            command: None,
            arguments: None,
        })?;
        Ok(resp.ok && resp.pong.unwrap_or(false))
    }

    pub fn list(&self) -> Result<DaemonResponse, String> {
        self.one_shot(DaemonRequest {
            op: "list".to_string(),
            session_id: None,
            command: None,
            arguments: None,
        })
    }

    fn one_shot(&self, req: DaemonRequest) -> Result<DaemonResponse, String> {
        if !self.is_available() {
            return Err(format!("daemon not available at {}", self.socket_path));
        }
        let mut stream = UnixStream::connect(&self.socket_path)
            .map_err(|e| format!("connect failed: {}", e))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(30)))
            .map_err(|e| format!("set timeout failed: {}", e))?;
        let payload = serde_json::to_string(&req).map_err(|e| format!("serialize failed: {}", e))?;
        stream.write_all(payload.as_bytes()).map_err(|e| format!("write failed: {}", e))?;
        stream.write_all(b"\n").map_err(|e| format!("write newline failed: {}", e))?;
        stream.flush().map_err(|e| format!("flush failed: {}", e))?;
        let mut line = String::new();
        let mut reader = BufReader::new(&stream);
        reader.read_line(&mut line).map_err(|e| format!("read failed: {}", e))?;
        serde_json::from_str(&line).map_err(|e| format!("deserialize failed: {}", e))
    }
}

/// Read journal rows directly from the daemon's SQLite file.
/// Returns one formatted line per entry: `[session_id] [timestamp] kind: payload`.
pub fn read_journal(db_path: &str, session_filter: Option<&str>) -> Result<Vec<String>, String> {
    if !std::path::Path::new(db_path).exists() {
        return Err(format!("journal DB not found at {}", db_path));
    }
    let conn = rusqlite::Connection::open_with_flags(
        db_path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    )
    .map_err(|e| format!("open journal DB failed: {}", e))?;
    let mut out = Vec::new();
    match session_filter {
        Some(sid) => {
            let mut stmt = conn
                .prepare(
                    "SELECT session_id, timestamp, kind, payload FROM sessions_journal \
                     WHERE session_id = ?1 ORDER BY timestamp ASC",
                )
                .map_err(|e| format!("prepare failed: {}", e))?;
            let rows = stmt
                .query_map([sid], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, f64>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                    ))
                })
                .map_err(|e| format!("query failed: {}", e))?;
            for r in rows {
                let (s, ts, kind, payload) = r.map_err(|e| format!("row failed: {}", e))?;
                out.push(format!("[{}] [{}] {}: {}", s, ts, kind, payload));
            }
        }
        None => {
            let mut stmt = conn
                .prepare(
                    "SELECT session_id, timestamp, kind, payload FROM sessions_journal \
                     ORDER BY timestamp ASC",
                )
                .map_err(|e| format!("prepare failed: {}", e))?;
            let rows = stmt
                .query_map([], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, f64>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                    ))
                })
                .map_err(|e| format!("query failed: {}", e))?;
            for r in rows {
                let (s, ts, kind, payload) = r.map_err(|e| format!("row failed: {}", e))?;
                out.push(format!("[{}] [{}] {}: {}", s, ts, kind, payload));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unavailable_when_socket_missing() {
        let c = DaemonClient::new(Some("/tmp/definitely-not-a-loom-socket-xyz.sock"));
        assert!(!c.is_available());
        assert!(c.ping().is_err());
    }

    #[test]
    fn default_socket_path_respects_env() {
        std::env::set_var("WR_LOOM_SOCKET", "/tmp/custom_loom.sock");
        let c = DaemonClient::new(None);
        assert_eq!(c.socket_path(), "/tmp/custom_loom.sock");
        std::env::remove_var("WR_LOOM_SOCKET");
    }

    #[test]
    fn read_journal_missing_db_errors() {
        let r = read_journal("/tmp/definitely-no-journal-db-xyz.sqlite", None);
        assert!(r.is_err());
    }

    #[test]
    fn read_journal_reads_rows() {
        let path = std::env::temp_dir().join(format!(
            "dosh_journal_test_{}.sqlite",
            std::process::id()
        ));
        let p = path.to_string_lossy().to_string();
        let _ = std::fs::remove_file(&p);
        {
            let conn = rusqlite::Connection::open(&p).unwrap();
            conn.execute_batch(
                "CREATE TABLE sessions_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL, timestamp REAL NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL);
                 INSERT INTO sessions_journal (session_id,timestamp,kind,payload)
                    VALUES ('s_test', 1.0, 'attach', '{}');
                 INSERT INTO sessions_journal (session_id,timestamp,kind,payload)
                    VALUES ('s_other', 2.0, 'attach', '{}');",
            )
            .unwrap();
        }
        let all = read_journal(&p, None).unwrap();
        assert_eq!(all.len(), 2);
        let filtered = read_journal(&p, Some("s_test")).unwrap();
        assert_eq!(filtered.len(), 1);
        assert!(filtered[0].contains("s_test"));
        let _ = std::fs::remove_file(&p);
    }
}
