//! `download.rs` — Native downloads (replaces `webresearch/download.py`).
//!
//! Streams responses to disk with SHA-256 hashing and optional hash
//! verification. No malware verdict engine — that absence is reported,
//! not hidden (matching the Python contract).

use reqwest::Client;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::PathBuf;
use tokio::fs::File;
use tokio::io::{AsyncWriteExt, AsyncReadExt};

/// The download record, matching the Python `Download` dataclass.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct Download {
    pub url: String,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub content_type: String,
    pub expected: String,
    pub verified: bool,
    pub error: String,
    pub scanned: bool,
}

/// The background batch record, matching the Python `BackgroundFetchRecord`.
#[derive(Serialize, Deserialize, Clone, Default, Debug)]
pub struct BackgroundFetchRecord {
    pub id: String,
    pub urls: Vec<String>,
    pub downloads: Vec<Download>,
    pub done: bool,
    pub error: String,
}

impl BackgroundFetchRecord {
    pub fn downloaded(&self) -> u64 {
        self.downloads
            .iter()
            .filter(|d| d.error.is_empty())
            .map(|d| d.bytes)
            .sum()
    }

    pub fn failures(&self) -> usize {
        self.downloads.iter().filter(|d| !d.error.is_empty()).count()
    }
}

/// Parse an expected-hash token. Accepts:
/// - `sha256-<b64|hex>`
/// - `<algo>-<b64|hex>` (algo ≤ 7 chars)
/// - bare hex (len 40/56/64/96/128 → sha1/sha224/sha256/sha384/sha512)
/// - base64
/// Returns (algorithm, raw_digest) or None on failure.
pub fn expected_digest(expected: &str) -> Option<(String, Vec<u8>)> {
    if expected.is_empty() {
        return None;
    }
    let token = expected.trim();
    let (algo, payload): (String, String) = {
        if token.starts_with("sha256-") {
            ("sha256".to_string(), token[7..].to_string())
        } else if token.contains('-') {
            let (algo, b64) = token.split_once('-').unwrap();
            if algo.len() <= 7 && !algo.is_empty() {
                (algo.to_lowercase(), b64.to_string())
            } else {
                ("sha256".to_string(), token.to_string())
            }
        } else {
            ("sha256".to_string(), token.to_string())
        }
    };

    // Hex check first: hex chars are valid base64, so base64-trying first
    // would silently "decode" a 64-char sha256 hex digest into garbage.
    if algo == "sha256" {
        let blen = payload.len();
        if [40, 56, 64, 96, 128].contains(&blen) {
            if let Ok(raw) = hex::decode(&payload) {
                if raw.len() * 2 == blen {
                    let names: HashMap<usize, &str> = [
                        (40, "sha1"), (56, "sha224"), (64, "sha256"), (96, "sha384"), (128, "sha512"),
                    ].iter().cloned().collect();
                    let mapped = names.get(&blen).map(|s| s.to_string()).unwrap_or("sha256".to_string());
                    return Some((mapped, raw));
                }
            }
        }
    }
    match base64_decode(&payload) {
        Some(raw) => Some((algo, raw)),
        None => hex::decode(&payload).ok().map(|raw| (algo, raw)),
    }
}

fn base64_decode(s: &str) -> Option<Vec<u8>> {
    use base64::{engine::general_purpose, Engine as _};
    match general_purpose::STANDARD.decode(s.trim()) {
        Ok(v) => Some(v),
        Err(_) => general_purpose::URL_SAFE_NO_PAD.decode(s.trim()).ok(),
    }
}

/// Stream-download `url` to a temp file, hashing as we go.
pub async fn download(
    client: &Client,
    url: &str,
    expected: Option<&str>,
    dest_dir: Option<&PathBuf>,
    max_bytes: u64,
) -> Download {
    let mut rec = Download {
        url: url.to_string(),
        expected: expected.unwrap_or("").to_string(),
        ..Default::default()
    };

    let tmp_dir = match dest_dir {
        Some(p) => p.clone(),
        None => std::env::temp_dir(),
    };
    let tmp_path = tmp_dir.join(format!("wr-download-{}", uuid_hash(url)));

    let mut hasher = Sha256::new();
    let mut file = match File::create(&tmp_path).await {
        Ok(f) => f,
        Err(e) => {
            rec.error = format!("io: {e}").chars().take(300).collect();
            return rec;
        }
    };

    let mut response = match client.get(url).send().await {
        Ok(r) => r,
        Err(e) => {
            rec.error = format!("request: {e}").chars().take(300).collect();
            return rec;
        }
    };

    let status = response.status().as_u16();
    if status >= 400 {
        rec.error = format!("http-{status}");
        return rec;
    }
    rec.content_type = response
        .headers()
        .get("content-type")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    let mut total: u64 = 0;
    let mut stream_err: Option<String> = None;
    let mut buf = Vec::with_capacity(64 * 1024);
    loop {
        match response.chunk().await {
            Ok(Some(chunk)) => {
                total += chunk.len() as u64;
                if total > max_bytes {
                    stream_err = Some(format!("exceeds {max_bytes} bytes"));
                    break;
                }
                hasher.update(&chunk);
                buf.extend_from_slice(&chunk);
                if file.write_all(&chunk).await.is_err() {
                    stream_err = Some("io: write failed".to_string());
                    break;
                }
            }
            Ok(None) => break,
            Err(e) => {
                stream_err = Some(format!("stream: {e}").chars().take(300).collect());
                break;
            }
        }
    }
    let _ = file.flush().await;

    if let Some(e) = stream_err {
        rec.error = e;
        let _ = tokio::fs::remove_file(&tmp_path).await;
        return rec;
    }

    rec.path = tmp_path.to_string_lossy().to_string();
    rec.bytes = total;
    rec.sha256 = hex::encode(hasher.finalize());

    // Hash verification
    let want = expected.map(expected_digest).unwrap_or(None);
    match want {
        Some((algo, digest)) => {
            let actual = if algo == "sha256" {
                hex::decode(&rec.sha256).ok()
            } else {
                read_file_digest(&tmp_path, &algo).await
            };
            let verified = match actual {
                Some(a) => a == digest,
                None => false,
            };
            if !verified {
                let _ = tokio::fs::remove_file(&tmp_path).await;
                rec.path = String::new();
                rec.error = format!("hash mismatch (expected {algo})");
            } else {
                rec.verified = true;
            }
        }
        None => rec.verified = true,
    }
    rec
}

