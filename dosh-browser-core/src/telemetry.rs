use std::collections::HashMap;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const REDACTED: &str = "[REDACTED]";

pub fn is_secret_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    let re = Regex::new(r"authorization|cookie|token|password|passwd|secret|api.?key|credential|session|csrf").unwrap();
    re.is_match(&lower)
}

pub fn sanitize_url(raw_url: &str) -> String {
    if let Ok(mut parsed) = url::Url::parse(raw_url) {
        parsed.set_fragment(None);
        let _ = parsed.set_username("");
        let _ = parsed.set_password(None);

        let pairs: Vec<(String, String)> = parsed
            .query_pairs()
            .map(|(k, _)| (k.to_string(), REDACTED.to_string()))
            .collect();

        if !pairs.is_empty() {
            let mut serializer = url::form_urlencoded::Serializer::new(String::new());
            for (k, v) in pairs {
                serializer.append_pair(&k, &v);
            }
            parsed.set_query(Some(&serializer.finish()));
        }

        parsed.to_string()
    } else {
        "[INVALID URL]".to_string()
    }
}

pub fn sanitize_headers(headers: &HashMap<String, String>) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for (k, v) in headers.iter().take(64) {
        let name = k.to_lowercase();
        let val = if is_secret_name(&name) {
            REDACTED.to_string()
        } else if name == "location" || name == "referer" || name == "content-location" {
            sanitize_url(v)
        } else {
            v.chars().take(1024).collect()
        };
        out.insert(name.chars().take(128).collect(), val);
    }
    out
}

