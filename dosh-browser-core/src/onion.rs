/// Validate and handle .onion addresses.
///
/// This engine has no Tor transport, so .onion hosts are never fetched:
/// resolving them over clearnet DNS would leak the destination.
pub fn parse_v3_onion(raw_host: &str) -> bool {
    let mut host = raw_host.trim().to_lowercase();
    if host.ends_with('.') {
        host.pop();
    }
    if host.ends_with(".onion") {
        host.truncate(host.len() - 6);
    }
    // Tor v3 onion address is 56 base32 characters
    if host.len() != 56 {
        return false;
    }
    // Check characters are valid base32 (a-z, 2-7)
    host.chars().all(|c| c.is_ascii_alphanumeric() && (c.is_ascii_lowercase() || ('2'..='7').contains(&c)))
}

pub fn onion_refusal(host: &str) -> (u16, String) {
    if !parse_v3_onion(host) {
        (403, format!("invalid .onion address: {}", host))
    } else {
        (
            403,
            format!(
                ".onion host requires a Tor transport, which this engine does not have; refusing to resolve {} over clearnet DNS",
                host
            ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_v3_onion() {
        let valid_onion = "mkxyobdwj6xy5kbpyyootrgdscfwzol5iy5ggtu6lb6xzcc5wdxynwad.onion";
        assert!(parse_v3_onion(valid_onion));

        let short_onion = "tooshort.onion";
        assert!(!parse_v3_onion(short_onion));
    }

    #[test]
    fn test_onion_refusal_message() {
        let valid_onion = "mkxyobdwj6xy5kbpyyootrgdscfwzol5iy5ggtu6lb6xzcc5wdxynwad.onion";
        let (status, reason) = onion_refusal(valid_onion);
        assert_eq!(status, 403);
        assert!(reason.contains("refusing to resolve"));
    }
}
