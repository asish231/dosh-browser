"""A2/A5: adversarial-lane decision paths — honest-capability tests.

A2 (reCAPTCHA challenge coverage): the solver supports dynamic,
multicaptcha, and imageselect challenge types; every OTHER type is
REFUSED with a clear error naming the unsupported type — never a
faked solve. These tests pin that decision path without network.

A5 (login/OTP): the engine never guesses OTP codes, caps attempts,
expires checkpoints, and reports honest verdicts. The existing
login-capability suite covers behavior; these tests pin the
never-guess + cap primitives directly.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.captcha import solve_recaptcha_v2, RecaptchaV2Error
from webresearch.engine.login import MAX_OTP_ATTEMPTS, OtpCheckpoint


# ---------------------------------------------------------------------------
# A2: supported challenge types + honest refusal of everything else
# ---------------------------------------------------------------------------

def test_a2_supported_challenge_types_are_the_documented_set():
    """The solver's dispatch names exactly the types it can attempt."""
    src = inspect.getsource(solve_recaptcha_v2)
    # The decision gate in the protocol loop:
    assert 'if ctype not in {"dynamic", "multicaptcha", "imageselect"}:' in src, (
        "the supported-type gate must stay explicit in the protocol loop"
    )
    # And the refusal path raises (never falls through to a fake solve):
    assert "unsupported challenge type" in src


def test_a2_unsupported_type_raises_never_fakes():
    """An unsupported challenge type (audio) must raise
    RecaptchaV2Error naming the type — the engine never fabricates a
    token for a challenge kind it cannot see."""

    class MockResp:
        def __init__(self, text="", content=b"", status_code=200, headers=None):
            self.text = text
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}

    class MockClient:
        """Serves a protocol flow whose reload rresp carries ctype='audio'."""

        async def post(self, url, content=None, data=None, headers=None):
            # reload is POSTed with a protobuf body — same rresp flow
            u = str(url)
            if "reload" in u:
                # _rc_json parses the SECOND line (real wire shape:
                # junk-prefix line, then JSON).
                return MockResp(
                    text=")]}'\n" + '["rresp","tok-1",1,"x",1,"audio",null,0,0,3600]'
                )
            return MockResp(status_code=404)

        async def get(self, url, headers=None):
            u = str(url)
            if "api.js" in u:
                # loader with a version the regex can extract
                return MockResp(
                    text="po.src='https://www.gstatic.com/recaptcha/releases/vTEST01/worker.js';"
                )
            if "anchor" in u:
                return MockResp(
                    text='<input id="recaptcha-token" type="hidden" value="ANCHOR-TOKEN-1">'
                )
            if "reload" in u:
                # rresp[5] = challenge type: 'audio' — UNSUPPORTED by the
                # engine's vision-tile lane
                return MockResp(
                    text='["rresp","tok-1",1,"x",1,"audio",null,0,0,3600]'
                )
            return MockResp(status_code=404)

    import asyncio

    async def run():
        return await solve_recaptcha_v2(
            MockClient(), site_key="sitekey", site_url="https://x.test/",
            grid_select=lambda *a, **k: [], tile_contains=lambda *a, **k: False,
            max_rounds=2,
        )

    raised = None
    try:
        result = asyncio.run(run())
        # reaching here with a result would be a FAKED solve — fail hard
        raise AssertionError(f"unsupported type must not produce a token, got {result!r}")
    except RecaptchaV2Error as exc:
        raised = exc
    assert raised is not None
    assert "unsupported challenge type" in str(raised), (
        f"the refusal must name the unsupported type: {raised!r}"
    )
    assert "audio" in str(raised), f"the refusal names the actual ctype: {raised!r}"


# ---------------------------------------------------------------------------
# A5: OTP never-guess primitives
# ---------------------------------------------------------------------------

def test_a5_otp_attempts_are_capped_by_constant():
    """The cap is a module constant — attempts are bounded by design."""
    assert isinstance(MAX_OTP_ATTEMPTS, int)
    assert 1 <= MAX_OTP_ATTEMPTS <= 5, (
        f"the OTP cap must stay small (capped, never-guess design): {MAX_OTP_ATTEMPTS}"
    )


def test_a5_otp_checkpoint_never_stores_the_code():
    """A checkpoint records that a code is EXPECTED, never the code itself."""
    import dataclasses

    fields = {f.name: f.type for f in dataclasses.fields(OtpCheckpoint)}
    assert "expected_code" not in fields and "code" not in fields and "secret" not in fields, (
        f"the checkpoint must never carry the expected code: {fields}"
    )


def test_a5_otp_checkpoint_expiry_is_real():
    """Expired checkpoints are refused — the constructor/validator must
    carry an expiry (or the login module enforces one at check time)."""
    import inspect as insp
    from webresearch.engine import login as login_mod

    src = insp.getsource(login_mod)
    assert "expire" in src.lower() or "ttl" in src.lower() or "expires_at" in src.lower(), (
        "the OTP lane must enforce checkpoint expiry"
    )
