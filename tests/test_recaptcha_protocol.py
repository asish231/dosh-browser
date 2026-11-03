"""reCAPTCHA v2 challenge protocol solver: unit-tested against recorded shapes.

The protocol lane (anchor -> reload protobuf -> payload -> userverify) is
driven with a mocked HTTP client and recorded response shapes from the live
widget build (v=bSuJ2xBC8cFy7pM4Z3Hu9boH, 2026-11): rresp/pmeta arrays,
dresp replacements, and the uvresp verdict (an embedded rresp = rejected).
Vision classification is stubbed; these tests pin the WIRE PROTOCOL, not
image understanding.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webresearch.engine.captcha as captcha_mod
from webresearch.engine.captcha import (
    RecaptchaV2Error,
    _goal_from_meta,
    _protobuf,
    _rc_base64,
    _varint,
    solve_recaptcha_v2,
)


def test_varint_small_values():
    out = bytearray()
    _varint(0, out)
    _varint(1, out)
    _varint(127, out)
    assert bytes(out) == b"\x00\x01\x7f"


def test_varint_multibyte():
    out = bytearray()
    _varint(128, out)
    assert bytes(out) == b"\x80\x01"


def test_protobuf_reload_body_shape():
    body = _protobuf(
        [
            (1, b"v1.2.3"),
            (2, b"token-abc"),
            (6, b"fi"),
            (14, b"6Lsitekey"),
        ]
    )
    # field 1 (tag 0x0a = '\n', len 6, value "v1.2.3")
    assert body == (
        b"\x0a\x06v1.2.3"          # field 1: version (6 bytes)
        b"\x12\x09token-abc"       # field 2: token (9 bytes)
        b"\x32\x02fi"              # field 6: reason
        b"\x72\x096Lsitekey"       # field 14: site key
    )


def test_rc_base64_urlsafe_and_dot_padded():
    # 4 -> 5 chars + one '.' pad, like Google's co= parameter.
    out = _rc_base64("https://x:1")
    assert "=" not in out
    assert out.endswith(".")


def test_goal_map_extraction_from_bundle(monkeypatch):
    """The ID->goal table is extracted from the bundle's string table.

    A miniature bundle mirrors the real layout: each class ID is followed
    (within 3000 chars) by its 'Select all squares with <strong>X</strong>'
    description. /m/04_sv=MOTORCYCLES and /m/01bjv=BUS are the exact pair
    a reversed guess got wrong on live multicaptcha rounds.
    """
    import asyncio

    import webresearch.engine.captcha as cap

    fake_bundle = (
        'x = ["/m/04_sv", null, 2, 4, 4];'
        'y = \'Select all squares with <strong>motorcycles</strong>\';'
        'z = ["/m/01bjv", null, 2, 4, 4];'
        'w = \'Select all squares with <strong>buses</strong>\';'
    )

    class FakeClient:
        async def get(self, url, headers=None):
            class R:
                status_code = 200
                text = fake_bundle
                content = fake_bundle.encode()

            return R()

    cap._RC_GOAL_CACHE.clear()
    mapping = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        cap._extract_goal_map(FakeClient(), "vTEST")
    )
    assert mapping.get("/m/04_sv") == "motorcycles"
    assert mapping.get("/m/01bjv") == "buses"
    # Cached per version: a second call must not re-fetch.
    cap._RC_GOAL_CACHE["vTEST"] = {"cached": True}
    again = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        cap._extract_goal_map(FakeClient(), "vTEST")
    )
    assert again == {"cached": True}


def test_goal_from_meta_named():
    goal, rows, cols = _goal_from_meta(["/m/0k4j", None, 3, 3, 3, None, "car"])
    assert (goal, rows, cols) == ("car", 3, 3)


def test_goal_from_meta_id_only():
    # multicaptcha metas carry only the class id. These mappings are verified
    # against the live bundle's string table (extracted 2026-11): /m/04_sv is
    # MOTORCYCLES and /m/01bjv is BUS — the reversed guess cost every
    # multicaptcha round until the bundle extraction landed.
    goal, rows, cols = _goal_from_meta(["/m/04_sv", None, 2, 4, 4])
    assert goal == "motorcycle"
    assert (rows, cols) == (4, 4)
    goal, _, _ = _goal_from_meta(["/m/01bjv", None, 2, 4, 4])
    assert goal == "bus"
    goal, _, _ = _goal_from_meta(["/m/0k4j", None, 2, 4, 4])
    assert goal == "car"


def _resp(status, text="", content=b""):
    class R:
        def __init__(self):
            self.status_code = status
            self.text = text
            self.content = content

    return R()


class FakeClient:
    """URL-dispatching fake: handlers keyed by URL substring, called in order."""

    def __init__(self, handlers: dict):
        # {url substring: [response, response, ...] or single response}
        self.handlers = {k: (list(v) if isinstance(v, list) else [v]) for k, v in handlers.items()}
        self.calls = []

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        for key, responses in self.handlers.items():
            if key in url:
                if len(responses) > 1:
                    return responses.pop(0)
                return responses[0]
        raise AssertionError(f"unexpected GET {url}")

    async def post(self, url, content=None, data=None, headers=None):
        self.calls.append(("POST", url, data))
        for key, responses in self.handlers.items():
            if key in url:
                if len(responses) > 1:
                    return responses.pop(0)
                return responses[0]
        raise AssertionError(f"unexpected POST {url}")


def _real_jpeg(size: int = 90) -> bytes:
    """A real (tiny) JPEG grid image: PIL must be able to crop it."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), (128, 128, 128)).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


