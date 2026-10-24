"""Discover a small, high-value page set for a site: homepage + key pages.

Follows the GEO audit quality gates: respect robots.txt, prefer sitemap,
fall back to homepage navigation links, cap the number of pages.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from app.fetch.safe_fetch import TEXT_TYPES, FetchError, FetchResult, SafeFetcher, UnsafeURLError

PRIORITY_KEYWORDS = (
    ("about", 6), ("faq", 6), ("question", 5), ("guide", 5), ("how", 4), ("product", 4),
    ("blog", 4), ("news", 3), ("story", 3), ("support", 3), ("help", 3), ("company", 3),
    ("service", 3), ("technology", 2), ("sustainab", 2), ("article", 2),
)
# Matched against whole path segments (so "/blog/steve-jobs" is kept) or file extensions.
EXCLUDE_SEGMENTS = re.compile(
    r"/(login|signin|sign-in|register|cart|checkout|account|privacy|cookies?|terms|legal|careers|jobs|search)(/|$)",
    re.IGNORECASE,
)
EXCLUDE_EXTENSIONS = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|mp4|mp3|xml|json)$", re.IGNORECASE)
ROBOTS_TOKEN = "AIReadinessBot"  # product token matched against robots.txt User-agent lines
MAX_SITEMAP_URLS = 2000


@dataclass
class CrawlResult:
    start_url: str
    homepage: FetchResult
    pages: list[FetchResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    robots_found: bool = False


def _same_site(url: str, base_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(host) == strip(base_host)


def score_candidate(url: str) -> int:
    path = urlparse(url).path.lower()
    if path in ("", "/"):
        return -1
    score = sum(weight for word, weight in PRIORITY_KEYWORDS if word in path)
    depth = path.strip("/").count("/")
    return score - depth  # prefer shallow, topical pages


def pick_candidates(urls: list[str], base_host: str, limit: int) -> list[str]:
    seen: set[str] = set()
    ranked: list[tuple[int, str]] = []
    for url in urls:
        clean = url.split("#")[0].rstrip("/")
        parsed = urlparse(clean)
        if not clean or clean in seen or parsed.scheme not in ("http", "https"):
            continue
        if EXCLUDE_SEGMENTS.search(parsed.path) or EXCLUDE_EXTENSIONS.search(parsed.path):
            continue
        if not _same_site(clean, base_host):
            continue
        seen.add(clean)
        ranked.append((score_candidate(clean), clean))
    ranked = [item for item in ranked if item[0] >= 0]
    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    return [url for _, url in ranked[:limit]]


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [urljoin(base_url, anchor["href"]) for anchor in soup.find_all("a", href=True)]


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Return (page_urls, child_sitemap_urls)."""
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.IGNORECASE)[:MAX_SITEMAP_URLS]
    if re.search(r"<sitemapindex", xml, re.IGNORECASE):
        return [], locs
    return locs, []


class Crawler:
    def __init__(self, fetcher: SafeFetcher, max_pages: int):
        self.fetcher = fetcher
        self.max_pages = max_pages

    async def _robots(self, origin: str) -> RobotFileParser | None:
        try:
            result = await self.fetcher.fetch(urljoin(origin, "/robots.txt"), accept=TEXT_TYPES)
        except (FetchError, UnsafeURLError):
            return None
        parser = RobotFileParser()
        parser.parse(result.text.splitlines())
        return parser

    async def _sitemap_urls(self, origin: str) -> list[str]:
        try:
            result = await self.fetcher.fetch(urljoin(origin, "/sitemap.xml"), accept=TEXT_TYPES)
        except (FetchError, UnsafeURLError):
            return []
        pages, children = parse_sitemap(result.text)
        for child in children[:2]:
            try:
                child_result = await self.fetcher.fetch(child, accept=TEXT_TYPES)
            except (FetchError, UnsafeURLError):
                continue
            pages.extend(parse_sitemap(child_result.text)[0])
            if len(pages) >= MAX_SITEMAP_URLS:
                break
        return pages

    async def crawl(self, url: str) -> CrawlResult:
        homepage = await self.fetcher.fetch(url)
        parsed = urlparse(homepage.url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        result = CrawlResult(start_url=url, homepage=homepage, pages=[homepage])

        robots = await self._robots(origin)
        result.robots_found = robots is not None

        candidates = extract_links(homepage.text, homepage.url)
        candidates += await self._sitemap_urls(origin)
        picks = pick_candidates(candidates, parsed.hostname or "", self.max_pages * 3)

        for candidate in picks:
            if len(result.pages) >= self.max_pages:
                break
            if robots and not robots.can_fetch(ROBOTS_TOKEN, candidate):
                continue
            try:
                result.pages.append(await self.fetcher.fetch(candidate))
            except (FetchError, UnsafeURLError) as exc:
                result.errors.append(f"{candidate}: {exc}")
        return result
