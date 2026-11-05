"""Multi-axis LLM benchmark via the pool proxy.

Axes: tokens in/out/cached (+ reasoning), cost (versioned provider list
prices), wall-clock latency, process RSS, concurrent sessions handled, and
agentic tool-loop completeness across task tiers.

Usage:
  .venv/bin/python scripts/bench_llm.py --smoke
  .venv/bin/python scripts/bench_llm.py --models deepseek-v4.1-flash,mercury-2,asi1-ultra --repeats 3
  .venv/bin/python scripts/bench_llm.py --sessions-probe 4

Raw-chat tasks use chat_llm directly; agentic tasks use ask() so the model
must drive browser tools. All token figures come from provider `usage`;
cost is computed from the dated table in webresearch.settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webresearch.agent import ask, chat_llm, message_text  # noqa: E402
from webresearch.settings import (  # noqa: E402
    PRICING_VERSION,
    PRICING_SOURCES,
    price_key,
    token_cost,
)

POOL_BASE_URL = "http://127.0.0.1:3001/v1"
POOL_API_KEY = "sk-local-pool-proxy"

# --- Task tiers ------------------------------------------------------------
# raw tiers: single chat completion (no tools). agentic tiers: ask() with
# browser tools. Each task has an optional `expect` substring used as a
# minimal success signal; answers are captured regardless.
RAW_TASKS = {
    "simple": {
        "prompt": "What is the capital of Australia? Answer in one word.",
        "expect": "canberra",
    },
    "reasoning": {
        "prompt": (
            "A train travels 120 km in 1.5 hours, then 180 km in 2 hours. "
            "What is its average speed in km/h for the whole trip? Show your math."
        ),
        "expect": "85",
    },
    "context": {
        "prompt": (
            "Read this passage and answer in one sentence: " + "The Amazon rainforest"
            " covers much of South America and is home to roughly 10% of known species. "
            + "Deforestation threatens its carbon storage. "
            + "Question: what does deforestation threaten?"
        ),
        "expect": "carbon",
    },
}

AGENTIC_TASKS = {
    "search": {
        "prompt": "Use web search to find what year the first iPhone was released. Answer with the year.",
        "expect": "2007",
    },
    "read": {
        "prompt": "Read https://example.com and tell me the page title.",
        "expect": "example",
    },
    "multi-hop": {
        "prompt": (
            "Search for the current CEO of Tesla, then delegate an independent read of "
            "https://example.com to confirm it loads. Report the CEO name and whether "
            "example.com loaded."
        ),
        # multi-hop needs a live session; coherence is judged by non-empty answer.
        "expect": None,
    },
}

RESULTS_DIR = Path(__file__).resolve().parents[1] / "benchmarks"


def rss_mb() -> float:
    current = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return (current / (1024 * 1024)) if sys.platform == "darwin" else (current / 1024)


def _usage_of(raw: dict) -> dict:
    usage = raw.get("usage") or {}
    details_p = usage.get("prompt_tokens_details") or {}
    details_c = usage.get("completion_tokens_details") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int(details_p.get("cached_tokens") or usage.get("cached_tokens") or 0)
    reasoning = int(details_c.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens") or (prompt + completion)),
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
    }


async def run_raw(model: str, prompt: str) -> dict:
    """One raw chat completion against the pool proxy."""
    t0 = time.perf_counter()
    rss_before = rss_mb()
    raw = await chat_llm(
        [{"role": "user", "content": prompt}],
        api_key=POOL_API_KEY,
        base_url=POOL_BASE_URL,
        model=model,
        provider="openai_compat",
        tools=None,
    )
    elapsed = time.perf_counter() - t0
    answer = message_text((raw.get("choices") or [{}])[0].get("message", {}).get("content"))
    usage = _usage_of(raw)
    return {
        "model": model,
        "prompt": prompt[:200],
        "answer": answer[:400],
        "usage": usage,
        "cost_usd": token_cost(
            model, prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            cached_tokens=usage["cached_tokens"],
        ),
        "wall_s": round(elapsed, 3),
        "rss_delta_mb": round(rss_mb() - rss_before, 3),
    }


async def run_agentic(model: str, prompt: str, max_rounds: int = 8) -> dict:
    """One agentic ask() against the pool: tools live, rounds counted."""
    t0 = time.perf_counter()
    rss_before = rss_mb()
    result = await ask(prompt, model=model, max_rounds=max_rounds, api_key=POOL_API_KEY, base_url=POOL_BASE_URL)
    elapsed = time.perf_counter() - t0
    u = result.usage
    return {
        "model": model,
        "prompt": prompt[:200],
        "answer": result.answer[:400],
        "rounds": result.rounds,
        "tool_calls": len(result.tool_calls),
        "truncated": result.truncated,
        "usage": {
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "total_tokens": u.total_tokens,
            "cached_tokens": u.cached_tokens,
            "reasoning_tokens": u.reasoning_tokens,
        },
        "cost_usd": token_cost(
            model, prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            cached_tokens=u.cached_tokens,
        ),
        "wall_s": round(elapsed, 3),
        "rss_delta_mb": round(rss_mb() - rss_before, 3),
    }


def score(run: dict, expect: str | None) -> bool:
    if expect is None:
        return bool(run.get("answer", "").strip())
    return expect in (run.get("answer") or "").lower()


async def run_all(models: list[str], repeats: int, tiers: str) -> list[dict]:
    runs: list[dict] = []
    tasks: list[tuple[str, dict]] = []
    if tiers in ("all", "raw"):
        tasks += [(f"raw:{name}", t) for name, t in RAW_TASKS.items()]
    if tiers in ("all", "agentic"):
        tasks += [(f"agentic:{name}", t) for name, t in AGENTIC_TASKS.items()]
    for model in models:
        for task_name, task in tasks:
            for i in range(repeats):
                kind = task_name.split(":")[0]
                try:
                    if kind == "raw":
                        run = await run_raw(model, task["prompt"])
                    else:
                        run = await run_agentic(model, task["prompt"])
                except Exception as exc:  # keep going; record the failure mode
                    run = {
                        "model": model, "prompt": task["prompt"][:200],
                        "answer": "", "error": f"{type(exc).__name__}: {exc}"[:300],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                                  "total_tokens": 0, "cached_tokens": 0, "reasoning_tokens": 0},
                        "cost_usd": None, "wall_s": 0.0, "rss_delta_mb": 0.0,
                    }
                run["task"] = task_name
                run["repeat"] = i
                run["success"] = score(run, task["expect"]) if "error" not in run else False
                runs.append(run)
                print(f"  {model} {task_name} [{i}] success={run['success']} "
                      f"tokens={run['usage']['total_tokens']} wall={run['wall_s']}s "
                      f"cost={run['cost_usd']}")
    return runs


async def probe_sessions(n: int, model: str, prompt: str) -> dict:
    """Run n ask() calls concurrently; report success count and wall time."""
    async def one(i: int) -> bool:
        try:
            await ask(prompt, model=model, max_rounds=4, api_key=POOL_API_KEY, base_url=POOL_BASE_URL)
            return True
        except Exception:
            return False

    t0 = time.perf_counter()
    results = await asyncio.gather(*(one(i) for i in range(n)))
    return {"model": model, "sessions": n, "succeeded": sum(results),
            "wall_s": round(time.perf_counter() - t0, 3)}


def aggregate(runs: list[dict]) -> dict:
    by_model: dict[str, list[dict]] = {}
    for run in runs:
        by_model.setdefault(run["model"], []).append(run)
    summary = {}
    for model, rows in by_model.items():
        costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
        walls = [r["wall_s"] for r in rows if r.get("wall_s")]
        totals = [r["usage"]["total_tokens"] for r in rows]
        ok = [r for r in rows if r.get("success")]
        summary[model] = {
            "runs": len(rows),
            "success_rate": round(len(ok) / len(rows), 3) if rows else 0.0,
            "tokens_total": sum(totals),
            "tokens_p50": statistics.median(totals) if totals else None,
            "tokens_p95": sorted(totals)[int(0.95 * (len(totals) - 1))] if totals else None,
            "cached_tokens_total": sum(r["usage"]["cached_tokens"] for r in rows),
            "reasoning_tokens_total": sum(r["usage"]["reasoning_tokens"] for r in rows),
            "cost_usd_total": round(sum(costs), 6) if costs else None,
            "cost_usd_per_success": round(sum(costs) / len(ok), 6) if costs and ok else None,
            "wall_s_p50": round(statistics.median(walls), 3) if walls else None,
            "wall_s_p95": round(sorted(walls)[int(0.95 * (len(walls) - 1))], 3) if walls else None,
            "rss_delta_mb_max": max((r.get("rss_delta_mb", 0.0) for r in rows), default=0.0),
            "pricing_key": price_key(model),
        }
    return summary


def render_markdown(summary: dict, pricing_version: str) -> str:
    lines = [
        f"# LLM benchmark (pricing {pricing_version})",
        "",
        "| model | runs | success | tokens p50 | tokens p95 | cached | "
        "wall p50 (s) | wall p95 (s) | cost $ total | cost $ / success | rss max MB |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, s in sorted(summary.items()):
        cost_total = f"{s['cost_usd_total']:.4f}" if s["cost_usd_total"] is not None else "n/a"
        cost_ok = f"{s['cost_usd_per_success']:.4f}" if s["cost_usd_per_success"] is not None else "n/a"
        lines.append(
            f"| {model} | {s['runs']} | {s['success_rate']:.0%} | "
            f"{s['tokens_p50']} | {s['tokens_p95']} | {s['cached_tokens_total']} | "
            f"{s['wall_s_p50']} | {s['wall_s_p95']} | {cost_total} | {cost_ok} | "
            f"{s['rss_delta_mb_max']} |"
        )
    lines += [
        "",
        "Cost is computed from provider list prices (dated table in "
        "`webresearch/settings.py`); `n/a` means no verified price — tokens "
        "are reported, cost is not invented.",
        "",
        "Sources: " + "; ".join(f"{k}: {v}" for k, v in PRICING_SOURCES.items()),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="deepseek-v4.1-flash,mercury-2,asi1-ultra")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tiers", choices=["all", "raw", "agentic"], default="all")
    parser.add_argument("--sessions-probe", type=int, default=0)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.smoke:
        models, args.repeats, args.tiers = models[:1], 1, "raw"

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    print(f"Benchmarking {models} (pricing {PRICING_VERSION})")
    runs = asyncio.run(run_all(models, args.repeats, args.tiers))
    summary = aggregate(runs)

    sessions = None
    if args.sessions_probe:
        sessions = asyncio.run(probe_sessions(args.sessions_probe, models[0], RAW_TASKS["simple"]["prompt"]))
        print(f"Sessions probe: {sessions}")

    payload = {
        "pricing_version": PRICING_VERSION,
        "pricing_sources": PRICING_SOURCES,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": POOL_BASE_URL,
        "summary": summary,
        "sessions_probe": sessions,
        "runs": runs,
    }
    json_path = RESULTS_DIR / f"bench-llm-{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    md_path = RESULTS_DIR / f"bench-llm-{stamp}.md"
    md_path.write_text(render_markdown(summary, PRICING_VERSION))
    print(f"\nWrote {json_path}\nWrote {md_path}\n")
    print(render_markdown(summary, PRICING_VERSION))


if __name__ == "__main__":
    main()
