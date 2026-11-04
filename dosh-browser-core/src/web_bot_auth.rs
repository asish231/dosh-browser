//! `web_bot_auth.rs` — RFC 9421 HTTP Message Signatures for Agent Identity.
//!
//! Implements cryptographic request signing for authorized web bots and AI agents.
//! Allows dosh-browser to prove its authentic identity to web servers and CDN anti-bot
//! gates (Cloudflare, Fastly, Akamai) via standard Ed25519 or HMAC-SHA256 signatures,
//! preventing unnecessary CAPTCHAs and IP bans.

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use ring::signature::{self, Ed25519KeyPair};
use std::time::{SystemTime, UNIX_EPOCH};

/// Algorithm used for RFC 9421 signature.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignatureAlgorithm {
    Ed25519,
    HmacSha256,
}

impl SignatureAlgorithm {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Ed25519 => "ed25519",
            Self::HmacSha256 => "hmac-sha256",
        }
    }
}

/// Agent authentication configuration.
#[derive(Clone)]
pub struct BotAuthConfig {
    pub key_id: String,
    pub algorithm: SignatureAlgorithm,
    pub agent_identifier: String,
    // 32-byte Ed25519 seed or HMAC secret
    pub secret_key: Vec<u8>,
}

impl BotAuthConfig {
    /// Create an Ed25519 configuration with a 32-byte raw seed.
    pub fn new_ed25519(key_id: &str, agent_id: &str, seed: [u8; 32]) -> Self {
        Self {
            key_id: key_id.to_string(),
            algorithm: SignatureAlgorithm::Ed25519,
            agent_identifier: agent_id.to_string(),
            secret_key: seed.to_vec(),
        }
    }

    /// Create an HMAC-SHA256 configuration with a shared secret.
    pub fn new_hmac_sha256(key_id: &str, agent_id: &str, secret: &[u8]) -> Self {
        Self {
            key_id: key_id.to_string(),
            algorithm: SignatureAlgorithm::HmacSha256,
            agent_identifier: agent_id.to_string(),
            secret_key: secret.to_vec(),
        }
    }
}

/// Signed headers pair output from signing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignedHeaders {
    pub signature_agent: String,
    pub signature_input: String,
    pub signature: String,
}

/// Sign an HTTP request following RFC 9421.
pub fn sign_request(
    config: &BotAuthConfig,
    method: &str,
    target_uri: &str,
    authority: &str,
    timestamp: Option<u64>,
) -> Result<SignedHeaders, String> {
    let now = timestamp.unwrap_or_else(|| {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
    });

    let agent_hdr = config.agent_identifier.clone();
    let label = "sig1";

    // RFC 9421 Signature-Input specification:
    // sig1=("@method" "@target-uri" "@authority" "signature-agent");created=...;keyid=...;alg=...
    let signature_input = format!(
        "{}=(\"@method\" \"@target-uri\" \"@authority\" \"signature-agent\");created={};keyid=\"{}\";alg=\"{}\"",
        label,
        now,
        config.key_id,
        config.algorithm.as_str()
    );

    // Compute signature base string per RFC 9421 Section 2.5:
    let signature_base = format!(
        "\"@method\": {}\n\"@target-uri\": {}\n\"@authority\": {}\n\"signature-agent\": {}\n\"@signature-params\": {}",
        method.to_uppercase(),
        target_uri,
        authority,
        agent_hdr,
        &signature_input[label.len() + 1..] // strip "sig1=" prefix for @signature-params
    );

    let raw_sig = match config.algorithm {
        SignatureAlgorithm::Ed25519 => {
            if config.secret_key.len() != 32 {
                return Err("Ed25519 seed must be exactly 32 bytes".to_string());
            }
            let key_pair = Ed25519KeyPair::from_seed_unchecked(&config.secret_key)
                .map_err(|e| format!("Failed to create Ed25519 key pair: {}", e))?;
            key_pair.sign(signature_base.as_bytes()).as_ref().to_vec()
        }
        SignatureAlgorithm::HmacSha256 => {
            let key = ring::hmac::Key::new(ring::hmac::HMAC_SHA256, &config.secret_key);
            ring::hmac::sign(&key, signature_base.as_bytes()).as_ref().to_vec()
        }
    };

    let signature_val = format!("{}=:{}:", label, BASE64.encode(raw_sig));

    Ok(SignedHeaders {
        signature_agent: agent_hdr,
        signature_input,
        signature: signature_val,
    })
}

/// Verify an Ed25519 RFC 9421 signature given public key bytes.
pub fn verify_ed25519_signature(
    public_key_bytes: &[u8],
    method: &str,
    target_uri: &str,
    authority: &str,
    signed_headers: &SignedHeaders,
) -> Result<(), String> {
    let label = "sig1";
    let sig_prefix = format!("{}=:", label);
    if !signed_headers.signature.starts_with(&sig_prefix) || !signed_headers.signature.ends_with(':') {
        return Err("Invalid Signature format".to_string());
    }

    let b64 = &signed_headers.signature[sig_prefix.len()..signed_headers.signature.len() - 1];
    let sig_bytes = BASE64.decode(b64).map_err(|e| format!("Base64 decode error: {}", e))?;

    let signature_base = format!(
        "\"@method\": {}\n\"@target-uri\": {}\n\"@authority\": {}\n\"signature-agent\": {}\n\"@signature-params\": {}",
        method.to_uppercase(),
        target_uri,
        authority,
        signed_headers.signature_agent,
        &signed_headers.signature_input[label.len() + 1..]
    );

    let peer_public_key = signature::UnparsedPublicKey::new(&signature::ED25519, public_key_bytes);
    peer_public_key
        .verify(signature_base.as_bytes(), &sig_bytes)
        .map_err(|_| "Signature verification failed".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::signature::KeyPair;

    #[test]
    fn test_ed25519_signing_and_verification() {
        // Fixed 32-byte test seed
        let seed = [42u8; 32];
        let key_pair = Ed25519KeyPair::from_seed_unchecked(&seed).unwrap();
        let pub_key = key_pair.public_key().as_ref().to_vec();

        let config = BotAuthConfig::new_ed25519("key-2026", "dosh-browser-agent/1.0", seed);

        let method = "GET";
        let target_uri = "https://api.example.com/v1/data";
        let authority = "api.example.com";
        let timestamp = 1712000000;

        let signed = sign_request(&config, method, target_uri, authority, Some(timestamp)).unwrap();

        assert_eq!(signed.signature_agent, "dosh-browser-agent/1.0");
        assert!(signed.signature_input.contains("created=1712000000"));
        assert!(signed.signature_input.contains("keyid=\"key-2026\""));
        assert!(signed.signature.starts_with("sig1=:"));

        // Verify valid signature
        assert!(verify_ed25519_signature(&pub_key, method, target_uri, authority, &signed).is_ok());

        // Tampered target URI must fail
        assert!(verify_ed25519_signature(
            &pub_key,
            method,
            "https://api.example.com/v1/tampered",
            authority,
            &signed
        )
        .is_err());
    }

    #[test]
    fn test_hmac_sha256_signing() {
        let secret = b"super-secret-hmac-key";
        let config = BotAuthConfig::new_hmac_sha256("hmac-1", "dosh-bot", secret);

        let signed = sign_request(
            &config,
            "POST",
            "https://internal.site/submit",
            "internal.site",
            Some(1712000000),
        )
        .unwrap();

        assert!(signed.signature_input.contains("alg=\"hmac-sha256\""));
        assert!(signed.signature.starts_with("sig1=:"));
    }
}
