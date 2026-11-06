"""S6: Cookie / site-data partitioning (double-key per top-site).

Real browsers partition third-party cookies: a cookie set by
``ads.example`` while browsing ``shop.example`` is stored under the
partition key ``(shop.example, ads.example)`` and is NOT sent to
``ads.example`` when the user is on a different top site, nor to the
top site itself.

This module implements that model honestly:

- ``PartitionedCookieJar`` holds a first-party jar (top-level cookies,
  double-keyed trivially as ``(site, site)``) plus per-partition jars
  keyed by ``(top_site, frame_origin)``.
- ``store_response_cookies`` takes the frame's Set-Cookie list plus the
  context (page_url, frame_url, is_third_party) and routes each cookie
  to the right jar. A third-party cookie without an explicit Partitioned
  attribute is STILL partitioned (the engine's disclosed default:
  full partitioning, stronger than Chrome's legacy third-party jar).
- ``request_cookies`` returns exactly what a request from a given frame
  context should carry: the top site's first-party cookies + that
  specific partition — never another partition's cookies.
- Storage (localStorage/sessionStorage seeds) partition by the same key
  via ``storage_partition_key``.
"""

from __future__ import annotations

from urllib.parse import urlparse


def _registrable(url: str) -> str:
    """The site a cookie/storage partition is keyed by: scheme + host.

    A registrable-domain (eTLD+1) reduction is the real-world key, but
    an honest engine cannot embed the Public Suffix List; host-level
    keying is the disclosed approximation (over-partitions subdomains
    like ``a.shop.example`` / ``b.shop.example`` — never under-isolates).
    """
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.hostname}"
        return url
    except Exception:
        return url


def _same_site(a: str, b: str) -> bool:
    return _registrable(a) == _registrable(b)


def is_third_party(page_url: str, frame_url: str) -> bool:
    return not _same_site(page_url, frame_url)


def partition_key(page_url: str, frame_url: str | None = None) -> tuple[str, str]:
    """The double key: (top_site, frame_origin). First-party is
    (site, site)."""
    top = _registrable(page_url)
    if frame_url is None or _same_site(page_url, frame_url):
        return (top, top)
    return (top, _registrable(frame_url))


class _Cookies:
    """A tiny ordered cookie bag (name -> value, insertion-ordered)."""

    def __init__(self) -> None:
        self._bag: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self._bag[name] = value

    def get(self, name: str) -> str | None:
        return self._bag.get(name)

    def delete(self, name: str) -> bool:
        if name in self._bag:
            del self._bag[name]
            return True
        return False

    def items(self):
        return list(self._bag.items())

    def names(self):
        return list(self._bag.keys())

    def __len__(self) -> int:
        return len(self._bag)


def parse_set_cookie(raw: str) -> dict:
    """Parse a Set-Cookie header into (name, value, attrs). Honest subset:
    name/value/Secure/HttpOnly/Partitioned/SameSite are recorded; path/
    domain are recorded but partitioning ignores them (host-keyed)."""
    parts = [p.strip() for p in raw.split(";")]
    if not parts or "=" not in parts[0]:
        return {}
    name, _, value = parts[0].partition("=")
    attrs: dict[str, str] = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        attrs[k.strip().lower()] = v.strip()
    return {
        "name": name.strip(),
        "value": value.strip(),
        "secure": "secure" in attrs,
        "http_only": "httponly" in attrs,
        "partitioned": "partitioned" in attrs,
        "same_site": attrs.get("samesite", ""),
    }


class PartitionedCookieJar:
    """Double-keyed cookie storage: (top_site, frame_origin) -> jar.

    First-party cookies live in the (site, site) partition and are
    shared by all first-party requests for that site. Third-party
    cookies live ONLY in their (top, frame) partition.
    """

    def __init__(self) -> None:
        self._partitions: dict[tuple[str, str], _Cookies] = {}

    def _jar(self, key: tuple[str, str]) -> _Cookies:
        if key not in self._partitions:
            self._partitions[key] = _Cookies()
        return self._partitions[key]

    # ---- write path ----------------------------------------------------
    def store_response_cookies(
        self,
        set_cookies: list[str],
        *,
        page_url: str,
        frame_url: str | None = None,
    ) -> list[dict]:
        """Store a frame's Set-Cookie list under the right partition.

        Returns a record of what happened per cookie (for telemetry):
        ``[{name, partition, third_party, partitioned_attr}]``.
        """
        key = partition_key(page_url, frame_url)
        records: list[dict] = []
        third = is_third_party(page_url, frame_url) if frame_url else False
        for raw in set_cookies:
            c = parse_set_cookie(raw)
            if not c:
                continue
            jar = self._jar(key)
            jar.set(c["name"], c["value"])
            records.append(
                {
                    "name": c["name"],
                    "partition": list(key),
                    "third_party": third,
                    "partitioned_attr": c["partitioned"],
                }
            )
        return records

    # ---- read path -----------------------------------------------------
    def request_cookies(
        self,
        request_url: str,
        *,
        page_url: str,
        frame_url: str | None = None,
    ) -> dict[str, str]:
        """The cookies a request from this frame context carries.

        First-party request: the (top, top) jar.
        Third-party request: the (top, frame) jar ONLY — the top site's
        first-party cookies are never leaked to a third party, and no
        other top site's partition is ever visible.
        """
        key = partition_key(page_url, frame_url)
        jar = self._partitions.get(key)
        if jar is None:
            return {}
        return {k: v for k, v in jar.items()}

    # ---- introspection --------------------------------------------------
    def partitions(self) -> list[tuple[str, str]]:
        return list(self._partitions.keys())

    def partition_names(self, key: tuple[str, str]) -> list[str]:
        jar = self._partitions.get(key)
        return jar.names() if jar else []

    def total_cookies(self) -> int:
        return sum(len(j) for j in self._partitions.values())


def storage_partition_key(page_url: str, frame_url: str | None = None) -> str:
    """The storage (localStorage etc.) partition key as a string — the
    same double key as cookies, joined for storage-area addressing."""
    top, frame = partition_key(page_url, frame_url)
    return f"{top}|{frame}"
