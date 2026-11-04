//! `acd.rs` — Agent Context Document generation (replaces `webresearch/acd.py`).
//!
//! Projects the raw DOM into structured, retrievable blocks (text, tables,
//! headings, JSON-LD schemas) and an action surface for the agent.
//! Mirrors the Python logic exactly (selectors, scoring, region fallbacks).

use kuchiki::NodeRef;
use kuchiki::traits::TendrilSink;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

use crate::content::{content_root, inside_chrome, tag_name, text_content, attr};
use crate::types::*;
use crate::provenance::extract_provenance;
use crate::features::extract_features;
use crate::classifier::classify_node;
use crate::cluster::cluster_siblings;

const BLOCK_SEL: &[&str] = &[
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "table",
    "[itemprop='description']", "[itemprop='reviewBody']", "[itemprop='text']",
    "[itemprop='articleBody']", "[itemprop='headline']", "[itemprop='caption']", "[itemprop='abstract']",
];

const REGION_SEL: &[&str] = &[
    "article", "[role='article']",
    "[class*='card']", "[class*='item']", "[class*='post']", "[class*='quote']",
    "[data-testid*='card']",
];

const REGION_MIN_CHARS: usize = 40;

/// Locate a DOM node's path: "div.card > h2".
fn locator(node: &NodeRef) -> String {
    let mut parts = Vec::new();
    let mut cur = Some(node.clone());
    let mut depth = 0;
    while let Some(n) = cur {
        if depth >= 4 { break; }
        let tag = tag_name(&n);
        if tag.is_empty() || tag == "-document" { break; }
        if let Some(id) = attr(&n, "id") {
            parts.push(format!("#{}", id));
            break;
        } else {
            let mut part = tag.clone();
            let mut cls = Vec::new();
            if let Some(c) = attr(&n, "class") {
                for t in c.split_whitespace().take(2) {
                    cls.push(t.to_string());
                }
            }
            if !cls.is_empty() {
                part.push('.');
                part.push_str(&cls.join("."));
            }
            parts.push(part);
        }
        cur = n.parent();
        depth += 1;
    }
    parts.reverse();
    parts.join(" > ")
}

#[allow(dead_code)]
fn emit_block(
    blocks: &mut Vec<AcdBlock>,
    counter: &mut i64,
    block_type: &str,
    text: String,
    level: Option<i64>,
    locator: String,
    rows: Vec<Vec<String>>,
    role: String,
) {
    emit_block_with_node(blocks, counter, block_type, text, level, locator, rows, role, None);
}

fn emit_block_with_node(
    blocks: &mut Vec<AcdBlock>,
    counter: &mut i64,
    block_type: &str,
    text: String,
    level: Option<i64>,
    locator: String,
    rows: Vec<Vec<String>>,
    role: String,
    node: Option<&NodeRef>,
) {
    *counter += 1;
    let (provenance, semantic_category, confidence) = if let Some(n) = node {
        let prov = extract_provenance(n);
        let feats = extract_features(n);
        let classif = classify_node(&feats);
        (Some(prov), Some(classif.category.as_str().to_string()), Some(classif.confidence))
    } else {
        (None, None, None)
    };

    blocks.push(AcdBlock {
        id: format!("b{}", counter),
        block_type: block_type.to_string(),
        text,
        level,
        ref_id: "".to_string(),
        role,
        href: "".to_string(),
        rows,
        locator,
        provenance,
        semantic_category,
        confidence,
        cluster_id: None,
    });
}

