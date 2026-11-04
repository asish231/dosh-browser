import pytest
import httpx
from webresearch.telemetry import NetworkLedger
from webresearch.acd import build_acd, diff_acd
from webresearch.session import Session
from webresearch.shell import DoshShell, ShellStore


def test_site_as_sdk_api_discovery():
    client = httpx.AsyncClient()
    ledger = NetworkLedger(client)
    ledger._records[1] = {
        "id": 1,
        "url": "https://api.example.com/v1/users?page=1&limit=50",
        "method": "GET",
        "status": 200,
        "content_type": "application/json",
        "source": "runtime",
    }
    ledger._records[2] = {
        "id": 2,
        "url": "https://api.example.com/v1/users?page=2",
        "method": "GET",
        "status": 200,
        "content_type": "application/json",
        "source": "runtime",
    }
    ledger._records[3] = {
        "id": 3,
        "url": "https://example.com/assets/app.js",
        "method": "GET",
        "status": 200,
        "content_type": "application/javascript",
        "source": "runtime",
    }
    apis = ledger.discover_apis()
    assert len(apis) == 1
    ep = apis[0]
    assert ep["method"] == "GET"
    assert ep["path"] == "/v1/users"
    assert ep["host"] == "api.example.com"
    assert ep["observations"] == 2
    assert "page" in ep["query_params"]


def test_diff_first_reading_and_block_content_hashing():
    html1 = "<main><h1>Title</h1><p>Paragraph 1</p></main>"
    html2 = "<main><h1>Title</h1><p>Paragraph 1 Edited</p><p>Paragraph 2 New</p></main>"

    acd1 = build_acd(url="https://example.com", title="Page 1", html=html1)
    acd2 = build_acd(url="https://example.com", title="Page 2", html=html2)

    assert len(acd1.blocks) == 2
    assert len(acd2.blocks) == 3

    assert acd1.blocks[0].content_hash
    assert acd2.blocks[0].content_hash
    assert acd1.blocks[0].content_hash == acd2.blocks[0].content_hash  # "Title" unchanged

    diff = diff_acd(acd1, acd2)
    assert diff["total_blocks"] == 3
    assert diff["unchanged_count"] == 1
    assert diff["mutated_count"] == 2


@pytest.mark.asyncio
async def test_durable_session_checkpoint_and_restore():
    async with httpx.AsyncClient() as client:
        session = Session(client, "test_session")
        shell = DoshShell(session)

        # Set up tab state
        session.current_tab.url = "https://example.com/dashboard"
        session.current_tab.html = "<h1>Dashboard</h1>"
        session._web_storage["https://example.com"] = {"theme": "dark", "token": "xyz"}

        # Save checkpoint
        chk_res = await shell.execute("checkpoint", {"id": "my_checkpoint"})
        assert chk_res["ok"] is True
        assert chk_res["result"]["checkpoint"]["id"] == "my_checkpoint"

        # Change session state
        session.current_tab.url = "https://example.com/other"
        session.current_tab.html = "<p>Different page</p>"

        # Restore checkpoint
        restore_res = await shell.execute("restore", {"id": "my_checkpoint"})
        assert restore_res["ok"] is True
        assert restore_res["result"]["restored"] == "my_checkpoint"
        assert session.current_tab.url == "https://example.com/dashboard"
        assert session.current_tab.html == "<h1>Dashboard</h1>"
        assert session._web_storage["https://example.com"]["theme"] == "dark"

        shell.close()
