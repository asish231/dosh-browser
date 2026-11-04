//! ES module support & import specifier resolver.
//! Direct port of `webresearch/engine/modules.py`.

use std::collections::HashMap;
use url::Url;

#[derive(Debug, Clone, Default)]
pub struct ModuleSource {
    pub url: String,
    pub code: String,
    pub imports: HashMap<String, String>,
}

#[derive(Debug, Clone, Default)]
pub struct ModuleGraph {
    pub order: Vec<ModuleSource>,
    pub missing: Vec<String>,
    pub blocked: Vec<HashMap<String, String>>,
}

/// Resolves an import specifier against a base URL, honoring an import map if present.
pub fn resolve_specifier(
    specifier: &str,
    base_url: &str,
    import_map: &HashMap<String, String>,
) -> Option<String> {
    if let Some(target) = import_map.get(specifier) {
        return Some(target.clone());
    }
    if specifier.starts_with("./") || specifier.starts_with("../") || specifier.starts_with('/') {
        if let Ok(base) = Url::parse(base_url) {
            if let Ok(joined) = base.join(specifier) {
                return Some(joined.to_string());
            }
        }
    }
    if specifier.starts_with("https://") || specifier.starts_with("http://") {
        return Some(specifier.to_string());
    }
    None
}

/// Scans source code for static `import ... from "..."` or `export ... from "..."` specifiers.
pub fn iter_imports(code: &str) -> Vec<String> {
    let mut out = Vec::new();
    let import_re = regex::Regex::new(r#"(?:import|export)\s+.*?from\s*["']([^"']+)["']"#).unwrap();
    for cap in import_re.captures_iter(code) {
        if let Some(m) = cap.get(1) {
            let spec = m.as_str().to_string();
            if !out.contains(&spec) {
                out.push(spec);
            }
        }
    }
    out
}

/// Parses inline `<script type="importmap">` JSON contents.
pub fn extract_import_map(html: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let re = regex::Regex::new(r#"(?is)<script[^>]*type=["']importmap["'][^>]*>(.*?)</script>"#).unwrap();
    for cap in re.captures_iter(html) {
        if let Some(m) = cap.get(1) {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(m.as_str()) {
                if let Some(imports) = val.get("imports").and_then(|v| v.as_object()) {
                    for (k, v) in imports {
                        if let Some(s) = v.as_str() {
                            out.insert(k.clone(), s.to_string());
                        }
                    }
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_import_map() {
        let html = r#"
            <script type="importmap">
            {
                "imports": {
                    "react": "https://esm.sh/react@18",
                    "lodash": "/vendor/lodash.js"
                }
            }
            </script>
        "#;
        let map = extract_import_map(html);
        assert_eq!(map.get("react").map(|s| s.as_str()), Some("https://esm.sh/react@18"));
        assert_eq!(map.get("lodash").map(|s| s.as_str()), Some("/vendor/lodash.js"));
    }

    #[test]
    fn test_resolve_specifier() {
        let mut import_map = HashMap::new();
        import_map.insert("vue".to_string(), "https://esm.sh/vue@3".to_string());

        assert_eq!(
            resolve_specifier("vue", "https://example.com/app.js", &import_map),
            Some("https://esm.sh/vue@3".to_string())
        );
        assert_eq!(
            resolve_specifier("./utils.js", "https://example.com/js/main.js", &import_map),
            Some("https://example.com/js/utils.js".to_string())
        );
    }

    #[test]
    fn test_iter_imports() {
        let code = r#"
            import React, { useState } from "react";
            import { helper } from "./utils.js";
            export { Button } from "./button.js";
            const x = 42;
        "#;
        let imports = iter_imports(code);
        assert_eq!(imports.len(), 3);
        assert_eq!(imports[0], "react");
        assert_eq!(imports[1], "./utils.js");
        assert_eq!(imports[2], "./button.js");
    }
}
