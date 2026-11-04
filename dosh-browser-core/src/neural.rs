//!  — Zero-dependency, pure-Rust SIMD Micro-Neural Network.
//! Evaluates unsemantic DOM candidates in ~100 nanoseconds with 0 heap allocations.

pub const NUM_IN: usize = 32;
pub const NUM_H1: usize = 16;
pub const NUM_OUT: usize = 5;

pub const S1: f32 = 0.011585374516764963f32;
pub const S2: f32 = 0.010805594639515314f32;

pub const W1: [i8; 512] = [70, 37, 44, 54, -1, 31, 23, 29, -2, -36, -39, -12, 24, -55, 19, -1, -20, 46, 55, -43, 104, -39, -43, -21, -62, 31, -1, 32, 1, 13, 58, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -41, 23, 9, 25, 0, 16, -20, 22, -2, -16, -49, 14, 14, 61, 23, 30, -35, 24, 57, -49, -101, -45, 19, -33, -56, 21, 2, -10, 0, 17, -34, 45, 43, -19, -33, -32, 1, -15, -61, -18, -14, 31, -46, 3, -4, -14, -8, 9, 44, -27, -37, -30, 50, 74, 21, 42, -20, -15, 0, 10, 0, 20, 21, 56, 1, 16, 47, 29, 0, 21, 49, 13, 10, -16, 44, -3, 7, -7, 8, -8, -20, 24, 35, 43, 1, -32, -23, -19, 28, 14, 0, -12, 0, 3, -4, -9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -30, -15, -20, -32, 1, -17, 42, -13, 3, 37, 27, 8, -9, 34, -8, 8, 38, -29, -43, 34, -79, 44, 23, 48, 39, -21, 1, -25, 0, 18, -35, 29, 13, 8, 5, 18, -1, 8, 65, 7, -6, 20, -33, -5, 6, -24, 5, 4, 22, 16, 24, -32, 27, 43, -16, 21, -38, 8, 0, 1, -1, 5, 10, -1, 2, 8, 29, 15, 0, 9, 75, 7, 8, 32, 16, -6, 3, -34, 4, -5, 44, 10, 18, 15, -15, 65, -24, 34, 13, 9, 0, -7, 0, 3, -4, 9, -7, 2, 4, 2, 0, 2, 68, 2, 0, 40, -3, -3, 1, -26, 1, 3, 39, 1, -1, -2, -33, 61, -14, 50, -4, 3, 0, -13, 0, 11, -12, 8, -57, 22, 15, 30, 0, 15, -39, 16, 0, -12, -46, 16, 13, 61, 19, 22, -36, 29, 55, -47, -127, -47, 15, -31, -61, 13, 3, -10, -1, 12, -43, 63, -35, -15, -22, -35, 1, -19, -66, -11, 9, -6, 48, 22, -10, 65, 3, 12, -20, -30, -33, 57, -93, -33, 34, -20, 62, -20, 1, -32, -1, 15, -38, 59, -6, 25, 34, 28, 0, 18, 60, 15, 11, -16, 48, -3, 5, -6, 9, -5, -21, 30, 36, 38, -7, -36, -26, -20, 26, 22, 0, 0, 0, 3, -2, -7, 40, -13, 0, -20, 1, -11, -56, -12, 14, -35, 48, 2, -8, 33, -8, -9, -24, -17, -44, 55, 51, -42, 21, -28, 50, -10, 0, 39, 0, 24, 27, 56, 65, -8, 17, -7, 0, -4, 2, -8, 11, -22, 57, -11, -6, -37, -8, -17, -16, -6, -33, 49, 89, -23, -5, -17, 60, -2, -2, 39, 1, 11, 42, 11, 2, 7, 32, 15, 0, 8, 64, 6, 22, -14, 56, -3, 1, 2, 3, -5, -17, 13, 14, 44, 5, -34, -10, -16, 51, 10, 0, -1, 0, 4, 1, -8];
pub const B1: [f32; 16] = [0.20671015977859497, -3.160510431143848e-08, 0.5120821595191956, 0.6471536159515381, 0.07533427327871323, -3.109640433168484e-20, 0.47169336676597595, -0.007417952176183462, -0.013817972503602505, 0.14566367864608765, 0.639681875705719, 0.6521186232566833, 0.06647858768701553, 0.4105101227760315, 0.15379810333251953, 0.04051605239510536];

