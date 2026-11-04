//! `cluster.rs` — Sibling Subtree Clustering Engine for ACD 2.0.
//!
//! Automatically identifies repeating sibling structures (search results, product cards,
//! catalog listings) and clusters them into structured `AcdCollection` entries.

use kuchiki::NodeRef;
use std::collections::HashMap;
use crate::content::{tag_name, text_content};
use crate::types::AcdCollection;

/// Computes a structural signature for an element based on its direct child tags.
/// e.g. "div > h3+p+a"
pub fn child_tag_signature(node: &NodeRef) -> String {
    let mut tags = Vec::new();
    for child in node.children() {
        if child.as_element().is_some() {
            let t = tag_name(&child);
            if !t.is_empty() {
                tags.push(t);
            }
        }
    }
    tags.join("+")
}

/// Identifies recurring clusters among sibling elements under a parent.
pub fn cluster_siblings(parent: &NodeRef, collection_id_start: usize) -> Vec<AcdCollection> {
    let mut collections = Vec::new();
    let mut signature_groups: HashMap<String, Vec<NodeRef>> = HashMap::new();

    for child in parent.children() {
        if child.as_element().is_some() {
            let sig = child_tag_signature(&child);
            // Ignore empty or single-tag trivial signatures
            if !sig.is_empty() && sig.contains('+') {
                signature_groups.entry(sig).or_default().push(child.clone());
            }
        }
    }

    let mut current_id = collection_id_start;
    for (sig, nodes) in signature_groups {
        // If 2 or more siblings share the exact multi-tag structural signature, form a collection
        if nodes.len() >= 2 {
            let sample_text = text_content(&nodes[0]);
            let category = if sig.contains("img") && (sample_text.contains('$') || sample_text.contains("₹") || sample_text.contains("€")) {
                "ProductCatalog"
            } else if sig.contains("h3") || sig.contains("h2") {
                "SearchResults"
            } else {
                "CardGrid"
            };

            collections.push(AcdCollection {
                id: format!("col_{}", current_id),
                category: category.to_string(),
                name: format!("Collection ({})", sig),
                count: nodes.len(),
                block_ids: Vec::new(),
            });
            current_id += 1;
        }
    }

    collections
}

#[cfg(test)]
mod tests {
    use super::*;
    use kuchiki::traits::TendrilSink;

    #[test]
    fn test_cluster_search_results() {
        let html = r#"
        <div id="results">
          <div class="result">
            <h3>Title 1</h3>
            <p>Snippet 1</p>
            <a href="/1">Link 1</a>
          </div>
          <div class="result">
            <h3>Title 2</h3>
            <p>Snippet 2</p>
            <a href="/2">Link 2</a>
          </div>
          <div class="result">
            <h3>Title 3</h3>
            <p>Snippet 3</p>
            <a href="/3">Link 3</a>
          </div>
        </div>
        "#;
        let doc = kuchiki::parse_html().one(html);
        let results_container = doc.select("#results").unwrap().next().unwrap();
        let clusters = cluster_siblings(results_container.as_node(), 1);

        assert_eq!(clusters.len(), 1);
        assert_eq!(clusters[0].count, 3);
        assert_eq!(clusters[0].category, "SearchResults");
    }
}

