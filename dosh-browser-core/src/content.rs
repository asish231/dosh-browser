//! `content.rs` — Content root detection (replaces `webresearch/content.py`).
//!
//! Finds the DOM container most likely to hold the page's main reading
//! content, and identifies nodes inside page chrome (nav/aside/footer/ads).
//! Byte-for-byte parity with Python's selectolax-based implementation.

use kuchiki::NodeRef;
use kuchiki::traits::TendrilSink;
use std::collections::{HashMap, HashSet};

pub const SITE_ROOT_SELECTORS: &[&str] = &[
    "#mw-content-text .mw-parser-output",
    "#mw-content-text",
];

pub const GENERIC_ROOT_SELECTORS: &[&str] = &[
    "main",
    "[role='main']",
    "article",
    ".post-content",
    ".entry-content",
    ".post-body",
    "#content",
    "body",
];

fn root_selector_priority(sel: &str) -> i64 {
    match sel {
        "main" => 6,
        "[role='main']" => 5,
        "#content" => 4,
        ".post-content" => 3,
        ".entry-content" => 3,
        ".post-body" => 3,
        "article" => 2,
        "body" => 1,
        _ => 0,
    }
}

const SCORABLE_BLOCK_TAGS: &[&str] = &[
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "table",
];

const SKIP_TAGS: &[&str] = &["nav", "aside", "form", "footer"];
const SKIP_ROLES: &[&str] = &["navigation", "banner", "contentinfo", "complementary"];
const SKIP_IDS: &[&str] = &[
    "toc", "mw-navigation", "mw-panel", "p-lang", "siteSub", "jump-to-nav",
    "google_ads", "ad-root", "advertisement", "ad_unit", "ad-container", "sponsor-block",
];
const SKIP_CLASS_TOKENS: &[&str] = &[
    "toc", "vector-toc", "mw-portlet", "infobox", "navbox", "sidebar", "hatnote",
    "mw-editsection", "reference", "references", "mw-references-wrap", "navbox-styles",
    "ad", "ads", "advertisement", "ad-container", "ad-slot", "banner-ad", "google-ad",
    "taboola", "outbrain", "sponsored", "promoted", "sponsor", "commercial",
];
const NEVER_CHROME_TAGS: &[&str] = &["html", "body"];

/// Collect all descendant text of a node, joined with spaces, whitespace collapsed.
/// Mirrors Python's `" ".join(node.text(separator=" ", strip=True).split())`.
pub fn text_content(node: &NodeRef) -> String {
    let mut parts = Vec::new();
    collect_text(node, &mut parts);
    parts.join(" ").split_whitespace().collect::<Vec<_>>().join(" ")
}

fn collect_text(node: &NodeRef, parts: &mut Vec<String>) {
    for child in node.children() {
        if let Some(t) = child.as_text() {
            let text = t.borrow().clone();
            parts.push(text);
        } else if child.as_element().is_some() {
            collect_text(&child, parts);
        }
    }
}

/// Get tag name of a node, lowercase ("div" etc). "" for non-elements.
pub fn tag_name(node: &NodeRef) -> String {
    match node.as_element() {
        Some(e) => e.name.local.to_string().to_lowercase(),
        None => String::new(),
    }
}

/// Get attribute value by name (case-insensitive on HTML).
pub fn attr(node: &NodeRef, name: &str) -> Option<String> {
    let elem = node.as_element()?;
    let attrs = elem.attributes.borrow();
    let map = &attrs.map;
    // HTML attributes are case-insensitive; the LocalName is stored as-is.
    for (k, v) in map.iter() {
        if k.local.as_ref().eq_ignore_ascii_case(name) {
            return Some(v.value.clone());
        }
    }
    None
}

/// Class tokens, with sub-tokens from both underscores and dashes.
/// Mirrors Python's `_class_tokens`.
pub fn class_tokens(node: &NodeRef) -> HashSet<String> {
    let raw = attr(node, "class").unwrap_or_default();
    let mut tokens = HashSet::new();
    for tok in raw.split_whitespace() {
        let low = tok.to_lowercase();
        tokens.insert(low.clone());
        for sub in low.replace("_", "-").split('-') {
            if !sub.is_empty() {
                tokens.insert(sub.to_string());
            }
        }
    }
    tokens
}

/// True when `ancestor` is a strict ancestor of `node` (kuchiki nodes compare by identity).
pub fn is_ancestor_of(node: &NodeRef, ancestor: &NodeRef) -> bool {
    let mut current = node.parent();
    while let Some(ref p) = current {
        if *p == *ancestor {
            return true;
        }
        current = p.parent();
    }
    false
}

