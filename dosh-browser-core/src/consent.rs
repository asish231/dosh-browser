//! `consent.rs` — Cookie Consent and GDPR Banner Auto-Dismisser.
//!
//! Detects and dismisses intrusive cookie consent modals (OneTrust, Cookiebot,
//! Klaro, TrustArc, Didomi, Quantcast, Google Consent) so they do not block
//! the agent's view or interfere with elements.
//!
//! Direct Rust counterpart to `webresearch/consent.py`.

use kuchiki::traits::TendrilSink;

pub const CONSENT_CONTAINER_SELECTORS: &[&str] = &[
    "#onetrust-consent-sdk",
    "#onetrust-banner-sdk",
    ".cookie-banner",
    ".cookie-consent",
    ".cookie-modal",
    ".consent-banner",
    ".fc-consent-root",
    "#cmpbox",
    "#didomi-host",
];

const CONSENT_KEYWORDS: &[&str] = &[
    "onetrust",
    "cookie-consent",
    "cookie-banner",
    "cookies-banner",
    "cookie consent",
    "didomi",
    "quantcast",
    "fc-consent",
];

/// Quick check if a consent banner is likely present in HTML.
pub fn has_consent_dialog(html: &str) -> bool {
    let low = html.to_lowercase();
    CONSENT_KEYWORDS.iter().any(|&kw| low.contains(kw))
}

/// Strip known consent dialog containers from the DOM and return cleaned HTML.
pub fn dismiss_consent_dom(html: &str) -> (String, bool) {
    if !has_consent_dialog(html) {
        return (html.to_string(), false);
    }

    let dom = kuchiki::parse_html().one(html);
    let mut dismissed = false;

    for &sel in CONSENT_CONTAINER_SELECTORS {
        if let Ok(iter) = dom.select(sel) {
            for node in iter {
                node.as_node().detach();
                dismissed = true;
            }
        }
    }

    // Check for explicit "Accept all" / "Allow cookies" buttons inside banner containers
    if let Ok(buttons) = dom.select("button, a.btn, input[type='button'], input[type='submit']") {
        for btn in buttons {
            let elem = btn.as_node();
            let mut text = String::new();
            for c in elem.children() {
                if let Some(t) = c.as_text() {
                    text.push_str(&t.borrow());
                }
            }
            let text_low = text.trim().to_lowercase();
            let is_match = text_low == "accept all"
                || text_low == "accept cookies"
                || text_low == "allow all"
                || text_low == "allow cookies"
                || text_low == "i agree"
                || text_low == "agree & close"
                || text_low == "agree";

            if is_match {
                // Walk ancestors up to 4 levels
                let mut curr = elem.parent();
                let mut depth = 0;
                while let Some(parent) = curr {
                    if depth >= 4 {
                        break;
                    }
                    if let Some(p_elem) = parent.as_element() {
                        let tag = p_elem.name.local.to_string();
                        let attrs = p_elem.attributes.borrow();
                        let cls = attrs.get("class").unwrap_or_default().to_lowercase();
                        let id = attrs.get("id").unwrap_or_default().to_lowercase();

                        if cls.contains("cookie")
                            || id.contains("cookie")
                            || cls.contains("consent")
                            || id.contains("consent")
                            || tag == "dialog"
                        {
                            parent.detach();
                            dismissed = true;
                            break;
                        }
                    }
                    curr = parent.parent();
                    depth += 1;
                }
            }
        }
    }

    if dismissed {
        let mut bytes = Vec::new();
        let _ = dom.serialize(&mut bytes);
        (String::from_utf8_lossy(&bytes).into_owned(), true)
    } else {
        (html.to_string(), false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_has_consent_dialog() {
        assert!(has_consent_dialog("<div class='cookie-banner'>Please accept</div>"));
        assert!(!has_consent_dialog("<div>Clean page</div>"));
    }

    #[test]
    fn test_dismiss_consent_dom() {
        let html = r#"<html><body><div id="onetrust-consent-sdk"><button>Accept</button></div><main><h1>Real Content</h1></main></body></html>"#;
        let (cleaned, dismissed) = dismiss_consent_dom(html);
        assert!(dismissed);
        assert!(!cleaned.contains("onetrust-consent-sdk"));
        assert!(cleaned.contains("Real Content"));
    }
}

