// src/lib.rs
// Pure-Rust dosh-browser core crate.

pub mod types;
pub mod fetch;
pub mod tree;
pub mod content;
pub mod links;
pub mod forms;
pub mod actions;
pub mod acd;
pub mod download;
pub mod video;
pub mod consent;
pub mod runtime;
pub mod session;
pub mod shell;
pub mod server;
pub mod mcp;
pub mod telemetry;
pub mod login;
pub mod frames;
pub mod security;
pub mod trackers;
pub mod search;
pub mod captcha;
pub mod device;
pub mod extract;
pub mod kinematics;
pub mod onion;
pub mod framing;
pub mod hsts;
pub mod cache;
pub mod safebrowsing;
pub mod cross_origin;
pub mod binary;
pub mod read;
pub mod dispatch;
pub mod modules;
pub mod tools;
pub mod client;
pub mod config;
pub mod agent;
pub mod provenance;
pub mod features;
pub mod classifier;
pub mod cluster;
pub mod neural;
pub mod ip_filter;
pub mod singleflight;
pub mod rate_limiter;
pub mod robots;
pub mod cors;
pub mod web_bot_auth;
pub mod cache_sqlite;
pub mod network;
pub mod tls_policy;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_boot() {
        assert_eq!(2 + 2, 4);
    }

    #[test]
    fn test_host_crypto_sha256_via_engine() {
        use sha2::{Sha256, Digest};
        let mut hasher = Sha256::new();
        hasher.update(b"dosh-browser");
        let result = hasher.finalize();
        let hex_str = hex::encode(result);
        assert_eq!(hex_str.len(), 64);
    }

    #[test]
    fn test_host_parse_html() {
        let html = "<html><head><title>Test</title></head><body><h1>Hello</h1></body></html>";
        let tree_val = tree::html_to_tree(html);
        assert_eq!(tree_val.get("t").and_then(|v| v.as_str()), Some("document"));
    }
}