pub fn build_acd(
    url: &str,
    title: &str,
    html: &str,
    targets: &[Target],
    links: &[Link],
    frames: &[FrameInfo],
    js_info: &Value,
    max_blocks: usize,
) -> Acd {
    let tree = kuchiki::parse_html().one(html);
    // tree IS the root document node.
    let root = content_root(&tree).unwrap_or(tree.clone());
    
    let mut blocks: Vec<AcdBlock> = Vec::new();
    let mut counter = 0i64;
    let mut chrome_skipped = 0i64;
    let mut budget_hit = false;
    let mut seen_region_texts = HashSet::new();

    // --- Primary Pass: BLOCK_SEL ---
    let mut all_block_nodes = Vec::new();
    for sel in BLOCK_SEL {
        if let Ok(sel_iter) = root.select(sel) {
            for node in sel_iter {
                all_block_nodes.push(node.as_node().clone());
            }
        }
    }
    
    let mut blocks_total = all_block_nodes.len() as i64;

    for node in &all_block_nodes {
        if inside_chrome(node, Some(&root)) {
            chrome_skipped += 1;
            continue;
        }
        let tag = tag_name(node);
        
        if budget_hit { continue; }

        if tag == "table" {
            let parent = node.parent();
            if let Some(p) = &parent {
                if tag_name(p) == "table" { continue; }
            }
            let mut rows: Vec<Vec<String>> = Vec::new();
            if let Ok(trs) = node.select("tr") {
                for tr in trs {
                    let tr_node = tr.as_node();
                    let mut row: Vec<String> = Vec::new();
                    if let Ok(cells) = tr_node.select("th, td") {
                        for cell in cells {
                            let t = text_content(cell.as_node());
                            if !t.is_empty() { row.push(t); }
                        }
                    }
                    if !row.is_empty() { rows.push(row); }
                }
            }
            if !rows.is_empty() {
                let loc = locator(node);
                emit_block_with_node(&mut blocks, &mut counter, "table", "".to_string(), None, loc, rows, "".to_string(), Some(node));
                if blocks.len() >= max_blocks { budget_hit = true; break; }
            }
            continue;
        }

        let text = text_content(node);
        if text.is_empty() { continue; }
        
        let loc = locator(node);
        if tag.starts_with('h') {
            if let Ok(lvl) = tag[1..].parse::<i64>() {
                emit_block_with_node(&mut blocks, &mut counter, "heading", text, Some(lvl), loc, vec![], "".to_string(), Some(node));
            }
        } else if tag == "pre" {
            emit_block_with_node(&mut blocks, &mut counter, "code", text, None, "".to_string(), vec![], "".to_string(), Some(node));
        } else if tag == "li" {
            emit_block_with_node(&mut blocks, &mut counter, "list", text, None, "".to_string(), vec![], "".to_string(), Some(node));
        } else if tag == "blockquote" {
            emit_block_with_node(&mut blocks, &mut counter, "quote", text, None, loc, vec![], "".to_string(), Some(node));
        } else {
            emit_block_with_node(&mut blocks, &mut counter, "text", text, None, loc, vec![], "".to_string(), Some(node));
        }
        
        if blocks.len() >= max_blocks { budget_hit = true; break; }
    }

    // --- Region Fallback Pass ---
    if !budget_hit {
        let mut region_nodes = Vec::new();
        for sel in REGION_SEL {
            if let Ok(sel_iter) = root.select(sel) {
                for node in sel_iter {
                    let n = node.as_node();
                    if inside_chrome(n, Some(&root)) { continue; }
                    region_nodes.push(n.clone());
                }
            }
        }

        // Filter: only innermost regions, and no inner BLOCK_SEL matches.
        let qualifying: Vec<NodeRef> = region_nodes.iter().filter(|node| {
            // Check for other regions inside this one
            let mut has_nested_region = false;
            for sel in REGION_SEL {
                if let Ok(sel_iter) = node.select(sel) {
                    for sub in sel_iter {
                        if sub.as_node() != *node {
                            has_nested_region = true;
                            break;
                        }
                    }
                }
            }
            if has_nested_region { return false; }
            
            // Check for inner BLOCK_SEL matches
            let mut has_block = false;
            for sel in BLOCK_SEL {
                if let Ok(sel_iter) = node.select(sel) {
                    if sel_iter.count() > 0 {
                        has_block = true;
                        break;
                    }
                }
            }
            if has_block { return false; }

            let text = text_content(node);
            if text.chars().count() < REGION_MIN_CHARS { return false; }
            if seen_region_texts.contains(&text) { return false; }
            seen_region_texts.insert(text);

            true
        }).cloned().collect();

        blocks_total += qualifying.len() as i64;
        for node in &qualifying {
            if blocks.len() >= max_blocks { budget_hit = true; break; }
            let text = text_content(node);
            let loc = locator(node);
            emit_block_with_node(&mut blocks, &mut counter, "text", text, None, loc, vec![], "region".to_string(), Some(node));
        }
    }

    // --- JSON-LD Extraction ---
    let mut json_ld_data: Vec<Value> = Vec::new();
    if let Ok(sel) = tree.select("script[type='application/ld+json']") {
        for node in sel {
            let n = node.as_node();
            // Script contents are text node children of the <script> element
            let mut raw = String::new();
            for c in n.children() {
                if let Some(t) = c.as_text() {
                    raw.push_str(&t.borrow());
                }
            }
            if raw.is_empty() { continue; }
            if let Ok(val) = serde_json::from_str::<Value>(&raw) {
                match val {
                    Value::Array(arr) => {
                        json_ld_data.extend(arr.into_iter().filter(|v| v.is_object()));
                    }
                    obj @ Value::Object(_) => {
                        if let Some(Value::Array(graph)) = obj.get("@graph").cloned() {
                            json_ld_data.extend(graph.into_iter().filter(|v| v.is_object()));
                        }
                        json_ld_data.push(obj);
                    }
                    _ => {}
                }
            }
        }
    }

    // --- JSON-LD Projection ---
    let mut pending: Vec<(String, Option<i64>, String, Vec<Vec<String>>)> = Vec::new();
    
    for entity in &json_ld_data {
        if let Some(Value::String(etype)) = entity.get("@type") {
            match etype.as_str() {
                "FAQPage" => {
                    if let Some(Value::Array(items)) = entity.get("mainEntity") {
                        for faq in items {
                            if let Some(faq_obj) = faq.as_object() {
                                let q = faq_obj.get("name").and_then(|v| v.as_str()).unwrap_or("");
                                let a = if let Some(ans_obj) = faq_obj.get("acceptedAnswer").and_then(|v| v.as_object()) {
                                    ans_obj.get("text").and_then(|v| v.as_str()).unwrap_or("")
                                } else { "" };
                                if !q.is_empty() && !a.is_empty() {
                                    let clean_a = if a.contains('<') {
                                        // Strip HTML from answer text
                                        let dom = kuchiki::parse_html().one(a);
                                        text_content(&dom).split_whitespace().collect::<Vec<_>>().join(" ")
                                    } else { a.to_string() };
                                    pending.push(("heading".into(), Some(4), format!("Q: {}", q), vec![]));
                                    pending.push(("text".into(), None, clean_a, vec![]));
                                }
                            }
                        }
                    }
                }
                "HowTo" => {
                    if let Some(Value::String(name)) = entity.get("name") {
                        pending.push(("heading".into(), Some(3), name.clone(), vec![]));
                    }
                    if let Some(Value::Array(steps)) = entity.get("step") {
                        for step in steps {
                            if let Some(step_obj) = step.as_object() {
                                let s_name = step_obj.get("name").and_then(|v| v.as_str()).unwrap_or("");
                                let s_text = if let Some(dir) = step_obj.get("itemListElement").and_then(|v| v.as_object()) {
                                    dir.get("text").and_then(|v| v.as_str()).unwrap_or("")
                                } else {
                                    step_obj.get("text").and_then(|v| v.as_str()).unwrap_or("")
                                };
                                let combined = if !s_name.is_empty() && !s_text.is_empty() {
                                    format!("{}: {}", s_name, s_text)
                                } else {
                                    format!("{}{}", s_name, s_text)
                                };
                                if !combined.is_empty() {
                                    pending.push(("list".into(), None, combined, vec![]));
                                }
                            }
                        }
                    }
                }
                "Product" => {
                    let name = entity.get("name").and_then(|v| v.as_str()).unwrap_or("");
                    let description = entity.get("description").and_then(|v| v.as_str()).unwrap_or("");
                    if !name.is_empty() {
                        pending.push(("heading".into(), Some(4), format!("Product: {}", name), vec![]));
                    }
                    if !description.is_empty() {
                        let clean_d = if description.contains('<') {
                            let dom = kuchiki::parse_html().one(description);
                            text_content(&dom).split_whitespace().collect::<Vec<_>>().join(" ")
                        } else { description.to_string() };
                        pending.push(("text".into(), None, clean_d, vec![]));
                    }
                    if let Some(offers) = entity.get("offers") {
                        let offer_list = match offers {
                            Value::Array(arr) => arr.clone(),
                            Value::Object(_) => vec![offers.clone()],
                            _ => Vec::new(),
                        };
                        for offer in offer_list {
                            if let Some(off_obj) = offer.as_object() {
                                let price = off_obj.get("price").and_then(|v| v.as_str()).unwrap_or("");
                                let currency = off_obj.get("priceCurrency").and_then(|v| v.as_str()).unwrap_or("");
                                let availability = off_obj.get("availability").and_then(|v| v.as_str()).unwrap_or("");
                                if !price.is_empty() {
                                    let bits = vec![format!("Price: {} {}", price, currency)];
                                    let text = if !availability.is_empty() {
                                        format!("{} — {}", &bits[0], availability.rsplit('/').next().unwrap_or(&availability))
                                    } else {
                                        bits[0].clone()
                                    };
                                    pending.push(("text".into(), None, text, vec![]));
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }
    
    blocks_total += pending.len() as i64;
    for (b_type, level, text, rows) in &pending {
        if blocks.len() >= max_blocks { budget_hit = true; break; }
        counter += 1;
        blocks.push(AcdBlock {
            id: format!("b{}", counter),
            block_type: b_type.clone(),
            text: text.clone(),
            level: *level,
            ref_id: "".to_string(),
            role: "".to_string(),
            href: "".to_string(),
            rows: rows.clone(),
            locator: "".to_string(),
            ..Default::default()
        });
    }

    // --- Actions ---
    let mut actions: Vec<AcdAction> = Vec::new();
    for target in targets {
        let kind = if target.challenge {
            "click".to_string()
        } else if target.role == "link" {
            "navigate".to_string()
        } else if target.role == "textbox" || target.role == "select" {
            "type".to_string()
        } else if target.role == "slider" {
            "drag".to_string()
        } else if target.role == "iframe" {
            "embed".to_string()
        } else if target.role == "button" && (target.name == "submit" || target.name == "search") {
            "submit".to_string()
        } else if target.role == "button" || target.role == "checkbox" || target.role == "label" {
            "activate".to_string()
        } else {
            "activate".to_string()
        };

        let routing = if kind == "navigate" && !target.href.is_empty() {
            target.href.clone()
        } else if kind == "submit" {
            if !target.name.is_empty() { target.name.clone() } else { target.ref_id.clone() }
        } else if kind == "drag" {
            format!("drag:path={}", target.path.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(","))
        } else if kind == "embed" {
            target.href.clone()
        } else {
            target.ref_id.clone()
        };

        let label_text = if !target.text.is_empty() {
            target.text.clone()
        } else if !target.name.is_empty() {
            target.name.clone()
        } else {
            target.href.clone()
        };

        actions.push(AcdAction {
            ref_id: target.ref_id.clone(),
            kind,
            role: target.role.clone(),
            label: label_text.chars().take(160).collect(),
            name: target.name.clone(),
            href: target.href.clone(),
            run: if !routing.is_empty() { routing } else { target.ref_id.clone() },
        });
    }

    // --- API ---
    let mut api: Vec<AcdApi> = Vec::new();
    if let Some(arr) = js_info.get("resource_requests").and_then(|v| v.as_array()) {
        for (i, item) in arr.iter().enumerate() {
            api.push(AcdApi {
                index: (i + 1) as i64,
                url: item.get("url").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                status: item.get("status").and_then(|v| v.as_i64()).unwrap_or(0),
                content_type: item.get("content_type").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                method: item.get("method").and_then(|v| v.as_str()).unwrap_or("GET").to_string(),
                data: item.get("data").cloned(),
            });
        }
    }

    // --- Framework / Errors ---
    let mut framework: HashMap<String, Value> = js_info.get("framework_data")
        .and_then(|v| v.as_object())
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    if !json_ld_data.is_empty() {
        framework.insert("json_ld".to_string(), Value::Array(json_ld_data));
    }
    let errors: Vec<String> = js_info.get("error_messages")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
        .unwrap_or_default();

    // --- Counts / Unavailable ---
    let mut counts = HashMap::new();
    counts.insert("blocks".to_string(), blocks.len() as i64);
    counts.insert("blocks_total".to_string(), blocks_total);
    counts.insert("actions".to_string(), actions.len() as i64);
    counts.insert("links".to_string(), links.len() as i64);
    counts.insert("api".to_string(), api.len() as i64);
    counts.insert("frames".to_string(), frames.len() as i64);
    counts.insert("framework_keys".to_string(), framework.len() as i64);
    counts.insert("errors".to_string(), errors.len() as i64);
    
    // We can't know controls_total without the inspect module. In the Rust port,
    // this comes from the Action surface. For now, we default to targets.len().
    let controls_total = targets.len() as i64;
    counts.insert("actions_total".to_string(), controls_total);

    let mut unavailable: Vec<String> = Vec::new();
    if actions.iter().any(|a| a.kind == "drag") {
        unavailable.push("drag: numeric deltas come from the mini-DOM block-flow layout engine (approximate, not Blink-exact)".into());
    }
    if actions.iter().any(|a| a.kind == "embed") {
        unavailable.push("embed: iframe pixels are structurally decomposed, never rastered; frame postMessage lanes are live".into());
    }
    if controls_total > actions.len() as i64 {
        unavailable.push(format!("actions: {} interactive controls beyond the 80-ref budget did not receive refs", controls_total - actions.len() as i64));
    }
    if chrome_skipped > 0 {
        unavailable.push(format!("blocks: {} candidate content region(s) inside nav/header/footer chrome were skipped", chrome_skipped));
    }
    if budget_hit && blocks_total > blocks.len() as i64 {
        unavailable.push(format!("blocks: {} candidate content region(s) beyond the {}-block budget were not projected", blocks_total - blocks.len() as i64, max_blocks));
    }

    // --- Subtree Clustering for Collections (ACD 2.0) ---
    let mut collections = cluster_siblings(&root, 1);
    if let Ok(containers) = tree.select("div, section, main, ul") {
        for cont in containers {
            let sub_cols = cluster_siblings(cont.as_node(), collections.len() + 1);
            for c in sub_cols {
                if !collections.iter().any(|existing| existing.name == c.name) {
                    collections.push(c);
                }
            }
            if collections.len() >= 10 { break; }
        }
    }

    Acd {
        url: url.to_string(),
        title: title.to_string(),
        version: "2".to_string(),
        blocks,
        actions,
        links: links.to_vec(),
        api,
        frames: frames.to_vec(),
        framework,
        errors,
        counts,
        collections,
        unavailable,
    }
}

pub fn error_summary(errors: &[String], limit: usize, width: usize) -> String {
    if errors.is_empty() { return String::new(); }
    let mut counts: HashMap<String, usize> = HashMap::new();
    for err in errors {
        let msg = err.split_whitespace().take(width / 8).collect::<Vec<_>>().join(" "); // Approx width
        *counts.entry(msg).or_insert(0) += 1;
    }
    let mut ranked: Vec<_> = counts.iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(a.1).then_with(|| a.0.cmp(b.0)));
    
    let parts: Vec<String> = ranked.iter().take(limit).map(|(msg, count)| {
        if **count > 1 { format!("{} (x{})", msg, count) } else { msg.to_string() }
    }).collect();
    
    let hidden = ranked.len().saturating_sub(parts.len());
    let distinct = if ranked.len() > 1 { format!("{} distinct", ranked.len()) } else { "1 distinct".to_string() };
    let suffix = if hidden > 0 { format!(" ; +{} more distinct", hidden) } else { String::new() };
    format!("JS_ERRORS: {} error(s), {} — {}{}", errors.len(), distinct, parts.join("; "), suffix)
}

pub fn acd_view(acd: &Acd, limit: usize) -> String {
    let mut lines = Vec::new();
    lines.push(format!("ACD v{} — {} blocks, {} actions, {} api, {} frames", 
        acd.version, acd.blocks.len(), acd.actions.len(), acd.api.len(), acd.frames.len()));
    if !acd.framework.is_empty() {
        let keys: Vec<_> = acd.framework.keys().collect();
        lines.push(format!("FRAMEWORK_DATA: {}", keys.iter().map(|k| k.as_str()).collect::<Vec<_>>().join(", ")));
    }
    if !acd.errors.is_empty() {
        lines.push(error_summary(&acd.errors, 3, 160));
    }
    if !acd.unavailable.is_empty() {
        lines.push(format!("UNAVAILABLE: {}", acd.unavailable.iter().take(5).map(|s| s.as_str()).collect::<Vec<_>>().join("; ")));
    }
    
    let actions_total = acd.counts.get("actions_total").copied().unwrap_or(acd.actions.len() as i64);
    if actions_total > acd.actions.len() as i64 {
        lines.push(format!("ACTIONS: {} of {} (budget-truncated)", acd.actions.len(), actions_total));
    }
    let blocks_total = acd.counts.get("blocks_total").copied().unwrap_or(acd.blocks.len() as i64);
    if blocks_total > acd.blocks.len() as i64 {
        lines.push(format!("BLOCKS: {} of {} (some skipped or budget-truncated; see UNAVAILABLE)", acd.blocks.len(), blocks_total));
    }
    
    for block in acd.blocks.iter().take(limit) {
        if block.block_type == "table" {
            let cols = block.rows.first().map(|r| r.len()).unwrap_or(0);
            lines.push(format!("[{}] table {}x{}", block.id, block.rows.len(), cols));
            for row in block.rows.iter().take(4) {
                lines.push(format!("    | {} |", row.join(" | ")));
            }
        } else if block.block_type == "heading" {
            let lvl = block.level.unwrap_or(0);
            lines.push(format!("[{}] h{} {}", block.id, lvl, block.text.chars().take(120).collect::<String>()));
        } else if block.block_type == "code" {
            lines.push(format!("[{}] code {}", block.id, block.text.chars().take(80).collect::<String>()));
        } else {
            lines.push(format!("[{}] {}", block.id, block.text.chars().take(160).collect::<String>()));
        }
    }
    lines.join("\n")
}

pub fn query_acd(acd: &Acd, term: &str, limit: usize) -> Vec<AcdBlock> {
    let needle = term.to_lowercase();
    if needle.is_empty() {
        return acd.blocks.iter().take(limit).cloned().collect();
    }
    let mut out = Vec::new();
    for block in &acd.blocks {
        let mut hay = block.text.to_lowercase();
        if !block.rows.is_empty() {
            hay.push(' ');
            let all_text: Vec<&str> = block.rows.iter().flat_map(|r| r.iter().map(|s| s.as_str())).collect();
            hay.push_str(&all_text.join(" ").to_lowercase());
        }
        if hay.contains(&needle) {
            out.push(block.clone());
        }
        if out.len() >= limit { break; }
    }
    out
}

pub fn slice_acd(acd: &Acd, start: &str, end: &str, limit: usize) -> Result<Vec<AcdBlock>, String> {
    let ids: Vec<&str> = acd.blocks.iter().map(|b| b.id.as_str()).collect();
    if !start.is_empty() && !ids.contains(&start) {
        if ids.is_empty() { return Err(format!("unknown block id: {}; the ACD has no blocks", start)); }
        return Err(format!("unknown block id: {}; valid range is {}..{}", start, ids[0], ids[ids.len()-1]));
    }
    if !end.is_empty() && !ids.contains(&end) {
        if ids.is_empty() { return Err(format!("unknown block id: {}; the ACD has no blocks", end)); }
        return Err(format!("unknown block id: {}; valid range is {}..{}", end, ids[0], ids[ids.len()-1]));
    }
    let i = if start.is_empty() { 0 } else { ids.iter().position(|&id| id == start).unwrap_or(0) };
    let j = if end.is_empty() {
        (i + limit).min(acd.blocks.len())
    } else {
        ids.iter().position(|&id| id == end).map(|p| p + 1).unwrap_or(acd.blocks.len()).min(i + limit)
    };
    Ok(acd.blocks[i..j.min(i + limit)].to_vec())
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    #[test]
    fn test_acd_headings_and_text() {
        let html = "<main><h1>Title</h1><p>Para 1</p><p>Para 2</p></main>";
        let acd = build_acd(
            "https://a.com", "Test", html, &[], &[], &[], &serde_json::json!({}), 100
        );
        assert_eq!(acd.blocks.len(), 3);
        assert_eq!(acd.blocks[0].block_type, "heading");
        assert_eq!(acd.blocks[0].text, "Title");
        assert_eq!(acd.blocks[0].level, Some(1));
        assert_eq!(acd.blocks[1].block_type, "text");
        assert_eq!(acd.blocks[1].text, "Para 1");
    }

    #[test]
    fn test_acd_table() {
        let html = "<main><table><tr><th>A</th><td>B</td></tr></table></main>";
        let acd = build_acd(
            "https://a.com", "Test", html, &[], &[], &[], &serde_json::json!({}), 100
        );
        assert_eq!(acd.blocks.len(), 1);
        assert_eq!(acd.blocks[0].block_type, "table");
        assert_eq!(acd.blocks[0].rows.len(), 1);
        assert_eq!(acd.blocks[0].rows[0], vec!["A", "B"]);
    }

    #[test]
    fn test_acd_chrome_skip() {
        let html = "<nav><p>Nav</p></nav><main><p>Content</p></main>";
        let acd = build_acd(
            "https://a.com", "Test", html, &[], &[], &[], &serde_json::json!({}), 100
        );
        assert_eq!(acd.blocks.len(), 1);
        assert_eq!(acd.blocks[0].text, "Content");
    }

    #[test]
    fn test_acd_region_fallback() {
        // Div-based card layout
        let html = "<main><div class=\"card-item\">This is a very long text that describes the item and has enough chars to be seen</div></main>";
        let acd = build_acd(
            "https://a.com", "Test", html, &[], &[], &[], &serde_json::json!({}), 100
        );
        assert_eq!(acd.blocks.len(), 1);
        assert_eq!(acd.blocks[0].role, "region");
    }

    #[test]
    fn test_acd_json_ld_faq() {
        let json = r#"{"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "Q1", "acceptedAnswer": {"@type": "Answer", "text": "A1"}}]}"#;
        let js_info = serde_json::json!({
            "framework_data": {},
            "resource_requests": [],
            "error_messages": []
        });
        let html = format!("<main><p>Content</p><script type=\"application/ld+json\">{}</script></main>", json);
        let acd = build_acd("https://a.com", "Test", &html, &[], &[], &[], &js_info, 100);
        assert!(acd.blocks.iter().any(|b| b.text.starts_with("Q: Q1")));
        assert_eq!(acd.framework.get("json_ld").is_some(), true);
    }

    #[test]
    fn test_query_acd() {
        let acd = Acd {
            url: "https://a.com".into(),
            blocks: vec![
                AcdBlock { id: "b1".into(), block_type: "text".into(), text: "Hello World".into(), ..Default::default() },
                AcdBlock { id: "b2".into(), block_type: "text".into(), text: "Foo Bar".into(), ..Default::default() },
            ],
            ..Default::default()
        };
        let res = query_acd(&acd, "world", 10);
        assert_eq!(res.len(), 1);
        assert_eq!(res[0].id, "b1");
    }

    #[test]
    fn test_slice_acd() {
        let acd = Acd {
            url: "https://a.com".into(),
            blocks: vec![
                AcdBlock { id: "b1".into(), ..Default::default() },
                AcdBlock { id: "b2".into(), ..Default::default() },
                AcdBlock { id: "b3".into(), ..Default::default() },
            ],
            ..Default::default()
        };
        let res = slice_acd(&acd, "b2", "b3", 10);
        assert!(res.is_ok());
        assert_eq!(res.unwrap().len(), 2);
        
        let res = slice_acd(&acd, "b99", "", 10);
        assert!(res.is_err());
    }

    #[test]
    fn test_acd_view() {
        let acd = Acd {
            url: "https://a.com".into(),
            version: "1".into(),
            blocks: vec![
                AcdBlock { id: "b1".into(), block_type: "heading".into(), text: "Title".into(), level: Some(1), ..Default::default() },
            ],
            counts: [("blocks_total".to_string(), 1i64)].iter().cloned().collect(),
            ..Default::default()
        };
        let view = acd_view(&acd, 10);
        assert!(view.contains("ACD v1"));
        assert!(view.contains("[b1] h1 Title"));
    }

    #[test]
    fn test_locator() {
        let html = "<div id=\"x\"><span>hi</span></div>";
        let dom = kuchiki::parse_html().one(html);
        let span = dom.select("span").unwrap().next().unwrap();
        assert_eq!(locator(span.as_node()), "#x > span");
    }

    #[test]
    fn test_acd_2_provenance_and_collections() {
        let html = r#"
        <html>
          <body>
            <main id="main">
              <article class="post">
                <h1>Balfour Case Analysis</h1>
                <p>A landmark English contract law decision on intention to create legal relations.</p>
              </article>
              <div id="results">
                <div class="card"><h3>Item 1</h3><p>Desc 1</p><a href="/1">1</a></div>
                <div class="card"><h3>Item 2</h3><p>Desc 2</p><a href="/2">2</a></div>
                <div class="card"><h3>Item 3</h3><p>Desc 3</p><a href="/3">3</a></div>
              </div>
            </main>
          </body>
        </html>
        "#;

        let acd = build_acd("https://law.example.com", "Law Title", html, &[], &[], &[], &serde_json::json!({}), 50);
        assert_eq!(acd.version, "2");
        assert!(!acd.blocks.is_empty());

        // Check first block provenance and classification
        let b0 = &acd.blocks[0];
        assert!(b0.provenance.is_some());
        let prov = b0.provenance.as_ref().unwrap();
        assert_eq!(prov.tag, "h1");
        assert!(prov.css_path.contains("main#main"));
        assert!(prov.xpath.contains("h1"));

        // Check semantic classification
        let p_block = acd.blocks.iter().find(|b| b.block_type == "text").unwrap();
        assert_eq!(p_block.semantic_category.as_deref(), Some("ArticleBody"));
        assert!(p_block.confidence.unwrap() > 0.5);

        // Check collections discovery
        assert!(!acd.collections.is_empty());
        let col = &acd.collections[0];
        assert_eq!(col.category, "SearchResults");
        assert_eq!(col.count, 3);
    }
}
