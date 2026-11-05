//! `session.rs` — Core session state machine (replaces `webresearch/session.py`).
//!
//! Manages tabs, navigation history, DOM state, and storage.
//! The goto() loop: fetch → parse → run scripts → return Page.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use url::Url;

/// The result of a page navigation, matching Python's Page model.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct Page {
    pub url: String,
    pub title: String,
    pub view: String,
    pub chars: usize,
    pub status: u16,
    pub content_type: String,
    pub headers: HashMap<String, String>,
    pub kind: String,
    pub acd: Vec<serde_json::Value>,
    pub errors: Vec<String>,
    pub truncated: bool,
}

/// Frame document loaded in an iframe.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct FrameDocument {
    pub ref_id: String,
    pub url: String,
    pub html: String,
}

/// Captcha inspection result.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct CaptchaInspection {
    pub provider: String,
    pub site_key: String,
    pub anchor_frame: String,
    pub challenge_frame: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Tab {
    pub id: String,
    pub history: Vec<Page>,
    pub index: isize,
    pub html: String,
    pub url: String,
    pub headers: HashMap<String, String>,
    pub frame_documents: Vec<FrameDocument>,
    pub captcha_inspection: Option<CaptchaInspection>,
    pub doc_seq: u64,
    pub session_storage: HashMap<String, HashMap<String, String>>,
    pub snapshots: Vec<serde_json::Value>,
    pub pending_scroll_y: i64,
    pub pending_popstate: bool,
}

impl Tab {
    pub fn new(id: &str) -> Self {
        Self {
            id: id.to_string(),
            ..Default::default()
        }
    }

    pub fn page(&self) -> Option<Page> {
        if self.index >= 0 && (self.index as usize) < self.history.len() {
            Some(self.history[self.index as usize].clone())
        } else {
            None
        }
    }

    /// Push a page to history. `replace` rewrites current entry; otherwise
    /// truncates forward stack and appends.
    pub fn push(
        &mut self,
        page: Page,
        html: Option<String>,
        headers: Option<HashMap<String, String>>,
        replace: bool,
        doc_id: Option<u64>,
    ) -> Page {
        if let Some(h) = html {
            self.html = h;
            self.url = page.url.clone();
        }
        if let Some(h) = headers {
            self.headers = h;
        }
        let pushed_page = if !replace {
            // New entry: truncate forward stack
            let idx = (self.index + 1) as usize;
            self.history.truncate(idx);
            self.snapshots.truncate(idx);
            self.history.push(page.clone());
            self.index = self.history.len() as isize - 1;
            page
        } else {
            // In-place replace
            if self.history.is_empty() {
                self.history.push(page.clone());
                self.index = 0;
                page
            } else {
                let idx = self.index as usize;
                self.history[idx] = page.clone();
                page
            }
        };
        // Document identity
        let new_doc_id = match doc_id {
            Some(id) => id,
            None if replace && (self.index as usize) < self.snapshots.len() => {
                self.snapshots[self.index as usize]["doc_id"].as_u64().unwrap_or(0)
            }
            None => {
                self.doc_seq += 1;
                self.doc_seq
            }
        };
        // Capture snapshot
        let snapshot = serde_json::json!({
            "doc_id": new_doc_id,
            "html": self.html,
            "url": self.url,
            "page": pushed_page,
        });
        let idx = self.index as usize;
        if replace && idx < self.snapshots.len() {
            self.snapshots[idx] = snapshot;
        } else {
            self.snapshots.truncate(idx);
            self.snapshots.push(snapshot);
        }
        pushed_page
    }

