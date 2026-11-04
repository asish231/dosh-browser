//! `video.rs` — Video downloads (replaces `webresearch/video.py`).
//!
//! Two lanes: direct media files stream via the transport; pages/HLS/DASH
//! extract via yt-dlp subprocess. Policy is enforced before any network work:
//! DRM subscription services and known piracy mirrors are refused.
//! Absent yt-dlp is reported, never faked.

use reqwest::Client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::PathBuf;
use std::process::Stdio;
use tokio::process::Command;
use url::Url;

/// DRM subscription platforms. Downloading their streams requires a licensed
/// content module (Widevine-class); that is a permanent "no", stated honestly.
const DRM_SERVICES: &[&str] = &[
    "netflix.com", "primevideo.com", "hotstar.com", "disneyplus.com",
    "hulu.com", "peacocktv.com", "max.com", "hbomax.com",
    "appletv.com", "spotify.com", "tidal.com", "deezer.com", "qobuz.com",
];

/// Known piracy mirrors (unlicensed restreams). Known entries only.
const PIRACY_MIRRORS: &[&str] = &[
    "netmirror.to", "netmirror.site", "netmirror.cc", "netmirror.live",
];

const DIRECT_MEDIA_EXTS: &[&str] = &[
    ".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".flv", ".ogv",
    ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac",
];

const DEFAULT_FORMAT: &str = "bv*+ba/b";
const DEFAULT_MAX_BYTES: u64 = 500 * 1024 * 1024;
const DEFAULT_TIMEOUT: u64 = 600;

#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct VideoDownload {
    pub url: String,
    pub page_url: String,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub title: String,
    pub extractor: String,
    pub format: String,
    pub duration: f64,
    pub content_type: String,
    pub lane: String,
    pub policy: String,
    pub policy_reason: String,
    pub ytdlp_version: String,
    pub ffmpeg_available: bool,
    pub verified: bool,
    pub scanned: bool,
    pub error: String,
    pub stderr_tail: String,
}

/// Check policy before any network work.
pub fn check_policy(url: &str) -> (String, String) {
    let parsed = match Url::parse(url) {
        Ok(u) => u,
        Err(_) => return ("refused".into(), "url is not parseable".into()),
    };
    if !["http", "https"].contains(&parsed.scheme()) {
        return ("refused".into(), format!("scheme {} is not http/https", parsed.scheme()));
    }
    let host = parsed.host_str().unwrap_or("").to_lowercase();
    if let Some(hit) = host_in(&host, DRM_SERVICES) {
        return ("refused".into(),
            format!("DRM-protected platform ({hit}); needs a licensed content module — not supported"));
    }
    if let Some(hit) = host_in(&host, PIRACY_MIRRORS) {
        return ("refused".into(),
            format!("known piracy mirror ({hit}); legitimate platforms only"));
    }
    ("allowed".into(), "".into())
}

fn host_in<'a>(host: &str, domains: &'a [&str]) -> Option<&'a str> {
    for &d in domains {
        if host == d || host.ends_with(&format!(".{}", d)) {
            return Some(d);
        }
    }
    None
}

pub fn is_direct_media(url: &str) -> bool {
    if let Ok(parsed) = Url::parse(url) {
        let path = parsed.path().to_lowercase();
        DIRECT_MEDIA_EXTS.iter().any(|ext| path.ends_with(ext))
    } else {
        false
    }
}

/// Check if yt-dlp is available.
pub fn ytdlp_version() -> String {
    match std::process::Command::new("yt-dlp")
        .arg("--version")
        .output()
    {
        Ok(out) => String::from_utf8_lossy(&out.stdout).trim().to_string(),
        Err(_) => String::new(),
    }
}

