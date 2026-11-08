use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use regex::Regex;
use url::Url;

use crate::frames::FrameDocument;
use crate::types::CaptchaReport;

pub fn varint(mut val: u64, out: &mut Vec<u8>) {
    loop {
        let b = (val & 0x7f) as u8;
        val >>= 7;
        if val == 0 {
            out.push(b);
            break;
        } else {
            out.push(b | 0x80);
        }
    }
}

pub fn protobuf_encode(fields: &[(u32, &[u8])]) -> Vec<u8> {
    let mut out = Vec::new();
    for &(field_num, bytes) in fields {
        let tag = (field_num << 3) | 2; // wire type 2 = length-delimited
        varint(tag as u64, &mut out);
        varint(bytes.len() as u64, &mut out);
        out.extend_from_slice(bytes);
    }
    out
}

pub fn rc_base64(s: &str) -> String {
    let b64 = URL_SAFE_NO_PAD.encode(s.as_bytes());
    format!("{}.", b64)
}

pub fn inspect_captcha(
    html: &str,
    parent_url: &str,
    frames: &[FrameDocument],
) -> CaptchaReport {
    let mut report = CaptchaReport::default();
    let lower_html = html.to_lowercase();

    // Check frames first
    for f in frames {
        let low_f = f.url.to_lowercase();
        if low_f.contains("recaptcha/api2/anchor") || low_f.contains("recaptcha/enterprise/anchor") {
            report.present = true;
            report.kind = "recaptcha".to_string();

            if let Ok(u) = Url::parse(&f.url) {
                for (k, v) in u.query_pairs() {
                    if k == "k" {
                        report.site_key = v.to_string();
                    }
                }
            }
            break;
        } else if low_f.contains("hcaptcha.com") {
            report.present = true;
            report.kind = "hcaptcha".to_string();
            break;
        } else if low_f.contains("challenges.cloudflare.com") {
            report.present = true;
            report.kind = "turnstile".to_string();
            break;
        }
    }

    // Check main HTML if not found in frames
    if !report.present {
        let re_key = Regex::new(r#"data-sitekey=["']([^"']+)["']"#).unwrap();
        let site_key = re_key
            .captures(html)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_default();

        if lower_html.contains("g-recaptcha") || lower_html.contains("recaptcha") {
            if !site_key.is_empty() {
                report.site_key = site_key;
                report.present = true;
                report.kind = "recaptcha".to_string();
            }
        } else if lower_html.contains("h-captcha") || lower_html.contains("hcaptcha") {
            report.present = true;
            report.kind = "hcaptcha".to_string();
            report.site_key = site_key;
        } else if lower_html.contains("cf-turnstile")
            || lower_html.contains("challenges.cloudflare.com")
            || lower_html.contains("_cf_chl_opt")
        {
            report.present = true;
            report.kind = "turnstile".to_string();
            report.site_key = site_key;
        } else if lower_html.contains("captcha-delivery.com")
            || lower_html.contains("data-dd-captcha-container")
            || lower_html.contains("dd-response-page")
        {
            report.present = true;
            let is_hard_block = lower_html.contains("dd-response-page--hard-block")
                || lower_html.contains("'t':'bv'")
                || lower_html.contains("'t': 'bv'");
            report.kind = if is_hard_block {
                "datadome:hard-block".to_string()
            } else {
                "datadome:captcha".to_string()
            };
            let re_cid = Regex::new(r#"['"]?cid['"]?\s*:\s*['"]([^'"]+)['"]"#).unwrap();
            if let Some(c) = re_cid.captures(html).and_then(|c| c.get(1)) {
                report.site_key = c.as_str().to_string();
            }
        }
    }

    if report.present {
        report.view = format!(
            "CAPTCHA: {} (inspection only)\nSITE_KEY: {}\nNOTE: detected on {}",
            report.kind, report.site_key, parent_url
        );
    }

    report
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_varint_small_values() {
        let mut out = Vec::new();
        varint(0, &mut out);
        varint(1, &mut out);
        varint(127, &mut out);
        assert_eq!(out, vec![0x00, 0x01, 0x7f]);
    }

    #[test]
    fn test_varint_multibyte() {
        let mut out = Vec::new();
        varint(128, &mut out);
        assert_eq!(out, vec![0x80, 0x01]);
    }

    #[test]
    fn test_protobuf_reload_body_shape() {
        let body = protobuf_encode(&[
            (1, b"v1.2.3"),
            (2, b"token-abc"),
            (6, b"fi"),
            (14, b"6Lsitekey"),
        ]);
        assert_eq!(
            body,
            b"\x0a\x06v1.2.3\x12\x09token-abc\x32\x02fi\x72\x096Lsitekey"
        );
    }

    #[test]
    fn test_rc_base64() {
        let out = rc_base64("https://x:1");
        assert!(!out.contains('='));
        assert!(out.ends_with('.'));
    }

    #[test]
    fn test_inspect_captcha_recaptcha() {
        let html = r#"
            <html><body>
                <div class="g-recaptcha" data-sitekey="6Ltestkey123"></div>
            </body></html>
        "#;
        let rep = inspect_captcha(html, "https://example.com/login", &[]);
        assert!(rep.present);
        assert_eq!(rep.kind, "recaptcha");
        assert_eq!(rep.site_key, "6Ltestkey123");
        assert!(rep.view.contains("SITE_KEY: 6Ltestkey123"));
    }
}
