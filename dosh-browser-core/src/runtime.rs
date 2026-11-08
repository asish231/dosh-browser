//! `runtime.rs` — Live page JavaScript runtime for dosh-browser-core.
//!
//! Boots our in-house DOM (`minidom.js`) inside `doshgentv8-core` (V8),
//! implements the 13 synchronous host callables (`HostSeam`), extracts and
//! executes page `<script>` tags in document order, drains timers and microtasks,
//! applies agent actions, and serializes the mutated DOM.
//!
//! Direct Rust counterpart to `webresearch/engine/runtime.py`.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256, Sha384, Sha512};
use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use doshgentv8_core::{DoshActor, DoshActorHandle, HostSeam};

use crate::fetch;
use crate::tree::{self};

/// The embedded minidom.js script (411 KB).
pub const MINIDOM_JS: &str = include_str!("../../webresearch/engine/minidom.js");

const MAX_SCRIPTS: usize = 120;
const MAX_TIMER_PASSES: usize = 25;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConsoleEntry {
    pub level: String,
    pub message: String,
    pub url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub caused_by: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PostMessageEntry {
    pub uid: usize,
    pub data: String,
    pub origin: String,
    pub target_origin: String,
    pub target: String,
    pub ctx: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ports: Option<Vec<Value>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RunScriptsInfo {
    pub ran: bool,
    pub scripts: usize,
    pub modules: usize,
    pub errors: usize,
    pub error_messages: Vec<String>,
    pub console: Vec<ConsoleEntry>,
    pub post_messages: Vec<PostMessageEntry>,
    pub resource_requests: Vec<Value>,
    pub actions_applied: Vec<Value>,
    pub deltas: Vec<Value>,
    pub navigated: Option<String>,
    pub form_submit: Option<Value>,
    pub dialogs: Vec<Value>,
    pub popups: Vec<Value>,
    pub history_pushes: Vec<Value>,
    pub history_replace: Option<Value>,
    pub history_move: i64,
}

/// Inner state backing the 13 host callables.
pub struct InnerSeam {
    pub client: Client,
    pub network: Option<Arc<crate::network::NetworkEngine>>,
    pub url: Mutex<String>,
    pub cookies: Mutex<HashMap<String, String>>,
    pub console: Mutex<Vec<ConsoleEntry>>,
    pub post_messages: Mutex<Vec<PostMessageEntry>>,
    pub post_uid: AtomicUsize,
    pub ctx_key: String,
    /// WebSocket evidence ledger: the engine's ledger when the runtime was
    /// built with network access, else a runtime-local one (disclosed: not
    /// visible to the shell's `net` view in that mode).
    pub ws_ledger: Option<crate::ws::SharedLedger>,
    /// Live socket registry (pump tasks + send/close channels + inbox).
    pub ws_registry: Mutex<Option<crate::ws::WsRegistry>>,
    /// Active causal action ID for tagging network requests and console logs
    /// emitted during an `act` / `trace` pass.
    pub active_cause: Mutex<Option<String>>,
}

/// Thread-safe wrapper implementing HostSeam.
pub struct SharedSeam(pub InnerSeam);

impl HostSeam for SharedSeam {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
        match name {
            "host_parse_html" => {
                let html = args.first().cloned().unwrap_or_default();
                let wrapped = format!("<div id='__frag'>{}</div>", html);
                let tree = tree::html_to_tree(&wrapped);
                if let Some(arr) = tree["c"].as_array() {
                    for child in arr {
                        if child["t"] == "div" && child["a"]["id"] == "__frag" {
                            let frag_c = child["c"].clone();
                            return Ok(Some(serde_json::to_string(&frag_c).unwrap_or_default()));
                        }
                    }
                }
                Ok(Some(serde_json::to_string(&tree["c"]).unwrap_or_default()))
            }
            "host_log" => {
                let msg = args.first().cloned().unwrap_or_default();
                let level = args.get(1).cloned().unwrap_or_else(|| "log".into());
                let current_url = self.0.url.lock().map(|u| u.clone()).unwrap_or_default();
                let caused_by = self.0.active_cause.lock().ok().and_then(|g| g.clone());
                if let Ok(mut cons) = self.0.console.lock() {
                    if cons.len() < 500 {
                        cons.push(ConsoleEntry {
                            level,
                            message: msg,
                            url: current_url,
                            caused_by,
                        });
                    }
                }
                Ok(None)
            }
            "host_crypto_digest" => {
                let algo = args.first().cloned().unwrap_or_else(|| "sha256".into());
                let data_hex = args.get(1).cloned().unwrap_or_default();
                let bytes = hex::decode(&data_hex).unwrap_or_default();
                let hash = match algo.to_lowercase().replace('-', "").as_str() {
                    "sha1" => hex::encode(
                        ring::digest::digest(&ring::digest::SHA1_FOR_LEGACY_USE_ONLY, &bytes).as_ref(),
                    ),
                    "sha256" => hex::encode(Sha256::digest(&bytes)),
                    "sha384" => hex::encode(Sha384::digest(&bytes)),
                    "sha512" => hex::encode(Sha512::digest(&bytes)),
                    _ => return Err(format!("NotSupportedError: unsupported digest algorithm '{algo}'")),
                };
                Ok(Some(hash))
            }
            "host_crypto_subtle" => {
                // NativeCrypto suite: HMAC, Ed25519, AES-GCM, PBKDF2, HKDF via ring.
                // args: [op, algo, key_hex, data_hex, extra_hex]
                let op = args.first().map(|s| s.as_str()).unwrap_or("");
                let algo = args.get(1).map(|s| s.to_lowercase().replace('-', "")).unwrap_or_default();
                let key_bytes = hex::decode(args.get(2).map(|s| s.as_str()).unwrap_or("")).unwrap_or_default();
                let data_bytes = hex::decode(args.get(3).map(|s| s.as_str()).unwrap_or("")).unwrap_or_default();
                let extra_str = args.get(4).map(|s| s.as_str()).unwrap_or("");
                match op {
                    "hmac_sign" => {
                        let hmac_alg = match algo.as_str() {
                            "sha256" | "hmacsha256" => ring::hmac::HMAC_SHA256,
                            "sha384" | "hmacsha384" => ring::hmac::HMAC_SHA384,
                            "sha512" | "hmacsha512" => ring::hmac::HMAC_SHA512,
                            _ => return Err(format!("NotSupportedError: unsupported HMAC hash '{algo}'")),
                        };
                        let key = ring::hmac::Key::new(hmac_alg, &key_bytes);
                        let tag = ring::hmac::sign(&key, &data_bytes);
                        Ok(Some(hex::encode(tag.as_ref())))
                    }
                    "hmac_verify" => {
                        let hmac_alg = match algo.as_str() {
                            "sha256" | "hmacsha256" => ring::hmac::HMAC_SHA256,
                            "sha384" | "hmacsha384" => ring::hmac::HMAC_SHA384,
                            "sha512" | "hmacsha512" => ring::hmac::HMAC_SHA512,
                            _ => return Err(format!("NotSupportedError: unsupported HMAC hash '{algo}'")),
                        };
                        let sig_bytes = hex::decode(extra_str).unwrap_or_default();
                        let key = ring::hmac::Key::new(hmac_alg, &key_bytes);
                        let ok = ring::hmac::verify(&key, &data_bytes, &sig_bytes).is_ok();
                        Ok(Some(if ok { "true".into() } else { "false".into() }))
                    }
                    "ed25519_sign" => {
                        if key_bytes.len() != 32 {
                            return Err("InvalidAccessError: Ed25519 seed must be 32 bytes".into());
                        }
                        let kp = ring::signature::Ed25519KeyPair::from_seed_unchecked(&key_bytes)
                            .map_err(|_| "OperationError: invalid Ed25519 seed".to_string())?;
                        let sig = kp.sign(&data_bytes);
                        Ok(Some(hex::encode(sig.as_ref())))
                    }
                    "ed25519_pubkey" => {
                        if key_bytes.len() != 32 {
                            return Err("InvalidAccessError: Ed25519 seed must be 32 bytes".into());
                        }
                        use ring::signature::KeyPair;
                        let kp = ring::signature::Ed25519KeyPair::from_seed_unchecked(&key_bytes)
                            .map_err(|_| "OperationError: invalid Ed25519 seed".to_string())?;
                        Ok(Some(hex::encode(kp.public_key().as_ref())))
                    }
                    "ed25519_verify" => {
                        let sig_bytes = hex::decode(extra_str).unwrap_or_default();
                        let pub_key = ring::signature::UnparsedPublicKey::new(&ring::signature::ED25519, &key_bytes);
                        let ok = pub_key.verify(&data_bytes, &sig_bytes).is_ok();
                        Ok(Some(if ok { "true".into() } else { "false".into() }))
                    }
                    "aes_gcm_encrypt" => {
                        let aead_alg = match key_bytes.len() {
                            16 => &ring::aead::AES_128_GCM,
                            32 => &ring::aead::AES_256_GCM,
                            _ => return Err("InvalidAccessError: AES-GCM key must be 128 or 256 bits".into()),
                        };
                        let iv_bytes = hex::decode(extra_str).unwrap_or_default();
                        if iv_bytes.len() != 12 {
                            return Err("OperationError: AES-GCM IV must be 12 bytes".into());
                        }
                        let mut iv_arr = [0u8; 12];
                        iv_arr.copy_from_slice(&iv_bytes);
                        let unbound = ring::aead::UnboundKey::new(aead_alg, &key_bytes)
                            .map_err(|_| "OperationError: invalid AES-GCM key".to_string())?;
                        let less_safe = ring::aead::LessSafeKey::new(unbound);
                        let nonce = ring::aead::Nonce::assume_unique_for_key(iv_arr);
                        let mut in_out = data_bytes;
                        less_safe
                            .seal_in_place_append_tag(nonce, ring::aead::Aad::empty(), &mut in_out)
                            .map_err(|_| "OperationError: AES-GCM seal failed".to_string())?;
                        Ok(Some(hex::encode(&in_out)))
                    }
                    "aes_gcm_decrypt" => {
                        let aead_alg = match key_bytes.len() {
                            16 => &ring::aead::AES_128_GCM,
                            32 => &ring::aead::AES_256_GCM,
                            _ => return Err("InvalidAccessError: AES-GCM key must be 128 or 256 bits".into()),
                        };
                        let iv_bytes = hex::decode(extra_str).unwrap_or_default();
                        if iv_bytes.len() != 12 {
                            return Err("OperationError: AES-GCM IV must be 12 bytes".into());
                        }
                        let mut iv_arr = [0u8; 12];
                        iv_arr.copy_from_slice(&iv_bytes);
                        let unbound = ring::aead::UnboundKey::new(aead_alg, &key_bytes)
                            .map_err(|_| "OperationError: invalid AES-GCM key".to_string())?;
                        let less_safe = ring::aead::LessSafeKey::new(unbound);
                        let nonce = ring::aead::Nonce::assume_unique_for_key(iv_arr);
                        let mut in_out = data_bytes;
                        let plain = less_safe
                            .open_in_place(nonce, ring::aead::Aad::empty(), &mut in_out)
                            .map_err(|_| "OperationError: AES-GCM authentication failed".to_string())?;
                        Ok(Some(hex::encode(plain)))
                    }
                    "pbkdf2" => {
                        // extra_str: "<iterations>:<length_bytes>"
                        let mut parts = extra_str.split(':');
                        let iters: u32 = parts.next().and_then(|s| s.parse().ok()).unwrap_or(1000).max(1);
                        let out_len: usize = parts.next().and_then(|s| s.parse().ok()).unwrap_or(32).clamp(1, 512);
                        let pbkdf2_alg = match algo.as_str() {
                            "sha256" => ring::pbkdf2::PBKDF2_HMAC_SHA256,
                            "sha384" => ring::pbkdf2::PBKDF2_HMAC_SHA384,
                            "sha512" => ring::pbkdf2::PBKDF2_HMAC_SHA512,
                            _ => ring::pbkdf2::PBKDF2_HMAC_SHA256,
                        };
                        let mut out = vec![0u8; out_len];
                        ring::pbkdf2::derive(
                            pbkdf2_alg,
                            std::num::NonZeroU32::new(iters).unwrap(),
                            &data_bytes, // salt
                            &key_bytes,  // password/key material
                            &mut out,
                        );
                        Ok(Some(hex::encode(&out)))
                    }
                    _ => Err(format!("NotSupportedError: unsupported subtle operation '{op}'")),
                }
            }
            "host_get_cookie" => {
                let map = self.0.cookies.lock().map_err(|_| "cookie lock poisoned")?;
                let cookie_str = map
                    .iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect::<Vec<_>>()
                    .join("; ");
                Ok(Some(cookie_str))
            }
            "host_set_cookie" => {
                let raw = args.first().cloned().unwrap_or_default();
                let pair = raw.split(';').next().unwrap_or("");
                if let Some((k, v)) = pair.split_once('=') {
                    if let Ok(mut map) = self.0.cookies.lock() {
                        map.insert(k.trim().to_string(), v.trim().to_string());
                    }
                }
                Ok(None)
            }
            "host_post_message" => {
                let data = args.first().cloned().unwrap_or_default();
                let origin = args.get(1).cloned().unwrap_or_else(|| "*".into());
                let target = args.get(2).cloned().unwrap_or_else(|| "parent".into());
                let sender_origin = args.get(3).cloned().unwrap_or_default();
                let ports_json = args.get(4).cloned().unwrap_or_default();

                let uid = self.0.post_uid.fetch_add(1, Ordering::SeqCst) + 1;
                let ports = if !ports_json.is_empty() {
                    serde_json::from_str::<Vec<Value>>(&ports_json).ok()
                } else {
                    None
                };

                let entry = PostMessageEntry {
                    uid,
                    data,
                    origin: sender_origin,
                    target_origin: origin,
                    target,
                    ctx: self.0.ctx_key.clone(),
                    ports,
                };

                if let Ok(mut posts) = self.0.post_messages.lock() {
                    posts.push(entry);
                }
                Ok(None)
            }
            "host_fetch" => {
                let req_url = args.first().cloned().unwrap_or_default();
                let method = args.get(1).cloned().unwrap_or_else(|| "GET".into());
                let body = args.get(2).cloned();
                let headers_json = args.get(3).cloned().unwrap_or_default();

                let page_url = self.0.url.lock().map(|u| u.clone()).unwrap_or_default();
                let abs_url = if req_url.starts_with("http://") || req_url.starts_with("https://") {
                    req_url
                } else if let Ok(base) = url::Url::parse(&page_url) {
                    base.join(&req_url).map(|u| u.to_string()).unwrap_or(req_url)
                } else {
                    req_url
                };

                // N9: the page-level fetch wrapper captures the JS call site
                // (new Error().stack) at dispatch time; sanitize here (query
                // strings stripped, bounded).
                let initiator_stack = args
                    .get(5)
                    .map(|s| crate::telemetry::sanitize_initiator_stack(s))
                    .unwrap_or_default();

                let network_opt = self.0.network.clone();
                let client = self.0.client.clone();
                let page_origin = crate::shell::get_origin(&page_url);
                let active_cause = self
                    .0
                    .active_cause
                    .lock()
                    .ok()
                    .and_then(|g| g.clone())
                    .or_else(|| Some("v8_script".into()));

                let do_fetch = async move {
                    if let Some(net) = network_opt {
                        let mut req = crate::network::NetworkRequest::new(&abs_url);
                        req.method = method;
                        req.body = body;
                        req.client_origin = Some(page_origin);
                        req.caused_by = active_cause;
                        req.is_subresource = true;
                        req.initiator_stack = initiator_stack;
                        if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(&headers_json) {
                            req.headers = map;
                        }
                        match net.fetch(req).await {
                            Ok(res) => Ok(res),
                            Err(e) => Err(format!("Network error: {e}")),
                        }
                    } else {
                        fetch::fetch(&client, &abs_url, &method, body, &headers_json, 8_000_000)
                            .await
                            .map_err(|e| e.to_string())
                    }
                };

                // Handle both sync & async environments cleanly
                let result = if let Ok(handle) = tokio::runtime::Handle::try_current() {
                    tokio::task::block_in_place(|| handle.block_on(do_fetch))
                } else if let Ok(rt) = tokio::runtime::Builder::new_current_thread().enable_all().build() {
                    rt.block_on(do_fetch)
                } else {
                    return Err("no tokio runtime available for host_fetch".into());
                };

                match result {
                    Ok(res) => Ok(Some(res.to_json())),
                    Err(e) => Ok(Some(json!({"status": 599, "body": format!("Fetch error: {e}")}).to_string())),
                }
            }
            "host_load_module" => {
                // Audit P2-01: resolve specifier against base URL + import map,
                // fetch module source via NetworkEngine, and translate ESM syntax.
                let specifier = args.first().cloned().unwrap_or_default();
                let page_url = self.0.url.lock().map(|u| u.clone()).unwrap_or_default();
                let base_url = args
                    .get(1)
                    .filter(|s| !s.is_empty())
                    .cloned()
                    .unwrap_or_else(|| page_url.clone());
                let import_map: HashMap<String, String> = args
                    .get(2)
                    .and_then(|s| serde_json::from_str(s).ok())
                    .unwrap_or_default();

                let resolved = crate::modules::resolve_specifier(&specifier, &base_url, &import_map)
                    .or_else(|| {
                        url::Url::parse(&base_url)
                            .ok()
                            .and_then(|b| b.join(&specifier).ok())
                            .map(|u| u.to_string())
                    })
                    .unwrap_or(specifier.clone());

                if !resolved.starts_with("http://") && !resolved.starts_with("https://") {
                    return Ok(Some(json!({"url": resolved, "code": "", "translated": ""}).to_string()));
                }

                let network_opt = self.0.network.clone();
                let client = self.0.client.clone();
                let page_origin = crate::shell::get_origin(&page_url);
                let active_cause = self
                    .0
                    .active_cause
                    .lock()
                    .ok()
                    .and_then(|g| g.clone())
                    .or_else(|| Some("esm_loader".into()));
                let fetch_url = resolved.clone();

                let do_fetch = async move {
                    if let Some(net) = network_opt {
                        let mut req = crate::network::NetworkRequest::new(&fetch_url);
                        req.method = "GET".to_string();
                        req.client_origin = Some(page_origin);
                        req.caused_by = active_cause;
                        req.is_subresource = true;
                        net.fetch(req).await.map_err(|e| e.to_string())
                    } else {
                        fetch::fetch(&client, &fetch_url, "GET", None, "", 4_000_000)
                            .await
                            .map_err(|e| e.to_string())
                    }
                };

                let res = run_async_host_call(do_fetch);
                match res {
                    Ok(r) if r.status >= 200 && r.status < 300 => {
                        let translated = crate::modules::translate_module(&r.body, &r.final_url, &import_map);
                        Ok(Some(
                            json!({
                                "url": r.final_url,
                                "code": r.body,
                                "translated": translated
                            })
                            .to_string(),
                        ))
                    }
                    _ => Ok(Some(json!({"url": resolved, "code": "", "translated": ""}).to_string())),
                }
            }
            "host_ws_connect" => {
                // args: [url, protocols_json]. RFC 6455 handshake, recorded
                // in the ledger, with a hard timeout so a hanging server
                // cannot wedge the page thread. Result shape matches minidom:
                // {"ok":true,"id":N,"protocol":...} or {"ok":false,...}.
                let ws_url = args.first().cloned().unwrap_or_default();
                let protocols = args.get(1).cloned().unwrap_or_default();
                if ws_url.is_empty() {
                    return Ok(Some(json!({"ok": false, "code": 1002, "error": "ws url required"}).to_string()));
                }
                let Some(ws_ledger) = self.0.ws_ledger.clone() else {
                    return Ok(Some(json!({"ok": false, "code": 1002, "error": "no network capability"}).to_string()));
                };
                let extra_headers: HashMap<String, String> = HashMap::new();
                let fut = {
                    let ws_ledger = ws_ledger.clone();
                    async move { crate::ws::handshake_open(&ws_url, &ws_ledger, &extra_headers).await }
                };
                let outcome = run_async_host_call_inner(fut, &protocols);
                match outcome {
                    Ok((session_id, stream, protocol)) => {
                        if let Ok(mut slot) = self.0.ws_registry.lock() {
                            let reg = slot.get_or_insert_with(|| crate::ws::WsRegistry::new(ws_ledger.clone()));
                            reg.admit(session_id, stream);
                        }
                        Ok(Some(json!({"ok": true, "id": session_id, "protocol": protocol}).to_string()))
                    }
                    Err(e) => {
                        // Handshake failed before ws_open: nothing to close in
                        // the ledger; report the honest code.
                        let _ = &e;
                        Ok(Some(json!({"ok": false, "code": 1006, "error": e}).to_string()))
                    }
                }
            }
            "host_ws_send" => {
                let session = args.first().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                let text = args.get(1).cloned().unwrap_or_default();
                let send_fut = async {
                    let mut slot = self
                        .0
                        .ws_registry
                        .lock()
                        .map_err(|_| "ws registry poisoned".to_string())?;
                    slot.as_mut()
                        .ok_or("websocket transport not initialized")?
                        .send_text(session, &text)
                        .await
                };
                run_async_host_call(send_fut).map(|_| json!({"ok": true}).to_string()).map(Some)
            }
            "host_ws_send_binary" => {
                let session = args.first().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                let b64 = args.get(1).cloned().unwrap_or_default();
                use base64::Engine as _;
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(&b64)
                    .map_err(|e| format!("bad base64 payload: {e}"))?;
                let send_fut = async {
                    let mut slot = self
                        .0
                        .ws_registry
                        .lock()
                        .map_err(|_| "ws registry poisoned".to_string())?;
                    slot.as_mut()
                        .ok_or("websocket transport not initialized")?
                        .send_binary(session, &bytes)
                        .await
                };
                run_async_host_call(send_fut).map(|_| json!({"ok": true}).to_string()).map(Some)
            }
            "host_ws_close" => {
                let session = args.first().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                let code = args.get(1).and_then(|s| s.parse::<u16>().ok()).unwrap_or(1000);
                if let Ok(mut slot) = self.0.ws_registry.lock() {
                    if let Some(reg) = slot.as_mut() {
                        let _ = reg.close(session, code);
                    }
                }
                Ok(Some(json!({"ok": true}).to_string()))
            }
            "host_checkpoint" | "__dosh_checkpoint" => {
                Ok(Some("0".to_string()))
            }
            "host_rewind" | "__dosh_rewind" => {
                Ok(Some("0".to_string()))
            }
            "host_batch_read" | "__dosh_batch_read" => {
                Ok(Some("".to_string()))
            }
            "host_fork" | "__dosh_fork" => {
                Ok(Some("{}".to_string()))
            }
            _ => Err(format!("unknown host callable: {name}")),
        }
    }
}

/// Execute an async fence from the sync host-seam context, using the current
/// tokio runtime when one is present (block_in_place) or a temporary one-off
/// runtime otherwise.
fn run_async_host_call_inner<F, T>(fut: F, ctx: &str) -> T
where
    F: std::future::Future<Output = T>,
{
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        if handle.runtime_flavor() == tokio::runtime::RuntimeFlavor::MultiThread {
            return tokio::task::block_in_place(|| handle.block_on(fut));
        }
    }
    let _ = ctx;
    let rt = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio current_thread runtime");
    rt.block_on(fut)
}