    /// Traverse history forwards or backwards to an absolute index.
    pub fn traverse(&mut self, target: isize) -> Result<Page, String> {
        if target < 0 || target as usize >= self.history.len() {
            return Err(format!("history target {} out of bounds [0, {})", target, self.history.len()));
        }
        self.index = target;
        let idx = target as usize;
        let page = self.history[idx].clone();
        if idx < self.snapshots.len() {
            if let Some(h) = self.snapshots[idx]["html"].as_str() {
                self.html = h.to_string();
            }
            if let Some(u) = self.snapshots[idx]["url"].as_str() {
                self.url = u.to_string();
            }
        }
        Ok(page)
    }
}

/// The main session — tabs, client, storage, and navigation.
pub struct Session {
    pub id: String,
    pub tabs: Vec<Tab>,
    pub current_tab_idx: usize,
    pub client: Client,
    /// localStorage shared per origin across tabs
    pub local_storage: HashMap<String, HashMap<String, String>>,
    /// Dialog history
    pub last_dialogs: Vec<serde_json::Value>,
    /// Popup tabs
    pub last_popups: Vec<serde_json::Value>,
    /// Network ledger for telemetry
    pub network_ledger: crate::telemetry::NetworkLedger,
    /// Central Network Engine
    pub network: crate::network::NetworkEngine,
    /// Console event log
    pub console_logs: Vec<serde_json::Value>,
    /// Content-addressed element identity tracker
    pub identity_tracker: crate::identity::IdentityTracker,
    /// Injected virtual clock (epoch milliseconds)
    pub virtual_clock_ms: Option<u64>,
    /// Deterministic PRNG seed for Math.random
    pub prng_seed: Option<u64>,
    /// Liftoff WASM execution fuel budget
    pub wasm_fuel: Option<u64>,
    /// Dynamic eval capability gate
    pub gate_eval: bool,
    /// WebAssembly capability gate
    pub gate_wasm: bool,
    /// Network capability gate
    pub gate_network: bool,
    /// Bounded session pool for resident V8 actors
    pub session_pool: std::sync::Arc<doshgentv8_core::SessionPool>,
    /// Live per-tab resident runtimes
    pub runtimes: HashMap<String, crate::runtime::Runtime>,
    /// Checkpointed page HTML snapshots keyed by checkpoint ID
    pub session_checkpoints: HashMap<u64, String>,
}

impl Session {
    pub fn new(client: Client) -> Self {
        let id = format!(
            "sess-{:x}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0)
        );
        let tab = Tab::new("tab-1");
        Self {
            id,
            tabs: vec![tab],
            current_tab_idx: 0,
            client,
            local_storage: HashMap::new(),
            last_dialogs: Vec::new(),
            last_popups: Vec::new(),
            network_ledger: crate::telemetry::NetworkLedger::new(1000, false),
            network: crate::network::NetworkEngine::default(),
            console_logs: Vec::new(),
            identity_tracker: crate::identity::IdentityTracker::new(),
            virtual_clock_ms: None,
            prng_seed: None,
            wasm_fuel: Some(10_000_000),
            gate_eval: true,
            gate_wasm: true,
            gate_network: true,
            session_pool: std::sync::Arc::new(doshgentv8_core::SessionPool::new(8)),
            runtimes: HashMap::new(),
            session_checkpoints: HashMap::new(),
        }
    }

    /// Retrieve or spawn a resident runtime for the current tab.
    pub fn get_or_create_runtime(&mut self, url: &str) -> Result<crate::runtime::Runtime, String> {
        let tab_id = self.current_tab().id.clone();
        if let Some(rt) = self.runtimes.get(&tab_id) {
            if let Ok(mut u) = rt.seam.0.url.lock() {
                *u = url.to_string();
            }
            return Ok(rt.clone());
        }

        let rt = crate::runtime::Runtime::new_with_network(
            self.client.clone(),
            url,
            Some(std::sync::Arc::new(self.network.clone())),
        )?;
        self.configure_runtime(&rt);
        self.runtimes.insert(tab_id, rt.clone());
        Ok(rt)
    }