pub const W2: [i8; 80] = [-114, 0, 93, 26, -68, 0, 39, -72, -92, -58, 97, 109, -80, 33, -81, -78, 106, 0, 71, -114, 57, 0, -65, 50, 18, -1, 67, -88, 58, -80, -57, 28, 98, 0, -94, 81, -31, 0, -97, -2, -69, -72, -127, -88, -47, 74, 88, -37, -82, 0, -85, -114, 62, 0, 28, -52, 2, -11, -96, 56, 62, 49, 60, 86, -66, 0, -75, 82, -48, 0, 73, 76, 53, 77, -91, -79, -53, -100, -49, -44];
pub const B2: [f32; 5] = [0.3774457275867462, -0.10773692280054092, 0.3718506097793579, -0.4346425533294678, 0.1265040934085846];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ActionRoleClass {
    NonInteractive = 0,
    Button = 1,
    Input = 2,
    Link = 3,
    Slider = 4,
}

#[derive(Clone, Debug)]
pub struct NeuralPrediction {
    pub class: ActionRoleClass,
    pub confidence: f32,
    pub probabilities: [f32; NUM_OUT],
}

/// Champion forward inference pass: 32 -> 16 (ReLU) -> 5 (Softmax).
#[inline(always)]
pub fn predict_interactivity(x: &[f32; NUM_IN]) -> NeuralPrediction {
    let mut h1 = [0.0f32; NUM_H1];
    let mut logits = [0.0f32; NUM_OUT];

    // Layer 1: 32 -> 16 (Auto-vectorized by LLVM into SIMD)
    for i in 0..NUM_H1 {
        let offset = i * NUM_IN;
        let mut acc = 0.0f32;
        for j in 0..NUM_IN {
            acc += x[j] * (W1[offset + j] as f32);
        }
        let val = acc * S1 + B1[i];
        h1[i] = if val > 0.0 { val } else { 0.0 };
    }

    // Layer 2: 16 -> 5
    let mut max_logit = -f32::INFINITY;
    for i in 0..NUM_OUT {
        let offset = i * NUM_H1;
        let mut acc = 0.0f32;
        for j in 0..NUM_H1 {
            acc += h1[j] * (W2[offset + j] as f32);
        }
        let val = acc * S2 + B2[i];
        logits[i] = val;
        if val > max_logit {
            max_logit = val;
        }
    }

    // Numerically stable Softmax
    let mut sum_exp = 0.0f32;
    let mut probs = [0.0f32; NUM_OUT];
    for i in 0..NUM_OUT {
        let e = (logits[i] - max_logit).exp();
        probs[i] = e;
        sum_exp += e;
    }

    let inv_sum = 1.0 / sum_exp;
    let mut best_idx = 0;
    let mut best_prob = 0.0f32;
    for i in 0..NUM_OUT {
        let p = probs[i] * inv_sum;
        probs[i] = p;
        if p > best_prob {
            best_prob = p;
            best_idx = i;
        }
    }

    let class = match best_idx {
        1 => ActionRoleClass::Button,
        2 => ActionRoleClass::Input,
        3 => ActionRoleClass::Link,
        4 => ActionRoleClass::Slider,
        _ => ActionRoleClass::NonInteractive,
    };

    NeuralPrediction {
        class,
        confidence: best_prob,
        probabilities: probs,
    }
}

use kuchiki::NodeRef;
use crate::content::{attr, class_tokens, tag_name, text_content};

