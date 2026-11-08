//! Canonical `BrowserControl` tool dispatch router (Audit P1-01).
//! Shared across MCP, OpenAI tools, REST `/v1/invoke`, and CLI adapters so
//! every advertised operation has one authoritative implementation.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use serde_json::{json, Value};

use crate::session::Session;
use crate::shell::{execute_command, ShellStore};

/// Invokes a named browser tool with JSON arguments.
pub async fn invoke(
    session: Arc<Mutex<Session>>,
    name: &str,
    args: &HashMap<String, Value>,
) -> Result<Value, String> {
    let args_val = serde_json::to_value(args).unwrap_or_else(|_| json!({}));
    match name {
        "browser_search" => {
            let q = args
                .get("q")
                .or_else(|| args.get("query"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let max_hits = args.get("max_hits").and_then(|v| v.as_u64()).unwrap_or(8) as usize;
            let engine = args.get("engine").and_then(|v| v.as_str()).unwrap_or("auto");
            let client = reqwest::Client::new();
            let res = crate::search::search(&client, q, max_hits, engine)
                .await
                .map_err(|e| e.to_string())?;
            serde_json::to_value(&res).map_err(|e| e.to_string())
        }
        "browser_read" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let max_chars = args.get("max_chars").and_then(|v| v.as_u64()).unwrap_or(8000) as usize;
            let js_str = args.get("js").and_then(|v| v.as_str()).unwrap_or("auto");
            let js_mode = crate::read::JsMode::from_str(js_str);
            let page = crate::read::read(url, max_chars, js_mode).await?;
            serde_json::to_value(&page).map_err(|e| e.to_string())
        }
        "browser_goto" | "browser_navigate" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let max_chars = args.get("max_chars").and_then(|v| v.as_u64()).unwrap_or(8000) as usize;
            let mut sess = session.lock().await;
            let page = sess.goto(url, max_chars).await.map_err(|e| e.to_string())?;
            serde_json::to_value(&page).map_err(|e| e.to_string())
        }
        "browser_click" => {
            let mut sess = session.lock().await;
            let store = ShellStore::new(None).map_err(|e| e.to_string())?;
            execute_command("act", &args_val, &mut sess, &store)
                .await
                .map_err(|e| e.to_string())
        }
        "browser_type" => {
            let mut sess = session.lock().await;
            let store = ShellStore::new(None).map_err(|e| e.to_string())?;
            execute_command("act", &args_val, &mut sess, &store)
                .await
                .map_err(|e| e.to_string())
        }
        "browser_actions" => {
            let max_actions = args.get("max_actions").and_then(|v| v.as_u64()).unwrap_or(200) as usize;
            let sess = session.lock().await;
            let actions = crate::actions::extract_actions(&sess.current_tab().html, &sess.current_tab().url, max_actions);
            serde_json::to_value(&actions).map_err(|e| e.to_string())
        }
        "browser_extract" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let fields_map: HashMap<String, String> = args
                .get("fields")
                .and_then(|v| v.as_object())
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                        .collect()
                })
                .unwrap_or_default();
            let mut sess = session.lock().await;
            if !url.is_empty() && sess.current_tab().url != url && !url.starts_with("mock://") {
                let _ = sess.goto(url, 8000).await.map_err(|e| e.to_string())?;
            }
            let tab = sess.current_tab();
            let res = crate::extract::extract_fields(&tab.html, &tab.url, &fields_map);
            serde_json::to_value(&res).map_err(|e| e.to_string())
        }
        "browser_login" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let username = args.get("username").and_then(|v| v.as_str()).unwrap_or("");
            let password = args.get("password").and_then(|v| v.as_str()).unwrap_or("");
            let mut sess = session.lock().await;
            sess.login(url, username, password).await
        }
        "browser_submit_otp" => {
            let cp = args
                .get("checkpoint_id")
                .or_else(|| args.get("checkpoint"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let code = args.get("code").and_then(|v| v.as_str()).unwrap_or("");
            let mut sess = session.lock().await;
            sess.submit_otp(cp, code).await
        }
        "browser_login_wall" => {
            let sess = session.lock().await;
            let wall = sess.detect_login_wall();
            serde_json::to_value(&wall).map_err(|e| e.to_string())
        }
        "browser_frames" => {
            let mut sess = session.lock().await;
            let store = ShellStore::new(None).map_err(|e| e.to_string())?;
            execute_command("frames", &args_val, &mut sess, &store)
                .await
                .map_err(|e| e.to_string())
        }
        "browser_acd" => {
            let max_blocks = args.get("max_blocks").and_then(|v| v.as_u64()).unwrap_or(200) as usize;
            let sess = session.lock().await;
            let tab = sess.current_tab();
            let title = tab.page().map(|p| p.title).unwrap_or_default();
            let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &json!({}), max_blocks);
            serde_json::to_value(&acd).map_err(|e| e.to_string())
        }
        "browser_query" => {
            let term = args.get("term").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(40) as usize;
            let sess = session.lock().await;
            let tab = sess.current_tab();
            let title = tab.page().map(|p| p.title).unwrap_or_default();
            let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &json!({}), 400);
            let results = crate::acd::query_acd(&acd, term, limit);
            serde_json::to_value(&results).map_err(|e| e.to_string())
        }
        "browser_slice" => {
            let start = args.get("start").and_then(|v| v.as_str()).unwrap_or("");
            let end = args.get("end").and_then(|v| v.as_str()).unwrap_or("");
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(120) as usize;
            let sess = session.lock().await;
            let tab = sess.current_tab();
            let title = tab.page().map(|p| p.title).unwrap_or_default();
            let acd = crate::acd::build_acd(&tab.url, &title, &tab.html, &[], &[], &[], &json!({}), 400);
            let results = crate::acd::slice_acd(&acd, start, end, limit)?;
            serde_json::to_value(&results).map_err(|e| e.to_string())
        }
        "browser_wasm" | "browser_clock" | "browser_gate" | "browser_heap" | "browser_assist"
        | "browser_find" | "browser_page_query" | "browser_canvas2digest" => {
            let cmd = name.strip_prefix("browser_").unwrap_or(name);
            let mut sess = session.lock().await;
            let store = ShellStore::new(None).map_err(|e| e.to_string())?;
            execute_command(cmd, &args_val, &mut sess, &store)
                .await
                .map_err(|e| e.to_string())
        }
        "browser_shell" => {
            let cmd = args.get("command").and_then(|v| v.as_str()).unwrap_or("help");
            let sub_args = args.get("arguments").cloned().unwrap_or_else(|| json!({}));
            let mut sess = session.lock().await;
            let store = ShellStore::new(None).map_err(|e| e.to_string())?;
            execute_command(cmd, &sub_args, &mut sess, &store)
                .await
                .map_err(|e| e.to_string())
        }
        _ => Err(format!("unknown tool: {}", name)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_invoke_unknown_tool() {
        let client = reqwest::Client::new();
        let sess = Arc::new(Mutex::new(Session::new(client)));
        let args = HashMap::new();
        let res = invoke(sess, "non_existent_tool", &args).await;
        assert!(res.is_err());
        assert!(res.unwrap_err().contains("unknown tool"));
    }
}