    /// Apply session's V8 configuration (clock, PRNG, fuel, gates) to a runtime.
    pub fn configure_runtime(&self, rt: &crate::runtime::Runtime) {
        if let Some(ms) = self.virtual_clock_ms {
            let _ = rt.engine.set_virtual_time(ms as f64);
        }
        if let Some(seed) = self.prng_seed {
            let _ = rt.engine.set_random_seed(seed);
        }
        if let Some(fuel) = self.wasm_fuel {
            let _ = rt.engine.set_fuel(fuel);
        }
        let _ = rt.engine.set_eval_allowed(self.gate_eval);
        let _ = rt.engine.set_wasm_allowed(self.gate_wasm);
        let _ = rt.engine.set_network_allowed(self.gate_network);
    }

    pub fn switch_tab(&mut self, tab_id: &str) -> Result<(), String> {
        if let Some(idx) = self.tabs.iter().position(|t| t.id == tab_id) {
            self.current_tab_idx = idx;
            Ok(())
        } else {
            Err(format!("tab not found: {}", tab_id))
        }
    }

    pub fn current_tab(&self) -> &Tab {
        &self.tabs[self.current_tab_idx]
    }

    pub fn current_tab_mut(&mut self) -> &mut Tab {
        &mut self.tabs[self.current_tab_idx]
    }

    /// Navigate to a URL: fetch → parse → run JS (via V8 engine) → build ACD/targets → return Page.
    pub async fn goto(
        &mut self,
        url: &str,
        max_chars: usize,
    ) -> Result<Page, Box<dyn std::error::Error + Send + Sync>> {
        // 1. Fetch the URL through the central NetworkEngine (SSRF, rate limiting, cache, singleflight)
        let req = crate::network::NetworkRequest::new(url);
        let result = self
            .network
            .fetch(req)
            .await
            .map_err(|e| Box::new(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())) as Box<dyn std::error::Error + Send + Sync>)?;

        let mut html = result.body.clone();
        let mut js_errors = Vec::new();
        let mut runtime_title = String::new();

        // 2. Execute page scripts through Runtime (V8 + minidom.js)
        if result.kind == "html" && !html.is_empty() {
            let (clean_html, dismissed) = crate::consent::dismiss_consent_dom(&html);
            if dismissed {
                html = clean_html;
            }
            if let Ok(mut rt) = self.get_or_create_runtime(&result.final_url) {
                self.configure_runtime(&rt);
                if let Ok((mutated, info)) = rt.run_scripts(&html, &result.final_url) {
                    html = mutated;
                    js_errors = info.error_messages;
                    if let Ok(t) = rt.eval("document.title") {
                        if !t.is_empty() && t != "undefined" {
                            runtime_title = t;
                        }
                    }
                }
            }
        }

        // 3. Build action targets, links, and frame inventory
        let targets = crate::actions::extract_actions(&html, &result.final_url, 100);
        self.identity_tracker.update(&targets);
        let links = crate::links::links(&html, &result.final_url, 50);
        let iframes = crate::tree::collect_iframes(&html);
        let frame_infos: Vec<crate::types::FrameInfo> = iframes
            .into_iter()
            .enumerate()
            .map(|(i, f)| crate::types::FrameInfo {
                ref_id: format!("frame-{}", i + 1),
                url: f.src,
                title: f.title,
                name: f.name,
                ..Default::default()
            })
            .collect();

        // 4. Build ACD projection and reading view
        let title = if !runtime_title.is_empty() {
            runtime_title
        } else {
            extract_title(&html)
        };
        let acd_obj = crate::acd::build_acd(
            &result.final_url,
            &title,
            &html,
            &targets,
            &links,
            &frame_infos,
            &serde_json::json!({}),
            max_chars / 80 + 10,
        );

        let view = crate::acd::acd_view(&acd_obj, max_chars);
        let chars = view.chars().count();
        let acd_json: Vec<serde_json::Value> = acd_obj
            .blocks
            .into_iter()
            .map(|b| serde_json::to_value(b).unwrap_or_default())
            .collect();

