"""Tests for native file upload support in dosh-browser.

Covers:
- DOM classes: Blob, File, FileList, FileReader, FormData
- Element.prototype.files property and native file input change/input events
- QuickJS client-side script execution handling uploaded files
- Session.upload and Session.act(files=...) methods
- Server endpoint /v1/sessions/{sid}/upload
- MCP tool browser_upload
- Real multipart form submission with file inputs
- Binary payloads (video/image/any bytes) surviving byte-exact end to end
"""

import httpx
import pytest
from quickjs import Context

from fastapi.testclient import TestClient

from webresearch.engine.runtime import MINIDOM_JS
from webresearch.mcp import McpServer
from webresearch.server import create_app
from webresearch.session import Session


def test_minidom_blob_file_filelist():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        var blob = new Blob(["hello ", "world"], { type: "text/plain" });
        var file = new File(["test data"], "example.txt", { type: "text/plain", lastModified: 1700000000 });
        var fileList = new FileList([file]);
    """)
    assert ctx.eval("blob.size") == 11
    assert ctx.eval("blob.type") == "text/plain"
    assert ctx.eval("file.name") == "example.txt"
    assert ctx.eval("file.size") == 9
    assert ctx.eval("file.lastModified") == 1700000000
    assert ctx.eval("fileList.length") == 1
    assert ctx.eval("fileList[0].name") == "example.txt"
    assert ctx.eval("fileList.item(0).name") == "example.txt"


def test_minidom_file_reader():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        var file = new File(["agent content"], "doc.txt", { type: "text/plain" });
        var reader = new FileReader();
        var readResult = null;
        reader.onload = function(e) {
            readResult = e.target.result;
        };
        reader.readAsText(file);
    """)
    # Drain timer pass
    ctx.eval("__drainTimers()")
    assert ctx.eval("readResult") == "agent content"

    # readAsDataURL test
    ctx.eval("""
        var reader2 = new FileReader();
        var dataUrl = null;
        reader2.onload = function(e) {
            dataUrl = e.target.result;
        };
        reader2.readAsDataURL(file);
    """)
    ctx.eval("__drainTimers()")
    url = ctx.eval("dataUrl")
    assert url.startswith("data:text/plain;base64,")


def test_minidom_upload_files_and_event():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        __load({t: 'html', c: [{t: 'body', c: []}]}, 'https://example.com');
        var doc = document;
        var inp = doc.createElement("input");
        inp.setAttribute("type", "file");
        inp.setAttribute("id", "avatar");
        doc.body.appendChild(inp);

        var eventFired = [];
        inp.addEventListener("change", function(e) {
            eventFired.push({
                type: e.type,
                filesCount: e.target.files.length,
                fileName: e.target.files[0] ? e.target.files[0].name : "",
                val: e.target.value
            });
        });

        __uploadFiles("#avatar", [
            { name: "profile.png", content: "fake_png_bytes", type: "image/png" }
        ]);
    """)
    assert ctx.eval("inp.files.length") == 1
    assert ctx.eval("inp.files[0].name") == "profile.png"
    assert ctx.eval("inp.files[0].size") == 14
    assert ctx.eval("inp.value") == r"C:\fakepath\profile.png"
    assert ctx.eval("eventFired.length") == 1
    assert ctx.eval("eventFired[0].fileName") == "profile.png"


def test_formdata_with_files_and_serialization():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    ctx.eval("""
        var fd = new FormData();
        fd.append("username", "testuser");
        var f = new File(["file-data-123"], "data.txt", { type: "text/plain" });
        fd.append("attachment", f);

        var serialized = __serializeFormData(fd);
    """)
    body = ctx.eval("serialized.body")
    ctype = ctx.eval("serialized.contentType")
    assert "multipart/form-data; boundary=" in ctype
    assert 'name="username"' in body
    assert "testuser" in body
    assert 'name="attachment"; filename="data.txt"' in body
    assert "Content-Type: text/plain" in body
    assert "file-data-123" in body


@pytest.mark.asyncio
async def test_session_upload_and_client_js_execution():
    html = """<!DOCTYPE html>
    <html>
    <body>
        <input type="file" id="uploader" />
        <div id="output">empty</div>
        <script>
            var inp = document.getElementById("uploader");
            var out = document.getElementById("output");
            inp.addEventListener("change", function(e) {
                var file = e.target.files[0];
                if (file) {
                    var reader = new FileReader();
                    reader.onload = function(evt) {
                        out.textContent = "Uploaded: " + file.name + " (" + evt.target.result + ")";
                    };
                    reader.readAsText(file);
                }
            });
        </script>
    </body>
    </html>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_upload_test")

    await session.goto("https://example.com/upload-page")
    targets, _ = session.inspect()
    file_target = next((t for t in targets if t.tag == "input" and t.role == "file"), None)
    assert file_target is not None

    page = await session.upload(
        file_target.ref,
        [{"name": "test_agent_file.txt", "content": "agent payload here", "type": "text/plain"}]
    )
    assert "Uploaded: test_agent_file.txt (agent payload here)" in session.html
    assert "Uploaded: test_agent_file.txt (agent payload here)" in page.view


