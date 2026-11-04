use base64::Engine;
use base64::engine::general_purpose::STANDARD as BASE64;
use sha2::{Digest, Sha256, Sha384, Sha512};
use url::Url;

/// Enforce Subresource Integrity (SRI). Returns true if valid or if integrity is empty.
pub fn verify_integrity(body: &str, integrity: Option<&str>) -> bool {
    let Some(integrity) = integrity else {
        return true;
    };
    let trimmed = integrity.trim();
    if trimmed.is_empty() {
        return true;
    }

    let data = body.as_bytes();
    for token in trimmed.split_whitespace() {
        let Some((algo, expected)) = token.split_once('-') else {
            continue;
        };
        let expected = expected.trim();
        if expected.is_empty() {
            continue;
        }

        let actual = match algo.to_lowercase().as_str() {
            "sha256" => {
                let hash = Sha256::digest(data);
                BASE64.encode(hash)
            }
            "sha384" => {
                let hash = Sha384::digest(data);
                BASE64.encode(hash)
            }
            "sha512" => {
                let hash = Sha512::digest(data);
                BASE64.encode(hash)
            }
            _ => continue,
        };

        if actual == expected {
            return true;
        }
    }

    false
}

/// Returns true if a secure (https) page tries to load an insecure (http) subresource.
pub fn mixed_content_blocked(page_url: &str, resource_url: &str) -> bool {
    let Ok(page) = Url::parse(page_url) else {
        return false;
    };
    let Ok(res) = Url::parse(resource_url) else {
        return false;
    };
    page.scheme() == "https" && res.scheme() == "http"
}

/// Check if a Set-Cookie header violates security policies:
/// - __Secure- prefix requires Secure attribute
/// - __Host- prefix requires Secure, Path=/, and no Domain
/// - SameSite=None requires Secure
pub fn cookie_violates_policy(raw_set_cookie: &str) -> bool {
    let parts: Vec<&str> = raw_set_cookie.split(';').map(|s| s.trim()).collect();
    if parts.is_empty() || !parts[0].contains('=') {
        return false;
    }

    let (name, _) = parts[0].split_once('=').unwrap();
    let name = name.trim();

    let mut secure = false;
    let mut samesite = String::new();
    let mut path = String::new();
    let mut has_domain = false;

    for attr in &parts[1..] {
        let (k, v) = if let Some((k, v)) = attr.split_once('=') {
            (k.trim().to_lowercase(), v.trim().to_lowercase())
        } else {
            (attr.trim().to_lowercase(), String::new())
        };

        if k == "secure" {
            secure = true;
        } else if k == "samesite" {
            samesite = v;
        } else if k == "path" {
            path = v;
        } else if k == "domain" {
            has_domain = true;
        }
    }

    if name.starts_with("__Secure-") && !secure {
        return true;
    }
    if name.starts_with("__Host-") && (!secure || path != "/" || has_domain) {
        return true;
    }
    if samesite == "none" && !secure {
        return true;
    }

    false
}

fn csp_entries(csp: &str, name: &str) -> Vec<String> {
    for part in csp.split(';') {
        let tokens: Vec<&str> = part.split_whitespace().collect();
        if let Some(first) = tokens.first() {
            if first.eq_ignore_ascii_case(name) {
                return tokens[1..].iter().map(|s| s.to_string()).collect();
            }
        }
    }
    Vec::new()
}

fn host_matches(source: &str, url_str: &str) -> bool {
    let Ok(u) = Url::parse(url_str) else {
        return false;
    };
    let s = source.trim().to_lowercase();
    if s == "*" {
        return true;
    }
    if s.ends_with(':') {
        return format!("{}:", u.scheme()) == s;
    }
    let host = u.host_str().unwrap_or("");
    if s.starts_with("*.") {
        return host.ends_with(&s[1..]) || host == &s[2..];
    }
    if s.contains("://") {
        if let Ok(su) = Url::parse(&s) {
            if let Some(sh) = su.host_str() {
                if sh != host {
                    return false;
                }
            }
            if su.scheme() != u.scheme() {
                return false;
            }
            return true;
        }
    }
    s == host
}