async fn read_file_digest(path: &PathBuf, algo: &str) -> Option<Vec<u8>> {
    let mut f = File::open(path).await.ok()?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).await.ok()?;
    match algo {
        "sha256" => Some(hex::encode(Sha256::digest(&buf)).into_bytes()),
        "sha384" => Some(hex::encode(sha2::Sha384::digest(&buf)).into_bytes()),
        "sha512" => Some(hex::encode(sha2::Sha512::digest(&buf)).into_bytes()),
        _ => None,
    }
}

/// A short stable id for temp filenames.
fn uuid_hash(url: &str) -> String {
    let h = Sha256::digest(url.as_bytes());
    hex::encode(&h[..8])
}

/// Concurrent background fetch.
pub async fn background_fetch(
    client: &Client,
    urls: Vec<String>,
    tag: Option<&str>,
    max_bytes: u64,
) -> BackgroundFetchRecord {
    let id = match tag {
        Some(t) if !t.is_empty() => t.to_string(),
        _ => {
            let joined = urls.join(",");
            let h = Sha256::digest(joined.as_bytes());
            format!("bg-{}", &hex::encode(&h[..6]))
        }
    };
    let total = urls.len();
    let mut rec = BackgroundFetchRecord {
        id,
        urls: urls.clone(),
        ..Default::default()
    };
    let mut futures = Vec::with_capacity(total);
    for u in urls {
        let client = client.clone();
        futures.push(tokio::spawn(async move {
            download(&client, &u, None, None, max_bytes).await
        }));
    }
    let mut done = 0;
    let mut had_err = false;
    for f in futures {
        match f.await {
            Ok(d) => {
                if !d.error.is_empty() {
                    had_err = true;
                }
                rec.downloads.push(d);
            }
            Err(e) => {
                had_err = true;
                rec.downloads.push(Download {
                    error: format!("join: {e}").chars().take(300).collect(),
                    ..Default::default()
                });
            }
        }
        done += 1;
        if done == total {
            break;
        }
    }
    if !had_err || rec.downloads.iter().any(|d| d.error.is_empty()) {
        rec.done = true;
    }
    rec
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_expected_digest_sha256_hex() {
        // Python's real behavior: a short hex string is decoded as base64
        // (base64.b64decode("deadbeefcafe") succeeds → 9 bytes).
        let d = expected_digest("deadbeefcafe");
        assert!(d.is_some(), "expected base64 decode to succeed");
        let (algo, raw) = d.unwrap();
        assert_eq!(algo, "sha256");
        assert_eq!(raw.len(), 9);
        // 64-char hex is recognized as sha256 hex
        let h = "ab".repeat(32);
        let (algo, raw) = expected_digest(&h).unwrap();
        assert_eq!(algo, "sha256");
        assert_eq!(raw.len(), 32);
    }

    #[test]
    fn test_expected_digest_prefixed() {
        let (algo, _raw) = expected_digest("sha256-aaaa").unwrap();
        assert_eq!(algo, "sha256");
        // base64 "QUJD" decodes to "ABC"
        let (_algo, raw) = expected_digest("sha256-QUJD").unwrap();
        assert_eq!(raw, b"ABC");
    }

    #[test]
    fn test_expected_digest_empty() {
        // Python: only the *untrimmed* empty string returns None;
        // "   " trims to "" and then base64-decodes to b"".
        assert!(expected_digest("").is_none());
        let d = expected_digest("   ");
        assert!(d.is_some(), "Python returns ('sha256', b'') for whitespace");
        let (algo, raw) = d.unwrap();
        assert_eq!(algo, "sha256");
        assert!(raw.is_empty());
    }

    #[test]
    fn test_bytes_count() {
        let r = BackgroundFetchRecord {
            downloads: vec![
                Download { bytes: 10, ..Default::default() },
                Download { bytes: 5, error: "x".into(), ..Default::default() },
            ],
            ..Default::default()
        };
        assert_eq!(r.downloaded(), 10);
        assert_eq!(r.failures(), 1);
    }
}
