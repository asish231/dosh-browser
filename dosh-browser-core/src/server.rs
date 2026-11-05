//! `server.rs` — The HTTP server (replaces `webresearch/server.py` + parts of `tools.py`).
//!
//! Exposes the browser capabilities as a REST API for the agent.

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    http::StatusCode,
    response::{Json, Response},
    routing::{get, post},
    Router,
};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

use crate::registry::SessionRegistry;
use crate::session::Session;
use crate::shell::{execute_command, ShellStore};
use reqwest::Client;

/// Shared application state.
#[derive(Clone)]
pub struct AppState {
    /// Backward-compatible default session used by the stateless REST endpoints.
    pub session: Arc<Mutex<Session>>,
    pub store: Arc<ShellStore>,
    /// Registry of live, isolated sessions for the bridged/attached model.
    pub registry: SessionRegistry,
}

impl AppState {
    pub fn new(store: ShellStore) -> Self {
        let client = Client::builder()
            .user_agent("dosh-browser-core/1.0.0")
            .build()
            .unwrap();
        let session = Session::new(client.clone());

        Self {
            session: Arc::new(Mutex::new(session)),
            store: Arc::new(store),
            registry: SessionRegistry::new(client),
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
        // Bridge: session registry + SSH-like persistent attach channel.
        .route("/v1/sessions", get(list_sessions).post(create_session))
        .route("/v1/attach", get(attach_ws))
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

// ---------------------------------------------------------------------------
// Bridge: session registry + SSH-like persistent attach channel
// ---------------------------------------------------------------------------

/// List live sessions in the registry.
async fn list_sessions(State(state): State<AppState>) -> Json<serde_json::Value> {
    let sessions = state.registry.list().await;
    Json(serde_json::json!({ "sessions": sessions, "count": sessions.len() }))
}

/// Create a fresh isolated session, returning its id.
async fn create_session(State(state): State<AppState>) -> Json<serde_json::Value> {
    let (id, _slot) = state.registry.create().await;
    Json(serde_json::json!({ "session_id": id }))
}

/// First frame a client sends over the attach socket.
#[derive(Deserialize)]
struct AttachHandshake {
    /// Session id to attach to; if absent or unknown, a new session is created.
    #[serde(default)]
    session_id: Option<String>,
    /// Refuse the attach if another connection already holds this session.
    #[serde(default)]
    exclusive: bool,
}

/// A command frame sent over an attached socket.
#[derive(Deserialize)]
struct BridgeCommand {
    #[serde(alias = "name")]
    command: String,
    #[serde(default)]
    arguments: serde_json::Value,
}

/// Upgrade an HTTP request to a persistent WebSocket attached to one session.
///
/// This is the "SSH-like" channel: the first message is an `AttachHandshake`
/// naming (or creating) a session; every subsequent message is a `BridgeCommand`
/// executed against THAT session's live state, with the response streamed back
/// over the same socket. Session context persists for the life of the socket and
/// is isolated from other connections' sessions.
async fn attach_ws(ws: WebSocketUpgrade, State(state): State<AppState>) -> Response {
    ws.on_upgrade(move |socket| handle_attach(socket, state))
}

async fn handle_attach(mut socket: WebSocket, state: AppState) {
    // 1. Handshake — the first text frame selects/creates the session.
    let first = match socket.recv().await {
        Some(Ok(Message::Text(t))) => t,
        _ => {
            let _ = socket
                .send(Message::Text(
                    serde_json::json!({ "type": "error", "error": "expected attach handshake" })
                        .to_string()
                        .into(),
                ))
                .await;
            return;
        }
    };

    let handshake: AttachHandshake = serde_json::from_str(&first).unwrap_or(AttachHandshake {
        session_id: None,
        exclusive: false,
    });

    let (resolved_id, slot) = match state
        .registry
        .attach(handshake.session_id.as_deref(), handshake.exclusive)
        .await
    {
        Ok((maybe_id, slot)) => {
            // `attach` returns the requested id for an existing session, or an
            // empty string when it created a new one — recover the real id.
            let id = if maybe_id.is_empty() {
                let session = slot.session.lock().await;
                session.id.clone()
            } else {
                maybe_id
            };
            (id, slot)
        }
        Err(e) => {
            let _ = socket
                .send(Message::Text(
                    serde_json::json!({ "type": "error", "error": e }).to_string().into(),
                ))
                .await;
            return;
        }
    };

    // Confirm the attach.
    let _ = socket
        .send(Message::Text(
            serde_json::json!({
                "type": "attached",
                "session_id": resolved_id,
                "exclusive": handshake.exclusive,
            })
            .to_string()
            .into(),
        ))
        .await;

    // 2. Command loop — every frame runs against THIS session, context persists.
    while let Some(msg) = socket.recv().await {
        match msg {
            Ok(Message::Text(text)) => {
                let cmd: BridgeCommand = match serde_json::from_str(&text) {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = socket
                            .send(Message::Text(
                                serde_json::json!({ "type": "error", "error": format!("bad command frame: {e}") })
                                    .to_string()
                                    .into(),
                            ))
                            .await;
                        continue;
                    }
                };

                let response = run_bridge_command(&state, &slot, &cmd).await;
                if socket.send(Message::Text(response.to_string().into())).await.is_err() {
                    break;
                }
            }
            Ok(Message::Close(_)) | Err(_) => break,
            Ok(Message::Ping(p)) => {
                let _ = socket.send(Message::Pong(p)).await;
            }
            _ => {}
        }
    }

    // 3. Detach when the socket closes.
    state.registry.detach(&resolved_id).await;
}

/// Execute one bridge command against an attached session.
async fn run_bridge_command(
    state: &AppState,
    slot: &Arc<crate::registry::SessionSlot>,
    cmd: &BridgeCommand,
) -> serde_json::Value {
    if cmd.command.starts_with("browser_") {
        let args_map: HashMap<String, serde_json::Value> = match &cmd.arguments {
            serde_json::Value::Object(map) => map.clone().into_iter().collect(),
            _ => HashMap::new(),
        };
        match crate::dispatch::invoke(slot.session.clone(), &cmd.command, &args_map).await {
            Ok(res) => serde_json::json!({ "type": "result", "command": cmd.command, "result": res }),
            Err(e) => serde_json::json!({ "type": "error", "command": cmd.command, "error": e }),
        }
    } else {
        let mut session = slot.session.lock().await;
        match execute_command(&cmd.command, &cmd.arguments, &mut session, &state.store).await {
            Ok(res) => serde_json::json!({ "type": "result", "command": cmd.command, "result": res }),
            Err(e) => {
                serde_json::json!({ "type": "error", "command": cmd.command, "error": e.to_string() })
            }
        }
    }
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

    // --- Bridge (attach) integration tests ---

    use futures_util::{SinkExt, StreamExt};
    use tokio_tungstenite::tungstenite::Message as WsMessage;

    async fn spawn_server() -> String {
        let store = ShellStore::new(None).unwrap();
        let state = AppState::new(store);
        let app = app(state);
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        // Give the server a moment to start accepting.
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
        format!("ws://{}/v1/attach", addr)
    }

    async fn recv_json(
        ws: &mut (impl StreamExt<Item = Result<WsMessage, tokio_tungstenite::tungstenite::Error>> + Unpin),
    ) -> serde_json::Value {
        loop {
            match ws.next().await {
                Some(Ok(WsMessage::Text(t))) => {
                    return serde_json::from_str(&t).unwrap();
                }
                Some(Ok(_)) => continue,
                other => panic!("expected text frame, got {other:?}"),
            }
        }
    }

    #[tokio::test]
    async fn test_bridge_attach_and_persist_context() {
        let url = spawn_server().await;
        let (mut ws, _) = tokio_tungstenite::connect_async(&url).await.unwrap();

        // Handshake: create a new session.
        ws.send(WsMessage::Text("{}".into())).await.unwrap();
        let attached = recv_json(&mut ws).await;
        assert_eq!(attached["type"], "attached");
        let session_id = attached["session_id"].as_str().unwrap().to_string();
        assert!(!session_id.is_empty());

        // Seed a document via the open command so eval has a realm.
        ws.send(WsMessage::Text(
            serde_json::json!({ "command": "open", "arguments": { "url": "https://example.com/" } })
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
        let _ = recv_json(&mut ws).await;

        // Turn 1: set a global over the bridge.
        ws.send(WsMessage::Text(
            serde_json::json!({ "command": "eval", "arguments": { "source": "globalThis.__bridge = 7; 'set'" } })
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
        let _ = recv_json(&mut ws).await;

        // Turn 2: read it back — context MUST persist across bridge frames.
        ws.send(WsMessage::Text(
            serde_json::json!({ "command": "eval", "arguments": { "source": "return String(globalThis.__bridge)" } })
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
        let got = recv_json(&mut ws).await;
        let result = got["result"]["result"].as_str().unwrap_or_default();
        assert!(result.contains('7'), "persisted global lost over bridge: {got:?}");
    }

    #[tokio::test]
    async fn test_bridge_two_connections_are_isolated() {
        let url = spawn_server().await;

        // Connection A creates a session and sets a global.
        let (mut a, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        a.send(WsMessage::Text("{}".into())).await.unwrap();
        let a_attached = recv_json(&mut a).await;
        let a_id = a_attached["session_id"].as_str().unwrap().to_string();
        a.send(WsMessage::Text(
            serde_json::json!({ "command": "open", "arguments": { "url": "https://a.example/" } })
                .to_string().into(),
        )).await.unwrap();
        let _ = recv_json(&mut a).await;
        a.send(WsMessage::Text(
            serde_json::json!({ "command": "eval", "arguments": { "source": "globalThis.__who='A'; 'ok'" } })
                .to_string().into(),
        )).await.unwrap();
        let _ = recv_json(&mut a).await;

        // Connection B attaches to a DIFFERENT (new) session.
        let (mut b, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        b.send(WsMessage::Text("{}".into())).await.unwrap();
        let b_attached = recv_json(&mut b).await;
        let b_id = b_attached["session_id"].as_str().unwrap().to_string();
        assert_ne!(a_id, b_id, "each fresh attach must get its own session");

        // B must NOT see A's global — sessions are isolated.
        b.send(WsMessage::Text(
            serde_json::json!({ "command": "open", "arguments": { "url": "https://b.example/" } })
                .to_string().into(),
        )).await.unwrap();
        let _ = recv_json(&mut b).await;
        b.send(WsMessage::Text(
            serde_json::json!({ "command": "eval", "arguments": { "source": "return String(globalThis.__who)" } })
                .to_string().into(),
        )).await.unwrap();
        let b_got = recv_json(&mut b).await;
        let b_result = b_got["result"]["result"].as_str().unwrap_or_default();
        assert!(
            !b_result.contains('A'),
            "session B leaked session A's state: {b_got:?}"
        );
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
