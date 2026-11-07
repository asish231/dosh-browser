"""MCP thin-client adapter for Dosh Loom session daemon.

When the Loom daemon is running (Unix socket exists), this client connects to it
and forwards MCP tool calls through the daemon. This allows the MCP process to
die/restart freely while the browser session lives on in the daemon.

When the daemon is NOT running, falls back to in-process session management
(legacy behavior).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from webresearch.session import Browser, Session


DEFAULT_SOCKET_PATH = Path.home() / ".dosh" / "loom.sock"


class LoomClient:
    """Thin client that connects to the Loom daemon via Unix socket."""

    def __init__(self, socket_path: str | Path | None = None):
        self.socket_path = Path(socket_path) if socket_path else DEFAULT_SOCKET_PATH
        self._session_id: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self) -> bool:
        """Connect to the daemon. Returns True if successful, False if socket doesn't exist."""
        if not self.socket_path.exists():
            return False
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(str(self.socket_path))
            # Attach to a session (auto-create if none specified)
            await self._send({"op": "attach"})
            r = await self._recv()
            if r.get("ok"):
                self._session_id = r.get("session_id")
                return True
            return False
        except Exception:
            return False

    async def disconnect(self):
        """Detach from the session and close the connection."""
        if self._writer:
            try:
                await self._send({"op": "detach"})
                await self._recv()
            except Exception:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
            self._session_id = None

    async def _send(self, frame: dict):
        assert self._writer is not None
        self._writer.write((json.dumps(frame) + "\n").encode())
        await self._writer.drain()

    async def _recv(self) -> dict:
        assert self._reader is not None
        line = await self._reader.readline()
        if not line:
            raise ConnectionError("daemon disconnected")
        return json.loads(line.decode())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a shell command through the daemon."""
        if self._writer is None:
            raise RuntimeError("not connected; call connect() first")
        await self._send({"op": "command", "command": name, "arguments": arguments})
        r = await self._recv()
        return r

    async def ping(self) -> bool:
        """Check if the daemon is responsive."""
        if self._writer is None:
            return False
        try:
            await self._send({"op": "ping"})
            r = await self._recv()
            return r.get("ok") and r.get("pong")
        except Exception:
            return False


async def make_client_or_fallback(socket_path: str | Path | None = None):
    """Try to connect to the Loom daemon; fall back to in-process session if not available.

    Returns either:
    - (LoomClient, None) if daemon is available
    - (None, Session) if falling back to in-process
    """
    client = LoomClient(socket_path)
    if await client.connect():
        return client, None
    # Fallback: create in-process session
    browser = Browser()
    session = await browser.create()
    return None, session
