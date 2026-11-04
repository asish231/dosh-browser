//! `forms.rs` — Form field extraction (replaces `webresearch/forms.py`).
//!
//! Extracts forms and their fields from the DOM, mapping them to the
//! `types::Form` and `types::FieldSpec` structures.

use kuchiki::traits::TendrilSink;

use crate::content::{attr, tag_name, text_content};
use crate::types::{FieldSpec, Form};

const INPUT_TYPES_TO_SKIP: &[&str] = &["hidden", "submit", "button", "reset", "image"];

/// Extract all forms from the DOM.
pub fn extract_forms(html: &str) -> Vec<Form> {
    let dom = kuchiki::parse_html().one(html);
    let mut forms = Vec::new();
    let mut n = 0;

    if let Ok(form_nodes) = dom.select("form") {
        for form_node in form_nodes {
            let form_elem = form_node.as_node().as_element().unwrap();
            let attrs = form_elem.attributes.borrow();
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

            let mut fields = Vec::new();
            // Collect inputs, selects, textareas
            let mut field_nodes = Vec::new();
            
            // We need to iterate over the form's descendants.
            // In Rust/Kuchiki, we can select relative to the node.
            for sel in &["input", "select", "textarea"] {
                if let Ok(nodes) = form_node.as_node().select(sel) {
                    for node in nodes {
                        field_nodes.push(node.as_node().clone());
                    }
                }
            }

            // Sort by document order? Kuchiki iterators usually return in order,
            // but `select` calls for different tags might mix.
            // For now, we just process them. Python's `forms.py` relies on 
            // the tree traversal order. Since we iterate specific selectors, 
            // we should ideally traverse once.
            // Python: `for field in form.css('input, select, textarea, button'):`
            
            // Let's re-do this with a single selector to match Python's order.
            field_nodes.clear();
            if let Ok(nodes) = form_node.as_node().select("input, select, textarea, button") {
                for node in nodes {
                    field_nodes.push(node.as_node().clone());
                }
            }

            for node in &field_nodes {
                let tag = tag_name(node);
                let input_type = attr(node, "type").unwrap_or_else(|| match tag.as_str() {
                    "input" => "text".to_string(), // Default input type
                    "button" => "button".to_string(),
                    _ => "".to_string(),
                }).to_lowercase();

                // Skip hidden/submit buttons as *fields*
                if tag == "input" && INPUT_TYPES_TO_SKIP.contains(&input_type.as_str()) {
                    continue;
                }

                let name = attr(node, "name").unwrap_or_default();
                if name.is_empty() { continue; }

                // Determine field type and options
                let mut field_type = "text".to_string();
                let mut options: Vec<String> = Vec::new();
                let mut value = String::new();
                let mut required = false;

                if tag == "select" {
                    field_type = "select".to_string();
                    if let Ok(opts) = node.select("option") {
                        for opt in opts {
                            let opt_node = opt.as_node();
                            let opt_val = attr(opt_node, "value").unwrap_or_else(|| {
                                // If no value, use text
                                text_content(opt_node)
                            });
                            options.push(opt_val);
                        }
                    }
                } else if tag == "textarea" {
                    field_type = "textarea".to_string();
                    value = text_content(node);
                } else if tag == "input" {
                    field_type = match input_type.as_str() {
                        "checkbox" => "checkbox",
                        "radio" => "radio",
                        "number" => "number",
                        "email" => "email",
                        "tel" => "tel",
                        "url" => "url",
                        "password" => "password",
                        "date" => "date",
                        "time" => "time",
                        "file" => "file",
                        _ => "text",
                    }.to_string();
                    value = attr(node, "value").unwrap_or_default();
                } else if tag == "button" {
                    // Buttons are usually actions, but if they have a name, they are fields too
                    value = attr(node, "value").unwrap_or_else(|| text_content(node));
                }

                if attr(node, "required").is_some() {
                    required = true;
                }

                fields.push(FieldSpec {
                    name,
                    field_type,
                    value,
                    required,
                    options,
                });
            }

            if !fields.is_empty() {
                n += 1;
                forms.push(Form {
                    n,
                    method,
                    action,
                    fields,
                });
            }
        }
    }
    forms
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_simple_form() {
        let html = r#"<form action="/login" method="POST">
            <input type="text" name="user">
            <input type="password" name="pass">
            <button type="submit">Login</button>
        </form>"#;
        let forms = extract_forms(html);
        assert_eq!(forms.len(), 1);
        assert_eq!(forms[0].action, "/login");
        assert_eq!(forms[0].method, "POST");
        // Button has no name, so it's not a field. Only 'user' and 'pass'.
        assert_eq!(forms[0].fields.len(), 2);
        assert_eq!(forms[0].fields[0].name, "user");
        assert_eq!(forms[0].fields[1].name, "pass");
    }

    #[test]
    fn test_extract_select() {
        // Test standard HTML behavior: value attribute takes precedence
        let html = r#"<form><select name="color">
            <option value="r">Red</option>
            <option value="g">Green</option>
        </select></form>"#;
        let forms = extract_forms(html);
        assert_eq!(forms.len(), 1);
        let f = &forms[0];
        assert_eq!(f.fields.len(), 1);
        assert_eq!(f.fields[0].name, "color");
        assert_eq!(f.fields[0].field_type, "select");
        assert_eq!(f.fields[0].options, vec!["r", "g"]);
        
        // Test fallback to text when value is missing
        let html2 = r#"<form><select name="color">
            <option>Red</option>
            <option>Green</option>
        </select></form>"#;
        let forms2 = extract_forms(html2);
        assert_eq!(forms2[0].fields[0].options, vec!["Red", "Green"]);
    }

    #[test]
    fn test_skip_hidden() {
        let html = r#"<form>
            <input type="hidden" name="csrf" value="abc">
            <input type="text" name="q">
        </form>"#;
        let forms = extract_forms(html);
        assert_eq!(forms.len(), 1);
        // Should only contain 'q'
        assert_eq!(forms[0].fields.len(), 1);
        assert_eq!(forms[0].fields[0].name, "q");
    }
}
