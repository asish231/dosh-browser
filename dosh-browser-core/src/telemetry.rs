use std::collections::HashMap;
use std::io::Read as _;
use base64::Engine as _;
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

// ============================================================================
// Phase 1 network-depth evidence types (DOSH-FULL-ACCESS-PLAN N1/N2/N4–N9).
// ============================================================================

/// Default cap on unredacted sealed capture (real replay/decode/reveal uses).
pub const SEALED_MAX_BYTES_DEFAULT: usize = 262_144;

pub fn epoch_ms_f64() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64() * 1000.0)
        .unwrap_or(0.0)
}

fn is_zero_u16(v: &u16) -> bool {
    *v == 0
}

/// One Server-Sent Events message captured from a `text/event-stream` body (N1).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SseEvent {
    /// Milliseconds after the response's first byte at which this event arrived.
    #[serde(default)]
    pub t: f64,
    /// The event `type` (spec default "message").
    #[serde(default)]
    pub event: String,
    /// The last `id:` seen before this event ("" when none).
    #[serde(default)]
    pub id: String,
    /// The joined `data:` payload lines.
    #[serde(default)]
    pub data: String,
}

/// Transport timing as actually measured by the dispatch path (N4). DNS and
/// TCP connect phases are not observable through reqwest's pooled high-level
/// client; they are absent rather than fabricated.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct TimingBreakdown {
    /// Epoch milliseconds when the request entered the HTTP transport.
    #[serde(default)]
    pub started_at_ms: f64,
    /// Milliseconds from dispatch to first body byte (headers received).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ttfb_ms: Option<f64>,
    /// Milliseconds from dispatch to full body completion.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<f64>,
}

/// One captured WebSocket frame (N2), text or binary, in either direction.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct WsFrameRecord {
    #[serde(default)]
    pub session: u64,
    /// Milliseconds after the session opened.
    #[serde(default)]
    pub t: f64,
    /// "in" for server→client, "out" for client→server.
    #[serde(default)]
    pub direction: String,
    #[serde(default)]
    pub opcode: u8,
    #[serde(default)]
    pub len: usize,
    /// Text payload for opcode-1 frames (UTF-8, capped at 4096 chars).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub text: String,
    /// Base64 payload for binary frames (opcode 2 and non-UTF-8 text).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub payload_b64: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct WsSessionRecord {
    #[serde(default)]
    pub session: u64,
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub opened_at_ms: f64,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub closed: bool,
    #[serde(default, skip_serializing_if = "is_zero_u16")]
    pub close_code: u16,
    #[serde(default)]
    pub frame_count: usize,
}

/// Unredacted request/response evidence for authorized replay (N5), decode
/// (N6) and reveal (N8). Held in memory only; never serialized by any query
/// output, and consumed solely behind the `allow_secret_reveal` gate.
#[derive(Debug, Clone, Default)]
pub struct SealedRequest {
    pub record_id: u64,
    pub url: String,
    pub method: String,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
    pub response_body: Vec<u8>,
    pub content_type: String,
}

