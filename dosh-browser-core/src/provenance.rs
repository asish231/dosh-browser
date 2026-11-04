//! `provenance.rs` — DOM Tag Provenance Engine for Advanced ACD 2.0.
//!
//! Generates deterministic CSS paths, XPaths, and extracts element attributes
//! for any DOM node so that extracted content blocks link directly to their source.

use kuchiki::NodeRef;
use std::collections::HashMap;
use crate::content::{tag_name, attr};
use crate::types::TagProvenance;

/// Compute a deterministic CSS path for a node up to `html` or an ID anchor.
/// e.g. "div#main > article.card:nth-of-type(2) > p:nth-of-type(1)"
pub fn compute_css_path(node: &NodeRef) -> String {
    let mut parts = Vec::new();
    let mut cur = Some(node.clone());

    while let Some(n) = cur {
        let tag = tag_name(&n);
        if tag.is_empty() || tag == "-document" {
            break;
        }

        // If node has an ID, use #id and stop walking up
        if let Some(id) = attr(&n, "id") {
            if !id.trim().is_empty() {
                parts.push(format!("{}#{}", tag, id.trim()));
                break;
            }
        }

        // Otherwise compute nth-of-type among siblings
        let mut nth = 1;
        let mut prev = n.previous_sibling();
        while let Some(p) = prev {
            if tag_name(&p) == tag {
                nth += 1;
            }
            prev = p.previous_sibling();
        }

        let mut part = tag.clone();
        if let Some(cls) = attr(&n, "class") {
            let first_cls = cls.split_whitespace().next();
            if let Some(c) = first_cls {
                if !c.contains(':') && !c.contains('[') {
                    part.push('.');
                    part.push_str(c);
                }
            }
        }

        if nth > 1 {
            part.push_str(&format!(":nth-of-type({})", nth));
        }

        parts.push(part);
        if tag == "html" {
            break;
        }
        cur = n.parent();
    }

    parts.reverse();
    parts.join(" > ")
}

/// Compute a standard 1-based XPath for a node.
/// e.g. "/html/body/div[1]/article[2]/p[1]"
pub fn compute_xpath(node: &NodeRef) -> String {
    let mut parts = Vec::new();
    let mut cur = Some(node.clone());

    while let Some(n) = cur {
        let tag = tag_name(&n);
        if tag.is_empty() || tag == "-document" {
            break;
        }

        let mut nth = 1;
        let mut prev = n.previous_sibling();
        while let Some(p) = prev {
            if tag_name(&p) == tag {
                nth += 1;
            }
            prev = p.previous_sibling();
        }

        let part = format!("{}[{}]", tag, nth);
        parts.push(part);
        if tag == "html" {
            break;
        }
        cur = n.parent();
    }

    parts.reverse();
    format!("/{}", parts.join("/"))
}

/// Extract all attributes for a node into a HashMap.
pub fn extract_attributes(node: &NodeRef) -> HashMap<String, String> {
    let mut map = HashMap::new();
    if let Some(elem) = node.as_element() {
        let attrs = elem.attributes.borrow();
        for (name, value) in attrs.map.iter() {
            map.insert(name.local.to_string(), value.value.to_string());
        }
    }
    map
}

/// Extract complete tag provenance for a DOM node.
pub fn extract_provenance(node: &NodeRef) -> TagProvenance {
    TagProvenance {
        tag: tag_name(node),
        css_path: compute_css_path(node),
        xpath: compute_xpath(node),
        attributes: extract_attributes(node),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    #[test]
    fn test_compute_css_path_and_xpath() {
        let html = r#"
        <html>
          <body>
            <div id="content">
              <article class="post">
                <h1>Title</h1>
                <p>First paragraph</p>
                <p>Second paragraph</p>
              </article>
            </div>
          </body>
        </html>
        "#;
        let doc = kuchiki::parse_html().one(html);
        let p2 = doc.select("p").unwrap().nth(1).unwrap();
        let node = p2.as_node();

        let css = compute_css_path(node);
        assert!(css.contains("div#content"));
        assert!(css.contains("p:nth-of-type(2)"));

        let xpath = compute_xpath(node);
        assert!(xpath.starts_with("/html[1]/body[1]"));
        assert!(xpath.contains("p[2]"));

        let prov = extract_provenance(node);
        assert_eq!(prov.tag, "p");
    }
}

