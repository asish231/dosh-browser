//! `identity.rs` — Content-addressed element identity tracker for dosh-browser.
//!
//! Solves element drift across single-page application (React, Vue, Svelte)
//! re-renders. In dynamic DOMs, ordinal references (`e1`, `e2`) shift as siblings
//! are inserted or banners appear. The identity tracker generates content-addressed,
//! structural invariant stable hashes for controls so agents can locate elements
//! deterministically without depending on volatile positional indices.

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use crate::types::Target;

#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ElementIdentity {
    pub stable_id: String,
    pub tag: String,
    pub role: String,
    pub name: String,
    pub href: String,
    pub text_sample: String,
    pub path_signature: String,
}

impl ElementIdentity {
    /// Compute a content-addressed, structural invariant hash for an action target.
    pub fn from_target(target: &Target) -> Self {
        let tag = target.tag.to_lowercase();
        let role = target.role.to_lowercase();
        let name = target.name.clone();
        let href = target.href.clone();
        let text_sample: String = target.text.chars().take(32).collect();
        let path_signature = format!("{:?}", target.path);

        // Compute SHA-256 fingerprint from structural invariants
        let mut hasher = Sha256::new();
        hasher.update(tag.as_bytes());
        hasher.update(b"|");
        hasher.update(role.as_bytes());
        hasher.update(b"|");
        hasher.update(name.as_bytes());
        hasher.update(b"|");
        hasher.update(href.as_bytes());
        hasher.update(b"|");
        hasher.update(text_sample.as_bytes());
        let hash_hex = format!("{:x}", hasher.finalize());
        let stable_id = format!("elem-{}", &hash_hex[..12]);

        Self {
            stable_id,
            tag,
            role,
            name,
            href,
            text_sample,
            path_signature,
        }
    }
}

/// Element identity tracker maintaining maps of ordinal refs to stable identities.
#[derive(Debug, Clone, Default)]
pub struct IdentityTracker {
    ref_to_identity: HashMap<String, ElementIdentity>,
    stable_to_target: HashMap<String, Target>,
}

impl IdentityTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Index current action targets into the identity registry.
    pub fn update(&mut self, targets: &[Target]) {
        self.ref_to_identity.clear();
        self.stable_to_target.clear();

        for target in targets {
            let identity = ElementIdentity::from_target(target);
            self.ref_to_identity.insert(target.ref_id.clone(), identity.clone());
            self.stable_to_target.insert(identity.stable_id.clone(), target.clone());
        }
    }

    /// Resolve a reference by either positional ref (`e1`) or content-addressed stable ID (`elem-...`).
    /// Detects and auto-reconciles DOM reconciliation drift.
    pub fn resolve<'a>(
        &self,
        query: &str,
        current_targets: &'a [Target],
    ) -> Result<(&'a Target, bool), String> {
        // Case 1: Direct stable ID lookup
        if query.starts_with("elem-") {
            for target in current_targets {
                let id = ElementIdentity::from_target(target);
                if id.stable_id == query {
                    return Ok((target, false));
                }
            }
            return Err(format!("Stable element identity '{}' not found in current DOM", query));
        }

        // Case 2: Positional ref lookup with drift verification
        if let Some(target) = current_targets.iter().find(|t| t.ref_id == query) {
            let current_id = ElementIdentity::from_target(target);
            if let Some(saved_id) = self.ref_to_identity.get(query) {
                if saved_id.stable_id == current_id.stable_id {
                    // Exact match, no drift
                    return Ok((target, false));
                }

                // Positional drift detected! Search for the original element elsewhere
                for candidate in current_targets {
                    let cand_id = ElementIdentity::from_target(candidate);
                    if cand_id.stable_id == saved_id.stable_id {
                        return Ok((candidate, true)); // Drift reconciled
                    }
                }
            }
            return Ok((target, false));
        }

        Err(format!("Element reference '{}' not found", query))
    }

    /// Get stable identity for a given positional ref.
    pub fn get_identity(&self, ref_id: &str) -> Option<&ElementIdentity> {
        self.ref_to_identity.get(ref_id)
    }

    /// Total tracked elements in current snapshot.
    pub fn len(&self) -> usize {
        self.ref_to_identity.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ref_to_identity.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_element_identity_stability() {
        let target1 = Target {
            ref_id: "e1".to_string(),
            tag: "button".to_string(),
            role: "button".to_string(),
            text: "Submit Order".to_string(),
            name: "submit_btn".to_string(),
            ..Default::default()
        };

        let target2 = Target {
            ref_id: "e5".to_string(), // shifted index after banner inserted!
            tag: "button".to_string(),
            role: "button".to_string(),
            text: "Submit Order".to_string(),
            name: "submit_btn".to_string(),
            ..Default::default()
        };

        let id1 = ElementIdentity::from_target(&target1);
        let id2 = ElementIdentity::from_target(&target2);

        // Content-addressed stable_id MUST match despite ref shifting from e1 to e5
        assert_eq!(id1.stable_id, id2.stable_id);
    }

    #[test]
    fn test_identity_tracker_drift_reconciliation() {
        let mut tracker = IdentityTracker::new();

        let initial_targets = vec![
            Target {
                ref_id: "e1".to_string(),
                tag: "input".to_string(),
                role: "textbox".to_string(),
                text: "Username".to_string(),
                name: "user".to_string(),
                ..Default::default()
            },
            Target {
                ref_id: "e2".to_string(),
                tag: "button".to_string(),
                role: "button".to_string(),
                text: "Login".to_string(),
                name: "login_btn".to_string(),
                ..Default::default()
            },
        ];

        tracker.update(&initial_targets);

        // Simulate re-render where a banner input is prepended, shifting e2 to e3
        let re_rendered_targets = vec![
            Target {
                ref_id: "e1".to_string(),
                tag: "div".to_string(),
                role: "banner".to_string(),
                text: "Cookie Notice".to_string(),
                ..Default::default()
            },
            Target {
                ref_id: "e2".to_string(),
                tag: "input".to_string(),
                role: "textbox".to_string(),
                text: "Username".to_string(),
                name: "user".to_string(),
                ..Default::default()
            },
            Target {
                ref_id: "e3".to_string(), // Login button shifted to e3!
                tag: "button".to_string(),
                role: "button".to_string(),
                text: "Login".to_string(),
                name: "login_btn".to_string(),
                ..Default::default()
            },
        ];

        // Resolving old "e2" should detect drift and find the Login button at e3
        let (resolved, drifted) = tracker.resolve("e2", &re_rendered_targets).unwrap();
        assert!(drifted, "Expected drift to be detected and reconciled");
        assert_eq!(resolved.ref_id, "e3");
        assert_eq!(resolved.text, "Login");
    }
}
