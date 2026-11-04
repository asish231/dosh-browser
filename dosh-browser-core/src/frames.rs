use reqwest::Client;
use serde::{Deserialize, Serialize};
use url::Url;

use crate::tree::collect_iframes;
use crate::types::FrameInfo;

pub const MAX_FRAMES: usize = 16;
pub const MAX_FRAME_DEPTH: usize = 3;
pub const MAX_EXCERPT: usize = 4000;

const CHALLENGE_NEEDLES: &[&str] = &[
    "recaptcha",
    "hcaptcha",
    "h-captcha",
    "turnstile",
    "cf-challenge",
    "datadome",
];

/// Extract normalized scheme://host[:port] origin from a URL.
pub fn extract_origin(raw_url: &str) -> String {
    if let Ok(parsed) = Url::parse(raw_url) {
        let scheme = parsed.scheme().to_lowercase();
        if scheme.is_empty() {
            return String::new();
        }
        let Some(host) = parsed.host_str() else {
            return String::new();
        };
        let host = host.to_lowercase();
        let port = parsed.port();
        let port = match (scheme.as_str(), port) {
            ("http", Some(80)) => None,
            ("https", Some(443)) => None,
            (_, p) => p,
        };
        if let Some(p) = port {
            format!("{}://{}:{}", scheme, host, p)
        } else {
            format!("{}://{}", scheme, host)
        }
    } else {
        String::new()
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrameDocument {
    pub ref_id: String,
    pub parent_ref: String,
    pub url: String,
    pub title: String,
    pub name: String,
    pub depth: usize,
    pub status: u16,
    pub content_type: String,
    pub html: String,
    pub error: String,
    pub origin_url: String,
    pub seed_src: String,
    pub seed_srcdoc: String,
    pub delivered_count: usize,
}

impl Default for FrameDocument {
    fn default() -> Self {
        Self {
            ref_id: String::new(),
            parent_ref: String::new(),
            url: String::new(),
            title: String::new(),
            name: String::new(),
            depth: 1,
            status: 0,
            content_type: String::new(),
            html: String::new(),
            error: String::new(),
            origin_url: String::new(),
            seed_src: String::new(),
            seed_srcdoc: String::new(),
            delivered_count: 0,
        }
    }
}

impl FrameDocument {
    pub fn is_challenge(&self) -> bool {
        let blob = format!("{} {} {}", self.url, self.title, self.html).to_lowercase();
        CHALLENGE_NEEDLES.iter().any(|&n| blob.contains(n))
    }

    pub fn to_info(&self) -> FrameInfo {
        let excerpt = self
            .html
            .split_whitespace()
            .collect::<Vec<&str>>()
            .join(" ");
        let excerpt = if excerpt.len() > MAX_EXCERPT {
            excerpt[..MAX_EXCERPT].to_string()
        } else {
            excerpt
        };

        FrameInfo {
            ref_id: self.ref_id.clone(),
            url: self.url.clone(),
            title: self.title.clone(),
            name: self.name.clone(),
            status: self.status as i64,
            depth: self.depth as i64,
            content_type: self.content_type.clone(),
            challenge: self.is_challenge(),
            error: self.error.clone(),
            html_excerpt: excerpt,
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FrameSeed {
    pub parent_ref: String,
    pub parent_url: String,
    pub depth: usize,
    pub src: String,
    pub srcdoc: String,
    pub title: String,
    pub name: String,
}

pub fn discover_seeds(
    html: &str,
    parent_url: &str,
    parent_ref: &str,
    depth: usize,
) -> Vec<FrameSeed> {
    let iframes = collect_iframes(html);
    iframes
        .into_iter()
        .map(|item| FrameSeed {
            parent_ref: parent_ref.to_string(),
            parent_url: parent_url.to_string(),
            depth,
            src: item.src,
            srcdoc: item.srcdoc,
            title: item.title,
            name: item.name,
        })
        .collect()
}

/// Fetch and load frame documents for all discovered frame seeds up to max_frames limit.
pub async fn load_frames(
    client: &Client,
    seeds: &[FrameSeed],
    max_frames: usize,
) -> Vec<FrameDocument> {
    let mut docs = Vec::new();
    let limit = seeds.len().min(max_frames).min(MAX_FRAMES);

    for (i, seed) in seeds.iter().take(limit).enumerate() {
        let ref_id = format!("frame-{}", i + 1);
        if !seed.srcdoc.is_empty() {
            docs.push(FrameDocument {
                ref_id,
                parent_ref: seed.parent_ref.clone(),
                url: format!("about:srcdoc#{}", i + 1),
                title: seed.title.clone(),
                name: seed.name.clone(),
                depth: seed.depth,
                status: 200,
                content_type: "text/html".to_string(),
                html: seed.srcdoc.clone(),
                error: String::new(),
                origin_url: seed.parent_url.clone(),
                seed_src: seed.src.clone(),
                seed_srcdoc: seed.srcdoc.clone(),
                delivered_count: 0,
            });
            continue;
        }

        if seed.src.is_empty() || seed.src.starts_with("javascript:") {
            continue;
        }

        let resolved_url = match Url::parse(&seed.parent_url) {
            Ok(base) => base.join(&seed.src).map(|u| u.to_string()).unwrap_or_else(|_| seed.src.clone()),
            Err(_) => seed.src.clone(),
        };

        match crate::fetch::fetch(client, &resolved_url, "GET", None, "{}", 4_000_000).await {
            Ok(res) => {
                docs.push(FrameDocument {
                    ref_id,
                    parent_ref: seed.parent_ref.clone(),
                    url: res.final_url,
                    title: seed.title.clone(),
                    name: seed.name.clone(),
                    depth: seed.depth,
                    status: res.status,
                    content_type: res.content_type,
                    html: res.body,
                    error: String::new(),
                    origin_url: resolved_url,
                    seed_src: seed.src.clone(),
                    seed_srcdoc: String::new(),
                    delivered_count: 0,
                });
            }
            Err(e) => {
                docs.push(FrameDocument {
                    ref_id,
                    parent_ref: seed.parent_ref.clone(),
                    url: resolved_url.clone(),
                    title: seed.title.clone(),
                    name: seed.name.clone(),
                    depth: seed.depth,
                    status: 0,
                    content_type: String::new(),
                    html: String::new(),
                    error: e.to_string(),
                    origin_url: resolved_url,
                    seed_src: seed.src.clone(),
                    seed_srcdoc: String::new(),
                    delivered_count: 0,
                });
            }
        }
    }

    docs
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_origin() {
        assert_eq!(extract_origin("https://example.com/page?x=1"), "https://example.com");
        assert_eq!(extract_origin("http://example.com:8080/foo"), "http://example.com:8080");
        assert_eq!(extract_origin("https://example.com:443/bar"), "https://example.com");
        assert_eq!(extract_origin("http://example.com:80/bar"), "http://example.com");
        assert_eq!(extract_origin("not a url"), "");
    }

    #[test]
    fn test_discover_seeds() {
        let html = r#"
            <html><body>
                <iframe src="/child1" title="Child One" name="frame1"></iframe>
                <iframe srcdoc="<p>Inline child</p>" name="frame2"></iframe>
            </body></html>
        "#;
        let seeds = discover_seeds(html, "https://parent.test", "", 1);
        assert_eq!(seeds.len(), 2);
        assert_eq!(seeds[0].src, "/child1");
        assert_eq!(seeds[0].title, "Child One");
        assert_eq!(seeds[1].srcdoc, "<p>Inline child</p>");
        assert_eq!(seeds[1].name, "frame2");
    }

    #[test]
    fn test_frame_document_challenge_detection() {
        let mut doc = FrameDocument::default();
        doc.url = "https://www.google.com/recaptcha/api2/anchor".to_string();
        assert!(doc.is_challenge());

        doc.url = "https://example.com".to_string();
        assert!(!doc.is_challenge());
        doc.html = "<div>Checking cf-challenge tokens</div>".to_string();
        assert!(doc.is_challenge());
    }
}
