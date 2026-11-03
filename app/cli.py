"""Command-line helpers.

    python -m app.cli audit <url>      # run one audit and print the summary
    python -m app.cli history [--limit N] [--kind audit|report|prewarm]   # recent runs
"""

import argparse
import asyncio
import json
import logging

import httpx

from app.audit import Auditor
from app.cache import JsonCache
from app.config import get_settings
from app.fetch.safe_fetch import SafeFetcher
from app.main import build_claude_client
from app.records import RunLog


async def _with_auditor(action):
    settings = get_settings()
    cache = JsonCache(settings.cache_path)
    fetcher = SafeFetcher(settings)
    async with httpx.AsyncClient(headers={"User-Agent": settings.api_user_agent}, timeout=15) as api:
        auditor = Auditor(settings, cache, fetcher, api, build_claude_client(settings))
        try:
            return await action(auditor)
        finally:
            await fetcher.aclose()
            cache.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_cmd = sub.add_parser("audit")
    audit_cmd.add_argument("url")
    history_cmd = sub.add_parser("history")
    history_cmd.add_argument("--limit", type=int, default=30)
    history_cmd.add_argument("--kind", choices=("audit", "report", "prewarm"))
    args = parser.parse_args()

    if args.command == "history":
        print_history(RunLog(get_settings().cache_path), args.limit, args.kind)

    if args.command == "audit":
        async def run(auditor: Auditor):
            result = await auditor.audit(args.url, refresh=True)
            summary = {k: result[k] for k in ("brand_name", "domain", "score", "label", "industry")}
            summary["citability"] = result["citability"]["score"]
            summary["authority"] = result["authority"]["score"]
            summary["authority_confidence"] = result["authority"]["confidence"]
            print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output

        asyncio.run(_with_auditor(run))


def print_history(runs: RunLog, limit: int = 30, kind: str | None = None) -> None:
    from datetime import datetime

    for r in runs.recent(limit, kind):
        when = datetime.fromtimestamp(r.created).strftime("%Y-%m-%d %H:%M")
        score = "-" if r.score is None else f"{r.score:.1f}"
        source = "saved" if r.cached else "fresh"
        print(f"{when}  {r.kind:<8} {r.status:<6} {source:<6} {r.seconds:>6.1f}s  {score:>5}  {r.domain}  {r.brand}")  # noqa: T201


if __name__ == "__main__":
    main()
