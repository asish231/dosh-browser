//! `robots.rs` — RFC 9309 Robots Exclusion Protocol Parser and Store for Dosh-Browser.
//!
//! Evaluates whether an agent or crawler is permitted to access a given URL path
//! based on the site's `robots.txt` specifications.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

/// A path rule inside a robots.txt group.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RuleType {
    Allow,
    Disallow,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathRule {
    pub pattern: String,
    pub rule_type: RuleType,
}

impl PathRule {
    /// Check if this rule matches the requested path.
    pub fn matches(&self, path: &str) -> bool {
        if self.pattern.is_empty() {
            return false;
        }

        // Standard RFC 9309 prefix and pattern matching
        if self.pattern.ends_with('$') {
            let prefix = &self.pattern[..self.pattern.len() - 1];
            path == prefix
        } else if self.pattern.contains('*') {
            pattern_match(&self.pattern, path)
        } else {
            path.starts_with(&self.pattern)
        }
    }
}

/// Simple glob-style pattern matcher for robots.txt (* wildcard).
fn pattern_match(pattern: &str, path: &str) -> bool {
    let parts: Vec<&str> = pattern.split('*').collect();
    if parts.is_empty() {
        return true;
    }

    let mut remaining = path;
    if !parts[0].is_empty() {
        if !remaining.starts_with(parts[0]) {
            return false;
        }
        remaining = &remaining[parts[0].len()..];
    }

    for (i, part) in parts[1..].iter().enumerate() {
        let is_last = i == parts.len() - 2;
        if part.is_empty() {
            continue;
        }
        if is_last && pattern.ends_with('$') {
            let actual_part = &part[..part.len() - 1];
            return remaining.ends_with(actual_part);
        }
        if let Some(pos) = remaining.find(part) {
            remaining = &remaining[pos + part.len()..];
        } else {
            return false;
        }
    }

    true
}

/// A parsed user-agent group inside robots.txt.
#[derive(Debug, Clone, Default)]
pub struct UserAgentGroup {
    pub user_agents: Vec<String>,
    pub rules: Vec<PathRule>,
    pub crawl_delay: Option<Duration>,
}

/// Complete parsed policy for an origin.
#[derive(Debug, Clone, Default)]
pub struct RobotsPolicy {
    pub groups: Vec<UserAgentGroup>,
    pub sitemaps: Vec<String>,
    pub cached_at: Option<Instant>,
    pub ttl: Duration,
}

impl RobotsPolicy {
    /// Parse raw robots.txt content according to RFC 9309.
    pub fn parse(text: &str) -> Self {
        let mut groups: Vec<UserAgentGroup> = Vec::new();
        let mut sitemaps = Vec::new();

        let mut current_uas: Vec<String> = Vec::new();
        let mut current_rules: Vec<PathRule> = Vec::new();
        let mut current_delay: Option<Duration> = None;

        for line in text.lines() {
            // Strip comments
            let clean = line.split('#').next().unwrap_or("").trim();
            if clean.is_empty() {
                continue;
            }

            let mut split = clean.splitn(2, ':');
            let key = split.next().unwrap_or("").trim().to_lowercase();
            let val = split.next().unwrap_or("").trim();

            match key.as_str() {
                "user-agent" => {
                    // Starting a new group if we already collected rules
                    if !current_rules.is_empty() || current_delay.is_some() {
                        if !current_uas.is_empty() {
                            groups.push(UserAgentGroup {
                                user_agents: current_uas.clone(),
                                rules: current_rules.clone(),
                                crawl_delay: current_delay,
                            });
                        }
                        current_uas.clear();
                        current_rules.clear();
                        current_delay = None;
                    }
                    current_uas.push(val.to_lowercase());
                }
                "disallow" => {
                    if !val.is_empty() {
                        current_rules.push(PathRule {
                            pattern: val.to_string(),
                            rule_type: RuleType::Disallow,
                        });
                    }
                }
                "allow" => {
                    if !val.is_empty() {
                        current_rules.push(PathRule {
                            pattern: val.to_string(),
                            rule_type: RuleType::Allow,
                        });
                    }
                }
                "crawl-delay" => {
                    if let Ok(secs) = val.parse::<f64>() {
                        current_delay = Some(Duration::from_secs_f64(secs));
                    }
                }
                "sitemap" => {
                    sitemaps.push(val.to_string());
                }
                _ => {}
            }
        }

        if !current_uas.is_empty() {
            groups.push(UserAgentGroup {
                user_agents: current_uas,
                rules: current_rules,
                crawl_delay: current_delay,
            });
        }

        Self {
            groups,
            sitemaps,
            cached_at: Some(Instant::now()),
            ttl: Duration::from_secs(86400), // 24-hour default TTL
        }
    }

