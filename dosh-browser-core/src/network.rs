//! `network.rs` — Central Network Coordinator Pipeline for Dosh-Browser.
//!
//! Integrates all network capabilities into an orchestrated, agent-grade pipeline:
//! 1. SSRF & Private IP Guard (`IpFilter`)
//! 2. SafeBrowsing & Malicious Host Filtering
//! 3. Tor / .onion Address Refusal
//! 4. Third-Party Tracker & Ad Blocking (`TrackerGraph`)
//! 5. RFC 9309 Robots.txt Compliance (`RobotsStore`)
//! 6. Origin Rate Limiting via GCRA Leaky Bucket (`RateLimiter`)
//! 7. W3C Cross-Origin Resource Sharing (`CorsGate` & `CorsStore`)
//! 8. RFC 9111 HTTP Cache with SQLite Persistence (`SqliteCache`)
//! 9. Concurrent Request Coalescing (`SingleFlight`)
//! 10. RFC 9421 Cryptographic Bot Authentication (`WebBotAuth`)
//! 11. Causal Network Ledger & Telemetry (`NetworkLedger`)
//! 12. HTTP/2 Transport with Chrome 124 TLS matching (`reqwest`)

use crate::cache::{now_secs, CacheEntry};
use crate::cache_sqlite::SqliteCache;
use crate::cors::{CorsDecision, CorsError, CorsGate};
use crate::fetch::{classify, decode_body, navigate_headers, FetchResult};
use crate::ip_filter::{IpFilter, IpFilterError};
use crate::onion::onion_refusal;
use crate::rate_limiter::RateLimiter;
use crate::robots::RobotsStore;
use crate::safebrowsing::is_blocked_host;
use crate::singleflight::SingleFlight;
use crate::telemetry::{NetworkLedger, NetworkRecord, SealedRequest};
use crate::trackers::TrackerGraph;
use crate::web_bot_auth::{sign_request, BotAuthConfig};

use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use url::Url;

/// Configuration for the unified NetworkEngine.
#[derive(Clone)]
pub struct NetworkConfig {
    pub block_private_ips: bool,
    pub enable_rate_limiting: bool,
    pub rate_limit_interval_ms: u64,
    pub rate_limit_burst: u32,
    pub enable_cache: bool,
    pub enable_robots: bool,
    pub enable_tracker_blocking: bool,
    pub bot_auth: Option<BotAuthConfig>,
    pub max_bytes: usize,
    pub user_agent: String,
}

impl Default for NetworkConfig {
    fn default() -> Self {
        Self {
            block_private_ips: true,
            enable_rate_limiting: true,
            rate_limit_interval_ms: 50,
            rate_limit_burst: 5,
            enable_cache: true,
            enable_robots: false,
            enable_tracker_blocking: true,
            bot_auth: None,
            max_bytes: 10 * 1024 * 1024, // 10 MB cap
            user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36".to_string(),
        }
    }
}

/// Request descriptor for the network pipeline.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetworkRequest {
    pub url: String,
    pub method: String,
    pub headers: HashMap<String, String>,
    pub body: Option<String>,
    pub client_origin: Option<String>,
    pub caused_by: Option<String>,
    pub is_subresource: bool,
    /// Sanitized JS call-site hint for the launch point (N9).
    pub initiator_stack: Vec<String>,
}

impl NetworkRequest {
    pub fn new(url: &str) -> Self {
        Self {
            url: url.to_string(),
            method: "GET".to_string(),
            headers: HashMap::new(),
            body: None,
            client_origin: None,
            caused_by: None,
            is_subresource: false,
            initiator_stack: Vec::new(),
        }
    }
}

/// Pipeline error categories.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NetworkError {
    InvalidUrl(String),
    BlockedSsrf(IpFilterError),
    BlockedOnion(String),
    BlockedMaliciousHost(String),
    BlockedTracker(String),
    BlockedRobots(String),
    BlockedCors(CorsError),
    HttpTransport(String),
}

