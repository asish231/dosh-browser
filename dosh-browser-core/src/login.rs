use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};
use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::types::Target;

pub const LOGIN_URL_PATTERNS: &[&str] = &[
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
];

pub const OTP_NAME_HINTS: &[&str] = &[
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
];

pub const OTP_TEXT_HINTS: &[&str] = &[
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
];

pub const LOCK_TEXT_HINTS: &[&str] = &[
    "account locked",
    "account has been locked",
    "temporarily locked",
    "too many attempts",
    "unusual activity",
    "verify your identity to continue",
    "account restricted",
];

pub const HUMAN_ONLY_TEXT_HINTS: &[&str] = &[
    "voice call",
    "phone call to verify",
    "call your phone",
    "upload a photo of",
    "video selfie",
    "government id",
    "verify with a document",
];

pub const LOGGED_IN_URL_PATTERNS: &[&str] = &[
    "/logout",
    "/signout",
    "/sign-out",
    "/account/logout",
];

pub const LOGGED_IN_TEXT_HINTS: &[&str] = &[
    "log out",
    "sign out",
    "signed in as",
    "welcome back",
    "my account",
    "your account",
];

pub const AUTH_COOKIE_HINTS: &[(&str, &[&str])] = &[
    ("linkedin.com", &["li_at"]),
    ("x.com", &["auth_token"]),
    ("twitter.com", &["auth_token"]),
    ("github.com", &["user_session", "logged_in"]),
    ("facebook.com", &["c_user"]),
    ("instagram.com", &["sessionid"]),
    ("reddit.com", &["reddit_session", "token_v2"]),
];

pub const MAX_OTP_ATTEMPTS: usize = 3;
pub const OTP_CHECKPOINT_TTL_SECONDS: u64 = 600;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoginWall {
    pub kind: String, // "form" | "otp" | "human_only" | "unknown"
    pub url: String,
    pub username_ref: String,
    pub password_ref: String,
    pub submit_ref: String,
    pub username_name: String,
    pub password_name: String,
    pub post_login_url: String,
    pub confidence: f64,
    pub detail: String,
    pub refs: Vec<String>,
    pub expected_length: Option<usize>,
    pub split_boxes: bool,
}

