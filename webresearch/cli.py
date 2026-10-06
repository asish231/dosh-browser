from __future__ import annotations

import argparse
import asyncio
import sys

from webresearch.fetch import make_client
from webresearch.read import captcha_code, read
from webresearch.search import search


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="webresearch",
        description="LLM-native browser. QuickJS mini-DOM, not Chromium.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="HTTP API + OpenAPI at /docs")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4747)

    read_cmd = sub.add_parser("read", help="Open a URL as an LLM page model")
    read_cmd.add_argument("url")
    read_cmd.add_argument("--json", action="store_true")
    read_cmd.add_argument("--max-chars", type=int, default=8000)
    read_cmd.add_argument("--js", choices=["auto", "always", "never"], default="auto")

    search_cmd = sub.add_parser("search", help="Google search (DuckDuckGo fallback)")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--json", action="store_true")
    search_cmd.add_argument("--engine", choices=["auto", "google", "ddg"], default="auto")

    captcha_cmd = sub.add_parser(
        "captcha",
        help="Inspect challenge frames, controls, tiles, and sprite metadata",
    )
    captcha_cmd.add_argument("url")
    captcha_cmd.add_argument("--json", action="store_true")
    captcha_cmd.add_argument("--code", action="store_true", help="Dump widget/iframe HTML")
    captcha_cmd.add_argument("--js", choices=["auto", "always", "never"], default="auto")

    ask_cmd = sub.add_parser("ask", help="Let an LLM browse with these tools")
    ask_cmd.add_argument("prompt")
    ask_cmd.add_argument(
        "--model",
        default="glm",
        help="glm (GLM-4.7-Flash, default) | mercury | auto (fail over on rate limits)",
    )
    ask_cmd.add_argument("--json", action="store_true")
    ask_cmd.add_argument("--max-rounds", type=int, default=8)
    ask_cmd.add_argument("--think", action="store_true", help="Enable model reasoning (slower)")
    ask_cmd.add_argument("--quiet", action="store_true", help="Hide tool-call progress")

    models_cmd = sub.add_parser("models", help="List configured LLMs and ping them")
    models_cmd.add_argument("--json", action="store_true")
    models_cmd.add_argument("--ping", action="store_true", help="Send a live test request")

    sub.add_parser("mcp", help="Run Model Context Protocol (MCP) server over stdio")

    profile_cmd = sub.add_parser("profile", help="Manage persistent profiles and cookies")
    p_sub = profile_cmd.add_subparsers(dest="action", required=True)
    p_sub.add_parser("list", help="List available profiles")
    p_import = p_sub.add_parser("import", help="Import cookies from cookies.txt (Netscape format)")
    p_import.add_argument("file", help="Path to cookies.txt file")
    p_import.add_argument("--name", default="default", help="Profile name")
    p_export = p_sub.add_parser("export", help="Export cookies in cookies.txt format")
    p_export.add_argument("--name", default="default", help="Profile name")
    p_clear = p_sub.add_parser("clear", help="Clear cookies in profile")
    p_clear.add_argument("--name", default="default", help="Profile name")

    args = parser.parse_args(argv)

    if args.cmd == "mcp":
        from webresearch.mcp import run_mcp_server

        run_mcp_server()
        return

    if args.cmd == "profile":
        from webresearch.profile import Profile, list_profiles

        if args.action == "list":
            profiles = list_profiles()
            if not profiles:
                print("No profiles found (default directory ~/.dosh-browser/profiles)")
            else:
                for p in profiles:
                    print(f"- {p}")
            return
        prof = Profile(name=args.name)
        if args.action == "import":
            with open(args.file, "r", encoding="utf-8") as f:
                count = prof.import_netscape(f.read())
            print(f"Imported {count} cookies into profile '{args.name}'")
            return
        if args.action == "export":
            print(prof.export_netscape())
            return
        if args.action == "clear":
            prof.clear()
            print(f"Cleared cookies in profile '{args.name}'")
            return

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("webresearch.server:app", host=args.host, port=args.port, reload=False)
        return

    if args.cmd in {"ask", "models"}:
        runner = _run_ask if args.cmd == "ask" else _run_models
        try:
            asyncio.run(runner(args))
        except KeyboardInterrupt:
            print("cancelled", file=sys.stderr)
            sys.exit(130)
        return

    asyncio.run(_run_cli(args))


