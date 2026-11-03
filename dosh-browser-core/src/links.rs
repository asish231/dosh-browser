//! `links.rs` — Link extraction (replaces `webresearch/links.py`).
//!
//! Extracts unique navigable links in document order. Skips chrome and
//! internal anchors.

use kuchiki::traits::TendrilSink;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::content::{content_root, inside_chrome};
use crate::types::Link;

const SKIP_SCHEMES: &[&str] = &["javascript", "mailto", "tel", "data"];

fn abs(base: &str, href: &str) -> Option<String> {
    let href = href.trim();
    if href.is_empty() || href.starts_with('#') {
        return None;
    }
    let base_url = match Url::parse(base) {
        Ok(u) => u,
        Err(_) => return None,
    };
    let url = match base_url.join(href) {
        Ok(u) => u,
        Err(_) => return None,
    };
    let scheme = url.scheme().to_lowercase();
    if SKIP_SCHEMES.contains(&scheme.as_str()) {
        return None;
    }
    if scheme != "http" && scheme != "https" {
        return None;
    }
    // Python's urljoin does not add a trailing slash to the root path.
    // Rust's url crate does. Strip it to match Python's byte-exact output.
    let mut out = url.to_string();
    if out.ends_with('/') && url.path() == "/" && url.query().is_none() && url.fragment().is_none() {
        out.pop();
    }
    Some(out)
}

pub fn count_all_links(html: &str, base_url: &str) -> usize {
    let tree = kuchiki::parse_html().one(html);
    let root = content_root(&tree);
    let root = root.as_ref().unwrap_or(&tree);
    let mut seen = std::collections::HashSet::new();
    if let Ok(sel) = root.select("a[href]") {
        for a in sel {
            let node = a.as_node();
            if inside_chrome(node, Some(root)) {
                continue;
            }
            let href = crate::content::attr(node, "href").unwrap_or_default();
            if href.starts_with("#cite") || href.contains("cite_note") || href.contains("cite_ref") {
                continue;
            }
            if let Some(url) = abs(base_url, &href) {
                seen.insert(url);
            }
        }
    }
    seen.len()
}

pub fn links(html: &str, base_url: &str, max_links: usize) -> Vec<Link> {
    let tree = kuchiki::parse_html().one(html);
    let root = content_root(&tree);
    let root = root.as_ref().unwrap_or(&tree);
    let mut seen = std::collections::HashMap::new();
    let mut ordered = Vec::new();
    if let Ok(sel) = root.select("a[href]") {
        for a in sel {
            let node = a.as_node();
            if inside_chrome(node, Some(root)) {
                continue;
            }
            let href = crate::content::attr(node, "href").unwrap_or_default();
            if href.starts_with("#cite") || href.contains("cite_note") || href.contains("cite_ref") {
                continue;
            }
            let url = abs(base_url, &href);
            if let Some(url) = url {
                let text = crate::content::text_content(node);
                if !seen.contains_key(&url) {
                    seen.insert(url.clone(), text.clone());
                    ordered.push(url);
                } else if !text.is_empty() && text.len() > seen[&url].len() {
                    seen.insert(url, text);
                }
                if ordered.len() >= max_links {
                    break;
                }
            }
        }
    }
    ordered.into_iter().enumerate().map(|(i, url)| Link {
        n: (i + 1) as i64,
        text: seen[&url].clone(),
        url,
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    #[test]
    fn test_links_extraction() {
        let html = r##"<main><a href="https://a.com">A</a><a href="https://b.com">B</a><a href="#top">Top</a></main>"##;
        let ls = links(html, "https://base.com", 10);
        assert_eq!(ls.len(), 2);
        assert_eq!(ls[0].n, 1);
        assert_eq!(ls[0].url, "https://a.com");
        assert_eq!(ls[1].n, 2);
    }

    #[test]
    fn test_links_dedup() {
        let html = r#"<main><a href="https://a.com">First</a><a href="https://a.com">Longer Text</a></main>"#;
        let ls = links(html, "https://base.com", 10);
        assert_eq!(ls.len(), 1);
        assert_eq!(ls[0].text, "Longer Text");
    }

    #[test]
    fn test_links_chrome_skip() {
        let html = r#"<nav><a href="https://nav.com">Nav</a></nav><main><a href="https://main.com">Main</a></main>"#;
        let ls = links(html, "https://base.com", 10);
        assert_eq!(ls.len(), 1);
        assert_eq!(ls[0].url, "https://main.com");
    }

    #[test]
    fn test_links_max_limit() {
        let html = r#"<main><a href="https://a.com">A</a><a href="https://b.com">B</a><a href="https://c.com">C</a></main>"#;
        let ls = links(html, "https://base.com", 2);
        assert_eq!(ls.len(), 2);
        assert_eq!(ls[1].url, "https://b.com");
    }
}
