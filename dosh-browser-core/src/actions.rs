//! `actions.rs` — The agent action surface (replaces `webresearch/actions.py`).
//!
//! Enumerates the *whole* surface, assigns stable refs that survive ordering,
//! and reports exactly what was omitted when a budget forces a cut.

use kuchiki::NodeRef;
use kuchiki::traits::TendrilSink;
use sha2::{Digest, Sha256};
use std::collections::HashMap;

use crate::content::{attr, class_tokens, tag_name, text_content};
use crate::types::Target;

// --- Constants from actions.py ---
#[allow(dead_code)]
const ACTIVE_TAGS: &[&str] = &[
    "button", "input", "textarea", "select", "label", "summary",
];
#[allow(dead_code)]
const EMBED_TAGS: &[&str] = &[
    "iframe", "canvas", "video", "audio", "object", "embed",
];
const NAV_TAGS: &[&str] = &["a", "area"];

const HANDLE_TOKENS: &[&str] = &[
    "slider", "handle", "knob", "grip", "lever", "thumb", "carriage", "cursor",
];
const TARGET_TOKENS: &[&str] = &[
    "slidertarget", "dragtarget", "droptarget", "thumbtarget",
];

// Generic class names that describe plumbing, not purpose.
const MACHINERY_TOKENS: &[&str] = &[
    "active", "disabled", "hidden", "visible", "wrapper", "container",
    "inner", "outer", "content", "item", "list", "row", "col", "cell",
    "block", "element", "component", "module", "root", "base", "main",
    "wrap", "box", "group", "parent", "child",
];

fn _stable_hash(node: &NodeRef) -> String {
    let tag = tag_name(node);
    let href = attr(node, "href").unwrap_or_default();
    let name = attr(node, "name").unwrap_or_default();
    let text = text_content(node).chars().take(80).collect::<String>();
    let id_attr = attr(node, "id").unwrap_or_default();
    let class_attr = attr(node, "class").unwrap_or_default();
    
    let mut hasher = Sha256::new();
    hasher.update(tag.as_bytes());
    hasher.update(href.as_bytes());
    hasher.update(name.as_bytes());
    hasher.update(text.as_bytes());
    hasher.update(id_attr.as_bytes());
    hasher.update(class_attr.as_bytes());
    format!("{:x}", hasher.finalize())[..8].to_string()
}

/// Escape a value for use inside a CSS identifier (mirrors actions.py
/// `_escape_ident`). Tailwind emits class names containing ':' and '['
/// ('lg:tw-flex', 'tw-max-w-300'); unescaped, they produce selectors the
/// engine rejects.
fn _escape_ident(value: &str) -> String {
    value
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c.to_string()
            } else {
                format!("\\{c}")
            }
        })
        .collect()
}

