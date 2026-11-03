import asyncio
import json
import re
from typing import Any

import httpx
import pytest

from webresearch.telemetry import NetworkLedger


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def test_hooks_preserved_idempotent_detach_and_redirects():
    async def go():
        calls = []

        async def request_hook(request):
            calls.append(("request", request.url.path))

        async def response_hook(response):
            calls.append(("response", response.status_code))

        def handler(request):
            if request.url.path == "/start":
                return httpx.Response(302, headers={"location": "/end?token=secret"})
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True,
            event_hooks={"request": [request_hook], "response": [response_hook]},
        ) as client:
            ledger = NetworkLedger(client).attach()
            assert ledger.attach() is ledger
            await client.get("https://example.test/start", headers={"sec-fetch-dest": "document"})
            await client.get("https://example.test/script.js")
            records = ledger.query()["records"]
            assert [r["id"] for r in records] == [1, 2, 3]
            assert [r["status"] for r in records] == [302, 200, 200]
            assert all(r["state"] == "complete" for r in records)
            assert records[0]["request_headers"]["sec-fetch-dest"] == "document"
            assert all(r["body_state"] == "disabled" for r in records)
            assert "secret" not in json.dumps(ledger.query())
            ledger.detach()
            ledger.detach()
            assert client.event_hooks == {"request": [request_hook], "response": [response_hook]}
            await client.get("https://example.test/unobserved")
            assert ledger.query()["total_observed"] == 3
            ledger.attach()
            await client.get("https://example.test/again")
            assert ledger.query()["latest_id"] == 4
        assert len(calls) == 10

    asyncio.run(go())


def test_pre_header_failure_is_unresolved_not_fabricated_success():
    async def go():
        def handler(request):
            raise httpx.ConnectError("password=DO_NOT_RETAIN", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ledger = NetworkLedger(client).attach()
            with pytest.raises(httpx.ConnectError):
                await client.get("https://example.test/?token=PRIVATE")
            result = ledger.query()
            record = result["records"][0]
            assert record["state"] == "unresolved"
            assert record["status"] is None
            assert record["network_sent"] is None
            assert "pre-header" in result["coverage"]["failures"]
            assert "DO_NOT_RETAIN" not in json.dumps(result)
            assert "PRIVATE" not in json.dumps(result)

    asyncio.run(go())


@pytest.mark.parametrize("capture", [False, True])
def test_stream_never_read_ahead_or_read_twice(capture):
    async def go():
        stream = Chunks([b'{"answer":', b'42,"password":"PRIVATE"}'])
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, stream=stream, headers={"content-type": "application/json"},
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            ledger = NetworkLedger(client, capture_bodies=capture).attach()
            async with client.stream("GET", "https://example.test/download") as response:
                assert stream.reads == 0
                assert ledger.query()["records"][0]["state"] == "headers_received"
                received = b"".join([part async for part in response.aiter_bytes()])
                assert received == b'{"answer":42,"password":"PRIVATE"}'
                assert stream.reads == 2
            assert stream.closed
            record = ledger.query()["records"][0]
            assert record["state"] == "complete"
            assert record["bytes"] == len(received)
            if capture:
                assert json.loads(record["response_body"]) == {"answer": 42, "password": "[REDACTED]"}
            else:
                assert "response_body" not in record
            assert "PRIVATE" not in json.dumps(ledger.query())

    asyncio.run(go())


def test_stream_read_failure_and_early_close():
    async def go():
        streams = [Chunks([b'{"partial":'], httpx.ReadError("token=PRIVATE")), Chunks([b"never consumed"])]
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, stream=streams.pop(0), headers={"content-type": "application/json"},
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            with pytest.raises(httpx.ReadError):
                await client.get("https://example.test/fails")
            async with client.stream("GET", "https://example.test/closed"):
                pass
            failed, closed = ledger.query()["records"]
            assert failed["state"] == "body_failed"
            assert failed["error"] == "ReadError"
            assert failed["body_state"] == "omitted_partial"
            assert closed["state"] == "body_not_completed"
            assert closed["body_state"] == "omitted_partial"
            assert all("response_body" not in record for record in (failed, closed))
            assert "PRIVATE" not in json.dumps(ledger.query())

    asyncio.run(go())


def test_stream_survives_detach_and_eviction():
    async def go():
        stream = Chunks([b'{"ok":true}'])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, stream=stream, headers={"content-type": "application/json"},
        ))) as client:
            ledger = NetworkLedger(client, max_records=1, capture_bodies=True).attach()
            async with client.stream("GET", "https://example.test/old") as response:
                ledger.record_runtime({"resource_requests": [{"url": "/new", "status": 200}]}, "tab", "https://example.test/")
                ledger.detach()
                assert await response.aread() == b'{"ok":true}'
            result = ledger.query()
            assert result["records"][0]["id"] == 2
            assert result["records"][0]["source"] == "runtime"
            assert result["omitted_retention"] == 1

    asyncio.run(go())


