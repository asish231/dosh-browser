//! `assist.rs` — In-session agentic DevTools assistant (the Chrome "AI assistance"
//! equivalent, built natively).
//!
//! Unlike `agent::ask` (a one-shot search→read research flow that spins up a
//! fresh session), `assist` drives an **already-live, attached** session and
//! walks the browser's own debugging surfaces — console, network ledger, API
//! discovery, the live cookie jar, storage, and bounded page-realm `eval` —
//! recording each action as a visible step (code executed + data returned),
//! exactly like the DevTools agent walkthrough.
//!
//! It then synthesizes a single runnable artifact (the "unified data collection
//! command"): a cURL template assembled from the discovered API endpoints plus
//! the live cookie names and storage keys, so an agent can replay an authed
//! request outside the browser.
//!
//! Two drive modes:
//!   * `plan_walkthrough` — a deterministic, LLM-free walkthrough. Hermetically
//!     testable; always available; no API key required.
//!   * (future) an LLM-driven loop that chooses the next tool each round. The
//!     deterministic plan is the floor so the feature never silently no-ops.
//!
//! Secret hygiene (repo rule): cookie and storage VALUES are never echoed. The
//! walkthrough and the synthesized command reference secrets by NAME and
//! redact values to `${ENV}`-style placeholders or lengths.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::session::Session;
use crate::shell::{execute_command, get_origin, ShellStore};

/// One recorded step of the walkthrough.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WalkStep {
    pub index: usize,
    /// Human-readable title, e.g. "Reading cookies".
    pub title: String,
    /// The shell command / code that was executed.
    pub code_executed: String,
    /// Compact, secret-safe summary of what the tool returned.
    pub data_returned: Value,
    pub ok: bool,
}

/// The full result of an assist run.
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct AssistResult {
    pub goal: String,
    pub origin: String,
    pub steps: Vec<WalkStep>,
    /// The synthesized, runnable artifact (unified data-collection command).
    pub artifact: Option<String>,
    /// Short natural-language summary of findings.
    pub summary: String,
}

/// Run the deterministic DevTools walkthrough against a live session.
///
/// This performs the same class of inspection the Chrome AI assistance panel
/// does: enumerate origin, read cookies, read storage keys, catalog discovered
/// background APIs, then synthesize a unified replay command. It never fabricates
/// data: if a surface is empty, the step records that honestly.
pub async fn plan_walkthrough(
    goal: &str,
    session: &mut Session,
    store: &ShellStore,
) -> AssistResult {
    let origin = get_origin(&session.current_tab().url);
    let mut steps: Vec<WalkStep> = Vec::new();
    let mut idx = 0usize;

    // Step 1 — establish the page origin / current document.
    idx += 1;
    {
        let url = session.current_tab().url.clone();
        steps.push(WalkStep {
            index: idx,
            title: "Listing page origin".into(),
            code_executed: "status".into(),
            data_returned: json!({ "origin": origin, "url": url }),
            ok: true,
        });
    }

    // Step 2 — read the live cookie jar (names + lengths, never values).
    idx += 1;
    let cookies_data = run_step(session, store, "cookies", &json!({ "values": true })).await;
    let cookie_names = extract_names(&cookies_data.1, "cookies", "name");
    steps.push(WalkStep {
        index: idx,
        title: "Reading cookies".into(),
        code_executed: "cookies values=true".into(),
        data_returned: cookies_data.1.clone(),
        ok: cookies_data.0,
    });

    // Step 3 — read storage keys (localStorage + sessionStorage), redacted.
    idx += 1;
    let storage_data = run_step(session, store, "storage", &json!({ "values": true })).await;
    let local_keys = extract_object_keys(&storage_data.1, "local_storage");
    steps.push(WalkStep {
        index: idx,
        title: "Reading local/session storage keys".into(),
        code_executed: "storage values=true".into(),
        data_returned: storage_data.1.clone(),
        ok: storage_data.0,
    });

    // Step 4 — catalog background API endpoints discovered from the network ledger.
    idx += 1;
    let apis_data = run_step(session, store, "net.apis", &json!({})).await;
    steps.push(WalkStep {
        index: idx,
        title: "Cataloging discovered background APIs".into(),
        code_executed: "net.apis".into(),
        data_returned: apis_data.1.clone(),
        ok: apis_data.0,
    });

    // Step 5 — synthesize the unified replay command from the gathered pieces.
    idx += 1;
    let artifact = synthesize_curl(&origin, &apis_data.1, &cookie_names, &local_keys);
    steps.push(WalkStep {
        index: idx,
        title: "Synthesizing unified data-collection command".into(),
        code_executed: "assemble(curl from net.apis + cookies + storage)".into(),
        data_returned: json!({
            "cookie_names": cookie_names,
            "storage_keys": local_keys,
            "endpoints_used": endpoint_count(&apis_data.1),
        }),
        ok: true,
    });

    let summary = format!(
        "Inspected origin {origin}: {} cookie(s), {} storage key(s), {} discovered endpoint(s). \
         Synthesized a replay command referencing secrets by name only.",
        cookie_names.len(),
        local_keys.len(),
        endpoint_count(&apis_data.1),
    );

    AssistResult {
        goal: goal.to_string(),
        origin,
        steps,
        artifact: Some(artifact),
        summary,
    }
}

