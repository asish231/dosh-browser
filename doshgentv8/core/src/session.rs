//! `session.rs` — Session Pool with LRU eviction for resident V8 actors.
//!
//! Provides a bounded registry of resident `DoshActorHandle`s keyed by session ID.
//! If active sessions exceed capacity (default 8), the least recently used session
//! actor is gracefully evicted and shut down to reclaim V8 heap memory.

use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;

use crate::actor::DoshActorHandle;

struct SessionEntry {
    actor: DoshActorHandle,
}

struct PoolInner {
    sessions: HashMap<String, SessionEntry>,
    lru_order: VecDeque<String>,
    capacity: usize,
}

impl PoolInner {
    fn touch(&mut self, session_id: &str) {
        if let Some(pos) = self.lru_order.iter().position(|s| s == session_id) {
            self.lru_order.remove(pos);
        }
        self.lru_order.push_back(session_id.to_string());
    }

    fn evict_if_needed(&mut self) -> Option<DoshActorHandle> {
        if self.sessions.len() >= self.capacity {
            if let Some(oldest_key) = self.lru_order.pop_front() {
                if let Some(entry) = self.sessions.remove(&oldest_key) {
                    entry.actor.shutdown();
                    return Some(entry.actor);
                }
            }
        }
        None
    }
}

/// Thread-safe bounded pool of resident V8 actor sessions.
pub struct SessionPool {
    inner: Mutex<PoolInner>,
}

impl SessionPool {
    /// Create a new session pool with the specified capacity limit.
    pub fn new(capacity: usize) -> Self {
        Self {
            inner: Mutex::new(PoolInner {
                sessions: HashMap::new(),
                lru_order: VecDeque::new(),
                capacity: capacity.max(1),
            }),
        }
    }

    /// Retrieve an existing resident actor by session ID, marking it as recently used.
    pub fn get(&self, session_id: &str) -> Option<DoshActorHandle> {
        let mut inner = self.inner.lock().expect("session pool poisoned");
        if let Some(entry) = inner.sessions.get(session_id) {
            let handle = entry.actor.clone();
            inner.touch(session_id);
            Some(handle)
        } else {
            None
        }
    }

    /// Retrieve an existing actor or create a new resident actor using `factory`.
    /// Evicts the least recently used session if capacity is reached.
    pub fn get_or_create<F>(&self, session_id: &str, factory: F) -> Result<DoshActorHandle, String>
    where
        F: FnOnce() -> Result<DoshActorHandle, String>,
    {
        let mut inner = self.inner.lock().expect("session pool poisoned");
        if let Some(entry) = inner.sessions.get(session_id) {
            let handle = entry.actor.clone();
            inner.touch(session_id);
            return Ok(handle);
        }

        // Evict LRU session if at capacity
        inner.evict_if_needed();

        let actor = factory()?;
        inner.sessions.insert(
            session_id.to_string(),
            SessionEntry {
                actor: actor.clone(),
            },
        );
        inner.touch(session_id);

        Ok(actor)
    }

    /// Remove a session from the pool and shut down its actor.
    pub fn remove(&self, session_id: &str) -> Option<DoshActorHandle> {
        let mut inner = self.inner.lock().expect("session pool poisoned");
        if let Some(pos) = inner.lru_order.iter().position(|s| s == session_id) {
            inner.lru_order.remove(pos);
        }
        if let Some(entry) = inner.sessions.remove(session_id) {
            entry.actor.shutdown();
            Some(entry.actor)
        } else {
            None
        }
    }

    /// Returns the current number of active resident sessions.
    pub fn len(&self) -> usize {
        let inner = self.inner.lock().expect("session pool poisoned");
        inner.sessions.len()
    }

    /// Check if the pool is empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Returns the pool's maximum session capacity.
    pub fn capacity(&self) -> usize {
        let inner = self.inner.lock().expect("session pool poisoned");
        inner.capacity
    }

    /// Returns a list of active session IDs.
    pub fn active_sessions(&self) -> Vec<String> {
        let inner = self.inner.lock().expect("session pool poisoned");
        inner.sessions.keys().cloned().collect()
    }

    /// Clear all sessions and shut down all resident actors.
    pub fn clear(&self) {
        let mut inner = self.inner.lock().expect("session pool poisoned");
        for (_, entry) in inner.sessions.drain() {
            entry.actor.shutdown();
        }
        inner.lru_order.clear();
    }
}

impl Default for SessionPool {
    fn default() -> Self {
        Self::new(8)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::actor::DoshActor;
    use crate::HostSeam;
    use std::sync::Arc;

    struct DummySeam;
    impl HostSeam for DummySeam {
        fn call(&self, _name: &str, _args: &[String]) -> Result<Option<String>, String> {
            Ok(None)
        }
    }

    #[test]
    fn test_session_pool_lru_eviction() {
        // Pool with capacity 2
        let pool = SessionPool::new(2);

        let actor1 = pool
            .get_or_create("session-1", || DoshActor::spawn(Arc::new(DummySeam), 0))
            .expect("create s1");
        actor1.eval("var val = 'one';").expect("eval s1");

        let actor2 = pool
            .get_or_create("session-2", || DoshActor::spawn(Arc::new(DummySeam), 0))
            .expect("create s2");
        actor2.eval("var val = 'two';").expect("eval s2");

        assert_eq!(pool.len(), 2);

        // Touch session 1 to make session 2 LRU
        let fetched_s1 = pool.get("session-1").expect("get s1");
        assert_eq!(fetched_s1.eval("val").unwrap(), "one");

        // Insert session 3 -> should evict session 2
        let actor3 = pool
            .get_or_create("session-3", || DoshActor::spawn(Arc::new(DummySeam), 0))
            .expect("create s3");
        actor3.eval("var val = 'three';").expect("eval s3");

        assert_eq!(pool.len(), 2);
        assert!(pool.get("session-2").is_none(), "session-2 should be evicted");
        assert!(pool.get("session-1").is_some(), "session-1 should still exist");
        assert!(pool.get("session-3").is_some(), "session-3 should exist");

        pool.clear();
        assert_eq!(pool.len(), 0);
    }
}

