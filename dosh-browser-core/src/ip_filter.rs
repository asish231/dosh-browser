//! `ip_filter.rs` — SSRF Protection and Private Network Guard for Dosh-Browser.
//!
//! Protects agent workflows from Server-Side Request Forgery (SSRF) and intranet scanning.
//! Blocks requests targeting:
//! - Loopback addresses (`127.0.0.0/8`, `::1`)
//! - RFC 1918 private IPv4 networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
//! - Cloud metadata endpoints (`169.254.0.0/16`, e.g. AWS/GCP/Azure `169.254.169.254`)
//! - IPv6 Link-Local (`fe80::/10`) and Unique Local Addresses (`fc00::/7`)
//! - Local domain suffixes (`.localhost`, `.local`, `.internal`, `.lan`)
//! - Custom user-defined CIDR blocklists and allowlists.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::str::FromStr;
use url::Url;

/// CIDR prefix matching structure for IPv4.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CidrV4 {
    pub network: u32,
    pub mask: u32,
}

impl CidrV4 {
    pub const fn new(addr: [u8; 4], prefix: u8) -> Self {
        let network = ((addr[0] as u32) << 24)
            | ((addr[1] as u32) << 16)
            | ((addr[2] as u32) << 8)
            | (addr[3] as u32);
        let mask = if prefix == 0 {
            0
        } else if prefix >= 32 {
            0xFFFF_FFFF
        } else {
            !((1u32 << (32 - prefix)) - 1)
        };
        Self { network: network & mask, mask }
    }

    #[inline]
    pub fn contains(&self, ip: Ipv4Addr) -> bool {
        let octets = ip.octets();
        let val = ((octets[0] as u32) << 24)
            | ((octets[1] as u32) << 16)
            | ((octets[2] as u32) << 8)
            | (octets[3] as u32);
        (val & self.mask) == self.network
    }
}

/// CIDR prefix matching structure for IPv6.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CidrV6 {
    pub network_hi: u64,
    pub network_lo: u64,
    pub mask_hi: u64,
    pub mask_lo: u64,
}

impl CidrV6 {
    pub const fn new(bytes: [u8; 16], prefix: u8) -> Self {
        let mut hi_bytes = [0u8; 8];
        let mut lo_bytes = [0u8; 8];
        let mut i = 0;
        while i < 8 {
            hi_bytes[i] = bytes[i];
            lo_bytes[i] = bytes[i + 8];
            i += 1;
        }
        let hi = u64::from_be_bytes(hi_bytes);
        let lo = u64::from_be_bytes(lo_bytes);

        let (mask_hi, mask_lo) = if prefix == 0 {
            (0, 0)
        } else if prefix <= 64 {
            let m = if prefix == 64 {
                0xFFFF_FFFF_FFFF_FFFF
            } else {
                !((1u64 << (64 - prefix)) - 1)
            };
            (m, 0)
        } else if prefix >= 128 {
            (0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFF)
        } else {
            let m_lo = !((1u64 << (128 - prefix)) - 1);
            (0xFFFF_FFFF_FFFF_FFFF, m_lo)
        };

        Self {
            network_hi: hi & mask_hi,
            network_lo: lo & mask_lo,
            mask_hi,
            mask_lo,
        }
    }

    #[inline]
    pub fn contains(&self, ip: Ipv6Addr) -> bool {
        let octets = ip.octets();
        let mut hi_bytes = [0u8; 8];
        let mut lo_bytes = [0u8; 8];
        hi_bytes.copy_from_slice(&octets[0..8]);
        lo_bytes.copy_from_slice(&octets[8..16]);
        let hi = u64::from_be_bytes(hi_bytes);
        let lo = u64::from_be_bytes(lo_bytes);

        ((hi & self.mask_hi) == self.network_hi) && ((lo & self.mask_lo) == self.network_lo)
    }
}