/// One audited secret-reveal event (N8). Output is a redaction waiver log,
/// not the secret itself. Retained, most-recent-last, bounded to 128.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct RevealAudit {
    #[serde(default)]
    pub t_ms: f64,
    #[serde(default)]
    pub action: String,
    #[serde(default)]
    pub detail: String,
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
    /// Captured SSE event sequence (N1) when the body was text/event-stream.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub stream_events: Vec<SseEvent>,
    /// Measured transport timing (N4); absent phases are not faked.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timing: Option<TimingBreakdown>,
    /// Sanitized JS call-site hint for the fetch/XHR launch point (N9).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub initiator_stack: Vec<String>,
    /// True when the body was cut by the transport byte cap (N7 honesty).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub truncated: bool,
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
            stream_events: Vec::new(),
            timing: None,
            initiator_stack: Vec::new(),
            truncated: false,
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
    /// Unredacted request/response evidence keyed by record id (N5/N6/N8).
    /// In-memory only; no query output ever contains it directly.
    sealed: Vec<SealedRequest>,
    pub sealed_max_bytes: usize,
    /// Operators' audited escape hatch (N8); OFF keeps redaction default.
    allow_secret_reveal: bool,
    reveal_audit: Vec<RevealAudit>,
    ws_sessions: Vec<WsSessionRecord>,
    ws_frames: Vec<WsFrameRecord>,
    next_ws_id: u64,
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
            sealed: Vec::new(),
            sealed_max_bytes: SEALED_MAX_BYTES_DEFAULT,
            allow_secret_reveal: false,
            reveal_audit: Vec::new(),
            ws_sessions: Vec::new(),
            ws_frames: Vec::new(),
            next_ws_id: 0,
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

            let initiator_stack = map
                .get("initiator_stack")
                .and_then(|v| v.as_str())
                .map(|s| sanitize_initiator_stack(s))
                .unwrap_or_default();

            let truncated = map.get("truncated").and_then(|v| v.as_bool()).unwrap_or(false);

            let timing = map
                .get("timing")
                .and_then(|v| serde_json::from_value::<TimingBreakdown>(v.clone()).ok());

            let bytes = map.get("bytes").and_then(|v| v.as_u64()).unwrap_or(0) as usize;

            let mut body_state = if !self.capture_bodies {
                "disabled".to_string()
            } else {
                "unavailable".to_string()
            };
            let mut response_body = None;

            let mut stream_events = Vec::new();
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
                    } else if content_type.to_lowercase().contains("text/event-stream") {
                        // N1: runtime-submitted SSE batches become an
                        // event sequence: JSON-typed data payloads are
                        // secret-redacted, non-JSON chunks are stored as
                        // text (disclosed: no key-based scan of prose).
                        stream_events = parse_sse_with_arrivals(bs, &[])
                            .into_iter()
                            .map(|mut ev| {
                                if let Ok(v) = serde_json::from_str::<Value>(&ev.data) {
                                    ev.data = redact_json(&v).to_string();
                                }
                                ev
                            })
                            .collect();
                        body_state = "stream".to_string();
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
                stream_events,
                timing,
                initiator_stack,
                truncated,
            });
            count += 1;
        }
        count
    }

    pub fn records(&self) -> &[NetworkRecord] {
        &self.records
    }

    // ------------------------------------------------------------------
    // Phase 1 network-depth ledger API (N1/N2/N3/N5/N6/N8).
    // ------------------------------------------------------------------

    /// Append a record, atomically attaching the unredacted sealed evidence
    /// captured at the transport. `record_id` on the sealed entry is filled
    /// in here, after sanitization has assigned the public id.
    pub fn append_with_sealed(&mut self, record: NetworkRecord, sealed: Option<SealedRequest>) -> u64 {
        let id = self.append(record);
        if let Some(mut s) = sealed {
            // Sealed payloads are capped; oversize incoming sealed bodies are
            // truncated here so the ledger never grows without bounds.
            if s.body.len() > self.sealed_max_bytes {
                s.body.truncate(self.sealed_max_bytes);
            }
            if s.response_body.len() > self.sealed_max_bytes {
                s.response_body.truncate(self.sealed_max_bytes);
            }
            s.record_id = id;
            if self.sealed.len() >= 512 {
                self.sealed.remove(0);
            }
            self.sealed.push(s);
        }
        id
    }

    pub fn sealed_for(&self, record_id: u64) -> Option<&SealedRequest> {
        self.sealed.iter().find(|s| s.record_id == record_id)
    }

    /// N8 gate. Flips are also audited so even toggling is on the record.
    pub fn set_secret_reveal(&mut self, enabled: bool) -> bool {
        if enabled == self.allow_secret_reveal {
            return false;
        }
        self.allow_secret_reveal = enabled;
        let t = epoch_ms_f64();
        self.reveal_audit.push(RevealAudit {
            t_ms: t,
            action: "reveal_gate".into(),
            detail: if enabled { "enabled by operator".into() } else { "disabled by operator".into() },
        });
        if self.reveal_audit.len() > 128 {
            self.reveal_audit.remove(0);
        }
        true
    }

    pub fn reveal_allowed(&self) -> bool {
        self.allow_secret_reveal
    }

    pub fn reveal_audit(&self) -> &[RevealAudit] {
        &self.reveal_audit
    }

    pub fn audit_reveal(&mut self, action: &str, detail: &str) {
        self.reveal_audit.push(RevealAudit {
            t_ms: epoch_ms_f64(),
            action: action.to_string(),
            detail: detail.chars().take(200).collect(),
        });
        if self.reveal_audit.len() > 128 {
            self.reveal_audit.remove(0);
        }
    }

    /// N8: return the unredacted request evidence for a record — ONLY when
    /// the operator has enabled the reveal gate this turn. The mixed-data
    /// response is intentionally capped; this surface exists for operators,
    /// not page emulation, and every call is entered in the audit log.
    pub fn reveal_record(&mut self, record_id: u64) -> Result<Value, String> {
        if !self.allow_secret_reveal {
            return Err(
                "secret reveal refused: allow_secret_reveal gate is off (redaction is the default)".into(),
            );
        }
        let Some(s) = self.sealed_for(record_id) else {
            return Err(format!(
                "no sealed (unredacted) evidence recorded for id {record_id}"
            ));
        };
        let (method, url, headers, body, response_body, content_type) = (
            s.method.clone(),
            s.url.clone(),
            s.headers.clone(),
            s.body.clone(),
            s.response_body.clone(),
            s.content_type.clone(),
        );
        self.audit_reveal(
            "record_reveal",
            &format!("record={record_id} method={method} url={url}"),
        );
        Ok(json!({
            "record_id": record_id,
            "method": method,
            "url": url,
            "request_headers": headers,
            "request_body_b64": base64::engine::general_purpose::STANDARD.encode(&body),
            "request_body_len": body.len(),
            "response_body_b64": base64::engine::general_purpose::STANDARD.encode(&response_body),
            "response_body_len": response_body.len(),
            "content_type": content_type,
            "audited": true,
        }))
    }

    /// N1: drain SSE events for a record incrementally (`after` = count of
    /// events already drained).
    pub fn stream_view(&self, record_id: u64, after: usize) -> Value {
        let Some(r) = self.records.iter().find(|r| r.id == record_id) else {
            return json!({ "error": format!("no network record with id {record_id}") });
        };
        let total = r.stream_events.len();
        let events: Vec<&SseEvent> = r.stream_events.iter().skip(after).take(256).collect();
        let next_after = after + events.len();
        json!({
            "record_id": record_id,
            "url": r.url,
            "total": total,
            "returned": events.len(),
            "next_after": next_after,
            "complete": next_after >= total,
            "events": events,
        })
    }

    // ---- N2: WebSocket session + frame capture ------------------------

    pub fn ws_open(&mut self, url: &str) -> u64 {
        self.next_ws_id += 1;
        let session = self.next_ws_id;
        self.ws_sessions.push(WsSessionRecord {
            session,
            url: sanitize_url(url),
            opened_at_ms: epoch_ms_f64(),
            closed: false,
            close_code: 0,
            frame_count: 0,
        });
        if self.ws_sessions.len() > 64 {
            self.ws_sessions.remove(0);
        }
        session
    }

    /// Record one frame. `payload` is the capture slice (already capped by
    /// the caller); `real_len` is the frame's true size so oversize capture
    /// is disclosed rather than silently clipped.
    pub fn ws_frame(&mut self, session: u64, direction: &str, opcode: u8, payload: &[u8], real_len: usize) {
        let Some(s) = self.ws_sessions.iter_mut().find(|s| s.session == session) else {
            return;
        };
        if s.closed {
            return;
        }
        let t = epoch_ms_f64() - s.opened_at_ms;
        let base64_engine = base64::engine::general_purpose::STANDARD;
        let (text, payload_b64) = if opcode == 1 {
            match std::str::from_utf8(payload) {
                Ok(s) if payload.len() <= 4096 => (s.to_string(), String::new()),
                _ => (String::new(), base64_engine.encode(payload)),
            }
        } else {
            (String::new(), base64_engine.encode(payload))
        };
        self.ws_frames.push(WsFrameRecord {
            session,
            t,
            direction: direction.to_string(),
            opcode,
            len: real_len,
            text,
            payload_b64,
        });
        s.frame_count += 1;
        if self.ws_frames.len() > 10_000 {
            self.ws_frames.remove(0);
        }
    }

    pub fn ws_close(&mut self, session: u64, code: u16) {
        if let Some(s) = self.ws_sessions.iter_mut().find(|s| s.session == session) {
            s.closed = true;
            s.close_code = code;
        }
    }

    pub fn ws_sessions(&self) -> &[WsSessionRecord] {
        &self.ws_sessions
    }

    pub fn ws_view(&self, session: u64, after: usize) -> Value {
        let Some(s) = self.ws_sessions.iter().find(|s| s.session == session) else {
            return json!({ "error": format!("no websocket session {session}") });
        };
        let frames = self
            .ws_frames
            .iter()
            .filter(|f| f.session == session)
            .collect::<Vec<_>>();
        let total = frames.len();
        let returned: Vec<&WsFrameRecord> = frames.into_iter().skip(after).take(256).collect();
        let next_after = after + returned.len();
        json!({
            "session": session,
            "url": s.url,
            "closed": s.closed,
            "close_code": s.close_code,
            "total": total,
            "returned": returned.len(),
            "next_after": next_after,
            "frames": returned,
        })
    }

    // ---- N3: CORS verdict over recorded requests ----------------------

    pub fn cors_view(&self, client_origin: &str, record_id: u64) -> Result<Value, String> {
        let Some(r) = self.records.iter().find(|r| r.id == record_id) else {
            return Err(format!("no network record with id {record_id}"));
        };
        let parsed = url::Url::parse(&r.url).map_err(|e| format!("bad record url: {e}"))?;
        let verdict = crate::cors::cors_verdict(
            client_origin,
            &r.method,
            &parsed,
            &r.request_headers,
            &r.response_headers,
        );
        Ok(json!({
            "record_id": record_id,
            "url": r.url,
            "verdict": verdict,
        }))
    }

    // ---- N4: timing waterfall ----------------------------------------

    pub fn timing_view(&self, pattern: &str) -> Value {
        let rows: Vec<Value> = self
            .records
            .iter()
            .filter(|r| r.timing.is_some() && (pattern.is_empty() || r.url.contains(pattern)))
            .map(|r| {
                json!({
                    "id": r.id,
                    "url": r.url,
                    "method": r.method,
                    "status": r.status,
                    "bytes": r.bytes,
                    "timing": r.timing,
                })
            })
            .collect();
        json!({ "count": rows.len(), "rows": rows })
    }

    // ---- N6: body decode over sealed raw bytes ------------------------

    pub fn decode_view(&self, record_id: u64, encoding: &str) -> Result<Value, String> {
        let Some(s) = self.sealed_for(record_id) else {
            return Err(format!(
                "no sealed body captured for id {record_id}; run with net.capture enabled"
            ));
        };
        let raw = if !s.response_body.is_empty() {
            &s.response_body
        } else {
            &s.body
        };
        if raw.is_empty() {
            return Ok(json!({ "record_id": record_id, "empty": true }));
        }
        let mut out = decode_payload_bytes(raw, encoding)?;
        out["record_id"] = json!(record_id);
        Ok(out)
    }

    #[cfg(test)]
    pub(crate) fn test_record_mut(&mut self, id: u64) -> Option<&mut NetworkRecord> {
        self.records.iter_mut().find(|r| r.id == id)
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

// ----------------------------------------------------------------------------
// Phase 1 free parsers/decomposers (N1 SSE, N6 inflate+protobuf, N9 stacks).
// ----------------------------------------------------------------------------

/// Header-value redaction used by `net.replay` output: values for sensitive
/// header names (authorization, cookie, token-bearing) are replaced; other
/// values pass through (their transport metadata is legitimately observable).
pub fn redact_header_value(name: &str, value: &str) -> String {
    let lname = name.to_lowercase();
    let sensitive = lname == "authorization"
        || lname == "cookie"
        || lname == "set-cookie"
        || lname == "proxy-authorization"
        || lname.contains("token");
    if sensitive {
        let kind = if lname == "authorization" || lname == "proxy-authorization" {
            // Keep the scheme (Bearer/Basic) so the replay contract is still
            // reconstructible; the credential itself is the secret.
            let split = value.split_once(' ');
            match split {
                Some((scheme, _)) => format!("{scheme} [REDACTED]"),
                None => "[REDACTED]".into(),
            }
        } else {
            "[REDACTED]".into()
        };
        kind
    } else {
        value.to_string()
    }
}

// ----------------------------------------------------------------------------
// Phase 1 free parsers/decomposers (N1 SSE, N6 inflate+protobuf, N9 stacks).
// ----------------------------------------------------------------------------

/// Structural protobuf wire-format field. Field numbers + wire types only;
/// there is no schema, and that fact is stated in the output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProtobufField {
    #[serde(default)]
    pub field: u32,
    #[serde(default)]
    pub wire_type: u8,
    #[serde(default)]
    pub value: Value,
}

