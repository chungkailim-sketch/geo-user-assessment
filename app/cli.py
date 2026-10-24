"""Command-line helpers.

    python -m app.cli warm-leaders     # pre-compute and cache all industry leader audits
    python -m app.cli audit <url>      # run one audit and print the summary
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
from app.industry import load_industries
from app.main import build_claude_client


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


async def warm_leaders(auditor: Auditor) -> None:
    for industry in load_industries():
        try:
            result = await auditor.audit(industry.leader_url, refresh=True)
            logging.info("%-22s %-12s score=%s", industry.name, industry.leader, result["score"])
        except Exception as exc:
            logging.error("%s (%s) failed: %s", industry.leader, industry.leader_url, exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("warm-leaders")
    audit_cmd = sub.add_parser("audit")
    audit_cmd.add_argument("url")
    args = parser.parse_args()

    if args.command == "warm-leaders":
        asyncio.run(_with_auditor(warm_leaders))
    else:
        async def run(auditor: Auditor):
            result = await auditor.audit(args.url, refresh=True)
            summary = {k: result[k] for k in ("brand_name", "domain", "score", "label", "industry")}
            summary["citability"] = result["citability"]["score"]
            summary["authority"] = result["authority"]["score"]
            summary["authority_confidence"] = result["authority"]["confidence"]
            print(json.dumps(summary, indent=2))  # noqa: T201 - CLI output

        asyncio.run(_with_auditor(run))


if __name__ == "__main__":
    main()
