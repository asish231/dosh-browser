"""Fix 4 tests: Python-lane Parena integration (projection + spill on Python pages)."""

import json
import os

import pytest


def _node(tag, text="", children=None, attrs=None):
    if tag == "#text":
        return {"t": "#text", "x": text}
    return {"t": tag, "a": attrs or {}, "c": children or []}


def _tree(text):
    return {
        "t": "document", "a": {},
        "c": [_node("html", children=[_node("body", children=[
            _node("p", children=[_node("#text", text)])
        ])])],
    }


def test_load_parena_projects_tree(monkeypatch):
    monkeypatch.delenv("WR_PARENA_SPILL_MB", raising=False)
    from doshgentv8 import DoshContext

    vm = DoshContext()
    tree = _tree("hello parena")
    n = vm.load_parena(json.dumps(tree, ensure_ascii=False))
    assert n == 5, f"document+html+body+p+#text = 5 nodes, got {n}"
    ram, spill, text = vm.parena_stats()
    assert text == len("hello parena")
    assert ram > 0
    assert spill == 0


def test_python_lane_external_reporting_and_spill(monkeypatch):
    """End-to-end Python lane: projection -> external reporting -> auto-spill.

    The 2MB arena exceeds the 1MB watermark, so with the knob set the next
    eval must spill the cold tail, releasing RAM observably (ram drops,
    spill grows, external report follows).
    """
    monkeypatch.setenv("WR_PARENA_SPILL_MB", "1")
    from doshgentv8 import DoshContext

    vm = DoshContext()
    big = "z" * (2 * 1024 * 1024)
    n = vm.load_parena(json.dumps(_tree(big), ensure_ascii=False))
    assert n == 5

    # Before any eval: no spill yet (the knob acts at eval time), full RAM.
    ram0, spill0, text0 = vm.parena_stats()
    assert spill0 == 0, "load_parena itself must not spill"
    assert text0 >= 2 * 1024 * 1024, "arena must hold the 2MB text"

    # The eval runs with the knob set: the 2MB arena exceeds the 1MB
    # watermark, so the cold tail must offload and the pacer must see
    # the post-spill (reduced) RAM.
    vm.eval("1 + 1")
    ram1, spill1, text1 = vm.parena_stats()
    assert spill1 > 0, "auto-spill must fire past the watermark"
    assert ram1 < ram0, f"spill must release RAM: {ram0} -> {ram1}"
    assert text1 == text0, "logical text must be unchanged by the spill"
    ext1 = vm.external_reported()
    assert ext1 > 0, "V8's pacer must see the host arena"
    assert ext1 <= ram0, "the reported external bytes must reflect the spill"


def test_hydration_path_populates_parena(tmp_path, monkeypatch):
    """The full page-load path: run_scripts projects the parsed document
    into the engine's Parena (info carries the node count)."""
    monkeypatch.delenv("WR_PARENA_SPILL_MB", raising=False)
    import asyncio

    import httpx

    from webresearch import cache
    from webresearch.cache import HttpCache
    from webresearch.engine.runtime import run_scripts

    monkeypatch.setattr(cache, "CACHE", HttpCache())

    html = ('<html><head><title>Proj</title></head><body>'
            '<p>alpha</p><p>beta</p>'
            '</body></html>')

    async def main():
        async with httpx.AsyncClient() as client:
            return await run_scripts(html, "https://proj.test/", client, force=True)

    _html, info = asyncio.run(main())
    assert info.get("parena_nodes", 0) >= 5, (
        f"the hydration path must project the document, got parena_nodes={info.get('parena_nodes')}"
    )