impl Default for LoginWall {
    fn default() -> Self {
        Self {
            kind: "form".to_string(),
            url: String::new(),
            username_ref: String::new(),
            password_ref: String::new(),
            submit_ref: String::new(),
            username_name: String::new(),
            password_name: String::new(),
            post_login_url: String::new(),
            confidence: 0.0,
            detail: String::new(),
            refs: Vec::new(),
            expected_length: None,
            split_boxes: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OtpCheckpoint {
    pub id: String,
    pub url: String,
    pub refs: Vec<String>,
    pub expected_length: Option<usize>,
    pub split_boxes: bool,
    pub submit_ref: String,
    pub sent_via: String,
    pub created_at: u64,
    pub attempts: usize,
}

impl OtpCheckpoint {
    pub fn new(id: String, url: String) -> Self {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        Self {
            id,
            url,
            refs: Vec::new(),
            expected_length: None,
            split_boxes: false,
            submit_ref: String::new(),
            sent_via: String::new(),
            created_at: now,
            attempts: 0,
        }
    }

    pub fn attempts_left(&self) -> usize {
        MAX_OTP_ATTEMPTS.saturating_sub(self.attempts)
    }

    pub fn expired(&self) -> bool {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        (now.saturating_sub(self.created_at)) > OTP_CHECKPOINT_TTL_SECONDS
    }
}

fn low(s: &str) -> String {
    s.trim().to_lowercase()
}

fn url_matches(url: &str, patterns: &[&str]) -> bool {
    let l = url.to_lowercase();
    patterns.iter().any(|&p| l.contains(p))
}

fn target_blob(target: &Target) -> String {
    format!(
        "{} {} {} {}",
        low(&target.name),
        low(&target.text),
        low(&target.role),
        low(&target.tag)
    )
}

pub fn is_username_target(target: &Target) -> bool {
    let r = target.role.as_str();
    if r != "textbox" && r != "email" && r != "searchbox" {
        return false;
    }
    if target.tag != "input" {
        return false;
    }
    let blob = target_blob(target);
    let hints = ["user", "email", "phone", "login", "identifier", "account", "session_key", "username"];
    hints.iter().any(|&h| blob.contains(h))
}

pub fn is_password_target(target: &Target) -> bool {
    target.role == "password" || target_blob(target).contains("password")
}

pub fn detect_login_wall(
    _html: &str,
    url: &str,
    targets: &[Target],
    status: u16,
) -> Option<LoginWall> {
    let password_targets: Vec<&Target> = targets.iter().filter(|t| is_password_target(t)).collect();
    if password_targets.is_empty() {
        return None;
    }

    let first_pwd = password_targets[0];
    let usernames: Vec<&Target> = targets
        .iter()
        .filter(|t| is_username_target(t) && t.ref_id != first_pwd.ref_id)
        .collect();

    let username = usernames.first().copied();
    let password = first_pwd;

    let mut submit = targets
        .iter()
        .find(|t| t.role == "button" && t.tag == "button");
    if submit.is_none() {
        submit = targets.iter().find(|t| t.role == "button");
    }

    let url_signal = url_matches(url, LOGIN_URL_PATTERNS);
    let has_both = username.is_some();

    if !has_both && !url_signal {
        return None;
    }

    let mut confidence: f64 = if !has_both {
        0.6
    } else if url_signal {
        0.95
    } else {
        0.7
    };

    let detail = if !has_both {
        "password field without a clear username input; URL suggests a login page".to_string()
    } else if url_signal {
        "username + password form on a login URL".to_string()
    } else {
        "username + password form on a non-login URL".to_string()
    };

    if status == 401 || status == 403 {
        confidence = (confidence + 0.1f64).min(1.0f64);
    }

    Some(LoginWall {
        kind: "form".to_string(),
        url: url.to_string(),
        username_ref: username.map(|u| u.ref_id.clone()).unwrap_or_default(),
        password_ref: password.ref_id.clone(),
        submit_ref: submit.map(|s| s.ref_id.clone()).unwrap_or_default(),
        username_name: username.map(|u| u.name.clone()).unwrap_or_default(),
        password_name: password.name.clone(),
        post_login_url: String::new(),
        confidence: (confidence * 100.0f64).round() / 100.0f64,
        detail,
        refs: Vec::new(),
        expected_length: None,
        split_boxes: false,
    })
}

pub fn detect_otp_page(
    html: &str,
    url: &str,
    targets: &[Target],
) -> Option<LoginWall> {
    let tag_re = Regex::new(r"<[^>]+>").unwrap();
    let text = tag_re.replace_all(html, " ");
    let text_low = low(&text);
    let html_low = low(html);

    let text_hit = OTP_TEXT_HINTS.iter().any(|&h| text_low.contains(h));
    let lock_hit = LOCK_TEXT_HINTS.iter().any(|&h| text_low.contains(h));

    if lock_hit && !text_hit {
        return Some(LoginWall {
            kind: "unknown".to_string(),
            url: url.to_string(),
            confidence: 0.7,
            detail: "account lock/challenge text present; may require human action".to_string(),
            ..Default::default()
        });
    }

    if !text_hit && !url_matches(url, &["/challenge", "/verify", "/otp", "/2fa", "/checkpoint"]) {
        return None;
    }

    let human_only = HUMAN_ONLY_TEXT_HINTS.iter().any(|&h| text_low.contains(h));
    if human_only {
        return Some(LoginWall {
            kind: "human_only".to_string(),
            url: url.to_string(),
            confidence: 0.85,
            detail: "page requires voice call / document upload: human must complete this outside the browser".to_string(),
            ..Default::default()
        });
    }

    let digit_cell_re = Regex::new(r"digit|code|otp|char|box|cell").unwrap();
    let maxlength1_re = Regex::new(r#"maxlength[="'\s]*1\b"#).unwrap();

    let boxes: Vec<&Target> = targets
        .iter()
        .filter(|t| {
            t.tag == "input"
                && (t.role == "textbox" || t.role == "password")
                && (digit_cell_re.is_match(&low(&t.name)) || maxlength1_re.is_match(&t.text))
        })
        .collect();

    let split = if boxes.len() >= 4 { Some(boxes) } else { None };

    let code_fields: Vec<&Target> = targets
        .iter()
        .filter(|t| {
            if t.tag != "input" {
                return false;
            }
            let blob_no_space = target_blob(t).replace(' ', "");
            OTP_NAME_HINTS.iter().any(|&h| blob_no_space.contains(h))
                || (html_low.contains("one-time-code") && (t.role == "textbox" || t.role == "password"))
                || target_blob(t).contains("numeric")
        })
        .collect();

    let mut expected_length: Option<usize> = None;
    let maxlen_re = Regex::new(r#"maxlength[="'\s]*([2-8])\b"#).unwrap();
    if let Some(caps) = maxlen_re.captures(&html_low) {
        if let Some(m) = caps.get(1) {
            expected_length = m.as_str().parse().ok();
        }
    }
    if expected_length.is_none() {
        let digit_words_re = Regex::new(r"\b(\d)[- ]?digit\b|\b(\d)\s*[- ]?digits\b").unwrap();
        if let Some(caps) = digit_words_re.captures(&text_low) {
            for i in 1..=2 {
                if let Some(m) = caps.get(i) {
                    expected_length = m.as_str().parse().ok();
                    if expected_length.is_some() {
                        break;
                    }
                }
            }
        }
    }

    if let Some(split_boxes) = split {
        let count = split_boxes.len();
        return Some(LoginWall {
            kind: "otp".to_string(),
            url: url.to_string(),
            confidence: 0.9,
            detail: format!("split code input: {} one-char boxes", count),
            refs: split_boxes.iter().map(|t| t.ref_id.clone()).collect(),
            expected_length: Some(count),
            split_boxes: true,
            ..Default::default()
        });
    }

    if let Some(first) = code_fields.first() {
        return Some(LoginWall {
            kind: "otp".to_string(),
            url: url.to_string(),
            confidence: 0.85,
            detail: "code input detected (name/attribute hints)".to_string(),
            refs: vec![first.ref_id.clone()],
            expected_length,
            ..Default::default()
        });
    }

    None
}

pub fn verify_logged_in(
    html: &str,
    url: &str,
    pre_url: &str,
    client_cookies: Option<&HashMap<String, String>>,
    status: u16,
) -> &'static str {
    let tag_re = Regex::new(r"<[^>]+>").unwrap();
    let text = tag_re.replace_all(html, " ");
    let text_low = low(&text);
    let html_low = low(html);

    let has_password_field = html_low.contains(r#"type="password""#) || html_low.contains("type='password'");

    if HUMAN_ONLY_TEXT_HINTS.iter().any(|&h| text_low.contains(h)) {
        return "human_only";
    }
    if LOCK_TEXT_HINTS.iter().any(|&h| text_low.contains(h)) {
        return "account_locked";
    }

    if !has_password_field {
        let otp_signal = OTP_TEXT_HINTS.iter().any(|&h| text_low.contains(h))
            || url_matches(url, &["/challenge", "/verify", "/otp", "/2fa", "/mfa"]);
        let code_input = OTP_NAME_HINTS.iter().any(|&h| html_low.replace(' ', "").contains(h));
        if otp_signal && (code_input || html_low.contains("one-time-code")) {
            return "otp_required";
        }
    }

    let logged_text = LOGGED_IN_TEXT_HINTS.iter().any(|&h| text_low.contains(h));
    let logout_link = LOGGED_IN_URL_PATTERNS.iter().any(|&p| html_low.contains(p));

    let mut cookie_hint = false;
    let empty_map = HashMap::new();
    let cookies = client_cookies.unwrap_or(&empty_map);

    let url_l = url.to_lowercase();
    let pre_url_l = pre_url.to_lowercase();
    for &(domain, names) in AUTH_COOKIE_HINTS {
        if url_l.contains(domain) || pre_url_l.contains(domain) {
            for &name in names {
                if let Some(val) = cookies.get(name) {
                    if !val.is_empty() {
                        cookie_hint = true;
                        break;
                    }
                }
            }
            if cookie_hint {
                break;
            }
        }
    }

    let generic_session_cookie = cookies.keys().any(|k| {
        let kl = k.to_lowercase();
        kl == "session" || kl == "sessionid" || kl == "sid" || kl == "auth" || kl == "token" || kl == "jwt"
    });

    if logout_link && !has_password_field {
        return "verified";
    }
    if logged_text && !has_password_field {
        return "verified";
    }
    if cookie_hint && !has_password_field {
        return "verified";
    }
    if generic_session_cookie && (logged_text || logout_link) {
        return "verified";
    }

    if has_password_field {
        return "failed";
    }
    if status == 401 || status == 403 {
        return "failed";
    }

    "unknown"
}

pub fn summarize_wall(wall: Option<&LoginWall>) -> String {
    let Some(wall) = wall else {
        return String::new();
    };
    match wall.kind.as_str() {
        "form" => {
            let mut parts = vec![format!(
                "LOGIN WALL: kind=form url={} confidence={}",
                wall.url, wall.confidence
            )];
            if !wall.username_ref.is_empty() {
                parts.push(format!("username=[{}]", wall.username_ref));
            }
            if !wall.password_ref.is_empty() {
                parts.push(format!("password=[{}]", wall.password_ref));
            }
            if !wall.submit_ref.is_empty() {
                parts.push(format!("submit=[{}]", wall.submit_ref));
            }
            parts.push("— ask the user for credentials; call browser_login(url, username, password). Never invent credentials.".to_string());
            parts.join(" ")
        }
        "otp" => {
            format!(
                "OTP PAGE: url={} — a code is required; the human receives it. Use browser_submit_otp after asking the user; never guess the code.",
                wall.url
            )
        }
        "human_only" => format!("LOGIN HUMAN-ONLY: url={} — {}", wall.url, wall.detail),
        _ => format!("LOGIN CHALLENGE: url={} — {}", wall.url, wall.detail),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::actions::extract_actions;

    const LOGIN_PAGE: &str = r#"<!DOCTYPE html><html><head><title>Sign in</title></head><body>
<h1>Sign in</h1>
<form action="/checkpoint/lg/login-submit" method="POST">
  <input id="username" name="session_key" type="text" placeholder="Email or Phone" />
  <input id="password" name="session_password" type="password" placeholder="Password" />
  <button id="login-btn" type="submit">Sign in</button>
</form>
<a href="/uas/request-password-reset">Forgot password?</a>
<script>var a = 1;</script>
</body></html>"#;

    const OTP_PAGE: &str = r#"<!DOCTYPE html><html><body>
<h2>Verify your identity</h2>
<p>we sent a code to your phone •••42</p>
<form action="/checkpoint/otp-submit" method="POST">
  <input name="otp" type="text" maxlength="6" inputmode="numeric" autocomplete="one-time-code" />
  <button type="submit">Verify</button>
</form>
</body></html>"#;

    const SPLIT_OTP_PAGE: &str = r#"<!DOCTYPE html><html><body>
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
</body></html>"#;

    const FEED_PAGE: &str = r#"<!DOCTYPE html><html><body>
<h1>Welcome back, Asish</h1>
<nav><a href="/logout">Log out</a></nav>
</body></html>"#;

    #[test]
    fn test_login_wall_detection() {
        let targets = extract_actions(LOGIN_PAGE, "https://www.linkedin.com/login", 50);
        let wall = detect_login_wall(LOGIN_PAGE, "https://www.linkedin.com/login", &targets, 200);
        assert!(wall.is_some());
        let w = wall.unwrap();
        assert_eq!(w.kind, "form");
        assert!(w.confidence >= 0.9);
        assert!(!w.username_ref.is_empty());
        assert!(!w.password_ref.is_empty());
        assert_eq!(w.password_name, "session_password");
        assert_eq!(w.username_name, "session_key");
    }

    #[test]
    fn test_otp_single_detection() {
        let targets = extract_actions(OTP_PAGE, "https://www.linkedin.com/checkpoint/challenge", 50);
        let wall = detect_otp_page(OTP_PAGE, "https://www.linkedin.com/checkpoint/challenge", &targets);
        assert!(wall.is_some());
        let w = wall.unwrap();
        assert_eq!(w.kind, "otp");
        assert_eq!(w.refs.len(), 1);
        assert_eq!(w.expected_length, Some(6));
        assert!(!w.split_boxes);
    }

    #[test]
    fn test_otp_split_detection() {
        let targets = extract_actions(SPLIT_OTP_PAGE, "https://www.instagram.com/challenge", 50);
        let wall = detect_otp_page(SPLIT_OTP_PAGE, "https://www.instagram.com/challenge", &targets);
        assert!(wall.is_some());
        let w = wall.unwrap();
        assert_eq!(w.kind, "otp");
        assert_eq!(w.refs.len(), 6);
        assert_eq!(w.expected_length, Some(6));
        assert!(w.split_boxes);
    }

    #[test]
    fn test_verify_logged_in_states() {
        assert_eq!(verify_logged_in(LOGIN_PAGE, "https://example.com/login", "", None, 200), "failed");
        assert_eq!(verify_logged_in(FEED_PAGE, "https://example.com/feed", "", None, 200), "verified");
        
        let mut cookies = HashMap::new();
        cookies.insert("li_at".to_string(), "valid_token".to_string());
        assert_eq!(verify_logged_in("<html><body><h1>Hello</h1></body></html>", "https://linkedin.com/in/test", "", Some(&cookies), 200), "verified");
    }
}