    /// Check if `path` is allowed for a given `user_agent`.
    pub fn is_allowed(&self, path: &str, user_agent: &str) -> bool {
        let ua_clean = user_agent.to_lowercase();

        // 1. Find the most specific user-agent group matching this client
        let group = self
            .groups
            .iter()
            .find(|g| g.user_agents.iter().any(|u| u != "*" && ua_clean.contains(u)))
            .or_else(|| self.groups.iter().find(|g| g.user_agents.iter().any(|u| u == "*")));

        let group = match group {
            Some(g) => g,
            None => return true, // No matching group means unrestricted
        };

        // 2. Find matching rules
        let mut best_match_len = 0;
        let mut decision = true; // Default allow

        for rule in &group.rules {
            if rule.matches(path) {
                let len = rule.pattern.len();
                // RFC 9309 Section 2.2.2: Longest match wins. On tie, Allow wins.
                if len > best_match_len {
                    best_match_len = len;
                    decision = rule.rule_type == RuleType::Allow;
                } else if len == best_match_len && rule.rule_type == RuleType::Allow {
                    decision = true;
                }
            }
        }

        decision
    }

    /// Crawl delay for this user agent if specified.
    pub fn crawl_delay(&self, user_agent: &str) -> Option<Duration> {
        let ua_clean = user_agent.to_lowercase();
        self.groups
            .iter()
            .find(|g| g.user_agents.iter().any(|u| u != "*" && ua_clean.contains(u)))
            .or_else(|| self.groups.iter().find(|g| g.user_agents.iter().any(|u| u == "*")))
            .and_then(|g| g.crawl_delay)
    }

    /// Check if cached policy is still fresh.
    pub fn is_fresh(&self) -> bool {
        match self.cached_at {
            Some(t) => t.elapsed() < self.ttl,
            None => false,
        }
    }
}

/// In-memory origin store for cached robots.txt policies.
#[derive(Clone, Default)]
pub struct RobotsStore {
    policies: Arc<Mutex<HashMap<String, RobotsPolicy>>>,
}

impl RobotsStore {
    pub fn new() -> Self {
        Self {
            policies: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Store a parsed policy for an origin (e.g. `https://example.com`).
    pub async fn set(&self, origin: &str, policy: RobotsPolicy) {
        let mut map = self.policies.lock().await;
        map.insert(origin.to_string(), policy);
    }

    /// Retrieve policy for an origin if present and fresh.
    pub async fn get(&self, origin: &str) -> Option<RobotsPolicy> {
        let mut map = self.policies.lock().await;
        if let Some(p) = map.get(origin) {
            if p.is_fresh() {
                return Some(p.clone());
            } else {
                map.remove(origin);
            }
        }
        None
    }

    /// Fast check if a URL is permitted.
    pub async fn check_url(&self, url: &url::Url, user_agent: &str) -> bool {
        let origin = format!(
            "{}://{}{}",
            url.scheme(),
            url.host_str().unwrap_or(""),
            url.port().map(|p| format!(":{}", p)).unwrap_or_default()
        );
        let path = url.path();
        if let Some(policy) = self.get(&origin).await {
            policy.is_allowed(path, user_agent)
        } else {
            true // If no robots.txt loaded or accessible, default permit
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE_ROBOTS: &str = r#"
# Sample robots.txt
User-agent: Googlebot
Disallow: /nogoogle/

User-agent: dosh-browser
Disallow: /admin/
Allow: /admin/public/
Crawl-delay: 2.5

User-agent: *
Disallow: /private/
Disallow: /secret/*.pdf
Allow: /private/open$
Sitemap: https://example.com/sitemap.xml
"#;

    #[test]
    fn test_robots_parsing_and_sitemap() {
        let policy = RobotsPolicy::parse(SAMPLE_ROBOTS);
        assert_eq!(policy.groups.len(), 3);
        assert_eq!(policy.sitemaps.len(), 1);
        assert_eq!(policy.sitemaps[0], "https://example.com/sitemap.xml");
    }

    #[test]
    fn test_specific_user_agent_rules() {
        let policy = RobotsPolicy::parse(SAMPLE_ROBOTS);

        // Client identifies as "dosh-browser/1.0"
        let ua = "dosh-browser/1.0";
        assert!(!policy.is_allowed("/admin/dashboard", ua));
        // More specific Allow rule wins over Disallow
        assert!(policy.is_allowed("/admin/public/login", ua));
        // Crawl delay
        assert_eq!(policy.crawl_delay(ua), Some(Duration::from_millis(2500)));

        // Path not covered by dosh-browser group falls back to allow
        assert!(policy.is_allowed("/other/page", ua));
    }

    #[test]
    fn test_wildcard_user_agent_rules() {
        let policy = RobotsPolicy::parse(SAMPLE_ROBOTS);
        let ua = "GenericAgent/2.0";

        assert!(!policy.is_allowed("/private/data", ua));
        // Suffix $ exact match allow
        assert!(policy.is_allowed("/private/open", ua));
        // Wildcard match
        assert!(!policy.is_allowed("/secret/annual-report.pdf", ua));
        assert!(policy.is_allowed("/secret/annual-report.html", ua));
    }

    #[tokio::test]
    async fn test_robots_store_cache() {
        let store = RobotsStore::new();
        let origin = "https://example.com";
        let policy = RobotsPolicy::parse(SAMPLE_ROBOTS);

        store.set(origin, policy).await;

        let url1 = url::Url::parse("https://example.com/admin/settings").unwrap();
        assert!(!store.check_url(&url1, "dosh-browser").await);

        let url2 = url::Url::parse("https://example.com/admin/public/faq").unwrap();
        assert!(store.check_url(&url2, "dosh-browser").await);
    }
}

