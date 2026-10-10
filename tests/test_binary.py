"""Unit tests for ANBF v1.0 binary format compiler and unpacker."""

import time
import pytest

from webresearch.engine.binary import (
    HEADER_SIZE,
    MAGIC,
    AnbDelta,
    BinaryError,
    IntegrityError,
    anbf_to_acd,
    anbf_to_agent_view,
    apply_delta_patch,
    pack_anbf,
    pack_delta_patch,
    unpack_anbf,
    unpack_header,
)
from webresearch.types import (
    Acd,
    AcdAction,
    AcdBlock,
)


@pytest.fixture
def sample_acd() -> Acd:
    return Acd(
        url="https://example.com/shop",
        title="Sample Shop Page",
        blocks=[
            AcdBlock(id="b1", type="heading", level=1, text="Welcome to Shop", locator="#hero > h1"),
            AcdBlock(id="b2", type="text", text="Find great deals on electronics.", locator="#hero > p"),
            AcdBlock(
                id="b3",
                type="table",
                rows=[
                    ["Item", "Price", "Stock"],
                    ["Laptop", "$999", "12"],
                    ["Mouse", "$29", "85"],
                ],
                locator="#products > table",
            ),
            AcdBlock(id="b4", type="code", text="console.log('loaded');"),
        ],
        actions=[
            AcdAction(ref="a1", kind="navigate", role="link", label="Home", href="/"),
            AcdAction(ref="a2", kind="activate", role="button", label="Add to Cart", run="click:path=0,1"),
            AcdAction(ref="a3", kind="drag", role="slider", label="Price Filter", run="drag:path=2,0"),
            AcdAction(ref="a4", kind="submit", role="button", label="Checkout", name="checkout_btn"),
        ],
        unavailable=["drag: numeric deltas are heuristic without a layout engine"],
        framework={"__NEXT_DATA__": True},
    )


def test_header_structure(sample_acd: Acd):
    packed = pack_anbf(sample_acd, status=200, flags=1)
    assert len(packed) >= HEADER_SIZE

    header = unpack_header(packed)
    assert header.magic == MAGIC
    assert header.magic == b"ANB1"
    assert header.version == 1
    assert header.flags == 1
    assert header.status == 200
    assert header.block_offset >= HEADER_SIZE
    assert header.action_offset > header.block_offset
    assert header.delta_offset >= header.action_offset
    assert header.crc32 != 0


def test_roundtrip_serialization(sample_acd: Acd):
    t0 = time.time()
    packed = pack_anbf(sample_acd, status=200, timestamp=t0)
    unpacked = unpack_anbf(packed)

    assert unpacked.url == sample_acd.url
    assert unpacked.title == sample_acd.title
    assert len(unpacked.blocks) == 4
    assert len(unpacked.actions) == 4

    # Verify semantic blocks
    b1 = unpacked.blocks[0]
    assert b1.id == "b1"
    assert b1.type == "heading"
    assert b1.level == 1
    assert b1.text == "Welcome to Shop"
    assert b1.locator == "#hero > h1"

    b3 = unpacked.blocks[2]
    assert b3.id == "b3"
    assert b3.type == "table"
    assert len(b3.rows) == 3
    assert b3.rows[1] == ["Laptop", "$999", "12"]

    # Verify actions
    a1 = unpacked.actions[0]
    assert a1.ref == "a1"
    assert a1.kind == "navigate"
    assert a1.label == "Home"

    a3 = unpacked.actions[2]
    assert a3.ref == "a3"
    assert a3.kind == "drag"
    assert a3.run == "drag:path=2,0"

    # Verify metadata & conversion back to Acd
    acd_restored = anbf_to_acd(unpacked)
    assert acd_restored.url == sample_acd.url
    assert acd_restored.unavailable == sample_acd.unavailable


def test_crc32_integrity_verification(sample_acd: Acd):
    packed = bytearray(pack_anbf(sample_acd))

    # Tamper with a byte in the payload
    packed[len(packed) - 10] ^= 0xFF

    with pytest.raises(IntegrityError, match="ANBF CRC32 integrity check failed"):
        unpack_anbf(bytes(packed), verify_crc=True)

    # Should not raise when verify_crc=False
    doc = unpack_anbf(bytes(packed), verify_crc=False)
    assert doc.url == sample_acd.url


def test_delta_micro_patch_streaming(sample_acd: Acd):
    packed = pack_anbf(sample_acd)
    doc = unpack_anbf(packed)

    # Agent clicks a button -> page updates b2 text and inserts b5
    deltas = [
        AnbDelta(kind="mutate", target_id="b2", content="Flash sale now active!"),
        AnbDelta(kind="insert", target_id="b5", content="Coupon applied: -20%"),
    ]

    patch = pack_delta_patch(deltas)
    assert len(patch) < 300  # Compact micro-patch

    updated_doc = apply_delta_patch(doc, patch)
    assert len(updated_doc.deltas) == 2

    # b2 was mutated in place
    b2 = next(b for b in updated_doc.blocks if b.id == "b2")
    assert b2.text == "Flash sale now active!"

    # b5 was appended
    b5 = next(b for b in updated_doc.blocks if b.id == "b5")
    assert b5.text == "Coupon applied: -20%"


def test_agent_view_projection(sample_acd: Acd):
    packed = pack_anbf(sample_acd)
    doc = unpack_anbf(packed)
    view = anbf_to_agent_view(doc)

    assert "ANBF v1" in view
    assert "Sample Shop Page" in view
    assert "[b1 H1] Welcome to Shop" in view
    assert "[b3 TABLE 3 rows]" in view
    assert "| Laptop | $999 | 12 |" in view
    assert "[a2 activate] Add to Cart" in view


def test_density_comparison_vs_json(sample_acd: Acd):
    packed_binary = pack_anbf(sample_acd)
    json_bytes = sample_acd.model_dump_json().encode("utf-8")

    # Binary must be denser than full JSON serialization
    assert len(packed_binary) < len(json_bytes)
    assert len(packed_binary) > 0


def test_large_string_and_empty_document():
    # Empty ACD
    empty = Acd(url="https://empty.test", title="", blocks=[], actions=[])
    packed_empty = pack_anbf(empty)
    doc_empty = unpack_anbf(packed_empty)
    assert len(doc_empty.blocks) == 0
    assert len(doc_empty.actions) == 0

    # Very large block (> 65535 bytes) to test 32-bit string encoding
    big_text = "A" * 70000
    big_acd = Acd(
        url="https://large.test",
        title="Large",
        blocks=[AcdBlock(id="b1", type="text", text=big_text)],
    )
    packed_big = pack_anbf(big_acd)
    doc_big = unpack_anbf(packed_big)
    assert len(doc_big.blocks[0].text) == 70000