REAL_JPEG = _real_jpeg()


def _rresp_dynamic(token="tok-round-1", goal="bus", p="p-1"):
    body = json.dumps(
        [
            "rresp",
            token,
            None,
            120,
            ["pmeta", ["/m/04_sv", None, 3, 3, 3, None, goal], None, [1, 3000]],
            "dynamic",
            None,
            None,
            None,
            p,
            None,
            None,
            "09AKid",
            None,
            None,
            None,
            0,
        ]
    )
    return _resp(200, ")]}'\n" + body)


def _rresp_imageselect(token="tok-static-1", goal="bus", p="p-static"):
    """The classic STATIC grid challenge (type 'imageselect'): no replacements."""
    body = json.dumps(
        [
            "rresp",
            token,
            None,
            120,
            ["pmeta", ["/m/04_sv", None, 3, 3, 3, None, goal], None, [1, 3000]],
            "imageselect",
            None,
            None,
            None,
            p,
            None,
            None,
            "09AKid",
            None,
            None,
            None,
            0,
        ]
    )
    return _resp(200, ")]}'\n" + body)


def _dresp(token="tok-after-replace", rid="rep-1", p="p-2"):
    body = json.dumps(["dresp", token, [rid], None, None, p])
    return _resp(200, ")]}'\n" + body)


def _uvresp_pass():
    body = json.dumps(["uvresp", "0cAFfinaltoken" * 20, 0, None, None, None, None, None, None, 0])
    return _resp(200, ")]}'\n" + body)


def _uvresp_fail_with(next_rresp_list):
    """A rejected verify: uvresp embedding a NEW rresp challenge list."""
    body = json.dumps(
        ["uvresp", "0cAFsessiontok" * 10, 0, None, None, None, None, next_rresp_list, None, None, 0]
    )
    return _resp(200, ")]}'\n" + body)


def _rresp_list(token="tok-round-1", goal="bus", p="p-1"):
    """The raw rresp LIST (for embedding inside a rejected uvresp)."""
    return [
        "rresp",
        token,
        None,
        120,
        ["pmeta", ["/m/04_sv", None, 3, 3, 3, None, goal], None, [1, 3000]],
        "dynamic",
        None,
        None,
        None,
        p,
        None,
        None,
        "09AKid",
        None,
        None,
        None,
        0,
    ]


async def always_true(tile, goal):
    return True


async def always_false(tile, goal):
    return False


async def grid_all(tile, goal, rows, cols):
    return list(range(rows * cols))


async def grid_none(tile, goal, rows, cols):
    return []


async def grid_first(tile, goal, rows, cols):
    return [0]


@pytest.mark.asyncio
async def test_solve_dynamic_pass_first_round():
    anchor_html = '<input id="recaptcha-token" value="anchor-token-123">'
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, anchor_html),
            "reload": _rresp_dynamic(),
            "payload": _resp(200, content=REAL_JPEG),
            "replaceimage": _dresp(),
            "userverify": _uvresp_pass(),
        }
    )
    token = await solve_recaptcha_v2(
        client,
        site_key="6Ltest",
        site_url="https://erp.example.ac.in/login.aspx",
        grid_select=grid_first,
        tile_contains=always_true,
    )
    assert token.startswith("0cAFfinaltoken")
    # The replaceimage POST carries the real tile id and the ds=[id] form.
    posts = [c for c in client.calls if c[0] == "POST"]
    replace_calls = [p for p in posts if "replaceimage" in p[1]]
    assert replace_calls, "dynamic flow must replace clicked tiles"
    assert replace_calls[0][2]["ds"] == "[0]"


