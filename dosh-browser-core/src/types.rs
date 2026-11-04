//! `types.rs` — Data types matching Python `webresearch/types.py`.
//!
//! Pydantic models → Rust structs with `serde`. JSON serialization must be
//! byte-identical to Python's `model_dump_json()` for parity verification.

use serde::{Deserialize, Serialize};

pub type Kind = String;

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Link {
    pub n: i64,
    pub text: String,
    pub url: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct FieldSpec {
    pub name: String,
    #[serde(rename = "type", default)]
    pub field_type: String,
    #[serde(default)]
    pub value: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub options: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Form {
    pub n: i64,
    #[serde(default = "default_get")]
    pub method: String,
    #[serde(default)]
    pub action: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fields: Vec<FieldSpec>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Meta {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub language: String,
    #[serde(default)]
    pub canonical: String,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub extra: std::collections::HashMap<String, String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Target {
    pub ref_id: String,
    pub role: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub tag: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub href: String,
    #[serde(default)]
    pub checked: Option<bool>,
    #[serde(default)]
    pub challenge: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub path: Vec<i64>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Challenge {
    pub kind: String,
    #[serde(default = "default_click")]
    pub passable: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub refs: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaTile {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub top: String,
    #[serde(default)]
    pub left: String,
    #[serde(default)]
    pub row: Option<i64>,
    #[serde(default)]
    pub column: Option<i64>,
    #[serde(default = "default_button")]
    pub role: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct FrameInfo {
    pub ref_id: String,
    #[serde(default)]
    pub parent_ref: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub depth: i64,
    #[serde(default)]
    pub status: i64,
    #[serde(default)]
    pub content_type: String,
    #[serde(default)]
    pub js_ran: bool,
    #[serde(default)]
    pub scripts: i64,
    #[serde(default)]
    pub challenge: bool,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub html_excerpt: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub post_messages: Vec<serde_json::Value>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaFrame {
    #[serde(default)]
    pub kind: String,
    #[serde(flatten)]
    pub frame: FrameInfo,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaReport {
    #[serde(default)]
    pub present: bool,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub site_key: String,
    #[serde(default)]
    pub callback: String,
    #[serde(default)]
    pub prompt: String,
    #[serde(default)]
    pub checkbox_label: String,
    #[serde(default)]
    pub checkbox_checked: Option<bool>,
    #[serde(default)]
    pub sprite_url: String,
    #[serde(default)]
    pub sprite_bytes: i64,
    #[serde(default)]
    pub sprite_content_type: String,
    #[serde(default)]
    pub sprite_sha256: String,
    #[serde(default)]
    pub sprite_endpoint: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tiles: Vec<CaptchaTile>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub buttons: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub frames: Vec<CaptchaFrame>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hidden_fields: Vec<String>,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub view: String,
    #[serde(default)]
    pub token: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaScript {
    #[serde(default)]
    pub src: String,
    #[serde(default)]
    pub r#type: String,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub frame_ref: String,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaCodePart {
    pub name: String,
    #[serde(default)]
    pub ref_id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub html: String,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaTileSelection {
    #[serde(default)]
    pub tile_id: String,
    #[serde(default)]
    pub row: Option<i64>,
    #[serde(default)]
    pub column: Option<i64>,
    #[serde(default)]
    pub confidence: f64,
    #[serde(default)]
    pub reason: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaSolution {
    #[serde(default)]
    pub solved: bool,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub prompt: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tiles_selected: Vec<CaptchaTileSelection>,
    #[serde(default)]
    pub model_used: String,
    #[serde(default)]
    pub attempt: i64,
    #[serde(default)]
    pub verify_clicked: bool,
    #[serde(default)]
    pub token: String,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub view: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaptchaCodeDump {
    #[serde(default)]
    pub present: bool,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub site_key: String,
    #[serde(default)]
    pub callback: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub parts: Vec<CaptchaCodePart>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub scripts: Vec<CaptchaScript>,
    #[serde(default)]
    pub sprite_b64: String,
    #[serde(default)]
    pub sprite_content_type: String,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct TagProvenance {
    #[serde(default)]
    pub tag: String,
    #[serde(default)]
    pub css_path: String,
    #[serde(default)]
    pub xpath: String,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub attributes: std::collections::HashMap<String, String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct AcdCollection {
    pub id: String,
    pub category: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub count: usize,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub block_ids: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct AcdBlock {
    pub id: String,
    #[serde(rename = "type")]
    pub block_type: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub level: Option<i64>,
    #[serde(default)]
    pub ref_id: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub href: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rows: Vec<Vec<String>>,
    #[serde(default)]
    pub locator: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provenance: Option<TagProvenance>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub semantic_category: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cluster_id: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct AcdAction {
    pub ref_id: String,
    #[serde(default = "default_click")]
    pub kind: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub href: String,
    #[serde(default)]
    pub run: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct AcdApi {
    pub index: i64,
    pub url: String,
    #[serde(default)]
    pub status: i64,
    #[serde(default)]
    pub content_type: String,
    #[serde(default = "default_get")]
    pub method: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Acd {
    #[serde(default = "default_version")]
    pub version: String,
    pub url: String,
    #[serde(default)]
    pub title: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blocks: Vec<AcdBlock>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub actions: Vec<AcdAction>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub links: Vec<Link>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub api: Vec<AcdApi>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub frames: Vec<FrameInfo>,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub framework: std::collections::HashMap<String, serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub errors: Vec<String>,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub counts: std::collections::HashMap<String, i64>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub collections: Vec<AcdCollection>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub unavailable: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Page {
    pub url: String,
    #[serde(default)]
    pub final_url: String,
    pub status: i64,
    #[serde(default)]
    pub content_type: String,
    #[serde(default = "default_html")]
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub view: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub links: Vec<Link>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub forms: Vec<Form>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub headings: Vec<String>,
    #[serde(default)]
    pub meta: Meta,
    #[serde(default)]
    pub spa_hint: bool,
    #[serde(default)]
    pub js_ran: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub targets: Vec<Target>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub challenges: Vec<Challenge>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub frames: Vec<FrameInfo>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub captcha: Option<CaptchaReport>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub acd: Option<Acd>,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub chars: i64,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub headers: std::collections::HashMap<String, String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct FetchResult {
    pub url: String,
    pub final_url: String,
    pub status: i64,
    #[serde(default)]
    pub content_type: String,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub headers: std::collections::HashMap<String, String>,
    #[serde(default = "default_other")]
    pub kind: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ExtractResult {
    pub url: String,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub fields: std::collections::HashMap<String, serde_json::Value>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SearchHit {
    pub n: i64,
    pub title: String,
    pub url: String,
    #[serde(default)]
    pub snippet: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SearchResult {
    pub query: String,
    #[serde(default)]
    pub engine: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hits: Vec<SearchHit>,
    #[serde(default)]
    pub view: String,
}

fn default_get() -> String { "GET".into() }
fn default_click() -> String { "click".into() }
fn default_button() -> String { "button".into() }
fn default_version() -> String { "1".into() }
fn default_html() -> String { "html".into() }
fn default_other() -> String { "other".into() }

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json;

    #[test]
    fn test_acd_block_serialization() {
        let b = AcdBlock {
            id: "b1".into(),
            block_type: "heading".into(),
            text: "Hello".into(),
            level: Some(1),
            ..Default::default()
        };
        let s = serde_json::to_string(&b).unwrap();
        assert!(s.contains("\"id\":\"b1\""));
        assert!(s.contains("\"type\":\"heading\""));
        assert!(s.contains("\"text\":\"Hello\""));
        assert!(s.contains("\"level\":1"));
    }

    #[test]
    fn test_link_serialization() {
        let l = Link { n: 1, text: "Go".into(), url: "https://a.com".into() };
        let s = serde_json::to_string(&l).unwrap();
        assert!(s.contains("\"n\":1"));
        assert!(s.contains("\"text\":\"Go\""));
    }

    #[test]
    fn test_meta_default() {
        let m = Meta::default();
        assert_eq!(m.title, "");
        assert_eq!(m.description, "");
        assert_eq!(m.extra.len(), 0);
    }
}