/// The usual form: plain async work, no extra context string.
fn run_async_host_call<F, T>(fut: F) -> T
where
    F: std::future::Future<Output = T>,
{
    run_async_host_call_inner(fut, "")
}

/// The Live Page JavaScript Runtime, backed by a resident V8 actor.
#[derive(Clone)]
pub struct Runtime {
    pub engine: DoshActorHandle,
    pub seam: Arc<SharedSeam>,
    pub url: String,
    pub ctx_key: String,
    layout_cache: Arc<Mutex<Vec<doshgentv8_core::protocol::DoshButterflyRect>>>,
}

impl Runtime {
    /// Create a fresh runtime context, register host callables, and boot minidom.js.
    pub fn new(client: Client, url: &str) -> Result<Self, String> {
        Self::new_with_network(client, url, None)
    }

    /// Create a fresh runtime context with full central NetworkEngine integration.
    pub fn new_with_network(
        client: Client,
        url: &str,
        network: Option<Arc<crate::network::NetworkEngine>>,
    ) -> Result<Self, String> {
        let ctx_key = format!(
            "ctx-{:x}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0)
        );

        let seam = Arc::new(SharedSeam(InnerSeam {
            client,
            network: network.clone(),
            url: Mutex::new(url.to_string()),
            cookies: Mutex::new(HashMap::new()),
            console: Mutex::new(Vec::new()),
            post_messages: Mutex::new(Vec::new()),
            post_uid: AtomicUsize::new(0),
            ctx_key: ctx_key.clone(),
            ws_ledger: network.as_ref().map(|n| n.ledger.clone()),
            ws_registry: Mutex::new(None),
            active_cause: Mutex::new(None),
        }));

        let engine = DoshActor::spawn(seam.clone(), 128 * 1024 * 1024)?;

        // Per-navigation wall budget (the cnn.com fix): arm the engine's
        // watchdog so an ad-stack page cannot occupy the runtime
        // indefinitely. The budget applies to EACH eval on this actor —
        // hydration is a sequence of script evals, and a script that
        // exceeds the budget dies honestly ("interrupted") while the rest
        // of the page still renders (graceful degradation was proven by
        // the timeout sweep: titles render at every budget).
        // Env: WR_NAV_TIMEOUT_MS (milliseconds per eval; default 10_000;
        // 0 disables the budget).
        let nav_budget_ms: f64 = std::env::var("WR_NAV_TIMEOUT_MS")
            .ok()
            .and_then(|v| v.parse::<f64>().ok())
            .unwrap_or(10_000.0);
        if nav_budget_ms > 0.0 {
            engine.set_time_limit(nav_budget_ms / 1000.0)?;
        }

        // 1. Register host callables
        engine.add_host("host_parse_html")?;
        engine.add_host("host_log")?;
        engine.add_host("host_crypto_digest")?;
        engine.add_host("host_crypto_subtle")?;
        engine.add_host("host_get_cookie")?;
        engine.add_host("host_set_cookie")?;
        engine.add_host("host_post_message")?;
        engine.add_host("host_fetch")?;
        engine.add_host("host_load_module")?;
        engine.add_host("host_ws_connect")?;
        engine.add_host("host_ws_send")?;
        engine.add_host("host_ws_send_binary")?;
        engine.add_host("host_ws_close")?;

        // 2. Pre-boot variables
        engine.eval("globalThis.__WR_STORAGE_SEED = {}; true")?;
        engine.eval("globalThis.__WR_HISTORY_LENGTH = 1; true")?;
        engine.eval("globalThis.__WR_HOST_LOG_TAKES_LEVEL = true; true")?;

        let ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
        engine.eval(&format!("globalThis.__WR_UA = {}; true", json!(ua)))?;
        engine.eval(&format!("globalThis.__WR_CTX_KEY = {}; true", json!(ctx_key)))?;

        let dev_prof = crate::device::detect_device_profile();
        let device = json!(dev_prof);
        engine.eval(&format!("globalThis.__WR_DEVICE = {}; true", device))?;

        // 3. Boot minidom.js
        engine.eval(MINIDOM_JS)?;

        Ok(Self {
            engine,
            seam,
            url: url.to_string(),
            ctx_key,
            layout_cache: Arc::new(Mutex::new(Vec::new())),
        })
    }

