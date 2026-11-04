//! `tls_policy.rs` — TLS interstitial model: classify transport TLS failures, decide explicitly.
//!
//! Default is deny. Proceeding past an overridable certificate error requires an
//! explicit operator decision (`WEBRESEARCH_TLS_PROCEED_HOSTS`) and is recorded.
//! Non-overridable errors (wrong host, HSTS-pinned host) can never proceed.

use std::env;
use std::sync::Mutex;

static DECISIONS: Mutex<Vec<TlsDecision>> = Mutex::new(Vec::new());

#[derive(Debug, Clone)]
pub struct TlsDecision {
    pub host: String,
    pub error_kind: String,
    pub overridable: bool,
    pub decision: String, // "proceed" | "deny"
}

fn _env_hosts(name: &str) -> Vec<String> {
    env::var(name)
        .unwrap_or_default()
        .replace('\n', ",")
        .split(',')
        .map(|s| s.trim().to_lowercase().trim_start_matches('.').to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

pub fn classify_tls_error(err_text: &str) -> Option<&'static str> {
    let text = err_text.to_lowercase();
    if !text.contains("ssl") && !text.contains("tls") && !text.contains("certificate") && !text.contains("handshake") {
        return None;
    }
    if text.contains("hostname") || text.contains("idna") || text.contains("doesn't match") || text.contains("mismatch") {
        return Some("wrong-host");
    }
    if text.contains("expired") {
        return Some("expired");
    }
    if text.contains("self-signed") || text.contains("self signed") || text.contains("unknown ca") || text.contains("unable to get local issuer") || text.contains("verify failed") {
        return Some("untrusted-chain");
    }
    Some("generic")
}

pub fn is_overridable(kind: &str, host: &str) -> bool {
    if kind == "wrong-host" {
        return false;
    }
    let pinned = _env_hosts("WEBRESEARCH_HSTS_PRELOAD");
    let h = host.to_lowercase();
    for p in &pinned {
        if h == *p || h.ends_with(&format!(".{}", p)) {
            return false;
        }
    }
    true
}

pub fn decision_for_host(host: &str, kind: &str) -> &'static str {
    if !is_overridable(kind, host) {
        return "deny";
    }
    let allowed = _env_hosts("WEBRESEARCH_TLS_PROCEED_HOSTS");
    let h = host.to_lowercase();
    for a in &allowed {
        if h == *a || h.ends_with(&format!(".{}", a)) {
            return "proceed";
        }
    }
    "deny"
}

pub fn handle_tls_error(host: &str, err_text: &str) -> Option<TlsInterstitial> {
    let kind = classify_tls_error(err_text)?;
    let overridable = is_overridable(kind, host);
    let decision = decision_for_host(host, kind);
    let interstitial = TlsInterstitial {
        host: host.to_string(),
        kind: kind.to_string(),
        overridable,
        decision: decision.to_string(),
    };
    interstitial.record();
    Some(interstitial)
}

pub struct TlsInterstitial {
    pub host: String,
    pub kind: String,
    pub overridable: bool,
    pub decision: String,
}

impl TlsInterstitial {
    pub fn record(&self) {
        if let Ok(mut decisions) = DECISIONS.lock() {
            decisions.push(TlsDecision {
                host: self.host.clone(),
                error_kind: self.kind.clone(),
                overridable: self.overridable,
                decision: self.decision.clone(),
            });
        }
    }
}

pub fn get_decisions() -> Vec<TlsDecision> {
    DECISIONS.lock().map(|d| d.clone()).unwrap_or_default()
}