impl std::fmt::Display for NetworkError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidUrl(u) => write!(f, "Network error: Invalid URL: {}", u),
            Self::BlockedSsrf(e) => write!(f, "Network error: Blocked by SSRF filter: {}", e),
            Self::BlockedOnion(msg) => write!(f, "Network error: Blocked .onion address: {}", msg),
            Self::BlockedMaliciousHost(h) => write!(f, "Network error: Blocked malicious host: {}", h),
            Self::BlockedTracker(e) => write!(f, "Network error: Blocked tracker: {}", e),
            Self::BlockedRobots(r) => write!(f, "Network error: Blocked by robots.txt: {}", r),
            Self::BlockedCors(c) => write!(f, "Network error: CORS violation: {}", c),
            Self::HttpTransport(e) => write!(f, "Network error: HTTP transport failure: {}", e),
        }
    }
}

impl std::error::Error for NetworkError {}

/// Central Network Coordinator for Dosh-Browser.
#[derive(Clone)]
pub struct NetworkEngine {
    pub config: NetworkConfig,
    client: Client,
    pub cookie_jar: Arc<reqwest::cookie::Jar>,
    /// Retry client with `danger_accept_invalid_certs(true)`, used only for
    /// requests to hosts listed in `WEBRESEARCH_TLS_PROCEED_HOSTS` (an explicit
    /// operator decision; recorded in the network ledger as proceeded-unverified).
    insecure_client: Option<Client>,
    pub ip_filter: IpFilter,
    pub rate_limiter: RateLimiter,
    pub singleflight: SingleFlight<FetchResult, String>,
    pub robots: RobotsStore,
    pub cors: CorsGate,
    pub cache: SqliteCache,
    pub trackers: Arc<TrackerGraph>,
    pub ledger: Arc<Mutex<NetworkLedger>>,
}

impl Default for NetworkEngine {
    fn default() -> Self {
        Self::new(NetworkConfig::default())
    }
}

impl NetworkEngine {
    /// Initialize a new NetworkEngine with the given configuration.
    pub fn new(config: NetworkConfig) -> Self {
        let cookie_jar = Arc::new(reqwest::cookie::Jar::default());
        let client = Client::builder()
            .cookie_provider(Arc::clone(&cookie_jar))
            .build()
            .unwrap_or_else(|_| Client::new());

        // Build an accept-invalid-certs fallback client only when the operator
        // has explicitly listed proceed hosts. Per-request host matching is
        // delegated to tls_policy::decision_for_host at dispatch time.
        let insecure_client = if std::env::var("WEBRESEARCH_TLS_PROCEED_HOSTS").is_ok() {
            Client::builder()
                .cookie_provider(Arc::clone(&cookie_jar))
                .danger_accept_invalid_certs(true)
                .build()
                .ok()
        } else {
            None
        };

        let ip_filter = IpFilter::new(config.block_private_ips);
        let rate_limiter = RateLimiter::new(
            config.rate_limit_interval_ms,
            config.rate_limit_burst,
        );
        let singleflight = SingleFlight::new();
        let robots = RobotsStore::new();
        let cors = CorsGate::new();
        let cache = SqliteCache::new(None).expect("Failed to initialize SQLite cache");
        let trackers = Arc::new(TrackerGraph::new());
        // Shared with the owning Session (same Arc): 1000 records so the
        // shell's `net` view keeps the deep history it always documented.
        let ledger = Arc::new(Mutex::new(NetworkLedger::new(1000, true)));

        Self {
            config,
            client,
            cookie_jar,
            insecure_client,
            ip_filter,
            rate_limiter,
            singleflight,
            robots,
            cors,
            cache,
            trackers,
            ledger,
        }
    }

    /// Inject raw cookie header string for a URL into the live HTTP/2 cookie jar.
    pub fn set_cookie(&self, cookie_str: &str, url: &Url) {
        self.cookie_jar.add_cookie_str(cookie_str, url);
    }

