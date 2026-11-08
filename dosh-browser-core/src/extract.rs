use kuchiki::traits::TendrilSink;
use serde_json::Value;
use std::collections::HashMap;

use crate::content::text_content;
use crate::types::ExtractResult;

pub fn extract_fields(
    html: &str,
    url: &str,
    fields: &HashMap<String, String>,
) -> ExtractResult {
    let document = kuchiki::parse_html().one(html);
    let mut out: HashMap<String, Value> = HashMap::new();

    for (name, spec) in fields {
        let key = spec.trim().to_lowercase();
        let target = if key == "auto" {
            name.to_lowercase()
        } else {
            key
        };

        match target.as_str() {
            "title" => {
                let mut title = String::new();
                if let Ok(node) = document.select_first("title") {
                    title = text_content(node.as_node()).trim().to_string();
                }
                out.insert(name.clone(), Value::String(title));
            }
            "description" => {
                let mut desc = String::new();
                if let Ok(node) = document.select_first("meta[name='description']") {
                    if let Some(elem) = node.as_node().as_element() {
                        if let Some(c) = elem.attributes.borrow().get("content") {
                            desc = c.trim().to_string();
                        }
                    }
                }
                out.insert(name.clone(), Value::String(desc));
            }
            "url" => {
                out.insert(name.clone(), Value::String(url.to_string()));
            }
            "headings" => {
                let mut headings = Vec::new();
                if let Ok(nodes) = document.select("h1, h2, h3") {
                    for n in nodes {
                        let t = text_content(n.as_node()).trim().to_string();
                        if !t.is_empty() {
                            headings.push(Value::String(t));
                        }
                    }
                }
                out.insert(name.clone(), Value::Array(headings));
            }
            "text" => {
                let text = if let Ok(body) = document.select_first("body") {
                    text_content(body.as_node())
                } else {
                    text_content(&document)
                };
                let collapsed: String = text
                    .split_whitespace()
                    .collect::<Vec<&str>>()
                    .join(" ");
                let capped = if collapsed.chars().count() > 4000 {
                    collapsed.chars().take(4000).collect::<String>()
                } else {
                    collapsed
                };
                out.insert(name.clone(), Value::String(capped));
            }
            selector => {
                // CSS Selector query
                if let Ok(nodes) = document.select(selector) {
                    let mut vals = Vec::new();
                    for n in nodes {
                        let t = text_content(n.as_node()).trim().to_string();
                        if !t.is_empty() {
                            vals.push(t);
                        }
                    }
                    if vals.is_empty() {
                        out.insert(name.clone(), Value::Null);
                    } else if vals.len() == 1 {
                        out.insert(name.clone(), Value::String(vals[0].clone()));
                    } else {
                        out.insert(
                            name.clone(),
                            Value::Array(vals.into_iter().map(Value::String).collect()),
                        );
                    }
                } else {
                    out.insert(name.clone(), Value::Null);
                }
            }
        }
    }

    ExtractResult {
        url: url.to_string(),
        fields: out,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const SAMPLE_HTML: &str = r#"
        <!DOCTYPE html>
        <html>
        <head>
            <title>My Sample Page</title>
            <meta name="description" content="A demonstration page for data extraction." />
        </head>
        <body>
            <h1>Main Title</h1>
            <h2>Subtitle 1</h2>
            <h2>Subtitle 2</h2>
            <div class="price">$49.99</div>
            <ul class="items">
                <li class="item">Item A</li>
                <li class="item">Item B</li>
                <li class="item">Item C</li>
            </ul>
        </body>
        </html>
    "#;

    #[test]
    fn test_extract_builtins() {
        let mut fields = HashMap::new();
        fields.insert("page_title".to_string(), "title".to_string());
        fields.insert("page_desc".to_string(), "description".to_string());
        fields.insert("all_headings".to_string(), "headings".to_string());

        let res = extract_fields(SAMPLE_HTML, "https://example.com/demo", &fields);
        assert_eq!(res.fields["page_title"], json!("My Sample Page"));
        assert_eq!(res.fields["page_desc"], json!("A demonstration page for data extraction."));
        let headings = res.fields["all_headings"].as_array().unwrap();
        assert_eq!(headings.len(), 3);
        assert_eq!(headings[0], json!("Main Title"));
    }

    #[test]
    fn test_extract_selectors() {
        let mut fields = HashMap::new();
        fields.insert("price".to_string(), ".price".to_string());
        fields.insert("items".to_string(), ".item".to_string());
        fields.insert("missing".to_string(), ".nonexistent".to_string());

        let res = extract_fields(SAMPLE_HTML, "https://example.com/demo", &fields);
        assert_eq!(res.fields["price"], json!("$49.99"));
        let items = res.fields["items"].as_array().unwrap();
        assert_eq!(items.len(), 3);
        assert_eq!(items[0], json!("Item A"));
        assert_eq!(res.fields["missing"], Value::Null);
    }
}