@pytest.mark.parametrize("streamed", [False, True])
def test_bodies_redact_nested_keys_and_do_not_capture_request(streamed):
    async def go():
        payload = {"ok": [{"access_token": "PRIVATE1", "Password": "PRIVATE2"}], "value": "public"}
        raw = json.dumps(payload).encode()

        def handler(request):
            assert request.content == b"password=PRIVATE_REQUEST"
            headers = {"content-type": "application/problem+json"}
            if streamed:
                return httpx.Response(200, headers=headers, stream=Chunks([raw]))
            return httpx.Response(200, headers=headers, content=raw)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            await client.post("https://example.test/", content=b"password=PRIVATE_REQUEST")
            result = ledger.query()
            record = result["records"][0]
            assert record["body_state"] == "captured_redacted"
            assert json.loads(record["response_body"]) == {
                "ok": [{"access_token": "[REDACTED]", "Password": "[REDACTED]"}], "value": "public",
            }
            assert "PRIVATE" not in json.dumps(result)

    asyncio.run(go())


@pytest.mark.parametrize("raw,content_type,state", [
    (b'{"password":"PRIVATE", "long":"' + b"x" * 150 + b'"}', "application/json", "omitted_over_cap"),
    (b'{"password":"PRIVATE', "application/json", "omitted_invalid_or_partial"),
    (b"password=PRIVATE", "text/plain", "omitted_unsupported_format"),
    (b'\xff', "application/json", "omitted_invalid_or_partial"),
])
def test_unsafe_body_prefixes_not_retained(raw, content_type, state):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, stream=Chunks([raw[:8], raw[8:]]), headers={"content-type": content_type},
        ))) as client:
            ledger = NetworkLedger(client, capture_bodies=True, body_max_bytes=100).attach()
            await client.get("https://example.test/")
            record = ledger.query()["records"][0]
            assert record["body_state"] == state
            assert "response_body" not in record
            assert "PRIVATE" not in json.dumps(ledger.query())

    asyncio.run(go())


def test_form_capture_and_redacted_output_cap():
    async def go():
        responses = [
            httpx.Response(200, content=b"token=PRIVATE&name=public&password=PRIVATE", headers={
                "content-type": "application/x-www-form-urlencoded",
            }),
            httpx.Response(200, json={"token": "x"}),
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: responses.pop(0))) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            await client.get("https://example.test/")
            record = ledger.query()["records"][0]
            assert "name=public" in record["response_body"]
            assert "PRIVATE" not in record["response_body"]
            ledger.body_max_bytes = 15
            await client.get("https://example.test/")
            assert ledger.query()["records"][1]["body_state"] == "omitted_over_cap"

    asyncio.run(go())


def test_encoded_stream_is_not_misrepresented_as_decoded_capture():
    import gzip

    async def go():
        raw = b'{"token":"PRIVATE"}'
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, stream=Chunks([gzip.compress(raw)]),
            headers={"content-type": "application/json", "content-encoding": "gzip"},
        ))) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            response = await client.get("https://example.test/")
            assert response.content == raw
            assert ledger.query()["records"][0]["body_state"] == "omitted_encoded_stream"

    asyncio.run(go())


