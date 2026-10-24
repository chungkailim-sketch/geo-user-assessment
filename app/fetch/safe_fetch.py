"""SSRF-safe HTTP fetching for user-supplied URLs.

Every hop (including redirects) is validated: http/https on standard ports, no
credentials, and the hostname must resolve exclusively to public IP addresses.
The validated IP is pinned at connect time (see `PublicOnlyBackend`) so a DNS
rebinding answer between "check" and "connect" cannot reach internal hosts.
"""

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import httpcore
import httpx

from app.config import Settings, get_settings

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
HTML_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = HTML_TYPES + ("text/plain", "application/xml", "text/xml")
# IPv6 transition ranges that can tunnel to arbitrary (incl. internal) IPv4 targets.
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("2002::/16", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32")
)
UNREACHABLE_MESSAGE = "That website address can't be reached or isn't publicly accessible."


class UnsafeURLError(ValueError):
    """Raised when a URL is malformed or points at a non-public destination."""


class FetchError(RuntimeError):
    """Raised when a page cannot be retrieved."""


class BlockedError(FetchError):
    """Raised when a site's bot protection refuses automated access (we never try to evade it)."""

    def __init__(self) -> None:
        super().__init__("This site blocks automated analysis (bot protection), so it can't be assessed.")


BLOCKED_STATUSES = {401, 403, 429}
CHALLENGE_MARKERS = re.compile(
    r"(just a moment\.\.\.|cf-challenge|challenge-platform|awswaf|are you a robot|request unsuccessful|"
    r"enable javascript and cookies to continue|<title>\s*access denied\s*</title>)",
    re.IGNORECASE,
)
CHALLENGE_MAX_BYTES = 20_000


def looks_like_challenge(status: int, text: str) -> bool:
    """Bot-wall heuristics. 202 counts only with a tiny body (e.g. Booking.com's interstitial)."""
    if status in BLOCKED_STATUSES:
        return True
    if len(text) >= CHALLENGE_MAX_BYTES:
        return False
    return status == 202 or bool(CHALLENGE_MARKERS.search(text))


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    text: str


def normalize_url(raw: str) -> str:
    """Add a scheme if missing and validate the URL's shape (no network access)."""
    candidate = raw.strip()
    if not candidate:
        raise UnsafeURLError("Please enter a website URL.")
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs are supported.")
    if not parsed.hostname or "." not in parsed.hostname:
        raise UnsafeURLError("That doesn't look like a valid website address.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid port in URL.") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("Only standard web ports (80/443) are allowed.")
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", parsed.query, ""))


def site_root(url: str) -> str:
    """The homepage for a URL. Audits always start here so one domain has one canonical result."""
    parsed = urlparse(normalize_url(url))
    return f"{parsed.scheme}://{parsed.netloc}/"


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif any(ip in network for network in BLOCKED_NETWORKS):
            return False
    return ip.is_global and not ip.is_multicast


async def resolve_public(hostname: str) -> list[str]:
    """Resolve a hostname and return its addresses, refusing if any is non-public."""
    try:
        ipaddress.ip_address(hostname)
        candidates = [hostname]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeURLError(UNREACHABLE_MESSAGE) from exc
        candidates = list(dict.fromkeys(info[4][0] for info in infos))
    if not candidates or not all(is_public_ip(addr) for addr in candidates):
        raise UnsafeURLError(UNREACHABLE_MESSAGE)
    return candidates


async def assert_public_host(hostname: str, settings: Settings) -> None:
    """Early, user-friendly check. The binding enforcement happens in PublicOnlyBackend."""
    if not settings.allow_private_hosts:
        await resolve_public(hostname)


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolves + validates at connect time and connects to the validated IP.

    TLS SNI and the Host header still use the original hostname (httpcore passes
    `server_hostname` separately in start_tls), so HTTPS verification is unaffected.
    """

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await resolve_public(host)
        return await self._inner.connect_tcp(addresses[0], port, timeout, local_address, socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise UnsafeURLError(UNREACHABLE_MESSAGE)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose connection pool uses PublicOnlyBackend."""

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        # httpx has no public hook for the network backend; swap the pool it built.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=PublicOnlyBackend(),
        )


def build_page_client(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.fetch_timeout_seconds,
        follow_redirects=False,
        trust_env=False,  # never route through env-configured proxies
        transport=transport or (None if settings.allow_private_hosts else PinnedTransport()),
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


class SafeFetcher:
    """Fetches pages with SSRF checks, size caps, deadlines and per-host politeness delays."""

    MAX_TRACKED_HOSTS = 1000

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client or build_page_client(self.settings)
        self._next_slot: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _politeness_wait(self, host: str) -> None:
        """Reserve the host's next slot under a short lock, then sleep outside it."""
        async with self._lock:
            now = time.monotonic()
            if len(self._next_slot) > self.MAX_TRACKED_HOSTS:
                self._next_slot = {h: t for h, t in self._next_slot.items() if t > now}
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + self.settings.per_host_delay_seconds
        if slot > now:
            await asyncio.sleep(slot - now)

    async def fetch(self, url: str, accept: tuple[str, ...] = HTML_TYPES) -> FetchResult:
        try:
            async with asyncio.timeout(self.settings.fetch_deadline_seconds):
                return await self._fetch(url, accept)
        except TimeoutError as exc:
            raise FetchError("The site took too long to respond.") from exc

    async def _fetch(self, url: str, accept: tuple[str, ...]) -> FetchResult:
        current = normalize_url(url)
        for _ in range(self.settings.max_redirects + 1):
            host = urlparse(current).hostname or ""
            await assert_public_host(host, self.settings)
            await self._politeness_wait(host)
            try:
                async with self._client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchError("Redirect without a location header.")
                        current = normalize_url(urljoin(current, location))
                        continue
                    return await self._read_response(current, response, accept)
            except httpx.TimeoutException as exc:
                raise FetchError("The site took too long to respond.") from exc
            except httpx.HTTPError as exc:
                raise FetchError("Could not connect to the site.") from exc
        raise FetchError("Too many redirects.")

    async def _read_response(self, url: str, response: httpx.Response, accept: tuple[str, ...]) -> FetchResult:
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code in BLOCKED_STATUSES:
            raise BlockedError()
        if response.status_code >= 400:
            raise FetchError(f"Site returned HTTP {response.status_code}.")
        if accept and not any(t in content_type for t in accept):
            raise FetchError("The page is not a web page (unsupported content type).")
        body = await self._read_capped(response)
        text = body.decode(response.encoding or "utf-8", errors="replace")
        if any(t in content_type for t in HTML_TYPES) and looks_like_challenge(response.status_code, text):
            raise BlockedError()
        return FetchResult(url=url, status=response.status_code, content_type=content_type, text=text)

    async def _read_capped(self, response: httpx.Response) -> bytes:
        """Read up to max_response_bytes; anything beyond is ignored (enough HTML to score)."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.settings.max_response_bytes:
                break
            chunks.append(chunk)
        return b"".join(chunks)
