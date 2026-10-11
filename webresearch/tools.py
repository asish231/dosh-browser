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
            "name": "browser_images",
            "description": (
                "Image search over HTTP (no Chromium). Waterfall brave-images -> bing-images -> "
                "ddg-images -> google-images. Returns numbered hits with title [source], direct "
                "image url, and proxy snippet. Google/DDG may report blocked with reason."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "max_hits": {"type": "integer", "default": 8},
                    "engine": {"type": "string", "enum": ["auto", "brave", "bing-images", "ddg-images", "google-images"], "default": "auto"},
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
            "name": "browser_actions",
            "description": (
                "List the complete action surface of a session's current page: every control "
                "reachable, with a stable ref, its kind (navigate/submit/activate/drag/"
                "dropzone/embed) and its target. Prefer this over browser_inspect when you "
                "need to act: inspect refs are numbered in document order and stop at 80, "
                "while this enumerates the whole page and reports what it omitted. Controls "
                "with 'needs real engine' have no href and no form, so browser_click cannot "
                "reach them; use browser_act, which routes to a real browser."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "max_actions": {"type": "integer", "default": 200},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_act",
            "description": (
                "Carry out one action from browser_actions by ref. Navigates for links, fills "
                "and submits for form fields (pass text), uploads files for file inputs (pass files), "
                "and uses a real browser for activate and drag controls. Pass dx/dy for a drag."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string", "description": "Action ref from browser_actions"},
                    "text": {"type": "string", "description": "Value for a form field"},
                    "dx": {"type": "number", "description": "Horizontal drag distance in px"},
                    "dy": {"type": "number", "description": "Vertical drag distance in px"},
                    "files": {
                        "type": "array",
                        "description": "Optional list of files to upload for a file input",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "content": {"type": "string", "description": "Plain text content (text files only)"},
                                "content_b64": {"type": "string", "description": "Base64 raw bytes; required for binary files"},
                                "type": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                    "max_chars": {"type": "integer", "default": 8000},
                },
                "required": ["session_id", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_upload",
            "description": (
                "Upload a file or files to an <input type='file'> control on the current page. "
                "Simulates native file picker selection and fires input/change events. "
                "Accepts local file path or in-memory file list with name and content (text) "
                "or content_b64 (base64 raw bytes for binary/video/image files, byte-exact)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string", "description": "Element ref or action ref of file input"},
                    "file_path": {"type": "string", "description": "Path to local file on disk to upload"},
                    "files": {
                        "type": "array",
                        "description": "List of file descriptors to upload",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "content": {"type": "string", "description": "Plain text content (text files only)"},
                                "content_b64": {"type": "string", "description": "Base64 raw bytes; required for binary files"},
                                "type": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                    "max_chars": {"type": "integer", "default": 8000},
                },
                "required": ["session_id", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_press_key",
            "description": (
                "Press a named key on a control: enter, tab, escape, backspace, delete, space, "
                "arrowup, arrowdown, arrowleft, arrowright, home, end, pageup, pagedown. "
                "Enter in a text input submits its form; Tab moves focus; arrows change selects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "ref": {"type": "string", "description": "Element ref ([eN] or action ref)"},
                    "key": {"type": "string", "default": "enter"},
                    "max_chars": {"type": "integer", "default": 8000},
                },
                "required": ["session_id", "ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_respond_dialog",
            "description": (
                "List page dialogs (alert/confirm/prompt) or stage an answer. The engine is "
                "synchronous: stage the answer, then re-run the action that triggers the dialog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "accept": {"type": "boolean", "description": "True to accept, False to dismiss (stages an answer)"},
                    "dismiss": {"type": "boolean", "description": "Alternative to accept: true dismisses"},
                    "text": {"type": "string", "description": "Text to return for prompt() dialogs"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_download",
            "description": (
                "Download a file with the session's cookies: streams to disk, reports path, "
                "byte count, sha256, content type, and optional hash verification. "
                "No malware verdict engine; the hash is provided instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "url": {"type": "string"},
                    "expected": {"type": "string", "description": "Expected hash (hex or base64, sha256- prefix allowed)"},
                    "dest_dir": {"type": "string", "description": "Optional destination directory"},
                },
                "required": ["session_id", "url"],
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
            "name": "browser_captcha_solve",
            "description": (
                "Attempt to solve a tile-based visual CAPTCHA (reCAPTCHA v2, hCaptcha) using "
                "a vision model. Requires a prior browser_captcha call to fetch the sprite image. "
                "Returns tile selections to click. This only handles image-select captchas with "
                "visible tiles, not slider puzzles or audio challenges. Requires an OpenAI-compatible "
                "vision model endpoint configured."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session with a loaded CAPTCHA page.",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Vision model id to use. Must support images. "
                            "Default: auto-selects from configured models."
                        ),
                        "default": "auto",
                    },
                    "max_attempts": {
                        "type": "integer",
                        "description": "Max solve attempts before giving up.",
                        "default": 1,
                    },
                },
                "required": ["session_id"],
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
            "name": "browser_scroll",
            "description": (
                "Session: scroll the page (default one viewport down). Fires the scroll "
                "event and IntersectionObserver notifications so lazy content loads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "y": {"type": "integer", "default": 800},
                    "x": {"type": "integer", "default": 0},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_query",
            "description": (
                "Retrieve matching ACD blocks from the current page by substring. "
                "Use this instead of re-reading the whole page: returns block ids [bN], "
                "text/tables/actions relevant to the term."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "term": {"type": "string"},
                    "limit": {"type": "integer", "default": 40},
                },
                "required": ["session_id", "term"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_slice",
            "description": (
                "Read a contiguous range of ACD blocks by block id, e.g. start=b12 end=b40. "
                "Use for pagination over long pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "limit": {"type": "integer", "default": 120},
                },
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
