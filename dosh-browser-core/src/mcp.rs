use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::session::Session;
use crate::shell::{execute_command, ShellStore};

pub const MCP_PROTOCOL_VERSION: &str = "2024-11-05";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub id: Option<Value>,
    pub method: String,
    #[serde(default)]
    pub params: Option<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
}

pub fn get_tools_list() -> Value {
    json!([
        {
            "name": "browser_search",
            "description": "Search the web with automatic fallback across Bing, Google, DuckDuckGo, and Yahoo. Returns clean markdown hits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Search query terms" },
                    "engine": { "type": "string", "description": "Search engine: auto (default), bing, google, ddg, yahoo, lite", "enum": ["auto", "bing", "google", "ddg", "yahoo", "lite"] },
                    "max_hits": { "type": "integer", "default": 8 }
                },
                "required": ["query"]
            }
        },
        {
            "name": "browser_navigate",
            "description": "Open a URL, execute client-side JavaScript in V8, auto-dismiss consent banners, and return the structured Page snapshot.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": { "type": "string", "description": "The absolute HTTP/HTTPS URL to open" },
                    "max_chars": { "type": "integer", "default": 8000 }
                },
                "required": ["url"]
            }
        },
        {
            "name": "browser_click",
            "description": "Click an interactive element by reference ref (e.g. 'e1', 'e2') or navigate a link.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ref": { "type": "string", "description": "The element reference marker" },
                    "max_chars": { "type": "integer", "default": 8000 },
                    "stealth": { "type": "boolean", "default": false }
                },
                "required": ["ref"]
            }
        },
        {
            "name": "browser_type",
            "description": "Type text into an input field by ref using humanized keystroke cadence.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ref": { "type": "string", "description": "The input element reference" },
                    "text": { "type": "string", "description": "The text string to type into the field" },
                    "max_chars": { "type": "integer", "default": 8000 }
                },
                "required": ["ref", "text"]
            }
        },
        {
            "name": "browser_actions",
            "description": "List the full action surface of the current page: every reachable control.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_actions": { "type": "integer", "default": 200 }
                }
            }
        },
        {
            "name": "browser_shell",
            "description": "Execute a dosh-browser terminal command.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": { "type": "string", "description": "Command string" },
                    "arguments": { "type": "object" }
                },
                "required": ["command"]
            }
        },
        {
            "name": "browser_login",
            "description": "Log in to a site using credentials with honest verification.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": { "type": "string" },
                    "username": { "type": "string" },
                    "password": { "type": "string" }
                },
                "required": ["url", "username", "password"]
            }
        },
        {
            "name": "browser_submit_otp",
            "description": "Submit a verification OTP code the user received.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "checkpoint_id": { "type": "string" },
                    "code": { "type": "string" }
                },
                "required": ["checkpoint_id", "code"]
            }
        },
        {
            "name": "browser_acd",
            "description": "Retrieve the full Agent Content Document (ACD 2.0) with micro-ML semantic classifications (ArticleBody, ProductCard, SearchResult), DOM tag provenance (CSS path & XPath), and clustered collections.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "max_blocks": { "type": "integer", "default": 200 }
                }
            }
        },
        {
            "name": "browser_query",
            "description": "Query semantic content blocks in the current page by term.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "term": { "type": "string", "description": "Search term" },
                    "limit": { "type": "integer", "default": 40 }
                },
                "required": ["term"]
            }
        },
        {
            "name": "browser_slice",
            "description": "Slice a range of content blocks by block ID (e.g. from 'b1' to 'b10').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start": { "type": "string", "description": "Starting block ID" },
                    "end": { "type": "string", "description": "Ending block ID" },
                    "limit": { "type": "integer", "default": 120 }
                }
            }
        },
        {
            "name": "browser_wasm",
            "description": "Inspect and control Liftoff WASM fuel traps, query remaining fuel, anti-tarpit state, and challenge exports.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fuel": { "type": "integer", "description": "Set fuel units limit for Liftoff WASM execution" }
                }
            }
        },
        {
            "name": "browser_clock",
            "description": "Inspect and manipulate the deterministic virtual host clock.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "set": { "type": "integer", "description": "Set virtual clock timestamp in milliseconds" },
                    "advance": { "type": "integer", "description": "Advance virtual clock by milliseconds" }
                }
            }
        },
        {
            "name": "browser_gate",
            "description": "Inspect and toggle runtime capability gates: dynamic code generation (eval), Liftoff WASM, and network access.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "eval": { "type": "boolean", "description": "Enable or disable eval and Function constructor" },
                    "wasm": { "type": "boolean", "description": "Enable or disable WebAssembly execution" },
                    "net": { "type": "boolean", "description": "Enable or disable network access" }
                }
            }
        },
        {
            "name": "browser_heap",
            "description": "Report V8 128MB pruned heap statistics, allocated bytes, and GC policy.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ])
}