/// Parse a `text/event-stream` body into ordered events (N1). `arrivals` are
/// `(byte_end_offset, arrival_ms_after_first_byte)` pairs from the chunked
/// read; an event is timestamped with the arrival of the chunk it ends in.
/// When no chunk-level timing was captured the list is empty and all events
/// get `t = 0` (recorded as absent, not invented).
pub fn parse_sse_with_arrivals(body: &str, arrivals: &[(usize, f64)]) -> Vec<SseEvent> {
    let mut events: Vec<SseEvent> = Vec::new();
    let mut data_lines: Vec<String> = Vec::new();
    let mut event_type = String::new();
    let mut last_id = String::new();
    let mut cur_end = 0usize;
    let bytes = body.as_bytes();
    let mut i = 0usize;
    let mut line_start = 0usize;
    while i <= bytes.len() {
        let (line_bytes, done) = match bytes[i..].iter().position(|&b| b == b'\n') {
            Some(rel) => (i + rel, false),
            None => (bytes.len(), true),
        };
        let line_text = String::from_utf8_lossy(&bytes[line_start..line_bytes]);
        let mut line = line_text.as_ref();
        if line.ends_with('\r') {
            line = &line[..line.len() - 1];
        }
        let is_blank = line.is_empty();
        let end_for_event = if is_blank { line_bytes + 1.min(bytes.len() - i) } else { line_bytes };
        cur_end = end_for_event;
        if is_blank {
            if !data_lines.is_empty() {
                let t = arrivals
                    .iter()
                    .find(|(off, _)| *off >= cur_end)
                    .or_else(|| arrivals.last())
                    .map(|(_, t)| *t)
                    .unwrap_or(0.0);
                events.push(SseEvent {
                    t,
                    event: if event_type.is_empty() { "message".into() } else { event_type.clone() },
                    id: last_id.clone(),
                    data: data_lines.join("\n"),
                });
                data_lines.clear();
                event_type.clear();
            }
        } else if let Some(rest) = line.strip_prefix("data:") {
            data_lines.push(rest.strip_prefix(' ').unwrap_or(rest).to_string());
        } else if let Some(rest) = line.strip_prefix("event:") {
            event_type = rest.strip_prefix(' ').unwrap_or(rest).to_string();
        } else if let Some(rest) = line.strip_prefix("id:") {
            last_id = rest.strip_prefix(' ').unwrap_or(rest).to_string();
        }
        if done {
            break;
        }
        i = line_bytes + 1;
        line_start = i;
    }
    events
}

