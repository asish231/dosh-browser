//! `cors.rs` — W3C Cross-Origin Resource Sharing (CORS) Gate and Preflight Store.
//!
//! Enforces CORS rules for subresource fetches, script XHR/fetch calls, and iframes:
//! - Identifies simple vs preflight-required requests
//! - Maintains an in-memory cache of preflight OPTIONS grants (`CorsStore`)
//! - Validates response headers (`Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials`)

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};
use serde::{Deserialize, Serialize};
use tokio::sync::Mutex;
use url::Url;

/// Outcome of pre-request CORS evaluation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CorsDecision {
    /// Same origin, no CORS restrictions apply.
    SameOrigin,
    /// Cross-origin but satisfies CORS-safelisted method and headers (no preflight needed).
    SimpleRequest { origin_header: String },
    /// Requires a preflight OPTIONS request before proceeding.
    PreflightRequired { origin_header: String },
    /// Already authorized by a cached preflight grant.
    PreflightCached { origin_header: String },
}

/// Errors occurring during CORS validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CorsError {
    MissingAllowOrigin,
    OriginMismatch { expected: String, actual: String },
    WildcardOriginWithCredentials,
    MethodNotAllowed(String),
    HeaderNotAllowed(String),
}

impl std::fmt::Display for CorsError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingAllowOrigin => write!(f, "CORS error: Missing Access-Control-Allow-Origin header"),
            Self::OriginMismatch { expected, actual } => {
                write!(f, "CORS error: Origin {} not allowed (got {})", expected, actual)
            }
            Self::WildcardOriginWithCredentials => {
                write!(f, "CORS error: Cannot use wildcard '*' Access-Control-Allow-Origin when credentials mode is 'include'")
            }
            Self::MethodNotAllowed(m) => write!(f, "CORS error: Method {} not allowed by preflight", m),
            Self::HeaderNotAllowed(h) => write!(f, "CORS error: Header {} not allowed by preflight", h),
        }
    }
}

impl std::error::Error for CorsError {}

/// Cached preflight authorization.
#[derive(Debug, Clone)]
pub struct PreflightEntry {
    pub allowed_methods: HashSet<String>,
    pub allowed_headers: HashSet<String>,
    pub allow_credentials: bool,
    pub expires_at: Instant,
}

impl PreflightEntry {
    pub fn is_fresh(&self) -> bool {
        Instant::now() < self.expires_at
    }

    pub fn allows_method(&self, method: &str) -> bool {
        let m = method.to_uppercase();
        self.allowed_methods.contains("*") || self.allowed_methods.contains(&m)
    }

    pub fn allows_header(&self, header: &str) -> bool {
        let h = header.to_lowercase();
        if h == "authorization" {
            // WHATWG Fetch §3.2.3: wildcard '*' does NOT cover Authorization
            self.allowed_headers.contains("authorization")
        } else {
            self.allowed_headers.contains("*") || self.allowed_headers.contains(&h)
        }
    }
}

/// In-memory preflight authorization cache.
#[derive(Clone, Default)]
pub struct CorsStore {
    // Key: (client_origin, target_origin)
    entries: Arc<Mutex<HashMap<(String, String), PreflightEntry>>>,
}

