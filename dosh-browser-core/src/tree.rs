//! `tree.rs` — Native Rust HTML5 parser (replaces `webresearch/tree.py`).
//!
//! Uses `kuchiki` (html5ever + cssparser) to build a DOM and outputs
//! the JSON tree shape the engine expects for JS hydration.
//!
//! Matches the `html_to_tree` contract of the Python product exactly:
//! root is `{"t": "document", ...}`, text nodes are `{"t": "#text", "x": "..."}`.

use kuchiki::traits::TendrilSink;
use kuchiki::NodeRef;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;

/// The JSON tree root produced by Python's `html_to_tree`.
pub fn html_to_tree(html: &str) -> Value {
    let dom = kuchiki::parse_html().one(html);
    let mut children: Vec<Value> = Vec::new();
    let has_html_tag = html.to_lowercase().contains("<html");

    if has_html_tag {
        if let Ok(mut sel) = dom.select("html") {
            if let Some(html_node) = sel.next() {
                if let Some(v) = node_to_json(html_node.as_node()) {
                    children.push(v);
                }
            }
        }
    } else {
        let body = dom
            .select("body")
            .ok()
            .and_then(|mut sel| sel.next())
            .map(|n| n.as_node().clone());
        let parent = match &body {
            Some(b) => b,
            None => &dom,
        };
        for child in parent.children() {
            if let Some(v) = node_to_json(&child) {
                children.push(v);
            }
        }
    }
    json!({
        "t": "document",
        "a": {},
        "c": children,
    })
}