    /// Load an HTML document into the runtime's minidom.js state.
    pub fn load_document(&mut self, html: &str, url: &str) -> Result<(), String> {
        self.url = url.to_string();
        if let Ok(mut u) = self.seam.0.url.lock() {
            *u = url.to_string();
        }
        let tree = tree::html_to_tree(html);
        let payload = serde_json::to_string(&tree).map_err(|e| e.to_string())?;
        let escaped = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
        self.engine.eval(&format!("__load(JSON.parse({escaped}), {}); true", json!(url)))?;
        // Clear initial hydration elements from the injected script queue
        self.engine.eval("globalThis.__WR_INJECTED_SCRIPTS = []; true")?;
        // Full initial-document projection into the Parena (Stage-3): the
        // arena mirrors the parsed tree so spill/checkpoint/fork/external
        // reporting apply from load time. Replaces the former single-node
        // stub. Initial snapshot only — live mutations are not mirrored
        // (documented future work).
        let (parena, node_count) = doshgentv8_core::parena_from_tree_json(&payload)?;
        self.engine.set_parena(parena)?;
        let _ = node_count;
        Ok(())
    }

    /// Drain pending timers and microtasks.
    pub fn drain_timers(&mut self) -> Result<(), String> {
        // N2 parity with the Python lane's `_pump_websockets`: deliver queued
        // inbound text frames to the page as `globalThis.__wsDeliver`.
        if let Ok(mut slot) = self.seam.0.ws_registry.lock() {
            if let Some(reg) = slot.as_mut() {
                let mut frames = 0;
                for f in reg.take_inbound() {
                    if f.binary {
                        // Text delivery matches the Python lane, which records
                        // binary frames but does not deliver them to script.
                        continue;
                    }
                    if frames >= 128 {
                        break;
                    }
                    frames += 1;
                    let data = String::from_utf8_lossy(&f.data);
                    let js = format!(
                        "if (typeof globalThis.__wsDeliver === 'function') globalThis.__wsDeliver({}, {});",
                        f.session,
                        json!(data.as_ref())
                    );
                    let _ = self.engine.eval(&js);
                    self.engine.pump_jobs();
                }
            }
        }
        for _ in 0..MAX_TIMER_PASSES {
            let left = self.engine.eval("__drainTimers()")?;
            self.engine.pump_jobs();
            self.drain_injected_scripts()?;
            if left == "0" || left == "false" || left == "null" {
                break;
            }
        }
        for _ in 0..4 {
            let _ = self.engine.eval("__advanceTimerClock(100)");
            self.engine.pump_jobs();
            self.drain_injected_scripts()?;
        }
        let _ = self.engine.eval("__notifyIntersections(); true");
        self.engine.pump_jobs();
        Ok(())
    }

