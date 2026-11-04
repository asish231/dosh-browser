//! Host denylist and Safe-Browsing filtering.
//! Direct port of `webresearch/engine/safebrowsing.py`.

use std::collections::HashSet;
use std::env;

const ENV_VAR: &str = "WEBRESEARCH_BLOCKED_HOSTS";

fn denylist() -> HashSet<String> {
    let raw = env::var(ENV_VAR).unwrap_or_default();
    let mut out = HashSet::new();
    for part in raw.replace('\n', ",").split(',') {
        let host = part.trim().to_lowercase().trim_start_matches('.').to_string();
        if !host.is_empty() {
            out.insert(host);
        }
    }
    out
}

/// Checks whether a given hostname is blocked either exactly or as a subdomain suffix.
pub fn is_blocked_host(hostname: &str) -> bool {
    let host = hostname.trim().trim_matches('.').to_lowercase();
    if host.is_empty() {
        return false;
    }
    let blocked = denylist();
    for b in &blocked {
        if &host == b || host.ends_with(&format!(".{}", b)) {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_blocked_host_logic() {
        env::set_var(ENV_VAR, "malware.example, phishing.org");
        assert!(is_blocked_host("malware.example"));
        assert!(is_blocked_host("sub.malware.example"));
        assert!(is_blocked_host("phishing.org"));
        assert!(!is_blocked_host("safe.example"));
        env::remove_var(ENV_VAR);
    }
}