/// Execute one shell command against the live session and return (ok, compact data).
async fn run_step(
    session: &mut Session,
    store: &ShellStore,
    command: &str,
    args: &Value,
) -> (bool, Value) {
    // `execute_command` can route back into the `assist` command, which calls
    // this function — a recursive async cycle the compiler cannot size without
    // indirection. Box the future to break the cycle. (In practice the commands
    // we call here — cookies/storage/net.apis — never re-enter `assist`.)
    let fut = execute_command(command, args, session, store);
    match Box::pin(fut).await {
        Ok(v) => (true, v),
        Err(e) => (false, json!({ "error": e.to_string() })),
    }
}

/// Pull a list of string field values from an array under `array_key`.
fn extract_names(data: &Value, array_key: &str, field: &str) -> Vec<String> {
    data.get(array_key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|e| e.get(field).and_then(|n| n.as_str()).map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

/// Pull object keys from a nested object under `obj_key`.
fn extract_object_keys(data: &Value, obj_key: &str) -> Vec<String> {
    data.get(obj_key)
        .and_then(|v| v.as_object())
        .map(|m| m.keys().cloned().collect())
        .unwrap_or_default()
}

fn endpoint_count(apis: &Value) -> usize {
    apis.get("endpoints")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0)
}

/// Build a unified cURL replay template. Secrets are referenced by name and
/// redacted to placeholders — never the live values.
fn synthesize_curl(
    origin: &str,
    apis: &Value,
    cookie_names: &[String],
    storage_keys: &[String],
) -> String {
    // Prefer the first discovered endpoint; fall back to the origin root.
    let target = apis
        .get("endpoints")
        .and_then(|v| v.as_array())
        .and_then(|a| a.first())
        .and_then(|e| e.get("url").and_then(|u| u.as_str()))
        .unwrap_or(origin)
        .to_string();

    let cookie_header = if cookie_names.is_empty() {
        "# (no cookies observed on this origin yet)".to_string()
    } else {
        let pairs: Vec<String> = cookie_names
            .iter()
            .map(|n| format!("{n}=${{{}}}", env_name(n)))
            .collect();
        format!("-H 'cookie: {}'", pairs.join("; "))
    };

    let storage_note = if storage_keys.is_empty() {
        "# (no localStorage keys observed)".to_string()
    } else {
        format!(
            "# localStorage tokens to collect from the session: {}",
            storage_keys.join(", ")
        )
    };

    format!(
        "# --- DOSH UNIFIED DATA-COLLECTION COMMAND ---\n\
         # Secrets are placeholders ($NAME); fill them from your live session.\n\
         {storage_note}\n\
         curl '{target}' \\\n  \
         -H 'content-type: application/json' \\\n  \
         {cookie_header}"
    )
}

/// Turn a cookie/storage key into a shell-safe uppercase env placeholder name.
fn env_name(raw: &str) -> String {
    let mut s: String = raw
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c.to_ascii_uppercase() } else { '_' })
        .collect();
    if s.is_empty() {
        s.push('X');
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::Session;
    use crate::shell::ShellStore;
    use reqwest::Client;

    #[tokio::test]
    async fn test_walkthrough_records_steps_and_artifact() {
        let mut session = Session::new(Client::new());
        let store = ShellStore::new(None).unwrap();

        // Seed a live document and set a cookie + localStorage via page-realm eval.
        session
            .goto_html(
                "https://api.example.com/app",
                "<html><body>app</body></html>",
                4000,
            )
            .expect("seed");
        execute_command(
            "eval",
            &json!({ "source": "document.cookie='auth_token=secretval'; localStorage.setItem('user_id','u42'); 'ok'" }),
            &mut session,
            &store,
        )
        .await
        .unwrap();

        let result = plan_walkthrough("collect all my tokens", &mut session, &store).await;

        // The walkthrough must have the full step sequence.
        assert!(result.steps.len() >= 5, "expected >=5 steps");
        let titles: Vec<&str> = result.steps.iter().map(|s| s.title.as_str()).collect();
        assert!(titles.iter().any(|t| t.contains("cookies")));
        assert!(titles.iter().any(|t| t.contains("storage")));
        assert!(titles.iter().any(|t| t.contains("APIs")));

        // The synthesized artifact must reference the cookie BY NAME and NOT echo
        // the secret value.
        let artifact = result.artifact.expect("artifact");
        assert!(artifact.contains("auth_token"), "cookie name in artifact");
        assert!(!artifact.contains("secretval"), "value must never leak");
        assert!(artifact.contains("curl"));
    }

    #[tokio::test]
    async fn test_walkthrough_empty_origin_is_honest() {
        // No navigation, no cookies: the walkthrough must still run and report
        // zero findings rather than fabricate anything.
        let mut session = Session::new(Client::new());
        let store = ShellStore::new(None).unwrap();
        let result = plan_walkthrough("collect tokens", &mut session, &store).await;
        assert!(result.artifact.is_some());
        assert!(result.summary.contains("0 cookie"));
    }
}