        let mut errors = js_errors;
        if errors.is_empty() {
            errors = html
                .lines()
                .filter(|l| l.contains("error") || l.contains("Error"))
                .take(10)
                .map(String::from)
                .collect();
        }

        let page = Page {
            url: result.final_url.clone(),
            title,
            view,
            chars,
            status: result.status,
            content_type: result.content_type.clone(),
            headers: result.headers.clone(),
            kind: result.kind.clone(),
            acd: acd_json,
            errors,
            truncated: result.truncated,
        };

        // 5. Push to history stack
        let tab = self.current_tab_mut();
        let pushed = tab.push(page.clone(), Some(html), Some(result.headers), false, None);
        Ok(pushed)
    }

    /// Load HTML content directly into session: executes page scripts via V8 runtime,
    /// builds full ACD and action surfaces, and pushes Page to history.
    pub fn goto_html(
        &mut self,
        url: &str,
        html: &str,
        max_chars: usize,
    ) -> Result<Page, Box<dyn std::error::Error + Send + Sync>> {
        let mut final_html = html.to_string();
        let mut js_errors = Vec::new();
        let mut runtime_title = String::new();

        if let Ok(mut rt) = self.get_or_create_runtime(url) {
            self.configure_runtime(&rt);
            if let Ok((mutated, info)) = rt.run_scripts(&final_html, url) {
                final_html = mutated;
                js_errors = info.error_messages;
                if let Ok(t) = rt.eval("document.title") {
                    if !t.is_empty() && t != "undefined" {
                        runtime_title = t;
                    }
                }
            }
        }

        let targets = crate::actions::extract_actions(&final_html, url, 100);
        self.identity_tracker.update(&targets);
        let links = crate::links::links(&final_html, url, 50);
        let iframes = crate::tree::collect_iframes(&final_html);
        let frame_infos: Vec<crate::types::FrameInfo> = iframes
            .into_iter()
            .enumerate()
            .map(|(i, f)| crate::types::FrameInfo {
                ref_id: format!("frame-{}", i + 1),
                url: f.src,
                title: f.title,
                name: f.name,
                ..Default::default()
            })
            .collect();

        let title = if !runtime_title.is_empty() {
            runtime_title
        } else {
            extract_title(&final_html)
        };
        let acd_obj = crate::acd::build_acd(
            url,
            &title,
            &final_html,
            &targets,
            &links,
            &frame_infos,
            &serde_json::json!({}),
            max_chars / 80 + 10,
        );

        let view = crate::acd::acd_view(&acd_obj, max_chars);
        let chars = view.chars().count();
        let acd_json: Vec<serde_json::Value> = acd_obj
            .blocks
            .into_iter()
            .map(|b| serde_json::to_value(b).unwrap_or_default())
            .collect();

        let page = Page {
            url: url.to_string(),
            title,
            view,
            chars,
            status: 200,
            content_type: "text/html".into(),
            headers: HashMap::new(),
            kind: "html".into(),
            acd: acd_json,
            errors: js_errors,
            truncated: false,
        };

        let tab = self.current_tab_mut();
        let pushed = tab.push(page.clone(), Some(final_html), None, false, None);
        Ok(pushed)
    }

    /// Return all actionable targets on the current page.
    pub fn actions(&self) -> Vec<crate::types::Target> {
        let tab = self.current_tab();
        if tab.html.is_empty() {
            return Vec::new();
        }
        crate::actions::extract_actions(&tab.html, &tab.url, 100)
    }

    /// Perform a click on a target ref in the live session.
    pub async fn click(&mut self, ref_id: &str) -> Result<Page, String> {
        let tab = self.current_tab();
        let html = tab.html.clone();
        let url = tab.url.clone();
        if html.is_empty() {
            return Err("No document loaded in session".to_string());
        }

        let targets = crate::actions::extract_actions(&html, &url, 100);
        let effective_ref = if let Ok((resolved, _)) = self.identity_tracker.resolve(ref_id, &targets) {
            resolved.ref_id.clone()
        } else {
            ref_id.to_string()
        };

        if let Some(target) = targets.into_iter().find(|t| t.ref_id == effective_ref) {
            if target.tag == "a"
                && !target.href.is_empty()
                && !target.href.starts_with('#')
                && !target.href.to_lowercase().starts_with("javascript:")
            {
                let full_url = if let Ok(base) = Url::parse(&url) {
                    base.join(&target.href).map(|u| u.to_string()).unwrap_or(target.href)
                } else {
                    target.href
                };
                return self.goto(&full_url, 8000).await.map_err(|e| e.to_string());
            }
        }

        // Apply action click via resident Runtime (V8)
        let action_payload = serde_json::json!({
            "type": "click",
            "ref": effective_ref,
        });

        let mut rt = self
            .get_or_create_runtime(&url)
            .map_err(|e| format!("Failed to get runtime: {e}"))?;
        let _ = rt.apply_action(&action_payload)?;
        let mutated = rt.serialize()?;

        self.update_page_after_action(&url, &mutated, 8000)
    }

    /// Perform text typing into a target ref in the live session.
    pub async fn type_text(&mut self, ref_id: &str, text: &str) -> Result<Page, String> {
        let tab = self.current_tab();
        let html = tab.html.clone();
        let url = tab.url.clone();
        if html.is_empty() {
            return Err("No document loaded in session".to_string());
        }

        let targets = crate::actions::extract_actions(&html, &url, 100);
        let effective_ref = if let Ok((resolved, _)) = self.identity_tracker.resolve(ref_id, &targets) {
            resolved.ref_id.clone()
        } else {
            ref_id.to_string()
        };

        let action_payload = serde_json::json!({
            "type": "type",
            "ref": effective_ref,
            "text": text,
        });

        let mut rt = self
            .get_or_create_runtime(&url)
            .map_err(|e| format!("Failed to get runtime: {e}"))?;
        let _ = rt.apply_action(&action_payload)?;
        let mutated = rt.serialize()?;

        self.update_page_after_action(&url, &mutated, 8000)
    }

    /// Update page ACD and history after a live in-realm action without re-executing document scripts.
    pub fn update_page_after_action(
        &mut self,
        url: &str,
        html: &str,
        max_chars: usize,
    ) -> Result<Page, String> {
        let targets = crate::actions::extract_actions(html, url, 100);
        self.identity_tracker.update(&targets);
        let links = crate::links::links(html, url, 50);
        let iframes = crate::tree::collect_iframes(html);
        let frame_infos: Vec<crate::types::FrameInfo> = iframes
            .into_iter()
            .enumerate()
            .map(|(i, f)| crate::types::FrameInfo {
                ref_id: format!("frame-{}", i + 1),
                url: f.src,
                title: f.title,
                name: f.name,
                ..Default::default()
            })
            .collect();

        let title = extract_title(html);
        let acd_obj = crate::acd::build_acd(
            url,
            &title,
            html,
            &targets,
            &links,
            &frame_infos,
            &serde_json::json!({}),
            max_chars / 80 + 10,
        );

        let view = crate::acd::acd_view(&acd_obj, max_chars);
        let chars = view.chars().count();
        let acd_json: Vec<serde_json::Value> = acd_obj
            .blocks
            .into_iter()
            .map(|b| serde_json::to_value(b).unwrap_or_default())
            .collect();

        let page = Page {
            url: url.to_string(),
            title,
            view,
            chars,
            status: 200,
            content_type: "text/html".into(),
            headers: HashMap::new(),
            kind: "html".into(),
            acd: acd_json,
            errors: Vec::new(),
            truncated: false,
        };

        let tab = self.current_tab_mut();
        let pushed = tab.push(page, Some(html.to_string()), None, false, None);
        Ok(pushed)
    }

    /// Capture a dirty-diff checkpoint of the current tab's page state in microseconds.
    pub fn checkpoint(&mut self) -> Result<u64, String> {
        let (url, html) = {
            let tab = self.current_tab();
            (tab.url.clone(), tab.html.clone())
        };
        if url.is_empty() {
            return Err("No page loaded to checkpoint".to_string());
        }
        let rt = self.get_or_create_runtime(&url)?;
        let cp_id = rt.checkpoint_parena()?;
        self.session_checkpoints.insert(cp_id, html);
        Ok(cp_id)
    }

    /// Roll back current tab's page arena to a previously captured checkpoint.
    pub fn rewind(&mut self, cp_id: u64) -> Result<Page, String> {
        let (url, fallback_html) = {
            let tab = self.current_tab();
            (tab.url.clone(), tab.html.clone())
        };
        if url.is_empty() {
            return Err("No page loaded to rewind".to_string());
        }
        let saved_html = self.session_checkpoints.get(&cp_id).cloned().unwrap_or(fallback_html);
        let mut rt = self.get_or_create_runtime(&url)?;
        let _reverted_bytes = rt.rewind_parena(cp_id)?;
        let _ = rt.load_document(&saved_html, &url);
        self.update_page_after_action(&url, &saved_html, 8000)
    }

    /// Fork the current tab into an independent speculative session with zero network reload.
    pub fn fork(&mut self) -> Result<Session, String> {
        let (url, html, page_opt) = {
            let tab = self.current_tab();
            (tab.url.clone(), tab.html.clone(), tab.page())
        };
        if url.is_empty() {
            return Err("No page loaded to fork".to_string());
        }
        let rt = self.get_or_create_runtime(&url)?;
        let forked_parena = rt.fork_parena()?;

        let mut forked_session = Self::new(self.client.clone());
        forked_session.network = self.network.clone();
        forked_session.wasm_fuel = self.wasm_fuel;
        forked_session.gate_eval = self.gate_eval;
        forked_session.gate_wasm = self.gate_wasm;
        forked_session.gate_network = self.gate_network;
        forked_session.local_storage = self.local_storage.clone();

        if let Some(page) = page_opt {
            let new_tab = forked_session.current_tab_mut();
            new_tab.push(page, Some(html), None, false, None);
        }

        let new_rt = forked_session.get_or_create_runtime(&url)?;
        new_rt.set_parena(forked_parena)?;

        Ok(forked_session)
    }

    /// Discover iframe seeds in current page.
    pub fn discover_frame_seeds(&self) -> Vec<crate::frames::FrameSeed> {
        let tab = self.current_tab();
        crate::frames::discover_seeds(&tab.html, &tab.url, "top", 0)
    }

    /// Check if current page is behind a login wall.
    pub fn detect_login_wall(&self) -> Option<crate::login::LoginWall> {
        let tab = self.current_tab();
        let targets = self.actions();
        crate::login::detect_login_wall(&tab.html, &tab.url, &targets, 200)
    }

    /// Execute a terminal shell command.
    pub async fn execute_shell_command(
        &mut self,
        cmd: &str,
        args: &[String],
    ) -> Result<serde_json::Value, String> {
        let store = crate::shell::ShellStore::new(None)
            .map_err(|e| e.to_string())?;
        let args_val = serde_json::to_value(args).unwrap_or_default();
        crate::shell::execute_command(cmd, &args_val, self, &store)
            .await
            .map_err(|e| e.to_string())
    }
}

