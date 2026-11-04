//! HTTP client for communicating with a running `dosh-browser-core` server.
//! Direct port of `webresearch/client.py`.

use reqwest::Client as HttpClient;
use serde_json::Value;
use std::collections::HashMap;

/// Client for the browser REST API.
#[derive(Clone)]
pub struct Client {
    pub base_url: String,
    http: HttpClient,
}

impl Client {
    pub fn new(base_url: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            http: HttpClient::new(),
        }
    }

    /// Invoke an agent tool or shell command via `POST /v1/invoke`.
    pub async fn invoke(
        &self,
        name: &str,
        args: HashMap<String, Value>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let endpoint = format!("{}/v1/invoke", self.base_url);
        let body = serde_json::json!({
            "name": name,
            "arguments": args,
        });

        let resp = self.http.post(&endpoint).json(&body).send().await?;
        let res_json: Value = resp.json().await?;
        if let Some(err) = res_json.get("error") {
            return Err(err.as_str().unwrap_or("unknown error").into());
        }
        Ok(res_json.get("result").cloned().unwrap_or(res_json))
    }

    /// Read a page as an LLM-ready document via `POST /v1/read`.
    pub async fn read(
        &self,
        url: &str,
        max_chars: Option<usize>,
        js: Option<&str>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let endpoint = format!("{}/v1/read", self.base_url);
        let body = serde_json::json!({
            "url": url,
            "max_chars": max_chars.unwrap_or(8000),
            "js": js.unwrap_or("auto"),
        });

        let resp = self.http.post(&endpoint).json(&body).send().await?;
        let res_json: Value = resp.json().await?;
        Ok(res_json)
    }

    /// Search the web via `POST /v1/search`.
    pub async fn search(
        &self,
        query: &str,
        max_hits: Option<usize>,
        engine: Option<&str>,
    ) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let endpoint = format!("{}/v1/search", self.base_url);
        let body = serde_json::json!({
            "q": query,
            "max_hits": max_hits.unwrap_or(8),
            "engine": engine.unwrap_or("auto"),
        });

        let resp = self.http.post(&endpoint).json(&body).send().await?;
        let res_json: Value = resp.json().await?;
        Ok(res_json)
    }

    /// Check server health via `GET /health`.
    pub async fn health(&self) -> Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let endpoint = format!("{}/health", self.base_url);
        let resp = self.http.get(&endpoint).send().await?;
        let res_json: Value = resp.json().await?;
        Ok(res_json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_init() {
        let client = Client::new("http://localhost:4747/");
        assert_eq!(client.base_url, "http://localhost:4747");
    }
}