pub fn redact_json(val: &Value) -> Value {
    match val {
        Value::Object(map) => {
            let mut new_map = serde_json::Map::new();
            for (k, v) in map {
                if is_secret_name(k) {
                    new_map.insert(k.clone(), Value::String(REDACTED.to_string()));
                } else {
                    new_map.insert(k.clone(), redact_json(v));
                }
            }
            Value::Object(new_map)
        }
        Value::Array(arr) => Value::Array(arr.iter().map(redact_json).collect()),
        other => other.clone(),
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetworkRecord {
    pub id: u64,
    pub source: String, // "fetch" | "runtime"
    pub url: String,
    pub method: String,
    pub status: Option<u16>,
    pub request_headers: HashMap<String, String>,
    pub response_headers: HashMap<String, String>,
    pub state: String, // "complete" | "failed" | "mocked" | "blocked" | "reported"
    pub network_sent: Option<bool>,
    pub body_state: String,
    pub bytes: usize,
    pub content_type: String,
    pub initiator_type: String,
    pub response_body: Option<String>,
    pub caused_by: Option<String>,
}

impl Default for NetworkRecord {
    fn default() -> Self {
        Self {
            id: 0,
            source: "runtime".to_string(),
            url: String::new(),
            method: "GET".to_string(),
            status: None,
            request_headers: HashMap::new(),
            response_headers: HashMap::new(),
            state: "reported".to_string(),
            network_sent: None,
            body_state: "disabled".to_string(),
            bytes: 0,
            content_type: String::new(),
            initiator_type: "fetch".to_string(),
            response_body: None,
            caused_by: None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct RequestRule {
    pub id: u64,
    pub pattern: String,
    pub action: String, // "block" | "mock" | "redirect"
    pub status: u16,
    pub body: String,
    pub content_type: String,
    pub redirect_url: String,
}

pub struct NetworkLedger {
    records: Vec<NetworkRecord>,
    last_id: u64,
    max_records: usize,
    pub capture_bodies: bool,
    pub body_max_bytes: usize,
    rules: Vec<RequestRule>,
    next_rule_id: u64,
}

impl NetworkLedger {
    pub fn new(max_records: usize, capture_bodies: bool) -> Self {
        Self {
            records: Vec::new(),
            last_id: 0,
            max_records: max_records.max(10),
            capture_bodies,
            body_max_bytes: 16384,
            rules: Vec::new(),
            next_rule_id: 1,
        }
    }

    pub fn add_rule(
        &mut self,
        pattern: &str,
        action: &str,
        status: u16,
        body: &str,
        content_type: &str,
        redirect_url: &str,
    ) -> u64 {
        let id = self.next_rule_id;
        self.next_rule_id += 1;
        self.rules.push(RequestRule {
            id,
            pattern: pattern.to_string(),
            action: action.to_string(),
            status,
            body: body.to_string(),
            content_type: content_type.to_string(),
            redirect_url: redirect_url.to_string(),
        });
        id
    }

    pub fn remove_rule(&mut self, rule_id: u64) -> bool {
        let before = self.rules.len();
        self.rules.retain(|r| r.id != rule_id);
        self.rules.len() < before
    }

    pub fn match_rule(&self, url: &str) -> Option<&RequestRule> {
        self.rules.iter().find(|r| url.contains(&r.pattern))
    }

    pub fn append(&mut self, mut record: NetworkRecord) -> u64 {
        self.last_id += 1;
        record.id = self.last_id;
        record.url = sanitize_url(&record.url);
        record.request_headers = sanitize_headers(&record.request_headers);
        record.response_headers = sanitize_headers(&record.response_headers);

        self.records.push(record);
        if self.records.len() > self.max_records {
            self.records.remove(0);
        }
        self.last_id
    }

    pub fn record_runtime_batch(
        &mut self,
        items: &[Value],
        _tab_id: &str,
        base_url: &str,
    ) -> usize {
        let mut count = 0;
        for item in items {
            let Some(map) = item.as_object() else {
                continue;
            };

            let disposition = map.get("disposition").and_then(|v| v.as_str()).unwrap_or("");
            let req_headers: HashMap<String, String> = map
                .get("request_headers")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default();
            let res_headers: HashMap<String, String> = map
                .get("response_headers")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default();

            let mocked = disposition.starts_with("mocked:")
                || res_headers.get("x-dosh-mock").map(|v| v == "1").unwrap_or(false);
            let blocked = disposition.starts_with("blocked:")
                || map.get("content_type").and_then(|v| v.as_str()) == Some("blocked-by-rule");
            let cached = map.get("sw").and_then(|v| v.as_bool()).unwrap_or(false);

            let status = map.get("status").and_then(|v| v.as_u64()).map(|v| v as u16);
            let error = map.get("error").and_then(|v| v.as_str()).unwrap_or("");
            let failed = status == Some(0) || !error.is_empty();

            let state = if mocked {
                "mocked"
            } else if blocked {
                "blocked"
            } else if failed {
                "failed"
            } else {
                "reported"
            };

            let network_sent = if mocked || blocked || cached {
                Some(false)
            } else {
                None
            };

            let raw_url = map.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let full_url = if let Ok(base) = url::Url::parse(base_url) {
                base.join(raw_url).map(|u| u.to_string()).unwrap_or_else(|_| raw_url.to_string())
            } else {
                raw_url.to_string()
            };

            let content_type = map
                .get("content_type")
                .and_then(|v| v.as_str())
                .unwrap_or_else(|| res_headers.get("content-type").map(|s| s.as_str()).unwrap_or(""))
                .to_string();

            let initiator_type = map
                .get("initiatorType")
                .and_then(|v| v.as_str())
                .unwrap_or("fetch")
                .to_string();

            let bytes = map.get("bytes").and_then(|v| v.as_u64()).unwrap_or(0) as usize;

            let mut body_state = if !self.capture_bodies {
                "disabled".to_string()
            } else {
                "unavailable".to_string()
            };
            let mut response_body = None;

            if self.capture_bodies {
                let body_str = map.get("response_body").and_then(|v| v.as_str()).or_else(|| {
                    if mocked {
                        map.get("body").and_then(|v| v.as_str())
                    } else {
                        None
                    }
                });
                if let Some(bs) = body_str {
                    if bs.len() > self.body_max_bytes {
                        body_state = "omitted_over_cap".to_string();
                    } else if let Ok(v) = serde_json::from_str::<Value>(bs) {
                        response_body = Some(redact_json(&v).to_string());
                        body_state = "captured_redacted".to_string();
                    } else {
                        body_state = "omitted_unsupported_format".to_string();
                    }
                }
            }

            self.append(NetworkRecord {
                id: 0,
                source: "runtime".to_string(),
                url: full_url,
                method: map.get("method").and_then(|v| v.as_str()).unwrap_or("GET").to_uppercase(),
                status,
                request_headers: req_headers,
                response_headers: res_headers,
                state: state.to_string(),
                network_sent,
                body_state,
                bytes,
                content_type,
                initiator_type,
                response_body,
                caused_by: map.get("caused_by").and_then(|v| v.as_str()).map(|s| s.to_string()),
            });
            count += 1;
        }
        count
    }

    pub fn records(&self) -> &[NetworkRecord] {
        &self.records
    }

    pub fn query(&self, after: u64, limit: usize, pattern: &str) -> Value {
        let matches: Vec<&NetworkRecord> = self
            .records
            .iter()
            .filter(|r| r.id > after && (pattern.is_empty() || r.url.contains(pattern)))
            .collect();

        let returned: Vec<Value> = matches
            .iter()
            .take(limit)
            .map(|r| serde_json::to_value(r).unwrap_or_default())
            .collect();

        let omitted_limit = matches.len().saturating_sub(returned.len());
        let oldest = self.records.first().map(|r| r.id).unwrap_or(self.last_id + 1);
        let omitted_retention = (oldest.saturating_sub(after)).saturating_sub(1) as usize;
        let next_after = if omitted_limit > 0 {
            matches.get(limit.saturating_sub(1)).map(|r| r.id).unwrap_or(after)
        } else {
            after.max(self.last_id)
        };

        json!({
            "records": returned,
            "next_after": next_after,
            "omitted_limit": omitted_limit,
            "omitted_retention": omitted_retention,
            "total_observed": self.last_id,
            "retained_count": self.records.len(),
            "latest_id": self.last_id,
            "capture_bodies": self.capture_bodies,
            "body_max_bytes": self.body_max_bytes,
        })
    }

    /// Catalog discovered background API endpoints (XHR/fetch/JSON) for Site-as-SDK direct replay.
    pub fn discover_apis(&self) -> Vec<Value> {
        let mut endpoints: HashMap<(String, String), Value> = HashMap::new();

        for r in &self.records {
            let is_api = r.url.contains("/api/")
                || r.content_type.to_lowercase().contains("json")
                || r.initiator_type == "fetch"
                || r.initiator_type == "xmlhttprequest"
                || r.url.ends_with(".json")
                || r.url.ends_with(".graphql");

            if !is_api {
                continue;
            }

            let Ok(parsed) = url::Url::parse(&r.url) else {
                continue;
            };

            let path = parsed.path().to_string();
            let host = parsed.host_str().unwrap_or("").to_string();
            let scheme = parsed.scheme().to_string();
            let base_endpoint = format!("{scheme}://{host}{path}");
            let key = (r.method.to_uppercase(), base_endpoint.clone());

            let query_keys: Vec<String> = parsed
                .query_pairs()
                .map(|(k, _)| k.to_string())
                .collect();

            if let Some(entry) = endpoints.get_mut(&key) {
                if let Some(obs) = entry.get_mut("observations") {
                    *obs = json!(obs.as_u64().unwrap_or(0) + 1);
                }
                if let Some(params) = entry.get_mut("query_params").and_then(|v| v.as_array_mut()) {
                    for qk in query_keys {
                        let qk_val = json!(qk);
                        if !params.contains(&qk_val) {
                            params.push(qk_val);
                        }
                    }
                }
            } else {
                endpoints.insert(
                    key,
                    json!({
                        "method": r.method.to_uppercase(),
                        "endpoint": base_endpoint,
                        "host": host,
                        "path": path,
                        "query_params": query_keys,
                        "content_type": r.content_type,
                        "sample_status": r.status,
                        "observations": 1,
                    }),
                );
            }
        }

        let mut res: Vec<Value> = endpoints.into_values().collect();
        res.sort_by(|a, b| {
            let obs_b = b["observations"].as_u64().unwrap_or(0);
            let obs_a = a["observations"].as_u64().unwrap_or(0);
            obs_b.cmp(&obs_a).then_with(|| {
                let ep_a = a["endpoint"].as_str().unwrap_or("");
                let ep_b = b["endpoint"].as_str().unwrap_or("");
                ep_a.cmp(ep_b)
            })
        });
        res
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sanitize_url() {
        let clean = sanitize_url("https://user:pass@example.com/api?token=secret123&user=john");
        assert_eq!(clean, "https://example.com/api?token=%5BREDACTED%5D&user=%5BREDACTED%5D");
    }

    #[test]
    fn test_redact_json() {
        let data = json!({
            "user": "alice",
            "password": "my_secret_password",
            "meta": {
                "apiKey": "12345",
                "normal": "value"
            }
        });
        let redacted = redact_json(&data);
        assert_eq!(redacted["user"], "alice");
        assert_eq!(redacted["password"], REDACTED);
        assert_eq!(redacted["meta"]["apiKey"], REDACTED);
        assert_eq!(redacted["meta"]["normal"], "value");
    }

    #[test]
    fn test_network_ledger_lifecycle() {
        let mut ledger = NetworkLedger::new(100, true);
        let r1 = NetworkRecord {
            url: "https://example.com/data?auth=xyz".to_string(),
            method: "GET".to_string(),
            status: Some(200),
            ..Default::default()
        };
        let id1 = ledger.append(r1);
        assert_eq!(id1, 1);

        let query = ledger.query(0, 10, "");
        assert_eq!(query["records"].as_array().unwrap().len(), 1);
        assert_eq!(query["latest_id"], 1);

        // Verify URL was redacted
        let record = &query["records"][0];
        assert!(record["url"].as_str().unwrap().contains("%5BREDACTED%5D"));
    }

    #[test]
    fn test_discover_apis_catalog() {
        let mut ledger = NetworkLedger::new(100, true);
        ledger.append(NetworkRecord {
            url: "https://example.com/api/v1/users?page=1&limit=20".to_string(),
            method: "GET".to_string(),
            status: Some(200),
            content_type: "application/json".to_string(),
            initiator_type: "fetch".to_string(),
            ..Default::default()
        });
        ledger.append(NetworkRecord {
            url: "https://example.com/api/v1/users?page=2".to_string(),
            method: "GET".to_string(),
            status: Some(200),
            content_type: "application/json".to_string(),
            initiator_type: "fetch".to_string(),
            ..Default::default()
        });
        ledger.append(NetworkRecord {
            url: "https://example.com/static/style.css".to_string(),
            method: "GET".to_string(),
            status: Some(200),
            content_type: "text/css".to_string(),
            initiator_type: "link".to_string(),
            ..Default::default()
        });

        let apis = ledger.discover_apis();
        assert_eq!(apis.len(), 1);
        let ep = &apis[0];
        assert_eq!(ep["method"], "GET");
        assert_eq!(ep["path"], "/api/v1/users");
        assert_eq!(ep["observations"], 2);
        let params = ep["query_params"].as_array().unwrap();
        assert!(params.contains(&json!("page")));
    }
}

