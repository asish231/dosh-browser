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
pub mod registry;
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
pub mod assist;
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
pub mod ws;
pub mod web_bot_auth;
pub mod cache_sqlite;
pub mod network;
pub mod tls_policy;
pub mod identity;
pub mod daemon_client;

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

    #[test]
    fn test_daemon_client_unavailable() {
        // When no daemon is running, is_available should return false
        let client = daemon_client::DaemonClient::new(Some("/tmp/nonexistent.sock"));
        assert!(!client.is_available());
    }

    #[test]
    fn test_daemon_client_ping_unavailable() {
        // When no daemon is running, ping should return an error
        let client = daemon_client::DaemonClient::new(Some("/tmp/nonexistent.sock"));
        let result = client.ping();
        assert!(result.is_err());
    }

    #[test]
    fn test_daemon_client_available() {
        // When the daemon IS running, is_available should return true
        let socket_path = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()) + "/.dosh/loom.sock";
        if std::path::Path::new(&socket_path).exists() {
            let client = daemon_client::DaemonClient::new(Some(&socket_path));
            assert!(client.is_available());
            // Try a ping
            let result = client.ping();
            assert!(result.is_ok(), "ping should succeed when daemon is running");
            assert_eq!(result.unwrap(), true);
        } else {
            // Daemon not running, skip this test
            eprintln!("Skipping test_daemon_client_available - daemon not running");
        }
    }

    #[test]
    fn test_daemon_client_attach_and_list() {
        // Test attach + list when daemon is running
        let socket_path = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string()) + "/.dosh/loom.sock";
        if !std::path::Path::new(&socket_path).exists() {
            eprintln!("Skipping test_daemon_client_attach_and_list - daemon not running");
            return;
        }

        let client = daemon_client::DaemonClient::new(Some(&socket_path));

        // Attach to a session (persistent connection)
        let mut sess = match daemon_client::Session::attach(Some(&socket_path), Some("rust_test_session")) {
            Ok(s) => s,
            Err(e) => panic!("attach should succeed: {}", e),
        };
        assert_eq!(sess.session_id, "rust_test_session");

        // List sessions
        let result = client.list();
        assert!(result.is_ok(), "list should succeed: {:?}", result);
        let resp = result.unwrap();
        assert!(resp.ok);
        let sessions = resp.sessions.unwrap_or_default();
        assert!(sessions.iter().any(|s| s.get("session_id").and_then(|v| v.as_str()) == Some("rust_test_session")),
                "rust_test_session should be in the session list");
        let _ = sess.detach();
    }
}
