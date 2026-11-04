//! High-level page reading, snapshotting, and CAPTCHA awareness.
//! Direct port of `webresearch/read.py`.

use crate::acd::build_acd;
use crate::fetch::fetch;
use crate::types::{Page, Challenge};
use crate::captcha::inspect_captcha;
use crate::actions::extract_actions;
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JsMode {
    Auto,
    Always,
    Never,
}

impl JsMode {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "always" => JsMode::Always,
            "never" => JsMode::Never,
            _ => JsMode::Auto,
        }
    }
}

/// Reads a URL and compiles it into an LLM-ready Page representation.
pub async fn read(
    url: &str,
    max_chars: usize,
    js_mode: JsMode,
) -> Result<Page, String> {
    let client = reqwest::Client::new();
    let fetch_res = fetch(&client, url, "GET", None, "{}", 8_000_000)
        .await
        .map_err(|e| e.to_string())?;

    let raw_html = fetch_res.body.clone();
    let final_url = fetch_res.final_url.clone();

    // Run scripts if requested and if runtime is enabled
    let (final_html, _js_ran) = if js_mode == JsMode::Never {
        (raw_html, false)
    } else {
        match crate::runtime::Runtime::new(client.clone(), &final_url) {
            Ok(mut rt) => match rt.run_scripts(&raw_html, &final_url) {
                Ok((mutated, _info)) => (mutated, true),
                Err(_) => (raw_html, false),
            },
            Err(_) => (raw_html, false),
        }
    };

    let targets = extract_actions(&final_html, &final_url, 100);
    let links = crate::links::links(&final_html, &final_url, 50);
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

    let title = crate::session::extract_title(&final_html);
    let acd_doc = build_acd(
        &final_url,
        &title,
        &final_html,
        &targets,
        &links,
        &frame_infos,
        &serde_json::json!({}),
        max_chars / 80 + 10,
    );

    let captcha_inspection = inspect_captcha(&final_html, &final_url, &[]);

    let mut view_lines = Vec::new();
    view_lines.push(format!("TITLE: {}", acd_doc.title));
    view_lines.push(format!("URL: {}", acd_doc.url));
    view_lines.push(format!("STATUS: {}", fetch_res.status));
    view_lines.push(String::new());

    for block in &acd_doc.blocks {
        if block.block_type == "heading" {
            let level = block.level.unwrap_or(1);
            let hashes = "#".repeat(level as usize);
            view_lines.push(format!("{} {}", hashes, block.text));
        } else if block.block_type == "table" {
            for row in &block.rows {
                view_lines.push(format!("| {} |", row.join(" | ")));
            }
        } else {
            view_lines.push(block.text.clone());
        }
    }

    if !links.is_empty() {
        view_lines.push(String::new());
        view_lines.push("Links:".to_string());
        for link in links.iter().take(20) {
            view_lines.push(format!("[{}] {} → {}", link.n, link.text, link.url));
        }
    }

    view_lines.push(String::new());
    view_lines.push("ENGINE: doshgentv8".to_string());
    if _js_ran {
        view_lines.push("JS: ran in V8 core, errors=0".to_string());
    }
    if !targets.is_empty() {
        view_lines.push("Inspect (LLM: click/type these refs):".to_string());
        for t in targets.iter().take(40) {
            let label = if !t.text.is_empty() {
                &t.text
            } else if !t.name.is_empty() {
                &t.name
            } else if !t.href.is_empty() {
                &t.href
            } else {
                &t.tag
            };
            view_lines.push(format!("[{}] {} {}", t.ref_id, t.role, label));
        }
    }
    view_lines.push(format!("ACD: {} blocks, {} actions, 0 api calls", acd_doc.blocks.len(), targets.len()));

    let mut full_view = view_lines.join("\n\n");
    if full_view.len() > max_chars {
        full_view.truncate(max_chars);
        full_view.push_str("\n... [truncated]");
    }

    let mut challenges = Vec::new();
    if captcha_inspection.present {
        challenges.push(Challenge {
            kind: captcha_inspection.kind.clone(),
            passable: "true".to_string(),
            refs: vec![],
            detail: captcha_inspection.prompt.clone(),
        });
    }

    Ok(Page {
        url: final_url.clone(),
        final_url,
        status: fetch_res.status as i64,
        content_type: "text/html".to_string(),
        kind: "html".to_string(),
        title: acd_doc.title.clone(),
        view: full_view,
        links,
        forms: vec![],
        headings: vec![],
        meta: crate::types::Meta::default(),
        spa_hint: false,
        js_ran: _js_ran,
        targets,
        challenges,
        frames: frame_infos,
        captcha: None,
        acd: Some(acd_doc),
        engine: "doshgentv8".to_string(),
        chars: 0,
        headers: HashMap::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_js_mode_parsing() {
        assert_eq!(JsMode::from_str("auto"), JsMode::Auto);
        assert_eq!(JsMode::from_str("ALWAYS"), JsMode::Always);
        assert_eq!(JsMode::from_str("Never"), JsMode::Never);
        assert_eq!(JsMode::from_str("unknown"), JsMode::Auto);
    }
}