@pytest.mark.asyncio
async def test_session_upload_local_file_path(tmp_path):
    local_file = tmp_path / "hello_world.txt"
    local_file.write_text("Hello from local file system!", encoding="utf-8")

    html = """<!DOCTYPE html>
    <html>
    <body>
        <form>
            <input type="file" id="doc" name="doc" />
        </form>
        <span id="name_display">none</span>
        <script>
            document.getElementById("doc").addEventListener("change", function(e) {
                document.getElementById("name_display").textContent = e.target.files[0].name;
            });
        </script>
    </body>
    </html>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_upload_local")

    await session.goto("https://example.com/form")
    targets, _ = session.inspect()
    ref = targets[0].ref

    page = await session.upload(ref, str(local_file))
    assert "hello_world.txt" in session.html
    assert "hello_world.txt" in page.view


@pytest.mark.asyncio
async def test_session_form_submit_with_file():
    received_requests = []

    def mock_handler(req: httpx.Request):
        if req.url.path == "/submit-target" and req.method == "POST":
            received_requests.append({
                "method": req.method,
                "url": str(req.url),
                "headers": dict(req.headers),
                "content": req.read(),
            })
            return httpx.Response(200, text="<html><body><h1>Upload Succeeded</h1></body></html>", headers={"content-type": "text/html"})
        html = """<!DOCTYPE html>
        <html>
        <body>
            <form action="/submit-target" method="POST" enctype="multipart/form-data">
                <input type="text" name="title" value="My Document" />
                <input type="file" name="attachment" id="att" />
                <button type="submit" id="btn_submit">Submit Form</button>
            </form>
        </body>
        </html>"""
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_submit_file")

    await session.goto("https://example.com/form")
    targets, _ = session.inspect()
    file_target = next(t for t in targets if t.tag == "input" and t.role == "file")

    # Upload file to the input
    await session.upload(
        file_target.ref,
        [{"name": "resume.pdf", "content": "%PDF-1.4 test bytes", "type": "application/pdf"}]
    )

    # Submit the form using act on submit button
    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.role == "button")
    await session.act(btn.ref)

    assert "Upload Succeeded" in session.html
    assert len(received_requests) == 1
    post_req = received_requests[0]
    assert b"resume.pdf" in post_req["content"]
    assert b"%PDF-1.4 test bytes" in post_req["content"]
    assert b"My Document" in post_req["content"]


@pytest.mark.asyncio
async def test_server_upload_endpoint():
    html = """<!DOCTYPE html>
    <html>
    <body>
        <input type="file" id="f_input" />
        <div id="status">pending</div>
        <script>
            document.getElementById("f_input").addEventListener("change", function(e) {
                document.getElementById("status").textContent = "selected " + e.target.files.length + " file";
            });
        </script>
    </body>
    </html>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    client = httpx.AsyncClient(transport=transport)

    app = create_app()
    with TestClient(app) as test_client:
        # Create session
        res = test_client.post("/v1/sessions")
        assert res.status_code == 200
        sid = res.json()["id"]

        browser_inst = app.state.browser
        session = browser_inst.get(sid)
        session.client = client

        # Goto page
        test_client.post(f"/v1/sessions/{sid}/goto", json={"url": "https://example.com/upload"})

        # Get inspect targets
        targets_res = test_client.get(f"/v1/sessions/{sid}/inspect")
        ref = targets_res.json()["targets"][0]["ref"]

        # Call upload endpoint
        up_res = test_client.post(
            f"/v1/sessions/{sid}/upload",
            json={
                "ref": ref,
                "files": [{"name": "report.docx", "content": "dummy word doc", "type": "application/vnd.openxmlformats"}]
            }
        )
        assert up_res.status_code == 200
        assert "selected 1 file" in up_res.json()["view"]


