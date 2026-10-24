"""Extract brand identity and off-site profile links from a homepage."""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup

PLATFORM_PATTERNS = {
    "youtube": re.compile(r"(?:youtube\.com/(?:@|c/|channel/|user/)|youtu\.be/)", re.I),
    "linkedin": re.compile(r"linkedin\.com/(?:company|showcase|school)/", re.I),
    "reddit": re.compile(r"reddit\.com/(?:r|user|u)/", re.I),
    "wikipedia": re.compile(r"(?:wikipedia\.org/wiki/|wikidata\.org/wiki/)", re.I),
    "instagram": re.compile(r"instagram\.com/[^/?#]+", re.I),
    "x": re.compile(r"(?:twitter|x)\.com/[^/?#]+", re.I),
    "facebook": re.compile(r"facebook\.com/[^/?#]+", re.I),
    "tiktok": re.compile(r"tiktok\.com/@", re.I),
    "github": re.compile(r"github\.com/[^/?#]+", re.I),
    "crunchbase": re.compile(r"crunchbase\.com/organization/", re.I),
    "trustpilot": re.compile(r"trustpilot\.com/review/", re.I),
    "glassdoor": re.compile(r"glassdoor\.[a-z.]+/", re.I),
    "pinterest": re.compile(r"pinterest\.[a-z.]+/[^/?#]+", re.I),
    "weibo": re.compile(r"weibo\.com/", re.I),
}
OTHER_PLATFORMS = tuple(k for k in PLATFORM_PATTERNS if k not in ("youtube", "linkedin", "reddit", "wikipedia"))
TITLE_SPLIT = re.compile(r"\s+[|\-–—:·•]\s+")


@dataclass
class BrandSignals:
    domain: str
    brand_name: str
    same_as: list[str] = field(default_factory=list)
    profiles: dict[str, list[str]] = field(default_factory=dict)
    has_organization_schema: bool = False
    title: str = ""
    description: str = ""
    headings: list[str] = field(default_factory=list)

    def has(self, platform: str) -> bool:
        return bool(self.profiles.get(platform))

    def other_platform_count(self) -> int:
        return sum(1 for p in OTHER_PLATFORMS if self.has(p))

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "brand_name": self.brand_name,
            "same_as": self.same_as,
            "profiles": self.profiles,
            "has_organization_schema": self.has_organization_schema,
        }


def registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def _iter_json_ld(soup: BeautifulSoup):
    for script in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                yield node
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)


def _brand_from_domain(domain: str) -> str:
    label = domain.split(".")[0]
    return label.replace("-", " ").title()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def choose_brand_name(candidates: list[str], domain: str) -> str:
    """Pick the candidate that matches the domain; avoid generic titles like 'Official Site'."""
    label = domain.split(".")[0]
    names = [c.strip() for c in candidates if c and 0 < len(c.strip()) <= 60]
    for name in names:
        if _squash(name) in (_squash(label), _squash(domain)):
            return name
    for name in names:
        match = re.search(re.escape(label).replace("\\-", "[-\\s]?"), name, re.IGNORECASE)
        if match:
            return match.group(0)
    return _brand_from_domain(domain)


def extract_signals(html: str, url: str, domain: str | None = None) -> BrandSignals:
    """`domain` is the brand's identity (the URL the user entered), which may differ
    from `url` after geo-redirects (e.g. dyson.com -> dyson.com.sg)."""
    soup = BeautifulSoup(html, "lxml")
    domain = domain or registrable_domain(url)

    org_name = ""
    same_as: list[str] = []
    has_org = False
    for node in _iter_json_ld(soup):
        types = node.get("@type", [])
        types = types if isinstance(types, list) else [types]
        if any(str(t) in ("Organization", "Corporation", "Brand", "LocalBusiness", "OnlineStore") for t in types):
            has_org = True
            if not org_name and isinstance(node.get("name"), str):
                org_name = node["name"].strip()
        links = node.get("sameAs")
        if isinstance(links, str):
            links = [links]
        if isinstance(links, list):
            same_as.extend(str(link) for link in links if isinstance(link, str))

    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    og_name = (og_site.get("content") or "").strip() if og_site else ""
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title_parts = [part.strip() for part in TITLE_SPLIT.split(title)] if title else []
    brand_name = choose_brand_name([org_name, og_name, *title_parts], domain)

    hrefs = [a["href"] for a in soup.find_all("a", href=True)] + same_as
    profiles: dict[str, list[str]] = {}
    for platform, pattern in PLATFORM_PATTERNS.items():
        matches = sorted({h.split("?")[0] for h in hrefs if pattern.search(h) and "share" not in h.lower()})
        if matches:
            profiles[platform] = matches[:5]

    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    headings = [h.get_text(" ", strip=True)[:120] for h in soup.find_all(["h1", "h2"])][:30]

    return BrandSignals(
        domain=domain,
        brand_name=brand_name,
        same_as=sorted(set(same_as))[:30],
        profiles=profiles,
        has_organization_schema=has_org,
        title=title[:300],
        description=((desc_tag.get("content") or "") if desc_tag else "")[:500],
        headings=headings,
    )
