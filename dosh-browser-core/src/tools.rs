//! `tools.rs` — OpenAI-style tool definitions (direct port of `webresearch/tools.py`).

use serde_json::{json, Value};

pub fn get_tools() -> Vec<Value> {
    vec![
        json!({
            "type": "function",
            "function": {
                "name": "browser_shell",
                "description": "Agent-native browser terminal. Use command=help for JSON commands: open, actions, act, net, console, eval, skill.*, db, storage, snapshot/diff, layers, frames, tabs, video, read, history, cookies, wait, caps, screenshot, frame.eval, ref, trace, watch. No OS shell.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "command": { "type": "string" },
                        "arguments": { "type": "object" }
                    },
                    "required": ["session_id", "command"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_search",
                "description": "Search the web for an agent. Returns numbered hits [n] with url + snippet.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "q": { "type": "string" },
                        "max_hits": { "type": "integer", "default": 8 },
                        "engine": { "type": "string", "enum": ["auto", "google", "ddg"], "default": "auto" }
                    },
                    "required": ["q"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_read",
                "description": "Open a URL. Runs page JS in built-in V8 engine. Returns compact text, links, forms, and inspect refs [eN].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": { "type": "string" },
                        "max_chars": { "type": "integer", "default": 8000 },
                        "js": { "type": "string", "enum": ["auto", "always", "never"], "default": "auto" }
                    },
                    "required": ["url"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_goto",
                "description": "Session: open a URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "url": { "type": "string" }
                    },
                    "required": ["session_id", "url"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_actions",
                "description": "List all interactive action controls on current page.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "max_actions": { "type": "integer", "default": 200 }
                    },
                    "required": ["session_id"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_click",
                "description": "Click top-level inspect ref [eN].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "ref": { "type": "string" }
                    },
                    "required": ["session_id", "ref"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_type",
                "description": "Type text into inspect ref [eN].",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "ref": { "type": "string" },
                        "text": { "type": "string" }
                    },
                    "required": ["session_id", "ref", "text"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_extract",
                "description": "Extract fields from a URL using CSS selectors or built-ins.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": { "type": "string" },
                        "fields": { "type": "object" }
                    },
                    "required": ["url", "fields"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_login",
                "description": "Attempt to log in with username/password through detected wall.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "url": { "type": "string" },
                        "username": { "type": "string" },
                        "password": { "type": "string" }
                    },
                    "required": ["session_id", "url", "username", "password"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "browser_submit_otp",
                "description": "Submit one-time passcode to checkpoint.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": { "type": "string" },
                        "checkpoint": { "type": "string" },
                        "code": { "type": "string" }
                    },
                    "required": ["session_id", "checkpoint", "code"]
                }
            }
        })
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tools_list() {
        let tools = get_tools();
        assert!(tools.len() >= 10);
        assert_eq!(tools[0]["function"]["name"], "browser_shell");
    }
}