def test_runtime_metadata_redaction_and_mock_block_cache_failure():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    ledger = NetworkLedger(client)
    headers = {
        "Authorization": "Bearer PRIVATE1", "Cookie": "sid=PRIVATE2", "X-Api-Token": "PRIVATE3",
        "Set-Cookie": "sid=PRIVATE4", "X-Password": "PRIVATE5", "Accept": "application/json",
        "Referer": "https://user:PRIVATE6@example.test/?q=PRIVATE7#PRIVATE8",
    }
    info = {"resource_requests": [
        {"url": "/mock?token=PRIVATE9", "status": 200, "disposition": "mocked:r1",
         "request_headers": headers, "response_headers": headers, "body": '{"token":"PRIVATE10"}',
         "request_body": "password=PRIVATE11", "data": {"cookie": "PRIVATE12"}},
        {"url": "/block", "status": 0, "content_type": "blocked-by-rule", "disposition": "blocked:r2"},
        {"url": "/cache", "status": 200, "sw": "CacheFirst-hit:worker"},
        {"url": "/error", "status": 0, "error": "password=PRIVATE13"},
        {"url": "/normal", "status": 200},
    ]}
    assert ledger.record_runtime(info, "tab-1", "https://example.test/?password=PRIVATE14") == 5
    result = ledger.query()
    records = result["records"]
    assert [r["state"] for r in records] == ["mocked", "blocked", "reported", "failed", "reported"]
    assert [r["network_sent"] for r in records] == [False, False, False, None, None]
    assert all(r["tab_id"] == "tab-1" for r in records)
    assert records[0]["request_headers"]["accept"] == "application/json"
    assert "PRIVATE" not in json.dumps(result)
    assert info["resource_requests"][0]["request_headers"]["Cookie"] == "sid=PRIVATE2"
    assert "explicit" in result["coverage"]["runtime"]


