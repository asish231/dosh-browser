"""Model Context Protocol (MCP) Server for dosh-browser.

Exposes dosh-browser tools over standard stdio JSON-RPC 2.0 so any AI agent
(Claude Desktop, Cursor, Antigravity IDE, OpenCode) can use dosh-browser as
its primary web navigation engine.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from webresearch.session import Browser, Session

MCP_PROTOCOL_VERSION = "2024-11-05"

from typing import Any

TOOLS = [
    {
        "name": "browser_search",
        "description": "Search the web with automatic engine fallback (Google -> DuckDuckGo). Returns clean markdown hits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query terms"},
                "max_hits": {"type": "integer", "description": "Maximum number of search results to return", "default": 8},
                "engine": {"type": "string", "enum": ["auto", "google", "ddg"], "default": "auto"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Open a URL, execute client-side JavaScript in QuickJS, auto-dismiss consent banners, and return the structured Page snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The absolute HTTP/HTTPS URL to open"},
                "js": {"type": "string", "enum": ["auto", "always", "never"], "default": "auto"},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": "Click an interactive element by reference ref (e.g. 'e1', 'e2') or navigate a link.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The element reference marker from the page inspect list"},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field by ref (e.g. 'e1', 'e2') using humanized keystroke cadence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The input element reference"},
                "text": {"type": "string", "description": "The text string to type into the field"},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "browser_query",
        "description": "Query structured ACD semantic blocks on the current page matching a term without loading the full DOM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Search keyword or topic"},
                "limit": {"type": "integer", "default": 40},
            },
            "required": ["term"],
        },
    },
    {
        "name": "browser_slice",
        "description": "Slice ACD blocks between start and end block IDs for token-efficient reading.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "default": ""},
                "end": {"type": "string", "default": ""},
                "limit": {"type": "integer", "default": 120},
            },
        },
    },
    {
        "name": "browser_actions",
        "description": "List the full action surface of the current page: every control the agent can reach, "
        "with a stable ref, its kind (navigate/submit/activate/drag/dropzone/embed), and its target. "
        "Unlike inspect refs this is not capped at 80 and reports how many controls it omitted. "
        "Controls marked 'needs real engine' have no href and no form, so only a real browser can actuate them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "max_actions": {"type": "integer", "default": 200},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "browser_act",
        "description": "Carry out one action from the surface by ref. Navigates for links, fills and submits for "
        "form fields (pass text), and uses a real browser for activate/drag controls. For a drag pass dx/dy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "ref": {"type": "string", "description": "Action ref from browser_actions"},
                "text": {"type": "string", "description": "Value for a form field"},
                "dx": {"type": "number", "description": "Horizontal drag distance in px"},
                "dy": {"type": "number", "description": "Vertical drag distance in px"},
                "max_chars": {"type": "integer", "default": 8000},
            },
            "required": ["session_id", "ref"],
        },
    },
    {
        "name": "browser_tabs",
        "description": "Manage multi-tab browsing: list open tabs, open a new tab, switch tabs, or close a tab.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "new", "switch", "close"]},
                "tab_id": {"type": "string", "description": "Target tab ID for switch or close"},
                "url": {"type": "string", "description": "Optional initial URL for a new tab"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "browser_captcha",
        "description": "Inspect the current page for captcha/challenge detection. Returns structured challenge details.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "browser_add_captcha_token",
        "description": "Add a captcha token to session cookies for retrying blocked requests.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string", "description": "The solved captcha token value"},
                "provider": {"type": "string", "enum": ["datadome", "recaptcha", "hcaptcha", "cloudflare"], "description": "The captcha provider"},
            },
            "required": ["token", "provider"],
        },
    },
]


class McpServer:
    """Stdio JSON-RPC 2.0 Model Context Protocol Server."""

    def __init__(self) -> None:
        self.browser = Browser()
        self.session: Session | None = None

    async def _get_session(self) -> Session:
        if self.session is None:
            self.session = await self.browser.create()
        return self.session

    async def handle_call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        session = await self._get_session()

        if name == "browser_search":
            query = args.get("query", "")
            max_hits = int(args.get("max_hits", 8))
            engine = args.get("engine", "auto")
            res = await session.search(query, engine=engine)
            return {"content": [{"type": "text", "text": res.view}]}

        elif name == "browser_navigate":
            url = args.get("url", "")
            js = args.get("js", "auto")
            max_chars = int(args.get("max_chars", 8000))
            page = await session.goto(url, js=js, max_chars=max_chars)
            return {"content": [{"type": "text", "text": page.view}]}

        elif name == "browser_click":
            ref = args.get("ref", "")
            max_chars = int(args.get("max_chars", 8000))
            page = await session.click(ref, max_chars=max_chars)
            return {"content": [{"type": "text", "text": page.view}]}

        elif name == "browser_type":
            ref = args.get("ref", "")
            text = args.get("text", "")
            max_chars = int(args.get("max_chars", 8000))
            page = await session.type(ref, text, max_chars=max_chars)
            return {"content": [{"type": "text", "text": page.view}]}

        elif name == "browser_query":
            term = args.get("term", "")
            limit = int(args.get("limit", 40))
            blocks = session.query(term, limit=limit)
            return {"content": [{"type": "text", "text": json.dumps(blocks, indent=2)}]}

        elif name == "browser_slice":
            start = args.get("start", "")
            end = args.get("end", "")
            limit = int(args.get("limit", 120))
            blocks = session.slice(start, end, limit=limit)
            return {"content": [{"type": "text", "text": json.dumps(blocks, indent=2)}]}

        elif name == "browser_actions":
            max_actions = int(args.get("max_actions", 200))
            surface = session.action_surface(max_actions=max_actions)
            payload = {
                "coverage": surface.coverage_line(),
                "complete": surface.complete,
                "total_found": surface.total_found,
                "returned": len(surface.actions),
                "truncated": surface.truncated,
                "engine_only": surface.engine_only,
                "actions": [
                    {
                        "ref": a.ref,
                        "kind": a.kind,
                        "role": a.role,
                        "label": a.label,
                        "target": a.target,
                        "field_name": a.field_name,
                        "checked": a.checked,
                        "disabled": a.disabled,
                        "notes": a.notes,
                        "http_callable": a.callable_without_engine,
                    }
                    for a in surface.actions
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

        elif name == "browser_act":
            ref = args.get("ref", "")
            text = args.get("text", "")
            dx = args.get("dx")
            dy = args.get("dy")
            max_chars = int(args.get("max_chars", 8000))
            try:
                page = await session.act(
                    ref,
                    text=text,
                    dx=dx,
                    dy=dy,
                    max_chars=max_chars,
                )
            except ValueError as exc:
                return {"content": [{"type": "text", "text": f"browser_act failed: {exc}"}]}
            return {"content": [{"type": "text", "text": page.view}]}

        elif name == "browser_tabs":
            action = args.get("action", "list")
            if action == "list":
                return {"content": [{"type": "text", "text": json.dumps(session.tabs, indent=2)}]}
            elif action == "new":
                tid = session.new_tab()
                url = args.get("url")
                if url:
                    page = await session.goto(url)
                    return {"content": [{"type": "text", "text": f"Created tab {tid}\n{page.view}"}]}
                return {"content": [{"type": "text", "text": f"Created blank tab {tid}"}]}
            elif action == "switch":
                tid = args.get("tab_id", "")
                page = session.switch_tab(tid)
                view = page.view if page else f"Switched to {tid} (empty)"
                return {"content": [{"type": "text", "text": view}]}
            elif action == "close":
                tid = args.get("tab_id", "")
                session.close_tab(tid)
                return {"content": [{"type": "text", "text": f"Closed tab {tid}"}]}
            raise ValueError(f"unknown tab action: {action}")

        elif name == "browser_captcha":
            if session.captcha_inspection is None:
                return {"content": [{"type": "text", "text": "No captcha detected on current page."}]}
            return {"content": [{"type": "text", "text": session.captcha_inspection.report.view}]}

        elif name == "browser_add_captcha_token":
            token = args.get("token", "")
            provider = args.get("provider", "datadome")
            if provider == "datadome":
                session.client.cookies.set("_datadome", token, domain=".tripadvisor.com")
                return {"content": [{"type": "text", "text": f"Added datadome token. Retry with: session.goto('{session.url}')"}]}
            elif provider == "recaptcha":
                session.client.cookies.set("g-recaptcha-response", token, domain=".tripadvisor.com")
                return {"content": [{"type": "text", "text": f"Added recaptcha token. Retry with: session.goto('{session.url}')"}]}
            else:
                return {"content": [{"type": "text", "text": f"Token added for {provider}. Try retrying navigation."}]}

        raise ValueError(f"unknown tool: {name}")

    async def run_stdio(self) -> None:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            raw = line.decode("utf-8").strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "dosh-browser", "version": "0.3.0"},
                    },
                }
                self._send(resp)

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": TOOLS},
                }
                self._send(resp)

            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments") or {}
                try:
                    result = await self.handle_call_tool(name, arguments)
                    resp = {"jsonrpc": "2.0", "id": msg_id, "result": result}
                except Exception as e:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32000, "message": str(e)},
                    }
                self._send(resp)

            elif msg_id is not None:
                resp = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
                self._send(resp)

    def _send(self, data: dict[str, Any]) -> None:
        out = json.dumps(data) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()


def run_mcp_server() -> None:
    server = McpServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    run_mcp_server()
