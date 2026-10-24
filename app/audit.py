"""Site audit pipeline: crawl -> citability -> authority -> readiness score."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from app.cache import JsonCache
from app.config import Settings
from app.fetch.crawl import Crawler
from app.fetch.safe_fetch import SafeFetcher, site_root
from app.industry import detect_industry, industry_for_leader_domain, load_industries
from app.scoring.authority import build_authority
from app.scoring.citability import analyze_site
from app.scoring.claude_authority import assess_authority, identify_industry
from app.scoring.readiness import (
    AUTHORITY_WEIGHT,
    CITABILITY_WEIGHT,
    build_actions,
    build_findings,
    readiness_label,
    readiness_score,
)
from app.scoring.signals import BrandSignals, extract_signals, registrable_domain
from app.scoring.wikipedia import WikipediaChecker

log = logging.getLogger(__name__)

Progress = Callable[[str, int], Awaitable[None]]
SITE_KEY = "site:v4:"  # bump when result wording/shape changes so stale cache entries are ignored
DEGRADED_NOTE = "部分数据源暂时不可用，本次诊断结果可能不完整（未写入缓存）。"


async def _noop_progress(stage: str, pct: int) -> None:
    return None


def _industry_text(signals: BrandSignals, citability: dict) -> str:
    parts = [signals.title] * 3 + [signals.description] * 2 + signals.headings
    parts += [b["heading"] or "" for b in citability["top_passages"]]
    parts += [b["preview"] for b in citability["top_passages"]]
    return " ".join(parts)


class Auditor:
    def __init__(
        self,
        settings: Settings,
        cache: JsonCache,
        fetcher: SafeFetcher,
        api_client: httpx.AsyncClient,
        claude_client: Any | None,
    ):
        self.settings = settings
        self.cache = cache
        self.fetcher = fetcher
        self.api_client = api_client
        self.claude = claude_client
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent_audits)

    def _ttl_for(self, domain: str) -> int:
        leaders = {d for i in load_industries() for d in (i.leader_domain, *i.aliases)}
        return self.settings.leader_cache_ttl_seconds if domain in leaders else self.settings.user_cache_ttl_seconds

    def cached(self, domain: str) -> dict | None:
        return self.cache.get(f"{SITE_KEY}{domain}", self._ttl_for(domain))

    async def audit(self, url: str, progress: Progress = _noop_progress, refresh: bool = False) -> dict:
        """Audit a site's homepage, serialising concurrent audits of the same domain.

        Every domain has one canonical cached result built from its homepage, so a
        user can't replace a leader's baseline by auditing a thin sub-page. A
        `refresh` is honoured only when the cached result is older than
        `min_refresh_seconds`, and degraded results (an upstream API failed) are
        never cached.
        """
        root = site_root(url)
        domain = registrable_domain(root)
        lock = self._locks.setdefault(domain, asyncio.Lock())
        self._lock_users[domain] = self._lock_users.get(domain, 0) + 1
        try:
            async with lock:
                ttl = self.settings.min_refresh_seconds if refresh else self._ttl_for(domain)
                if hit := self.cache.get(f"{SITE_KEY}{domain}", ttl):
                    await progress("已加载近期诊断结果", 80)
                    return hit
                async with self._slots:
                    async with asyncio.timeout(self.settings.audit_deadline_seconds):
                        result = await self._run(root, progress)
                if not result["degraded"]:
                    self.cache.set(f"{SITE_KEY}{domain}", result)
                return result
        finally:
            self._lock_users[domain] -= 1
            if not self._lock_users[domain]:
                del self._lock_users[domain]
                self._locks.pop(domain, None)

    async def _run(self, url: str, progress: Progress) -> dict:
        await progress("正在抓取网站页面", 10)
        crawl = await Crawler(self.fetcher, self.settings.max_pages).crawl(url)
        domain = registrable_domain(url)  # identity = what the user entered, not geo-redirect targets
        final_domain = registrable_domain(crawl.homepage.url)

        await progress("正在评估内容可引用性", 35)
        citability = analyze_site([(page.url, page.text) for page in crawl.pages])

        await progress("正在核查维基百科与Wikidata收录", 50)
        signals = extract_signals(crawl.homepage.text, crawl.homepage.url, domain)
        leader_of = industry_for_leader_domain(domain)
        alt_domains = tuple(
            {final_domain, *(leader_of.aliases if leader_of else ())} - {domain}
        )
        wiki = await WikipediaChecker(self.api_client).check(signals.brand_name, domain, alt_domains)

        claude_result = None
        if self.claude is not None:
            await progress("正在联网检索品牌权威信号", 65)
            claude_result = await assess_authority(
                self.claude, self.settings.claude_model, signals.brand_name, domain, signals.to_dict()
            )
        authority = build_authority(signals, wiki, claude_result)
        degraded = bool(wiki.errors) or (self.claude is not None and claude_result is None)

        await progress("正在识别所属行业", 85)
        industry = await self._detect_industry(domain, signals, citability)

        score = readiness_score(citability["score"], authority["score"])
        return {
            "url": url,
            "final_url": crawl.homepage.url,
            "domain": domain,
            "brand_name": signals.brand_name,
            "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": score,
            "label": readiness_label(score),
            "weights": {"citability": CITABILITY_WEIGHT, "authority": AUTHORITY_WEIGHT},
            "citability": citability,
            "authority": authority,
            "findings": build_findings(citability, authority) + (
                [{"severity": "info", "text": DEGRADED_NOTE}] if degraded else []
            ),
            "actions": build_actions(citability, authority),
            "industry": industry,
            "crawl": {"pages": [p.url for p in crawl.pages], "failed_pages": len(crawl.errors)},
            "degraded": degraded,
        }

    async def _detect_industry(self, domain: str, signals: BrandSignals, citability: dict) -> dict:
        """Leader domains are definitive; otherwise Claude classifies (and may name an industry
        outside the catalogue), with keyword matching as the fallback when Claude is unavailable."""
        text = _industry_text(signals, citability)
        detection = detect_industry(domain, text)
        if detection and detection.method == "leader domain":
            return {"detected": detection.industry, "method": detection.method, "custom": None}
        if self.claude is not None:
            catalogue = [i.to_dict() for i in load_industries()]
            answer = await identify_industry(
                self.claude, self.settings.claude_model, catalogue, signals.brand_name, domain, text
            )
            if answer and answer.custom:
                return {"detected": answer.industry, "method": "ai custom", "custom": answer.custom.model_dump()}
            if answer:
                return {"detected": answer.industry, "method": "ai classification", "custom": None}
        detected, method = (detection.industry, detection.method) if detection else (None, "none")
        return {"detected": detected, "method": method, "custom": None}
