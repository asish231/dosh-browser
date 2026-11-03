//! `fetch.rs` — Native Rust HTTP transport (replaces `webresearch/fetch.py`).
//!
//! Uses `reqwest` for HTTP/2, TLS, and cookie jar management.
//! Matches the `FetchResult` contract of the Python product.

use reqwest::Client;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// The result shape expected by the engine's `host_fetch` wrapper.
#[derive(Serialize, Deserialize, Clone)]
pub struct FetchResult {
    pub url: String,
    pub final_url: String,
    pub status: u16,
    pub content_type: String,
    pub headers: HashMap<String, String>,
    pub kind: String,
    pub body: String,
    pub truncated: bool,
}

impl FetchResult {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_default()
    }
}

pub fn classify(content_type: &str) -> &'static str {
    let mime = content_type.split(';').next().unwrap_or("").trim().to_lowercase();
    if mime.contains("text/html") || mime.contains("application/xhtml") {
        "html"
    } else if mime.contains("application/json") {
        "json"
    } else if mime.starts_with("text/") {
        "text"
    } else {
        "other"
    }
}

pub fn navigate_headers(url: &str) -> HeaderMap {
    let mut h = HeaderMap::new();
    h.insert("sec-fetch-dest", HeaderValue::from_static("document"));
    h.insert("sec-fetch-mode", HeaderValue::from_static("navigate"));
    h.insert("sec-fetch-site", HeaderValue::from_static("none"));
    h.insert("sec-fetch-user", HeaderValue::from_static("?1"));
    h.insert(
        "Accept",
        HeaderValue::from_static(
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        ),
    );
    h
}

pub fn decode_body(raw: &[u8], content_type: &str) -> String {
    let mut charset = "utf-8";
    for part in content_type.split(';') {
        let p = part.trim();
        if p.to_lowercase().starts_with("charset=") {
            charset = &p[8..].trim().trim_matches('"').trim_matches('\'');
            break;
        }
    }
    match encoding_rs::Encoding::for_label(charset.as_bytes()) {
        Some(enc) => {
            let (cow, _, had_errors) = enc.decode(raw);
            if had_errors {
                cow.into_owned() + " [decoding error]"
            } else {
                cow.into_owned()
            }
        }
        None => String::from_utf8_lossy(raw).into_owned(),
    }
}

pub async fn fetch(
    client: &Client,
    url: &str,
    method: &str,
    data: Option<String>,
    headers_json: &str,
    max_bytes: usize,
) -> Result<FetchResult, reqwest::Error> {
    let mut req_headers = navigate_headers(url);
    if !headers_json.is_empty() {
        if let Ok(map) = serde_json::from_str::<HashMap<String, String>>(headers_json) {
            for (k, v) in map {
                if let (Ok(kh), Ok(vh)) = (HeaderName::try_from(k), HeaderValue::try_from(v)) {
                    req_headers.insert(kh, vh);
                }
            }
        }
    }

    let mut builder = client.request(method.parse().unwrap_or(reqwest::Method::GET), url);
    builder = builder.headers(req_headers);
    if let Some(body) = data {
        builder = builder.body(body);
    }

    let response = builder.send().await?;
    let final_url = response.url().as_str().to_string();
    let status = response.status().as_u16();
    let headers: HashMap<String, String> = response
        .headers()
        .iter()
        .map(|(k, v)| (k.as_str().to_string(), v.to_str().unwrap_or("").to_string()))
        .collect();
    let content_type = response
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    let raw = response.bytes().await.unwrap_or_default();
    let truncated = raw.len() > max_bytes;
    let body_bytes = if truncated { &raw[..max_bytes] } else { &raw[..] };
    let body = decode_body(body_bytes, &content_type);
    let kind = classify(&content_type);

    Ok(FetchResult {
        url: url.to_string(),
        final_url,
        status,
        content_type,
        headers,
        kind: kind.to_string(),
        body,
        truncated,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_classify() {
        assert_eq!(classify("text/html; charset=utf-8"), "html");
        assert_eq!(classify("application/json"), "json");
        assert_eq!(classify("image/png"), "other");
    }

    #[test]
    fn test_decode_body() {
        let raw = b"hello world";
        assert_eq!(decode_body(raw, "text/html; charset=utf-8"), "hello world");
    }
}
