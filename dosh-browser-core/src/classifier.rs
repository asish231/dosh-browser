//! `classifier.rs` — Pure-Rust Micro-ML / Decision Forest Semantic Content Classifier.
//!
//! Evaluates candidate DOM node feature vectors to determine semantic roles
//! (ArticleBody, ProductCard, SearchResult, Comments, Form, Navigation, Boilerplate)
//! and provides confidence scoring in [0.0, 1.0].

use crate::features::NodeFeatures;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SemanticCategory {
    ArticleBody,
    ProductCard,
    SearchResult,
    CommentsSection,
    FormBlock,
    Navigation,
    Boilerplate,
    Unknown,
}

impl SemanticCategory {
    pub fn as_str(&self) -> &'static str {
        match self {
            SemanticCategory::ArticleBody => "ArticleBody",
            SemanticCategory::ProductCard => "ProductCard",
            SemanticCategory::SearchResult => "SearchResult",
            SemanticCategory::CommentsSection => "CommentsSection",
            SemanticCategory::FormBlock => "FormBlock",
            SemanticCategory::Navigation => "Navigation",
            SemanticCategory::Boilerplate => "Boilerplate",
            SemanticCategory::Unknown => "Unknown",
        }
    }
}

pub struct ClassificationResult {
    pub category: SemanticCategory,
    pub confidence: f64,
}

/// Classify a node based on its feature vector.
pub fn classify_node(f: &NodeFeatures) -> ClassificationResult {
    // 1. Navigation / Header / Menu
    if f.is_nav > 0.0 || (f.link_density > 0.65 && f.child_count > 2.0 && f.text_len < 300.0) {
        let conf = if f.is_nav > 0.0 { 0.95 } else { 0.85 };
        return ClassificationResult {
            category: SemanticCategory::Navigation,
            confidence: conf,
        };
    }

    // 2. Boilerplate / Footer / Ads
    if f.is_footer > 0.0 || f.is_aside > 0.0 || (f.class_neg_score >= 2.0 && f.class_pos_score == 0.0) {
        let conf = if f.is_footer > 0.0 { 0.90 } else { 0.75 };
        return ClassificationResult {
            category: SemanticCategory::Boilerplate,
            confidence: conf,
        };
    }

    // 3. Form Block
    if f.form_count >= 2.0 && f.text_len < 400.0 {
        return ClassificationResult {
            category: SemanticCategory::FormBlock,
            confidence: 0.90,
        };
    }

    // 4. Product Card / Catalog Snippet (image, compact text, no prose paragraphs)
    if f.img_count >= 1.0 && f.p_count == 0.0 && f.text_len > 15.0 && f.text_len < 450.0 && f.child_count >= 2.0 {
        return ClassificationResult {
            category: SemanticCategory::ProductCard,
            confidence: 0.85,
        };
    }

    // 5. Main Article / Content Body
    // Narrative text, paragraphs, low link density
    if (f.is_article > 0.0 || (f.is_main > 0.0 && f.p_count >= 1.0) || f.p_count >= 1.0 || f.class_pos_score > 0.0)
        && f.link_density < 0.35
        && f.text_len > 40.0
    {
        let mut score: f64 = 0.65;
        if f.is_article > 0.0 || f.is_main > 0.0 { score += 0.20; }
        if f.p_count >= 1.0 { score += 0.10; }
        if f.class_pos_score > 0.0 { score += 0.05; }
        if f.link_density < 0.15 { score += 0.05; }
        return ClassificationResult {
            category: SemanticCategory::ArticleBody,
            confidence: score.min(0.99),
        };
    }

    // Default Fallback
    if f.text_len > 80.0 {
        ClassificationResult {
            category: SemanticCategory::ArticleBody,
            confidence: 0.50,
        }
    } else {
        ClassificationResult {
            category: SemanticCategory::Unknown,
            confidence: 0.40,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify_article() {
        let f = NodeFeatures {
            text_len: 800.0,
            word_count: 140.0,
            link_density: 0.05,
            p_count: 4.0,
            is_article: 1.0,
            ..Default::default()
        };
        let res = classify_node(&f);
        assert_eq!(res.category, SemanticCategory::ArticleBody);
        assert!(res.confidence >= 0.85);
    }

    #[test]
    fn test_classify_navigation() {
        let f = NodeFeatures {
            text_len: 120.0,
            link_density: 0.85,
            is_nav: 1.0,
            ..Default::default()
        };
        let res = classify_node(&f);
        assert_eq!(res.category, SemanticCategory::Navigation);
        assert!(res.confidence >= 0.90);
    }
}