    /// Primary entry point: process a request through the full network pipeline.
    pub async fn fetch(&self, req: NetworkRequest) -> Result<FetchResult, NetworkError> {
        let parsed_url = Url::parse(&req.url)
            .map_err(|e| NetworkError::InvalidUrl(format!("{}: {}", req.url, e)))?;

        let host = parsed_url
            .host_str()
            .ok_or_else(|| NetworkError::InvalidUrl("Missing host in URL".into()))?;

        // 1. Tor .onion check
        if host.ends_with(".onion") {
            let (_, msg) = onion_refusal(host);
            return Err(NetworkError::BlockedOnion(msg));
        }

        // 2. SafeBrowsing malicious host check
        if is_blocked_host(host) {
            return Err(NetworkError::BlockedMaliciousHost(host.to_string()));
        }

        // 3. SSRF & Private IP check
        if let Err(e) = self.ip_filter.check_url(&parsed_url) {
            return Err(NetworkError::BlockedSsrf(e));
        }

        // 4. Tracker & Ad blocking check for subresources
        if req.is_subresource && self.config.enable_tracker_blocking {
            let context_url = req.client_origin.as_deref().unwrap_or(&req.url);
            let decision = self.trackers.evaluate(&req.url, context_url);
            if decision.status == "BLOCKED" {
                if !decision.surrogate.is_empty() {
                    // Return functional surrogate script
                    return Ok(FetchResult {
                        url: req.url.clone(),
                        final_url: req.url.clone(),
                        status: 200,
                        content_type: "application/javascript".to_string(),
                        headers: HashMap::new(),
                        kind: "other".to_string(),
                        body: decision.surrogate,
                        truncated: false,
                    });
                }
                return Err(NetworkError::BlockedTracker(decision.entity));
            }
        }

        // 5. Robots.txt policy gate
        if self.config.enable_robots {
            if !self.robots.check_url(&parsed_url, &self.config.user_agent).await {
                return Err(NetworkError::BlockedRobots(format!(
                    "Path {} disallowed for agent",
                    parsed_url.path()
                )));
            }
        }

        // 6. Origin Rate Limiter (GCRA Leaky Bucket)
        if self.config.enable_rate_limiting {
            self.rate_limiter.acquire(host).await;
        }

        // 7. W3C CORS Gate
        if let Some(ref client_origin) = req.client_origin {
            let decision = self
                .cors
                .evaluate_request(client_origin, &parsed_url, &req.method, &req.headers)
                .await;

            match decision {
                CorsDecision::PreflightRequired { .. } => {
                    // Execute preflight OPTIONS request
                    self.execute_preflight(client_origin, &parsed_url).await?;
                }
                CorsDecision::SameOrigin
                | CorsDecision::SimpleRequest { .. }
                | CorsDecision::PreflightCached { .. } => {}
            }
        }

        // 8. RFC 9111 HTTP Cache Lookup
        let mut cached_entry: Option<CacheEntry> = None;
        if self.config.enable_cache && req.method.eq_ignore_ascii_case("GET") {
            if let Some(entry) = self.cache.get(&req.url) {
                if entry.is_fresh(None) {
                    let mut ledger = self.ledger.lock().await;
                    ledger.append(NetworkRecord {
                        source: "cache".into(),
                        url: req.url.clone(),
                        method: req.method.clone(),
                        status: Some(entry.status),
                        request_headers: req.headers.clone(),
                        response_headers: entry.resp_headers.clone(),
                        state: "complete".into(),
                        network_sent: Some(false),
                        bytes: entry.body.len(),
                        content_type: entry.resp_headers.get("content-type").cloned().unwrap_or_default(),
                        ..Default::default()
                    });

                    let ct = entry.resp_headers.get("content-type").cloned().unwrap_or_default();
                    let body = decode_body(&entry.body, &ct);
                    let kind = classify(&ct).to_string();

                    return Ok(FetchResult {
                        url: req.url.clone(),
                        final_url: entry.url,
                        status: entry.status,
                        content_type: ct,
                        headers: entry.resp_headers,
                        kind,
                        body,
                        truncated: false,
                    });
                } else {
                    cached_entry = Some(entry);
                }
            }
        }

        // 9. SingleFlight Request Coalescing & Network Dispatch
        let sf_key = format!("{}:{}", req.method.to_uppercase(), req.url);
        let this = self.clone();
        let req_clone = req.clone();

        self.singleflight
            .execute(&sf_key, move || async move {
                this.dispatch_http_request(req_clone, cached_entry).await
            })
            .await
            .map_err(NetworkError::HttpTransport)
    }

