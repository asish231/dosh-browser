//! `cache_sqlite.rs` — Persistent SQLite RFC 9111 HTTP Cache for Dosh-Browser.
//!
//! Provides durable cross-session HTTP caching using SQLite, supporting
//! freshness validation, ETags, Last-Modified, and 304 Not Modified reuse.

use crate::cache::{now_secs, parse_cache_control, CacheEntry};
use rusqlite::{params, Connection};
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

/// A persistent SQLite-backed HTTP cache.
#[derive(Clone)]
pub struct SqliteCache {
    conn: Arc<Mutex<Connection>>,
}

impl SqliteCache {
    /// Initialize SQLite cache in-memory or on disk.
    pub fn new(path: Option<&Path>) -> Result<Self, rusqlite::Error> {
        let conn = match path {
            Some(p) => Connection::open(p)?,
            None => Connection::open_in_memory()?,
        };

        conn.execute(
            "CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY,
                status INTEGER NOT NULL,
                headers_json TEXT NOT NULL,
                body BLOB NOT NULL,
                stored_at REAL NOT NULL,
                max_age REAL NOT NULL,
                etag TEXT,
                last_modified TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_http_cache_stored ON http_cache(stored_at);",
            [],
        )?;

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Retrieve a cached response for `url`. If expired, returns entry for revalidation.
    pub fn get(&self, url: &str) -> Option<CacheEntry> {
        let conn = self.conn.lock().ok()?;
        let mut stmt = conn
            .prepare(
                "SELECT url, status, headers_json, body, stored_at
                 FROM http_cache WHERE url = ?1",
            )
            .ok()?;

        let mut rows = stmt
            .query_map(params![url], |row| {
                let u: String = row.get(0)?;
                let status: u16 = row.get(1)?;
                let headers_raw: String = row.get(2)?;
                let body: Vec<u8> = row.get(3)?;
                let stored_at: f64 = row.get(4)?;
                Ok((u, status, headers_raw, body, stored_at))
            })
            .ok()?;

        if let Some(Ok((u, status, headers_raw, body, stored_at))) = rows.next() {
            let resp_headers: HashMap<String, String> =
                serde_json::from_str(&headers_raw).unwrap_or_default();
            Some(CacheEntry {
                url: u,
                status,
                resp_headers,
                body,
                stored_at,
                vary: HashMap::new(),
            })
        } else {
            None
        }
    }

    /// Store a response in the cache if cacheable.
    pub fn store(&self, entry: &CacheEntry) {
        let cc_val = entry
            .resp_headers
            .get("cache-control")
            .cloned()
            .unwrap_or_default();
        let cc = parse_cache_control(&cc_val);

        if cc.contains_key("no-store") {
            return;
        }

        let max_age = entry.freshness_lifetime().unwrap_or(0.0);
        let etag = entry.resp_headers.get("etag").cloned();
        let last_modified = entry.resp_headers.get("last-modified").cloned();
        let headers_json = serde_json::to_string(&entry.resp_headers).unwrap_or_default();

        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "INSERT OR REPLACE INTO http_cache
                 (url, status, headers_json, body, stored_at, max_age, etag, last_modified)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    entry.url,
                    entry.status,
                    headers_json,
                    entry.body,
                    entry.stored_at,
                    max_age,
                    etag,
                    last_modified,
                ],
            );
        }
    }

    /// Remove an entry from the cache.
    pub fn invalidate(&self, url: &str) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute("DELETE FROM http_cache WHERE url = ?1", params![url]);
        }
    }

    /// Delete all entries whose freshness lifetime has expired.
    pub fn sweep_expired(&self) -> usize {
        let now = now_secs();
        if let Ok(conn) = self.conn.lock() {
            conn.execute(
                "DELETE FROM http_cache WHERE (stored_at + max_age) < ?1",
                params![now],
            )
            .unwrap_or(0)
        } else {
            0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sqlite_cache_lifecycle() {
        let cache = SqliteCache::new(None).unwrap();
        let url = "https://example.com/api/data";

        let mut headers = HashMap::new();
        headers.insert("cache-control".into(), "max-age=3600".into());
        headers.insert("etag".into(), "\"abc-123\"".into());
        headers.insert("content-type".into(), "application/json".into());

        let entry = CacheEntry {
            url: url.into(),
            status: 200,
            resp_headers: headers,
            body: b"{\"success\": true}".to_vec(),
            stored_at: now_secs(),
            vary: HashMap::new(),
        };

        cache.store(&entry);

        // Fetch back
        let retrieved = cache.get(url).expect("Entry should exist in cache");
        assert_eq!(retrieved.status, 200);
        assert_eq!(retrieved.body, b"{\"success\": true}");
        assert_eq!(retrieved.resp_headers.get("etag").unwrap(), "\"abc-123\"");
        assert!(retrieved.is_fresh(None));

        // Invalidate
        cache.invalidate(url);
        assert!(cache.get(url).is_none());
    }

    #[test]
    fn test_no_store_not_cached() {
        let cache = SqliteCache::new(None).unwrap();
        let url = "https://example.com/no-store";

        let mut headers = HashMap::new();
        headers.insert("cache-control".into(), "no-store, private".into());

        let entry = CacheEntry {
            url: url.into(),
            status: 200,
            resp_headers: headers,
            body: b"secret".to_vec(),
            stored_at: now_secs(),
            vary: HashMap::new(),
        };

        cache.store(&entry);
        assert!(cache.get(url).is_none());
    }
}
