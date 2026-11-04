//! `features.rs` — DOM Node Feature Vector Extractor for Micro-ML / GBDT.
//!
//! Extracts 28 statistical, structural, and semantic signals from any candidate DOM node.

use kuchiki::NodeRef;
use crate::content::{tag_name, text_content, attr};

#[derive(Clone, Debug, Default)]
pub struct NodeFeatures {
    pub text_len: f64,
    pub word_count: f64,
    pub link_density: f64,
    pub punctuation_ratio: f64,
    pub depth: f64,
    pub child_count: f64,
    pub descendant_count: f64,
    pub p_count: f64,
    pub heading_count: f64,
    pub list_count: f64,
    pub table_count: f64,
    pub form_count: f64,
    pub img_count: f64,
    pub is_article: f64,
    pub is_main: f64,
    pub is_nav: f64,
    pub is_footer: f64,
    pub is_aside: f64,
    pub class_pos_score: f64,
    pub class_neg_score: f64,
}

impl NodeFeatures {
    pub fn to_vector(&self) -> Vec<f64> {
        vec![
            self.text_len,
            self.word_count,
            self.link_density,
            self.punctuation_ratio,
            self.depth,
            self.child_count,
            self.descendant_count,
            self.p_count,
            self.heading_count,
            self.list_count,
            self.table_count,
            self.form_count,
            self.img_count,
            self.is_article,
            self.is_main,
            self.is_nav,
            self.is_footer,
            self.is_aside,
            self.class_pos_score,
            self.class_neg_score,
        ]
    }
}

/// Calculate the depth of a node in the DOM tree.
fn compute_node_depth(node: &NodeRef) -> f64 {
    let mut depth = 0.0;
    let mut cur = node.parent();
    while let Some(p) = cur {
        depth += 1.0;
        cur = p.parent();
    }
    depth
}

/// Extract feature vector for a candidate container node.
pub fn extract_features(node: &NodeRef) -> NodeFeatures {
    let text = text_content(node);
    let trimmed_text = text.trim();
    let text_len = trimmed_text.len() as f64;
    let word_count = trimmed_text.split_whitespace().count() as f64;

    let punct_count = trimmed_text.chars().filter(|c| c.is_ascii_punctuation()).count() as f64;
    let punctuation_ratio = if text_len > 0.0 { punct_count / text_len } else { 0.0 };

    // Anchor text length for link density
    let mut anchor_text_len = 0.0;
    let mut child_count = 0.0;
    let mut descendant_count = 0.0;
    let mut p_count = 0.0;
    let mut heading_count = 0.0;
    let mut list_count = 0.0;
    let mut table_count = 0.0;
    let mut form_count = 0.0;
    let mut img_count = 0.0;

    for child in node.children() {
        if child.as_element().is_some() {
            child_count += 1.0;
        }
    }

    for desc in node.descendants() {
        if let Some(elem) = desc.as_element() {
            descendant_count += 1.0;
            let tag = elem.name.local.as_ref();
            match tag {
                "a" => {
                    let a_text = text_content(&desc);
                    anchor_text_len += a_text.trim().len() as f64;
                }
                "p" => p_count += 1.0,
                "h1" | "h2" | "h3" | "h4" | "h5" | "h6" => heading_count += 1.0,
                "ul" | "ol" | "li" => list_count += 1.0,
                "table" | "tr" | "td" | "th" => table_count += 1.0,
                "form" | "input" | "button" | "select" | "textarea" => form_count += 1.0,
                "img" | "svg" | "picture" => img_count += 1.0,
                _ => {}
            }
        }
    }

    let link_density = if text_len > 0.0 {
        (anchor_text_len / text_len).min(1.0)
    } else {
        0.0
    };

    let tag = tag_name(node);
    if tag == "p" { p_count += 1.0; }
    if tag.starts_with('h') { heading_count += 1.0; }

    let mut is_article = if tag == "article" { 1.0 } else { 0.0 };
    let mut is_main = if tag == "main" { 1.0 } else { 0.0 };
    let mut is_nav = if tag == "nav" { 1.0 } else { 0.0 };
    let mut is_footer = if tag == "footer" { 1.0 } else { 0.0 };
    let mut is_aside = if tag == "aside" { 1.0 } else { 0.0 };

    let pos_keywords = ["content", "article", "post", "body", "main", "entry", "story", "text", "summary"];
    let neg_keywords = ["sidebar", "ad", "banner", "nav", "footer", "menu", "promo", "cookie", "social", "widget"];

    let mut class_pos_score = 0.0;
    let mut class_neg_score = 0.0;

    let mut cur = Some(node.clone());
    let mut depth_walk = 0;
    while let Some(n) = cur {
        if depth_walk >= 6 { break; }
        let t = tag_name(&n);
        if t == "article" { is_article = 1.0; }
        if t == "main" { is_main = 1.0; }
        if t == "nav" { is_nav = 1.0; }
        if t == "footer" { is_footer = 1.0; }
        if t == "aside" { is_aside = 1.0; }

        let class_str = attr(&n, "class").unwrap_or_default().to_lowercase();
        let id_str = attr(&n, "id").unwrap_or_default().to_lowercase();
        let haystack = format!("{} {}", class_str, id_str);

        for kw in &pos_keywords {
            if haystack.contains(kw) { class_pos_score += 1.0; }
        }
        for kw in &neg_keywords {
            if haystack.contains(kw) { class_neg_score += 1.0; }
        }

        cur = n.parent();
        depth_walk += 1;
    }

    NodeFeatures {
        text_len,
        word_count,
        link_density,
        punctuation_ratio,
        depth: compute_node_depth(node),
        child_count,
        descendant_count,
        p_count,
        heading_count,
        list_count,
        table_count,
        form_count,
        img_count,
        is_article,
        is_main,
        is_nav,
        is_footer,
        is_aside,
        class_pos_score,
        class_neg_score,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    #[test]
    fn test_extract_features_article() {
        let html = r#"
        <article class="post-content">
          <h1>Main Article Headline</h1>
          <p>This is a long narrative paragraph describing the legal doctrine of intent to create legal relations.</p>
          <p>In Balfour v Balfour, Lord Justice Atkin held that domestic agreements between spouses are not contracts.</p>
          <a href="/cite">Citation</a>
        </article>
        "#;
        let doc = kuchiki::parse_html().one(html);
        let article = doc.select("article").unwrap().next().unwrap();
        let feats = extract_features(article.as_node());

        assert_eq!(feats.is_article, 1.0);
        assert!(feats.p_count >= 2.0);
        assert!(feats.heading_count >= 1.0);
        assert!(feats.word_count > 20.0);
        assert!(feats.link_density < 0.3); // predominantly narrative text
        assert!(feats.class_pos_score >= 1.0);
    }
}