@pytest.mark.asyncio
async def test_mcp_browser_upload_tool():
    html = """<!DOCTYPE html>
    <html>
    <body>
        <input type="file" id="myfile" />
        <p id="msg">ready</p>
        <script>
            document.getElementById("myfile").addEventListener("change", function(e) {
                document.getElementById("msg").textContent = "MCP got " + e.target.files[0].name;
            });
        </script>
    </body>
    </html>"""

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    mock_client = httpx.AsyncClient(transport=transport)

    mcp = McpServer()
    session = await mcp._get_session()
    session.client = mock_client

    # Navigate to page
    await mcp.handle_call_tool("browser_navigate", {"url": "https://example.com/mcp-page"})

    targets, _ = session.inspect()
    ref = targets[0].ref

    # Call browser_upload tool
    result = await mcp.handle_call_tool(
        "browser_upload",
        {
            "ref": ref,
            "files": [{"name": "spreadsheet.csv", "content": "a,b,c\n1,2,3", "type": "text/csv"}]
        }
    )
    content_text = result["content"][0]["text"]
    assert "MCP got spreadsheet.csv" in content_text


# ---------------------------------------------------------------------------
# Binary payloads: real bytes (NUL, CR, invalid UTF-8, 0x00-0xFF) must
# survive the whole chain byte-exact. These are hermetic regressions for the
# read_text(errors="replace") corruption that turned 1024 binary bytes into
# 2048 mangled ones.


def _binary_payload() -> bytes:
    return bytes(range(256)) * 4


def test_minidom_binary_file_size_and_b64_roundtrip():
    import base64 as b64mod

    ctx = Context()
    ctx.eval(MINIDOM_JS)
    raw = _binary_payload()
    ctx.eval(f"""
        var byteStr = atob({b64mod.b64encode(raw).decode('ascii')!r});
        var binFile = new File([byteStr], "clip.mp4", {{ type: "video/mp4" }});
        // Binary marking mirrors the engine's real upload path (__uploadFiles):
        // atob output is one char per byte, so the binary size override is the
        // byte count. Text Blob size is spec-correct UTF-8 length instead.
        binFile._binary = true;
        binFile.size = binFile._content.length;
    """)
    assert ctx.eval("binFile.size") == len(raw)
    # btoa of the Latin-1 byte string must reproduce the original base64
    roundtrip = ctx.eval("btoa(binFile._content)")
    assert b64mod.b64decode(roundtrip) == raw


def test_text_blob_size_is_utf8_bytes():
    """Spec: Blob size for a string is its UTF-8 byte length (was Latin-1)."""
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    # caf\u00e9 = 5 UTF-8 bytes (c,a,f,0xC3,0xA9), not 4 chars
    assert ctx.eval('new Blob(["caf\\u00e9"]).size') == 5
    assert ctx.eval('new Blob(["hello"]).size') == 5


def test_upload_files_content_b64_marks_binary():
    ctx = Context()
    ctx.eval(MINIDOM_JS)
    import base64 as b64mod

    raw = _binary_payload()
    ctx.eval("""
        __load({t: 'html', c: [{t: 'body', c: []}]}, 'https://example.com');
        var inp = document.createElement("input");
        inp.setAttribute("type", "file");
        inp.setAttribute("id", "v");
        document.body.appendChild(inp);
    """)
    payload = b64mod.b64encode(raw).decode("ascii")
    ctx.eval(
        f"__uploadFiles('#v', [{{ name: 'clip.mp4', contentB64: {payload!r}, type: 'video/mp4' }}])"
    )
    assert ctx.eval("inp.files.length") == 1
    assert ctx.eval("inp.files[0].size") == len(raw)
    assert ctx.eval("inp.files[0]._binary") is True
    assert ctx.eval("btoa(inp.files[0]._content)") == b64mod.b64encode(raw).decode("ascii")


