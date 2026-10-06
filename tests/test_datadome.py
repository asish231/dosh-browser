"""DataDome detection and solving.

Behaviour pinned against captured real widget markup.
"""

from __future__ import annotations

import unittest

from webresearch.engine import datadome as dd

# Captured from geo.captcha-delivery.com: the interstitials are script wrappers
# whose only child is a challenge iframe.
HARD_BLOCK = (
    "<html><body class=\"dd-response-page--hard-block\" "
    "data-dd-response-page=\"hard-block\">"
    "<script>var dd={'rt':'c','cid':'AHrlqAAAAAMA3Ytg9PwJ1TcAK-HBlw==',"
    "'hsh':'2F05D671381DB06BEE4CC52C7A6FD3','t':'bv','qp':'','s':46694,"
    "'e':'a63a84c58f720fe500b951a5f1906f1181a91efa5a403f0175c72e3bce3fc'}</script>"
    "<script data-cfasync=\"false\" src=\"https://ct.captcha-delivery.com/c.js\"></script>"
    "<iframe src=\"https://geo.captcha-delivery.com/captcha/?t=bv\" "
    "title=\"DataDome CAPTCHA\"></iframe></body></html>"
)

INTERSTITIAL = (
    "<html><body><script>var dd={'rt':'c','cid':'AHrlqAAAAAMA8Y3gXeGijL8AK-HBlw==',"
    "'t':'fe','s':46694}</script>"
    "<script data-cfasync=\"false\" src=\"https://ct.captcha-delivery.com/c.js\"></script>"
    "<iframe src=\"https://geo.captcha-delivery.com/captcha/?t=fe\" "
    "title=\"DataDome CAPTCHA\"></iframe></body></html>"
)

NETWORK_FLAGGED = (
    "<html><body class=\"dd-response-page--hard-block\">"
    "<iframe src=\"https://geo.captcha-delivery.com/captcha/\"></iframe>"
    "<p>Automated (bot) activity on your network (IP 43.225.193.151)</p>"
    "</body></html>"
)

CONTENT = (
    "<html><body><h1>Munich Ghosts and Spirits Walking Tour</h1>"
    "<p>Reviews from travellers.</p></body></html>"
)


class DetectTest(unittest.TestCase):
    def test_clean_content_is_not_flagged(self) -> None:
        result = dd.detect(CONTENT)
        self.assertFalse(result.present)
        self.assertEqual(result.kind, "")
        self.assertFalse(result.solvable)

    def test_hard_block_is_detected_and_marked_unsolvable(self) -> None:
        result = dd.detect(HARD_BLOCK)
        self.assertTrue(result.present)
        self.assertEqual(result.kind, "hard-block")
        self.assertEqual(result.variant, "bv")
        self.assertFalse(result.solvable, "a hard block has no widget to drag")

    def test_interactive_challenge_is_detected_and_solvable(self) -> None:
        result = dd.detect(INTERSTITIAL)
        self.assertTrue(result.present)
        self.assertEqual(result.kind, "captcha")
        self.assertEqual(result.variant, "fe")
        self.assertTrue(result.solvable)

    def test_empty_body_is_not_flagged(self) -> None:
        self.assertFalse(dd.detect("").present)

    def test_network_flag_is_surfaced(self) -> None:
        result = dd.detect(NETWORK_FLAGGED)
        self.assertTrue(result.network_flagged)
        self.assertEqual(result.flagged_ip, "43.225.193.151")

    def test_request_id_is_extracted(self) -> None:
        result = dd.detect(INTERSTITIAL)
        self.assertTrue(result.request_id.startswith("AHrlq"))

    def test_frame_body_overrides_a_solvable_wrapper(self) -> None:
        """The wrapper claims a challenge; the frame may hold a hard block.

        Observed live: the interstitial said t:'fe' (a solvable first
        encounter) while the frame it framed rendered a hard block. Believing
        the wrapper made the solver retry a drag for a widget that does not
        exist.
        """
        wrapper = INTERSTITIAL  # claims solvable
        frame = (
            "<html><body class=\"dd-response-page--hard-block\" "
            "data-dd-response-page=\"hard-block\">"
            "<p>Access is temporarily restricted</p></body></html>"
        )
        self.assertTrue(dd.detect(wrapper).solvable)
        self.assertFalse(dd.detect(frame).solvable)

    def test_frame_body_can_override_a_hard_block_wrapper(self) -> None:
        wrapper = HARD_BLOCK  # claims unsolvable
        frame = (
            "<html><body class=\"dd-response-page--captcha\" "
            "data-dd-response-page=\"captcha\">"
            "<div class=\"sliderContainer\"><div class=\"sliderTarget\"></div>"
            "<div class=\"slider\"></div></div></body></html>"
        )
        self.assertFalse(dd.detect(wrapper).solvable)
        self.assertTrue(dd.detect(frame).solvable)


class SolveReportingTest(unittest.TestCase):
    """The report must state plainly when client-side solving cannot help."""

    def test_report_marks_engine_only_actions(self) -> None:
        from webresearch.actions import collect

        widget = (
            '<html><body><div class="sliderContainer">'
            '<div class="sliderTarget"></div>'
            '<div class="slider"></div>'
            "</div></body></html>"
        )
        surface = collect(widget, "https://geo.captcha-delivery.com/captcha/")
        handle = next(a for a in surface.actions if a.kind == "drag")
        self.assertFalse(handle.callable_without_engine)
        self.assertEqual(handle.notes, "needs real engine")
        self.assertGreater(surface.engine_only, 0)
        self.assertIn("real browser", surface.view(limit=0))

    def test_handle_and_drop_target_pair_up(self) -> None:
        from webresearch.actions import collect

        widget = (
            '<html><body><div class="sliderContainer">'
            '<div class="sliderTarget"></div>'
            '<div class="slider"></div>'
            "</div></body></html>"
        )
        surface = collect(widget, "https://geo.captcha-delivery.com/captcha/")
        kinds = [a.kind for a in surface.actions]
        self.assertIn("drag", kinds)
        self.assertIn("dropzone", kinds)


if __name__ == "__main__":
    unittest.main()