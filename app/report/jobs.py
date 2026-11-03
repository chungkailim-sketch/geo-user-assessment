"""Background jobs for "send the full PDF report to my email": full audit → PDF → SMTP.

The site-level part of the full audit (crawl, technical/schema/E-E-A-T/platform analysis and the AI-written
narrative) is saved per website and reused by every report on that site while its quick audit is unchanged.
It is also pre-built in the background as soon as a quick audit finishes, so the PDF is usually ready by the
time the visitor asks for it.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field, replace

from app.cache import JsonCache
from app.config import Settings
from app.fetch.safe_fetch import FetchError, UnsafeURLError
from app.fullaudit.analysis import FullAuditor, with_audit_context
from app.jobs import AUDIT_KEY, AUDIT_TTL_SECONDS
from app.records import RunLog, RunRecord
from app.report.mailer import EmailError, build_message, send_report
from app.report.pdf import render_report_pdf

log = logging.getLogger(__name__)

FULL_KEY = "full-site:v1:"  # per website (registrable domain)
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
    runs: RunLog | None = None
    jobs: dict[str, ReportJob] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    _slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    _prewarm_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))  # never starves sends
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

    def _record(self, record: RunRecord) -> None:
        if self.runs is not None:
            self.runs.add(record)

    async def _report_data(self, audit_id: str, progress=None) -> tuple[dict, bool]:
        """The full report for this audit and whether the site analysis came from the saved copy.

        The site analysis is reused while it was built from the same quick audit (same `audited_at`); the
        report-specific parts (industry, benchmark, strategy) always come from this audit."""
        quick = self.cache.get(f"{AUDIT_KEY}{audit_id}", AUDIT_TTL_SECONDS)
        if quick is None:
            raise ReportRequestError("诊断报告已过期，请重新诊断后再试。")
        domain = quick["domain"]
        lock = self._locks.setdefault(domain, asyncio.Lock())
        if lock.locked() and progress:
            await progress("该网站的完整报告正在后台生成，请稍候", 40)
        async with lock:
            key = f"{FULL_KEY}{domain}"
            saved = self.cache.get(key, self.settings.full_report_cache_ttl_seconds)
            if saved and saved.get("quick_audited_at") == quick["audited_at"]:
                if progress:
                    await progress("已加载近期生成的完整诊断数据", 80)
                return with_audit_context(saved, quick), True
            report = await self.full_auditor.run(quick, progress)
            if report["ai_written"] or self.full_auditor.claude is None:  # retry a failed AI analysis next time
                self.cache.set(key, {**report, "quick_audited_at": quick["audited_at"]})
            return report, False

    def prewarm(self, audit_id: str) -> None:
        """Build the site analysis in the background right after a quick audit, ready for a later send."""
        if not (self.settings.email_enabled and self.settings.report_prewarm):
            return
        task = asyncio.create_task(self._prewarm(audit_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _prewarm(self, audit_id: str) -> None:
        started = time.monotonic()
        try:
            async with self._prewarm_slots:
                async with asyncio.timeout(self.settings.report_deadline_seconds):
                    report, cached = await self._report_data(audit_id)
            if not cached:
                self._record(RunRecord("prewarm", report["domain"], report["brand_name"], report["geo_score"],
                                       round(time.monotonic() - started, 1), False))
        except Exception as exc:  # a failed pre-build just means the send builds it instead
            log.warning("Report pre-build for audit %s failed: %s", audit_id, type(exc).__name__)

    async def _run(self, job_id: str, audit_id: str, email: str) -> None:
        async def progress(stage: str, pct: int) -> None:
            self._update(job_id, status="running", stage=stage, progress=pct)

        started = time.monotonic()
        report, cached = None, False
        try:
            async with self._slots:
                await progress("开始全面诊断", 5)
                async with asyncio.timeout(self.settings.report_deadline_seconds):
                    report, cached = await self._report_data(audit_id, progress)
                    await progress("正在生成PDF报告", 88)
                    pdf = await asyncio.to_thread(render_report_pdf, report, self.settings.pdf_font_path)
                    await progress("正在发送邮件", 94)
                    message = build_message(self.settings, email, report["brand_name"], report["url"],
                                            report["geo_score"], pdf)
                    await send_report(self.settings, message)
            self._update(job_id, status="sent", stage="报告已发送", progress=100)
            log.info("Full report for audit %s sent (%d bytes, saved analysis: %s)", audit_id, len(pdf), cached)
            self._record(RunRecord("report", report["domain"], report["brand_name"], report["geo_score"],
                                   round(time.monotonic() - started, 1), cached, "sent"))
        except (ReportRequestError, EmailError, UnsafeURLError, FetchError) as exc:
            self._update(job_id, status="error", stage="发送失败", error=str(exc))
            if report:
                self._record(RunRecord("report", report["domain"], report["brand_name"], report["geo_score"],
                                       round(time.monotonic() - started, 1), cached, "error"))
        except TimeoutError:
            self._update(job_id, status="error", stage="发送失败", error="报告生成耗时过长，请稍后重试。")
        except Exception:
            log.exception("Full report job %s failed", job_id)
            self._update(job_id, status="error", stage="发送失败", error=GENERIC_ERROR)
