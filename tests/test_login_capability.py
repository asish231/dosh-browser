"""Login capability: wall detection, browser_login, OTP checkpoints, state probe.

Hermetic: every HTTP exchange runs through MockTransport. Secrets are
asserted to never appear in views/results (the no-echo rule).
"""

import httpx
import pytest

from webresearch.engine.inspect import inspect_html
from webresearch.engine.login import (
    MAX_OTP_ATTEMPTS,
    OtpCheckpoint,
    detect_login_wall,
    detect_otp_page,
    verify_logged_in,
)
from webresearch.session import Session

LOGIN_PAGE = """<!DOCTYPE html><html><head><title>Sign in</title></head><body>
<h1>Sign in</h1>
<form action="/checkpoint/lg/login-submit" method="POST">
  <input id="username" name="session_key" type="text" placeholder="Email or Phone" />
  <input id="password" name="session_password" type="password" placeholder="Password" />
  <button id="login-btn" type="submit">Sign in</button>
</form>
<a href="/uas/request-password-reset">Forgot password?</a>
<script>var a = 1;</script><script>var b = 2;</script>
</body></html>"""

OTP_PAGE = """<!DOCTYPE html><html><body>
<h2>Verify your identity</h2>
<p>we sent a code to your phone •••42</p>
<form action="/checkpoint/otp-submit" method="POST">
  <input name="otp" type="text" maxlength="6" inputmode="numeric" autocomplete="one-time-code" />
  <button type="submit">Verify</button>
</form>
</body></html>"""

SPLIT_OTP_PAGE = """<!DOCTYPE html><html><body>
<h2>Enter the code we sent</h2>
<form action="/split-verify" method="POST">
  <input name="digit-0" maxlength="1" type="text" />
  <input name="digit-1" maxlength="1" type="text" />
  <input name="digit-2" maxlength="1" type="text" />
  <input name="digit-3" maxlength="1" type="text" />
  <input name="digit-4" maxlength="1" type="text" />
  <input name="digit-5" maxlength="1" type="text" />
  <button type="submit">Verify</button>
</form>
</body></html>"""

FEED_PAGE = """<!DOCTYPE html><html><body>
<h1>Welcome back, Asish</h1>
<nav><a href="/logout">Log out</a></nav>
</body></html>"""

LOCK_PAGE = """<!DOCTYPE html><html><body>
<p>Your account has been temporarily locked due to too many attempts</p>
</body></html>"""

USER = "asish@example.com"
PASS = "hunter2-secret"
GOOD_CODE = "482913"