/// Standard Private IPv4 subnets (RFC 1918, RFC 3927, RFC 6598, loopback, multicast).
pub const PRIVATE_V4: &[CidrV4] = &[
    CidrV4::new([127, 0, 0, 0], 8),      // Loopback
    CidrV4::new([0, 0, 0, 0], 8),        // Current network
    CidrV4::new([10, 0, 0, 0], 8),       // RFC 1918 private class A
    CidrV4::new([172, 16, 0, 0], 12),    // RFC 1918 private class B
    CidrV4::new([192, 168, 0, 0], 16),   // RFC 1918 private class C
    CidrV4::new([169, 254, 0, 0], 16),   // Link-local / Cloud metadata (169.254.169.254)
    CidrV4::new([100, 64, 0, 0], 10),    // Carrier-grade NAT (RFC 6598)
    CidrV4::new([192, 0, 0, 0], 24),     // IETF Protocol Assignments
    CidrV4::new([198, 18, 0, 0], 15),    // Network benchmark testing
    CidrV4::new([224, 0, 0, 0], 4),      // Multicast
    CidrV4::new([240, 0, 0, 0], 4),      // Reserved & Broadcast
];

/// Standard Private IPv6 subnets (Loopback, Link-Local, ULA, Site-Local).
pub const PRIVATE_V6: &[CidrV6] = &[
    CidrV6::new([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 128), // Unspecified ::
    CidrV6::new([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1], 128), // Loopback ::1
    CidrV6::new([0xfe, 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 10), // Link-local fe80::/10
    CidrV6::new([0xfc, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 7),     // Unique Local Address fc00::/7
    CidrV6::new([0xfe, 0xc0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], 10), // Site-local deprecated fec0::/10
];

/// SSRF Filter Error types.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IpFilterError {
    BlockedPrivateIp(IpAddr),
    BlockedMetadataService(IpAddr),
    BlockedHostname(String),
    BlockedByCustomCidr(IpAddr),
    InvalidUrl(String),
}

impl std::fmt::Display for IpFilterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::BlockedPrivateIp(ip) => write!(f, "SSRF: Request blocked targeting private IP: {}", ip),
            Self::BlockedMetadataService(ip) => write!(f, "SSRF: Blocked cloud metadata request: {}", ip),
            Self::BlockedHostname(h) => write!(f, "SSRF: Request blocked targeting internal hostname: {}", h),
            Self::BlockedByCustomCidr(ip) => write!(f, "SSRF: Request blocked by custom CIDR rule: {}", ip),
            Self::InvalidUrl(msg) => write!(f, "Invalid URL for IP filtering: {}", msg),
        }
    }
}

impl std::error::Error for IpFilterError {}

/// Main SSRF & IP Filter engine.
#[derive(Debug, Clone)]
pub struct IpFilter {
    pub block_private: bool,
    pub custom_block_v4: Vec<CidrV4>,
    pub custom_block_v6: Vec<CidrV6>,
    pub custom_allow_v4: Vec<CidrV4>,
    pub custom_allow_v6: Vec<CidrV6>,
}

impl Default for IpFilter {
    fn default() -> Self {
        Self::new(true)
    }
}

impl IpFilter {
    /// Create a new IP filter. If `block_private` is true, all RFC1918, link-local,
    /// loopback, and metadata IPs are automatically blocked.
    pub fn new(block_private: bool) -> Self {
        Self {
            block_private,
            custom_block_v4: Vec::new(),
            custom_block_v6: Vec::new(),
            custom_allow_v4: Vec::new(),
            custom_allow_v6: Vec::new(),
        }
    }

    /// Add a custom CIDR string (e.g. `192.0.2.0/24` or `2001:db8::/32`) to block list.
    pub fn add_block_cidr(&mut self, cidr_str: &str) -> Result<(), String> {
        let (ip, prefix) = parse_cidr(cidr_str)?;
        match ip {
            IpAddr::V4(v4) => self.custom_block_v4.push(CidrV4::new(v4.octets(), prefix)),
            IpAddr::V6(v6) => self.custom_block_v6.push(CidrV6::new(v6.octets(), prefix)),
        }
        Ok(())
    }

    /// Add a custom CIDR string to allow list (overrides default private block).
    pub fn add_allow_cidr(&mut self, cidr_str: &str) -> Result<(), String> {
        let (ip, prefix) = parse_cidr(cidr_str)?;
        match ip {
            IpAddr::V4(v4) => self.custom_allow_v4.push(CidrV4::new(v4.octets(), prefix)),
            IpAddr::V6(v6) => self.custom_allow_v6.push(CidrV6::new(v6.octets(), prefix)),
        }
        Ok(())
    }

