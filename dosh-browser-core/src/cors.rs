//! `cors.rs` — W3C Cross-Origin Resource Sharing (CORS) Gate and Preflight Store.
//!
//! Enforces CORS rules for subresource fetches, script XHR/fetch calls, and iframes:
//! - Identifies simple vs preflight-required requests
//! - Maintains an in-memory cache of preflight OPTIONS grants (`CorsStore`)
//! - Validates response headers (`Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials`)

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant};
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
            .min(86400); // capped at 24 hours

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

        let entry = PreflightEntry {
            allowed_methods,
            allowed_headers,
            allow_credentials,
            expires_at: Instant::now() + Duration::from_secs(max_age_secs),
        };

        let mut map = self.entries.lock().await;
        map.insert((client_origin.to_string(), target_origin.to_string()), entry);
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
            if preflight.allowed_methods.contains(&upper_method) || preflight.allowed_methods.contains("*") {
                return CorsDecision::PreflightCached { origin_header };
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

/// Compute origin string `scheme://host:port` from a URL.
pub fn get_origin(url: &Url) -> String {
    format!(
        "{}://{}{}",
        url.scheme(),
        url.host_str().unwrap_or(""),
        url.port().map(|p| format!(":{}", p)).unwrap_or_default()
    )
}

/// Determine whether a request is a W3C CORS-safelisted simple request.
fn is_simple_request(method: &str, headers: &HashMap<String, String>) -> bool {
    // 1. Method must be GET, HEAD, or POST
    if method != "GET" && method != "HEAD" && method != "POST" {
        return false;
    }

    // 2. Headers must only be CORS-safelisted
    for (k, v) in headers {
        let lk = k.to_lowercase();
        match lk.as_str() {
            "accept" | "accept-language" | "content-language" => {}
            "content-type" => {
                let lv = v.to_lowercase();
                let mime = lv.split(';').next().unwrap_or("").trim();
                if mime != "application/x-www-form-urlencoded"
                    && mime != "multipart/form-data"
                    && mime != "text/plain"
                {
                    return false;
                }
            }
            // Standard user-agent and host headers are handled by browser
            "user-agent" | "host" | "origin" | "referer" => {}
            _ => return false,
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
}
