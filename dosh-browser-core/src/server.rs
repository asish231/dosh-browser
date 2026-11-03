//! `server.rs` — The HTTP server (replaces `webresearch/server.py` + parts of `tools.py`).
//!
//! Exposes the browser capabilities as a REST API for the agent.

use axum::{
    extract::State,
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde::Deserialize;
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::session::Session;
use crate::shell::{execute_command, ShellStore};
use reqwest::Client;

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    pub session: Arc<Mutex<Session>>,
    pub store: Arc<ShellStore>,
}

impl AppState {
    pub fn new(store: ShellStore) -> Self {
        let client = Client::builder()
            .user_agent("dosh-browser-core/1.0.0")
            .build()
            .unwrap();
        let session = Session::new(client);
        
        Self {
            session: Arc::new(Mutex::new(session)),
            store: Arc::new(store),
        }
    }
}

#[derive(Deserialize)]
pub struct CommandRequest {
    pub command: String,
    #[serde(default)]
    pub arguments: serde_json::Value,
}

#[derive(Deserialize)]
pub struct AskRequest {
    pub query: String,
    pub max_hits: Option<usize>,
}

/// The Axum router.
pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/v1/tools", get(list_tools))
        .route("/v1/invoke", post(invoke_command))
        .route("/v1/ask", post(ask))
        .with_state(state)
}

/// List available tools (matches `tools.py`).
async fn list_tools() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "browser_shell",
                    "description": "Agent-native browser terminal.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": { "type": "string" },
                            "arguments": { "type": "object" }
                        }
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_search",
                    "description": "Search the web.",
                    "parameters": { "type": "object", "properties": { "q": { "type": "string" } } }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "browser_goto",
                    "description": "Navigate to a URL.",
                    "parameters": { "type": "object", "properties": { "url": { "type": "string" } } }
                }
            }
        ]
    }))
}

/// Execute a shell command.
async fn invoke_command(
    State(state): State<AppState>,
    Json(req): Json<CommandRequest>,
) -> Json<serde_json::Value> {
    let mut session = state.session.lock().await;
    match execute_command(&req.command, &req.arguments, &mut session, &state.store).await {
        Ok(res) => Json(serde_json::json!({ "status": "success", "result": res })),
        Err(e) => Json(serde_json::json!({ "status": "error", "error": e.to_string() })),
    }
}

/// Simple search endpoint (placeholder).
async fn ask(
    State(state): State<AppState>,
    Json(req): Json<AskRequest>,
) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "query": req.query,
        "response": "Search placeholder - Tavily integration needed"
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use axum::body::to_bytes;
    use tower::ServiceExt;

    #[tokio::test]
    async fn test_list_tools() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/v1/tools")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert!(json["tools"].is_array());
    }

    #[tokio::test]
    async fn test_invoke_help() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let req_body = serde_json::json!({
            "command": "help",
            "arguments": {}
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/invoke")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_string(&req_body).unwrap()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["status"], "success");
        assert!(json["result"]["commands"].is_array());
    }

    #[tokio::test]
    async fn test_invoke_status() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let req_body = serde_json::json!({
            "command": "status",
            "arguments": {}
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/invoke")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_string(&req_body).unwrap()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["status"], "success");
        assert!(json["result"]["tabs"].is_number());
    }
}
