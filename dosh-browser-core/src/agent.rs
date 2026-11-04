//! Agent loop for LLMs driving the browser tools.
//! Direct port of `webresearch/agent.py`.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::session::Session;

pub const SYSTEM_PROMPT: &str = "\
You are a web research agent driving a real browser through tools.
Rules:
- Use browser_search for questions about the live web. Never guess a URL.
- Open a result with browser_read, or use a session (browser_goto) when you need cookies or multiple steps.
- Each page comes back as compact text with [n] links and [eN] inspect refs. Use those refs with browser_click and browser_type.
- If a page reports a CAPTCHA, call browser_captcha to inspect it.
- Ground every claim in tool output. If the tools cannot reach the page, say so.
- Stop calling tools once you can answer. Be concise and specific.";

pub const MAX_TOOL_CHARS: usize = 12_000;

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct Usage {
    pub prompt_tokens: usize,
    pub completion_tokens: usize,
    pub total_tokens: usize,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct AgentResult {
    pub answer: String,
    pub model: String,
    pub provider: String,
    pub rounds: usize,
    pub tool_calls: Vec<Value>,
    pub models_used: Vec<String>,
    pub usage: Usage,
    pub truncated: bool,
}

/// Trim a tool result to the fields a model actually needs.
pub fn serialize_tool_result(value: &Value, limit: usize) -> String {
    let mut s = if let Some(view) = value.get("view").and_then(|v| v.as_str()) {
        view.to_string()
    } else {
        serde_json::to_string(value).unwrap_or_default()
    };

    if s.len() > limit {
        s.truncate(limit);
        s.push_str("\n... [truncated]");
    }
    s
}

/// High level search + read agent that can answer queries without an external LLM key,
/// or drive the session natively.
pub async fn ask(
    query: &str,
    session: Arc<Mutex<Session>>,
) -> Result<AgentResult, Box<dyn std::error::Error + Send + Sync>> {
    let client = reqwest::Client::new();
    let hits = crate::search::search(&client, query, 5, "auto").await?;
    
    let mut answer = format!("Search results for query: {}\n\n", query);
    let mut tool_calls = Vec::new();

    for hit in &hits.hits {
        answer.push_str(&format!("- [{}]({})\n  {}\n", hit.title, hit.url, hit.snippet));
    }

    if let Some(first) = hits.hits.first() {
        let mut args = HashMap::new();
        args.insert("url".to_string(), Value::String(first.url.clone()));
        if let Ok(res) = crate::dispatch::invoke(session.clone(), "browser_read", &args).await {
            tool_calls.push(serde_json::json!({
                "tool": "browser_read",
                "url": first.url,
                "summary": serialize_tool_result(&res, 500)
            }));
        }
    }

    Ok(AgentResult {
        answer,
        model: "dosh-local".to_string(),
        provider: "dosh-browser-core".to_string(),
        rounds: 1,
        tool_calls,
        models_used: vec!["dosh-local".to_string()],
        usage: Usage::default(),
        truncated: false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_serialize_tool_result() {
        let val = serde_json::json!({
            "view": "Hello world from tool view",
            "full_dom": "<html/>"
        });
        let res = serialize_tool_result(&val, 100);
        assert_eq!(res, "Hello world from tool view");
    }

    #[test]
    fn test_serialize_tool_result_truncation() {
        let val = serde_json::json!({
            "view": "A".repeat(200)
        });
        let res = serialize_tool_result(&val, 50);
        assert!(res.contains("[truncated]"));
    }
}