/// True if node sits in nav/aside/toc/infobox/ads, stopping at the article root.
pub fn inside_chrome(node: &NodeRef, stop: Option<&NodeRef>) -> bool {
    let mut current = Some(node.clone());
    while let Some(ref cur) = current {
        if let Some(s) = stop {
            if *cur == *s {
                break;
            }
        }
        let tag = tag_name(cur);
        if NEVER_CHROME_TAGS.contains(&tag.as_str()) {
            return false;
        }
        if SKIP_TAGS.contains(&tag.as_str()) {
            return true;
        }
        let role = attr(cur, "role").unwrap_or_default().to_lowercase();
        if SKIP_ROLES.contains(&role.as_str()) {
            return true;
        }
        let nid = attr(cur, "id").unwrap_or_default().to_lowercase();
        if SKIP_IDS.contains(&nid.as_str()) {
            return true;
        }
        let has_skip_sub = nid.replace('_', "-").split('-')
            .any(|sub| !sub.is_empty() && SKIP_IDS.contains(&sub));
        if has_skip_sub {
            return true;
        }
        if !class_tokens(cur).is_disjoint(&skip_class_tokens_set()) {
            return true;
        }
        current = cur.parent();
    }
    false
}

fn skip_class_tokens_set() -> HashSet<String> {
    SKIP_CLASS_TOKENS.iter().map(|s| s.to_string()).collect()
}

/// Score a reading container by its projected non-chrome content.
/// Returns (words, headings, blocks).
fn candidate_score(candidate: &NodeRef) -> (i64, i64, i64) {
    let mut words = 0i64;
    let mut blocks = 0i64;
    let mut headings = 0i64;
    let sel_str = SCORABLE_BLOCK_TAGS.join(",");
    if let Ok(sel) = candidate.select(&sel_str) {
        for node in sel {
            let node_ref = node.as_node();
            if inside_chrome(node_ref, Some(candidate)) {
                continue;
            }
            let text = text_content(node_ref);
            if text.is_empty() {
                continue;
            }
            words += text.split_whitespace().count() as i64;
            blocks += 1;
            if tag_name(node_ref).starts_with('h') {
                headings += 1;
            }
        }
    }
    (words, headings, blocks)
}

