"""Session.solve_recaptcha_v2: wiring test with a stubbed protocol solver.

Pins the session-level contract: it reads the site key from the current
page's captcha inspection, routes tile classification through the engine's
vision helper, stages the token on the report (view upgraded, token never
echoed), and raises honest errors when the page has no captcha or no key.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webresearch.engine.captcha as captcha_mod
from webresearch.fetch import make_client
from webresearch.session import Session
from webresearch.types import CaptchaReport


def _real_jpeg(size: int = 90) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), (128, 128, 128)).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _session_with_captcha(report):
    client = make_client()
    session = Session(client, "s_test")
    # A current page is required; the inspection provides the site key.
    session.current_tab.html = "<html><body>login</body></html>"
    session.url = "https://erp.example.ac.in/login.aspx"
    session.captcha_inspection = captcha_mod.CaptchaInspection(
        report=report,
        parent_url="https://erp.example.ac.in/login.aspx",
        parent_html="<html></html>",
    )
    return session


@pytest.mark.asyncio
async def test_solve_reads_key_and_stages_token(monkeypatch):
    report = CaptchaReport(
        present=True,
        kind="recaptcha",
        site_key="6Ltestkey",
        view="CAPTCHA: recaptcha (inspection only)\nSITE_KEY: 6Ltestkey\nNOTE: n",
    )
    session = _session_with_captcha(report)
    captured: dict = {}

    async def fake_solve(client, *, site_key, site_url, grid_select, tile_contains, max_rounds=8):
        captured["site_key"] = site_key
        captured["site_url"] = site_url
        verdict = await tile_contains(_real_jpeg(), "bus")
        captured["verdict"] = verdict
        cells = await grid_select(_real_jpeg(), "bus", 3, 3)
        captured["cells"] = cells
        return "0cAFtoken" + "x" * 40

    # The session method imports the solver lazily from engine.captcha, so
    # patching the module attribute is enough.
    monkeypatch.setattr(captcha_mod, "solve_recaptcha_v2", fake_solve)

    token = await session.solve_recaptcha_v2()
    assert token.startswith("0cAFtoken")
    assert captured["site_key"] == "6Ltestkey"
    assert "erp.example.ac.in" in captured["site_url"]
    assert isinstance(captured["verdict"], bool)
    assert isinstance(captured["cells"], list) and all(isinstance(c, int) for c in captured["cells"])
    # The token is staged on the report; the VIEW announces it WITHOUT the token.
    assert report.token == token
    assert "SOLVED" in report.view
    assert token not in report.view


@pytest.mark.asyncio
async def test_solve_errors_without_captcha():
    session = Session(make_client(), "s_test2")
    with pytest.raises(ValueError):
        await session.solve_recaptcha_v2()


@pytest.mark.asyncio
async def test_solve_errors_without_site_key():
    report = CaptchaReport(present=True, kind="recaptcha", site_key="", view="CAPTCHA: recaptcha")
    session = _session_with_captcha(report)
    with pytest.raises(ValueError):
        await session.solve_recaptcha_v2()
