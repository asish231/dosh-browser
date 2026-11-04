use url::Url;

/// Check if two URLs share the exact same origin (scheme, host, port).
pub fn same_origin(a: &str, b: &str) -> bool {
    let Ok(ua) = Url::parse(a) else {
        return false;
    };
    let Ok(ub) = Url::parse(b) else {
        return false;
    };

    let pa = ua.port_or_known_default();
    let pb = ub.port_or_known_default();

    ua.scheme() == ub.scheme() && ua.host_str() == ub.host_str() && pa == pb
}

/// Evaluates X-Frame-Options: DENY, SAMEORIGIN, ALLOW-FROM
pub fn xfo_decision(xfo: Option<&str>, parent_url: &str, frame_url: &str) -> Option<String> {
    let Some(xfo) = xfo else {
        return None;
    };

    for directive in xfo.split(',') {
        let parts: Vec<&str> = directive.split_whitespace().collect();
        if parts.is_empty() {
            continue;
        }

        let name = parts[0].to_uppercase();
        if name == "DENY" {
            return Some("blocked by X-Frame-Options: DENY".to_string());
        }
        if name == "SAMEORIGIN" && !same_origin(parent_url, frame_url) {
            return Some("blocked by X-Frame-Options: SAMEORIGIN (cross-origin embedder)".to_string());
        }
        if name == "ALLOW-FROM" && parts.len() > 1 {
            let allowed = parts[1];
            if !same_origin(allowed, parent_url) {
                return Some(format!("blocked by X-Frame-Options: ALLOW-FROM {}", allowed));
            }
        }
    }

    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_same_origin() {
        assert!(same_origin("https://example.com/page1", "https://example.com/page2"));
        assert!(same_origin("https://example.com:443/page", "https://example.com/page"));
        assert!(!same_origin("https://example.com/page", "http://example.com/page"));
        assert!(!same_origin("https://a.example.com", "https://b.example.com"));
    }

    #[test]
    fn test_xfo_decision() {
        assert_eq!(
            xfo_decision(Some("DENY"), "https://parent.com", "https://child.com"),
            Some("blocked by X-Frame-Options: DENY".to_string())
        );

        assert_eq!(
            xfo_decision(Some("SAMEORIGIN"), "https://parent.com", "https://child.com"),
            Some("blocked by X-Frame-Options: SAMEORIGIN (cross-origin embedder)".to_string())
        );

        assert_eq!(
            xfo_decision(Some("SAMEORIGIN"), "https://example.com", "https://example.com/frame"),
            None
        );
    }
}

