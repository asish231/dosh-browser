//! `registry.rs` — Live session registry for the bridged/attached connection model.
//!
//! The REST server historically held a single process-wide `Arc<Mutex<Session>>`
//! (one process = one session). The "SSH-like" bridge needs many **isolated**
//! live sessions, each addressable by id, so that a model/client can attach to a
//! specific running session over a persistent channel and drive it across many
//! turns without a new connection clobbering another connection's context.
//!
//! This module owns:
//!   * a `HashMap<SessionId, SessionSlot>` of resident sessions,
//!   * an attachment guard per session so two connections do not interleave
//!     commands on the same live DOM/isolate unless they explicitly share it.
//!
//! Isolation model: each session is behind its own `Arc<Mutex<Session>>`, so
//! commands on different sessions run fully in parallel. A single session is
//! still serialized by its own lock (the V8 isolate is not re-entrant). The
//! `attached` flag is advisory bookkeeping used by the bridge to refuse a
//! second exclusive attach to the same session.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use reqwest::Client;
use tokio::sync::Mutex;

use crate::session::Session;

/// A resident session plus its attachment bookkeeping.
pub struct SessionSlot {
    pub session: Arc<Mutex<Session>>,
    /// Number of live bridge connections currently attached to this session.
    pub attachments: AtomicU64,
    /// Epoch-millis of creation, for idle reporting.
    pub created_ms: u64,
}

impl SessionSlot {
    fn new(session: Session) -> Self {
        Self {
            session: Arc::new(Mutex::new(session)),
            attachments: AtomicU64::new(0),
            created_ms: now_ms(),
        }
    }
}

/// Thread-safe registry of live sessions, shared across every transport.
#[derive(Clone)]
pub struct SessionRegistry {
    inner: Arc<Mutex<HashMap<String, Arc<SessionSlot>>>>,
    client: Client,
}

impl SessionRegistry {
    pub fn new(client: Client) -> Self {
        Self {
            inner: Arc::new(Mutex::new(HashMap::new())),
            client,
        }
    }

    /// Create a fresh isolated session, register it, and return its id + handle.
    pub async fn create(&self) -> (String, Arc<SessionSlot>) {
        let session = Session::new(self.client.clone());
        let id = session.id.clone();
        let slot = Arc::new(SessionSlot::new(session));
        self.inner.lock().await.insert(id.clone(), slot.clone());
        (id, slot)
    }

    /// Look up an existing session slot by id.
    pub async fn get(&self, id: &str) -> Option<Arc<SessionSlot>> {
        self.inner.lock().await.get(id).cloned()
    }

    /// List live session ids with attachment + age metadata.
    pub async fn list(&self) -> Vec<serde_json::Value> {
        let map = self.inner.lock().await;
        map.iter()
            .map(|(id, slot)| {
                serde_json::json!({
                    "session_id": id,
                    "attachments": slot.attachments.load(Ordering::SeqCst),
                    "created_ms": slot.created_ms,
                })
            })
            .collect()
    }

    /// Remove a session from the registry. Returns true if it existed.
    pub async fn remove(&self, id: &str) -> bool {
        self.inner.lock().await.remove(id).is_some()
    }

    /// Attach to a session, creating it if `id` is None or unknown-and-create.
    /// `exclusive` refuses the attach when another connection already holds it.
    pub async fn attach(
        &self,
        id: Option<&str>,
        exclusive: bool,
    ) -> Result<(String, Arc<SessionSlot>), String> {
        let slot = match id {
            Some(existing) => match self.get(existing).await {
                Some(s) => s,
                None => {
                    // Attaching to a named-but-unknown session creates it under
                    // a fresh id (the caller gets the real id back).
                    return Ok(self.create().await);
                }
            },
            None => return Ok(self.create().await),
        };

        if exclusive && slot.attachments.load(Ordering::SeqCst) > 0 {
            return Err(format!(
                "session '{}' is already attached exclusively",
                id.unwrap_or_default()
            ));
        }
        slot.attachments.fetch_add(1, Ordering::SeqCst);
        Ok((id.unwrap_or_default().to_string(), slot))
    }

    /// Detach a connection from a session (decrements the attachment count).
    pub async fn detach(&self, id: &str) {
        if let Some(slot) = self.get(id).await {
            let prev = slot.attachments.load(Ordering::SeqCst);
            if prev > 0 {
                slot.attachments.fetch_sub(1, Ordering::SeqCst);
            }
        }
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn client() -> Client {
        Client::new()
    }

    #[tokio::test]
    async fn test_create_and_get() {
        let reg = SessionRegistry::new(client());
        let (id, _slot) = reg.create().await;
        assert!(!id.is_empty());
        assert!(reg.get(&id).await.is_some());
        assert!(reg.get("nonexistent").await.is_none());
    }

    #[tokio::test]
    async fn test_sessions_are_isolated() {
        // Two sessions must be independent handles (no shared Session).
        let reg = SessionRegistry::new(client());
        let (id1, slot1) = reg.create().await;
        let (id2, slot2) = reg.create().await;
        assert_ne!(id1, id2);
        assert!(!Arc::ptr_eq(&slot1.session, &slot2.session));
    }

    #[tokio::test]
    async fn test_attach_detach_counts() {
        let reg = SessionRegistry::new(client());
        let (id, slot) = reg.create().await;

        let (_aid, _s) = reg.attach(Some(&id), false).await.unwrap();
        assert_eq!(slot.attachments.load(Ordering::SeqCst), 1);

        // Non-exclusive second attach is allowed (shared, tmux-like).
        let (_aid2, _s2) = reg.attach(Some(&id), false).await.unwrap();
        assert_eq!(slot.attachments.load(Ordering::SeqCst), 2);

        reg.detach(&id).await;
        assert_eq!(slot.attachments.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn test_exclusive_attach_refused_when_busy() {
        let reg = SessionRegistry::new(client());
        let (id, _slot) = reg.create().await;
        let _first = reg.attach(Some(&id), true).await.ok().unwrap();
        // A second EXCLUSIVE attach must be refused (no overlap).
        let second = reg.attach(Some(&id), true).await;
        match second {
            Err(msg) => assert!(msg.contains("already attached")),
            Ok(_) => panic!("exclusive attach should have been refused"),
        }
    }

    #[tokio::test]
    async fn test_attach_none_creates() {
        let reg = SessionRegistry::new(client());
        let (id, _slot) = reg.attach(None, false).await.unwrap();
        assert!(!id.is_empty());
        assert!(reg.get(&id).await.is_some());
    }
}