fn read_varint(data: &[u8], mut i: usize) -> Result<(u64, usize), String> {
    let start = i;
    let mut result: u64 = 0;
    let mut shift = 0u32;
    loop {
        if i >= data.len() {
            return Err("truncated varint".into());
        }
        let b = data[i];
        i += 1;
        result |= ((b & 0x7f) as u64) << shift;
        if b & 0x80 == 0 {
            break;
        }
        shift += 7;
        if i - start > 10 || shift > 63 {
            return Err("varint too long".into());
        }
    }
    Ok((result, i))
}

fn nested_or_text(chunk: &[u8], depth: usize) -> Result<Value, String> {
    // Prefer a structural nested-message interpretation only when the chunk
    // parses fully as a message with at least one field and is not plain text.
    let text_looks = chunk.iter().all(|&b| (0x20..0x7f).contains(&b) || b == b'\n' || b == b'\r' || b == b'\t');
    if text_looks && !chunk.is_empty() {
        return Ok(json!(String::from_utf8_lossy(chunk).as_ref()));
    }
    if let Ok(Ok(fields)) = std::panic::catch_unwind(|| decode_protobuf_structural(chunk, depth + 1)) {
        if !fields.is_empty() {
            return Ok(json!(fields));
        }
    }
    if let Ok(text) = std::str::from_utf8(chunk) {
        return Ok(json!(text));
    }
    Ok(json!({ "hex": hex::encode_upper(chunk) }))
}

