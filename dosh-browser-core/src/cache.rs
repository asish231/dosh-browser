//! HTTP private RFC 9111 in-memory cache with Vary, freshness, and revalidation.
//! Direct port of `webresearch/cache.py`.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

pub const MAX_ENTRIES: usize = 200;
pub const MAX_BODY_BYTES: usize = 2 * 1024 * 1024;

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Parses standard HTTP Cache-Control header directives.
pub fn parse_cache_control(value: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for part in value.split(',') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some((k, v)) = trimmed.split_once('=') {
            out.insert(k.trim().to_lowercase(), v.trim().trim_matches('"').to_string());
        } else {
            out.insert(trimmed.to_lowercase(), "true".to_string());
        }
    }
    out
}

#[derive(Debug, Clone)]
pub struct CacheEntry {
    pub url: String,
    pub status: u16,
    pub resp_headers: HashMap<String, String>,
    pub body: Vec<u8>,
    pub stored_at: f64,
    pub vary: HashMap<String, String>,
}

impl CacheEntry {
    pub fn age(&self, now: f64) -> f64 {
        (now - self.stored_at).max(0.0)
    }

    pub fn freshness_lifetime(&self) -> Option<f64> {
        let cc_val = self.resp_headers.get("cache-control").cloned().unwrap_or_default();
        let cc = parse_cache_control(&cc_val);
        if cc.contains_key("no-store") || cc.contains_key("no-cache") {
            return Some(0.0);
        }
        if let Some(max_age_str) = cc.get("max-age") {
            if let Ok(secs) = max_age_str.parse::<f64>() {
                return Some(secs.max(0.0));
            }
        }
        None
    }

    pub fn is_fresh(&self, now: Option<f64>) -> bool {
        let t = now.unwrap_or_else(now_secs);
        match self.freshness_lifetime() {
            Some(lifetime) => self.age(t) < lifetime,
            None => false,
        }
    }

    pub fn has_validator(&self) -> bool {
        self.resp_headers.contains_key("etag") || self.resp_headers.contains_key("last-modified")
    }

    pub fn conditional_headers(&self) -> HashMap<String, String> {
        let mut out = HashMap::new();
        if let Some(etag) = self.resp_headers.get("etag") {
            out.insert("if-none-match".to_string(), etag.clone());
        } else if let Some(lm) = self.resp_headers.get("last-modified") {
            out.insert("if-modified-since".to_string(), lm.clone());
        }
        out
    }
}

pub struct HttpCache {
    entries: HashMap<String, Vec<CacheEntry>>,
    max_entries: usize,
}

impl Default for HttpCache {
    fn default() -> Self {
        Self::new(MAX_ENTRIES)
    }
}

impl HttpCache {
    pub fn new(max_entries: usize) -> Self {
        Self {
            entries: HashMap::new(),
            max_entries,
        }
    }

    fn vary_key_matches(entry: &CacheEntry, req_headers: &HashMap<String, String>) -> bool {
        let lowered_req: HashMap<String, String> = req_headers
            .iter()
            .map(|(k, v)| (k.to_lowercase(), v.clone()))
            .collect();

        for (name, value) in &entry.vary {
            if lowered_req.get(name).map(|s| s.as_str()).unwrap_or("") != value.as_str() {
                return false;
            }
        }
        true
    }

    pub fn lookup(&self, method: &str, url: &str, req_headers: &HashMap<String, String>) -> Option<CacheEntry> {
        if !method.eq_ignore_ascii_case("GET") {
            return None;
        }
        let req_cc = parse_cache_control(
            req_headers.get("cache-control").map(|s| s.as_str()).unwrap_or("")
        );
        if req_cc.contains_key("no-store") || req_cc.contains_key("no-cache") {
            return None;
        }
        if let Some(bucket) = self.entries.get(url) {
            for entry in bucket {
                if Self::vary_key_matches(entry, req_headers) {
                    return Some(entry.clone());
                }
            }
        }
        None
    }

