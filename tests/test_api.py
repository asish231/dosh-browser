import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from webresearch.server import create_app

PARENT = b"""<!doctype html><html><body>
<div class="g-recaptcha" data-sitekey="api-site-key"></div>
<iframe src="/anchor" title="reCAPTCHA"></iframe>
</body></html>"""

ANCHOR = b"""<!doctype html><html><body>
<div id="recaptcha-anchor" role="checkbox" aria-checked="false"
     aria-label="I'm not a robot"></div>
<iframe src="/bframe" title="challenge"></iframe>
</body></html>"""

BFRAME = b"""<!doctype html><html><body>
<div class="rc-imageselect-desc-wrapper">Select all images with traffic lights</div>
<table><tr>
<td id="0" class="rc-imageselect-tile"><img class="rc-image-tile-33" src="/sprite.jpg" style="top:0%;left:0%"></td>
</tr></table>
<input id="recaptcha-token" type="hidden" value="opaque-session-data">
<button id="recaptcha-verify-button">Verify</button>
</body></html>"""

SPRITE = b"\xff\xd8\xff\xe0test-captcha-sprite"


class _SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        resources = {
            "/": (PARENT, "text/html"),
            "/anchor": (ANCHOR, "text/html"),
            "/bframe": (BFRAME, "text/html"),
            "/sprite.jpg": (SPRITE, "image/jpeg"),
        }
        body, content_type = resources.get(self.path, (b"missing", "text/plain"))
        self.send_response(200 if self.path in resources else 404)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class CaptchaApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
        cls.site_thread = threading.Thread(target=cls.site.serve_forever, daemon=True)
        cls.site_thread.start()
        cls.url = f"http://127.0.0.1:{cls.site.server_port}/"

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()
        cls.site_thread.join(timeout=2)

    def test_session_exposes_captcha_frames_source_and_sprite(self):
        with TestClient(create_app()) as api:
            sid = api.post("/v1/sessions", json={}).json()["id"]
            page_response = api.post(
                f"/v1/sessions/{sid}/goto",
                json={"url": self.url, "js": "auto"},
            )
            self.assertEqual(page_response.status_code, 200)
            page = page_response.json()
            self.assertTrue(page["captcha"]["present"])
            self.assertIn("CAPTCHA OBJECT", page["view"])

            report = api.get(f"/v1/sessions/{sid}/captcha").json()
            self.assertEqual(report["site_key"], "api-site-key")
            self.assertEqual(report["sprite_bytes"], len(SPRITE))
            self.assertEqual(
                report["sprite_endpoint"],
                f"/v1/sessions/{sid}/captcha/sprite",
            )

            frames = api.get(f"/v1/sessions/{sid}/frames").json()
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[1]["parent_ref"], "f1")

            source = api.get(f"/v1/sessions/{sid}/frames/f2/source")
            self.assertEqual(source.status_code, 200)
            self.assertIn("traffic lights", source.text)

            sprite = api.get(f"/v1/sessions/{sid}/captcha/sprite")
            self.assertEqual(sprite.status_code, 200)
            self.assertEqual(sprite.content, SPRITE)

            invoked = api.post(
                "/v1/invoke",
                json={
                    "name": "browser_captcha",
                    "arguments": {"session_id": sid},
                },
            )
            self.assertEqual(invoked.status_code, 200)
            self.assertTrue(invoked.json()["present"])

            code = api.get(f"/v1/sessions/{sid}/captcha/code").json()
            self.assertTrue(code["present"])
            self.assertIn("g-recaptcha", code["code"])
            self.assertIn("recaptcha-anchor", code["code"])
            self.assertIn("recaptcha-token", code["code"])
            self.assertIn("opaque-session-data", code["code"])
            self.assertTrue(any(part["name"] == "frame" for part in code["parts"]))
            self.assertTrue(code["sprite_b64"])

            text = api.get(f"/v1/sessions/{sid}/captcha/code.txt")
            self.assertEqual(text.status_code, 200)
            self.assertIn("CAPTCHA CODE DUMP", text.text)

            oneshot = api.post("/v1/captcha/code", json={"url": self.url, "js": "auto"})
            self.assertEqual(oneshot.status_code, 200)
            self.assertTrue(oneshot.json()["present"])

            invoked_code = api.post(
                "/v1/invoke",
                json={
                    "name": "browser_captcha_code",
                    "arguments": {"session_id": sid},
                },
            )
            self.assertEqual(invoked_code.status_code, 200)
            self.assertIn("======== CAPTCHA CODE DUMP ========", invoked_code.json()["code"])


if __name__ == "__main__":
    unittest.main()

