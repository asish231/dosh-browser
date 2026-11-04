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
use std::collections::HashMap;
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
    #[serde(alias = "name")]
    pub command: String,
    #[serde(default)]
    pub arguments: serde_json::Value,
}

#[derive(Deserialize)]
pub struct AskRequest {
    #[serde(alias = "q")]
    pub query: String,
    pub max_hits: Option<usize>,
}

#[derive(Deserialize)]
pub struct ReadRequest {
    pub url: String,
    pub max_chars: Option<usize>,
    pub js: Option<String>,
}

#[derive(Deserialize)]
pub struct SearchRequest {
    pub q: String,
    pub max_hits: Option<usize>,
    pub engine: Option<String>,
}

/// The Axum router.
pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/v1/tools", get(list_tools))
        .route("/v1/invoke", post(invoke_command))
        .route("/v1/read", post(read_page))
        .route("/v1/search", post(search_page))
        .route("/v1/ask", post(ask))
        .with_state(state)
}

/// Health check endpoint.
async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "ok": true,
        "engine": "doshgentv8",
        "lane": "dosh-browser-core"
    }))
}

/// List available tools (matches `tools.py`).
async fn list_tools() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "tools": crate::tools::get_tools()
    }))
}

/// Execute a shell command or dispatch tool.
async fn invoke_command(
    State(state): State<AppState>,
    Json(req): Json<CommandRequest>,
) -> Json<serde_json::Value> {
    if req.command.starts_with("browser_") {
        let args_map: HashMap<String, serde_json::Value> = match req.arguments {
            serde_json::Value::Object(map) => map.into_iter().collect(),
            _ => HashMap::new(),
        };
        match crate::dispatch::invoke(state.session.clone(), &req.command, &args_map).await {
            Ok(res) => Json(serde_json::json!({ "status": "success", "result": res })),
            Err(e) => Json(serde_json::json!({ "status": "error", "error": e })),
        }
    } else {
        let mut session = state.session.lock().await;
        match execute_command(&req.command, &req.arguments, &mut session, &state.store).await {
            Ok(res) => Json(serde_json::json!({ "status": "success", "result": res })),
            Err(e) => Json(serde_json::json!({ "status": "error", "error": e.to_string() })),
        }
    }
}

/// Read a page (REST endpoint).
async fn read_page(
    Json(req): Json<ReadRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let js_mode = match req.js.as_deref().unwrap_or("auto") {
        "always" => crate::read::JsMode::Always,
        "never" => crate::read::JsMode::Never,
        _ => crate::read::JsMode::Auto,
    };
    match crate::read::read(&req.url, req.max_chars.unwrap_or(8000), js_mode).await {
        Ok(page) => Ok(Json(serde_json::to_value(&page).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?)),
        Err(_) => Err(StatusCode::BAD_GATEWAY),
    }
}

/// Search the web (REST endpoint).
async fn search_page(
    Json(req): Json<SearchRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let client = Client::builder()
        .user_agent("dosh-browser-core/1.0.0")
        .build()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let engine = req.engine.unwrap_or_else(|| "auto".to_string());
    match crate::search::search(&client, &req.q, req.max_hits.unwrap_or(8), &engine).await {
        Ok(results) => Ok(Json(serde_json::to_value(&results).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?)),
        Err(_) => Err(StatusCode::BAD_GATEWAY),
    }
}

/// Search/ask endpoint.
async fn ask(
    State(_state): State<AppState>,
    Json(req): Json<AskRequest>,
) -> Json<serde_json::Value> {
    let client = Client::builder()
        .user_agent("dosh-browser-core/1.0.0")
        .build()
        .unwrap_or_default();
    let hits = match crate::search::search(&client, &req.query, req.max_hits.unwrap_or(8), "auto").await {
        Ok(res) => serde_json::to_value(&res).unwrap_or_default(),
        Err(e) => serde_json::json!({ "error": e.to_string() }),
    };

    Json(serde_json::json!({
        "query": req.query,
        "results": hits
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
        assert!(json["tools"].as_array().unwrap().len() >= 10);
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
    async fn test_invoke_browser_tool_dispatch() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let req_body = serde_json::json!({
            "name": "browser_actions",
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
        assert!(json["result"].is_array());
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

    #[tokio::test]
    async fn test_health() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let response = app
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["ok"], true);
        assert_eq!(json["engine"], "doshgentv8");
    }

    #[tokio::test]
    async fn test_ask_endpoint() {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);

        let req_body = serde_json::json!({
            "query": "rust lang",
            "max_hits": 3
        });

        let response = app
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/v1/ask")
                    .header("content-type", "application/json")
                    .body(Body::from(serde_json::to_string(&req_body).unwrap()))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(json["query"], "rust lang");
        assert!(json.get("results").is_some());
    }
}
