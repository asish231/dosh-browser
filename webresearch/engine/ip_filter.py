"""ip_filter.py — SSRF Protection and Private Network Guard for Dosh-Browser.

Protects agent workflows from Server-Side Request Forgery (SSRF) and intranet scanning.
Blocks requests targeting:
- Loopback addresses (127.0.0.0/8, ::1)
- RFC 1918 private IPv4 networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Cloud metadata endpoints (169.254.0.0/16, e.g. AWS/GCP/Azure 169.254.169.254)
- IPv6 Link-Local (fe80::/10) and Unique Local Addresses (fc00::/7)
- Local domain suffixes (.localhost, .local, .internal, .lan)
- Custom user-defined CIDR blocklists and allowlists.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Standard blocked subnets
PRIVATE_V4_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.ip_network("100.64.0.0/10"),  # Carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),   # Multicast
    ipaddress.ip_network("240.0.0.0/4"),   # Reserved
]

PRIVATE_V6_NETWORKS = [
    ipaddress.ip_network("::1/128"),        # Loopback
    ipaddress.ip_network("::/128"),         # Unspecified
    ipaddress.ip_network("fe80::/10"),      # Link-local
    ipaddress.ip_network("fc00::/7"),       # Unique Local Address
    ipaddress.ip_network("fec0::/10"),      # Site-local deprecated
]

BLOCKED_INTERNAL_DOMAINS = (
    "localhost",
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    "metadata.google.internal",
    "instance-data",
)

METADATA_IPV4 = ipaddress.ip_address("169.254.169.254")


class IpFilterError(Exception):
    """Raised when an outbound URL violates SSRF / private network boundaries."""


class IpFilter:
    def __init__(self, block_private: bool | None = None):
        import os
        if block_private is None:
            # If WR_ALLOW_PRIVATE_IPS=1 is set (e.g. during local test runs), don't block loopback by default
            block_private = os.environ.get("WR_ALLOW_PRIVATE_IPS") != "1"
        self.block_private = block_private
        self.custom_block_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self.custom_allow_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []

    def add_block_cidr(self, cidr_str: str) -> None:
        net = ipaddress.ip_network(cidr_str.strip(), strict=False)
        self.custom_block_cidrs.append(net)

    def add_allow_cidr(self, cidr_str: str) -> None:
        net = ipaddress.ip_network(cidr_str.strip(), strict=False)
        self.custom_allow_cidrs.append(net)

    def is_ip_allowed(self, ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        """Raise IpFilterError if IP is not permitted, otherwise returns None."""
        # 1. Check custom allowlists first (explicit operator override)
        for allowed in self.custom_allow_cidrs:
            if ip_obj in allowed:
                return

        # 2. Check custom blocklists
        for blocked in self.custom_block_cidrs:
            if ip_obj in blocked:
                raise IpFilterError(f"SSRF: IP {ip_obj} blocked by custom CIDR rule {blocked}")

        if not self.block_private:
            return

        # 3. Check cloud metadata address specifically
        if ip_obj == METADATA_IPV4 or (
            isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped == METADATA_IPV4
        ):
            raise IpFilterError(f"SSRF: Blocked cloud metadata request: {ip_obj}")

        # Handle IPv4-mapped IPv6 (::ffff:127.0.0.1)
        if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
            ip_obj = ip_obj.ipv4_mapped

        if isinstance(ip_obj, ipaddress.IPv4Address):
            for net in PRIVATE_V4_NETWORKS:
                if ip_obj in net:
                    raise IpFilterError(f"SSRF: Request blocked targeting private IP: {ip_obj} (in {net})")
        else:
            for net in PRIVATE_V6_NETWORKS:
                if ip_obj in net:
                    raise IpFilterError(f"SSRF: Request blocked targeting private IP: {ip_obj} (in {net})")

    def is_host_allowed(self, host: str) -> None:
        """Check hostname or literal IP string against SSRF rules."""
        clean = host.strip().strip("[]").lower()

        # Check if literal IP address
        try:
            ip_obj = ipaddress.ip_address(clean)
            self.is_ip_allowed(ip_obj)
            return
        except ValueError:
            pass

        # Check dangerous domain patterns
        if self.block_private:
            if clean == "localhost" or any(clean.endswith(d) for d in BLOCKED_INTERNAL_DOMAINS):
                raise IpFilterError(f"SSRF: Request blocked targeting internal hostname: {clean}")

    def check_url(self, url: str) -> None:
        """Validate URL host against SSRF rules."""
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise IpFilterError(f"SSRF: Invalid URL (missing hostname): {url}")
        self.is_host_allowed(hostname)
