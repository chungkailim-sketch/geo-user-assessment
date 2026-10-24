"""Pydantic models for API input/output and validated Claude responses."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PLATFORMS = ("youtube", "reddit", "wikipedia", "linkedin", "other")


class AuditRequest(BaseModel):
    url: str = Field(min_length=3, max_length=2048)
    industry: str | None = Field(default=None, max_length=64)
    refresh: bool = False

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    stage: str = ""
    progress: int = 0
    error: str | None = None
    result: dict | None = None


class PlatformAssessment(BaseModel):
    score: int = Field(ge=0, le=100)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    sources: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, value: object) -> int:
        try:
            number = int(round(float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        return max(0, min(100, number))

    @field_validator("evidence", mode="before")
    @classmethod
    def trim_evidence(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item)[:300] for item in value[:8]]

    @field_validator("sources", mode="before")
    @classmethod
    def http_sources_only(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(u)[:500] for u in value if str(u).lower().startswith(("https://", "http://"))][:8]


class CustomIndustry(BaseModel):
    """An industry (and its leader) that Claude identified outside the default catalogue."""

    name: str = Field(min_length=2, max_length=48)
    name_zh: str = Field(min_length=1, max_length=24)
    leader: str = Field(min_length=1, max_length=60)
    leader_zh: str = Field(min_length=1, max_length=40)
    leader_url: str = Field(max_length=200)

    @field_validator("name", "name_zh", "leader", "leader_zh", mode="before")
    @classmethod
    def plain_text(cls, value: object) -> str:
        return re.sub(r"[\x00-\x1f\x7f<>\"]", " ", str(value)).strip()

    @field_validator("leader_url")
    @classmethod
    def homepage_only(cls, value: str) -> str:
        from app.fetch.safe_fetch import UnsafeURLError, site_root

        try:
            return site_root(value)  # shape check only; the fetcher still enforces public IPs
        except UnsafeURLError as exc:
            raise ValueError("invalid leader_url") from exc


class ClaudeAuthorityResponse(BaseModel):
    youtube: PlatformAssessment
    reddit: PlatformAssessment
    linkedin: PlatformAssessment
    other: PlatformAssessment
    wikipedia: PlatformAssessment | None = None
