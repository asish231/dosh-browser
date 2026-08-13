"""Stress-test Google search path: N queries in one burst."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

from webresearch.fetch import DEFAULT_UA
from webresearch.search import search

TOPICS = [
    "hotels", "flights", "restaurants", "museums", "beaches",
    "hiking", "coffee", "laptops", "python", "react",
]


async def one(client: httpx.AsyncClient, sem: asyncio.Semaphore, query: str, engine: str, row: list) -> None:
    async with sem:
        t0 = time.perf_counter()
        try:
            result = await search(query, client=client, max_hits=5, engine=engine)  # type: ignore[arg-type]
            ms = (time.perf_counter() - t0) * 1000
            row.append(
                {
                    "q": query,
                    "ok": bool(result.hits),
                    "engine": result.engine,
                    "hits": len(result.hits),
                    "ms": round(ms, 1),
                    "err": None,
                }
            )
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000
            row.append(
                {
                    "q": query,
                    "ok": False,
                    "engine": "",
                    "hits": 0,
                    "ms": round(ms, 1),
                    "err": type(exc).__name__ + ": " + str(exc)[:160],
                }
            )


async def run(n: int, concurrency: int, engine: str, out: Path) -> dict:
    queries = [f"{TOPICS[i % len(TOPICS)]} query {i:04d}" for i in range(n)]
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []
    t0 = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"},
        limits=httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency),
    ) as client:
        await asyncio.gather(*[one(client, sem, q, engine, rows) for q in queries])
    elapsed = time.perf_counter() - t0

    ok = [r for r in rows if r["ok"]]
    fail = [r for r in rows if not r["ok"]]
    google = [r for r in rows if r["engine"] == "google"]
    ddg = [r for r in rows if r["engine"] == "ddg"]
    times = [r["ms"] for r in rows]
    times.sort()

    def pct(p: float) -> float:
        if not times:
            return 0.0
        i = min(len(times) - 1, max(0, int(round((p / 100) * (len(times) - 1)))))
        return times[i]

    summary = {
        "n": n,
        "concurrency": concurrency,
        "engine_requested": engine,
        "elapsed_s": round(elapsed, 2),
        "qps": round(n / elapsed, 2) if elapsed else 0,
        "ok": len(ok),
        "fail": len(fail),
        "ok_pct": round(100 * len(ok) / n, 2) if n else 0,
        "google": len(google),
        "ddg": len(ddg),
        "empty_engine": len(rows) - len(google) - len(ddg),
        "hits_total": sum(r["hits"] for r in rows),
        "ms_avg": round(statistics.mean(times), 1) if times else 0,
        "ms_p50": round(pct(50), 1),
        "ms_p95": round(pct(95), 1),
        "ms_p99": round(pct(99), 1),
        "ms_max": round(max(times), 1) if times else 0,
        "error_types": {},
    }
    for r in fail:
        key = (r["err"] or "no_hits").split(":")[0]
        if r["err"] is None:
            key = "no_hits"
        summary["error_types"][key] = summary["error_types"].get(key, 0) + 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Burst-search stress test")
    parser.add_argument("-n", type=int, default=2000)
    parser.add_argument("-c", "--concurrency", type=int, default=24)
    parser.add_argument("--engine", choices=["auto", "google", "ddg"], default="google")
    parser.add_argument("-o", "--out", default="tmp/stress_search.json")
    args = parser.parse_args()
    summary = asyncio.run(run(args.n, args.concurrency, args.engine, Path(args.out)))
    print(json.dumps(summary, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
