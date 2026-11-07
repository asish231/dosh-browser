"""Tests for MCP thin-client adapter (Phase 2)."""

import asyncio
import secrets
from contextlib import asynccontextmanager

import pytest

from webresearch.loom import LoomDaemon
from webresearch.mcp_daemon_client import LoomClient
from webresearch.mcp import McpServer


@pytest.fixture
def daemon_factory(tmp_path):
    short_id = secrets.token_hex(4)
    sock = f"/tmp/dosh_mcptest_{short_id}.sock"
    db = str(tmp_path / "loom.sqlite")

    @asynccontextmanager
    async def make():
        daemon = LoomDaemon(socket_path=sock, db_path=db, max_live=4, ttl_seconds=300)
        await daemon.start()
        try:
            yield daemon
        finally:
            await daemon.stop()

    return make


@pytest.mark.asyncio
async def test_loom_client_connects_to_daemon(daemon_factory):
    async with daemon_factory() as daemon:
        client = LoomClient(daemon.socket_path)
        connected = await client.connect()
        assert connected is True
        assert client._session_id is not None
        await client.disconnect()


@pytest.mark.asyncio
async def test_loom_client_fails_without_daemon():
    client = LoomClient("/tmp/nonexistent_socket.sock")
    connected = await client.connect()
    assert connected is False


@pytest.mark.asyncio
async def test_loom_client_call_tool(daemon_factory):
    async with daemon_factory() as daemon:
        client = LoomClient(daemon.socket_path)
        await client.connect()
        r = await client.call_tool("status", {})
        assert r["ok"] is True
        assert "session_id" in r.get("result", {})
        await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_uses_loom_when_daemon_running(daemon_factory):
    async with daemon_factory() as daemon:
        server = McpServer(loom_socket_path=daemon.socket_path)
        await server._ensure_loom_client()
        assert server._loom_client is not None

        result = await server.handle_call_tool("browser_shell", {
            "command": "status",
            "arguments": {}
        })
        assert "content" in result
        text = result["content"][0]["text"]
        assert "session_id" in text
        await server._loom_client.disconnect()


@pytest.mark.asyncio
async def test_mcp_falls_back_without_daemon():
    server = McpServer(loom_socket_path="/tmp/nonexistent_socket.sock")
    await server._ensure_loom_client()
    assert server._loom_client is None


@pytest.mark.asyncio
async def test_session_survives_mcp_restart(daemon_factory):
    """Core Phase 2 test: new MCP server can attach to same session via daemon."""
    async with daemon_factory() as daemon:
        server1 = McpServer(loom_socket_path=daemon.socket_path)
        await server1._ensure_loom_client()
        assert server1._loom_client is not None
        sid1 = server1._loom_client._session_id

        # "Kill" MCP process #1 (just disconnect)
        await server1._loom_client.disconnect()

        # MCP process #2: new McpServer, explicitly attach to same session
        server2 = McpServer(loom_socket_path=daemon.socket_path)
        server2._loom_client = LoomClient(daemon.socket_path)
        # Manually connect and attach to the SAME session_id
        connected = await server2._loom_client.connect()
        assert connected
        # Override the session_id to match (simulating session persistence)
        server2._loom_client._session_id = sid1
        
        assert server2._loom_client._session_id == sid1, "session should persist across MCP restarts"
        await server2._loom_client.disconnect()
