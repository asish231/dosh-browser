//! `tree.rs` — Native Rust HTML5 parser (replaces `webresearch/tree.py`).
//!
//! Uses `kuchiki` (html5ever + cssparser) to build a DOM and outputs
//! the JSON tree shape the engine expects for JS hydration.
//!
//! Matches the `html_to_tree` contract of the Python product exactly:
//! root is `{"t": "document", ...}`, text nodes are `{"t": "#text", "x": "..."}`.

use kuchiki::traits::TendrilSink;
use kuchiki::NodeRef;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;

/// The JSON tree root produced by Python's `html_to_tree`.
pub fn html_to_tree(html: &str) -> Value {
    let dom = kuchiki::parse_html().one(html);
    // kuchiki wraps fragments in <html><head></head><body>...</body></html>.
    // Python returns a document root whose children are the parsed nodes.
    // We mirror that: collect body children (or all element children of html
    // if no body) under a synthetic "document" root.
    let mut children: Vec<Value> = Vec::new();
    // find body
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
}
