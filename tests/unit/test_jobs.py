import asyncio
import time

import pytest

from app.cache import JsonCache
from app.jobs import FINISHED_JOB_TTL_SECONDS, JobManager, JobState, QueueFullError

pytestmark = pytest.mark.unit


class SlowAuditor:
    def __init__(self):
        self.release = asyncio.Event()

    async def audit(self, url, progress=None, refresh=False):
        await progress("Loaded cached result", 80)
        await self.release.wait()
        raise TimeoutError()


async def test_queue_cap_and_timeout_message(tmp_path):
    auditor = SlowAuditor()
    jobs = JobManager(auditor=auditor, cache=JsonCache(tmp_path / "c.db"), max_active=1)
    job_id = jobs.start("https://a.example/", None)
    with pytest.raises(QueueFullError):
        jobs.start("https://b.example/", None)
    await asyncio.sleep(0)
    auditor.release.set()
    await asyncio.gather(*jobs.tasks)
    status = jobs.status(job_id)
    assert status.status == "error" and "too long" in status.error


async def test_progress_never_moves_backwards(tmp_path):
    jobs = JobManager(auditor=SlowAuditor(), cache=JsonCache(tmp_path / "c.db"))
    jobs.jobs["j"] = JobState(job_id="j")
    jobs._update("j", progress=80)
    jobs._update("j", progress=40, stage="later stage")
    assert jobs.jobs["j"].progress == 80 and jobs.jobs["j"].stage == "later stage"


def test_finished_jobs_are_swept(tmp_path):
    jobs = JobManager(auditor=SlowAuditor(), cache=JsonCache(tmp_path / "c.db"))
    old = time.monotonic() - FINISHED_JOB_TTL_SECONDS - 1
    jobs.jobs = {
        "old-done": JobState(job_id="old-done", status="done", updated=old),
        "old-running": JobState(job_id="old-running", status="running", updated=old),
        "fresh-error": JobState(job_id="fresh-error", status="error"),
    }
    jobs._sweep()
    assert set(jobs.jobs) == {"old-running", "fresh-error"}


def test_cache_keep_created_does_not_extend_ttl(tmp_path):
    cache = JsonCache(tmp_path / "c.db")
    cache.set("k", {"v": 1})
    time.sleep(0.05)
    cache.set("k", {"v": 2}, keep_created=True)
    assert cache.get("k", 0.04) is None  # still aged from the first write
    assert cache.get("k", 60) == {"v": 2}
    assert cache.purge_older_than(0.01) == 1