pub async fn handle_mcp_message(
    req: JsonRpcRequest,
    session: &Arc<Mutex<Session>>,
    store: &Arc<ShellStore>,
) -> Option<JsonRpcResponse> {
    let id = req.id;
    match req.method.as_str() {
        "initialize" => Some(JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id,
            result: Some(json!({
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "dosh-browser-core",
                    "version": "1.0.0"
                }
            })),
            error: None,
        }),
        "notifications/initialized" => None,
        "tools/list" => Some(JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id,
            result: Some(json!({
                "tools": get_tools_list()
            })),
            error: None,
        }),
        "tools/call" => {
            let params = req.params.unwrap_or(Value::Null);
            let name = params["name"].as_str().unwrap_or("");
            let arguments = &params["arguments"];

            let result_text = match name {
                "browser_navigate" => {
                    let url = arguments["url"].as_str().unwrap_or("");
                    let max_chars = arguments["max_chars"].as_u64().unwrap_or(8000) as usize;
                    let mut sess = session.lock().await;
                    match sess.goto(url, max_chars).await {
                        Ok(p) => p.view,
                        Err(e) => format!("Error navigating: {}", e),
                    }
                }
                "browser_search" => {
                    let mut args_map: std::collections::HashMap<String, Value> = match arguments {
                        Value::Object(m) => m.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
                        _ => std::collections::HashMap::new(),
                    };
                    if !args_map.contains_key("q") {
                        if let Some(query) = args_map.get("query") {
                            args_map.insert("q".to_string(), query.clone());
                        }
                    }
                    match crate::dispatch::invoke(session.clone(), "browser_search", &args_map).await {
                        Ok(res) => res.get("view").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| res.to_string()),
                        Err(e) => format!("Search error: {}", e),
                    }
                }
                "browser_read" => {
                    let args_map: std::collections::HashMap<String, Value> = match arguments {
                        Value::Object(m) => m.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
                        _ => std::collections::HashMap::new(),
                    };
                    match crate::dispatch::invoke(session.clone(), "browser_read", &args_map).await {
                        Ok(res) => res.get("view").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| res.to_string()),
                        Err(e) => format!("Read error: {}", e),
                    }
                }
                "browser_goto" => {
                    let args_map: std::collections::HashMap<String, Value> = match arguments {
                        Value::Object(m) => m.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
                        _ => std::collections::HashMap::new(),
                    };
                    match crate::dispatch::invoke(session.clone(), "browser_goto", &args_map).await {
                        Ok(res) => res.get("view").and_then(|v| v.as_str()).map(|s| s.to_string()).unwrap_or_else(|| res.to_string()),
                        Err(e) => format!("Goto error: {}", e),
                    }
                }
                "browser_click" => {
                    let r = arguments["ref"].as_str().unwrap_or("");
                    let mut sess = session.lock().await;
                    let args = json!({ "ref": r });
                    match execute_command("act", &args, &mut sess, store).await {
                        Ok(res) => res.to_string(),
                        Err(e) => format!("Error clicking {}: {}", r, e),
                    }
                }
                "browser_type" => {
                    let r = arguments["ref"].as_str().unwrap_or("");
                    let text = arguments["text"].as_str().unwrap_or("");
                    let mut sess = session.lock().await;
                    let args = json!({ "ref": r, "text": text });
                    match execute_command("act", &args, &mut sess, store).await {
                        Ok(res) => res.to_string(),
                        Err(e) => format!("Error typing into {}: {}", r, e),
                    }
                }
                "browser_actions" => {
                    let sess = session.lock().await;
                    let max_actions = arguments["max_actions"].as_u64().unwrap_or(200) as usize;
                    let targets = crate::actions::extract_actions(&sess.current_tab().html, &sess.current_tab().url, max_actions);
                    serde_json::to_string_pretty(&targets).unwrap_or_default()
                }
                "browser_acd" => {
                    let sess = session.lock().await;
                    let tab = sess.current_tab();
                    let max_blocks = arguments["max_blocks"].as_u64().unwrap_or(200) as usize;
                    let title = tab.page().map(|p| p.title).unwrap_or_default();
                    let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &serde_json::json!({}), max_blocks);
                    serde_json::to_string_pretty(&acd).unwrap_or_default()
                }
                "browser_query" => {
                    let sess = session.lock().await;
                    let tab = sess.current_tab();
                    let term = arguments["term"].as_str().unwrap_or("");
                    let limit = arguments["limit"].as_u64().unwrap_or(40) as usize;
                    let title = tab.page().map(|p| p.title).unwrap_or_default();
                    let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &serde_json::json!({}), 400);
                    let results = crate::acd::query_acd(&acd, term, limit);
                    serde_json::to_string_pretty(&results).unwrap_or_default()
                }
                "browser_slice" => {
                    let sess = session.lock().await;
                    let tab = sess.current_tab();
                    let start = arguments["start"].as_str().unwrap_or("");
                    let end = arguments["end"].as_str().unwrap_or("");
                    let limit = arguments["limit"].as_u64().unwrap_or(120) as usize;
                    let title = tab.page().map(|p| p.title).unwrap_or_default();
                    let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &serde_json::json!({}), 400);
                    match crate::acd::slice_acd(&acd, start, end, limit) {
                        Ok(results) => serde_json::to_string_pretty(&results).unwrap_or_default(),
                        Err(e) => format!("Slice error: {}", e),
                    }
                }
                "browser_shell" => {
                    let cmd = arguments["command"].as_str().unwrap_or("help");
                    let sub_args = &arguments["arguments"];
                    let mut sess = session.lock().await;
                    match execute_command(cmd, sub_args, &mut sess, store).await {
                        Ok(res) => res.to_string(),
                        Err(e) => format!("Shell error: {}", e),
                    }
                }
                "browser_wasm" => {
                    let mut sess = session.lock().await;
                    match execute_command("wasm", arguments, &mut sess, store).await {
                        Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                        Err(e) => format!("WASM error: {}", e),
                    }
                }
                "browser_clock" => {
                    let mut sess = session.lock().await;
                    match execute_command("clock", arguments, &mut sess, store).await {
                        Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                        Err(e) => format!("Clock error: {}", e),
                    }
                }
                "browser_gate" => {
                    let mut sess = session.lock().await;
                    match execute_command("gate", arguments, &mut sess, store).await {
                        Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                        Err(e) => format!("Gate error: {}", e),
                    }
                }
                "browser_heap" => {
                    let mut sess = session.lock().await;
                    match execute_command("heap", arguments, &mut sess, store).await {
                        Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                        Err(e) => format!("Heap error: {}", e),
                    }
                }
                _ => {
                    if name.starts_with("browser_") {
                        let args_map: std::collections::HashMap<String, Value> = match arguments {
                            Value::Object(m) => m.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
                            _ => std::collections::HashMap::new(),
                        };
                        match crate::dispatch::invoke(session.clone(), name, &args_map).await {
                            Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                            Err(e) => format!("Dispatch error: {}", e),
                        }
                    } else {
                        let mut sess = session.lock().await;
                        match execute_command(name, arguments, &mut sess, store).await {
                            Ok(res) => serde_json::to_string_pretty(&res).unwrap_or_default(),
                            Err(e) => format!("Command error: {}", e),
                        }
                    }
                }
            };

            Some(JsonRpcResponse {
                jsonrpc: "2.0".to_string(),
                id,
                result: Some(json!({
                    "content": [
                        {
                            "type": "text",
                            "text": result_text
                        }
                    ]
                })),
                error: None,
            })
        }
        _ => Some(JsonRpcResponse {
            jsonrpc: "2.0".to_string(),
            id,
            result: None,
            error: Some(json!({
                "code": -32601,
                "message": format!("Method not found: {}", req.method)
            })),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use reqwest::Client;

    #[tokio::test]
    async fn test_mcp_initialize() {
        let store = Arc::new(ShellStore::new(None).unwrap());
        let session = Arc::new(Mutex::new(Session::new(Client::new())));

        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(json!(1)),
            method: "initialize".to_string(),
            params: None,
        };

        let resp = handle_mcp_message(req, &session, &store).await;
        assert!(resp.is_some());
        let r = resp.unwrap();
        assert_eq!(r.jsonrpc, "2.0");
        assert_eq!(r.id, Some(json!(1)));
        assert_eq!(r.result.unwrap()["serverInfo"]["name"], "dosh-browser-core");
    }

    #[tokio::test]
    async fn test_mcp_tools_list() {
        let store = Arc::new(ShellStore::new(None).unwrap());
        let session = Arc::new(Mutex::new(Session::new(Client::new())));

        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(json!(2)),
            method: "tools/list".to_string(),
            params: None,
        };

        let resp = handle_mcp_message(req, &session, &store).await;
        assert!(resp.is_some());
        let tools = resp.unwrap().result.unwrap()["tools"].as_array().unwrap().clone();
        assert!(tools.len() >= 6);
        assert!(tools.iter().any(|t| t["name"] == "browser_navigate"));
        assert!(tools.iter().any(|t| t["name"] == "browser_shell"));
    }

    #[tokio::test]
    async fn test_mcp_tools_call_shell() {
        let store = Arc::new(ShellStore::new(None).unwrap());
        let session = Arc::new(Mutex::new(Session::new(Client::new())));

        let req = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(json!(3)),
            method: "tools/call".to_string(),
            params: Some(json!({
                "name": "browser_shell",
                "arguments": {
                    "command": "help"
                }
            })),
        };

        let resp = handle_mcp_message(req, &session, &store).await;
        assert!(resp.is_some());
        let res = resp.unwrap();
        assert!(res.error.is_none());
        let text = res.result.unwrap()["content"][0]["text"].as_str().unwrap().to_string();
        assert!(text.contains("commands"));
    }

    #[tokio::test]
    async fn test_mcp_tools_call_wasm_and_clock() {
        let store = Arc::new(ShellStore::new(None).unwrap());
        let session = Arc::new(Mutex::new(Session::new(Client::new())));

        // Test browser_wasm via MCP
        let req_wasm = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(json!(4)),
            method: "tools/call".to_string(),
            params: Some(json!({
                "name": "browser_wasm",
                "arguments": {
                    "fuel": 75000
                }
            })),
        };
        let resp_wasm = handle_mcp_message(req_wasm, &session, &store).await.unwrap();
        assert!(resp_wasm.error.is_none());
        let text_wasm = resp_wasm.result.unwrap()["content"][0]["text"].as_str().unwrap().to_string();
        assert!(text_wasm.contains("75000"));

        // Test browser_clock via MCP
        let req_clock = JsonRpcRequest {
            jsonrpc: "2.0".to_string(),
            id: Some(json!(5)),
            method: "tools/call".to_string(),
            params: Some(json!({
                "name": "browser_clock",
                "arguments": {
                    "set": 1700000000000u64
                }
            })),
        };
        let resp_clock = handle_mcp_message(req_clock, &session, &store).await.unwrap();
        assert!(resp_clock.error.is_none());
        let text_clock = resp_clock.result.unwrap()["content"][0]["text"].as_str().unwrap().to_string();
        assert!(text_clock.contains("1700000000000"));
    }
}
