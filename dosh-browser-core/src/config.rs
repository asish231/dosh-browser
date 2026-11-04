//! Project configuration: lanes, engines, and status.
//! Direct port of `webresearch/config.py`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LaneInfo {
    pub name: String,
    pub path: String,
    pub status: String,
    pub summary: String,
}

pub const OFFICIAL_LANE: &str = "rust";
pub const DEFAULT_ENGINE: &str = "doshgentv8";
pub const DEFAULT_PORT: u16 = 4747;

/// Returns project status information across all lanes.
pub fn project_status() -> serde_json::Value {
    serde_json::json!({
        "official_lane": OFFICIAL_LANE,
        "default_engine": DEFAULT_ENGINE,
        "lanes": {
            "rust": {
                "path": "dosh-browser-core/",
                "status": "official",
                "summary": "Full pure-Rust browser core with doshgentv8 V8 engine, RFC 9111 caching, ANBF binary packaging, 35 shell commands, and HTTP/REST server."
            },
            "python": {
                "path": "webresearch/",
                "status": "reference",
                "summary": "Original reference prototype with QuickJS."
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_project_status() {
        let status = project_status();
        assert_eq!(status["official_lane"], "rust");
        assert_eq!(status["default_engine"], "doshgentv8");
    }
}

