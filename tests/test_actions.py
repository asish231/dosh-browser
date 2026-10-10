"""Action surface coverage.

Every test here pins a loss measured against a real page: the fixtures are
captured markup, and the assertions compare what the agent can reach against
what actually exists in the document. A silent drop fails the suite.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from webresearch.actions import collect
from webresearch.types import FetchResult

REPO = Path(__file__).resolve().parents[1]


def _unescape(selector: str) -> str:
    """Undo the CSS identifier escaping applied when building selectors."""
    return re.sub(r"\\(.)", r"\1", selector)


def _resolve_last(target: str):
    """Resolve the final selector segment against the DataDome fixture."""
    from selectolax.lexbor import LexborHTMLParser

    final = _unescape(target.split(">")[-1].strip())
    return LexborHTMLParser(DATADOME_FIXTURE).css_first(final)

# Captured DataDome widget markup. The handle is `<div class="slider">` with an
# `<i class="sliderIcon">` child, sitting beside `.sliderTarget`, inside
# `.sliderContainer`. "slider" is a prefix of all four, which is the trap.
DATADOME_FIXTURE = """
<html><body class="dd-response-page--captcha">
<div id="captcha-container" class="captcha" data-dd-captcha-container>
  <div class="captcha__header" data-dd-captcha-header></div>
  <p class="captcha__human__title">Verification Required</p>
  <div id="ddv1-captcha-container" class="captcha__ddv1">
    <div id="captcha__puzzle" class="toggled">
      <canvas width="280" height="0" aria-hidden="true"></canvas>
    </div>
    <div id="captcha__frame__bottom" class="toggled">
      <div class="sliderText"><p>Slide right to secure your access</p></div>
      <div class="sliderContainer">
        <div class="sliderbg"></div>
        <div class="sliderMask"></div>
        <div class="sliderTarget"><i class="sliderTargetIcon"></i></div>
        <div class="slider"><i class="sliderIcon"></i></div>
      </div>
    </div>
    <div id="captcha__audio">
      <audio class="audio-captcha-track" src="https://dd.test/a.wav"></audio>
      <div class="audio-captcha-input-container">
        <input class="audio-captcha-inputs" maxlength="1" data-index="0" name="d0">
        <input class="audio-captcha-inputs" maxlength="1" data-index="1" name="d1">
        <input class="audio-captcha-inputs" maxlength="1" data-index="2" name="d2">
      </div>
      <button class="audio-captcha-submit-button">Verify</button>
    </div>
  </div>
</div>
<form id="human-contact-form" method="post" action="/contact-human">
  <input type="hidden" name="captcha_hash" value="SECRETHASH">
  <input type="hidden" name="captcha_request_id" value="SECRETID">
  <textarea name="human-comment"></textarea>