/// The container most likely to hold the page's main reading content.
pub fn content_root(tree: &NodeRef) -> Option<NodeRef> {
    // Site-specific selectors win outright.
    for selector in SITE_ROOT_SELECTORS {
        match tree.select(selector) {
            Ok(mut sel) => {
                if let Some(first) = sel.next() {
                    return Some(first.as_node().clone());
                }
            }
            Err(_) => {}
        }
    }

    // Score generic landmarks.
    let mut candidates: Vec<(i64, NodeRef, (i64, i64, i64))> = Vec::new();
    for selector in GENERIC_ROOT_SELECTORS {
        if let Ok(sel) = tree.select(selector) {
            for candidate in sel {
                let node = candidate.as_node().clone();
                let score = candidate_score(&node);
                let priority = root_selector_priority(selector);
                candidates.push((priority, node, score));
            }
        }
    }

    // Precompute ancestor chains once (O(n*depth), not O(n^2*depth)).
    let chains: Vec<Vec<NodeRef>> = candidates
        .iter()
        .map(|(_, c, _)| {
            let mut chain = vec![c.clone()];
            let mut node = c.parent();
            while let Some(ref p) = node {
                if chain.len() >= 24 {
                    break;
                }
                chain.push(p.clone());
                node = p.parent();
            }
            chain
        })
        .collect();
    let anc_tags: Vec<HashSet<String>> = chains
        .iter()
        .map(|ch| {
            ch[1..]
                .iter()
                .filter(|n| !tag_name(n).is_empty())
                .map(tag_name)
                .collect()
        })
        .collect();
    let mut container_tags = HashSet::new();
    for ch in &chains {
        for anc in &ch[1..] {
            let t = tag_name(anc);
            if !t.is_empty() {
                container_tags.insert(t);
            }
        }
    }

    let mut best: Option<(i64, i64, i64, i64)> = None;
    let mut best_node: Option<NodeRef> = None;
    for (idx, (priority, candidate, (mut words, mut headings, mut blocks))) in candidates.iter().enumerate() {
        let ctag = tag_name(candidate);
        // Candidate indexes strictly inside this one.
        let mut descendant_idx: Vec<usize> = Vec::new();
        if container_tags.contains(&ctag) {
            for ydx in 0..candidates.len() {
                if ydx == idx {
                    continue;
                }
                for anc in &chains[ydx][1..] {
                    if anc == candidate {
                        descendant_idx.push(ydx);
                        break;
                    }
                }
            }
        }
        if !descendant_idx.is_empty() {
            // Split into top-level siblings vs nested duplicates.
            let mut nested = HashSet::new();
            for &a in &descendant_idx {
                let a_anc_tags = &anc_tags[a];
                for &b in &descendant_idx {
                    if a == b {
                        continue;
                    }
                    let b_node = &candidates[b].1;
                    let b_tag = tag_name(b_node);
                    if !a_anc_tags.contains(&b_tag) {
                        continue;
                    }
                    for anc in &chains[a][1..] {
                        if anc == b_node {
                            nested.insert(a);
                            break;
                        }
                    }
                }
            }
            let top_level: Vec<usize> = descendant_idx.iter()
                .filter(|i| !nested.contains(i)).copied().collect();
            let mut keep_union = false;
            if top_level.len() >= 2 {
                let tags: HashSet<String> = top_level.iter()
                    .map(|i| tag_name(&candidates[*i].1)).collect();
                let same_tag = tags.len() == 1;
                let mut balanced_pair = true;
                if top_level.len() == 2 {
                    let mut sw: Vec<i64> = top_level.iter().map(|i| candidates[*i].2.0).collect();
                    sw.sort();
                    balanced_pair = sw[0] > 0 && sw[0] * 8 >= sw[sw.len() - 1];
                }
                keep_union = same_tag && balanced_pair;
            }
            if keep_union {
                for &ydx in nested.iter() {
                    let (ow, oh, ob) = candidates[ydx].2;
                    words = (words - ow).max(0);
                    headings = (headings - oh).max(0);
                    blocks = (blocks - ob).max(0);
                }
            } else {
                for &ydx in descendant_idx.iter() {
                    let (ow, oh, ob) = candidates[ydx].2;
                    words = (words - ow).max(0);
                    headings = (headings - oh).max(0);
                    blocks = (blocks - ob).max(0);
                }
            }
        }
        let key = (words, headings, blocks, *priority);
        if best.is_none() || key > best.unwrap() {
            best = Some(key);
            best_node = Some(candidate.clone());
        }
    }
    best_node
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    fn parse(html: &str) -> NodeRef {
        kuchiki::parse_html().one(html)
    }

    #[test]
    fn test_main_wins_over_body() {
        let dom = parse(r#"<main><p>article content here with many words to score</p></main>"#);
        let root = content_root(&dom).unwrap();
        assert_eq!(tag_name(&root), "main");
    }

    #[test]
    fn test_body_fallback() {
        let dom = parse("<div><p>plain</p></div>");
        let root = content_root(&dom).unwrap();
        assert_eq!(tag_name(&root), "body");
    }

    #[test]
    fn test_inside_chrome_nav() {
        let dom = parse("<nav><a href='/x'>nav link</a></nav>");
        let a = dom.select("a").unwrap().next().unwrap();
        assert!(inside_chrome(a.as_node(), None));
    }

    #[test]
    fn test_inside_chrome_content() {
        let dom = parse("<main><p>content</p></main>");
        let p = dom.select("p").unwrap().next().unwrap();
        assert!(!inside_chrome(p.as_node(), None));
    }

    #[test]
    fn test_inside_chrome_footer() {
        let dom = parse("<footer><span>x</span></footer>");
        let span = dom.select("span").unwrap().next().unwrap();
        assert!(inside_chrome(span.as_node(), None));
    }

    #[test]
    fn test_inside_chrome_ad_class() {
        let dom = parse(r#"<div class="ad-container"><span>buy</span></div>"#);
        let span = dom.select("span").unwrap().next().unwrap();
        assert!(inside_chrome(span.as_node(), None));
    }

    #[test]
    fn test_class_tokens() {
        let dom = parse(r#"<div class="HatNote toc_sub"></div>"#);
        let d = dom.select("div").unwrap().next().unwrap();
        let tokens = class_tokens(d.as_node());
        assert!(tokens.contains("hatnote"));
        assert!(tokens.contains("toc"));
        assert!(tokens.contains("sub"));
    }

    #[test]
    fn test_text_content() {
        let dom = parse("<div>Hello\n  <span>World</span></div>");
        let d = dom.select("div").unwrap().next().unwrap();
        assert_eq!(text_content(d.as_node()), "Hello World");
    }

    #[test]
    fn test_text_content_collapses() {
        let dom = parse("<p>a   b\n\nc</p>");
        let p = dom.select("p").unwrap().next().unwrap();
        assert_eq!(text_content(p.as_node()), "a b c");
    }

    #[test]
    fn test_wikipedia_content_root() {
        let html = r#"<div id="mw-content-text"><div class="mw-parser-output"><p>real article</p></div></div>"#;
        let dom = parse(html);
        let root = content_root(&dom).unwrap();
        // Python's content_root returns the inner .mw-parser-output div for this selector.
        assert_eq!(tag_name(&root), "div");
        assert!(attr(&root, "class").unwrap_or_default().contains("mw-parser-output"));
    }
}