/// Decode protobuf wire format structurally (N6): field numbers, wire types,
/// varints/floats/bytes. Nested length-delimited chunks that parse as valid
/// messages become nested field lists. Depth-capped at 8.
pub fn decode_protobuf_structural(data: &[u8], depth: usize) -> Result<Vec<ProtobufField>, String> {
    if depth > 8 {
        return Err("protobuf nested message exceeds depth 8".into());
    }
    let mut out: Vec<ProtobufField> = Vec::new();
    let mut i = 0usize;
    while i < data.len() {
        let (key, next) = read_varint(data, i)?;
        i = next;
        let field = (key >> 3) as u32;
        let wt = (key & 7) as u8;
        if field == 0 {
            return Err("field number 0 is invalid".into());
        }
        let value = match wt {
            0 => {
                let (v, n) = read_varint(data, i)?;
                i = n;
                json!(v)
            }
            1 => {
                if i + 8 > data.len() {
                    return Err("truncated 64-bit value".into());
                }
                let bits = u64::from_le_bytes(data[i..i + 8].try_into().unwrap());
                i += 8;
                json!(f64::from_bits(bits))
            }
            5 => {
                if i + 4 > data.len() {
                    return Err("truncated 32-bit value".into());
                }
                let bits = u32::from_le_bytes(data[i..i + 4].try_into().unwrap());
                i += 4;
                json!(f32::from_bits(bits))
            }
            2 => {
                let (len, n) = read_varint(data, i)?;
                i = n;
                if len > 16 * 1024 * 1024 {
                    return Err("length-delimited field over 16 MB".into());
                }
                if i + len as usize > data.len() {
                    return Err("truncated length-delimited field".into());
                }
                let chunk = &data[i..i + len as usize];
                i += len as usize;
                nested_or_text(chunk, depth)?
            }
            3 | 4 => return Err(format!("unsupported wire type {wt} (deprecated groups)")),
            w => return Err(format!("unknown wire type {w}")),
        };
        out.push(ProtobufField { field, wire_type: wt, value });
    }
    Ok(out)
}