/// Approximate selector usable against the engine (mirrors actions.py
/// `_css_path`). Walks up to six ancestors, stops at the nearest id (a unique
/// id makes every ancestor step unnecessary), escapes class tokens, keeps at
/// most two classes per element (utility frameworks reorder class lists on
/// every build), and drops the html/body tail. Repeated identical controls
/// share one selector; the action lane disambiguates them with the
/// document-order occurrence (`ref~N`).
fn _css_path(node: &NodeRef) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = Some(node.clone());
    let mut hops = 0;
    while let Some(cur) = cursor {
        if hops >= 6 {
            break;
        }
        let tag = tag_name(&cur);
        if tag.is_empty() {
            // Synthetic document root: including it emits a leading `*`
            // segment that can never match (the html element's parent is
            // the document, not an element), defeating the whole chain.
            break;
        }
        if let Some(node_id) = attr(&cur, "id") {
            if !node_id.is_empty() {
                parts.push(format!("#{}", _escape_ident(&node_id)));
                break;
            }
        }
        let classes: Vec<String> = attr(&cur, "class")
            .unwrap_or_default()
            .split_whitespace()
            .filter(|c| !c.is_empty())
            .take(2)
            .map(|c| format!(".{}", _escape_ident(c)))
            .collect();
        if classes.is_empty() {
            parts.push(if tag.is_empty() { "*".to_string() } else { tag });
        } else {
            parts.push(format!("{}{}", if tag.is_empty() { "*" } else { &tag }, classes.join("")));
        }
        cursor = cur.parent();
        hops += 1;
    }
    // Drop ancestors the engine cannot help with: 'html' and 'body' are in
    // every document, so keeping them only lengthens the selector.
    while let Some(last) = parts.last() {
        let base = last.split('.').next().unwrap_or("");
        if base == "html" || base == "body" {
            parts.pop();
        } else {
            break;
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.iter().rev().cloned().collect::<Vec<_>>().join(" > ")
}

fn _label_for(node: &NodeRef) -> String {
    // Priority: Visible Text -> aria-label -> title -> alt -> placeholder -> name/value -> id
    let text = text_content(node).chars().take(80).collect::<String>();
    if !text.is_empty() && !is_machinery(&text) {
        return text;
    }
    if let Some(v) = attr(node, "aria-label") {
        if !v.is_empty() { return v; }
    }
    if let Some(v) = attr(node, "title") {
        if !v.is_empty() { return v; }
    }
    if let Some(v) = attr(node, "alt") {
        if !v.is_empty() { return v; }
    }
    if let Some(v) = attr(node, "placeholder") {
        if !v.is_empty() { return v; }
    }
    if let Some(v) = attr(node, "name") {
        if !v.is_empty() { return v; }
    }
    if let Some(v) = attr(node, "value") {
        if !v.is_empty() { return v; }
    }
    attr(node, "id").unwrap_or_default()
}

fn is_machinery(text: &str) -> bool {
    let lower = text.to_lowercase();
    MACHINERY_TOKENS.iter().any(|t| lower.contains(t))
}

/// The element is hidden by its own markup or CSS without an engine cascade:
/// inline display:none / visibility:hidden, [hidden] attribute on element or ancestor,
/// closed <dialog>, closed [popover], or closed <details>.
pub fn is_inline_hidden(node: &NodeRef) -> bool {
    // 1. Check aria-hidden on node
    if attr(node, "aria-hidden").as_deref() == Some("true") {
        return true;
    }

    // 2. Walk self and ancestors for style / hidden attributes
    let mut cur = Some(node.clone());
    while let Some(p) = cur {
        if let Some(style_val) = attr(&p, "style") {
            let s = style_val.replace(" ", "").to_lowercase();
            if s.contains("display:none")
                || s.contains("visibility:hidden")
                || s.contains("visibility:collapse")
                || s.contains("opacity:0")
                || s.contains("left:-9999px")
                || s.contains("position:absolute;left:-")
            {
                return true;
            }
        }
        if attr(&p, "hidden").is_some() {
            return true;
        }
        let tag = tag_name(&p);
        if tag == "dialog" && attr(&p, "open").is_none() {
            return true;
        }
        if attr(&p, "popover").is_some() && attr(&p, "open").is_none() {
            return true;
        }
        cur = p.parent();
    }
    false
}

pub fn extract_actions(html: &str, _base_url: &str, max_actions: usize) -> Vec<Target> {
    let dom = kuchiki::parse_html().one(html);
    let root = dom.clone();
    
    let mut targets: Vec<Target> = Vec::new();
    
    // We need to select them all.
    let selector = "a, area, button, input, textarea, select, label, summary, iframe, canvas, video, audio, object, embed, [role='button'], [role='link'], [role='slider']";
    let mut interactive_nodes: Vec<NodeRef> = Vec::new();
    if let Ok(sel) = root.select(selector) {
        for node in sel {
            interactive_nodes.push(node.as_node().clone());
        }
    }

    let mut ref_counts: HashMap<String, i32> = HashMap::new();
    let mut seen_ptrs: std::collections::HashSet<usize> = std::collections::HashSet::new();

    for node in &interactive_nodes {
        seen_ptrs.insert(&**node as *const _ as usize);
        let tag = tag_name(node);
        let input_type = attr(node, "type").unwrap_or("text".to_string()).to_lowercase();
        // Filter out hidden/disabled, invisible CSS honeypots, and hidden inputs
        if tag == "input" && input_type == "hidden" { continue; }
        if attr(node, "disabled").is_some() { continue; }
        if is_inline_hidden(node) { continue; }
        
        let href = attr(node, "href").unwrap_or_default();
        let is_nav = NAV_TAGS.contains(&tag.as_str()) && !href.is_empty();
        
        let form_action = get_parent_form_action(node);
        let input_type = attr(node, "type").unwrap_or("text".to_string()).to_lowercase();
        let is_submit = (tag == "input" && (input_type == "submit" || input_type == "image"))
                     || (tag == "button" && (input_type == "submit" || input_type.is_empty()))
                     || (form_action.is_some() && tag == "button"); // Only buttons in forms are submits usually
        
        // Check for sliders
        let tokens = class_tokens(node);
        let is_dragger = tokens.iter().any(|t| {
            if t == "cursor" {
                let raw_cls = attr(node, "class").unwrap_or_default().to_lowercase();
                let raw_style = attr(node, "style").unwrap_or_default().to_lowercase();
                !raw_cls.contains("cursor-") && !raw_style.contains("cursor:")
            } else {
                HANDLE_TOKENS.contains(&t.as_str())
            }
        });
        let is_target = tokens.iter().any(|t| TARGET_TOKENS.contains(&t.as_str()));

        let mut kind = "activate".to_string();
        if is_nav { kind = "navigate".to_string(); }
        else if is_submit && form_action.is_some() { kind = "submit".to_string(); }
        else if is_dragger { kind = "drag".to_string(); }
        else if tag == "iframe" || tag == "canvas" || tag == "video" { kind = "embed".to_string(); }
        else if tag == "audio" { kind = "activate".to_string(); }
        
        if is_target {
            kind = "dropzone".to_string(); 
        }

        let label = _label_for(node);
        let role = get_role(node, &tag, &kind);
        let ref_base = _stable_hash(node);
        let count = ref_counts.entry(ref_base.clone()).or_insert(0);
        *count += 1;
        let occurrence = *count;
        
        let ref_id = if occurrence > 1 {
            format!("{}~{}", ref_base, occurrence)
        } else {
            ref_base.clone()
        };

        if targets.len() >= max_actions { continue; }

        targets.push(Target {
            ref_id,
            role,
            text: if !label.is_empty() { label } else { text_content(node).chars().take(80).collect() },
            tag: tag.clone(),
            name: attr(node, "name").unwrap_or_default(),
            href,
            checked: if tag == "input" {
                let t = attr(node, "type").unwrap_or_default();
                if t == "checkbox" || t == "radio" {
                    Some(attr(node, "checked").is_some())
                } else { None }
            } else { None },
            challenge: false,
            path: Vec::new(),
            selector: _css_path(node),
        });
    }

    // --- Neural pass: recover unsemantic interactive elements ---
    // (React 18/19 synthetic clicks, Vue _vei, Angular/Svelte, Tailwind buttons)
    let neural_selector = "[data-dosh-interact], [data-action], [data-click], div, span, li, p, svg, i, tr";
    let mut candidate_nodes: Vec<NodeRef> = Vec::new();
    if let Ok(sel) = root.select(neural_selector) {
        for node in sel {
            candidate_nodes.push(node.as_node().clone());
        }
    }

    for node in &candidate_nodes {
        let ptr = &**node as *const _ as usize;
        if seen_ptrs.contains(&ptr) {
            continue;
        }
        if is_inline_hidden(node) {
            continue;
        }

        let raw_flags = attr(node, "data-dosh-interact");
        let flags: u32 = raw_flags.as_deref().and_then(|v| v.parse().ok()).unwrap_or(0);

        let has_role = attr(node, "role").is_some();
        let has_act = attr(node, "data-action").is_some() || attr(node, "data-click").is_some();
        let has_href = attr(node, "data-href").is_some() || attr(node, "href").is_some() || attr(node, "to").is_some();
        let tokens = class_tokens(node);
        let has_interactive_class = tokens.iter().any(|t| {
            let lt = t.to_lowercase();
            lt.contains("btn")
                || lt.contains("button")
                || lt.contains("cta")
                || lt.contains("press")
                || lt.contains("cursor-pointer")
                || lt.contains("clickable")
                || lt.contains("handle")
                || lt.contains("thumb")
                || lt.contains("link")
                || lt.contains("search")
                || lt.contains("select")
                || lt.contains("switch")
                || lt.contains("toggle")
        });

        if flags == 0 && !has_role && !has_act && !has_href && !has_interactive_class {
            continue;
        }

        let feats = crate::neural::extract_interactivity_features(node, flags);
        let pred = crate::neural::predict_interactivity(&feats);

        if pred.class != crate::neural::ActionRoleClass::NonInteractive && pred.confidence >= 0.50 {
            // Tree domination: do not create child targets if ancestor was already added
            let mut dominated = false;
            let mut p = node.parent();
            while let Some(parent) = p {
                let p_ptr = &*parent as *const _ as usize;
                if seen_ptrs.contains(&p_ptr) {
                    dominated = true;
                    break;
                }
                p = parent.parent();
            }
            if dominated {
                continue;
            }

            seen_ptrs.insert(ptr);

            let tag = tag_name(node);
            let href = attr(node, "href").or_else(|| attr(node, "data-href")).unwrap_or_default();
            let role = match pred.class {
                crate::neural::ActionRoleClass::Button => "button".to_string(),
                crate::neural::ActionRoleClass::Input => "textbox".to_string(),
                crate::neural::ActionRoleClass::Link => "link".to_string(),
                crate::neural::ActionRoleClass::Slider => "slider".to_string(),
                crate::neural::ActionRoleClass::NonInteractive => unreachable!(),
            };

            let label = _label_for(node);
            let ref_base = _stable_hash(node);
            let count = ref_counts.entry(ref_base.clone()).or_insert(0);
            *count += 1;
            let occurrence = *count;

            let ref_id = if occurrence > 1 {
                format!("{}~{}", ref_base, occurrence)
            } else {
                ref_base.clone()
            };

            if targets.len() >= max_actions {
                continue;
            }

            let selector = _css_path(node);
            targets.push(Target {
                ref_id,
                role,
                text: if !label.is_empty() { label } else { text_content(node).chars().take(80).collect() },
                tag,
                name: attr(node, "name").unwrap_or_default(),
                href,
                checked: None,
                challenge: false,
                path: Vec::new(),
                selector,
            });
        }
    }

    targets
}

fn get_parent_form_action(node: &NodeRef) -> Option<(String, String)> {
    let mut current = Some(node.clone());
    while let Some(n) = current {
        if tag_name(&n) == "form" {
            if let Some(elem) = n.as_element() {
                let attrs = elem.attributes.borrow();
                let mut action = String::new();
                let mut method = String::new();
                for (k, v) in attrs.map.iter() {
                    if k.local.as_ref() == "action" {
                        action = v.value.clone();
                    } else if k.local.as_ref() == "method" {
                        method = v.value.to_uppercase();
                    }
                }
                if method.is_empty() { method = "GET".to_string(); }
                return Some((action, method));
            }
        }
        current = n.parent();
    }
    None
}

fn get_role(node: &NodeRef, tag: &str, _kind: &str) -> String {
    if let Some(r) = attr(node, "role") {
        return r;
    }
    match tag {
        "a" => "link".to_string(),
        "button" => "button".to_string(),
        "input" => {
            match attr(node, "type").unwrap_or("text".to_string()).as_str() {
                "checkbox" => "checkbox",
                "radio" => "radio",
                "submit" => "button",
                "button" => "button",
                "file" => "file",
                "password" => "password",
                _ => "textbox",
            }
        }.to_string(),
        "select" => "select".to_string(),
        "textarea" => "textbox".to_string(),
        "label" => "label".to_string(),
        "summary" => "button".to_string(),
        "iframe" => "iframe".to_string(),
        _ => "button".to_string(), 
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_link() {
        let html = r#"<a href="https://google.com">Search</a>"#;
        let targets = extract_actions(html, "https://base.com", 10);
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].role, "link");
        assert_eq!(targets[0].href, "https://google.com");
    }

    #[test]
    fn test_extract_button() {
        let html = r#"<button id="btn">Click Me</button>"#;
        let targets = extract_actions(html, "https://base.com", 10);
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].role, "button");
        assert_eq!(targets[0].tag, "button");
    }

    #[test]
    fn test_extract_form_input() {
        let html = r#"<form action="/sub"><input type="text" name="q"><button type="submit">Go</button></form>"#;
        let targets = extract_actions(html, "https://base.com", 10);
        // Should have 2 targets: input and button
        assert_eq!(targets.len(), 2);
        // Input should be textbox
        assert_eq!(targets[0].role, "textbox");
        assert_eq!(targets[0].name, "q");
        // Button should be button
        assert_eq!(targets[1].role, "button");
        // Button text might be in 'text' or we just check the tag/name
        // (Text extraction can be tricky with whitespace)
        assert_eq!(targets[1].tag, "button");
    }

    #[test]
    fn test_extract_slider() {
        let html = r#"<div class="slider"><div class="handle" role="slider">Drag</div></div>"#;
        let targets = extract_actions(html, "https://base.com", 10);
        // The handle should be a target with role=slider
        let handles: Vec<_> = targets.iter().filter(|t| t.role == "slider").collect();
        assert_eq!(handles.len(), 1);
    }

    #[test]
    fn test_skip_css_hidden_and_honeypot() {
        let html = r#"
            <div>
                <button style="display: none">Hidden Button</button>
                <input type="text" style="visibility: hidden" name="bot_trap">
                <input type="hidden" name="csrf" value="123">
                <div style="opacity: 0"><a href="/trap">Invisible Trap</a></div>
                <button aria-hidden="true">Screen Reader Only</button>
                <button id="real">Visible Button</button>
            </div>
        "#;
        let targets = extract_actions(html, "https://base.com", 10);
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].ref_id.len(), 8);
        assert_eq!(targets[0].role, "button");
    }

    #[test]
    fn test_neural_react_synthetic_button() {
        let html = r#"
            <div>
                <h1>Header</h1>
                <div data-dosh-interact="64" class="px-4 py-2 bg-blue-600 rounded text-white font-medium">
                    Proceed to Checkout
                </div>
            </div>
        "#;
        let targets = extract_actions(html, "https://store.example.com", 10);
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].role, "button");
        assert_eq!(targets[0].tag, "div");
        assert!(targets[0].text.contains("Proceed to Checkout"));
    }

    #[test]
    fn test_neural_vue_dominated_hierarchy() {
        let html = r#"
            <div data-dosh-interact="128" class="btn btn-primary">
                <span class="btn-text">Save Changes</span>
                <svg class="icon" viewBox="0 0 24 24"></svg>
            </div>
        "#;
        let targets = extract_actions(html, "https://app.example.com", 10);
        // Tree domination: only outer div emitted, inner span/svg dominated
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].role, "button");
        assert!(targets[0].text.contains("Save Changes"));
    }

    #[test]
    fn test_neural_unsemantic_link() {
        let html = r#"
            <div data-dosh-interact="1" data-href="/dashboard" class="nav-item cursor-pointer">
                User Dashboard
            </div>
        "#;
        let targets = extract_actions(html, "https://app.example.com", 10);
        assert_eq!(targets.len(), 1);
        assert_eq!(targets[0].role, "link");
        assert_eq!(targets[0].href, "/dashboard");
    }

    #[test]
    fn test_aktu_login_page() {
        let html = r#"
        <!DOCTYPE html>
        <html xmlns="http://www.w3.org/1999/xhtml">
        <head><title>AKTU Login</title></head>
        <body>
            <form method="post" action="./login.aspx" id="form1">
                <input name="txtUserId" type="text" id="txtUserId" class="input100" PlaceHolder="User ID" />
                <input type="hidden" name="hdnUserId" id="hdnUserId" />
                <input name="txtPassword" type="password" id="txtPassword" class="input100" PlaceHolder="Password" />
                <input type="hidden" name="hdnPassword" id="hdnPassword" />
                <input type="submit" name="btnLogin" value="Login" id="btnLogin" title="Click here to login" class="login100-form-btn" />
            </form>
            <a href="forgot.aspx" title="Forgot password">Forgot</a>
        </body>
        </html>
        "#;
        let targets = extract_actions(html, "https://erp.aktu.ac.in/login.aspx", 100);
        // Debug: print all targets found
        for t in &targets {
            eprintln!("TARGET: ref={} tag={} role={} kind=? name={} text={}", 
                t.ref_id, t.tag, t.role, t.name, t.text.chars().take(40).collect::<String>());
        }
        assert_eq!(targets.len(), 4, "expected exactly 4 targets (textbox, password, button, link), got {} total", targets.len());
        
        let textboxes: Vec<_> = targets.iter().filter(|t| t.role == "textbox").collect();
        assert_eq!(textboxes.len(), 1);
        assert_eq!(textboxes[0].name, "txtUserId");
        
        let passwords: Vec<_> = targets.iter().filter(|t| t.role == "password").collect();
        assert_eq!(passwords.len(), 1);
        assert_eq!(passwords[0].name, "txtPassword");
        
        let buttons: Vec<_> = targets.iter().filter(|t| t.role == "button").collect();
        assert_eq!(buttons.len(), 1);
        assert_eq!(buttons[0].name, "btnLogin");
        
        let links: Vec<_> = targets.iter().filter(|t| t.role == "link").collect();
        assert_eq!(links.len(), 1);
    }
}