    /// Check if a concrete IP address is allowed.
    pub fn is_ip_allowed(&self, ip: IpAddr) -> Result<(), IpFilterError> {
        // 1. Check custom allowlists first (explicit user override)
        match ip {
            IpAddr::V4(v4) => {
                for cidr in &self.custom_allow_v4 {
                    if cidr.contains(v4) {
                        return Ok(());
                    }
                }
            }
            IpAddr::V6(v6) => {
                for cidr in &self.custom_allow_v6 {
                    if cidr.contains(v6) {
                        return Ok(());
                    }
                }
            }
        }

        // 2. Check custom blocklists
        match ip {
            IpAddr::V4(v4) => {
                for cidr in &self.custom_block_v4 {
                    if cidr.contains(v4) {
                        return Err(IpFilterError::BlockedByCustomCidr(ip));
                    }
                }
            }
            IpAddr::V6(v6) => {
                for cidr in &self.custom_block_v6 {
                    if cidr.contains(v6) {
                        return Err(IpFilterError::BlockedByCustomCidr(ip));
                    }
                }
            }
        }

        // 3. Check default private ranges if enabled
        if self.block_private {
            match ip {
                IpAddr::V4(v4) => {
                    // Check specifically for AWS/GCP/Azure link-local metadata 169.254.169.254
                    if v4 == Ipv4Addr::new(169, 254, 169, 254) {
                        return Err(IpFilterError::BlockedMetadataService(ip));
                    }
                    for cidr in PRIVATE_V4 {
                        if cidr.contains(v4) {
                            return Err(IpFilterError::BlockedPrivateIp(ip));
                        }
                    }
                }
                IpAddr::V6(v6) => {
                    // IPv4-mapped IPv6 ::ffff:a.b.c.d
                    if let Some(v4) = v6.to_ipv4_mapped() {
                        if v4 == Ipv4Addr::new(169, 254, 169, 254) {
                            return Err(IpFilterError::BlockedMetadataService(IpAddr::V4(v4)));
                        }
                        for cidr in PRIVATE_V4 {
                            if cidr.contains(v4) {
                                return Err(IpFilterError::BlockedPrivateIp(IpAddr::V4(v4)));
                            }
                        }
                    }
                    for cidr in PRIVATE_V6 {
                        if cidr.contains(v6) {
                            return Err(IpFilterError::BlockedPrivateIp(ip));
                        }
                    }
                }
            }
        }

        Ok(())
    }

    /// Check if a hostname string is allowed. Rejects obvious intranet hostnames
    /// and resolves IP literals.
    pub fn is_host_allowed(&self, host: &str) -> Result<(), IpFilterError> {
        let clean = host.trim().trim_start_matches('[').trim_end_matches(']').to_lowercase();

        // Check if host is a literal IP address
        if let Ok(ip) = IpAddr::from_str(&clean) {
            return self.is_ip_allowed(ip);
        }

        // Check dangerous internal host patterns
        if self.block_private {
            if clean == "localhost"
                || clean.ends_with(".localhost")
                || clean.ends_with(".local")
                || clean.ends_with(".internal")
                || clean.ends_with(".lan")
                || clean == "metadata.google.internal"
                || clean == "instance-data"
            {
                return Err(IpFilterError::BlockedHostname(clean));
            }
        }

        Ok(())
    }

    /// Check a full URL before network dispatch.
    pub fn check_url(&self, url: &Url) -> Result<(), IpFilterError> {
        let host_str = url.host_str().ok_or_else(|| IpFilterError::InvalidUrl("Missing host".into()))?;
        self.is_host_allowed(host_str)
    }
}