impl CorsStore {
    pub fn new() -> Self {
        Self {
            entries: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Record a preflight OPTIONS response into cache.
    pub async fn record_preflight(
        &self,
        client_origin: &str,
        target_origin: &str,
        headers: &HashMap<String, String>,
    ) {
        let max_age_secs = headers
            .get("access-control-max-age")
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(5)
            .min(7200); // capped at 2 hours per WHATWG / Chromium specification

        let mut allowed_methods = HashSet::new();
        if let Some(methods_hdr) = headers.get("access-control-allow-methods") {
            for m in methods_hdr.split(',') {
                allowed_methods.insert(m.trim().to_uppercase());
            }
        }

        let mut allowed_headers = HashSet::new();
        if let Some(headers_hdr) = headers.get("access-control-allow-headers") {
            for h in headers_hdr.split(',') {
                allowed_headers.insert(h.trim().to_lowercase());
            }
        }

        let allow_credentials = headers
            .get("access-control-allow-credentials")
            .map(|v| v.trim().eq_ignore_ascii_case("true"))
            .unwrap_or(false);

        let key = (client_origin.to_string(), target_origin.to_string());
        let mut map = self.entries.lock().await;
        if let Some(existing) = map.get_mut(&key) {
            if existing.is_fresh() {
                // WHATWG Fetch specification: union allowed methods & headers, clamp TTL
                existing.allowed_methods.extend(allowed_methods);
                existing.allowed_headers.extend(allowed_headers);
                existing.allow_credentials = existing.allow_credentials || allow_credentials;
                existing.expires_at = existing.expires_at.min(Instant::now() + Duration::from_secs(max_age_secs));
                return;
            }
        }

        let entry = PreflightEntry {
            allowed_methods,
            allowed_headers,
            allow_credentials,
            expires_at: Instant::now() + Duration::from_secs(max_age_secs),
        };
        map.insert(key, entry);
    }

    /// Retrieve valid preflight entry.
    pub async fn get(&self, client_origin: &str, target_origin: &str) -> Option<PreflightEntry> {
        let mut map = self.entries.lock().await;
        if let Some(entry) = map.get(&(client_origin.to_string(), target_origin.to_string())) {
            if entry.is_fresh() {
                return Some(entry.clone());
            } else {
                map.remove(&(client_origin.to_string(), target_origin.to_string()));
            }
        }
        None
    }
}

/// CORS Gate orchestrator.
#[derive(Clone, Default)]
pub struct CorsGate {
    pub store: CorsStore,
}

impl CorsGate {
    pub fn new() -> Self {
        Self {
            store: CorsStore::new(),
        }
    }

    /// Check if target URL is same origin as client origin.
    pub fn is_same_origin(client_origin: &str, target_url: &Url) -> bool {
        let target_origin = get_origin(target_url);
        client_origin.trim_end_matches('/') == target_origin.trim_end_matches('/')
    }

    /// Evaluate request requirements prior to sending.
    pub async fn evaluate_request(
        &self,
        client_origin: &str,
        target_url: &Url,
        method: &str,
        headers: &HashMap<String, String>,
    ) -> CorsDecision {
        if Self::is_same_origin(client_origin, target_url) {
            return CorsDecision::SameOrigin;
        }

        let origin_header = client_origin.to_string();
        let target_origin = get_origin(target_url);
        let upper_method = method.to_uppercase();

        // Check if preflight is already cached
        if let Some(preflight) = self.store.get(client_origin, &target_origin).await {
            if preflight.allows_method(method) {
                let mut all_headers_allowed = true;
                for (name, val) in headers {
                    let lowered = name.to_lowercase();
                    if lowered == "user-agent"
                        || lowered == "host"
                        || lowered == "origin"
                        || lowered == "referer"
                        || lowered.starts_with("sec-fetch-")
                        || lowered.starts_with("sec-ch-ua")
                    {
                        continue;
                    }
                    if !is_safelisted_header(&lowered, val) && !preflight.allows_header(&lowered) {
                        all_headers_allowed = false;
                        break;
                    }
                }
                if all_headers_allowed {
                    return CorsDecision::PreflightCached { origin_header };
                }
            }
        }

        // Check if request is a CORS-safelisted simple request
        if is_simple_request(&upper_method, headers) {
            CorsDecision::SimpleRequest { origin_header }
        } else {
            CorsDecision::PreflightRequired { origin_header }
        }
    }

    /// Validate the final response against CORS policy.
    pub fn validate_response(
        client_origin: &str,
        with_credentials: bool,
        response_headers: &HashMap<String, String>,
    ) -> Result<(), CorsError> {
        let allow_origin = response_headers
            .get("access-control-allow-origin")
            .ok_or(CorsError::MissingAllowOrigin)?;

        let trimmed = allow_origin.trim();

        if trimmed == "*" {
            if with_credentials {
                return Err(CorsError::WildcardOriginWithCredentials);
            }
            return Ok(());
        }

        if trimmed != client_origin.trim_end_matches('/') {
            return Err(CorsError::OriginMismatch {
                expected: client_origin.to_string(),
                actual: trimmed.to_string(),
            });
        }

        Ok(())
    }
}

// ----------------------------------------------------------------------------
// N3: per-request CORS verdict over recorded evidence.
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CorsVerdict {
    /// Whether the recorded request, as evidence shows it, would be allowed to
    /// be read by page script at `client_origin`.
    pub allowed: bool,
    /// Reason string; never fabricated when evidence is absent.
    pub reason: String,
    /// "same-origin" when no CORS evaluation applies, otherwise "cross-origin".
    pub kind: String,
    /// The response's ACAO value ("" when absent).
    pub allow_origin: String,
    pub allow_credentials: bool,
    /// False only when the request legally required preflight and the verdict
    /// could not be granted; for granted requests this reflects server
    /// acceptance of the method that was actually issued.
    pub method_ok: bool,
    /// Whether this request legally required a preflight (non-simple).
    pub preflight_required: bool,
}

/// Compute the CORS verdict for one recorded request/response pair, exactly
/// the way the engine's own gates (`is_same_origin`, `is_simple_request`,
/// `validate_response`) would judge it.
pub fn cors_verdict(
    client_origin: &str,
    method: &str,
    target_url: &Url,
    request_headers: &HashMap<String, String>,
    response_headers: &HashMap<String, String>,
) -> CorsVerdict {
    let allow_origin = response_headers
        .get("access-control-allow-origin")
        .cloned()
        .unwrap_or_default();
    let allow_credentials = response_headers
        .get("access-control-allow-credentials")
        .map(|v| v.trim().eq_ignore_ascii_case("true"))
        .unwrap_or(false);

    if CorsGate::is_same_origin(client_origin, target_url) {
        return CorsVerdict {
            allowed: true,
            reason: "same-origin: CORS not enforced".into(),
            kind: "same-origin".into(),
            allow_origin,
            allow_credentials,
            method_ok: true,
            preflight_required: false,
        };
    }

    let preflight_required = !is_simple_request(method, request_headers);

    if allow_origin.trim().is_empty() {
        return CorsVerdict {
            allowed: false,
            reason: "blocked: response carries no Access-Control-Allow-Origin, so a cross-origin read is not allowed".into(),
            kind: "cross-origin".into(),
            allow_origin,
            allow_credentials,
            method_ok: false,
            preflight_required,
        };
    }

    let with_credentials = request_headers.keys().any(|k| {
        let k = k.to_lowercase();
        k == "cookie" || k == "authorization"
    });

    match CorsGate::validate_response(client_origin, with_credentials, response_headers) {
        Ok(()) => {
            let reason = if preflight_required {
                "allowed: cross-origin response grants the origin; non-simple request, preflight was served".into()
            } else {
                "allowed: cross-origin response grants origin".into()
            };
            CorsVerdict {
                allowed: true,
                reason,
                kind: "cross-origin".into(),
                allow_origin,
                allow_credentials,
                method_ok: true,
                preflight_required,
            }
        }
        Err(e) => {
            let method_ok = !matches!(
                e,
                CorsError::WildcardOriginWithCredentials
                    | CorsError::OriginMismatch { .. }
                    | CorsError::MissingAllowOrigin
            );
            CorsVerdict {
                allowed: false,
                reason: format!("blocked: {e}"),
                kind: "cross-origin".into(),
                allow_origin,
                allow_credentials,
                method_ok,
                preflight_required,
            }
        }
    }
}

/// Compute origin string `scheme://host:port` from a URL.
pub fn get_origin(url: &Url) -> String {
    format!(
        "{}://{}{}",
        url.scheme(),
        url.host_str().unwrap_or(""),
        url.port().map(|p| format!(":{}", p)).unwrap_or_default()
    )
}

/// WHATWG Fetch Specification: CORS-unsafe byte detection.
/// Controls (0x00-0x08, 0x0A-0x1F, 0x7F) and delimiters `"` `(` `)` `:` `<` `>` `?` `@` `[` `\` `]` `{` `}`.
pub fn is_cors_unsafe_byte(c: u8) -> bool {
    matches!(
        c,
        0..=0x08
            | 0x0A..=0x1F
            | b'"'
            | b'('
            | b')'
            | b':'
            | b'<'
            | b'>'
            | b'?'
            | b'@'
            | b'['
            | b'\\'
            | b']'
            | b'{'
            | b'}'
            | 0x7F
    )
}

/// Verify string contains no CORS-unsafe bytes.
pub fn has_no_cors_unsafe_bytes(value: &str) -> bool {
    !value.bytes().any(is_cors_unsafe_byte)
}

/// Safelisted characters for `Accept-Language` and `Content-Language`.
pub fn is_safelisted_language_value(value: &str) -> bool {
    value.bytes().all(|c| {
        matches!(
            c,
            b'0'..=b'9'
                | b'A'..=b'Z'
                | b'a'..=b'z'
                | b' '
                | b'*'
                | b','
                | b'-'
                | b'.'
                | b';'
                | b'='
        )
    })
}

/// Verify whether a Content-Type MIME type is CORS-safelisted.
pub fn is_safelisted_content_type(value: &str) -> bool {
    let semi = value.find(';').unwrap_or(value.len());
    let mime = value[..semi].trim().to_lowercase();
    (mime == "application/x-www-form-urlencoded"
        || mime == "multipart/form-data"
        || mime == "text/plain")
        && has_no_cors_unsafe_bytes(value)
}

/// Check if a specific header name and value is CORS-safelisted per WHATWG.
pub fn is_safelisted_header(name: &str, value: &str) -> bool {
    // 1. Length constraint: safelisted request header values cannot exceed 128 bytes
    if value.len() > 128 {
        return false;
    }

    let lk = name.to_lowercase();
    match lk.as_str() {
        "accept" => has_no_cors_unsafe_bytes(value),
        "accept-language" | "content-language" => is_safelisted_language_value(value),
        "content-type" => is_safelisted_content_type(value),
        _ => false,
    }
}

/// Check if an HTTP method is CORS-safelisted.
pub fn is_safelisted_method(method: &str) -> bool {
    let m = method.to_uppercase();
    m == "GET" || m == "HEAD" || m == "POST"
}

/// Determine whether a request is a W3C CORS-safelisted simple request.
pub fn is_simple_request(method: &str, headers: &HashMap<String, String>) -> bool {
    // 1. Method must be GET, HEAD, or POST
    if !is_safelisted_method(method) {
        return false;
    }

    // 2. Authored headers must only be CORS-safelisted
    for (k, v) in headers {
        let lk = k.to_lowercase();
        // Skip browser/agent internal infrastructure headers
        if lk == "user-agent"
            || lk == "host"
            || lk == "origin"
            || lk == "referer"
            || lk.starts_with("sec-fetch-")
            || lk.starts_with("sec-ch-ua")
        {
            continue;
        }

        if !is_safelisted_header(&lk, v) {
            return false;
        }
    }

    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_same_origin_detection() {
        let url = Url::parse("https://example.com/api/data").unwrap();
        assert!(CorsGate::is_same_origin("https://example.com", &url));
        assert!(!CorsGate::is_same_origin("https://other.com", &url));
    }

    #[tokio::test]
    async fn test_simple_vs_preflight() {
        let gate = CorsGate::new();
        let target = Url::parse("https://api.partner.com/items").unwrap();
        let client = "https://my-app.com";

        // Simple GET request with Accept header
        let mut simple_headers = HashMap::new();
        simple_headers.insert("accept".into(), "text/plain".into());
        let d1 = gate.evaluate_request(client, &target, "GET", &simple_headers).await;
        assert!(matches!(d1, CorsDecision::SimpleRequest { .. }));

        // Non-simple request with custom header (Authorization)
        let mut complex_headers = HashMap::new();
        complex_headers.insert("authorization".into(), "Bearer 12345".into());
        let d2 = gate.evaluate_request(client, &target, "GET", &complex_headers).await;
        assert!(matches!(d2, CorsDecision::PreflightRequired { .. }));

        // PUT request always requires preflight
        let d3 = gate.evaluate_request(client, &target, "PUT", &simple_headers).await;
        assert!(matches!(d3, CorsDecision::PreflightRequired { .. }));
    }

    #[tokio::test]
    async fn test_preflight_caching() {
        let gate = CorsGate::new();
        let target = Url::parse("https://api.partner.com/items").unwrap();
        let client = "https://my-app.com";
        let target_origin = "https://api.partner.com";

        let mut preflight_headers = HashMap::new();
        preflight_headers.insert("access-control-allow-methods".into(), "GET, POST, PUT".into());
        preflight_headers.insert("access-control-allow-headers".into(), "authorization".into());
        preflight_headers.insert("access-control-max-age".into(), "60".into());

        gate.store.record_preflight(client, target_origin, &preflight_headers).await;

        let mut headers = HashMap::new();
        headers.insert("authorization".into(), "Bearer token".into());
        let d = gate.evaluate_request(client, &target, "PUT", &headers).await;
        assert!(matches!(d, CorsDecision::PreflightCached { .. }));
    }

    #[test]
    fn test_response_validation() {
        let client = "https://app.com";

        // Wildcard allowed without credentials
        let mut h1 = HashMap::new();
        h1.insert("access-control-allow-origin".into(), "*".into());
        assert!(CorsGate::validate_response(client, false, &h1).is_ok());

        // Wildcard REJECTED with credentials
        assert!(matches!(
            CorsGate::validate_response(client, true, &h1),
            Err(CorsError::WildcardOriginWithCredentials)
        ));

        // Explicit origin matching
        let mut h2 = HashMap::new();
        h2.insert("access-control-allow-origin".into(), "https://app.com".into());
        assert!(CorsGate::validate_response(client, true, &h2).is_ok());

        // Mismatched origin
        let mut h3 = HashMap::new();
        h3.insert("access-control-allow-origin".into(), "https://attacker.com".into());
        assert!(matches!(
            CorsGate::validate_response(client, false, &h3),
            Err(CorsError::OriginMismatch { .. })
        ));
    }

    #[test]
    fn test_whatwg_unsafe_bytes() {
        assert!(is_cors_unsafe_byte(0x00));
        assert!(is_cors_unsafe_byte(0x08));
        assert!(is_cors_unsafe_byte(0x0A));
        assert!(is_cors_unsafe_byte(b'"'));
        assert!(is_cors_unsafe_byte(b'<'));
        assert!(is_cors_unsafe_byte(b'>'));
        assert!(is_cors_unsafe_byte(b'{'));
        assert!(is_cors_unsafe_byte(0x7F));

        assert!(!is_cors_unsafe_byte(b'a'));
        assert!(!is_cors_unsafe_byte(b'/'));
        assert!(!is_cors_unsafe_byte(b'*'));

        assert!(!has_no_cors_unsafe_bytes("text/html; charset=\"utf-8\""));
        assert!(has_no_cors_unsafe_bytes("text/html; charset=utf-8"));
    }

    #[test]
    fn test_safelisted_header_length_cap() {
        let ok_val = "a".repeat(128);
        let too_long = "a".repeat(129);

        assert!(is_safelisted_header("accept", &ok_val));
        assert!(!is_safelisted_header("accept", &too_long));
    }

    #[test]
    fn test_language_value_validation() {
        assert!(is_safelisted_language_value("en-US,en;q=0.9,fr;q=0.8"));
        assert!(!is_safelisted_language_value("en-US\nEvilHeader: true"));
        assert!(!is_safelisted_language_value("en-US<script>"));
    }

    #[test]
    fn test_cors_verdict_missing_acao_blocks_cross_origin() {
        let target = Url::parse("https://api.partner.com/chat").unwrap();
        let headers = HashMap::new();
        let resp = HashMap::new();
        let v = cors_verdict("https://chat.example.com", "POST", &target, &headers, &resp);
        assert_eq!(v.kind, "cross-origin");
        assert!(!v.allowed);
        assert!(v.reason.contains("no Access-Control-Allow-Origin"));
    }

    #[test]
    fn test_cors_verdict_wildcard_grants_simple_request() {
        let target = Url::parse("https://api.partner.com/items").unwrap();
        let headers = HashMap::new();
        let mut resp = HashMap::new();
        resp.insert("access-control-allow-origin".into(), "*".into());
        let v = cors_verdict("https://chat.example.com", "GET", &target, &headers, &resp);
        assert!(v.allowed);
        assert_eq!(v.allow_origin, "*");
        assert!(!v.preflight_required);
    }

    #[test]
    fn test_cors_verdict_credentials_with_wildcard_blocked() {
        let target = Url::parse("https://api.partner.com/chat").unwrap();
        let mut req = HashMap::new();
        req.insert("authorization".into(), "Bearer x".into());
        let mut resp = HashMap::new();
        resp.insert("access-control-allow-origin".into(), "*".into());
        resp.insert("access-control-allow-credentials".into(), "true".into());
        let v = cors_verdict("https://chat.example.com", "POST", &target, &req, &resp);
        assert!(!v.allowed);
        assert!(v.reason.contains("wildcard"));
        assert!(v.preflight_required);
    }

    #[test]
    fn test_cors_verdict_matching_origin_grants_preflighted_request() {
        let target = Url::parse("https://api.partner.com/chat").unwrap();
        let mut req = HashMap::new();
        req.insert("authorization".into(), "Bearer x".into());
        let mut resp = HashMap::new();
        resp.insert("access-control-allow-origin".into(), "https://chat.example.com".into());
        resp.insert("access-control-allow-credentials".into(), "true".into());
        let v = cors_verdict("https://chat.example.com", "POST", &target, &req, &resp);
        assert!(v.allowed);
        assert!(v.preflight_required);
        assert!(v.allow_credentials);
        assert!(v.method_ok);
    }

    #[test]
    fn test_cors_verdict_same_origin_no_cors() {
        let target = Url::parse("https://chat.example.com/api").unwrap();
        let resp = HashMap::new();
        let v = cors_verdict("https://chat.example.com", "GET", &target, &HashMap::new(), &resp);
        assert!(v.allowed);
        assert_eq!(v.kind, "same-origin");
    }

    #[test]
    fn test_content_type_safelist_variants() {
        assert!(is_safelisted_content_type("text/plain"));
        assert!(is_safelisted_content_type("application/x-www-form-urlencoded; charset=utf-8"));
        assert!(is_safelisted_content_type("multipart/form-data; boundary=something"));

        // JSON or XML are NOT CORS-safelisted and MUST trigger preflight
        assert!(!is_safelisted_content_type("application/json"));
        assert!(!is_safelisted_content_type("application/xml"));
    }

    #[tokio::test]
    async fn test_whatwg_preflight_grant_merging() {
        let gate = CorsGate::new();
        let target = Url::parse("https://api.partner.com/items").unwrap();
        let client = "https://my-app.com";
        let target_origin = "https://api.partner.com";

        // Grant 1: GET and PUT, custom header x-custom-a, max-age 300
        let mut h1 = HashMap::new();
        h1.insert("access-control-allow-methods".into(), "GET, PUT".into());
        h1.insert("access-control-allow-headers".into(), "x-custom-a".into());
        h1.insert("access-control-max-age".into(), "300".into());
        gate.store.record_preflight(client, target_origin, &h1).await;

        // Grant 2: DELETE, custom header x-custom-b, max-age 60
        let mut h2 = HashMap::new();
        h2.insert("access-control-allow-methods".into(), "DELETE".into());
        h2.insert("access-control-allow-headers".into(), "x-custom-b".into());
        h2.insert("access-control-max-age".into(), "60".into());
        gate.store.record_preflight(client, target_origin, &h2).await;

        // Both PUT and DELETE must now be cached
        let mut req_put = HashMap::new();
        req_put.insert("x-custom-a".into(), "val".into());
        assert!(matches!(
            gate.evaluate_request(client, &target, "PUT", &req_put).await,
            CorsDecision::PreflightCached { .. }
        ));

        let mut req_del = HashMap::new();
        req_del.insert("x-custom-b".into(), "val".into());
        assert!(matches!(
            gate.evaluate_request(client, &target, "DELETE", &req_del).await,
            CorsDecision::PreflightCached { .. }
        ));
    }

    #[tokio::test]
    async fn test_whatwg_authorization_wildcard_exemption() {
        let gate = CorsGate::new();
        let target = Url::parse("https://api.partner.com/items").unwrap();
        let client = "https://my-app.com";
        let target_origin = "https://api.partner.com";

        // Preflight grants wildcard '*' for headers
        let mut h = HashMap::new();
        h.insert("access-control-allow-methods".into(), "GET, POST".into());
        h.insert("access-control-allow-headers".into(), "*".into());
        gate.store.record_preflight(client, target_origin, &h).await;

        // Arbitrary custom header is covered by '*'
        let mut req_custom = HashMap::new();
        req_custom.insert("x-random-header".into(), "val".into());
        assert!(matches!(
            gate.evaluate_request(client, &target, "POST", &req_custom).await,
            CorsDecision::PreflightCached { .. }
        ));

        // BUT Authorization is NOT covered by '*' per WHATWG Fetch §3.2.3
        let mut req_auth = HashMap::new();
        req_auth.insert("authorization".into(), "Bearer secret".into());
        assert!(matches!(
            gate.evaluate_request(client, &target, "POST", &req_auth).await,
            CorsDecision::PreflightRequired { .. }
        ));
    }
}