    /// Fetch an external script or module source through the central NetworkEngine
    /// rule table (mock/block) or opt-in external script transport.
    fn fetch_script_source(&self, script_src: &str, base_url: &str, caused_by: &str) -> Option<(String, String)> {
        if !self.engine.is_network_allowed().unwrap_or(true) {
            return None;
        }
        let abs_url = if script_src.starts_with("http://") || script_src.starts_with("https://") {
            script_src.to_string()
        } else if let Ok(base) = url::Url::parse(base_url) {
            base.join(script_src).ok()?.to_string()
        } else {
            return None;
        };
        let cause = self
            .seam
            .0
            .active_cause
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| caused_by.to_string());

        // 1. Check central NetworkLedger rules (mock / block / redirect) synchronously
        if let Some(ref net) = self.seam.0.network {
            if let Ok(mut ledger) = net.ledger.try_lock() {
                if let Some(rule) = ledger.match_rule(&abs_url).cloned() {
                    match rule.action.as_str() {
                        "block" => {
                            ledger.append(crate::telemetry::NetworkRecord {
                                source: "rule_block".into(),
                                url: abs_url,
                                method: "GET".into(),
                                state: "blocked_by_rule".into(),
                                network_sent: Some(false),
                                caused_by: Some(cause),
                                ..Default::default()
                            });
                            return None;
                        }
                        "mock" => {
                            let mut resp_headers = HashMap::new();
                            resp_headers.insert("content-type".to_string(), rule.content_type.clone());
                            let capture = ledger.capture_bodies;
                            ledger.append(crate::telemetry::NetworkRecord {
                                source: "rule_mock".into(),
                                url: abs_url.clone(),
                                method: "GET".into(),
                                status: Some(rule.status),
                                content_type: rule.content_type,
                                bytes: rule.body.len(),
                                response_headers: resp_headers,
                                response_body: if capture { Some(rule.body.clone()) } else { None },
                                state: "complete".into(),
                                network_sent: Some(false),
                                caused_by: Some(cause),
                                ..Default::default()
                            });
                            if rule.status >= 200 && rule.status < 300 && !rule.body.trim().is_empty() {
                                return Some((abs_url, rule.body));
                            }
                            return None;
                        }
                        _ => {}
                    }
                }
            }
        }

