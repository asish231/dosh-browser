"""Cookie Consent and GDPR Banner Auto-Dismisser.

Detects and dismisses intrusive cookie consent modals (OneTrust, Cookiebot,
Klaro, TrustArc, Didomi, Quantcast, Google Consent) so they do not block
the agent's view, interfere with element clicks, or lock scrolling.
"""

from __future__ import annotations

import re
from typing import Any

from selectolax.lexbor import LexborHTMLParser

CONSENT_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
    ".cc-accept",
    ".cc-allow",
    ".cookie-accept-all",
    ".accept-cookies-button",
    "button[id*='accept-cookie']",
    "button[id*='cookie-accept']",
    "button[id*='consent-accept']",
    "button[class*='cookie-accept']",
    "button[class*='consent-accept']",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Agree' i]",
    "button[aria-label*='Allow all' i]",
]

CONSENT_CONTAINER_SELECTORS = [
    "#onetrust-consent-sdk",
    "#onetrust-banner-sdk",
    ".cookie-banner",
    ".cookie-consent",
    ".cookie-modal",
    ".consent-banner",
    ".fc-consent-root",
    "#cmpbox",
    "#didomi-host",
]

_TEXT_PATTERNS = [
    re.compile(r"^(accept all|accept cookies|allow all|allow cookies|i agree|agree & close|agree)$", re.I),
]


def has_consent_dialog(html: str) -> bool:
    """Quick check if a consent banner is likely present."""
    low = html.lower()
    return any(
        kw in low
        for kw in (
            "onetrust",
            "cookie-consent",
            "cookie-banner",
            "cookies-banner",
            "cookie consent",
            "didomi",
            "quantcast",
            "fc-consent",
        )
    )


def dismiss_consent_dom(html: str) -> tuple[str, bool]:
    """Strip known consent dialog containers from the DOM and restore scrolling."""
    if not has_consent_dialog(html):
        return html, False

    tree = LexborHTMLParser(html)
    dismissed = False

    for sel in CONSENT_CONTAINER_SELECTORS:
        for node in tree.css(sel):
            node.decompose()
            dismissed = True

    # Check for buttons with explicit text like 'Accept all' in any top-level fixed/dialog div
    for btn in tree.css("button, a.btn, input[type='button'], input[type='submit']"):
        txt = (btn.text() or "").strip()
        val = btn.attributes.get("value", "").strip() if btn.attributes else ""
        if any(p.match(txt) or p.match(val) for p in _TEXT_PATTERNS):
            parent = btn.parent
            # If the button is inside a banner/modal/fixed container, drop the container
            depth = 0
            while parent is not None and depth < 4:
                tag = (parent.tag or "").lower()
                attrs = parent.attributes or {}
                cls = attrs.get("class", "").lower()
                pid = attrs.get("id", "").lower()
                if "cookie" in cls or "cookie" in pid or "consent" in cls or "consent" in pid or tag == "dialog":
                    parent.decompose()
                    dismissed = True
                    break
                parent = parent.parent
                depth += 1

    if dismissed:
        # Clear overflow: hidden from body and html styles if locked by the modal
        body = tree.css_first("body")
        if body is not None and body.attributes:
            style = body.attributes.get("style", "")
            if "overflow" in style:
                clean_style = re.sub(r"overflow\s*:\s*hidden;?", "", style, flags=re.I)
                body.attrs["style"] = clean_style.strip()

        return tree.html, True

    return html, False