/// Inflate a captured body (N6). `auto` detects gzip (`1f 8b`) and zlib
/// (`0x78`), else raw deflate. Brotli has no magic bytes, so it is only tried
/// when explicitly requested. Failing decompressions are reported, never
/// masked with a guessed payload.
pub fn decode_payload_bytes(raw: &[u8], encoding: &str) -> Result<Value, String> {
    let enc = encoding.to_ascii_lowercase();
    let decoded: Vec<u8> = match enc.as_str() {
        "identity" | "none" | "" => raw.to_vec(),
        "gzip" => {
            let mut d = flate2::read::GzDecoder::new(raw);
            let mut out = Vec::new();
            d.read_to_end(&mut out).map_err(|e| format!("gzip decompress failed: {e}"))?;
            out
        }
        "zlib" | "deflate" => {
            let mut d = flate2::read::ZlibDecoder::new(raw);
            let mut out = Vec::new();
            d.read_to_end(&mut out).map_err(|e| format!("zlib decompress failed: {e}"))?;
            out
        }
        "raw-deflate" => {
            let mut d = flate2::read::DeflateDecoder::new(raw);
            let mut out = Vec::new();
            d.read_to_end(&mut out).map_err(|e| format!("raw deflate decompress failed: {e}"))?;
            out
        }
        "brotli" => {
            let mut d = brotli::Decompressor::new(raw, 4096);
            let mut out = Vec::new();
            d.read_to_end(&mut out).map_err(|e| format!("brotli decompress failed: {e}"))?;
            out
        }
        "auto" => {
            if raw.len() >= 2 && raw[0] == 0x1f && raw[1] == 0x8b {
                let mut d = flate2::read::GzDecoder::new(raw);
                let mut out = Vec::new();
                d.read_to_end(&mut out).map_err(|e| format!("gzip decompress failed: {e}"))?;
                out
            } else if raw.len() >= 1 && raw[0] == 0x78 {
                let mut d = flate2::read::ZlibDecoder::new(raw);
                let mut out = Vec::new();
                d.read_to_end(&mut out).map_err(|e| format!("zlib decompress failed: {e}"))?;
                out
            } else {
                let mut d = flate2::read::DeflateDecoder::new(raw);
                let mut out = Vec::new();
                d.read_to_end(&mut out).map_err(|e| format!(
                    "auto decompress failed: not gzip/zlib header, raw deflate failed: {e}; available: gzip|zlib|raw-deflate|brotli|identity"
                ))?;
                out
            }
        }
        other => {
            return Err(format!(
                "unknown encoding '{other}'; available: auto|gzip|zlib|deflate|raw-deflate|brotli|identity"
            ))
        }
    };

    let mut out = json!({});
    out["encoding"] = json!(enc);
    out["compressed_len"] = json!(raw.len());
    out["decoded_len"] = json!(decoded.len());

    // Structure analysis: protobuf first, then JSON, then UTF-8 text, then hex
    // — each with an explicit branch so nothing is silently transformed.
    let is_protobuf = matches!(decode_protobuf_structural(&decoded, 0), Ok(fields) if !fields.is_empty())
        && !decoded.iter().all(|&b| (0x20..0x7f).contains(&b));
    if is_protobuf {
        let fields = decode_protobuf_structural(&decoded, 0).map_err(|e| format!("protobuf decode: {e}"))?;
        out["format"] = json!("protobuf-structural");
        out["fields"] = json!(fields);
        out["note"] = json!("field numbers + wire types only; no schema file");
        return Ok(out);
    }
    if let Ok(text) = std::str::from_utf8(&decoded) {
        let trimmed = text.trim_start();
        if trimmed.starts_with('{') || trimmed.starts_with('[') {
            if let Ok(v) = serde_json::from_str::<Value>(trimmed) {
                out["format"] = json!("json");
                out["redacted_data"] = json!(redact_json(&v));
                return Ok(out);
            }
        }
        out["format"] = json!("text");
        out["text"] = json!(text.chars().take(200_000).collect::<String>());
        return Ok(out);
    }
    out["format"] = json!("binary");
    out["hex_prefix"] = json!(hex::encode_upper(&decoded[..decoded.len().min(256)]));
    Ok(out)
}