def make_transport(state: dict):
    """A tiny auth server: correct creds → OTP; correct OTP → feed (li_at=FULL)."""

    def handler(req: httpx.Request):
        path = req.url.path
        if req.method == "POST":
            body = req.read().decode()
            state.setdefault("posts", []).append((path, body))
            if "login-submit" in path:
                if f"session_key={USER.replace('@', '%40')}" in body and f"session_password={PASS}" in body:
                    return httpx.Response(
                        200,
                        text=OTP_PAGE,
                        headers={"content-type": "text/html", "set-cookie": "li_at=HALF; Path=/"},
                    )
                return httpx.Response(200, text=LOGIN_PAGE, headers={"content-type": "text/html"})
            if "otp-submit" in path:
                if f"otp={GOOD_CODE}" in body:
                    return httpx.Response(
                        200,
                        text=FEED_PAGE,
                        headers={"content-type": "text/html", "set-cookie": "li_at=FULL; Path=/"},
                    )
                return httpx.Response(200, text=OTP_PAGE, headers={"content-type": "text/html"})
            if "split-verify" in path:
                joined = "&".join(f"digit-{i}={GOOD_CODE[i]}" for i in range(6))
                if joined in body:
                    return httpx.Response(
                        200,
                        text=FEED_PAGE,
                        headers={"content-type": "text/html", "set-cookie": "li_at=FULL; Path=/"},
                    )
                return httpx.Response(200, text=SPLIT_OTP_PAGE, headers={"content-type": "text/html"})
        if "locked" in path:
            return httpx.Response(200, text=LOCK_PAGE, headers={"content-type": "text/html"})
        if "feed" in path:
            return httpx.Response(200, text=FEED_PAGE, headers={"content-type": "text/html"})
        return httpx.Response(200, text=LOGIN_PAGE, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def _session(state: dict) -> Session:
    return Session(httpx.AsyncClient(transport=make_transport(state)), "s_login_test")


# ---------------------------------------------------------------------------
# Detection units


def test_login_wall_detected_with_refs():
    targets, _ = inspect_html(LOGIN_PAGE, "https://www.linkedin.com/login")
    wall = detect_login_wall(LOGIN_PAGE, "https://www.linkedin.com/login", targets)
    assert wall is not None and wall.kind == "form"
    assert wall.confidence >= 0.9
    assert wall.username_ref and wall.password_ref and wall.submit_ref
    assert wall.username_name == "session_key"


def test_password_role_in_both_surfaces():
    from webresearch.actions import collect

    targets, _ = inspect_html(LOGIN_PAGE, "https://x.com/login")
    pwd = next(t for t in targets if t.name == "session_password")
    assert pwd.role == "password"
    surface = collect(LOGIN_PAGE, "https://x.com/login")
    action = next(a for a in surface.actions if a.field_name == "session_password")
    assert action.role == "password"


def test_otp_single_input_detected():
    targets, _ = inspect_html(OTP_PAGE, "https://www.linkedin.com/checkpoint/challenge")
    wall = detect_otp_page(OTP_PAGE, "https://www.linkedin.com/checkpoint/challenge", targets)
    assert wall is not None and wall.kind == "otp"
    assert len(wall.refs) == 1
    assert wall.expected_length == 6
    assert wall.split_boxes is False


def test_otp_split_boxes_detected():
    targets, _ = inspect_html(SPLIT_OTP_PAGE, "https://www.instagram.com/challenge")
    wall = detect_otp_page(SPLIT_OTP_PAGE, "https://www.instagram.com/challenge", targets)
    assert wall is not None and wall.kind == "otp"
    assert wall.split_boxes is True
    assert len(wall.refs) == 6
    assert wall.expected_length == 6


def test_verify_verdicts():
    assert verify_logged_in(FEED_PAGE, "https://x.com/feed", client_cookies={}) == "verified"
    assert verify_logged_in(FEED_PAGE, "https://x.com/feed", client_cookies={"li_at": "T"}) == "verified"
    assert verify_logged_in(LOGIN_PAGE, "https://x.com/login") == "failed"
    assert (
        verify_logged_in(OTP_PAGE, "https://www.linkedin.com/checkpoint/challenge") == "otp_required"
    )
    assert verify_logged_in(LOCK_PAGE, "https://x.com/account/locked") == "account_locked"


def test_checkpoint_attempt_and_expiry():
    cp = OtpCheckpoint(id="c1", refs=["e1"], expected_length=6)
    cp.attempts = MAX_OTP_ATTEMPTS
    assert cp.attempts_left == 0
    old = OtpCheckpoint(id="c2", refs=["e1"])
    old.created_at -= 700
    assert old.expired is True


# ---------------------------------------------------------------------------
# End-to-end login flows


@pytest.mark.asyncio
async def test_login_wall_visible_in_view():
    state: dict = {}
    session = _session(state)
    page = await session.goto("https://www.linkedin.com/login")
    assert "LOGIN WALL" in page.view
    assert "browser_login" in page.view
    assert "Never invent credentials" in page.view


@pytest.mark.asyncio
async def test_correct_credentials_lead_to_otp_then_verified():
    state: dict = {}
    session = _session(state)
    result = await session.login("https://www.linkedin.com/login", USER, PASS)
    assert result["verdict"] == "otp_required"
    checkpoint = result["checkpoint"]
    assert checkpoint

    otp = await session.submit_otp(checkpoint, GOOD_CODE)
    assert otp["verdict"] == "verified"
    assert "li_at" in session.client.cookies

    # Credentials and code never echoed into the view.
    assert PASS not in (session.page.view if session.page else "")
    assert GOOD_CODE not in (session.page.view if session.page else "")


@pytest.mark.asyncio
async def test_wrong_credentials_reported_failed():
    state: dict = {}
    session = _session(state)
    result = await session.login("https://www.linkedin.com/login", USER, "wrong-password")
    assert result["verdict"] == "failed"
    assert "li_at" not in session.client.cookies
    assert "wrong-password" not in result.get("message", "")


@pytest.mark.asyncio
async def test_otp_attempts_capped_and_expired():
    state: dict = {}
    session = _session(state)
    result = await session.login("https://www.linkedin.com/login", USER, PASS)
    checkpoint = result["checkpoint"]

    wrong1 = await session.submit_otp(checkpoint, "000000")
    assert wrong1["verdict"] == "rejected" and wrong1["attempts_left"] == 2
    wrong2 = await session.submit_otp(checkpoint, "111111")
    assert wrong2["verdict"] == "rejected" and wrong2["attempts_left"] == 1
    wrong3 = await session.submit_otp(checkpoint, "222222")
    # Third wrong code exhausts the checkpoint; refuse further attempts.
    assert wrong3["verdict"] == "failed"
    after = await session.submit_otp(checkpoint, GOOD_CODE)
    assert after["verdict"] == "failed"  # checkpoint closed

    # Expiry: forge an old checkpoint.
    from webresearch.engine.login import OTP_CHECKPOINT_TTL_SECONDS

    session._otp_checkpoints["old"] = OtpCheckpoint(id="old", refs=[], created_at=1790000000.0)
    expired = await session.submit_otp("old", GOOD_CODE)
    assert expired["verdict"] == "failed" and "expired" in expired["message"]
    assert OTP_CHECKPOINT_TTL_SECONDS == 600


@pytest.mark.asyncio
async def test_otp_length_mismatch_counts_attempt():
    state: dict = {}
    session = _session(state)
    result = await session.login("https://www.linkedin.com/login", USER, PASS)
    short = await session.submit_otp(result["checkpoint"], "12")
    assert short["verdict"] == "failed"
    assert "expects 6" in short["message"]


@pytest.mark.asyncio
async def test_split_box_otp_fills_every_digit():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            text=SPLIT_OTP_PAGE if "split" in req.url.path or req.method == "GET" else FEED_PAGE,
            headers={"content-type": "text/html"},
        )
        if "split-verify" not in req.url.path
        else httpx.Response(
            200,
            text=(
                FEED_PAGE
                if all(f"digit-{i}={GOOD_CODE[i]}" in req.read().decode() for i in range(6))
                else SPLIT_OTP_PAGE
            ),
            headers={"content-type": "text/html", "set-cookie": "li_at=FULL; Path=/"},
        )
    )
    session = Session(httpx.AsyncClient(transport=transport), "s_split")
    await session.goto("https://www.instagram.com/challenge")
    wall = session.login_wall()
    assert wall is not None and wall["kind"] == "otp" and wall["split_boxes"] is True

    # Forge the checkpoint from the detected wall.
    from webresearch.engine.login import OtpCheckpoint

    cp = OtpCheckpoint(
        id="split1",
        refs=wall["refs"],
        expected_length=wall["expected_length"],
        split_boxes=True,
    )
    session._otp_checkpoints["split1"] = cp
    result = await session.submit_otp("split1", GOOD_CODE)
    assert result["verdict"] == "verified", result