@pytest.mark.asyncio
async def test_binary_local_file_uploads_byte_exact(tmp_path):
    """The live repro: 1024 bytes 0x00-0xFF must arrive in the POST intact."""
    raw = _binary_payload()
    local = tmp_path / "clip.mp4"
    local.write_bytes(raw)

    received = []

    def handler(req: httpx.Request):
        if req.url.path == "/submit-target" and req.method == "POST":
            received.append(req.read())
            return httpx.Response(200, text="<html><body><h1>OK</h1></body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text="""<!DOCTYPE html>
        <html><body>
        <form action="/submit-target" method="POST" enctype="multipart/form-data">
            <input type="file" name="video" id="vid" />
            <span id="sz">none</span>
            <button type="submit" id="btn">Send</button>
        </form>
        <script>
            document.getElementById("vid").addEventListener("change", function (e) {
                document.getElementById("sz").textContent = "size:" + e.target.files[0].size;
            });
        </script>
        </body></html>""", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_binary_exact")

    await session.goto("https://example.com/form")

    targets, _ = session.inspect()
    file_target = next(t for t in targets if t.tag == "input" and t.role == "file")
    await session.upload(file_target.ref, str(local))
    assert "size:1024" in session.html  # File.size is byte count, not char count

    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.role == "button")
    await session.act(btn.ref)

    assert len(received) == 1
    body = received[0]
    assert raw in body, "original binary bytes must appear intact in the POST body"
    # the file part between headers and closing boundary is exactly the payload
    idx = body.find(b'name="video"; filename="clip.mp4"')
    assert idx != -1
    start = body.find(b"\r\n\r\n", idx) + 4
    end = body.find(b"\r\n--", start)
    assert body[start:end] == raw


@pytest.mark.asyncio
async def test_binary_inmemory_b64_via_mcp():
    import base64 as b64mod

    raw = _binary_payload()
    received = []

    def handler(req: httpx.Request):
        if req.url.path == "/up" and req.method == "POST":
            received.append(req.read())
            return httpx.Response(200, text="<html><body>done</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, text="""<!DOCTYPE html>
        <html><body>
        <form action="/up" method="POST" enctype="multipart/form-data">
            <input type="file" name="blob" id="b" />
            <button type="submit" id="go">Go</button>
        </form>
        </body></html>""", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=transport)

    mcp = McpServer()
    session = await mcp._get_session()
    session.client = mock_client

    await mcp.handle_call_tool("browser_navigate", {"url": "https://example.com/mcp-bin"})
    targets, _ = session.inspect()
    ref = targets[0].ref

    result = await mcp.handle_call_tool(
        "browser_upload",
        {
            "ref": ref,
            "files": [{
                "name": "data.bin",
                "content_b64": b64mod.b64encode(raw).decode("ascii"),
                "type": "application/octet-stream",
            }],
        },
    )
    assert "browser_upload failed" not in result["content"][0]["text"]

    surface = session.action_surface()
    btn = next(a for a in surface.actions if a.role == "button")
    await session.act(btn.ref)

    assert len(received) == 1
    assert raw in received[0]


@pytest.mark.asyncio
async def test_page_script_fetch_formdata_binary_byte_exact(tmp_path):
    """A page's own fetch() with FormData must carry binary bytes exactly."""
    raw = _binary_payload()
    local = tmp_path / "clip.mp4"
    local.write_bytes(raw)

    received = []

    def handler(req: httpx.Request):
        if req.url.path == "/api/upload" and req.method == "POST":
            received.append(req.read())
            return httpx.Response(200, text='{"ok":true}', headers={"content-type": "application/json"})
        return httpx.Response(200, text="""<!DOCTYPE html>
        <html><body>
        <input type="file" id="vid" />
        <span id="status">idle</span>
        <script>
            document.getElementById("vid").addEventListener("change", function (e) {
                var f = e.target.files[0];
                var fd = new FormData();
                fd.append("video", f, f.name);
                fetch("/api/upload", { method: "POST", body: fd })
                    .then(function (r) { return r.json(); })
                    .then(function (j) { document.getElementById("status").textContent = "sent " + j.ok; });
            });
        </script>
        </body></html>""", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_fetch_bin")

    await session.goto("https://example.com/fetch-up")
    targets, _ = session.inspect()
    file_target = next(t for t in targets if t.tag == "input" and t.role == "file")
    await session.upload(file_target.ref, str(local))

    assert "sent true" in session.html
    assert len(received) == 1
    assert raw in received[0]


@pytest.mark.asyncio
async def test_upload_rejects_oversize_payload(tmp_path):
    from webresearch.engine.runtime import MAX_UPLOAD_B64_CHARS

    big = tmp_path / "huge.bin"
    big.write_bytes(b"\x00" * (MAX_UPLOAD_B64_CHARS * 3 // 4 + 1024))

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            text='<html><body><input type="file" id="f" /></body></html>',
            headers={"content-type": "text/html"},
        )
    )
    client = httpx.AsyncClient(transport=transport)
    session = Session(client, "s_oversize")

    await session.goto("https://example.com/big")
    targets, _ = session.inspect()
    with pytest.raises(ValueError, match="exceeds engine limit"):
        await session.upload(targets[0].ref, str(big))
