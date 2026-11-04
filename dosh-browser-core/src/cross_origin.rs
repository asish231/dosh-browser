//! Cross-origin isolation enforcement: CORP, COEP, COOP notes.
//! Direct port of `webresearch/engine/cross_origin.py`.

use std::collections::HashMap;
use url::Url;
use crate::trackers::registrable_domain;

fn origin(u: &str) -> Option<(String, String, u16)> {
    let parsed = Url::parse(u).ok()?;
    let scheme = parsed.scheme().to_lowercase();
    let host = parsed.host_str()?.to_lowercase();
    let port = parsed.port_or_known_default().unwrap_or(if scheme == "https" { 443 } else { 80 });
    Some((scheme, host, port))
}

pub fn same_origin(a: &str, b: &str) -> bool {
    match (origin(a), origin(b)) {
        (Some(oa), Some(ob)) => oa == ob,
        _ => false,
    }
}

pub fn same_site(a: &str, b: &str) -> bool {
    match (origin(a), origin(b)) {
        (Some(oa), Some(ob)) => {
            oa.0 == ob.0 && registrable_domain(&oa.1) == registrable_domain(&ob.1)
        }
        _ => false,
    }
}

pub fn corp_decision(resp_headers: &HashMap<String, String>, resource_url: &str, requester_url: &str) -> Option<String> {
    let lowered: HashMap<String, String> = resp_headers
        .iter()
        .map(|(k, v)| (k.to_lowercase(), v.clone()))
        .collect();

    let corp_raw = lowered.get("cross-origin-resource-policy").map(|s| s.as_str()).unwrap_or("");
    let corp = corp_raw.split(';').next().unwrap_or("").trim().to_lowercase();

    if corp.is_empty() || corp == "cross-origin" || corp == "unsafe-none" {
        return None;
    }

    if corp == "same-origin" {
        if !same_origin(resource_url, requester_url) {
            return Some("blocked by Cross-Origin-Resource-Policy: same-origin".to_string());
        }
        return None;
    }

    if corp == "same-site" {
        if !same_site(resource_url, requester_url) {
            return Some("blocked by Cross-Origin-Resource-Policy: same-site".to_string());
        }
        return None;
    }

    None
}

pub fn coep_requires_corp(page_headers: Option<&HashMap<String, String>>) -> bool {
    let headers = match page_headers {
        Some(h) => h,
        None => return false,
    };
    for (k, v) in headers {
        if k.eq_ignore_ascii_case("cross-origin-embedder-policy") {
            let policy = v.split(';').next().unwrap_or("").trim().to_lowercase();
            return policy == "require-corp";
        }
    }
    false
}

pub fn coep_decision(
    resource_resp_headers: &HashMap<String, String>,
    resource_url: &str,
    requester_url: &str,
    page_headers: Option<&HashMap<String, String>>,
) -> Option<String> {
    if !coep_requires_corp(page_headers) {
        return None;
    }
    if same_origin(resource_url, requester_url) {
        return None;
    }

    let lowered: HashMap<String, String> = resource_resp_headers
        .iter()
        .map(|(k, v)| (k.to_lowercase(), v.clone()))
        .collect();

    if lowered.contains_key("cross-origin-resource-policy") {
        return None;
    }

    Some("blocked by COEP require-corp: cross-origin subresource without CORP".to_string())
}

pub fn coop_notes(resp_headers: &HashMap<String, String>) -> Vec<String> {
    let mut notes = Vec::new();
    for (k, v) in resp_headers {
        let kl = k.to_lowercase();
        if kl == "cross-origin-opener-policy" {
            let val = v.split(';').next().unwrap_or("").trim();
            notes.push(format!("coop:{}", val));
        } else if kl == "cross-origin-opener-policy-report-only" {
            let val = v.split(';').next().unwrap_or("").trim();
            notes.push(format!("coop-report-only:{}", val));
        }
    }
    notes
}

pub fn check_subresource(
    resource_resp_headers: &HashMap<String, String>,
    resource_url: &str,
    requester_url: &str,
    page_headers: Option<&HashMap<String, String>>,
) -> (Option<String>, Vec<String>) {
    let mut reason = corp_decision(resource_resp_headers, resource_url, requester_url);
    if reason.is_none() {
        reason = coep_decision(resource_resp_headers, resource_url, requester_url, page_headers);
    }
    let notes = coop_notes(resource_resp_headers);
    (reason, notes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_corp_same_origin() {
        let mut hdrs = HashMap::new();
        hdrs.insert("Cross-Origin-Resource-Policy".to_string(), "same-origin".to_string());

        let res = corp_decision(&hdrs, "https://a.com/script.js", "https://b.com/");
        assert!(res.is_some());
        assert!(res.unwrap().contains("same-origin"));

        let res2 = corp_decision(&hdrs, "https://a.com/script.js", "https://a.com/page");
        assert!(res2.is_none());
    }

    #[test]
    fn test_coep_require_corp() {
        let mut page_hdrs = HashMap::new();
        page_hdrs.insert("Cross-Origin-Embedder-Policy".to_string(), "require-corp".to_string());

        let res_hdrs = HashMap::new();
        let decision = coep_decision(&res_hdrs, "https://cdn.other.com/asset.png", "https://my-app.com/", Some(&page_hdrs));
        assert!(decision.is_some());
        assert!(decision.unwrap().contains("require-corp"));
    }
}

