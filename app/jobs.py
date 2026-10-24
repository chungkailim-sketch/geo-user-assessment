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
from app.industry import Industry, get_industry, load_industries
from app.models import JobStatus

log = logging.getLogger(__name__)

AUDIT_TTL_SECONDS = 24 * 3600
FINISHED_JOB_TTL_SECONDS = 3600
GENERIC_ERROR = "诊断过程中出现异常，请稍后重试。"


class QueueFullError(RuntimeError):
    """Raised when too many audits are already queued or running."""


@dataclass(frozen=True)
class JobState:
    job_id: str
    status: str = "queued"
    stage: str = "排队中"
    progress: int = 0
    error: str | None = None
    updated: float = field(default_factory=time.monotonic)


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
        stored = self.cache.get(f"audit:{job_id}", AUDIT_TTL_SECONDS)
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
            industry = get_industry(industry_name) or get_industry(brand["industry"]["detected"])
            benchmark = None
            if industry is not None:
                await progress(f"正在与行业标杆{industry.leader_zh}对比", 90)
                benchmark = await self.benchmark_for(brand, industry)
            self.cache.set(f"audit:{job_id}", self._finalise(job_id, brand, industry, benchmark))
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

    def _finalise(self, job_id: str, brand: dict, industry: Industry | None, benchmark: dict | None) -> dict:
        return {
            **brand,
            "audit_id": job_id,
            "industry": {
                **brand["industry"],
                "selected": industry.name if industry else None,
                "options": [i.name for i in load_industries()],
                "option_labels": {i.name: i.name_zh for i in load_industries()},
            },
            "benchmark": benchmark,
        }

    async def rebenchmark(self, audit_id: str, industry_name: str) -> dict | None:
        """Compare an existing audit with another industry's leader (serialised per audit)."""
        industry = get_industry(industry_name)
        if industry is None:
            return None
        lock = self._bench_locks.setdefault(audit_id, asyncio.Lock())
        try:
            async with lock:
                stored = self.cache.get(f"audit:{audit_id}", AUDIT_TTL_SECONDS)
                if stored is None:
                    return None
                benchmark = await self.benchmark_for(stored, industry)
                updated = {**stored, "industry": {**stored["industry"], "selected": industry.name}, "benchmark": benchmark}
                self.cache.set(f"audit:{audit_id}", updated, keep_created=True)
                return benchmark
        finally:
            if not lock.locked():
                self._bench_locks.pop(audit_id, None)
