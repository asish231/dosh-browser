"""OpenAI-style tools for an LLM agent. Primary product surface."""

from __future__ import annotations

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": (
                "Search the web for an agent. Tries Google HTML first, DuckDuckGo if blocked. "
                "Prefer this over opening google.com. Returns numbered hits [n] with url + snippet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "max_hits": {"type": "integer", "default": 8},
                    "engine": {"type": "string", "enum": ["auto", "google", "ddg"], "default": "auto"},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": (
                "Open a URL. Runs page JS in the built-in QuickJS mini-DOM (not Chromium). "
                "Returns compact text, numbered links [n], forms, inspect refs [eN], and any bot challenge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 8000},
                    "js": {"type": "string", "enum": ["auto", "always", "never"], "default": "auto"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_session_create",
            "description": "Start a multi-step agent session (cookies, history, click/type).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_goto",
            "description": "Session: open a URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "url": {"type": "string"},
                    "js": {"type": "string", "enum": ["auto", "always", "never"], "default": "auto"},
                },
                "required": ["session_id", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "Session: follow numbered link [n] from the current page view.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "n": {"type": "integer"},
                },
                "required": ["session_id", "n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_inspect",
            "description": (
                "List clickable/typeable refs [eN] on the current top-level page. "
                "Use browser_captcha for challenge iframe internals."
            ),
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_frames",
            "description": (
                "List iframe documents loaded in isolated QuickJS contexts. Returns [fN] refs, "
                "parent refs, URLs, status, JS counts, challenge flags, and HTML excerpts."
            ),
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_frame_source",
            "description": "Read the loaded HTML source for iframe ref [fN].",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string", "description": "e.g. f2"},
                    "max_chars": {"type": "integer", "default": 20000},
                },
                "required": ["session_id", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_captcha",
            "description": (
                "Inspect a CAPTCHA as a separate structured object. Returns provider/site key, "
                "challenge frames, checkbox state, prompt, sprite metadata, tile crop offsets, "
                "controls, and hidden-field metadata. Inspection only; never solves or bypasses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Inspect the current page in this session.",
                    },
                    "url": {
                        "type": "string",
                        "description": "One-shot alternative when no session is available.",
                    },
                    "max_chars": {"type": "integer", "default": 8000},
                    "js": {
                        "type": "string",
                        "enum": ["auto", "always", "never"],
                        "default": "auto",
                    },
                },
                "anyOf": [{"required": ["session_id"]}, {"required": ["url"]}],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_captcha_code",
            "description": (
                "When a CAPTCHA is present, dump its loaded source: parent widget HTML, "
                "iframe documents, checkbox/token/tile markup, related scripts, and sprite "
                "bytes as base64. This returns code for inspection; it does not solve or bypass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Dump CAPTCHA code from the current session page.",
                    },
                    "url": {
                        "type": "string",
                        "description": "One-shot alternative when no session is available.",
                    },
                    "js": {
                        "type": "string",
                        "enum": ["auto", "always", "never"],
                        "default": "auto",
                    },
                },
                "anyOf": [{"required": ["session_id"]}, {"required": ["url"]}],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": (
                "Click top-level inspect ref [eN]. Links navigate and local checkboxes toggle. "
                "This does not solve or submit CAPTCHA verification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string", "description": "e.g. e3"},
                },
                "required": ["session_id", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type into inspect ref [eN] (input/textarea).",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["session_id", "ref", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_submit",
            "description": "Submit numbered form [n] with field values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "n": {"type": "integer"},
                    "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["session_id", "n"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_back",
            "description": "Session: go back one page.",
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "Extract fields from a URL. CSS selector or builtin: title, description, url, headings, text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["url", "fields"],
            },
        },
    },
]