/// Check if ffmpeg is available.
pub fn ffmpeg_available() -> bool {
    std::process::Command::new("ffmpeg")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Download a video. Two lanes: direct media or yt-dlp.
pub async fn get_video(
    client: &Client,
    url: &str,
    dest_dir: Option<&PathBuf>,
    format: Option<&str>,
    _use_session: bool,
    max_bytes: Option<u64>,
    timeout: Option<u64>,
) -> VideoDownload {
    let mut rec = VideoDownload {
        url: url.to_string(),
        format: format.unwrap_or(DEFAULT_FORMAT).to_string(),
        ffmpeg_available: ffmpeg_available(),
        ..Default::default()
    };

    let (policy, reason) = check_policy(url);
    rec.policy = policy;
    rec.policy_reason = reason;
    if rec.policy != "allowed" {
        rec.error = format!("policy: {}", rec.policy_reason);
        return rec;
    }

    let max_bytes = max_bytes.unwrap_or(DEFAULT_MAX_BYTES);
    let timeout = timeout.unwrap_or(DEFAULT_TIMEOUT);

    if is_direct_media(url) {
        rec.lane = "direct".to_string();
        // Reuse download module
        let got = crate::download::download(client, url, None, dest_dir, max_bytes).await;
        rec.path = got.path;
        rec.bytes = got.bytes;
        rec.sha256 = got.sha256;
        rec.content_type = got.content_type;
        rec.verified = got.verified;
        rec.error = got.error.clone();
        if !got.error.is_empty() {
            rec.path = String::new();
        }
        return rec;
    }

    rec.lane = "ytdlp".to_string();
    rec.ytdlp_version = ytdlp_version();
    if rec.ytdlp_version.is_empty() {
        rec.error = "yt-dlp is not installed; install via pip or your package manager".to_string();
        return rec;
    }

    let tmp_dir = match tempfile::tempdir() {
        Ok(d) => d,
        Err(e) => {
            rec.error = format!("temp dir: {e}");
            return rec;
        }
    };
    let out_tpl = tmp_dir.path().join("%(title).190B.%(ext)s").to_string_lossy().to_string();
    let max_bytes_str = max_bytes.to_string();
    let argv = vec![
        "yt-dlp", "--no-playlist", "--no-simulate", "--no-progress", "--no-warnings",
        "--retries", "3", "--socket-timeout", "30",
        "--max-filesize", max_bytes_str.as_str(),
        "-f", rec.format.as_str(),
        "-o", out_tpl.as_str(),
        "--print", "after_move:%(title)j",
        "--print", "after_move:%(extractor)j",
        "--print", "after_move:%(duration)j",
        "--print", "after_move:%(webpage_url)j",
        "--print", "after_move:filepath",
        "--", url,
    ];

    // yt-dlp doesn't support --cookies with Netscape in this simple form
    // for now we skip cookie export (Phase 2: add Netscape cookie export)

    let mut cmd = Command::new("yt-dlp");
    cmd.args(argv)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // With timeout
    let output = match tokio::time::timeout(
        std::time::Duration::from_secs(timeout),
        cmd.output()
    ).await {
        Ok(Ok(out)) => out,
        Ok(Err(e)) => {
            rec.error = format!("yt-dlp failed: {e}");
            return rec;
        }
        Err(_) => {
            rec.error = format!("yt-dlp exceeded {timeout}s timeout");
            rec.stderr_tail = "killed after timeout".to_string();
            return rec;
        }
    };

    let err_text = String::from_utf8_lossy(&output.stderr);
    rec.stderr_tail = err_text.chars().rev().take(400).collect::<String>().chars().rev().collect::<String>();
    
    if !output.status.success() {
        rec.error = format!(
            "yt-dlp exit {}: {}",
            output.status.code().unwrap_or(-1),
            err_text.trim().chars().rev().take(300).collect::<String>().chars().rev().collect::<String>()
        );
        return rec;
    }

    let lines: Vec<String> = String::from_utf8_lossy(&output.stdout)
        .lines()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
        .collect();

    let meta = meta_from_lines(&lines);
    let produced = meta.get("filepath").cloned().unwrap_or_default();
    let produced_path = PathBuf::from(&produced);

    if produced.is_empty() || !produced_path.exists() {
        rec.error = if produced.is_empty() {
            "yt-dlp exited 0 but produced no file".to_string()
        } else {
            format!("yt-dlp reported path that does not exist: {produced}")
        };
        return rec;
    }

    rec.title = meta.get("title").cloned().unwrap_or_default();
    rec.extractor = meta.get("extractor").cloned().unwrap_or_default();
    rec.duration = meta.get("duration")
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    rec.page_url = meta.get("page_url").cloned().unwrap_or_default();

    // Hash the produced file
    match hash_file(&produced_path, max_bytes as usize) {
        Ok((total, digest, err)) => {
            rec.path = produced;
            rec.bytes = total;
            rec.sha256 = digest;
            if !err.is_empty() {
                rec.error = err;
                let _ = std::fs::remove_file(&produced_path);
                rec.path = String::new();
                return rec;
            }
            rec.verified = true;
        }
        Err(e) => {
            rec.error = format!("hash failed: {e}");
            let _ = std::fs::remove_file(&produced_path);
            return rec;
        }
    }
    rec
}

fn meta_from_lines(lines: &[String]) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    // Order: title, extractor, duration, webpage_url, filepath
    let keys = ["title", "extractor", "duration", "page_url"];
    if lines.len() >= 5 {
        for (i, key) in keys.iter().enumerate() {
            // Duration arrives as a bare JSON number; others are JSON strings.
            let val = match serde_json::from_str::<serde_json::Value>(&lines[i]) {
                Ok(serde_json::Value::String(s)) => s,
                Ok(v) => v.to_string(),
                Err(_) => String::new(),
            };
            meta.insert(key.to_string(), val);
        }
        meta.insert("filepath".to_string(), lines[4].clone());
    } else if !lines.is_empty() {
        meta.insert("filepath".to_string(), lines.last().cloned().unwrap_or_default());
    }
    meta
}

