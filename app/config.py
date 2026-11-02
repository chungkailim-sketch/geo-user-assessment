"""Application settings loaded from environment variables (or a .env file)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_timeout_seconds: float = 120.0

    cache_path: Path = ROOT_DIR / "data" / "cache.sqlite3"
    user_cache_ttl_seconds: int = 24 * 3600
    leader_cache_ttl_seconds: int = 7 * 24 * 3600

    max_pages: int = 6
    fetch_timeout_seconds: float = 20.0  # per network read
    fetch_deadline_seconds: float = 30.0  # total per page, incl. redirects (slowloris guard)
    max_response_bytes: int = 2 * 1024 * 1024
    max_redirects: int = 5
    per_host_delay_seconds: float = 1.0
    user_agent: str = "Mozilla/5.0 (compatible; AIReadinessBot/1.0; +https://github.com/)"
    # Wikimedia APIs reject requests without a descriptive UA that includes contact info.
    api_user_agent: str = "AIReadinessScore/1.0 (https://github.com/geo-user-assessment; ai-readiness@example.com)"

    rate_limit: str = "5/minute"
    benchmark_rate_limit: str = "10/minute"
    max_concurrent_audits: int = 3
    max_queued_jobs: int = 20
    audit_deadline_seconds: float = 240.0
    min_refresh_seconds: int = 3600  # a "fresh" re-run is honoured at most hourly per domain
    # Behind a reverse proxy (e.g. Railway) every request arrives from the proxy, so rate limits key
    # on the client IP the proxy appends to X-Forwarded-For. Only enable when such a proxy is in front.
    trust_proxy_headers: bool = False
    # Test-only escape hatch so integration tests can audit a local fixture site.
    allow_private_hosts: bool = False

    # Full PDF report, emailed on request from the results page.
    full_audit_max_pages: int = 15
    report_rate_limit: str = "3/day"  # per visitor IP
    report_max_sends_per_audit: int = 3
    report_deadline_seconds: float = 420.0
    pdf_font_path: str = ""  # optional CJK TrueType font; otherwise a bundled/system font is used
    # SMTP delivery. The report feature is switched off until SMTP_HOST and SMTP_FROM are set.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "starttls"  # starttls (port 587) | ssl (port 465) | none
    smtp_from: str = ""  # sender address, e.g. geo@yourdomain.com
    smtp_from_name: str = "罗德出海GEO准备度评估"
    smtp_reply_to: str = ""
    report_bcc: str = ""  # optional internal copy of every report sent (comma-separated)
    smtp_timeout_seconds: float = 30.0

    @property
    def claude_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)


@lru_cache
def get_settings() -> Settings:
    return Settings()