    /// Internal network dispatch for non-cached or coalesced requests.
    async fn dispatch_http_request(
        &self,
        req: NetworkRequest,
        stale_cache: Option<CacheEntry>,
    ) -> Result<FetchResult, String> {
        let mut req_headers = navigate_headers(&req.url);

        // Inject custom user agent
        req_headers.insert(
            "user-agent",
            HeaderValue::from_str(&self.config.user_agent).unwrap_or(HeaderValue::from_static("dosh-browser")),
        );

        // Inject conditional cache validation headers if stale cache entry is available
        if let Some(ref entry) = stale_cache {
            if let Some(etag) = entry.resp_headers.get("etag") {
                if let Ok(val) = HeaderValue::from_str(etag) {
                    req_headers.insert("if-none-match", val);
                }
            }
            if let Some(lm) = entry.resp_headers.get("last-modified") {
                if let Ok(val) = HeaderValue::from_str(lm) {
                    req_headers.insert("if-modified-since", val);
                }
            }
        }

        // Apply caller-supplied headers
        for (k, v) in &req.headers {
            if let (Ok(kh), Ok(vh)) = (HeaderName::try_from(k.as_str()), HeaderValue::try_from(v.as_str())) {
                req_headers.insert(kh, vh);
            }
        }

        // Inject RFC 9421 Bot Authentication Signatures if configured
        if let Some(ref bot_config) = self.config.bot_auth {
            let parsed_url = Url::parse(&req.url).map_err(|e| e.to_string())?;
            let authority = parsed_url.host_str().unwrap_or("");
            if let Ok(signed) = sign_request(bot_config, &req.method, &req.url, authority, None) {
                if let Ok(v) = HeaderValue::from_str(&signed.signature_agent) {
                    req_headers.insert("signature-agent", v);
                }
                if let Ok(v) = HeaderValue::from_str(&signed.signature_input) {
                    req_headers.insert("signature-input", v);
                }
                if let Ok(v) = HeaderValue::from_str(&signed.signature) {
                    req_headers.insert("signature", v);
                }
            }
        }

        // Build reqwest request
        let method = req.method.parse().unwrap_or(reqwest::Method::GET);
        let mut builder = self.client.request(method.clone(), &req.url).headers(req_headers.clone());
        if let Some(ref b) = req.body {
            builder = builder.body(b.clone());
        }

        // TLS proceed intercept: if the primary client fails with a TLS error
        // and the host is explicitly allowed, retry once with the insecure client.
        let parsed = Url::parse(&req.url).map_err(|e| e.to_string())?;
        let host = parsed.host_str().unwrap_or("");
        // N4: timing起点 is the transport dispatch itself.
        let t0_ms = crate::telemetry::epoch_ms_f64();
        let resp = match builder.send().await {
            Ok(r) => r,
            Err(err) => {
                // reqwest's Display prints only the top-level message; rustls/TLS
                // details live in the source chain. Walk it for honest classification.
                let mut err_text = err.to_string();
                let mut src: Option<&(dyn std::error::Error + 'static)> = std::error::Error::source(&err);
                while let Some(e) = src {
                    err_text.push_str(&format!(": {}", e));
                    src = e.source();
                }
                if let Some(interstitial) = crate::tls_policy::handle_tls_error(host, &err_text) {
                    if interstitial.decision == "proceed" {
                        let insecure = self.insecure_client
                            .as_ref()
                            .ok_or_else(|| format!("TLS proceed decision but no insecure client available for {}", host))?;
                        let mut builder2 = insecure.request(method, &req.url).headers(req_headers.clone());
                        if let Some(ref b) = req.body {
                            builder2 = builder2.body(b.clone());
                        }
                        let resp2 = builder2.send().await.map_err(|e2| e2.to_string())?;
                        // Attach x-webresearch-tls header to mark the response
                        return self.build_fetch_result(req, resp2, Some(format!("proceeded-unverified:{}", interstitial.kind)), stale_cache, t0_ms).await;
                    }
                }
                return Err(err_text);
            }
        };

        self.build_fetch_result(req, resp, None, stale_cache, t0_ms).await
    }

    /// Common path for building a FetchResult from a successful reqwest Response.
    /// Optionally injects an `x-webresearch-tls` header (e.g. "proceeded-unverified:untrusted-chain").
    async fn build_fetch_result(
        &self,
        req: NetworkRequest,
        resp: reqwest::Response,
        tls_header: Option<String>,
        stale_cache: Option<CacheEntry>,
        t0_ms: f64,
    ) -> Result<FetchResult, String> {
        let status = resp.status().as_u16();
        let final_url = resp.url().as_str().to_string();

        let mut resp_headers: HashMap<String, String> = resp
            .headers()
            .iter()
            .map(|(k, v)| (k.as_str().to_string(), v.to_str().unwrap_or("").to_string()))
            .collect();

        if let Some(tls_val) = tls_header {
            resp_headers.insert("x-webresearch-tls".to_string(), tls_val);
        }

        let content_type = resp_headers
            .get("content-type")
            .cloned()
            .unwrap_or_default();

        // Check for 304 Not Modified
        if status == 304 {
            if let Some(entry) = stale_cache {
                let mut updated = entry.clone();
                updated.stored_at = now_secs();
                self.cache.store(&updated);

                let body = decode_body(&updated.body, &content_type);
                let kind = classify(&content_type).to_string();

                return Ok(FetchResult {
                    url: req.url.clone(),
                    final_url,
                    status: 200,
                    content_type,
                    headers: updated.resp_headers,
                    kind,
                    body,
                    truncated: false,
                });
            }
        }

        // ---- N1/N4: chunked body read with real arrival timestamps --------
        // Each chunk's byte-end offset + epoch-ms is recorded so SSE event
        // boundaries can be stamped with when they actually arrived. This is
        // measured, not simulated.
        let mut raw_bytes: Vec<u8> = Vec::new();
        let mut arrivals: Vec<(usize, f64)> = Vec::new();
        let mut ttfb_ms: Option<f64> = None;
        {
            use futures_util::StreamExt as _;
            let mut chunk_stream = resp;
            while let Some(chunk) = chunk_stream
                .chunk()
                .await
                .map_err(|e| e.to_string())?
            {
                let now = crate::telemetry::epoch_ms_f64();
                if ttfb_ms.is_none() {
                    ttfb_ms = Some(now - t0_ms);
                }
                raw_bytes.extend_from_slice(&chunk);
                arrivals.push((raw_bytes.len(), now));
            }
        }
        let duration_ms = Some(crate::telemetry::epoch_ms_f64() - t0_ms);
        let truncated = raw_bytes.len() > self.config.max_bytes;
        let body_slice = if truncated {
            &raw_bytes[..self.config.max_bytes]
        } else {
            &raw_bytes[..]
        };

        // Cache response if eligible
        if self.config.enable_cache && status == 200 && req.method.eq_ignore_ascii_case("GET") && !truncated {
            self.cache.store(&CacheEntry {
                url: req.url.clone(),
                status,
                resp_headers: resp_headers.clone(),
                body: raw_bytes.to_vec(),
                stored_at: now_secs(),
                vary: HashMap::new(),
            });
        }

        // Validate CORS headers on response if origin was provided
        if let Some(ref client_origin) = req.client_origin {
            if !CorsGate::is_same_origin(client_origin, &Url::parse(&req.url).unwrap()) {
                CorsGate::validate_response(client_origin, false, &resp_headers)
                    .map_err(|e| e.to_string())?;
            }
        }

        // Record in telemetry ledger with sealed (unredacted) transport
        // evidence for authorized replay/decode/reveal, measured timing, and
        // arrival-stamped SSE events.
        {
            let mut sealed = SealedRequest {
                record_id: 0,
                url: req.url.clone(),
                method: req.method.clone(),
                headers: req.headers.clone(),
                body: req.body.clone().unwrap_or_default().into_bytes(),
                response_body: raw_bytes
                    .iter()
                    .take(crate::telemetry::SEALED_MAX_BYTES_DEFAULT)
                    .copied()
                    .collect(),
                content_type: content_type.clone(),
            };
            let stream_events = if content_type.to_lowercase().contains("text/event-stream") {
                crate::telemetry::parse_sse_with_arrivals(
                    &String::from_utf8_lossy(&raw_bytes),
                    &arrivals,
                )
            } else {
                Vec::new()
            };
            let mut ledger = self.ledger.lock().await;
            ledger.append_with_sealed(
                NetworkRecord {
                    source: "network".into(),
                    url: req.url.clone(),
                    method: req.method.clone(),
                    status: Some(status),
                    request_headers: req.headers.clone(),
                    response_headers: resp_headers.clone(),
                    state: "complete".into(),
                    network_sent: Some(true),
                    bytes: raw_bytes.len(),
                    content_type: content_type.clone(),
                    stream_events,
                    timing: Some(crate::telemetry::TimingBreakdown {
                        started_at_ms: t0_ms,
                        ttfb_ms,
                        duration_ms,
                    }),
                    initiator_stack: req.initiator_stack.clone(),
                    truncated,
                    ..Default::default()
                },
                Some(std::mem::take(&mut sealed)),
            );
        }

        let body = decode_body(body_slice, &content_type);
        let kind = classify(&content_type).to_string();

        Ok(FetchResult {
            url: req.url,
            final_url,
            status,
            content_type,
            headers: resp_headers,
            kind,
            body,
            truncated,
        })
    }

    /// Execute a preflight OPTIONS request for CORS.
    async fn execute_preflight(&self, client_origin: &str, target_url: &Url) -> Result<(), NetworkError> {
        let mut preflight_headers = HeaderMap::new();
        preflight_headers.insert("origin", HeaderValue::from_str(client_origin).unwrap());
        preflight_headers.insert("access-control-request-method", HeaderValue::from_static("GET"));

        let resp = self
            .client
            .request(reqwest::Method::OPTIONS, target_url.as_str())
            .headers(preflight_headers)
            .send()
            .await
            .map_err(|e| NetworkError::HttpTransport(format!("Preflight failed: {}", e)))?;

        let headers: HashMap<String, String> = resp
            .headers()
            .iter()
            .map(|(k, v)| (k.as_str().to_string(), v.to_str().unwrap_or("").to_string()))
            .collect();

        let target_origin = crate::cors::get_origin(target_url);
        self.cors
            .store
            .record_preflight(client_origin, &target_origin, &headers)
            .await;

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::robots::RobotsPolicy;

    #[tokio::test]
    async fn test_network_engine_ssrf_blocked() {
        let engine = NetworkEngine::default();

        let req1 = NetworkRequest::new("http://127.0.0.1:8080/admin");
        let res1 = engine.fetch(req1).await;
        assert!(matches!(res1, Err(NetworkError::BlockedSsrf(_))));

        let req2 = NetworkRequest::new("http://169.254.169.254/latest/meta-data/");
        let res2 = engine.fetch(req2).await;
        assert!(matches!(res2, Err(NetworkError::BlockedSsrf(_))));

        let req3 = NetworkRequest::new("http://localhost/secret");
        let res3 = engine.fetch(req3).await;
        assert!(matches!(res3, Err(NetworkError::BlockedSsrf(_))));
    }

    #[tokio::test]
    async fn test_network_engine_onion_blocked() {
        let engine = NetworkEngine::default();
        let req = NetworkRequest::new("http://mkxyobdwj6xy5kbpyyootrgdscfwzol5iy5ggtu6lb6xzcc5wdxynwad.onion/index.html");
        let res = engine.fetch(req).await;
        assert!(matches!(res, Err(NetworkError::BlockedOnion(_))));
    }

    #[tokio::test]
    async fn test_network_engine_tracker_surrogate() {
        let mut cfg = NetworkConfig::default();
        cfg.block_private_ips = false; // allow localhost/mocking
        let engine = NetworkEngine::new(cfg);

        let mut req = NetworkRequest::new("https://www.google-analytics.com/analytics.js");
        req.is_subresource = true;
        req.client_origin = Some("https://example.com".into());

        let res = engine.fetch(req).await.expect("Tracker surrogate should return successfully");
        assert_eq!(res.status, 200);
        assert!(res.body.contains("surrogate: google-analytics"));
    }

    #[tokio::test]
    async fn test_network_engine_robots_disallow() {
        let mut cfg = NetworkConfig::default();
        cfg.enable_robots = true;
        let engine = NetworkEngine::new(cfg);

        // Populate robots policy
        let origin = "https://restricted.example.com";
        let robots_txt = "User-agent: *\nDisallow: /no-agent/\n";
        engine.robots.set(origin, RobotsPolicy::parse(robots_txt)).await;

        let req = NetworkRequest::new("https://restricted.example.com/no-agent/confidential.html");
        let res = engine.fetch(req).await;
        assert!(matches!(res, Err(NetworkError::BlockedRobots(_))));
    }
}
