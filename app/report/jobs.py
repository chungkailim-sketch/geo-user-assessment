"""Background jobs for "send the full PDF report to my email": full audit → PDF → SMTP."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace

from app.cache import JsonCache
from app.config import Settings
from app.fetch.safe_fetch import FetchError, UnsafeURLError
from app.fullaudit.analysis import FullAuditor
from app.jobs import AUDIT_KEY, AUDIT_TTL_SECONDS
from app.report.mailer import EmailError, build_message, send_report
from app.report.pdf import render_report_pdf

log = logging.getLogger(__name__)

FULL_KEY = "full-report:v1:"
SENDS_KEY = "report-sends:v1:"
FINISHED_TTL_SECONDS = 3600
GENERIC_ERROR = "报告生成失败，请稍后重试。"


class ReportRequestError(ValueError):
    """The request can't be accepted; the message is shown to the user."""


@dataclass(frozen=True)
class ReportJob:
    job_id: str
    status: str = "queued"  # queued | running | sent | error
    stage: str = "排队中"
    progress: int = 0
    error: str | None = None
    updated: float = field(default_factory=time.monotonic)


@dataclass
class ReportManager:
    settings: Settings
    cache: JsonCache
    full_auditor: FullAuditor
    jobs: dict[str, ReportJob] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    _slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def _update(self, job_id: str, **changes) -> None:
        current = self.jobs[job_id]
        if "progress" in changes:
            changes["progress"] = max(current.progress, changes["progress"])
        self.jobs[job_id] = replace(current, updated=time.monotonic(), **changes)

    def _sweep(self) -> None:
        cutoff = time.monotonic() - FINISHED_TTL_SECONDS
        self.jobs = {k: j for k, j in self.jobs.items() if j.status in ("queued", "running") or j.updated > cutoff}

    def start(self, audit_id: str, email: str) -> str:
        if not self.settings.email_enabled:
            raise ReportRequestError("邮件发送服务尚未配置，请稍后再试或联系罗德业务经理。")
        if self.cache.get(f"{AUDIT_KEY}{audit_id}", AUDIT_TTL_SECONDS) is None:
            raise ReportRequestError("未找到该诊断报告，请重新诊断后再试。")
        sends_key = f"{SENDS_KEY}{audit_id}"
        sends = (self.cache.get(sends_key, AUDIT_TTL_SECONDS) or {}).get("count", 0)
        if sends >= self.settings.report_max_sends_per_audit:
            raise ReportRequestError("该报告的发送次数已达上限。")
        self._sweep()
        if sum(1 for j in self.jobs.values() if j.status in ("queued", "running")) >= 10:
            raise ReportRequestError("当前报告生成请求较多，请稍后再试。")
        self.cache.set(sends_key, {"count": sends + 1}, keep_created=True)
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = ReportJob(job_id=job_id)
        task = asyncio.create_task(self._run(job_id, audit_id, email))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return job_id

    def status(self, job_id: str) -> ReportJob | None:
        return self.jobs.get(job_id)

    async def _report_data(self, audit_id: str, progress) -> dict:
        """The full audit for this quick audit, built once and reused for repeat sends."""
        lock = self._locks.setdefault(audit_id, asyncio.Lock())
        async with lock:
            key = f"{FULL_KEY}{audit_id}"
            if cached := self.cache.get(key, self.settings.user_cache_ttl_seconds):
                return cached
            quick = self.cache.get(f"{AUDIT_KEY}{audit_id}", AUDIT_TTL_SECONDS)
            if quick is None:
                raise ReportRequestError("诊断报告已过期，请重新诊断后再试。")
            report = await self.full_auditor.run(quick, progress)
            if report["ai_written"] or self.full_auditor.claude is None:  # retry a failed AI analysis next time
                self.cache.set(key, report)
            return report

    async def _run(self, job_id: str, audit_id: str, email: str) -> None:
        async def progress(stage: str, pct: int) -> None:
            self._update(job_id, status="running", stage=stage, progress=pct)

        try:
            async with self._slots:
                await progress("开始全面诊断", 5)
                async with asyncio.timeout(self.settings.report_deadline_seconds):
                    report = await self._report_data(audit_id, progress)
                    await progress("正在生成PDF报告", 88)
                    pdf = await asyncio.to_thread(render_report_pdf, report, self.settings.pdf_font_path)
                    await progress("正在发送邮件", 94)
                    message = build_message(self.settings, email, report["brand_name"], report["url"],
                                            report["geo_score"], pdf)
                    await send_report(self.settings, message)
            self._update(job_id, status="sent", stage="报告已发送", progress=100)
            log.info("Full report for audit %s sent (%d bytes)", audit_id, len(pdf))
        except (ReportRequestError, EmailError, UnsafeURLError, FetchError) as exc:
            self._update(job_id, status="error", stage="发送失败", error=str(exc))
        except TimeoutError:
            self._update(job_id, status="error", stage="发送失败", error="报告生成耗时过长，请稍后重试。")
        except Exception:
            log.exception("Full report job %s failed", job_id)
            self._update(job_id, status="error", stage="发送失败", error=GENERIC_ERROR)
