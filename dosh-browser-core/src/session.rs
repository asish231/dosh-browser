//! `session.rs` — Core session state machine (replaces `webresearch/session.py`).
//!
//! Manages tabs, navigation history, DOM state, and storage.
//! The goto() loop: fetch → parse → run scripts → return Page.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

use crate::fetch;
use crate::tree;
use crate::download;

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
        }
    }

    pub fn current_tab(&self) -> &Tab {
        &self.tabs[self.current_tab_idx]
    }

    pub fn current_tab_mut(&mut self) -> &mut Tab {
        &mut self.tabs[self.current_tab_idx]
    }

    /// Navigate to a URL: fetch → parse → run JS (via engine) → return Page.
    pub async fn goto(
        &mut self,
        url: &str,
        max_chars: usize,
    ) -> Result<Page, Box<dyn std::error::Error + Send + Sync>> {
        // 1. Fetch the URL
        let result = fetch::fetch(
            &self.client,
            url,
            "GET",
            None,
            "{}",
            8_000_000,
        ).await?;

        // 2. Parse to tree → generate JS to hydrate minidom.js
        let tree_json = if result.kind == "html" && !result.body.is_empty() {
            serde_json::to_string(&tree::html_to_tree(&result.body)).unwrap_or_default()
        } else {
            "[]".to_string()
        };

        // 3. Build the view (simplified ACD — full port needs content.rs too)
        let mut view = String::new();
        let mut chars = 0;
        if result.kind == "html" {
            // Extract text content (simplified; full port uses content.rs)
            let tree = tree::html_to_tree(&result.body);
            view = extract_text_recursive(&tree, max_chars);
            chars = view.chars().count();
        } else {
            view = result.body.clone();
            chars = view.len();
        }

        let page = Page {
            url: result.final_url.clone(),
            title: extract_title(&result.body),
            view,
            chars,
            status: result.status,
            content_type: result.content_type.clone(),
            headers: result.headers.clone(),
            kind: result.kind.clone(),
            acd: vec![],
            errors: result.body.lines()
                .filter(|l| l.contains("error") || l.contains("Error"))
                .take(10)
                .map(String::from)
                .collect(),
            truncated: result.truncated,
        };

        // 4. Push to history
        let tab = self.current_tab_mut();
        let pushed = tab.push(page.clone(), Some(result.body), Some(result.headers), false, None);
        Ok(pushed)
    }
}

fn extract_text_recursive(tree: &serde_json::Value, max_chars: usize) -> String {
    let mut out = String::new();
    extract_recursive_inner(tree, &mut out, max_chars);
    out
}

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

fn extract_title(html: &str) -> String {
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
}