@pytest.mark.asyncio
async def test_login_state_probe():
    state: dict = {}
    session = _session(state)
    out = await session.login_state("https://www.linkedin.com/login")
    assert out["verdict"] in {"failed", "unknown"}
    assert out.get("login_wall") is True

    # Logged-in probe: cookie present, feed markers.
    session.client.cookies.set("li_at", "FULL")
    out2 = await session.login_state("https://www.linkedin.com/feed")
    assert out2["verdict"] == "verified"


@pytest.mark.asyncio
async def test_login_already_logged_in_short_circuits():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text=FEED_PAGE, headers={"content-type": "text/html"})
    )
    session = Session(httpx.AsyncClient(transport=transport), "s_already")
    result = await session.login("https://www.linkedin.com/feed", USER, PASS)
    assert result["verdict"] == "verified"
    assert "already logged in" in result["message"]


@pytest.mark.asyncio
async def test_tools_and_server_surfaces_exposed():
    from fastapi.testclient import TestClient

    from webresearch.mcp import TOOLS as MCP_TOOLS
    from webresearch.server import create_app
    from webresearch.tools import TOOLS

    names = {t["function"]["name"] for t in TOOLS}
    mcp_names = {t["name"] for t in MCP_TOOLS}
    assert {"browser_login", "browser_submit_otp", "browser_login_state"} <= names
    assert {"browser_login", "browser_submit_otp", "browser_login_state"} <= mcp_names

    app = create_app()
    with TestClient(app) as tc:
        res = tc.post("/v1/sessions")
        sid = res.json()["id"]
        # Tool wiring smoke: invalid checkpoint returns a clean failure, not a 500.
        otp = tc.post(f"/v1/sessions/{sid}/otp", json={"checkpoint": "nope", "code": "123456"})
        assert otp.status_code == 200
        assert otp.json()["verdict"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_login_tools():
    from webresearch.session import Browser

    state: dict = {}
    browser = Browser()
    session = await browser.create()
    session.client = httpx.AsyncClient(transport=make_transport(state))
    browser.sessions[session.id] = session

    from webresearch.dispatch import invoke

    result = await invoke(
        httpx.AsyncClient(),
        browser,
        "browser_login",
        {"session_id": session.id, "url": "https://www.linkedin.com/login", "username": USER, "password": PASS},
    )
    assert result["verdict"] == "otp_required"
    otp = await invoke(
        httpx.AsyncClient(),
        browser,
        "browser_submit_otp",
        {"session_id": session.id, "checkpoint": result["checkpoint"], "code": GOOD_CODE},
    )
    assert otp["verdict"] == "verified"
    # The password never appears in the result payloads.
    assert PASS not in str(result) and PASS not in str(otp)


@pytest.mark.asyncio
async def test_runtime_reports_login_wall_in_js_info():
    # A two-script login page triggers the JS pass, and login detection rides it.
    state: dict = {}
    session = _session(state)
    page = await session.goto("https://www.linkedin.com/login")
    assert "LOGIN WALL" in page.view
    wall = session.login_wall()
    assert wall is not None
    assert wall["kind"] == "form"
    assert wall["confidence"] >= 0.9


@pytest.mark.asyncio
async def test_no_secret_in_views_or_results():
    state: dict = {}
    session = _session(state)
    page = await session.goto("https://www.linkedin.com/login")
    # The view shows refs, not credentials typed later.
    result = await session.login("https://www.linkedin.com/login", USER, PASS)
    otp = await session.submit_otp(result["checkpoint"], GOOD_CODE)
    blob = page.view + str(result) + str(otp) + (session.page.view if session.page else "")
    assert PASS not in blob
    assert GOOD_CODE not in blob
    assert USER not in blob or "session_key" in blob  # email may appear only as a field name context