fn node_to_json(node: &NodeRef) -> Option<Value> {
    if let Some(elem) = node.as_element() {
        let tag = elem.name.local.to_string();
        let mut attrs = BTreeMap::new();
        for (name, attr) in elem.attributes.borrow().map.iter() {
            attrs.insert(name.local.to_string(), json!(attr.value.clone()));
        }
        let mut children: Vec<Value> = Vec::new();
        for child in node.children() {
            if let Some(v) = node_to_json(&child) {
                children.push(v);
            }
        }
        Some(json!({
            "t": tag,
            "a": attrs,
            "c": children,
        }))
    } else if let Some(t) = node.as_text() {
        let text = t.borrow().clone();
        if text.trim().is_empty() {
            None
        } else {
            Some(json!({
                "t": "#text",
                "x": text,
            }))
        }
    } else {
        None
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct ScriptTag {
    pub src: String,
    pub code: String,
    pub r#type: String,
    pub integrity: String,
    pub defer: bool,
    pub nonce: String,
}

/// Extract all <script> tags from HTML, preserving document order.
pub fn extract_scripts(html: &str) -> Vec<ScriptTag> {
    let dom = kuchiki::parse_html().one(html);
    let mut scripts = Vec::new();
    if let Ok(iter) = dom.select("script") {
        for node in iter {
            let elem = node.as_node();
            let mut src = String::new();
            let mut r#type = String::new();
            let mut integrity = String::new();
            let mut nonce = String::new();
            let mut defer = false;

            if let Some(data) = elem.as_element() {
                let attrs = data.attributes.borrow();
                if let Some(s) = attrs.get("src") {
                    src = s.to_string();
                }
                if let Some(t) = attrs.get("type") {
                    r#type = t.to_string();
                }
                if let Some(i) = attrs.get("integrity") {
                    integrity = i.to_string();
                }
                if let Some(n) = attrs.get("nonce") {
                    nonce = n.to_string();
                }
                if attrs.contains("defer") || attrs.contains("async") {
                    defer = true;
                }
            }

            let mut code = String::new();
            for child in elem.children() {
                if let Some(t) = child.as_text() {
                    code.push_str(&t.borrow());
                }
            }

            scripts.push(ScriptTag {
                src,
                code,
                r#type,
                integrity,
                defer,
                nonce,
            });
        }
    }
    scripts
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct IframeInfo {
    pub src: String,
    pub srcdoc: String,
    pub title: String,
    pub name: String,
    pub width: String,
    pub height: String,
    pub sandbox: String,
}

/// Collect all <iframe> tags and their attributes.
pub fn collect_iframes(html: &str) -> Vec<IframeInfo> {
    let dom = kuchiki::parse_html().one(html);
    let mut iframes = Vec::new();
    if let Ok(iter) = dom.select("iframe") {
        for node in iter {
            if let Some(data) = node.as_node().as_element() {
                let attrs = data.attributes.borrow();
                iframes.push(IframeInfo {
                    src: attrs.get("src").unwrap_or_default().to_string(),
                    srcdoc: attrs.get("srcdoc").unwrap_or_default().to_string(),
                    title: attrs.get("title").unwrap_or_default().to_string(),
                    name: attrs.get("name").unwrap_or_default().to_string(),
                    width: attrs.get("width").unwrap_or_default().to_string(),
                    height: attrs.get("height").unwrap_or_default().to_string(),
                    sandbox: attrs.get("sandbox").unwrap_or_default().to_string(),
                });
            }
        }
    }
    iframes
}

/// A contiguous, flat DOM representation inspired by JavaScriptCore's Butterfly layout.
/// Nodes, attributes, and children are packed into flat contiguous arrays, minimizing pointer indirection and GC fragmentation.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct DomButterfly {
    pub nodes: Vec<ButterflyNode>,
    pub attrs: Vec<(String, String)>,
    pub children: Vec<usize>,
    pub text_pool: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ButterflyNode {
    pub tag: String,
    pub attr_start: usize,
    pub attr_count: usize,
    pub child_start: usize,
    pub child_count: usize,
    pub text_start: usize,
    pub text_len: usize,
}

impl DomButterfly {
    /// Build a contiguous DomButterfly from a recursive Value tree.
    pub fn from_value(tree: &Value) -> Self {
        let mut bf = Self::default();
        if tree.is_null() {
            return bf;
        }
        bf.ingest_node(tree);
        bf
    }

    fn ingest_node(&mut self, val: &Value) -> usize {
        let node_idx = self.nodes.len();
        let tag = val.get("t").and_then(|v| v.as_str()).unwrap_or("").to_string();

        let mut text_start = 0;
        let mut text_len = 0;
        if let Some(x) = val.get("x").and_then(|v| v.as_str()) {
            text_start = self.text_pool.len();
            text_len = x.len();
            self.text_pool.push_str(x);
        }

        let attr_start = self.attrs.len();
        let mut attr_count = 0;
        if let Some(map) = val.get("a").and_then(|v| v.as_object()) {
            for (k, v) in map {
                let v_str = match v {
                    Value::String(s) => s.clone(),
                    other => other.to_string(),
                };
                self.attrs.push((k.clone(), v_str));
                attr_count += 1;
            }
        }

        // Push placeholder
        self.nodes.push(ButterflyNode {
            tag,
            attr_start,
            attr_count,
            child_start: 0,
            child_count: 0,
            text_start,
            text_len,
        });

        // Ingest children
        let mut child_indices = Vec::new();
        if let Some(c_arr) = val.get("c").and_then(|v| v.as_array()) {
            for c_val in c_arr {
                let c_idx = self.ingest_node(c_val);
                child_indices.push(c_idx);
            }
        }

        let child_start = self.children.len();
        let child_count = child_indices.len();
        self.children.extend(child_indices);

        self.nodes[node_idx].child_start = child_start;
        self.nodes[node_idx].child_count = child_count;

        node_idx
    }

    /// Recursion-free trampoline traversal to find nodes matching tag.
    pub fn find_by_tag(&self, tag: &str) -> Vec<usize> {
        let mut matches = Vec::new();
        if self.nodes.is_empty() {
            return matches;
        }

        let mut stack = vec![0]; // DFS trampoline stack
        while let Some(idx) = stack.pop() {
            let node = &self.nodes[idx];
            if node.tag.eq_ignore_ascii_case(tag) {
                matches.push(idx);
            }
            // Push children in reverse order so first child is popped next
            for i in (0..node.child_count).rev() {
                stack.push(self.children[node.child_start + i]);
            }
        }
        matches
    }

    /// Read attribute of a node in contiguous memory.
    pub fn get_attr(&self, node_idx: usize, name: &str) -> Option<&str> {
        let node = self.nodes.get(node_idx)?;
        for i in 0..node.attr_count {
            let (ref k, ref v) = self.attrs[node.attr_start + i];
            if k.eq_ignore_ascii_case(name) {
                return Some(v.as_str());
            }
        }
        None
    }

    /// Read text content of a node.
    pub fn get_text(&self, node_idx: usize) -> Option<&str> {
        let node = self.nodes.get(node_idx)?;
        if node.text_len > 0 {
            Some(&self.text_pool[node.text_start..node.text_start + node.text_len])
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_li() {
        let tree = html_to_tree("<li>hello</li>");
        let arr = tree["c"].as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["t"], "li");
    }

    #[test]
    fn test_root_is_document() {
        let tree = html_to_tree("<li>hello</li>");
        assert_eq!(tree["t"], "document");
    }

    #[test]
    fn test_parse_with_attr() {
        let tree = html_to_tree("<div id='foo'><span>bar</span></div>");
        let arr = tree["c"].as_array().unwrap();
        assert_eq!(arr[0]["t"], "div");
        assert_eq!(arr[0]["a"]["id"], "foo");
        assert_eq!(arr[0]["c"][0]["t"], "span");
    }

    #[test]
    fn test_text_node_shape() {
        let tree = html_to_tree("<li>hello</li>");
        let arr = tree["c"].as_array().unwrap();
        assert_eq!(arr[0]["c"][0]["t"], "#text");
        assert_eq!(arr[0]["c"][0]["x"], "hello");
    }

    #[test]
    fn test_extract_scripts() {
        let html = r#"<html><head><script src="/bundle.js" defer></script></head><body><script type="module">console.log("hello");</script></body></html>"#;
        let scripts = extract_scripts(html);
        assert_eq!(scripts.len(), 2);
        assert_eq!(scripts[0].src, "/bundle.js");
        assert!(scripts[0].defer);
        assert_eq!(scripts[1].r#type, "module");
        assert_eq!(scripts[1].code, "console.log(\"hello\");");
    }

    #[test]
    fn test_collect_iframes() {
        let html = r#"<div><iframe src="https://example.com/frame" title="test-frame" width="300" height="200"></iframe></div>"#;
        let frames = collect_iframes(html);
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].src, "https://example.com/frame");
        assert_eq!(frames[0].title, "test-frame");
        assert_eq!(frames[0].width, "300");
    }

    #[test]
    fn test_dom_butterfly_layout() {
        let html = r#"<div id="container" class="box"><p class="text">Hello</p><span>World</span></div>"#;
        let tree = html_to_tree(html);
        let bf = DomButterfly::from_value(&tree);

        // Find elements with trampoline
        let ps = bf.find_by_tag("p");
        assert_eq!(ps.len(), 1);
        assert_eq!(bf.get_attr(ps[0], "class"), Some("text"));

        let divs = bf.find_by_tag("div");
        assert_eq!(divs.len(), 1);
        assert_eq!(bf.get_attr(divs[0], "id"), Some("container"));
        assert_eq!(bf.get_attr(divs[0], "class"), Some("box"));

        // Text nodes
        let texts = bf.find_by_tag("#text");
        assert_eq!(texts.len(), 2);
        assert_eq!(bf.get_text(texts[0]), Some("Hello"));
        assert_eq!(bf.get_text(texts[1]), Some("World"));
    }
}
