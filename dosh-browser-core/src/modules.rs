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

/// Translates ES module syntax (`import` / `export` / `import.meta`) into an
/// executable script backed by `globalThis.__WR_MODS` and `host_load_module`
/// so `<script type="module">` and dynamic module graphs execute in V8's
/// classic script evaluation path (Audit P2-01).
pub fn translate_module(
    code: &str,
    module_url: &str,
    import_map: &HashMap<String, String>,
) -> String {
    let url_json = serde_json::to_string(module_url).unwrap_or_else(|_| "\"\"".to_string());
    let map_json = serde_json::to_string(import_map).unwrap_or_else(|_| "{}".to_string());

    // Replace import.meta.url and import.meta
    let mut body = code.replace(
        "import.meta.url",
        &url_json,
    );
    body = body.replace(
        "import.meta",
        &format!("({{ url: {url_json}, env: {{}} }})"),
    );

    // 1. `import * as ns from "spec"`
    let ns_re = regex::Regex::new(r#"(?m)^\s*import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = ns_re
        .replace_all(&body, |caps: &regex::Captures| {
            let ns = &caps[1];
            let spec = &caps[2];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            format!("const {ns} = __wrRequireModule({spec_json}, {url_json});")
        })
        .into_owned();

    // 2. `import def, { a, b as c } from "spec"`
    let both_re = regex::Regex::new(r#"(?m)^\s*import\s+([A-Za-z_$][\w$]*)\s*,\s*\{([^}]*)\}\s+from\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = both_re
        .replace_all(&body, |caps: &regex::Captures| {
            let def_ident = &caps[1];
            let named = caps[2].replace(" as ", ": ");
            let spec = &caps[3];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            format!(
                "const __m_{def_ident} = __wrRequireModule({spec_json}, {url_json}); const {def_ident} = (__m_{def_ident} && 'default' in __m_{def_ident}) ? __m_{def_ident}.default : __m_{def_ident}; const {{ {named} }} = __m_{def_ident} || {{}};"
            )
        })
        .into_owned();

    // 3. `import { a, b as c } from "spec"`
    let named_re = regex::Regex::new(r#"(?m)^\s*import\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = named_re
        .replace_all(&body, |caps: &regex::Captures| {
            let named = caps[1].replace(" as ", ": ");
            let spec = &caps[2];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            format!("const {{ {named} }} = __wrRequireModule({spec_json}, {url_json}) || {{}};")
        })
        .into_owned();

    // 4. `import def from "spec"`
    let def_re = regex::Regex::new(r#"(?m)^\s*import\s+([A-Za-z_$][\w$]*)\s+from\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = def_re
        .replace_all(&body, |caps: &regex::Captures| {
            let def_ident = &caps[1];
            let spec = &caps[2];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            format!(
                "const __m_{def_ident} = __wrRequireModule({spec_json}, {url_json}); const {def_ident} = (__m_{def_ident} && 'default' in __m_{def_ident}) ? __m_{def_ident}.default : __m_{def_ident};"
            )
        })
        .into_owned();

    // 5. Bare `import "spec"`
    let bare_re = regex::Regex::new(r#"(?m)^\s*import\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = bare_re
        .replace_all(&body, |caps: &regex::Captures| {
            let spec = &caps[1];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            format!("__wrRequireModule({spec_json}, {url_json});")
        })
        .into_owned();

    // 6. `export default function/class Name` or `export default expr`
    let exp_def_named_re = regex::Regex::new(r"(?m)^\s*export\s+default\s+(function|class)\s+([A-Za-z_$][\w$]*)").unwrap();
    body = exp_def_named_re
        .replace_all(&body, |caps: &regex::Captures| {
            let kw = &caps[1];
            let name = &caps[2];
            format!("__exports.default = {kw} {name}")
        })
        .into_owned();
    let exp_def_re = regex::Regex::new(r"(?m)^\s*export\s+default\s+").unwrap();
    body = exp_def_re.replace_all(&body, "__exports.default = ").into_owned();

    // 7. `export const/let/var name = ...`
    let exp_var_re = regex::Regex::new(r"(?m)^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=").unwrap();
    body = exp_var_re
        .replace_all(&body, |caps: &regex::Captures| {
            let name = &caps[1];
            format!("const {name} = __exports.{name} =")
        })
        .into_owned();

    // 8. `export function/class name`
    let exp_fn_re = regex::Regex::new(r"(?m)^\s*export\s+(function|class)\s+([A-Za-z_$][\w$]*)").unwrap();
    body = exp_fn_re
        .replace_all(&body, |caps: &regex::Captures| {
            let kw = &caps[1];
            let name = &caps[2];
            format!("const {name} = __exports.{name} = {kw} {name}")
        })
        .into_owned();

    // 9. `export { a, b as c } from "spec"`
    let reexp_re = regex::Regex::new(r#"(?m)^\s*export\s*\{([^}]*)\}\s*from\s*["']([^"']+)["']\s*;?"#).unwrap();
    body = reexp_re
        .replace_all(&body, |caps: &regex::Captures| {
            let list = &caps[1];
            let spec = &caps[2];
            let spec_json = serde_json::to_string(spec).unwrap_or_default();
            let mut assigns = Vec::new();
            for item in list.split(',') {
                let item = item.trim();
                if item.is_empty() {
                    continue;
                }
                if let Some((orig, alias)) = item.split_once(" as ") {
                    assigns.push(format!("__exports.{} = __re.{};", alias.trim(), orig.trim()));
                } else {
                    assigns.push(format!("__exports.{item} = __re.{item};"));
                }
            }
            format!(
                "{{ const __re = __wrRequireModule({spec_json}, {url_json}) || {{}}; {} }}",
                assigns.join(" ")
            )
        })
        .into_owned();

    // 10. `export { a, b as c }`
    let exp_list_re = regex::Regex::new(r"(?m)^\s*export\s*\{([^}]*)\}\s*;?").unwrap();
    body = exp_list_re
        .replace_all(&body, |caps: &regex::Captures| {
            let list = &caps[1];
            let mut assigns = Vec::new();
            for item in list.split(',') {
                let item = item.trim();
                if item.is_empty() {
                    continue;
                }
                if let Some((orig, alias)) = item.split_once(" as ") {
                    assigns.push(format!("__exports.{} = {};", alias.trim(), orig.trim()));
                } else {
                    assigns.push(format!("__exports.{item} = {item};"));
                }
            }
            assigns.join(" ")
        })
        .into_owned();

    format!(
        r#"(function() {{
  globalThis.__WR_MODS = globalThis.__WR_MODS || Object.create(null);
  globalThis.__WR_IMPORT_MAP = Object.assign(globalThis.__WR_IMPORT_MAP || {{}}, {map_json});
  if (typeof globalThis.__wrRequireModule !== 'function') {{
    globalThis.__wrRequireModule = function(spec, baseUrl) {{
      if (globalThis.__WR_MODS[spec]) return globalThis.__WR_MODS[spec];
      if (typeof globalThis.host_load_module === 'function') {{
        var raw = globalThis.host_load_module(spec, baseUrl || '', JSON.stringify(globalThis.__WR_IMPORT_MAP || {{}}));
        if (raw) {{
          try {{
            var info = JSON.parse(raw);
            if (info && info.url && globalThis.__WR_MODS[info.url]) return globalThis.__WR_MODS[info.url];
            if (info && info.translated) {{
              var ex = eval(info.translated);
              if (info.url) globalThis.__WR_MODS[info.url] = ex;
              globalThis.__WR_MODS[spec] = ex;
              return ex;
            }}
          }} catch (e) {{}}
        }}
      }}
      return globalThis.__WR_MODS[spec] || {{}};
    }};
  }}
  var __exports = globalThis.__WR_MODS[{url_json}] = globalThis.__WR_MODS[{url_json}] || {{}};
  {body}
  return __exports;
}})()"#
    )
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