/// Evaluates Content-Security-Policy (CSP) script-src / script-src-elem for scripts.
pub fn script_source_allowed(
    csp: Option<&str>,
    inline_code: &str,
    external_url: &str,
    page_url: &str,
    nonce: Option<&str>,
) -> bool {
    let Some(csp) = csp else {
        return true;
    };
    let mut entries = csp_entries(csp, "script-src-elem");
    if entries.is_empty() {
        entries = csp_entries(csp, "script-src");
    }
    if entries.is_empty() {
        entries = csp_entries(csp, "default-src");
    }
    if entries.is_empty() {
        return true;
    }

    let lowered: Vec<String> = entries.iter().map(|s| s.to_lowercase()).collect();

    // Check nonce
    if let Some(n) = nonce {
        let expected = format!("'nonce-{}'", n.to_lowercase());
        if lowered.iter().any(|s| s == &expected) {
            return true;
        }
    }

    if !external_url.is_empty() {
        let Ok(page_u) = Url::parse(page_url) else {
            return true;
        };
        let Ok(ext_u) = Url::parse(external_url) else {
            return true;
        };

        if lowered.iter().any(|s| s == "'self'") && page_u.host_str() == ext_u.host_str() {
            return true;
        }
        if lowered.iter().any(|s| s == "https:") && external_url.starts_with("https:") {
            return true;
        }
        return lowered
            .iter()
            .filter(|s| !s.starts_with('\''))
            .any(|s| host_matches(s, external_url));
    }

    if !inline_code.is_empty() {
        if lowered.iter().any(|s| s == "'unsafe-inline'") {
            return true;
        }
        // Hash check
        let data = inline_code.as_bytes();
        for entry in &entries {
            if entry.starts_with("'sha256-") && entry.ends_with('\'') {
                let expected = &entry[8..entry.len() - 1];
                let actual = BASE64.encode(Sha256::digest(data));
                if actual == expected {
                    return true;
                }
            }
        }
        return false;
    }

    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_verify_integrity() {
        let script = "console.log('hello');";
        let hash = BASE64.encode(Sha256::digest(script.as_bytes()));
        let token = format!("sha256-{}", hash);

        assert!(verify_integrity(script, Some(&token)));
        assert!(!verify_integrity(script, Some("sha256-wronghash")));
        assert!(verify_integrity(script, None));
    }

    #[test]
    fn test_mixed_content_blocked() {
        assert!(mixed_content_blocked("https://example.com", "http://example.com/api.js"));
        assert!(!mixed_content_blocked("https://example.com", "https://example.com/api.js"));
        assert!(!mixed_content_blocked("http://example.com", "http://example.com/api.js"));
    }

    #[test]
    fn test_cookie_violates_policy() {
        assert!(cookie_violates_policy("__Secure-token=123; Path=/")); // missing secure
        assert!(!cookie_violates_policy("__Secure-token=123; Path=/; Secure"));
        assert!(cookie_violates_policy("__Host-token=123; Secure; Path=/test")); // path not /
        assert!(!cookie_violates_policy("__Host-token=123; Secure; Path=/"));
        assert!(cookie_violates_policy("sess=123; SameSite=None")); // SameSite=None requires Secure
    }

    #[test]
    fn test_csp_script_source() {
        let csp = "default-src 'self'; script-src 'self' https://cdn.example.com 'unsafe-inline'";
        assert!(script_source_allowed(Some(csp), "var x = 1;", "", "https://example.com", None));
        assert!(script_source_allowed(Some(csp), "", "https://cdn.example.com/lib.js", "https://example.com", None));
        assert!(!script_source_allowed(Some(csp), "", "https://malicious.com/lib.js", "https://example.com", None));
    }
}

