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
                if let Ok(mut cons) = self.0.console.lock() {
                    if cons.len() < 500 {
                        cons.push(ConsoleEntry {
                            level,
                            message: msg,
                            url: current_url,
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
                    "sha256" => hex::encode(Sha256::digest(&bytes)),
                    "sha384" => hex::encode(Sha384::digest(&bytes)),
                    "sha512" => hex::encode(Sha512::digest(&bytes)),
                    _ => hex::encode(Sha256::digest(&bytes)),
                };
                Ok(Some(hash))
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

                let do_fetch = async move {
                    if let Some(net) = network_opt {
                        let mut req = crate::network::NetworkRequest::new(&abs_url);
                        req.method = method;
                        req.body = body;
                        req.client_origin = Some(page_origin);
                        req.caused_by = Some("v8_script".into());
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
                Ok(Some(json!({"url": "", "code": ""}).to_string()))
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
        tokio::task::block_in_place(|| handle.block_on(fut))
    } else if let Ok(rt) = tokio::runtime::Builder::new_current_thread().enable_all().build() {
        let _ = ctx;
        rt.block_on(fut)
    } else {
        unreachable!("no tokio runtime available for host call")
    }
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
        }));

        let engine = DoshActor::spawn(seam.clone(), 128 * 1024 * 1024)?;

        // 1. Register host callables
        engine.add_host("host_parse_html")?;
        engine.add_host("host_log")?;
        engine.add_host("host_crypto_digest")?;
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
        self.engine.eval(&format!("__load({payload}, {}); true", json!(url)))?;
        // Clear initial hydration elements from the injected script queue
        self.engine.eval("globalThis.__WR_INJECTED_SCRIPTS = []; true")?;
        let mut parena = doshgentv8_core::parena::Parena::new();
        parena.add_node(doshgentv8_core::parena::TagId::Html, 0, &[], "");
        let _ = self.engine.set_parena(parena);
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

    /// Drain injected script queue (created via createElement + appendChild).
    fn drain_injected_scripts(&mut self) -> Result<(), String> {
        let raw = self.engine.eval("JSON.stringify(globalThis.__WR_INJECTED_SCRIPTS || [])")?;
        if let Ok(scripts) = serde_json::from_str::<Vec<Value>>(&raw) {
            for s in scripts.iter().take(40) {
                let text = s["text"].as_str().unwrap_or_default().trim();
                if !text.is_empty() {
                    let _ = self.engine.eval(text);
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

        let scripts = tree::extract_scripts(html);
        let mut classic = Vec::new();
        let mut deferred = Vec::new();

        for s in scripts {
            let st = s.r#type.to_lowercase();
            if st.contains("ld+json") || st.contains("json") || st.contains("importmap") {
                continue;
            }
            if s.defer {
                deferred.push(s);
            } else {
                classic.push(s);
            }
        }

        // 1. Run classic & deferred scripts in order
        let mut ran_count = 0;
        for s in classic.into_iter().chain(deferred.into_iter()) {
            if ran_count >= MAX_SCRIPTS {
                break;
            }
            if !s.code.trim().is_empty() {
                match self.engine.eval(&s.code) {
                    Ok(_) => {
                        ran_count += 1;
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
        }
        info.scripts = ran_count;

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
}
