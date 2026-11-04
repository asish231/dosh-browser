use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};
use url::Url;

pub const PRELOAD_SEED: &[(&str, bool)] = &[
    ("google.com", true),
    ("youtube.com", true),
    ("facebook.com", true),
    ("twitter.com", true),
    ("instagram.com", true),
    ("linkedin.com", true),
    ("microsoft.com", true),
    ("apple.com", true),
    ("amazon.com", true),
    ("cloudflare.com", true),
    ("github.com", true),
    ("gmail.com", true),
    ("paypal.com", true),
    ("stripe.com", true),
    ("duckduckgo.com", true),
    ("wikipedia.org", true),
    ("mozilla.org", true),
    ("netflix.com", true),
    ("spotify.com", true),
    ("reddit.com", true),
];

#[derive(Debug, Clone)]
pub struct HstsEntry {
    pub host: String,
    pub include_subdomains: bool,
    pub expires_at: u64,
    pub preload: bool,
}

impl HstsEntry {
    pub fn is_live(&self, now: u64) -> bool {
        self.preload || self.expires_at > now
    }

    pub fn covers(&self, host: &str, now: u64) -> bool {
        if !self.is_live(now) {
            return false;
        }
        let h = host.to_lowercase();
        if h == self.host {
            return true;
        }
        if self.include_subdomains && h.ends_with(&format!(".{}", self.host)) {
            return true;
        }
        false
    }
}

pub struct HstsStore {
    entries: HashMap<String, HstsEntry>,
}

impl Default for HstsStore {
    fn default() -> Self {
        Self::new()
    }
}

impl HstsStore {
    pub fn new() -> Self {
        let mut entries = HashMap::new();
        for &(host, subdomains) in PRELOAD_SEED {
            entries.insert(
                host.to_string(),
                HstsEntry {
                    host: host.to_string(),
                    include_subdomains: subdomains,
                    expires_at: 0,
                    preload: true,
                },
            );
        }
        Self { entries }
    }

    pub fn observe_header(&mut self, url_str: &str, sts_value: &str) {
        let Ok(u) = Url::parse(url_str) else {
            return;
        };
        if u.scheme() != "https" {
            return;
        }
        let Some(host) = u.host_str() else {
            return;
        };
        let host = host.to_lowercase();

        let mut max_age: Option<u64> = None;
        let mut include_subdomains = false;

        for part in sts_value.split(';') {
            let part = part.trim().to_lowercase();
            if part.starts_with("max-age=") {
                if let Ok(secs) = part["max-age=".len()..].parse::<u64>() {
                    max_age = Some(secs);
                }
            } else if part == "includesubdomains" {
                include_subdomains = true;
            }
        }

        if let Some(secs) = max_age {
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();

            if secs == 0 {
                self.entries.remove(&host);
            } else {
                self.entries.insert(
                    host.clone(),
                    HstsEntry {
                        host,
                        include_subdomains,
                        expires_at: now + secs,
                        preload: false,
                    },
                );
            }
        }
    }

    pub fn should_upgrade(&self, url_str: &str) -> bool {
        let Ok(u) = Url::parse(url_str) else {
            return false;
        };
        if u.scheme() != "http" {
            return false;
        }
        let Some(host) = u.host_str() else {
            return false;
        };

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        for entry in self.entries.values() {
            if entry.covers(host, now) {
                return true;
            }
        }
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_preload_hsts_upgrade() {
        let store = HstsStore::new();
        assert!(store.should_upgrade("http://google.com/search"));
        assert!(store.should_upgrade("http://sub.google.com/test"));
        assert!(store.should_upgrade("http://github.com/rust-lang"));
        assert!(!store.should_upgrade("http://unencrypted-site.test/"));
    }

    #[test]
    fn test_dynamic_hsts_observe() {
        let mut store = HstsStore::new();
        assert!(!store.should_upgrade("http://my-secure-api.test/"));

        store.observe_header("https://my-secure-api.test/", "max-age=31536000; includeSubDomains");
        assert!(store.should_upgrade("http://my-secure-api.test/"));
        assert!(store.should_upgrade("http://sub.my-secure-api.test/"));
    }
}

