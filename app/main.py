"""FastAPI entrypoint for the AI Readiness Score web app."""

import logging
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.audit import Auditor
from app.cache import JsonCache
from app.config import ROOT_DIR, Settings, get_settings
from app.fetch.safe_fetch import SafeFetcher, UnsafeURLError, build_page_client, normalize_url
from app.industry import get_industry, load_industries
from app.jobs import JobManager, QueueFullError
from app.models import AuditRequest, JobStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CSP = (
    "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))


def build_claude_client(settings: Settings):
    if not settings.claude_enabled:
        log.info("ANTHROPIC_API_KEY not set: authority will use limited, on-site signals only")
        return None
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.claude_timeout_seconds)


def create_app(settings: Settings | None = None, claude_client=None, transport=None) -> FastAPI:
    settings = settings or get_settings()
    if settings.allow_private_hosts:
        log.warning("ALLOW_PRIVATE_HOSTS is enabled: SSRF protection is OFF. Never use this in production.")
    limiter = Limiter(key_func=get_remote_address, enabled=settings.rate_limit != "off")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cache = JsonCache(settings.cache_path)
        cache.purge_older_than(settings.leader_cache_ttl_seconds)
        fetcher = SafeFetcher(settings, client=build_page_client(settings, transport))
        api_client = httpx.AsyncClient(
            headers={"User-Agent": settings.api_user_agent}, timeout=15, transport=transport, trust_env=False
        )
        claude = claude_client if claude_client is not None else build_claude_client(settings)
        auditor = Auditor(settings, cache, fetcher, api_client, claude)
        app.state.jobs = JobManager(auditor=auditor, cache=cache, max_active=settings.max_queued_jobs)
        yield
        await fetcher.aclose()
        await api_client.aclose()
        cache.close()

    app = FastAPI(title="AI搜索就绪度诊断", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.limiter = limiter
    app.mount("/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse({"detail": "请求过于频繁，请一分钟后再试。"}, status_code=429)

    @app.exception_handler(QueueFullError)
    async def queue_full(request: Request, exc: QueueFullError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503, headers={"Retry-After": "60"})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"industries": load_industries()})

    @app.get("/results/{job_id}", response_class=HTMLResponse)
    async def results(request: Request, job_id: str):
        _check_job_id(job_id)
        return templates.TemplateResponse(request, "results.html", {"job_id": job_id})

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.get("/api/industries")
    async def industries():
        return [
            {"name": i.name, "name_zh": i.name_zh, "leader": i.leader, "leader_zh": i.leader_zh, "leader_url": i.leader_url}
            for i in load_industries()
        ]

    @app.post("/api/audit")
    @limiter.limit(settings.rate_limit if settings.rate_limit != "off" else "1000/second")
    async def start_audit(request: Request, body: AuditRequest):
        try:
            url = normalize_url(body.url)
        except UnsafeURLError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.industry and get_industry(body.industry) is None:
            raise HTTPException(status_code=400, detail="未知行业。")
        job_id = request.app.state.jobs.start(url, body.industry, refresh=body.refresh)
        return {"job_id": job_id, "results_url": f"/results/{job_id}"}

    @app.get("/api/audit/{job_id}", response_model=JobStatus)
    async def audit_status(request: Request, job_id: str):
        _check_job_id(job_id)
        status = request.app.state.jobs.status(job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="未找到该诊断报告，或报告已过期。")
        return status

    class BenchmarkRequest(BaseModel):
        audit_id: str = Field(pattern=JOB_ID_PATTERN.pattern)
        industry: str = Field(max_length=64)

    @app.post("/api/benchmark")
    @limiter.limit(settings.benchmark_rate_limit if settings.rate_limit != "off" else "1000/second")
    async def rebenchmark(request: Request, body: BenchmarkRequest):
        benchmark = await request.app.state.jobs.rebenchmark(body.audit_id, body.industry)
        if benchmark is None:
            raise HTTPException(status_code=404, detail="未找到该诊断报告或行业。")
        return benchmark

    return app


def _check_job_id(job_id: str) -> None:
    if not JOB_ID_PATTERN.match(job_id):
        raise HTTPException(status_code=404, detail="未找到该诊断报告。")


app = create_app()