def test_runtime_response_body_not_ambiguous_body_or_data():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    ledger = NetworkLedger(client, capture_bodies=True)
    ledger.record_runtime({"resource_requests": [
        {"url": "/response", "status": 200, "content_type": "application/json",
         "response_body": '{"token":"PRIVATE1","ok":true}', "body": "PRIVATE_REQUEST"},
        {"url": "/mock", "status": 200, "content_type": "application/json",
         "response_headers": {"x-dosh-mock": "1"}, "body": '{"password":"PRIVATE2"}'},
        {"url": "/ambiguous", "status": 200, "content_type": "application/json",
         "body": '{"password":"PRIVATE3"}', "data": {"token": "PRIVATE4"}},
    ]}, "tab", "https://example.test/")
    result = ledger.query()
    assert [r["body_state"] for r in result["records"]] == ["captured_redacted", "captured_redacted", "unavailable"]
    assert json.loads(result["records"][0]["response_body"])["ok"] is True
    assert result["records"][1]["network_sent"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_retention_pagination_literal_filter_and_copy_isolation():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    ledger = NetworkLedger(client, max_records=3)
    ledger.record_runtime({"resource_requests": [{"url": f"/{i}.json", "status": 200} for i in range(5)]},
                          "tab", "https://example.test/")
    first = ledger.query(limit=1)
    assert first["total_observed"] == 5
    assert first["retained"] == 3
    assert first["oldest_id"] == 3
    assert first["omitted_retention"] == 2
    assert first["omitted_limit"] == 2
    assert not first["complete"]
    assert first["next_after"] == 3
    second = ledger.query(after=first["next_after"], limit=1)
    assert second["records"][0]["id"] == 4
    assert second["omitted_retention"] == 0
    last = ledger.query(after=second["next_after"])
    assert last["records"][0]["id"] == 5
    assert last["complete"]
    assert last["next_after"] == 5
    assert ledger.query(after=5)["records"] == []
    assert ledger.query(after=100)["next_after"] == 100
    assert ledger.query(pattern=".*")["records"] == []
    filtered = ledger.query(after=2, pattern="3.json")
    assert filtered["records"][0]["id"] == 4
    assert filtered["next_after"] == 5
    filtered["records"][0]["request_headers"]["token"] = "PRIVATE"
    filtered["coverage"]["rules"] = "incorrect"
    assert "PRIVATE" not in json.dumps(ledger.query())
    assert "runtime" in ledger.query()["coverage"]["rules"]


def test_concurrent_completion_keeps_start_id_order():
    async def go():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            if request.url.path == "/slow":
                entered.set()
                await release.wait()
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ledger = NetworkLedger(client).attach()
            slow = asyncio.create_task(client.get("https://example.test/slow"))
            await entered.wait()
            await client.get("https://example.test/fast")
            records = ledger.query()["records"]
            assert [r["state"] for r in records] == ["unresolved", "complete"]
            release.set()
            await slow
            assert [r["id"] for r in ledger.query()["records"]] == [1, 2]
            assert all(r["state"] == "complete" for r in ledger.query()["records"])

    asyncio.run(go())


def test_rules_validate_preserve_existing_and_do_not_intercept_httpx():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            old = {"id": "old", "action": "block", "pattern": "existing"}
            shared: list[dict[str, Any]] = [old]
            setattr(client, "_dosh_network_rules", shared)  # noqa: B010
            ledger = NetworkLedger(client).attach()
            block = ledger.add_rule("block", "blocked", method="get")
            headers = {"Authorization": "Bearer PRIVATE"}
            header = ledger.add_rule("header", "example", headers=headers)
            mock = ledger.add_rule("mock", r"/api/\d+", status=201, content_type="application/json", body='{"token":"PRIVATE"}')
            assert getattr(client, "_dosh_network_rules", None) is shared
            assert shared[0] is old
            assert shared[1]["method"] == "GET"
            assert len({block["id"], header["id"], mock["id"]}) == 3
            headers["Authorization"] = "changed"
            assert shared[2]["headers"]["Authorization"] == "Bearer PRIVATE"
            assert "PRIVATE" not in json.dumps([block, header, mock])
            assert "runtime" in block["scope"]
            await client.get("https://example.test/blocked")
            assert ledger.query()["records"][0]["status"] == 200
            with pytest.raises(re.error):
                ledger.add_rule("block", "[")
            assert len(shared) == 4
            assert ledger.remove_rule(header["id"])
            assert not ledger.remove_rule(header["id"])
            assert not ledger.remove_rule("missing")
            assert [rule["id"] for rule in shared] == ["old", block["id"], mock["id"]]
            assert ledger.remove_rule(block["id"])
            assert ledger.remove_rule(mock["id"])
            assert shared == [old]
            ledger.detach()
            assert shared == [old]

    asyncio.run(go())


@pytest.mark.parametrize("action,options", [
    ("redirect", {}), ("block", {"status": 200}), ("block", {"enabled": "yes"}),
    ("header", {"headers": "bad"}), ("header", {"headers": {"x": 42}}),
    ("mock", {"status": 999}), ("mock", {"status": True}), ("mock", {"body": {}}),
    ("mock", {"content_type": 42}), ("mock", {"id": "override"}),
])
def test_invalid_rules_leave_client_unchanged(action, options):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    ledger = NetworkLedger(client)
    with pytest.raises(ValueError):
        ledger.add_rule(action, "example", **options)
    assert not hasattr(client, "_dosh_network_rules")


@pytest.mark.parametrize("options", [{"max_records": 0}, {"max_records": True}, {"body_max_bytes": -1}])
def test_invalid_constructor_limits(options):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with pytest.raises(ValueError):
        NetworkLedger(client, **options)


@pytest.mark.parametrize("options", [{"after": -1}, {"limit": 0}, {"after": True}, {"limit": 1.5}])
def test_invalid_query(options):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with pytest.raises(ValueError):
        NetworkLedger(client).query(**options)


def test_existing_hook_can_consume_stream_without_buffering():
    async def go():
        consumed = []

        async def consume(response):
            consumed.extend([part async for part in response.aiter_bytes()])

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks([b"original"]))),
            event_hooks={"response": [consume]},
        ) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            async with client.stream("GET", "https://example.test/"):
                pass
            assert consumed == [b"original"]
            record = ledger.query()["records"][0]
            assert record["state"] == "consumed_before_ledger"
            assert record["body_state"] == "unavailable_prior_hook"

    asyncio.run(go())


def test_real_quickjs_mock_and_block_rule_contract():
    from selectolax.lexbor import LexborHTMLParser

    from webresearch.engine.runtime import run_scripts

    async def go():
        def forbidden(request):
            pytest.fail(f"unexpected transport request: {request.method}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            ledger = NetworkLedger(client, capture_bodies=True).attach()
            ledger.add_rule("mock", "/api", body='{"answer":42,"token":"PRIVATE"}')
            ledger.add_rule("block", "/blocked")
            html, info = await run_scripts(
                '<html><body><p id="out"></p><script>'
                'fetch("/api").then(r=>r.json()).then(d=>document.getElementById("out").textContent=String(d.answer));'
                'fetch("/blocked");'
                '</script></body></html>',
                "https://example.test/", client, force=True,
            )
            output = LexborHTMLParser(html).css_first("#out")
            assert output is not None
            assert output.text() == "42"
            assert ledger.record_runtime(info, "tab", "https://example.test/") == 2
            records = ledger.query()["records"]
            assert [r["state"] for r in records] == ["mocked", "blocked"]
            assert all(r["network_sent"] is False for r in records)
            assert "PRIVATE" not in json.dumps(ledger.query())

    asyncio.run(go())