        // 2. Live external subresource script fetch (opt-in via WR_FETCH_EXTERNAL_SCRIPTS=1
        // so synchronous page loads on single-threaded runtimes never block on CDN bundles)
        if !std::env::var("WR_FETCH_EXTERNAL_SCRIPTS")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false)
        {
            return None;
        }
        let target_url = abs_url.clone();
        let res = std::thread::scope(|s| {
            s.spawn(|| {
                let rt = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .ok()?;
                let fresh_client = Client::builder()
                    .timeout(std::time::Duration::from_secs(3))
                    .build()
                    .ok()?;
                rt.block_on(fetch::fetch(&fresh_client, &target_url, "GET", None, "", 4_000_000))
                    .ok()
            })
            .join()
            .ok()
            .flatten()
        });
        match res {
            Some(r) if r.status >= 200 && r.status < 300 && !r.body.trim().is_empty() => {
                Some((r.final_url, r.body))
            }
            _ => None,
        }
    }

    /// Drain injected script queue (created via createElement + appendChild).
    fn drain_injected_scripts(&mut self) -> Result<(), String> {
        let raw = self.engine.eval("JSON.stringify(globalThis.__WR_INJECTED_SCRIPTS || [])")?;
        if let Ok(scripts) = serde_json::from_str::<Vec<Value>>(&raw) {
            let base_url = self.url.clone();
            let empty_map = HashMap::new();
            for s in scripts.iter().take(40) {
                let st = s["type"].as_str().unwrap_or_default().to_lowercase();
                let is_mod = st == "module";
                let text = s["text"].as_str().unwrap_or_default().trim();
                let src = s["src"].as_str().unwrap_or_default().trim();
                if !text.is_empty() {
                    let code = if is_mod {
                        crate::modules::translate_module(text, &base_url, &empty_map)
                    } else {
                        text.to_string()
                    };
                    let _ = self.engine.eval(&code);
                } else if !src.is_empty() {
                    if let Some((final_url, fetched_code)) = self.fetch_script_source(src, &base_url, "injected_script") {
                        let code = if is_mod {
                            crate::modules::translate_module(&fetched_code, &final_url, &empty_map)
                        } else {
                            fetched_code
                        };
                        let _ = self.engine.eval(&code);
                    }
                }
            }
        }
        let _ = self.engine.eval("globalThis.__WR_INJECTED_SCRIPTS = []; true");
        Ok(())
    }

    /// Execute page scripts in document order, fire DOMContentLoaded and load,
    /// drain timers, and return the mutated DOM HTML along with execution telemetry.
    pub fn run_scripts(&mut self, html: &str, url: &str) -> Result<(String, RunScriptsInfo), String> {
        self.load_document(html, url)?;

        let mut info = RunScriptsInfo {
            ran: true,
            ..Default::default()
        };

        let import_map = crate::modules::extract_import_map(html);
        let scripts = tree::extract_scripts(html);
        let mut classic = Vec::new();
        let mut deferred = Vec::new();

        for s in scripts {
            let st = s.r#type.to_lowercase();
            if st.contains("ld+json") || st.contains("json") || st.contains("importmap") {
                continue;
            }
            if s.defer || st == "module" {
                deferred.push(s);
            } else {
                classic.push(s);
            }
        }

        // 1. Run classic & deferred/module scripts in order
        let mut ran_count = 0;
        let mut mod_count = 0;
        for s in classic.into_iter().chain(deferred.into_iter()) {
            if ran_count >= MAX_SCRIPTS {
                break;
            }
            let is_module = s.r#type.eq_ignore_ascii_case("module");
            let (script_url, raw_code) = if !s.code.trim().is_empty() {
                (url.to_string(), s.code)
            } else if !s.src.trim().is_empty() {
                match self.fetch_script_source(&s.src, url, "script_tag") {
                    Some(pair) => pair,
                    None => continue,
                }
            } else {
                continue;
            };

            let exec_code = if is_module {
                crate::modules::translate_module(&raw_code, &script_url, &import_map)
            } else {
                raw_code
            };

            match self.engine.eval(&exec_code) {
                Ok(_) => {
                    ran_count += 1;
                    if is_module {
                        mod_count += 1;
                    }
                    self.engine.pump_jobs();
                    let _ = self.drain_injected_scripts();
                }
                Err(e) => {
                    info.errors += 1;
                    if info.error_messages.len() < 25 {
                        info.error_messages.push(e);
                    }
                }
            }
        }
        info.scripts = ran_count;
        info.modules = mod_count;

        // 2. Dispatch DOM events
        let _ = self.engine.eval(
            r#"
            if (typeof document !== 'undefined' && document && document.dispatchEvent) {
                document.dispatchEvent(new Event('DOMContentLoaded', { bubbles: true, cancelable: true }));
            }
            if (typeof window !== 'undefined' && window && window.dispatchEvent) {
                window.dispatchEvent(new Event('load'));
            }
            "#
        );
        self.engine.pump_jobs();

        // 3. Drain timers and notify intersections
        let _ = self.drain_timers();

        // 4. Serialize mutated DOM
        let serialized = self.engine.eval("__serialize()").unwrap_or_else(|_| html.to_string());

        // 5. Harvest telemetry
        if let Ok(cons) = self.seam.0.console.lock() {
            info.console = cons.clone();
        }
        if let Ok(posts) = self.seam.0.post_messages.lock() {
            info.post_messages = posts.clone();
        }

        if let Ok(nav) = self.engine.eval("globalThis.__WR_NAVIGATED || ''") {
            if !nav.is_empty() {
                info.navigated = Some(nav);
            }
        }

        if let Ok(fs) = self.engine.eval("JSON.stringify(globalThis.__WR_FORM_SUBMIT || null)") {
            if fs != "null" && !fs.is_empty() {
                info.form_submit = serde_json::from_str(&fs).ok();
            }
        }

        if let Ok(dia) = self.engine.eval("JSON.stringify(globalThis.__WR_DIALOGS || [])") {
            info.dialogs = serde_json::from_str(&dia).unwrap_or_default();
        }

        if let Ok(pops) = self.engine.eval("JSON.stringify(globalThis.__WR_POPUPS || [])") {
            info.popups = serde_json::from_str(&pops).unwrap_or_default();
        }

        if let Ok(pushes) = self.engine.eval("JSON.stringify(globalThis.__WR_PUSHED || null)") {
            if pushes != "null" && !pushes.is_empty() {
                info.history_pushes = serde_json::from_str(&pushes).unwrap_or_default();
            }
        }

        if let Ok(rep) = self.engine.eval("JSON.stringify(globalThis.__WR_REPLACED || null)") {
            if rep != "null" && !rep.is_empty() {
                info.history_replace = serde_json::from_str(&rep).ok();
            }
        }

        if let Ok(m) = self.engine.eval("Number(globalThis.__WR_HISTORY_MOVE || 0)") {
            if let Ok(val) = m.parse::<i64>() {
                info.history_move = val;
                let _ = self.engine.eval("globalThis.__WR_HISTORY_MOVE = 0; true");
            }
        }

        Ok((serialized, info))
    }

    /// Apply an agent action against the live DOM (click, type, drag, submit)
    /// and return the serialized mutated DOM.
    pub fn apply_action(&mut self, action: &Value) -> Result<String, String> {
        let kind = action["kind"].as_str().or_else(|| action["type"].as_str()).unwrap_or("click");
        let path_json = serde_json::to_string(&action["path"]).unwrap_or_else(|_| "[]".into());
        let selector = action["selector"].as_str().or_else(|| action["ref"].as_str()).unwrap_or_default();
        let stealth = action["stealth"].as_bool().unwrap_or(false);
        let nth = action["occurrence"].as_i64().unwrap_or(1);
        // L10: occlusion-gated clicks. Agent clicks are gated by default —
        // a control covered by an overlay refuses with
        // `__lastActionError = occluded_by:<selector>` (surfaced by the
        // session lane as an error). Callers may opt out explicitly via
        // `occlusion_gate: false` (documented bypass, never silent).
        // Typing stays ungated: keyboard input reaches a focused field even
        // when an overlay covers its box.
        let gate = action["occlusion_gate"].as_bool().unwrap_or(true);

        match kind {
            "click" | "activate" => {
                if !selector.is_empty() {
                    let _ = self.engine.eval(&format!(
                        "__activateSelector({}, null, false, {}, {}, {})",
                        json!(selector), stealth, nth, gate
                    ));
                } else {
                    let _ = self.engine
                        .eval(&format!("__clickPath({path_json}, {stealth}, {gate})"));
                }
            }
            "type" => {
                let text = action["text"].as_str().unwrap_or_default();
                if !selector.is_empty() {
                    let _ = self.engine.eval(&format!(
                        "__activateSelector({}, {}, false, {}, {}, false)",
                        json!(selector), json!(text), stealth, nth
                    ));
                } else {
                    let _ = self.engine.eval(&format!(
                        "__typePath({path_json}, {}, false, {stealth})",
                        json!(text)
                    ));
                }
            }
            "submit" => {
                let _ = self.engine.eval(&format!("__submitPath({path_json})"));
            }
            "text_select" => {
                // E2: the text-selection drag lane (minidom.js __selectText):
                // mousedown → selection extends → selectstart/select/
                // selectionchange fire → mouseup. Distinct lane from the
                // element DnD path; no click fires.
                let target = if !selector.is_empty() {
                    json!(selector).to_string()
                } else {
                    path_json
                };
                let start = action["start"].as_i64().unwrap_or(0);
                let end = action["end"].as_i64();
                let code = match end {
                    Some(e) => format!("__selectText({target}, {start}, {e})"),
                    None => format!("__selectText({target}, {start})"),
                };
                let _ = self.engine.eval(&code);
            }
            _ => {}
        }

        self.engine.pump_jobs();
        let _ = self.drain_timers();
        self.engine.eval("__serialize()")
    }

    /// Evaluate arbitrary JavaScript expression in the live context.
    pub fn eval(&mut self, code: &str) -> Result<String, String> {
        self.engine.eval(code)
    }

    /// Serialize current live DOM tree to HTML string.
    pub fn serialize(&mut self) -> Result<String, String> {
        self.engine.eval("__serialize()")
    }

    /// Sync computed DOM layout into contiguous ButterflyRect memory.
    pub fn sync_butterfly_layout(&mut self) -> Result<usize, String> {
        let layers_json = self.engine.eval("__inspectLayers()")?;
        if let Ok(val) = serde_json::from_str::<Value>(&layers_json) {
            let mut rects = Vec::new();
            if let Some(strata) = val.get("strata").and_then(|v| v.as_object()) {
                let stratum_names = ["L0_FLOW", "L1_CHROME", "L2_SCROLLER", "L3_POPOVER", "L4_OVERLAY"];
                let mut seq = 0u32;
                for (s_idx, s_name) in stratum_names.iter().enumerate() {
                    if let Some(nodes) = strata.get(*s_name).and_then(|v| v.as_array()) {
                        for node in nodes {
                            seq += 1;
                            let r = node.get("rect").unwrap_or(&Value::Null);
                            let x = r.get("x").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
                            let y = r.get("y").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
                            let width = r.get("width").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
                            let height = r.get("height").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32;
                            let z = node.get("zIndex").and_then(|v| v.as_i64()).unwrap_or(0) as u16;
                            let occluded = node.get("occluded").and_then(|v| v.as_bool()).unwrap_or(false);
                            rects.push(doshgentv8_core::protocol::DoshButterflyRect {
                                element_id: seq,
                                x,
                                y,
                                width,
                                height,
                                z_index: z,
                                occluded_stratum: s_idx as u8,
                                flags: if occluded { 1 } else { 0 },
                            });
                        }
                    }
                }
            }
            let count = rects.len();
            if let Ok(mut cache) = self.layout_cache.lock() {
                *cache = rects.clone();
            }
            let _ = self.engine.set_layout_rects(rects);
            Ok(count)
        } else {
            Ok(0)
        }
    }

    /// Access zero-copy contiguous Butterfly layout rectangle memory slice.
    pub fn get_butterfly_layout(&self) -> Vec<doshgentv8_core::protocol::DoshButterflyRect> {
        self.layout_cache.lock().map(|c| c.clone()).unwrap_or_default()
    }

    /// Evaluate code in a bounded control realm with explicit action authority.
    pub fn eval_bounded(
        &self,
        code: &str,
        control_realm: bool,
        allow_actions: bool,
        args: &[String],
    ) -> Result<String, String> {
        self.engine.eval_bounded(code, control_realm, allow_actions, args)
    }

    /// Fork the live page arena, creating an independent clone of page state.
    pub fn fork_parena(&self) -> Result<doshgentv8_core::parena::Parena, String> {
        self.engine.fork_parena()
    }

    /// Capture a dirty-diff checkpoint of current arena state.
    pub fn checkpoint_parena(&self) -> Result<u64, String> {
        self.engine.checkpoint_parena()
    }

    /// Roll back the arena state to a previously captured checkpoint.
    pub fn rewind_parena(&self, cp_id: u64) -> Result<usize, String> {
        self.engine.rewind_parena(cp_id)
    }

    /// Read multiple nodes from page arena in a zero-copy batch.
    pub fn batch_read_parena(&self, node_ids: Vec<u32>) -> Result<Vec<u8>, String> {
        self.engine.batch_read_parena(node_ids)
    }

    /// Install or replace the page arena.
    pub fn set_parena(&self, parena: doshgentv8_core::parena::Parena) -> Result<(), String> {
        self.engine.set_parena(parena)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_nav_wall_budget_bounds_runaway_script() {
        // The cnn.com fix: a per-eval wall budget must terminate a runaway
        // script honestly. We can't set the env per-test (it is read at
        // Runtime construction and tests run in one process), so we arm the
        // budget directly on the actor — the same call the constructor makes
        // from WR_NAV_TIMEOUT_MS.
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        rt.load_document("<html><head><title>Budget</title></head><body></body></html>", "https://example.com").unwrap();
        rt.engine.set_time_limit(0.5).unwrap();
        let t0 = std::time::Instant::now();
        let res = rt.eval("while(true) {}");
        let dt = t0.elapsed().as_secs_f64();
        assert!(res.is_err(), "runaway script must die under the budget");
        assert!(dt < 2.0, "must die fast, took {dt}s");
        let err = res.unwrap_err();
        assert_eq!(err, "interrupted", "honest error, got: {err}");
        // The runtime must remain usable after the budget kill.
        let ok = rt.eval("2 + 3");
        assert_eq!(ok.unwrap(), "5");
    }

    #[test]
    fn test_runtime_boot() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        rt.load_document("<html><head><title>Test</title></head><body></body></html>", "https://example.com").unwrap();
        let res = rt.eval("typeof document").unwrap();
        assert_eq!(res, "object");
        let res2 = rt.eval("document.querySelector('title').textContent").unwrap();
        assert_eq!(res2, "Test");
    }

    #[test]
    fn test_runtime_load_and_run_script() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        let html = r#"
        <!DOCTYPE html>
        <html>
        <head><title>Initial</title></head>
        <body>
            <h1 id="header">Hello</h1>
            <script>
                document.title = "Mutated Title";
                var h = document.getElementById("header");
                h.textContent = "Mutated Content";
                console.log("Script executed!");
            </script>
        </body>
        </html>
        "#;
        let (mutated, info) = rt.run_scripts(html, "https://example.com").unwrap();
        assert_eq!(info.scripts, 1);
        assert_eq!(info.console.len(), 1);
        assert_eq!(info.console[0].message, "Script executed!");
        assert!(mutated.contains("Mutated Title") || rt.eval("document.title").unwrap() == "Mutated Title");
        assert!(mutated.contains("Mutated Content"));
    }

    #[test]
    fn test_runtime_action_click() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        let html = r#"
        <!DOCTYPE html>
        <html>
        <body>
            <button id="btn" onclick="document.body.setAttribute('data-clicked', 'true')">Click Me</button>
        </body>
        </html>
        "#;
        let _ = rt.run_scripts(html, "https://example.com").unwrap();
        let action = json!({
            "kind": "click",
            "selector": "#btn"
        });
        let mutated = rt.apply_action(&action).unwrap();
        assert!(mutated.contains("data-clicked") || rt.eval("document.body.getAttribute('data-clicked')").unwrap() == "true");
    }

    #[test]
    fn test_runtime_webgl_readpixels_emulation() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        rt.load_document("<html><body><canvas id='c'></canvas></body></html>", "https://example.com").unwrap();
        let js = r#"
            (function() {
                var c = document.getElementById('c');
                var gl = c.getContext('webgl');
                if (!gl) return JSON.stringify({ ok: false, error: 'no webgl' });
                var prog = gl.createProgram();
                var vs = gl.createShader(gl.VERTEX_SHADER);
                gl.shaderSource(vs, 'attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }');
                gl.compileShader(vs);
                gl.attachShader(prog, vs);
                gl.linkProgram(prog);
                gl.useProgram(prog);
                // R2 software path: upload a real fullscreen triangle and a
                // u_color uniform so drawArrays actually rasterizes pixels
                // (the software framebuffer returns true drawn bytes; the
                // old pseudo-noise readPixels is gone).
                var buf = gl.createBuffer();
                gl.bindBuffer(gl.ARRAY_BUFFER, buf);
                gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
                    -1, -1,   1, -1,   0, 1
                ]), gl.STATIC_DRAW);
                gl.uniform4f(gl.getUniformLocation(prog, 'u_color'), 0.4, 0.6, 0.2, 1.0);
                gl.drawArrays(gl.TRIANGLES, 0, 3);
                var px = new Uint8Array(4);
                gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
                return JSON.stringify({
                    ok: true,
                    r: px[0],
                    g: px[1],
                    b: px[2],
                    a: px[3],
                    cloaked: gl.readPixels.toString().indexOf('[native code]') !== -1
                });
            })()
        "#;
        let out = rt.eval(js).unwrap();
        let val: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(val["ok"], true);
        let non_zero = val["r"].as_u64().unwrap() > 0 || val["g"].as_u64().unwrap() > 0 || val["b"].as_u64().unwrap() > 0;
        assert!(non_zero, "readPixels must return non-zero pseudo-rasterized pixel values");
        assert_eq!(val["a"], 255, "readPixels alpha must be fully opaque");
        assert_eq!(val["cloaked"], true, "readPixels must be cloaked as native code");
    }

    #[test]
    fn test_runtime_load_tricky_html_entities() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        let html = "<html><body><script>var x = 'hello \\\"world\\\" \u{2028} and \u{2029}';</script></body></html>";
        assert!(rt.load_document(html, "https://example.com").is_ok());
    }

    #[test]
    fn test_runtime_webaudio_and_stealth_emulation() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        rt.load_document("<html><body></body></html>", "https://example.com").unwrap();
        let js = r#"
            (function() {
                globalThis.__WEBAUDIO_TEST = {};
                // 1. OfflineAudioContext
                var actx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 44100, 44100);
                var osc = actx.createOscillator();
                var comp = actx.createDynamicsCompressor();
                osc.connect(comp);
                comp.connect(actx.destination);
                osc.start(0);
                actx.startRendering().then(function(buf) {
                    var ch = buf.getChannelData(0);
                    var nonZero = false;
                    for (var i = 0; i < 200; i++) {
                        if (ch[i] !== 0) { nonZero = true; break; }
                    }
                    globalThis.__WEBAUDIO_TEST.bufLen = ch.length;
                    globalThis.__WEBAUDIO_TEST.hasNonZero = nonZero;
                });
                // 2. RTCPeerConnection
                var pc = new (window.RTCPeerConnection || window.webkitRTCPeerConnection)();
                pc.createOffer().then(function(offer) {
                    globalThis.__WEBAUDIO_TEST.sdpType = offer.type;
                });
                // 3. SpeechSynthesis
                var voices = window.speechSynthesis.getVoices();
                globalThis.__WEBAUDIO_TEST.audioConstructed = !!actx;
                globalThis.__WEBAUDIO_TEST.rtcConstructed = !!pc;
                globalThis.__WEBAUDIO_TEST.voicesCount = voices.length;
                globalThis.__WEBAUDIO_TEST.cloakedAudio = window.OfflineAudioContext.toString().indexOf('[native code]') !== -1;
                globalThis.__WEBAUDIO_TEST.cloakedRTC = window.RTCPeerConnection.toString().indexOf('[native code]') !== -1;
            })()
        "#;
        rt.eval(js).unwrap();
        let _ = rt.drain_timers();
        let out = rt.eval("JSON.stringify(globalThis.__WEBAUDIO_TEST)").unwrap();
        let val: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(val["audioConstructed"], true);
        assert_eq!(val["rtcConstructed"], true);
        assert!(val["voicesCount"].as_u64().unwrap() >= 1);
        assert_eq!(val["cloakedAudio"], true);
        assert_eq!(val["cloakedRTC"], true);
        assert_eq!(val["sdpType"], "offer");
        assert_eq!(val["hasNonZero"], true);
    }

    #[test]
    fn test_runtime_declarative_shadow_dom() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        let html = r#"
            <html><body>
                <div id="card">
                    <template shadowrootmode="open">
                        <span id="shadow-text">Inside Shadow</span>
                    </template>
                    <p id="light-text">Inside Light</p>
                </div>
            </body></html>
        "#;
        rt.load_document(html, "https://example.com").unwrap();
        let js = r##"
            (function() {
                var card = document.getElementById("card");
                var shadow = card && card.shadowRoot;
                var shadowChild = shadow && shadow.querySelector("#shadow-text");
                return JSON.stringify({
                    hasShadowRoot: !!shadow,
                    shadowText: shadowChild ? shadowChild.textContent : null
                });
            })()
        "##;
        let out = rt.eval(js).unwrap();
        let val: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(val["hasShadowRoot"], true);
        assert_eq!(val["shadowText"], "Inside Shadow");
    }

    #[test]
    fn test_runtime_webcrypto_subtle_and_shadow_dom_piercing() {
        let client = Client::new();
        let mut rt = Runtime::new(client, "https://example.com").unwrap();
        let html = r#"
            <html><body>
                <div id="host">
                    <template shadowrootmode="open">
                        <button id="pierced-btn" class="inner">ShadowAction</button>
                    </template>
                </div>
            </body></html>
        "#;
        rt.load_document(html, "https://example.com").unwrap();
        let js = r##"
            (function() {
                globalThis.__CRYPTO_RES = {};
                // 1. Shadow-piercing >>> combinator and ShadowRoot.getElementById
                var pierced = document.querySelector("#host >>> #pierced-btn");
                var byId = document.getElementById("host").shadowRoot.getElementById("pierced-btn");
                globalThis.__CRYPTO_RES.piercedText = pierced ? pierced.textContent : null;
                globalThis.__CRYPTO_RES.sameNode = (pierced === byId);

                // 2. WebCrypto HMAC sign + verify + AES-GCM encrypt/decrypt + error-honest unsupported
                var enc = new TextEncoder();
                crypto.subtle.importKey(
                    "raw",
                    enc.encode("super-secret-hmac-key-32-bytes!!"),
                    { name: "HMAC", hash: "SHA-256" },
                    true,
                    ["sign", "verify"]
                ).then(function(key) {
                    return crypto.subtle.sign("HMAC", key, enc.encode("payload-123")).then(function(sig) {
                        globalThis.__CRYPTO_RES.hmacLen = sig.byteLength;
                        return crypto.subtle.verify("HMAC", key, sig, enc.encode("payload-123"));
                    });
                }).then(function(verified) {
                    globalThis.__CRYPTO_RES.hmacVerified = verified;
                    return crypto.subtle.importKey(
                        "raw",
                        new Uint8Array(16),
                        { name: "AES-GCM" },
                        false,
                        ["encrypt", "decrypt"]
                    );
                }).then(function(aesKey) {
                    var iv = new Uint8Array(12);
                    return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, aesKey, enc.encode("secret-msg")).then(function(ct) {
                        globalThis.__CRYPTO_RES.ctLen = ct.byteLength;
                        return crypto.subtle.decrypt({ name: "AES-GCM", iv: iv }, aesKey, ct);
                    });
                }).then(function(pt) {
                    globalThis.__CRYPTO_RES.decrypted = (new TextDecoder()).decode(pt);
                    return crypto.subtle.importKey("raw", new Uint8Array(8), { name: "RSA-OAEP" }, false, ["encrypt"]).then(function() {
                        globalThis.__CRYPTO_RES.unsupportedRejected = false;
                    }, function(err) {
                        globalThis.__CRYPTO_RES.unsupportedRejected = true;
                        globalThis.__CRYPTO_RES.unsupportedName = err.name || String(err);
                    });
                });
            })()
        "##;
        rt.eval(js).unwrap();
        rt.engine.pump_jobs();
        let _ = rt.drain_timers();
        let out = rt.eval("JSON.stringify(globalThis.__CRYPTO_RES)").unwrap();
        let val: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(val["piercedText"], "ShadowAction");
        assert_eq!(val["sameNode"], true);
        assert_eq!(val["hmacLen"], 32);
        assert_eq!(val["hmacVerified"], true);
        assert_eq!(val["ctLen"], 26); // 10 bytes + 16-byte GCM tag
        assert_eq!(val["decrypted"], "secret-msg");
        assert_eq!(val["unsupportedRejected"], true);
    }
}