</form>
</body></html>
"""


class SliderDiscoveryTest(unittest.TestCase):
    """The handle must be found, and its neighbours must not be mistaken for it."""

    def setUp(self) -> None:
        self.surface = collect(
            DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/"
        )
        self.drags = [a for a in self.surface.actions if a.kind == "drag"]

    def test_exactly_one_handle_is_offered(self) -> None:
        self.assertEqual(
            len(self.drags),
            1,
            f"expected one slider handle, got {[d.target for d in self.drags]}",
        )

    def test_handle_selector_resolves_to_the_movable_element(self) -> None:
        """The selector must land on `.slider`, not on a substring match.

        Asserting on the string invites the same mistake the code made: `target`
        legitimately contains "sliderContainer" as an ancestor. Resolve it and
        check the element the engine would actually hit.
        """
        target = self.drags[0].target
        node = _resolve_last(target)
        self.assertIsNotNone(node, f"selector does not resolve: {target}")
        self.assertEqual(
            (node.attributes.get("class") or "").lower(),
            "slider",
            f"handle selector resolves to the wrong element: {target}",
        )

    def test_selector_classes_are_escaped_for_the_engine(self) -> None:
        """Utility classes contain ':' and '[' and must not break selectors."""
        html = (
            '<html><body><div class="row">'
            '<button class="lg:tw-flex[2]">Go</button>'
            "</div></body></html>"
        )
        surface = collect(html, "https://x.test/")
        target = next(a.target for a in surface.actions if a.kind == "activate")
        self.assertIn("\\:", target)
        self.assertIn("\\[", target)
        # '-document' is not valid CSS and must never reach a selector.
        self.assertNotIn("-document", target)
        # selectolax cannot parse escaped class names, so verify the escaping
        # directly and confirm the raw classes are the ones being escaped.
        final = _unescape(target.split(">")[-1].strip())
        self.assertIn("lg:tw-flex[2]", final)

    def test_drop_target_is_offered_as_a_goal_not_a_handle(self) -> None:
        dropzones = [a for a in self.surface.actions if a.kind == "dropzone"]
        self.assertEqual(len(dropzones), 1)
        drop = _resolve_last(dropzones[0].target)
        self.assertIsNotNone(drop, dropzones[0].target)
        self.assertEqual(
            (drop.attributes.get("class") or "").lower(), "slidertarget"
        )
        self.assertIn("target", dropzones[0].label.lower())

    def test_handle_gets_a_label(self) -> None:
        self.assertEqual(self.drags[0].label, "drag handle")

    def test_media_track_is_not_a_handle(self) -> None:
        targets = [d.target for d in self.drags]
        self.assertFalse([t for t in targets if "audio" in t])

    def test_repeated_inputs_get_distinct_refs(self) -> None:
        digits = [
            a
            for a in self.surface.actions
            if a.tag == "input" and a.label.startswith("d")
        ]
        self.assertEqual(len(digits), 3)
        self.assertEqual(
            len({a.ref for a in digits}),
            3,
            "identical inputs must not collapse onto one ref",
        )

    def test_hidden_token_values_are_not_exposed_as_actions(self) -> None:
        for action in self.surface.actions:
            self.assertNotIn("SECRETHASH", action.label)
            self.assertNotIn("SECRETID", action.label)


class CoverageAccountingTest(unittest.TestCase):
    """The surface must admit what it hides, instead of truncating silently."""

    def test_candidate_set_is_fully_converted(self) -> None:
        surface = collect(
            DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/",
            max_actions=10_000,
        )
        # Every candidate becomes an action, except controls deliberately
        # excluded because they are not agent actions: the form itself, and
        # hidden inputs that carry submission tokens.
        hidden = 2
        self.assertEqual(len(surface.actions), surface.total_found - hidden)
        self.assertTrue(surface.complete)
        self.assertIn("complete", surface.coverage_line())

    def test_hidden_inputs_are_excluded_from_the_action_surface(self) -> None:
        surface = collect(DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/")
        tags = {a.tag for a in surface.actions if a.kind in {"activate", "submit"}}
        names = {a.label for a in surface.actions}
        self.assertNotIn("captcha_hash", names)
        self.assertNotIn("captcha_request_id", names)

    def test_budget_is_disclosed_when_it_hides_controls(self) -> None:
        surface = collect(
            DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/",
            max_actions=3,
        )
        self.assertEqual(len(surface.actions), 3)
        self.assertGreater(surface.truncated, 0)
        self.assertFalse(surface.complete)
        line = surface.coverage_line()
        self.assertIn("hidden by budget", line)

    def test_engine_only_controls_are_labelled_as_such(self) -> None:
        surface = collect(
            DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/"
        )
        self.assertGreater(surface.engine_only, 0)
        drags = [a for a in surface.actions if a.kind == "drag"]
        self.assertTrue(all(not a.callable_without_engine for a in drags))
        self.assertTrue(all(a.notes == "needs real engine" for a in drags))

    def test_navigation_controls_stay_callable_without_an_engine(self) -> None:
        html = '<html><body><nav><a href="/a">A</a></nav></body></html>'
        surface = collect(html, "https://x.test/")
        self.assertEqual(len(surface.actions), 1)
        self.assertTrue(surface.actions[0].callable_without_engine)


class RealPageCoverageTest(unittest.TestCase):
    """Measured against captured markup from a live Nuxt marketing page."""

    FIXTURE = REPO / "testhtml" / "test1.html"

    def setUp(self) -> None:
        if not self.FIXTURE.exists():
            self.skipTest("testhtml/test1.html fixture not present")
        self.raw = self.FIXTURE.read_text(encoding="utf-8", errors="replace")
        self.surface = collect(
            self.raw, "https://www.design.com/website-builder", max_actions=100_000
        )

    def test_link_count_is_preserved_not_capped(self) -> None:
        import re

        raw_links = len(re.findall(r"<a\b", self.raw, re.I))
        navigable = [
            a for a in self.surface.actions if a.kind == "navigate"
        ]
        # One <a> per href is the floor; the old path exposed only 47 of 183
        # unique links because snapshot() capped the list at 50 in silence.
        self.assertEqual(
            len(navigable),
            raw_links,
            "every anchor must appear in the action surface",
        )

    def test_buttons_outside_forms_are_reachable(self) -> None:
        # 14 <button> elements, only 2 of them inside a <form>. The other 12 are
        # reachable only because the action surface does not apply the reading
        # filter, which treats nav/header/footer as chrome.
        labels = {a.label for a in self.surface.actions}
        self.assertIn("Mobile Navigation Button", labels)
        # Count by tag, not by role: role reflects semantics the author chose,
        # tag reflects what the document actually contains.
        self.assertEqual(
            len([a for a in self.surface.actions if a.tag == "button"]), 14
        )

    def test_form_controls_are_routed_to_their_form(self) -> None:
        submits = [a for a in self.surface.actions if a.kind == "submit"]
        self.assertTrue(submits)
        self.assertTrue(all(a.target.startswith("form[") for a in submits))
        # The hero search box and its submit button both belong to form 1.
        form_one = [a for a in submits if a.target == "form[1]"]
        self.assertTrue(form_one)


class HiddenFieldHandlingTest(unittest.TestCase):
    """Token values stay out of the model's view but still reach the server."""

    def _doc(self, body: str):
        from webresearch.parse import parse

        return parse(
            FetchResult(
                url="https://x.test/p",
                final_url="https://x.test/p",
                status=200,
                content_type="text/html",
                kind="html",
                body=f"<html><body>{body}</body></html>",
            )
        )

    def test_hidden_value_is_blank_in_the_view(self) -> None:
        from webresearch.forms import forms

        form = forms(self._doc(
            '<form method="post" action="/p">'
            '<input type="hidden" name="csrf" value="TOKEN123">'
            '<input name="email" type="email">'
            "</form>"
        ))[0]
        csrf = next(f for f in form.fields if f.name == "csrf")
        self.assertEqual(csrf.value, "")
        self.assertEqual(csrf.type, "hidden")

    def test_hidden_value_is_available_at_submit_time(self) -> None:
        from webresearch.forms import hidden_values

        live = hidden_values(self._doc(
            '<form method="post" action="/p">'
            '<input type="hidden" name="csrf" value="TOKEN123">'
            "</form>"
        ), 1)
        self.assertEqual(live["csrf"], "TOKEN123")

    def test_visible_fields_keep_their_values(self) -> None:
        from webresearch.forms import forms

        form = forms(self._doc(
            '<form method="get" action="/s">'
            '<input name="q" value="hello">'
            "</form>"
        ))[0]
        self.assertEqual(form.fields[0].value, "hello")