    pub fn store(
        &mut self,
        method: &str,
        url: &str,
        req_headers: &HashMap<String, String>,
        status: u16,
        resp_headers: &HashMap<String, String>,
        body: &[u8],
    ) -> bool {
        if !method.eq_ignore_ascii_case("GET") {
            return false;
        }
        let cacheable_status = matches!(status, 200 | 203 | 204 | 300 | 301 | 308 | 404 | 405 | 414 | 501);
        if !cacheable_status {
            return false;
        }
        if body.len() > MAX_BODY_BYTES {
            return false;
        }

        let lowered_resp: HashMap<String, String> = resp_headers
            .iter()
            .map(|(k, v)| (k.to_lowercase(), v.clone()))
            .collect();

        let cc = parse_cache_control(
            lowered_resp.get("cache-control").map(|s| s.as_str()).unwrap_or("")
        );
        if cc.contains_key("no-store") {
            return false;
        }

        let vary_str = lowered_resp.get("vary").map(|s| s.as_str()).unwrap_or("");
        let vary_names: Vec<String> = vary_str
            .split(',')
            .map(|v| v.trim().to_lowercase())
            .filter(|v| !v.is_empty())
            .collect();

        if vary_names.iter().any(|v| v == "*") {
            return false;
        }

        let lowered_req: HashMap<String, String> = req_headers
            .iter()
            .map(|(k, v)| (k.to_lowercase(), v.clone()))
            .collect();

        let mut vary = HashMap::new();
        for name in vary_names {
            let val = lowered_req.get(&name).cloned().unwrap_or_default();
            vary.insert(name, val);
        }

        let entry = CacheEntry {
            url: url.to_string(),
            status,
            resp_headers: lowered_resp,
            body: body.to_vec(),
            stored_at: now_secs(),
            vary: vary.clone(),
        };

        let bucket = self.entries.entry(url.to_string()).or_default();
        bucket.retain(|e| e.vary != vary);
        bucket.push(entry);

        let total: usize = self.entries.values().map(|v| v.len()).sum();
        if total > self.max_entries {
            if let Some(first_key) = self.entries.keys().next().cloned() {
                if let Some(b) = self.entries.get_mut(&first_key) {
                    if !b.is_empty() {
                        b.remove(0);
                    }
                }
                if self.entries.get(&first_key).map(|b| b.is_empty()).unwrap_or(false) {
                    self.entries.remove(&first_key);
                }
            }
        }
        true
    }

    pub fn update_on_304(&mut self, url: &str, vary: &HashMap<String, String>, resp_headers: &HashMap<String, String>) {
        if let Some(bucket) = self.entries.get_mut(url) {
            for entry in bucket.iter_mut() {
                if &entry.vary == vary {
                    for (k, v) in resp_headers {
                        let kl = k.to_lowercase();
                        if kl != "content-length" && kl != "content-encoding" && kl != "transfer-encoding" {
                            entry.resp_headers.insert(kl, v.clone());
                        }
                    }
                    entry.stored_at = now_secs();
                    break;
                }
            }
        }
    }

    pub fn clear(&mut self) {
        self.entries.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_cache_control() {
        let cc = parse_cache_control("max-age=3600, no-transform, must-revalidate");
        assert_eq!(cc.get("max-age").map(|s| s.as_str()), Some("3600"));
        assert_eq!(cc.get("no-transform").map(|s| s.as_str()), Some("true"));
        assert_eq!(cc.get("must-revalidate").map(|s| s.as_str()), Some("true"));
    }

    #[test]
    fn test_cache_store_and_lookup() {
        let mut cache = HttpCache::new(50);
        let mut req_hdrs = HashMap::new();
        req_hdrs.insert("Accept".to_string(), "text/html".to_string());

        let mut resp_hdrs = HashMap::new();
        resp_hdrs.insert("Cache-Control".to_string(), "max-age=600".to_string());
        resp_hdrs.insert("ETag".to_string(), "\"12345\"".to_string());
        resp_hdrs.insert("Vary".to_string(), "Accept".to_string());

        let ok = cache.store("GET", "https://example.com/page", &req_hdrs, 200, &resp_hdrs, b"hello world");
        assert!(ok);

        let hit = cache.lookup("GET", "https://example.com/page", &req_hdrs);
        assert!(hit.is_some());
        let entry = hit.unwrap();
        assert_eq!(entry.body, b"hello world");
        assert!(entry.is_fresh(None));
        assert!(entry.has_validator());
        let cond = entry.conditional_headers();
        assert_eq!(cond.get("if-none-match").map(|s| s.as_str()), Some("\"12345\""));
    }

    #[test]
    fn test_cache_no_store() {
        let mut cache = HttpCache::new(50);
        let req_hdrs = HashMap::new();
        let mut resp_hdrs = HashMap::new();
        resp_hdrs.insert("Cache-Control".to_string(), "no-store".to_string());

        let stored = cache.store("GET", "https://example.com/secret", &req_hdrs, 200, &resp_hdrs, b"secret");
        assert!(!stored);
        assert!(cache.lookup("GET", "https://example.com/secret", &req_hdrs).is_none());
    }
}

