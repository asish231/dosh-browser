//! dosh-browser-core — the pure-Rust dosh-browser product.
//!
//! Consumes `doshgentv8-core` (V8 engine) and implements the `HostSeam`
//! trait natively in Rust. Replaces `webresearch/engine/runtime.py` and
//! the Python host-callable seam.
//!
//! Port strategy: incremental parity. Each module (fetch, tree, crypto,
//! session) is ported and tested against the Python oracle before the
//! full product switch.

mod fetch;
mod tree;
mod download;
mod video;
pub mod session;
pub mod types;
mod content;
mod links;
pub mod acd;

use doshgentv8_core::{DoshEngine, HostSeam};
use sha2::{Digest, Sha256, Sha384, Sha512};
use std::sync::Arc;
use std::sync::Mutex;
use reqwest::Client;

/// Local wrapper to satisfy the orphan rule for `impl HostSeam`.
struct SharedSeam(Mutex<InnerSeam>);

struct InnerSeam {
    client: Client,
}

impl HostSeam for SharedSeam {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
        let mut this = self.0.lock().map_err(|_| "lock poisoned")?;
        this.call(name, args)
    }
}

/// The Rust product's host seam implementation. Implements `HostSeam`
/// so `doshgentv8-core` can call back into Rust for fetch, parse, crypto, etc.
pub struct RustProduct {
    engine: DoshEngine,
    client: Client,
}

impl RustProduct {
    pub fn new() -> Result<Self, String> {
        let client = Client::builder()
            .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            .build()
            .map_err(|e| format!("reqwest client init failed: {e}"))?;
        
        // The engine needs a HostSeam at creation. We wrap InnerSeam in
        // SharedSeam (which implements HostSeam via Mutex).
        let seam = SharedSeam(Mutex::new(InnerSeam {
            client: client.clone(),
        }));
        let engine = DoshEngine::new(Arc::new(seam))?;
        Ok(Self { engine, client })
    }

    pub fn eval(&mut self, code: &str) -> Result<String, String> {
        self.engine.eval(code)
    }

    pub fn pump_jobs(&mut self) -> bool {
        self.engine.pump_jobs()
    }

    pub fn set_time_limit(&mut self, seconds: f64) {
        self.engine.set_time_limit(seconds);
    }

    pub fn set_memory_limit(&mut self, bytes: usize) {
        self.engine.set_memory_limit(bytes);
    }

    pub fn add_host(&mut self, name: &str) -> Result<(), String> {
        self.engine.add_host(name)
    }
}

impl InnerSeam {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
        match name {
            "host_fetch" => {
                // args: [url, method, body, headers_json, body_b64]
                let url = args.first().cloned().unwrap_or_default();
                let method = args.get(1).cloned().unwrap_or_else(|| "GET".into());
                let body = args.get(2).cloned();
                let headers_json = args.get(3).cloned().unwrap_or_default();
                
                // We need an async runtime to run fetch. We spawn a single-threaded
                // one here. In the final product, the product loop will be async.
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .map_err(|e| format!("tokio runtime failed: {e}"))?;
                
                let result = rt.block_on(fetch::fetch(
                    &self.client,
                    &url,
                    &method,
                    body,
                    &headers_json,
                    8_000_000,
                )).map_err(|e| format!("fetch error: {e}"))?;
                
                Ok(Some(result.to_json()))
            }
            "host_parse_html" => {
                let html = args.first().cloned().unwrap_or_default();
                let tree = tree::html_to_tree(&html);
                Ok(Some(serde_json::to_string(&tree).unwrap_or_default()))
            }
            "host_crypto_digest" => {
                let algo = args.first().cloned().unwrap_or_else(|| "sha256".into());
                let data_hex = args.get(1).cloned().unwrap_or_default();
                let bytes = hex::decode(&data_hex).unwrap_or_default();
                let hash = match algo.to_lowercase().as_str() {
                    "sha256" => hex::encode(Sha256::digest(&bytes)),
                    "sha384" => hex::encode(Sha384::digest(&bytes)),
                    "sha512" => hex::encode(Sha512::digest(&bytes)),
                    _ => return Err(format!("unknown digest algorithm: {algo}")),
                };
                Ok(Some(hash))
            }
            "host_log" => {
                if args.len() >= 2 {
                    eprintln!("[{}] {}", args[1], args[0]);
                } else if let Some(msg) = args.first() {
                    eprintln!("[log] {msg}");
                }
                Ok(None)
            }
            "host_get_cookie" => Ok(Some(String::new())),
            "host_set_cookie" | "host_load_module" | "host_post_message" => Ok(Some(String::new())),
            "host_ws_connect" | "host_ws_send" | "host_ws_send_binary" | "host_ws_close" => {
                Ok(Some(String::from("{}")))
            }
            _ => Err(format!("unknown host callable: {name}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_boot() {
        let mut prod = RustProduct::new().unwrap();
        assert_eq!(prod.eval("1 + 1").unwrap(), "2");
        assert_eq!(prod.eval("typeof document").unwrap(), "undefined");
    }

    #[test]
    fn test_host_parse_html() {
        let mut prod = RustProduct::new().unwrap();
        prod.add_host("host_parse_html").unwrap();
        let result = prod.eval("host_parse_html('<li>hello</li>')").unwrap();
        assert!(result.contains("hello"));
        assert!(result.contains("li"));
    }

    #[test]
    fn test_host_crypto_sha256_via_engine() {
        let mut prod = RustProduct::new().unwrap();
        prod.add_host("host_crypto_digest").unwrap();
        let result = prod
            .eval("host_crypto_digest('sha256', '00ff')")
            .unwrap();
        // SHA256 of 0x00 0xff is 14 bytes long in hex (64 chars)
        assert_eq!(result.len(), 64);
    }
}
