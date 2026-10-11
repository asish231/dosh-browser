import unittest

import httpx

from webresearch.engine.captcha import (
    CaptchaInspection,
    CaptchaSolveResult,
    dump_captcha_code,
    inspect_captcha,
    solve_captcha,
)
from webresearch.engine.frames import load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import run_scripts
from webresearch.engine.tree import html_to_tree, tree_to_html
from webresearch.tools import TOOLS
from webresearch.types import CaptchaReport

JS_PAGE = """<!doctype html><html><body>
<div id="root">loading</div>
<script>
document.getElementById('root').textContent = 'hello from js';
</script>
<input type="checkbox" id="bot" name="robot">
<label for="bot">I'm not a robot</label>
<iframe src="https://www.google.com/recaptcha/api2/anchor" title="reCAPTCHA"></iframe>
</body></html>
"""


class EngineTests(unittest.TestCase):
    def test_js_mutates_dom(self):
        import asyncio

        import httpx

        async def run():
            async with httpx.AsyncClient() as client:
                return await run_scripts(JS_PAGE, "https://example.com/", client, force=True)

        html, info = asyncio.run(run())
        self.assertTrue(info["ran"])
        self.assertIn("hello from js", html)

    def test_inspect_and_click_robot_checkbox(self):
        targets, challenges = inspect_html(JS_PAGE, "https://example.com/")
        roles = [t.role for t in targets]
        self.assertIn("checkbox", roles)
        self.assertTrue(any(c.kind == "recaptcha" for c in challenges))
        box = next(t for t in targets if t.role == "checkbox")
        self.assertFalse(box.checked)
        html, nav = click_html(JS_PAGE, "https://example.com/", box.ref)
        self.assertIsNone(nav)
        targets2, _ = inspect_html(html, "https://example.com/")
        box2 = next(t for t in targets2 if t.ref == box.ref)
        self.assertTrue(box2.checked)

    def test_type_into_input(self):
        html = '<html><body><input type="text" name="q"></body></html>'
        targets, _ = inspect_html(html)
        ref = targets[0].ref
        out = type_html(html, ref, "paris hotels")
        self.assertIn('value="paris hotels"', out)

    def test_tree_roundtrip(self):
        html = "<html><body><p>Hi</p></body></html>"
        back = tree_to_html(html_to_tree(html))
        self.assertIn("<p>Hi</p>", back)

    def test_post_message_is_captured(self):
        import asyncio

        page = "<html><body><script>parent.postMessage('frame-ready', '*')</script></body></html>"

        async def run():
            async with httpx.AsyncClient() as client:
                return await run_scripts(page, "https://example.com/", client, force=True)

        _html, info = asyncio.run(run())
        self.assertEqual(info["post_messages"][0]["data"], "frame-ready")

    def test_frame_tree_and_separate_captcha_report(self):
        import asyncio

        parent = """
        <html><body>
          <div class="g-recaptcha" data-sitekey="site-key-123" data-callback="done"></div>
          <iframe src="/anchor" title="reCAPTCHA"></iframe>
        </body></html>
        """
        anchor = """
        <html><body>
          <div id="recaptcha-anchor" class="recaptcha-checkbox" role="checkbox"
               aria-checked="false" aria-label="I'm not a robot"></div>
          <iframe src="/bframe" title="challenge"></iframe>
        </body></html>
        """
        cells = "".join(
            (
                f'<td id="{index}" class="rc-imageselect-tile" role="button">'
                f'<img class="rc-image-tile-33" src="/payload.jpg" '
                f'style="top: -{index // 3 * 100}%; left: -{index % 3 * 100}%"></td>'
            )
            for index in range(9)
        )
        bframe = f"""
        <html><body>
          <div class="rc-imageselect-desc-wrapper">Select all images with buses</div>
          <table><tr>{cells}</tr></table>
          <input id="recaptcha-token" type="hidden" value="session-blob">
          <button id="recaptcha-reload-button"></button>
          <button id="recaptcha-verify-button">Verify</button>
        </body></html>
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/anchor":
                return httpx.Response(200, text=anchor, headers={"content-type": "text/html"})
            if request.url.path == "/bframe":
                return httpx.Response(200, text=bframe, headers={"content-type": "text/html"})
            if request.url.path == "/payload.jpg":
                return httpx.Response(200, content=b"\xff\xd8sprite", headers={"content-type": "image/jpeg"})
            return httpx.Response(404, text="missing")

        async def run():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
                frames = await load_frames(
                    parent,
                    "https://example.test/page",
                    client,
                    synthetic_captcha=False,
                )
                inspection = await inspect_captcha(
                    parent,
                    "https://example.test/page",
                    client,
                    frames,
                    sprite_endpoint="/sprite",
                )
                return frames, inspection

        frames, inspection = asyncio.run(run())
        report = inspection.report
        self.assertEqual([frame.parent_ref for frame in frames], ["", "f1"])
        self.assertTrue(report.present)
        self.assertEqual(report.kind, "recaptcha")
        self.assertEqual(report.site_key, "site-key-123")
        self.assertEqual(report.checkbox_checked, False)
        self.assertIn("buses", report.prompt)
        self.assertEqual(len(report.tiles), 9)
        self.assertEqual(report.sprite_bytes, len(b"\xff\xd8sprite"))
        self.assertEqual(report.sprite_endpoint, "/sprite")
        self.assertIn("verify", report.buttons)
        self.assertTrue(any("recaptcha-token" in field for field in report.hidden_fields))
        self.assertIn("Inspection only", report.note)

        dump = dump_captcha_code(inspection)
        self.assertTrue(dump.present)
        self.assertIn("g-recaptcha", dump.code)
        self.assertIn("recaptcha-anchor", dump.code)
        self.assertIn("session-blob", dump.code)
        self.assertTrue(any(part.name == "frame" for part in dump.parts))
        self.assertTrue(dump.sprite_b64)

    def test_agent_tools_include_frame_and_captcha_surfaces(self):
        names = {tool["function"]["name"] for tool in TOOLS}
        self.assertIn("browser_frames", names)
        self.assertIn("browser_frame_source", names)
        self.assertIn("browser_captcha", names)
        self.assertIn("browser_captcha_code", names)
        self.assertIn("browser_captcha_solve", names)


class CaptchaSolverTests(unittest.TestCase):
    def test_solve_fails_without_sprite(self):
        import asyncio

        async def run():
            inspection = CaptchaInspection(
                report=CaptchaReport(
                    present=True,
                    kind="recaptcha",
                    prompt="Select all buses",
                    sprite_url="",
                    sprite_bytes=0,
                ),
                sprite=None,
                parent_url="https://example.com",
                parent_html="<html></html>",
            )
            result = await solve_captcha(
                inspection,
                api_key="fake",
                base_url="https://fake.test",
                model="fake",
            )
            return result

        result = asyncio.run(run())
        self.assertFalse(result.solution.solved)
        self.assertIn("sprite", result.solution.error.lower())

    def test_solve_fails_without_prompt(self):
        import asyncio

        async def run():
            inspection = CaptchaInspection(
                report=CaptchaReport(
                    present=True,
                    kind="recaptcha",
                    prompt="",
                    sprite_url="https://example.com/sprite.jpg",
                    sprite_bytes=100,
                ),
                sprite=b"\xff\xd8fake-image-data",
                parent_url="https://example.com",
                parent_html="<html></html>",
            )
            result = await solve_captcha(
                inspection,
                api_key="fake",
                base_url="https://fake.test",
                model="fake",
            )
            return result

        result = asyncio.run(run())
        self.assertFalse(result.solution.solved)
        self.assertIn("prompt", result.solution.error.lower())

    def test_solve_fails_without_tiles(self):
        import asyncio

        async def run():
            inspection = CaptchaInspection(
                report=CaptchaReport(
                    present=True,
                    kind="recaptcha",
                    prompt="Select all buses",
                    sprite_url="https://example.com/sprite.jpg",
                    sprite_bytes=100,
                    tiles=[],
                ),
                sprite=b"\xff\xd8fake-image-data",
                parent_url="https://example.com",
                parent_html="<html></html>",
            )
            result = await solve_captcha(
                inspection,
                api_key="fake",
                base_url="https://fake.test",
                model="fake",
            )
            return result

        result = asyncio.run(run())
        self.assertFalse(result.solution.solved)
        self.assertIn("tile", result.solution.error.lower())

    def test_solve_calls_vision_model_with_correct_payload(self):
        import asyncio
        import json

        from webresearch.types import CaptchaTile

        captured_requests = []

        def handler(request: httpx.Request):
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"selected": [1, 3, 7]}'
                            }
                        }
                    ]
                },
            )

        tiles = [
            CaptchaTile(id=str(i), row=i // 3, column=i % 3)
            for i in range(9)
        ]

        async def run():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                inspection = CaptchaInspection(
                    report=CaptchaReport(
                        present=True,
                        kind="recaptcha",
                        prompt="Select all traffic lights",
                        sprite_url="https://example.com/sprite.jpg",
                        sprite_bytes=100,
                        sprite_content_type="image/jpeg",
                        tiles=tiles,
                    ),
                    sprite=b"\xff\xd8fake-image-data",
                    parent_url="https://example.com",
                    parent_html="<html></html>",
                )
                result = await solve_captcha(
                    inspection,
                    api_key="test-key",
                    base_url="https://vision.test",
                    model="gpt-4o-mini",
                    client=client,
                )
                return result

        result = asyncio.run(run())
        self.assertTrue(result.solution.solved)
        self.assertEqual(len(result.solution.tiles_selected), 3)
        self.assertEqual(result.solution.prompt, "Select all traffic lights")
        self.assertEqual(result.solution.model_used, "gpt-4o-mini")
        self.assertEqual(result.solution.attempt, 1)

        # Verify the request was correctly formed
        self.assertEqual(len(captured_requests), 1)
        req = captured_requests[0]
        self.assertEqual(req.url.path, "/chat/completions")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["temperature"], 0.0)

        # Check the message contains the prompt and image
        msg = body["messages"][0]
        self.assertEqual(msg["role"], "user")
        self.assertTrue(any(c.get("type") == "text" for c in msg["content"]))
        self.assertIn("traffic lights", msg["content"][0]["text"])
        self.assertTrue(any(c.get("type") == "image_url" for c in msg["content"]))

        # Verify tile IDs match the vision model selection
        selected_ids = [s.tile_id for s in result.solution.tiles_selected]
        self.assertEqual(selected_ids, ["0", "2", "6"])  # 0-indexed for 1,3,7

    def test_solve_view_format(self):
        import asyncio
        import json

        from webresearch.types import CaptchaTile

        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"selected": [2, 5]}'
                            }
                        }
                    ]
                },
            )

        tiles = [
            CaptchaTile(id=str(i), row=i // 3, column=i % 3)
            for i in range(9)
        ]

        async def run():
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                inspection = CaptchaInspection(
                    report=CaptchaReport(
                        present=True,
                        kind="hcaptcha",
                        prompt="Select squares with crosswalks",
                        sprite_url="https://example.com/sprite.jpg",
                        sprite_bytes=100,
                        sprite_content_type="image/jpeg",
                        tiles=tiles,
                    ),
                    sprite=b"\xff\xd8fake-image-data",
                    parent_url="https://example.com",
                    parent_html="<html></html>",
                )
                result = await solve_captcha(
                    inspection,
                    api_key="test-key",
                    base_url="https://vision.test",
                    model="vision-model",
                    client=client,
                )
                return result

        result = asyncio.run(run())
        self.assertTrue(result.solution.solved)
        view = result.solution.view
        self.assertIn("CAPTCHA SOLVE", view)
        self.assertIn("crosswalks", view)
        self.assertIn("vision-model", view)
        self.assertIn("TILES_SELECTED: 2", view)


if __name__ == "__main__":
    unittest.main()
