"""S6: Cookie / site-data partitioning — hermetic tests.

Pins the double-key model: third-party cookies are stored under
(top_site, frame_origin), never leak across top sites, never leak to
the top site itself, and storage areas partition by the same key.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.engine.partition import (
    PartitionedCookieJar,
    is_third_party,
    parse_set_cookie,
    partition_key,
    storage_partition_key,
)


def test_partition_key_first_party_is_double_same():
    assert partition_key("https://shop.example/a", None) == ("https://shop.example",) * 2
    assert partition_key("https://shop.example/a", "https://shop.example/frame") == ("https://shop.example",) * 2


def test_partition_key_third_party_double_keys():
    key = partition_key("https://shop.example/a", "https://ads.example/widget")
    assert key == ("https://shop.example", "https://ads.example")
    assert is_third_party("https://shop.example/a", "https://ads.example/widget")
    assert not is_third_party("https://shop.example/a", "https://shop.example/b")


def test_third_party_cookie_lands_in_its_partition():
    jar = PartitionedCookieJar()
    jar.store_response_cookies(
        ["sid=abc; Path=/", "tracker=xyz; Partitioned"],
        page_url="https://shop.example/page",
        frame_url="https://ads.example/frame",
    )
    # Both cookies live ONLY in the (shop, ads) partition.
    top, frame = "https://shop.example", "https://ads.example"
    assert sorted(jar.partition_names((top, frame))) == ["sid", "tracker"]
    # The first-party partition is untouched.
    assert jar.partition_names((top, top)) == []
    # The third party reading in this exact context sees them.
    got = jar.request_cookies("https://ads.example/pixel", page_url="https://shop.example/page", frame_url="https://ads.example/frame")
    assert got == {"sid": "abc", "tracker": "xyz"}


def test_no_cross_top_site_leak():
    """The same third party on a DIFFERENT top site has a separate
    partition — the two top sites never see each other's trackers."""
    jar = PartitionedCookieJar()
    jar.store_response_cookies(
        ["t=shop-value"], page_url="https://shop.example/p", frame_url="https://ads.example/f",
    )
    jar.store_response_cookies(
        ["t=news-value"], page_url="https://news.example/p", frame_url="https://ads.example/f",
    )
    shop_view = jar.request_cookies("https://ads.example/x", page_url="https://shop.example/p", frame_url="https://ads.example/f")
    news_view = jar.request_cookies("https://ads.example/x", page_url="https://news.example/p", frame_url="https://ads.example/f")
    assert shop_view == {"t": "shop-value"}
    assert news_view == {"t": "news-value"}, "partitions must be isolated per top site"


def test_first_party_cookies_never_leak_to_third_party_requests():
    jar = PartitionedCookieJar()
    jar.store_response_cookies(
        ["session=top-secret"], page_url="https://bank.example/", frame_url=None,
    )
    # A third-party frame on the same page asks for its cookies:
    got = jar.request_cookies("https://ads.example/x", page_url="https://bank.example/", frame_url="https://ads.example/f")
    assert got == {}, "the top site's first-party cookie must NOT be sent to the third party"
    # The top site's own request still sees it:
    mine = jar.request_cookies("https://bank.example/api", page_url="https://bank.example/")
    assert mine == {"session": "top-secret"}


def test_unknown_context_sees_nothing():
    jar = PartitionedCookieJar()
    jar.store_response_cookies(["a=1"], page_url="https://shop.example/", frame_url="https://ads.example/f")
    # A request with no recorded partition context:
    empty = jar.request_cookies("https://ads.example/x", page_url="https://other.example/", frame_url="https://ads.example/f")
    assert empty == {}, "unrelated contexts must see nothing"


def test_storage_partition_key_matches_cookie_partition():
    ck = partition_key("https://shop.example/p", "https://ads.example/f")
    sk = storage_partition_key("https://shop.example/p", "https://ads.example/f")
    assert sk == f"{ck[0]}|{ck[1]}"
    # First-party storage shares the trivial partition:
    assert storage_partition_key("https://shop.example/p") == "https://shop.example|https://shop.example"


def test_parse_set_cookie_records_partitioned_attribute():
    c = parse_set_cookie("x=1; Secure; HttpOnly; Partitioned; SameSite=None")
    assert c["name"] == "x" and c["value"] == "1"
    assert c["secure"] and c["http_only"] and c["partitioned"]
    assert c["same_site"].lower() == "none"


def test_total_and_partition_listing():
    jar = PartitionedCookieJar()
    jar.store_response_cookies(["a=1"], page_url="https://s1.example/", frame_url="https://t.example/f")
    jar.store_response_cookies(["b=2"], page_url="https://s2.example/", frame_url="https://t.example/f")
    jar.store_response_cookies(["c=3"], page_url="https://s1.example/")
    assert jar.total_cookies() == 3
    assert set(jar.partitions()) == {
        ("https://s1.example", "https://t.example"),
        ("https://s2.example", "https://t.example"),
        ("https://s1.example", "https://s1.example"),
    }
