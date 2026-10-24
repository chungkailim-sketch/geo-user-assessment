"""Background audit jobs with progress tracking."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace

from app.audit import Auditor
from app.benchmark import compare
from app.cache import JsonCache
from app.fetch.safe_fetch import FetchError, UnsafeURLError
from app.industry import Industry, get_industry, load_industries, resolve_industry
from app.models import JobStatus
from app.scoring.claude_strategy import leader_for_industry, media_strategy
from app.scoring.readiness import PR_STRATEGY_ACTION

log = logging.getLogger(__name__)

AUDIT_TTL_SECONDS = 24 * 3600
AUDIT_KEY = "audit:v5:"  # bump with SITE_KEY when result wording/shape changes
MEDIA_KEY = "media:v1:"
LEADER_KEY = "industry-leader:v1:"
FINISHED_JOB_TTL_SECONDS = 3600
MAX_MANUAL_INDUSTRIES = 3
GENERIC_ERROR = "诊断过程中出现异常，请稍后重试。"


class QueueFullError(RuntimeError):
    """Raised when too many audits are already queued or running."""


class CustomIndustryError(ValueError):
    """A manually entered industry could not be resolved; the message is shown to the user."""


@dataclass(frozen=True)
class JobState:
    job_id: str
    status: str = "queued"
    stage: str = "排队中"
    progress: int = 0
    error: str | None = None
    updated: float = field(default_factory=time.monotonic)


def _customs(industry_block: dict) -> list[dict]:
    """Industries outside the catalogue attached to an audit: AI-detected first, then manual ones,
    de-duplicated by name (a typed industry can resolve to the one AI already detected)."""
    unique: dict[str, dict] = {}
    for custom in [industry_block.get("custom"), *industry_block.get("manual", [])]:
        if custom and custom["name"].lower() not in unique:
            unique[custom["name"].lower()] = custom
    return list(unique.values())


def industry_state(block: dict, selected: Industry | None) -> dict:
    """The industry block the results page renders: dropdown options, labels and the selection."""
    customs = _customs(block)
    catalogue = load_industries()
    return {
        **block,
        "selected": selected.name if selected else None,
        "options": [c["name"] for c in customs] + [i.name for i in catalogue],
        "option_labels": {**{i.name: i.name_zh for i in catalogue}, **{c["name"]: c["name_zh"] for c in customs}},
        "custom_names": [c["name"] for c in customs],
    }


def with_strategy(actions: list[dict], strategy: dict) -> list[dict]:
    return [a for a in actions if a.get("kind") != "strategy"] + [strategy]


@dataclass
class JobManager:
    auditor: Auditor
    cache: JsonCache
    max_active: int = 20
    jobs: dict[str, JobState] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    _bench_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _update(self, job_id: str, **changes) -> None:
        current = self.jobs[job_id]
        if "progress" in changes:  # never let the bar move backwards (e.g. cache hit, then benchmark)
            changes["progress"] = max(current.progress, changes["progress"])
        self.jobs[job_id] = replace(current, updated=time.monotonic(), **changes)

    def _sweep(self) -> None:
        cutoff = time.monotonic() - FINISHED_JOB_TTL_SECONDS
        self.jobs = {
            jid: job for jid, job in self.jobs.items()
            if job.status in ("queued", "running") or job.updated > cutoff
        }

    def active_count(self) -> int:
        return sum(1 for job in self.jobs.values() if job.status in ("queued", "running"))

    def start(self, url: str, industry: str | None, refresh: bool = False) -> str:
        self._sweep()
        if self.active_count() >= self.max_active:
            raise QueueFullError("当前诊断请求较多，请一分钟后再试。")
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = JobState(job_id=job_id)
        task = asyncio.create_task(self._run(job_id, url, industry, refresh))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job_id

    def status(self, job_id: str) -> JobStatus | None:
        stored = self.cache.get(f"{AUDIT_KEY}{job_id}", AUDIT_TTL_SECONDS)
        if stored is not None:
            return JobStatus(job_id=job_id, status="done", stage="诊断完成", progress=100, result=stored)
        state = self.jobs.get(job_id)
        if state is None:
            return None
        return JobStatus(job_id=state.job_id, status=state.status, stage=state.stage,
                         progress=state.progress, error=state.error)

    async def _run(self, job_id: str, url: str, industry_name: str | None, refresh: bool) -> None:
        async def progress(stage: str, pct: int) -> None:
            self._update(job_id, status="running", stage=stage, progress=pct)

        try:
            await progress("开始品牌诊断", 5)
            brand = await self.auditor.audit(url, progress, refresh=refresh)
            industry = get_industry(industry_name) or resolve_industry(
                brand["industry"]["detected"], *_customs(brand["industry"]))
            benchmark = None
            if industry is not None:
                await progress(f"正在与行业标杆{industry.leader_zh}对比", 88)
                benchmark = await self.benchmark_for(brand, industry)
            await progress("正在分析AI高频引用的媒体信源", 94)
            strategy = await self.strategy_for(brand, industry)
            result = {
                **brand,
                "audit_id": job_id,
                "industry": industry_state(brand["industry"], industry),
                "benchmark": benchmark,
                "actions": with_strategy(brand["actions"], strategy),
            }
            self.cache.set(f"{AUDIT_KEY}{job_id}", result)
            self._update(job_id, status="done", stage="诊断完成", progress=100)
        except (UnsafeURLError, FetchError) as exc:
            # These messages are written by us and never contain internal details.
            self._update(job_id, status="error", stage="诊断失败", error=str(exc))
        except TimeoutError:
            self._update(job_id, status="error", stage="诊断失败", error="诊断耗时过长，请稍后重试。")
        except Exception:
            log.exception("Audit job %s failed", job_id)
            self._update(job_id, status="error", stage="诊断失败", error=GENERIC_ERROR)

    async def benchmark_for(self, brand: dict, industry: Industry) -> dict:
        if industry.is_leader(brand["domain"]):
            return compare(brand, brand, industry)
        try:
            leader = await self.auditor.audit(industry.leader_url)
        except (UnsafeURLError, FetchError) as exc:
            log.warning("Leader audit for %s failed: %s", industry.leader, exc)
            return compare(brand, None, industry, reason=str(exc))
        except Exception:
            log.exception("Leader audit for %s failed", industry.leader)
            return compare(brand, None, industry, reason="暂时无法评估该行业标杆网站。")
        return compare(brand, leader, industry)

    async def strategy_for(self, brand: dict, industry: Industry | None) -> dict:
        """The PR/media strategy action, tailored to the industry's most AI-cited media when Claude is
        available; otherwise (or on failure) the generic strategy. Tailored results are cached."""
        claude = self.auditor.claude
        if claude is None or industry is None:
            return PR_STRATEGY_ACTION
        settings = self.auditor.settings
        key = f"{MEDIA_KEY}{brand['domain']}:{industry.name.lower()}"
        if hit := self.cache.get(key, settings.user_cache_ttl_seconds):
            return hit
        try:
            async with asyncio.timeout(settings.claude_timeout_seconds + 30):
                result = await media_strategy(
                    claude, settings.claude_model, brand["brand_name"], brand["domain"], industry.to_dict())
        except TimeoutError:
            log.warning("Media strategy for %s timed out", brand["domain"])
            result = None
        if result is None:
            return PR_STRATEGY_ACTION
        strategy = {
            **PR_STRATEGY_ACTION,
            "action": result.strategy,
            "tailored": True,
            "industry_zh": industry.name_zh,
            "market": result.market,
            "media": [source.model_dump() for source in result.sources],
        }
        self.cache.set(key, strategy)
        return strategy

    async def _manual_industry(self, stored: dict, text: str) -> tuple[Industry, dict | None]:
        """Resolve an industry the user typed: a catalogue industry, or a new one with its AI-found leader."""
        claude = self.auditor.claude
        if claude is None:
            raise CustomIndustryError("自定义行业需要启用AI（配置 ANTHROPIC_API_KEY）后才能识别其行业标杆。")
        key = f"{LEADER_KEY}{text.lower()}"
        answer = self.cache.get(key, self.auditor.settings.leader_cache_ttl_seconds)
        if answer is None:
            found = await leader_for_industry(
                claude, self.auditor.settings.claude_model, text,
                [i.to_dict() for i in load_industries()], stored["brand_name"], stored["domain"],
            )
            if found is None:
                raise CustomIndustryError("未能识别该行业的标杆品牌，请换个说法重试。")
            answer = {"industry": found.industry, "custom": found.custom.model_dump() if found.custom else None}
            self.cache.set(key, answer)
        if answer["custom"] is None:
            return get_industry(answer["industry"]), None
        return Industry.from_custom(answer["custom"]), answer["custom"]

    async def rebenchmark(
        self, audit_id: str, industry_name: str | None = None, custom_text: str | None = None
    ) -> dict | None:
        """Compare an existing audit with another industry's leader (serialised per audit).

        `custom_text` is an industry typed by the user ("其他"); it is resolved with Claude and
        remembered on the audit so it stays in the dropdown.
        """
        lock = self._bench_locks.setdefault(audit_id, asyncio.Lock())
        try:
            async with lock:
                stored = self.cache.get(f"{AUDIT_KEY}{audit_id}", AUDIT_TTL_SECONDS)
                if stored is None:
                    return None
                block = stored["industry"]
                if custom_text:
                    industry, custom = await self._manual_industry(stored, custom_text)
                    if custom is not None and custom["name"].lower() not in {c["name"].lower() for c in _customs(block)}:
                        others = block.get("manual", [])
                        block = {**block, "manual": [custom, *others][:MAX_MANUAL_INDUSTRIES]}
                else:
                    industry = resolve_industry(industry_name, *_customs(block))
                    if industry is None:
                        return None
                benchmark = await self.benchmark_for(stored, industry)
                strategy = await self.strategy_for(stored, industry)
                updated = {
                    **stored,
                    "industry": industry_state(block, industry),
                    "benchmark": benchmark,
                    "actions": with_strategy(stored["actions"], strategy),
                }
                self.cache.set(f"{AUDIT_KEY}{audit_id}", updated, keep_created=True)
                return benchmark
        finally:
            if not lock.locked():
                self._bench_locks.pop(audit_id, None)
