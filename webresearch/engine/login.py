"""Login walls, OTP/2FA checkpoints, and logged-in verification.

The pattern follows DIALOGS and CAPTCHA OBJECT: detect, surface as a
first-class object, never silently fail, never fake a verdict. Secrets
(passwords, codes) live in memory only — never persisted, never echoed back.

Detection is heuristic and honest: `confidence` says how sure we are, and
`human_only` verdicts name dead ends (voice-call OTP, account locks) instead
of pretending the engine handled them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from webresearch.types import Target

LOGIN_URL_PATTERNS = (
    "/login",
    "/signin",
    "/sign-in",
    "/sign_in",
    "/u/login",
    "/uas/login",
    "/accounts/login",
    "/auth/login",
    "/session/new",
    "/authorize",
    "/checkpoint",
)

OTP_NAME_HINTS = (
    "otp",
    "one-time",
    "onetime",
    "verifycode",
    "verificationcode",
    "verification_code",
    "securitycode",
    "security_code",
    "twofactor",
    "two_factor",
    "2fa",
    "mfa",
    "totp",
    "challenge",
    "confirmationcode",
    "pin",
    "code",
)

OTP_TEXT_HINTS = (
    "enter the code",
    "enter code",
    "verification code",
    "verify your identity",
    "two-factor",
    "2fa",
    "we sent",
    "sent a code",
    "code we sent",
    "check your phone",
    "check your email",
    "security challenge",
    "confirm your identity",
    "authenticate",
)

LOCK_TEXT_HINTS = (
    "account locked",
    "account has been locked",
    "temporarily locked",
    "too many attempts",
    "unusual activity",
    "verify your identity to continue",
    "account restricted",
)

HUMAN_ONLY_TEXT_HINTS = (
    "voice call",
    "phone call to verify",
    "call your phone",
    "upload a photo of",
    "video selfie",
    "government id",
    "verify with a document",
)

LOGGED_IN_URL_PATTERNS = (
    "/logout",
    "/signout",
    "/sign-out",
    "/account/logout",
)

LOGGED_IN_TEXT_HINTS = (
    "log out",
    "sign out",
    "signed in as",
    "welcome back",
    "my account",
    "your account",
)

# Sites where a known cookie is strong evidence of a live session.
AUTH_COOKIE_HINTS: dict[str, tuple[str, ...]] = {
    "linkedin.com": ("li_at",),
    "x.com": ("auth_token",),
    "twitter.com": ("auth_token",),
    "github.com": ("user_session", "logged_in"),
    "facebook.com": ("c_user",),
    "instagram.com": ("sessionid",),
    "reddit.com": ("reddit_session", "token_v2"),
}

MAX_OTP_ATTEMPTS = 3
OTP_CHECKPOINT_TTL_SECONDS = 600


@dataclass
class LoginWall:
    kind: str = "form"  # form | otp | human_only | unknown
    url: str = ""
    username_ref: str = ""
    password_ref: str = ""
    submit_ref: str = ""
    username_name: str = ""
    password_name: str = ""
    post_login_url: str = ""
    confidence: float = 0.0
    detail: str = ""
    # OTP variant: the code input refs (all boxes for split inputs).
    refs: list[str] = field(default_factory=list)
    expected_length: int | None = None
    split_boxes: bool = False


@dataclass
class OtpCheckpoint:
    id: str
    url: str = ""
    refs: list[str] = field(default_factory=list)
    expected_length: int | None = None
    split_boxes: bool = False
    submit_ref: str = ""
    sent_via: str = ""
    created_at: float = field(default_factory=time.time)
    attempts: int = 0

    @property
    def attempts_left(self) -> int:
        return max(0, MAX_OTP_ATTEMPTS - self.attempts)

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > OTP_CHECKPOINT_TTL_SECONDS


def _low(s: str) -> str:
    return (s or "").strip().lower()


def _url_matches(url: str, patterns: tuple[str, ...]) -> bool:
    low = (url or "").lower()
    return any(p in low for p in patterns)


def _target_blob(target: Target) -> str:
    return " ".join(
        [
            _low(target.name),
            _low(target.text),
            _low(target.role),
            _low(target.tag),
        ]
    )


def is_username_target(target: Target) -> bool:
    if target.role not in {"textbox", "email", "searchbox"} or target.tag != "input":
        return False
    blob = _target_blob(target)
    hints = ("user", "email", "phone", "login", "identifier", "account", "session_key", "username")
    return any(h in blob for h in hints)


def is_password_target(target: Target) -> bool:
    return target.role == "password" or "password" in _target_blob(target)


def _otp_length_from(target: Target) -> int | None:
    m = re.search(r"maxlength[=\"'\s]*(\d)", target.text or "")
    if m:
        return int(m.group(1))
    return None


def detect_login_wall(
    html: str,
    url: str,
    targets: list[Target],
    status: int = 200,
) -> LoginWall | None:
    """Heuristic login-wall detector.

    Looks for a password input plus a username-ish input and a submit control,
    backed by URL signals. A lone password field (no username) still counts,
    with lower confidence. Returns None when the page is not a login wall.
    """
    password_targets = [t for t in targets if is_password_target(t)]
    if not password_targets:
        return None

    usernames = [t for t in targets if is_username_target(t) and t.ref != (password_targets[0].ref if password_targets else "")]
    username = usernames[0] if usernames else None
    password = password_targets[0]

    # Submit control: a button near the form, or any button at all.
    submit = next(
        (t for t in targets if t.role == "button" and t.tag == "button"),
        None,
    )
    if submit is None:
        submit = next((t for t in targets if t.role == "button"), None)

    url_signal = _url_matches(url, LOGIN_URL_PATTERNS)
    has_both = username is not None and password is not None

    if not has_both and not url_signal:
        # Password field but no username and no URL signal: too weak.
        return None

    if not has_both:
        confidence = 0.6
        detail = "password field without a clear username input; URL suggests a login page"
    elif url_signal:
        confidence = 0.95
        detail = "username + password form on a login URL"
    else:
        confidence = 0.7
        detail = "username + password form on a non-login URL"

    if status in (401, 403):
        confidence = min(1.0, confidence + 0.1)

    return LoginWall(
        kind="form",
        url=url,
        username_ref=username.ref if username else "",
        password_ref=password.ref,
        submit_ref=submit.ref if submit else "",
        username_name=username.name if username else "",
        password_name=password.name,
        confidence=round(confidence, 2),
        detail=detail,
    )


def detect_otp_page(
    html: str,
    url: str,
    targets: list[Target],
) -> LoginWall | None:
    """Detect an OTP/verification page and locate the code inputs.

    Handles both single inputs (maxlength/inputmode/autocomplete hints) and
    the split one-digit-per-box cluster. Returns a LoginWall with kind='otp'
    carrying the code-field refs.
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    text_low = _low(text)
    html_low = _low(html or "")

    text_hit = any(h in text_low for h in OTP_TEXT_HINTS)
    lock_hit = any(h in text_low for h in LOCK_TEXT_HINTS)
    if lock_hit and not text_hit:
        return LoginWall(kind="unknown", url=url, confidence=0.7, detail="account lock/challenge text present; may require human action")
    if not text_hit and not _url_matches(url, ("/challenge", "/verify", "/otp", "/2fa", "/checkpoint")):
        return None

    human_only = any(h in text_low for h in HUMAN_ONLY_TEXT_HINTS)
    if human_only:
        return LoginWall(
            kind="human_only",
            url=url,
            confidence=0.85,
            detail="page requires voice call / document upload: human must complete this outside the browser",
        )

    if "voice call" in text_low or "phone call" in text_low:
        pass  # voice call listed as an option, not a requirement — continue

    # Split boxes: a cluster of single-char inputs whose names/ids hint at code/digit.
    boxes = [
        t
        for t in targets
        if t.tag == "input"
        and t.role in {"textbox", "password"}
        and (
            re.search(r"digit|code|otp|char|box|cell", _low(t.name))
            or re.search(r"maxlength[=\"'\s]*1\b", t.text or "")
        )
    ]
    # Heuristic cluster: at least 4 boxes and every box maxlength 1
    split = None
    if len(boxes) >= 4:
        split = boxes

    code_fields = [
        t
        for t in targets
        if t.tag == "input"
        and (
            any(h in _target_blob(t).replace(" ", "") for h in OTP_NAME_HINTS)
            or ("one-time-code" in html_low and t.role in {"textbox", "password"})
            or "numeric" in _target_blob(t)
        )
    ]

    expected_length: int | None = None
    m = re.search(r"maxlength[=\"'\s]*([2-8])\b", html_low)
    if m:
        expected_length = int(m.group(1))
    digit_words = re.findall(r"\b(\d)[- ]?digit\b|\b(\d)\s*[- ]?digits\b", text_low)
    if not expected_length and digit_words:
        expected_length = int(next(g for g in digit_words[0] if g))

    if split:
        return LoginWall(
            kind="otp",
            url=url,
            confidence=0.9,
            detail=f"split code input: {len(split)} one-char boxes",
            refs=[t.ref for t in split],
            expected_length=len(split),
            split_boxes=True,
        )
    if code_fields:
        first = code_fields[0]
        return LoginWall(
            kind="otp",
            url=url,
            confidence=0.85,
            detail="code input detected (name/attribute hints)",
            refs=[first.ref],
            expected_length=expected_length,
        )
    return None