class StableRefTest(unittest.TestCase):
    def test_refs_survive_reordering_of_unrelated_elements(self) -> None:
        base = '<html><body><a href="/a">Alpha</a><button>Beta</button></body></html>'
        shifted = (
            '<html><body><nav><a href="/nav">Nav</a></nav>'
            '<a href="/a">Alpha</a><button>Beta</button></body></html>'
        )
        before = {a.label: a.ref for a in collect(base, "https://x.test/").actions}
        after = {a.label: a.ref for a in collect(shifted, "https://x.test/").actions}
        self.assertEqual(set(before), {"Alpha", "Beta"})
        for label in ("Alpha", "Beta"):
            self.assertEqual(before[label], after[label], f"{label} ref moved")

    def test_refs_are_unique_within_a_page(self) -> None:
        surface = collect(DATADOME_FIXTURE, "https://geo.captcha-delivery.com/captcha/")
        refs = [a.ref for a in surface.actions]
        self.assertEqual(len(refs), len(set(refs)))


class NativeActuationTest(unittest.TestCase):
    """Controls must be actuatable in the mini-DOM, with no external engine.

    Chromium is out of scope for this engine, so a control the agent can see has
    to be a control the agent can move. These tests pin the three routes that
    have to work natively: activate, type, and drag.
    """

    FIXTURE = """<html><body>
<button id="go">Go</button>
<div id="out">idle</div>
<form method="get" action="/s"><input name="q" id="q" type="text" placeholder="Search"></form>
<div class="sliderContainer">
  <div class="slider" id="handle"></div>
  <div class="sliderTarget" id="tgt"></div>
</div>
<div id="draglog">no-drag</div>
<script>
// Bound with addEventListener, not an inline onclick attribute: the mini-DOM
// runs listeners but does not wire `onclick=` attributes, and a test written
// against the inline form would pass while proving nothing.
document.getElementById('go').addEventListener('click', function () {
  document.getElementById('out').textContent = 'clicked';
});
document.getElementById('handle').addEventListener('mousedown', function () {
  document.getElementById('draglog').textContent = 'down';
});
document.getElementById('handle').addEventListener('mousemove', function (e) {
  document.getElementById('draglog').textContent = 'move:' + (e.clientX || 0);
});
document.getElementById('handle').addEventListener('mouseup', function (e) {
  document.getElementById('draglog').textContent = 'up:' + (e.clientX || 0);
});
</script>
</body></html>"""

    @staticmethod
    def _text_of(html: str, element_id: str) -> str:
        """Read an element's text out of serialized HTML.

        Matching on the element's own content rather than searching the whole
        document: an earlier version of this test asserted 'clicked' in html,
        which was already true because the string appeared in the source, so
        the assertion passed whether or not the handler ever ran.
        """
        import re

        match = re.search(rf'id="{element_id}"[^>]*>([^<]*)<', html)
        return match.group(1) if match else ""

    def _run(self, actions):
        import asyncio

        import httpx

        from webresearch.engine.runtime import run_scripts

        async def go():
            async with httpx.AsyncClient() as client:
                return await run_scripts(
                    self.FIXTURE, "https://x.test/p", client, force=True,
                    actions=actions,
                )

        return asyncio.run(go())

    def test_button_click_reaches_its_handler(self):
        surface = collect(self.FIXTURE, "https://x.test/p")
        go = next(a for a in surface.actions if a.label == "Go")
        html, info = self._run([{"kind": "activate", "selector": go.selector}])
        self.assertTrue(info["actions_applied"][0]["ok"])
        self.assertEqual(self._text_of(html, "out"), "clicked")

    def test_checkbox_click_sets_the_dom_property_not_just_the_attribute(self):
        """Scripts read el.checked; markup reads the attribute. Set both."""
        fixture = (
            '<html><body><input type="checkbox" id="c">'
            '<div id="s">idle</div>'
            "<script>document.getElementById('c').addEventListener('click',"
            "function(){document.getElementById('s').textContent="
            "document.getElementById('c').checked?'on':'off';});</script>"
            "</body></html>"
        )
        import asyncio

        import httpx

        from webresearch.engine.runtime import run_scripts

        async def go():
            async with httpx.AsyncClient() as client:
                return await run_scripts(
                    fixture, "https://x.test/p", client, force=True,
                    actions=[{"kind": "activate", "selector": "#c"}],
                )

        html, info = asyncio.run(go())
        self.assertTrue(info["actions_applied"][0]["ok"])
        self.assertEqual(self._text_of(html, "s"), "on")
        self.assertIn("checked", html)

    def test_typing_reaches_the_field(self):
        surface = collect(self.FIXTURE, "https://x.test/p")
        field = next(a for a in surface.actions if a.role == "textbox")
        html, info = self._run(
            [{"kind": "type", "selector": field.selector, "text": "hello"}]
        )
        self.assertTrue(info["actions_applied"][0]["ok"])
        self.assertIn('value="hello"', html)

    def test_drag_emits_a_press_move_release_sequence_with_coordinates(self):
        """A click is not a drag. The whole move chain must arrive."""
        surface = collect(self.FIXTURE, "https://x.test/p")
        handle = next(a for a in surface.actions if a.kind == "drag")
        html, info = self._run(
            [{"kind": "drag", "selector": handle.selector, "dx": 222, "dy": 0}]
        )
        self.assertTrue(info["actions_applied"][0]["ok"], info["actions_applied"])
        # clientX must survive Event construction, otherwise the release lands
        # at (0, 0) and a real slider computes a zero-length move.
        self.assertEqual(self._text_of(html, "draglog"), "up:222")

    def test_form_field_carries_both_a_wire_name_and_a_selector(self):
        surface = collect(self.FIXTURE, "https://x.test/p")
        field = next(a for a in surface.actions if a.role == "textbox")
        self.assertEqual(field.field_name, "q")
        self.assertEqual(field.target, "form[1]")
        self.assertEqual(field.selector, "#q")


class VisibleTextBeatsMachineIdsTest(unittest.TestCase):
    def test_button_text_wins_over_its_id(self):
        html = '<html><body><button id="go">Go</button></body></html>'
        action = collect(html, "https://x.test/").actions[0]
        self.assertEqual(action.label, "Go")

    def test_visible_text_wins_over_a_value_attribute(self):
        html = '<html><body><button value="btn_submit">Save changes</button></body></html>'
        action = collect(html, "https://x.test/").actions[0]
        self.assertEqual(action.label, "Save changes")


if __name__ == "__main__":
    unittest.main()