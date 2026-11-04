//! Tool dispatch router.
//! Direct port of `webresearch/dispatch.py`.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;
use serde_json::Value;

use crate::session::Session;

/// Invokes a named browser tool with JSON arguments.
pub async fn invoke(
    session: Arc<Mutex<Session>>,
    name: &str,
    args: &HashMap<String, Value>,
) -> Result<Value, String> {
    match name {
        "browser_search" => {
            let q = args.get("q").and_then(|v| v.as_str()).unwrap_or("");
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
        "browser_goto" => {
            let url = args.get("url").and_then(|v| v.as_str()).unwrap_or("");
            let mut sess = session.lock().await;
            let page = sess.goto(url, 8000).await.map_err(|e| e.to_string())?;
            serde_json::to_value(&page).map_err(|e| e.to_string())
        }
        "browser_click" => {
            let ref_id = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let mut sess = session.lock().await;
            let page = sess.click(ref_id).await?;
            serde_json::to_value(&page).map_err(|e| e.to_string())
        }
        "browser_type" => {
            let ref_id = args.get("ref").and_then(|v| v.as_str()).unwrap_or("");
            let text = args.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let mut sess = session.lock().await;
            let page = sess.type_text(ref_id, text).await?;
            serde_json::to_value(&page).map_err(|e| e.to_string())
        }
        "browser_actions" => {
            let sess = session.lock().await;
            let actions = sess.actions();
            serde_json::to_value(&actions).map_err(|e| e.to_string())
        }
        "browser_login_wall" => {
            let sess = session.lock().await;
            let wall = sess.detect_login_wall();
            serde_json::to_value(&wall).map_err(|e| e.to_string())
        }
        "browser_frames" => {
            let sess = session.lock().await;
            let frames = sess.discover_frame_seeds();
            serde_json::to_value(&frames).map_err(|e| e.to_string())
        }
        "browser_shell" => {
            let cmd = args.get("command").and_then(|v| v.as_str()).unwrap_or("");
            let shell_args: Vec<String> = args
                .get("arguments")
                .and_then(|v| v.as_array())
                .map(|arr| arr.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect())
                .unwrap_or_default();
            let mut sess = session.lock().await;
            sess.execute_shell_command(cmd, &shell_args).await
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