def verify_logged_in(
    html: str,
    url: str,
    pre_url: str = "",
    client_cookies: dict[str, str] | None = None,
    status: int = 200,
) -> str:
    """Return a verdict string for the state after a login/OTP submit.

    verdicts: verified | failed | otp_required | account_locked | human_only | unknown
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    text_low = _low(text)
    html_low = _low(html or "")

    targets_blob = html_low
    has_password_field = 'type="password"' in html_low or "type='password'" in html_low

    # Human-only first: never pretend we can pass these.
    if any(h in text_low for h in HUMAN_ONLY_TEXT_HINTS):
        return "human_only"
    if any(h in text_low for h in LOCK_TEXT_HINTS):
        return "account_locked"

    # A page still showing a password field is the LOGIN FORM, never an OTP
    # checkpoint — no matter what OTP-ish text its scripts contain (a real
    # false-positive: LinkedIn's login page mentions verification in bundle
    # scripts, and the verdict said otp_required while we were still on the
    # sign-in page with an inert button).
    if not has_password_field:
        otp_signal = any(h in text_low for h in OTP_TEXT_HINTS) or _url_matches(url, ("/challenge", "/verify", "/otp", "/2fa", "/mfa"))
        code_input = any(h in targets_blob.replace(" ", "") for h in OTP_NAME_HINTS)
        if otp_signal and (code_input or "one-time-code" in html_low):
            return "otp_required"

    # Logged-in markers
    logged_text = any(h in text_low for h in LOGGED_IN_TEXT_HINTS)
    logout_link = any(p in targets_blob for p in LOGGED_IN_URL_PATTERNS)

    # Known auth cookies (strongest signal when present).
    cookie_hint = ""
    cookies = client_cookies or {}
    for domain, names in AUTH_COOKIE_HINTS.items():
        if domain in (url or "").lower() or domain in (pre_url or "").lower():
            for name in names:
                if name in cookies and cookies.get(name):
                    cookie_hint = name
                    break
            if cookie_hint:
                break
    generic_session_cookie = any(
        name.lower() in {"session", "sessionid", "sid", "auth", "token", "jwt"}
        for name in cookies
    )

    if logout_link and not has_password_field:
        return "verified"
    if logged_text and not has_password_field:
        return "verified"
    if cookie_hint and not has_password_field:
        return "verified"
    if generic_session_cookie and (logged_text or logout_link):
        return "verified"

    if has_password_field:
        # Back on (or still on) a login form: credentials did not take.
        return "failed"
    if status in (401, 403):
        return "failed"
    return "unknown"


def summarize_wall(wall: LoginWall | None) -> str:
    """Human/agent-readable one-liner for the view."""
    if wall is None:
        return ""
    if wall.kind == "form":
        parts = [f"LOGIN WALL: kind=form url={wall.url} confidence={wall.confidence}"]
        if wall.username_ref:
            parts.append(f"username=[{wall.username_ref}]")
        if wall.password_ref:
            parts.append(f"password=[{wall.password_ref}]")
        if wall.submit_ref:
            parts.append(f"submit=[{wall.submit_ref}]")
        parts.append(
            "— ask the user for credentials; call browser_login(url, username, password). Never invent credentials."
        )
        return " ".join(parts)
    if wall.kind == "otp":
        return (
            f"OTP PAGE: url={wall.url} — a code is required; the human receives it. "
            "Use browser_submit_otp after asking the user; never guess the code."
        )
    if wall.kind == "human_only":
        return f"LOGIN HUMAN-ONLY: url={wall.url} — {wall.detail}"
    return f"LOGIN CHALLENGE: url={wall.url} — {wall.detail}"