/// Helper to parse standard CIDR notation `IP/prefix`.
fn parse_cidr(s: &str) -> Result<(IpAddr, u8), String> {
    let parts: Vec<&str> = s.split('/').collect();
    if parts.len() != 2 {
        return Err(format!("Invalid CIDR format (expected IP/prefix): {}", s));
    }
    let ip = IpAddr::from_str(parts[0]).map_err(|e| format!("Invalid IP in CIDR {}: {}", s, e))?;
    let prefix = parts[1].parse::<u8>().map_err(|e| format!("Invalid prefix in CIDR {}: {}", s, e))?;

    match ip {
        IpAddr::V4(_) if prefix > 32 => Err(format!("IPv4 prefix cannot exceed 32: {}", prefix)),
        IpAddr::V6(_) if prefix > 128 => Err(format!("IPv6 prefix cannot exceed 128: {}", prefix)),
        _ => Ok((ip, prefix)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_loopback_blocked() {
        let filter = IpFilter::default();
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1))).is_err());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(127, 255, 255, 254))).is_err());
        assert!(filter.is_ip_allowed(IpAddr::V6(Ipv6Addr::new(0, 0, 0, 0, 0, 0, 0, 1))).is_err());
    }

    #[test]
    fn test_cloud_metadata_blocked() {
        let filter = IpFilter::default();
        let res = filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(169, 254, 169, 254)));
        assert!(matches!(res, Err(IpFilterError::BlockedMetadataService(_))));
    }

    #[test]
    fn test_rfc1918_private_subnets_blocked() {
        let filter = IpFilter::default();
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(10, 0, 5, 23))).is_err());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(172, 16, 0, 1))).is_err());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(172, 31, 255, 255))).is_err());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(192, 168, 1, 100))).is_err());

        // Public IP should pass cleanly
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(8, 8, 8, 8))).is_ok());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(1, 1, 1, 1))).is_ok());
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(172, 32, 0, 1))).is_ok());
    }

    #[test]
    fn test_ipv6_link_local_and_ula_blocked() {
        let filter = IpFilter::default();
        // fe80::1
        let link_local = IpAddr::V6(Ipv6Addr::new(0xfe80, 0, 0, 0, 0, 0, 0, 1));
        assert!(filter.is_ip_allowed(link_local).is_err());

        // fc00::1
        let ula = IpAddr::V6(Ipv6Addr::new(0xfc00, 0, 0, 0, 0, 0, 0, 1));
        assert!(filter.is_ip_allowed(ula).is_err());

        // Public IPv6 (Google DNS 2001:4860:4860::8888) should pass
        let google_v6 = IpAddr::V6(Ipv6Addr::new(0x2001, 0x4860, 0x4860, 0, 0, 0, 0, 0x8888));
        assert!(filter.is_ip_allowed(google_v6).is_ok());
    }

    #[test]
    fn test_hostname_filtering() {
        let filter = IpFilter::default();
        assert!(filter.is_host_allowed("localhost").is_err());
        assert!(filter.is_host_allowed("app.localhost").is_err());
        assert!(filter.is_host_allowed("service.local").is_err());
        assert!(filter.is_host_allowed("api.internal").is_err());
        assert!(filter.is_host_allowed("metadata.google.internal").is_err());

        assert!(filter.is_host_allowed("google.com").is_ok());
        assert!(filter.is_host_allowed("api.github.com").is_ok());
    }

    #[test]
    fn test_custom_cidr_allow_and_block() {
        let mut filter = IpFilter::new(true);
        // Explicitly allow private subnet 10.50.0.0/16
        filter.add_allow_cidr("10.50.0.0/16").unwrap();
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(10, 50, 1, 2))).is_ok());
        // Other 10.x.x.x should still be blocked
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(10, 51, 1, 2))).is_err());

        // Explicitly block a public subnet
        filter.add_block_cidr("93.184.216.0/24").unwrap();
        assert!(filter.is_ip_allowed(IpAddr::V4(Ipv4Addr::new(93, 184, 216, 34))).is_err());
    }

    #[test]
    fn test_url_check() {
        let filter = IpFilter::default();
        let safe_url = Url::parse("https://example.com/index.html").unwrap();
        assert!(filter.check_url(&safe_url).is_ok());

        let attack_url = Url::parse("http://169.254.169.254/latest/meta-data/").unwrap();
        assert!(filter.check_url(&attack_url).is_err());

        let attack_local = Url::parse("http://localhost:3000/admin").unwrap();
        assert!(filter.check_url(&attack_local).is_err());
    }
}