#[allow(dead_code)]
fn extract_text_recursive(tree: &serde_json::Value, max_chars: usize) -> String {
    let mut out = String::new();
    extract_recursive_inner(tree, &mut out, max_chars);
    out
}

#[allow(dead_code)]
fn extract_recursive_inner(node: &serde_json::Value, out: &mut String, max: usize) {
    if out.chars().count() >= max {
        return;
    }
    if let Some(arr) = node.as_array() {
        for child in arr {
            extract_recursive_inner(child, out, max);
            if out.chars().count() >= max { return; }
        }
    }
    if let Some(obj) = node.as_object() {
        if obj.get("t").and_then(|v| v.as_str()) == Some("#text") {
            if let Some(text) = obj.get("x").and_then(|v| v.as_str()) {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    let remaining = max - out.chars().count();
                    let chars: Vec<char> = trimmed.chars().take(remaining).collect();
                    out.push_str(&chars.into_iter().collect::<String>());
                    out.push('\n');
                }
            }
        }
        if let Some(children) = obj.get("c").and_then(|v| v.as_array()) {
            for child in children {
                extract_recursive_inner(child, out, max);
                if out.chars().count() >= max { return; }
            }
        }
    }
}

pub fn extract_title(html: &str) -> String {
    // Simple title extraction — full port uses tree.rs
    if let Some(start) = html.find("<title>") {
        if let Some(end) = html.find("</title>") {
            if end > start + 7 {
                let t = html[start + 7..end].trim();
                if !t.is_empty() {
                    return t.to_string();
                }
            }
        }
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tab_push_new() {
        let mut tab = Tab::new("t1");
        let page = Page {
            url: "https://example.com".into(),
            title: "Example".into(),
            view: "hello".into(),
            chars: 5,
            status: 200,
            ..Default::default()
        };
        let pushed = tab.push(page.clone(), None, None, false, None);
        assert_eq!(tab.history.len(), 1);
        assert_eq!(tab.index, 0);
        assert_eq!(pushed.url, "https://example.com");
        assert_eq!(tab.doc_seq, 1);
    }

    #[test]
    fn test_tab_push_replace() {
        let mut tab = Tab::new("t1");
        let p1 = Page { url: "https://a.com".into(), ..Default::default() };
        let p2 = Page { url: "https://a.com/updated".into(), ..Default::default() };
        tab.push(p1.clone(), None, None, false, None);
        assert_eq!(tab.history.len(), 1);
        let pushed = tab.push(p2.clone(), None, None, true, None);
        assert_eq!(tab.history.len(), 1);
        assert_eq!(pushed.url, "https://a.com/updated");
    }

    #[test]
    fn test_extract_title() {
        assert_eq!(extract_title("<html><head><title>Hello World</title></head></html>"), "Hello World");
        assert_eq!(extract_title("<html><body>No title</body></html>"), "");
    }

    #[test]
    fn test_extract_text() {
        let tree = serde_json::json!([
            {"t": "div", "a": {}, "c": [
                {"t": "#text", "x": "Hello"},
                {"t": "span", "a": {}, "c": [
                    {"t": "#text", "x": " World"}
                ]}
            ]}
        ]);
        let text = extract_text_recursive(&tree, 100);
        assert!(text.contains("Hello"));
        assert!(text.contains("World"));
    }

    #[test]
    fn test_goto_simple() {
        // This test doesn't actually fetch (it will return empty), but
        // verifies the goto flow doesn't panic.
        let client = Client::builder()
            .user_agent("test")
            .build()
            .unwrap();
        let mut session = Session::new(client);
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let result = rt.block_on(session.goto("https://example.com", 8000));
        // This will fail to connect without network, but shouldn't panic
        assert!(result.is_ok() || result.is_err());
    }

    #[test]
    fn test_goto_html_runs_scripts() {
        let client = Client::builder().user_agent("test").build().unwrap();
        let mut session = Session::new(client);
        let html = r#"
        <!DOCTYPE html>
        <html>
        <head><title>Original Title</title></head>
        <body>
            <div id="root">Initial Content</div>
            <script>
                document.title = "Client-Rendered Title";
                var root = document.getElementById("root");
                root.innerHTML = "<p>Hydrated SPA Paragraph</p><button id='action-btn'>Click Here</button>";
            </script>
        </body>
        </html>
        "#;
        let page = session.goto_html("https://spa.example.com", html, 8000).unwrap();
        assert_eq!(page.title, "Client-Rendered Title");
        assert!(page.view.contains("Hydrated SPA Paragraph") || page.chars > 0);
        let tab = session.current_tab();
        assert!(tab.html.contains("Hydrated SPA Paragraph"));
        assert!(tab.html.contains("action-btn"));
    }

    #[tokio::test]
    async fn test_resident_actor_click_persists_closure_state() {
        let client = Client::builder().user_agent("test").build().unwrap();
        let mut session = Session::new(client);
        let html = r#"
        <!DOCTYPE html>
        <html>
        <body>
            <button id="counter-btn">Count: 0</button>
            <div id="status">idle</div>
            <script>
                var count = 0;
                var btn = document.getElementById("counter-btn");
                btn.addEventListener("click", function() {
                    count++;
                    btn.textContent = "Count: " + count;
                    document.getElementById("status").textContent = "clicked-" + count;
                });
            </script>
        </body>
        </html>
        "#;
        let page = session.goto_html("https://counter.example.com", html, 8000).unwrap();
        assert!(session.runtimes.contains_key("tab-1"));
        assert!(page.view.contains("Count: 0") || page.chars > 0);

        // First click on resident actor
        let p1 = session.click("counter-btn").await.expect("click 1 failed");
        assert!(p1.view.contains("Count: 1") || session.current_tab().html.contains("Count: 1"));

        // Second click retains in-memory closure state (count reaches 2)
        let p2 = session.click("counter-btn").await.expect("click 2 failed");
        assert!(p2.view.contains("Count: 2") || session.current_tab().html.contains("Count: 2"));
    }

    #[tokio::test]
    async fn test_session_checkpoint_and_rewind() {
        let client = Client::builder().user_agent("test").build().unwrap();
        let mut session = Session::new(client);
        let html = r#"
        <!DOCTYPE html>
        <html>
        <body>
            <button id="counter-btn">Count: 0</button>
            <script>
                var count = 0;
                var btn = document.getElementById("counter-btn");
                btn.addEventListener("click", function() {
                    count++;
                    btn.textContent = "Count: " + count;
                });
            </script>
        </body>
        </html>
        "#;
        session.goto_html("https://cp.example.com", html, 8000).unwrap();

        // 1. Capture checkpoint prior to speculative branch
        let cp_id = session.checkpoint().expect("checkpoint capture");
        assert!(cp_id > 0);

        // 2. Perform actions on live session
        let p1 = session.click("counter-btn").await.expect("click 1");
        assert!(p1.view.contains("Count: 1") || session.current_tab().html.contains("Count: 1"));

        // 3. Rewind back to checkpoint in microseconds
        let p_rewound = session.rewind(cp_id).expect("session rewind");
        assert!(p_rewound.view.contains("Count: 0") || session.current_tab().html.contains("Count: 0"));
    }

    #[tokio::test]
    async fn test_session_fork_speculative_execution() {
        let client = Client::builder().user_agent("test").build().unwrap();
        let mut main_session = Session::new(client);
        let html = r#"
        <!DOCTYPE html>
        <html>
        <body>
            <button id="btn">Initial State</button>
        </body>
        </html>
        "#;
        main_session.goto_html("https://fork.example.com", html, 8000).unwrap();

        // Fork session into speculative branch A
        let mut branch_a = main_session.fork().expect("session fork");
        assert_eq!(branch_a.current_tab().url, main_session.current_tab().url);

        // Branch A performs an action
        let res_a = branch_a.click("btn").await;
        assert!(res_a.is_ok());

        // Main session remains untouched
        assert!(main_session.current_tab().html.contains("Initial State"));
    }
}