async def _run_models(args: argparse.Namespace) -> None:
    import json as _json

    from webresearch.agent import LlmError, chat_llm, message_text
    from webresearch.settings import available_models, llm_config

    rows = available_models()
    if args.ping:
        for row in rows:
            if not row["configured"]:
                row["status"] = "no api key"
                continue
            cfg = llm_config(str(row["id"]))
            try:
                raw = await chat_llm(
                    [{"role": "user", "content": "Reply with the single word: pong"}],
                    api_key=cfg["api_key"],
                    base_url=cfg["base_url"],
                    model=cfg["model"],
                    provider=cfg["provider"],
                    tools=None,
                    retries=1,
                    max_tokens=200,
                )
                reply = message_text((raw.get("choices") or [{}])[0].get("message", {}).get("content"))
                row["status"] = f"ok: {reply.strip()[:40]}"
            except LlmError as exc:
                row["status"] = f"unavailable: {exc}"

    if args.json:
        print(_json.dumps(rows, indent=2))
        return
    for row in rows:
        mark = "✓" if row["configured"] else "·"
        line = f"{mark} {row['id']:<16} {row['vendor']:<10} {row['label']}"
        if row.get("status"):
            line += f"\n    {row['status']}"
        print(line)


async def _run_ask(args: argparse.Namespace) -> None:
    from webresearch.agent import LlmError, LlmRateLimitError
    from webresearch.agent import ask as llm_ask

    def on_event(kind: str, payload: dict) -> None:
        if args.quiet or args.json:
            return
        if kind == "tool":
            extra = payload.get("arguments") or {}
            hint = extra.get("q") or extra.get("url") or extra.get("ref") or ""
            print(f"→ {payload.get('name')} {hint}".rstrip(), file=sys.stderr)
        elif kind == "fallback":
            print(f"! {payload['from']} rate limited, switching to {payload['to']}", file=sys.stderr)
        elif kind == "model" and payload.get("tool_calls"):
            print(
                f"· round {payload['round']} ({payload['model']}): "
                f"{payload['tool_calls']} tool call(s)",
                file=sys.stderr,
            )

    try:
        result = await llm_ask(
            args.prompt,
            model=args.model,
            max_rounds=max(1, min(args.max_rounds, 12)),
            thinking=args.think,
            on_event=on_event,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except LlmRateLimitError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    except LlmError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if args.json:
        print(result.model_dump_json(indent=2))
        return
    print(result.answer)
    if not args.quiet:
        print(
            f"\n[{result.model} · {result.rounds} round(s) · "
            f"{result.usage.total_tokens} tokens]",
            file=sys.stderr,
        )


async def _run_cli(args: argparse.Namespace) -> None:
    async with make_client() as client:
        if args.cmd == "read":
            page = await read(args.url, client=client, max_chars=args.max_chars, js=args.js)
            print(page.model_dump_json(indent=2) if args.json else page.view)
            return
        if args.cmd == "search":
            result = await search(args.query, client=client, engine=args.engine)
            print(result.model_dump_json(indent=2) if args.json else result.view)
            return
        if args.cmd == "captcha":
            if args.code:
                dump = await captcha_code(args.url, client=client, js=args.js)
                print(dump.model_dump_json(indent=2) if args.json else dump.code)
                return
            page = await read(args.url, client=client, js=args.js)
            report = page.captcha
            if report is None:
                print("CAPTCHA: not detected")
            else:
                print(report.model_dump_json(indent=2) if args.json else report.view)
            return
    sys.exit(1)


if __name__ == "__main__":
    main()