/// Sanitize a JS call-site stack hint passed from the fetch bridge (N9):
/// strip query strings (tokens routinely live in page URLs), cap lines.
pub fn sanitize_initiator_stack(stack: &str) -> Vec<String> {
    stack
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .take(12)
        .map(|line| {
            let mut out = line.to_string();
            if let Some(q) = out.find('?') {
                out.truncate(q);
                out.push_str("?<REDACTED>");
            }
            out.chars().take(200).collect::<String>()
        })
        .collect()
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

    // ---- N1: SSE capture ------------------------------------------------

    #[test]
    fn test_sse_parse_in_order_with_arrivals() {
        // A real event-stream template (CRLF + LF mix), like a chat API emits.
        let body = "retry: 1000\r\n\r\n\n   \r\nid: 42\nevent: token\ndata: {\"tok\":\"abc\"}\n\ndata: second chunk\n\ndata: done\n";
        // Chunks: chunk 1 comprises the first 30 bytes, chunk 2 everything else.
        let arrivals = vec![(30usize, 12.5f64), (body.len(), 40.0f64)];
        let events = parse_sse_with_arrivals(body, &arrivals);
        assert_eq!(events.len(), 3, "got: {events:?}");
        // The retry line alone (no data) is not an emitted event; the first
        // event carries id 42 / event "token" / JSON token payload.
        assert_eq!(events[0].event, "token");
        assert_eq!(events[0].id, "42");
        assert_eq!(events[0].data, "{\"tok\":\"abc\"}");
        assert_eq!(events[1].data, "second chunk");
        assert_eq!(events[2].data, "done");
        // Arrival stamps: the final event closes inside the last chunk.
        assert!((events[2].t - 40.0).abs() < 1e-6);
        assert!(events[2].t >= events[0].t);
    }

    #[test]
    fn test_sse_parse_no_arrivals_defaults_t_zero() {
        let body = "data: hello\n\ndata: world\n\n";
        let events = parse_sse_with_arrivals(body, &[]);
        assert_eq!(events.len(), 2);
        assert_eq!(events[1].data, "world");
        assert_eq!(events[0].t, 0.0);
        assert!(events.iter().all(|e| e.event == "message"));
    }

    #[test]
    fn test_runtime_batch_stream_records_captured_and_redacted() {
        let mut ledger = NetworkLedger::new(50, true);
        ledger.record_runtime_batch(
            &[
                serde_json::json!({
                    "url": "/open-apis/chat/stream",
                    "status": 200,
                    "method": "POST",
                    "content_type": "text/event-stream",
                    "response_body": "data: {\"reply\":\"hello\",\"token\":\"sk-live-1\"}\n\ndata: keep-alive\n\n",
                }),
            ],
            "tab-1",
            "https://chat.example.com",
        );
        let query = ledger.query(0, 10, "chat");
        let rec = &query["records"][0];
        assert_eq!(rec["body_state"], "stream");
        let events = rec["stream_events"].as_array().unwrap();
        assert_eq!(events.len(), 2);
        assert!(events[0]["data"].as_str().unwrap().contains("[REDACTED]"));
        assert!(events[0]["data"].as_str().unwrap().contains("hello"));
        assert_eq!(events[1]["data"], "keep-alive");
        // Incremental drain
        let v = ledger.stream_view(query["records"][0]["id"].as_u64().unwrap(), 1);
        assert_eq!(v["returned"], 1);
        assert_eq!(v["events"][0]["data"], "keep-alive");
    }

    // ---- N2: WS session/frame registry ----------------------------------

    #[test]
    fn test_ws_frames_text_and_binary_captured() {
        let mut ledger = NetworkLedger::new(50, false);
        let s = ledger.ws_open("ws://challenge.example.com/socket");
        ledger.ws_frame(s, "in", 1, "hello from server".as_bytes(), 17);
        ledger.ws_frame(s, "out", 1, "hello from client".as_bytes(), 17);
        ledger.ws_frame(s, "in", 2, &[0xde, 0xad, 0xbe, 0xef], 4);
        ledger.ws_close(s, 1000);

        let v = ledger.ws_view(s, 0);
        assert_eq!(v["total"], 3);
        assert_eq!(v["closed"], true);
        assert_eq!(v["close_code"], 1000);
        let frames = v["frames"].as_array().unwrap();
        assert_eq!(frames[0]["direction"], "in");
        assert_eq!(frames[0]["opcode"], 1);
        assert_eq!(frames[0]["text"], "hello from server");
        assert_eq!(frames[1]["text"], "hello from client");
        // Binary frame carries base64 payload, not mojibake text
        assert_eq!(frames[2]["opcode"], 2);
        assert!(frames[2]["text"].is_null(), "binary frame carries no text field");
        assert_eq!(frames[2]["payload_b64"], "3q2+7w==");
        // Session is URL-sanitized
        assert!(v["url"].as_str().unwrap().contains("challenge.example.com"));
        // Incremental drain
        let v2 = ledger.ws_view(s, 2);
        assert_eq!(v2["returned"], 1);
        assert_eq!(v2["frames"][0]["opcode"], 2);
    }

    // ---- N6: inflate + protobuf structural decode ------------------------

    fn test_protobuf_fixture() -> Vec<u8> {
        // Hand-built wire bytes for message:
        //   field 1 (varint) = 7
        //   field 2 (length-delimited) = "chat reply"
        //   field 3 (length-delimited) = nested message: field 1 varint = 3
        let mut out = Vec::new();
        out.push(0x08); // field 1, wire 0
        out.push(0x07);
        out.push(0x12); // field 2, wire 2
        out.push(0x0a); // len 10
        out.extend_from_slice(b"chat reply");
        out.push(0x1a); // field 3, wire 2
        out.push(0x02); // len 2
        out.push(0x08); // nested field 1 varint
        out.push(0x03);
        out
    }

    #[test]
    fn test_protobuf_structural_decode() {
        let fields = decode_protobuf_structural(&test_protobuf_fixture(), 0).unwrap();
        assert_eq!(fields.len(), 3);
        assert_eq!(fields[0].field, 1);
        assert_eq!(fields[0].wire_type, 0);
        assert_eq!(fields[0].value.as_u64(), Some(7));
        assert_eq!(fields[1].value.as_str(), Some("chat reply"));
        // Nested message became a nested field list
        let nested = fields[2].value.as_array().unwrap();
        assert_eq!(nested.len(), 1);
        assert_eq!(nested[0]["field"].as_u64(), Some(1));
    }

    #[test]
    fn test_protobuf_decode_rejects_truncated() {
        let mut bad = test_protobuf_fixture();
        bad.truncate(bad.len() - 2);
        assert!(decode_protobuf_structural(&bad, 0).is_err());
    }

    #[test]
    fn test_inflate_gzip_and_protobuf_in_decode_payload() {
        use flate2::write::GzEncoder;
        use std::io::Write as _;
        let raw = test_protobuf_fixture();
        let mut enc = GzEncoder::new(Vec::new(), flate2::Compression::default());
        enc.write_all(&raw).unwrap();
        let gz = enc.finish().unwrap();

        let out = decode_payload_bytes(&gz, "auto").unwrap();
        assert_eq!(out["encoding"], "auto");
        assert_eq!(out["format"], "protobuf-structural");
        assert_eq!(out["decoded_len"], raw.len());
        let fields = out["fields"].as_array().unwrap();
        assert_eq!(fields.len(), 3);
    }

    #[test]
    fn test_inflate_brotli_explicit() {
        use std::io::Read as _;
        use std::io::Write as _;
        let raw = b"challenge body not protobuf".to_vec();
        let mut out = Vec::new();
        {
            let mut w = brotli::CompressorWriter::new(&mut out, 4096, 5, 22);
            w.write_all(&raw).unwrap();
            w.flush().unwrap();
        }
        let mut decoded = Vec::new();
        decode_payload_bytes(&out, "brotli").unwrap();
        // also make sure identity round trip returns text
        let ident = decode_payload_bytes(&raw, "identity").unwrap();
        assert_eq!(ident["format"], "text");
        assert!(ident["text"].as_str().unwrap().starts_with("challenge"));
        assert!(decoded.is_empty());
        assert!(brotli::Decompressor::new(out.as_slice(), 4096)
            .read_to_end(&mut decoded)
            .is_ok());
    }

    #[test]
    fn test_decode_rejects_unknown_encoding() {
        let err = decode_payload_bytes(b"abc", "xz").unwrap_err();
        assert!(err.contains("unknown encoding"));
    }

    // ---- N8: audited reveal gate ----------------------------------------

    #[test]
    fn test_reveal_gate_default_off_denies_and_audits_when_on() {
        let mut ledger = NetworkLedger::new(50, true);
        let sealed = SealedRequest {
            record_id: 0,
            url: "https://sso.example.com/token".into(),
            method: "POST".into(),
            headers: [("authorization".to_string(), "Bearer live-secret".to_string())].into(),
            body: b"pwd=hunter2".to_vec(),
            response_body: br#"{"ok":true}"#.to_vec(),
            content_type: "text/plain".into(),
        };
        let rec = NetworkRecord {
            url: "https://sso.example.com/token".into(),
            method: "POST".into(),
            status: Some(200),
            ..Default::default()
        };
        let id = ledger.append_with_sealed(rec, Some(sealed));

        // Gate off: refuses, and never echoes the secret.
        assert!(!ledger.reveal_allowed());
        let denied = ledger.reveal_record(id).unwrap_err();
        assert!(denied.contains("allow_secret_reveal gate is off"));

        // Operator enables the gate; the record becomes available and audited.
        assert!(ledger.set_secret_reveal(true));
        let revealed = ledger.reveal_record(id).unwrap();
        assert_eq!(revealed["request_headers"]["authorization"], "Bearer live-secret");
        assert_eq!(revealed["audited"], true);
        let audit = ledger.reveal_audit();
        assert!(audit.iter().any(|a| a.action == "record_reveal"));
        assert!(audit.iter().any(|a| a.action == "reveal_gate" && a.detail.contains("enabled")));

        // Turning it back off is audited too.
        assert!(ledger.set_secret_reveal(false));
        assert!(ledger.reveal_record(id).is_err());
    }

    // ---- N4 + N9: timing and initiator stack ----------------------------

    #[test]
    fn test_timing_and_initiator_stack_roundtrip() {
        let mut ledger = NetworkLedger::new(50, false);
        let id = ledger.append_with_sealed(
            NetworkRecord {
                url: "https://x.example.com/api".into(),
                timing: Some(TimingBreakdown {
                    started_at_ms: 1000.0,
                    ttfb_ms: Some(45.0),
                    duration_ms: Some(90.0),
                }),
                initiator_stack: vec!["at https://x.example.com/app.js:42:9".into()],
                ..Default::default()
            },
            None,
        );
        assert_eq!(id, 1);
        let view = ledger.timing_view("");
        assert_eq!(view["count"], 1);
        assert_eq!(view["rows"][0]["timing"]["ttfb_ms"], 45.0);
        assert_eq!(view["rows"][0]["id"], 1);
    }

    #[test]
    fn test_initiator_stack_query_string_redacted() {
        let stack = "at f (https://a.example.com/x.js?token=secret123:9:3)\n    at g (https://b.example.com/y.js:1:1)";
        let lines = sanitize_initiator_stack(stack);
        assert_eq!(lines.len(), 2);
        assert!(!lines[0].contains("secret123"));
        assert!(lines[0].ends_with("?<REDACTED>"));
        assert!(lines[1].contains("y.js:1:1"));
    }
}