#[allow(dead_code)]
fn parse_json_field(line: &str) -> Option<String> {
    // yt-dlp with %j flag outputs JSON-encoded strings
    if let Ok(val) = serde_json::from_str::<String>(line) {
        Some(val)
    } else {
        None
    }
}

fn hash_file(path: &PathBuf, max_bytes: usize) -> Result<(u64, String, String), Box<dyn std::error::Error>> {
    let mut f = std::fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 1024 * 1024];
    let mut total: u64 = 0;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 { break; }
        total += n as u64;
        if total > max_bytes as u64 {
            return Ok((total, String::new(), format!("exceeds {max_bytes} bytes")));
        }
        hasher.update(&buf[..n]);
    }
    Ok((total, hex::encode(hasher.finalize()), String::new()))
}

use std::collections::HashMap;

/// The wire shape every surface (MCP/HTTP/shell) returns.
pub fn video_view(rec: &VideoDownload) -> serde_json::Value {
    serde_json::to_value(rec).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_check_policy_allowed() {
        let (p, _) = check_policy("https://www.youtube.com/watch?v=abc");
        assert_eq!(p, "allowed");
    }

    #[test]
    fn test_check_policy_drm() {
        let (p, r) = check_policy("https://netflix.com/title/123");
        assert_eq!(p, "refused");
        assert!(r.contains("DRM"));
    }

    #[test]
    fn test_check_policy_piracy() {
        let (p, _) = check_policy("https://netmirror.to/movie");
        assert_eq!(p, "refused");
    }

    #[test]
    fn test_is_direct_media() {
        assert!(is_direct_media("https://example.com/video.mp4"));
        assert!(is_direct_media("https://cdn.example.com/clip.m4a"));
        assert!(!is_direct_media("https://youtube.com/watch?v=abc"));
        assert!(!is_direct_media("https://example.com/page.html"));
    }

    #[test]
    fn test_meta_from_lines() {
        let lines = vec![
            r#""Test Video""#.into(),
            r#""youtube""#.into(),
            "120.5".into(),
            r#""https://youtube.com/watch?v=abc""#.into(),
            "/tmp/Test_Video.mp4".into(),
        ];
        let meta = meta_from_lines(&lines);
        assert_eq!(meta["title"], "Test Video");
        assert_eq!(meta["extractor"], "youtube");
        assert!((meta["duration"].parse::<f64>().unwrap() - 120.5).abs() < 0.01);
    }

    #[test]
    fn test_parse_json_field() {
        assert_eq!(parse_json_field(r#""hello""#), Some("hello".into()));
        assert!(parse_json_field(r#"hello"#).is_none());
    }
}
