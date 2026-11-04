//! `cache_sqlite.rs` — Persistent SQLite RFC 9111 HTTP Cache for Dosh-Browser.
//!
//! Provides durable cross-session HTTP caching using SQLite, supporting
//! freshness validation, ETags, Last-Modified, 304 Not Modified renewal,
//! Vary matching, and script bytecode caching.

use crate::cache::{now_secs, parse_cache_control, CacheEntry};
use rusqlite::{params, Connection};
use std::collections::HashMap;
use std::path::Path;
use std::sync::{Arc, Mutex};

/// Status of cache lookup per RFC 9111.
#[derive(Debug, Clone, PartialEq)]
pub enum CacheGetResult {
    Hit(CacheEntry),
    Revalidate(CacheEntry),
    Stale(CacheEntry),
    Miss,
}

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

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS http_cache (
                url TEXT PRIMARY KEY,
                status INTEGER NOT NULL,
                headers_json TEXT NOT NULL,
                body BLOB NOT NULL,
                stored_at REAL NOT NULL,
                max_age REAL NOT NULL,
                etag TEXT,
                last_modified TEXT,
                vary_headers TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_http_cache_stored ON http_cache(stored_at);
            CREATE TABLE IF NOT EXISTS script_cache (
                url TEXT PRIMARY KEY,
                bytecode BLOB NOT NULL,
                sha256 TEXT NOT NULL,
                stored_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_script_cache_stored ON script_cache(stored_at);",
        )?;

        // Migration check for vary_headers
        let has_vary = {
            let mut stmt = conn.prepare("PRAGMA table_info(http_cache);")?;
            let names: Vec<String> = stmt
                .query_map([], |row| {
                    let name: String = row.get(1)?;
                    Ok(name)
                })?
                .filter_map(Result::ok)
                .collect();
            names.iter().any(|name| name == "vary_headers")
        };

        if !has_vary {
            let _ = conn.execute("ALTER TABLE http_cache ADD COLUMN vary_headers TEXT;", []);
        }

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
        })
    }

    /// Retrieve a cached response for `url`. If expired, returns entry for revalidation.
    pub fn get(&self, url: &str) -> Option<CacheEntry> {
        self.get_with_req(url, None)
    }

    /// Retrieve a cached response verifying Vary request headers.
    pub fn get_with_req(&self, url: &str, req_headers: Option<&HashMap<String, String>>) -> Option<CacheEntry> {
        let conn = self.conn.lock().ok()?;
        let mut stmt = conn
            .prepare(
                "SELECT url, status, headers_json, body, stored_at, vary_headers
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
                let vary_raw: Option<String> = row.get(5).ok();
                Ok((u, status, headers_raw, body, stored_at, vary_raw))
            })
            .ok()?;

        if let Some(Ok((u, status, headers_raw, body, stored_at, vary_raw))) = rows.next() {
            let resp_headers: HashMap<String, String> =
                serde_json::from_str(&headers_raw).unwrap_or_default();
            let vary_map: HashMap<String, String> = vary_raw
                .as_deref()
                .and_then(|raw| serde_json::from_str(raw).ok())
                .unwrap_or_default();

            // Match Vary headers if requested
            if let Some(req_hdrs) = req_headers {
                let lowered_req: HashMap<String, String> = req_hdrs
                    .iter()
                    .map(|(k, v)| (k.to_lowercase(), v.clone()))
                    .collect();

                for (name, val) in &vary_map {
                    let cur = lowered_req.get(&name.to_lowercase()).map(|s| s.as_str()).unwrap_or("");
                    if cur != val.as_str() {
                        return None;
                    }
                }
            }

            Some(CacheEntry {
                url: u,
                status,
                resp_headers,
                body,
                stored_at,
                vary: vary_map,
            })
        } else {
            None
        }
    }

    /// Retrieve cache result categorised by RFC 9111 freshness state.
    pub fn get_result(
        &self,
        url: &str,
        req_headers: Option<&HashMap<String, String>>,
        now: Option<f64>,
    ) -> CacheGetResult {
        match self.get_with_req(url, req_headers) {
            Some(entry) => {
                if entry.is_fresh(now) {
                    CacheGetResult::Hit(entry)
                } else if entry.has_validator() {
                    CacheGetResult::Revalidate(entry)
                } else {
                    CacheGetResult::Stale(entry)
                }
            }
            None => CacheGetResult::Miss,
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
        let vary_json = if !entry.vary.is_empty() {
            Some(serde_json::to_string(&entry.vary).unwrap_or_default())
        } else {
            None
        };

        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "INSERT OR REPLACE INTO http_cache
                 (url, status, headers_json, body, stored_at, max_age, etag, last_modified, vary_headers)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    entry.url,
                    entry.status,
                    headers_json,
                    entry.body,
                    entry.stored_at,
                    max_age,
                    etag,
                    last_modified,
                    vary_json,
                ],
            );
        }
    }

    /// Renew headers on 304 Not Modified without rewriting the cached body BLOB.
    pub fn renew(&self, url: &str, status: u16, headers: &HashMap<String, String>) -> bool {
        let conn = match self.conn.lock() {
            Ok(c) => c,
            Err(_) => return false,
        };

        let old_raw: Option<String> = {
            let mut stmt = match conn.prepare("SELECT headers_json FROM http_cache WHERE url = ?1") {
                Ok(s) => s,
                Err(_) => return false,
            };
            let mut rows = match stmt.query_map(params![url], |row| row.get(0)) {
                Ok(r) => r,
                Err(_) => return false,
            };
            rows.next().and_then(Result::ok)
        };

        if let Some(old_raw) = old_raw {
            let mut old_headers: HashMap<String, String> =
                serde_json::from_str(&old_raw).unwrap_or_default();

            for (k, v) in headers {
                let kl = k.to_lowercase();
                if kl != "content-length" && kl != "content-encoding" && kl != "transfer-encoding" {
                    old_headers.insert(kl, v.clone());
                }
            }

            let cc_val = old_headers.get("cache-control").cloned().unwrap_or_default();
            let cc = parse_cache_control(&cc_val);
            let mut max_age = 0.0;
            if let Some(ma) = cc.get("max-age").and_then(|s| s.parse::<f64>().ok()) {
                max_age = ma.max(0.0);
            }

            let etag = old_headers.get("etag").cloned();
            let last_modified = old_headers.get("last-modified").cloned();
            let headers_json = serde_json::to_string(&old_headers).unwrap_or_default();
            let now = now_secs();

            let res = conn.execute(
                "UPDATE http_cache
                 SET status = ?1, headers_json = ?2, stored_at = ?3, max_age = ?4, etag = ?5, last_modified = ?6
                 WHERE url = ?7",
                params![status, headers_json, now, max_age, etag, last_modified, url],
            );
            res.is_ok()
        } else {
            false
        }
    }

    /// Retrieve cached bytecode for a script URL if sha256 matches.
    pub fn get_script_cache(&self, url: &str, sha256: Option<&str>) -> Option<Vec<u8>> {
        let conn = self.conn.lock().ok()?;
        if let Some(hash) = sha256 {
            let mut stmt = conn
                .prepare("SELECT bytecode FROM script_cache WHERE url = ?1 AND sha256 = ?2")
                .ok()?;
            let mut rows = stmt
                .query_map(params![url, hash], |row| row.get::<_, Vec<u8>>(0))
                .ok()?;
            rows.next().and_then(Result::ok)
        } else {
            let mut stmt = conn
                .prepare("SELECT bytecode FROM script_cache WHERE url = ?1")
                .ok()?;
            let mut rows = stmt
                .query_map(params![url], |row| row.get::<_, Vec<u8>>(0))
                .ok()?;
            rows.next().and_then(Result::ok)
        }
    }

    /// Store compiled script bytecode in cache.
    pub fn put_script_cache(&self, url: &str, bytecode: &[u8], sha256: &str) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute(
                "INSERT OR REPLACE INTO script_cache (url, bytecode, sha256, stored_at)
                 VALUES (?1, ?2, ?3, ?4)",
                params![url, bytecode, sha256, now_secs()],
            );
        }
    }

    /// Remove an entry from the cache.
    pub fn invalidate(&self, url: &str) {
        if let Ok(conn) = self.conn.lock() {
            let _ = conn.execute("DELETE FROM http_cache WHERE url = ?1", params![url]);
            let _ = conn.execute("DELETE FROM script_cache WHERE url = ?1", params![url]);
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

        let mut resp_headers = HashMap::new();
        resp_headers.insert("cache-control".to_string(), "max-age=300".to_string());
        resp_headers.insert("etag".to_string(), "\"abc\"".to_string());

        let entry = CacheEntry {
            url: url.to_string(),
            status: 200,
            resp_headers,
            body: b"cached body".to_vec(),
            stored_at: now_secs(),
            vary: HashMap::new(),
        };

        cache.store(&entry);

        let retrieved = cache.get(url).expect("should find cached entry");
        assert_eq!(retrieved.body, b"cached body");
        assert_eq!(retrieved.status, 200);

        match cache.get_result(url, None, None) {
            CacheGetResult::Hit(e) => assert_eq!(e.body, b"cached body"),
            other => panic!("expected Hit, got {:?}", other),
        }

        // Test 304 renewal
        let mut renew_headers = HashMap::new();
        renew_headers.insert("cache-control".to_string(), "max-age=600".to_string());
        let renewed = cache.renew(url, 304, &renew_headers);
        assert!(renewed);

        let updated = cache.get(url).unwrap();
        assert_eq!(updated.body, b"cached body");

        // Test script cache
        cache.put_script_cache("https://example.com/bundle.js", b"compiled-code", "hash123");
        let bc = cache.get_script_cache("https://example.com/bundle.js", Some("hash123"));
        assert_eq!(bc, Some(b"compiled-code".to_vec()));

        cache.invalidate(url);
        assert!(cache.get(url).is_none());
    }
}