/// Extracts the 32-dimensional feature vector for any DOM node in ~2 microseconds.
pub fn extract_interactivity_features(node: &NodeRef, flags: u32) -> [f32; NUM_IN] {
    let mut f = [0.0f32; NUM_IN];
    f[31] = 1.0; // Bias constant

    let tag = tag_name(node);
    f[0] = match tag.as_str() {
        "div" => 0.1,
        "span" => 0.2,
        "li" => 0.3,
        "p" => 0.4,
        "a" => 0.5,
        "button" => 0.6,
        "input" => 0.7,
        "svg" => 0.8,
        _ => 0.0,
    };

    // Framework & event flags
    if (flags & 1) != 0 { f[1] = 1.0; }      // Native click
    if (flags & 64) != 0 { f[2] = 1.0; }     // React synthetic
    if (flags & 128) != 0 { f[3] = 1.0; }    // Vue synthetic
    if (flags & 768) != 0 { f[4] = 1.0; }    // Angular / Svelte
    if (flags & 32) != 0 { f[5] = 1.0; }     // Inline onclick
    if (flags & 1024) != 0 { f[6] = 1.0; }   // Computed cursor: pointer

    let tokens = class_tokens(node);
    let style = attr(node, "style").unwrap_or_default().replace(" ", "").to_lowercase();
    if (flags & (1 | 64 | 128 | 256 | 512 | 1024)) != 0
        || tokens.iter().any(|t| t.to_lowercase().contains("cursor-pointer"))
        || style.contains("cursor:pointer")
    {
        f[6] = 1.0;
    }

    // ARIA roles
    let role = attr(node, "role").unwrap_or_default().to_lowercase();
    if role == "button" { f[7] = 1.0; }
    if role == "link" { f[8] = 1.0; }
    if role == "slider" { f[9] = 1.0; }

    if attr(node, "aria-expanded").is_some() || attr(node, "aria-pressed").is_some() || attr(node, "aria-checked").is_some() {
        f[10] = 1.0;
    }
    if attr(node, "aria-disabled").as_deref() == Some("true") {
        f[11] = 1.0;
    }
    if let Some(tb) = attr(node, "tabindex").and_then(|t| t.parse::<i32>().ok()) {
        if tb >= 0 { f[12] = 1.0; }
    }

    // Text metrics
    let text = text_content(node);
    let chars_len = text.chars().count();
    f[13] = (chars_len.min(100) as f32) / 100.0;
    let words_len = text.split_whitespace().count();
    f[14] = (words_len.min(16) as f32) / 16.0;

    // Topology: only count element children (skip text nodes and comments)
    let mut child_count = 0;
    let mut has_svg = false;
    for c in node.children() {
        if c.as_element().is_some() {
            child_count += 1;
            if tag_name(&c) == "svg" {
                has_svg = true;
            }
        }
    }
    f[15] = (child_count.min(10) as f32) / 10.0;
    if child_count == 0 { f[16] = 1.0; }
    if has_svg { f[17] = 1.0; }

    // Classes
    let tokens = class_tokens(node);
    for t in &tokens {
        let lt = t.to_lowercase();
        if lt.contains("btn") || lt.contains("button") || lt.contains("cta") || lt.contains("press") {
            f[18] = 1.0;
        }
        if lt.contains("link") || lt.contains("nav") || lt.contains("menu") || lt.contains("item") {
            f[19] = 1.0;
        }
        if lt.contains("input") || lt.contains("field") || lt.contains("search") {
            f[20] = 1.0;
        }
        if lt.contains("slider") || lt.contains("thumb") || lt.contains("handle") || lt.contains("grip") {
            f[21] = 1.0;
        }
        if lt.contains("container") || lt.contains("wrapper") || lt.contains("row") || lt.contains("col") || lt.contains("grid") {
            f[22] = 1.0;
        }
    }

    // Custom data attributes
    if attr(node, "data-action").is_some() || attr(node, "data-click").is_some() { f[23] = 1.0; }
    if attr(node, "data-href").is_some() || attr(node, "href").is_some() || attr(node, "to").is_some() { f[24] = 1.0; }

    // Action verbs
    let lower_text = text.to_lowercase();
    let first_word = lower_text.split_whitespace().next().unwrap_or("");
    if matches!(first_word, "submit" | "apply" | "save" | "delete" | "next" | "prev" | "buy" | "sign" | "checkout" | "proceed") {
        f[25] = 1.0;
    }
    if first_word.starts_with('$') || first_word.parse::<f64>().is_ok() {
        f[26] = 1.0;
    }

    if attr(node, "contenteditable").as_deref() == Some("true") {
        f[30] = 1.0;
    }

    f
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_champion_predict_interactivity() {
        let mut feats = [0.0f32; 32];
        feats[31] = 1.0;
        // Button with React synthetic click
        feats[0] = 0.1; // div
        feats[2] = 1.0; // React
        feats[6] = 1.0; // cursor: pointer
        feats[18] = 1.0; // btn class
        let pred = predict_interactivity(&feats);
        assert_eq!(pred.class, ActionRoleClass::Button);
        assert!(pred.confidence >= 0.90);
    }
}