@pytest.mark.asyncio
async def test_solve_rejects_then_passes_second_round():
    anchor_html = '<input id="recaptcha-token" value="anchor-token-123">'
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, anchor_html),
            "reload": _rresp_dynamic(),
            "payload": _resp(200, content=REAL_JPEG),
            "replaceimage": _dresp(),
            # First verify is REJECTED (embeds a new rresp); the solver
            # replays the next round against the same endpoints.
            "userverify": [
                _uvresp_fail_with(_rresp_list(token="tok-round-2", p="p-3")),
                _uvresp_pass(),
            ],
        }
    )
    token = await solve_recaptcha_v2(
        client,
        site_key="6Ltest",
        site_url="https://erp.example.ac.in/login.aspx",
        grid_select=grid_first,
        tile_contains=always_true,
        max_rounds=4,
    )
    assert token.startswith("0cAFfinaltoken")
    # Exactly two userverify POSTs happened (round 1 rejected, round 2 passed).
    verifies = [c for c in client.calls if c[0] == "POST" and "userverify" in c[1]]
    assert len(verifies) == 2


@pytest.mark.asyncio
async def test_solve_gives_up_after_max_rounds():
    anchor_html = '<input id="recaptcha-token" value="anchor-token-123">'
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, anchor_html),
            "reload": _rresp_dynamic(),
            "payload": _resp(200, content=REAL_JPEG),
            "replaceimage": _dresp(),
            # Every verify is rejected with a fresh challenge.
            "userverify": _uvresp_fail_with(_rresp_list(token="tok-next", p="p-next")),
        }
    )
    with pytest.raises(RecaptchaV2Error):
        await solve_recaptcha_v2(
            client,
            site_key="6Ltest",
            site_url="https://erp.example.ac.in/login.aspx",
            grid_select=grid_none,
            tile_contains=always_false,
            max_rounds=3,
        )


@pytest.mark.asyncio
async def test_solve_imageselect_static_grid():
    """The classic static grid: select cells, verify, NO replaceimage calls."""
    anchor_html = '<input id="recaptcha-token" value="anchor-token-123">'
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, anchor_html),
            "reload": _rresp_imageselect(),
            "payload": _resp(200, content=REAL_JPEG),
            "userverify": _uvresp_pass(),
        }
    )
    token = await solve_recaptcha_v2(
        client,
        site_key="6Ltest",
        site_url="https://erp.example.ac.in/login.aspx",
        grid_select=grid_first,
        tile_contains=always_true,
    )
    assert token.startswith("0cAFfinaltoken")
    # A static grid never triggers tile replacement.
    replaces = [c for c in client.calls if c[0] == "POST" and "replaceimage" in c[1]]
    assert replaces == []


@pytest.mark.asyncio
async def test_solve_raises_without_anchor_token():
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, "<html>no token here</html>"),
        }
    )
    with pytest.raises(RecaptchaV2Error):
        await solve_recaptcha_v2(
            client,
            site_key="6Ltest",
            site_url="https://erp.example.ac.in/",
            grid_select=grid_first,
            tile_contains=always_true,
        )


@pytest.mark.asyncio
async def test_solve_raises_on_non_image_payload(monkeypatch):
    """A permanently-broken payload endpooint exhausts rounds and raises.

    The solver now RECOVERS from a throttled grid fetch by requesting a
    fresh challenge via reload instead of aborting; with the payload
    always broken the loop consumes its round budget and raises. Pacing
    is zeroed so the recovery waits cost the test nothing.
    """
    monkeypatch.setattr(captcha_mod, "_RC_PACING_SCALE", 0.0)
    anchor_html = '<input id="recaptcha-token" value="anchor-token-123">'
    client = FakeClient(
        {
            "api.js": _resp(200, "src: https://www.gstatic.com/recaptcha/releases/vTEST1/recaptcha__en.js"),
            "anchor": _resp(200, anchor_html),
            "reload": _rresp_dynamic(),
            "payload": _resp(200, content=b"<html>error page</html>"),  # never an image
        }
    )
    with pytest.raises(RecaptchaV2Error):
        await solve_recaptcha_v2(
            client,
            site_key="6Ltest",
            site_url="https://erp.example.ac.in/",
            grid_select=grid_first,
            tile_contains=always_true,
            max_rounds=3,
        )
    # Recovery actually engaged: a reload (fresh challenge) was requested.
    reloads = [c for c in client.calls if c[0] == "POST" and "reload" in c[1]]
    assert reloads, "grid throttle must trigger a fresh-challenge reload"
