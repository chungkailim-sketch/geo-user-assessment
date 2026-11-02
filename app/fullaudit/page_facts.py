"""Parse each crawled page once into the facts the full-audit rubrics need."""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.fetch.safe_fetch import FetchResult

QUESTION_HEADING = re.compile(r"(\?|？|^(how|what|why|when|which|who|where|can|does|is|are|should)\b|如何|怎么|怎样|什么|为什么|哪些|是否|吗)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(\+?\d[\d\s().-]{7,}\d)")
FIRST_PERSON = re.compile(r"\b(we|our|we've|we're|i)\b|我们|我司|本公司", re.I)
STAT = re.compile(r"\d+(\.\d+)?\s?(%|percent|million|billion|万|亿|倍)", re.I)
DATE_TEXT = re.compile(r"\b(20[12]\d)[-/.年](0?[1-9]|1[0-2])", re.I)
LINK_HINTS = {
    "privacy": re.compile(r"privacy|隐私", re.I),
    "terms": re.compile(r"terms|conditions|legal|条款|使用协议", re.I),
    "about": re.compile(r"about|company|who-we-are|关于", re.I),
    "contact": re.compile(r"contact|联系", re.I),
    "careers": re.compile(r"careers|jobs|招聘", re.I),
}
TRUST_WORDS = {
    "case_study": re.compile(r"case stud|success stor|客户案例|成功案例", re.I),
    "award": re.compile(r"award|winner|recogni[sz]ed|获奖|荣获|奖项", re.I),
    "testimonial": re.compile(r"testimonial|customer review|what our customers|客户评价|用户评价", re.I),
    "research": re.compile(r"our research|survey of|study found|white ?paper|report finds|研究报告|白皮书|调研", re.I),
    "methodology": re.compile(r"methodolog|how we test|how we tested|评测方法|测试方法", re.I),
    "editorial": re.compile(r"editorial (policy|standards)|corrections policy|编辑方针", re.I),
    "disclosure": re.compile(r"affiliate|sponsored|disclosure|广告声明|赞助", re.I),
}


VERIFICATION_METAS = {"google-site-verification": "google", "msvalidate.01": "bing",
                      "baidu-site-verification": "baidu", "yandex-verification": "yandex"}


@dataclass
class PageFacts:
    url: str
    status: int
    title: str = ""
    meta_description: str = ""
    canonical: str = ""
    robots_meta: str = ""
    viewport: str = ""
    lang: str = ""
    hreflangs: list[str] = field(default_factory=list)
    h1_count: int = 0
    headings: list[str] = field(default_factory=list)
    heading_levels: list[int] = field(default_factory=list)
    question_headings: int = 0
    tables: int = 0
    lists: int = 0
    images: int = 0
    images_missing_alt: int = 0
    images_sized: int = 0
    images_lazy: int = 0
    og_tags: int = 0
    blocking_scripts: int = 0
    scripts: int = 0
    word_count: int = 0
    html_bytes: int = 0
    jsonld: list = field(default_factory=list)  # parsed objects
    jsonld_errors: int = 0
    microdata: bool = False
    rdfa: bool = False
    dates: list[str] = field(default_factory=list)
    has_author: bool = False
    has_email: bool = False
    has_phone: bool = False
    has_address: bool = False
    first_person: int = 0
    stats: int = 0
    link_hints: set[str] = field(default_factory=set)
    trust_words: set[str] = field(default_factory=set)
    internal_links: int = 0
    external_links: int = 0
    text_sample: str = ""
    verifications: set[str] = field(default_factory=set)  # webmaster-tool verification metas
    videos: int = 0
    headers: dict = field(default_factory=dict)
    elapsed_ms: int = 0
    redirects: int = 0


def _jsonld(soup: BeautifulSoup) -> tuple[list, int]:
    found, errors = [], 0
    for tag in soup.find_all("script", type=re.compile(r"application/ld\+json", re.I)):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            errors += 1
            continue
        found.extend(data if isinstance(data, list) else [data])
    return found, errors


def extract_facts(page: FetchResult) -> PageFacts:
    html = page.text
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(page.url).hostname or ""
    facts = PageFacts(url=page.url, status=page.status, html_bytes=len(html.encode("utf-8", "ignore")),
                      headers=page.headers, elapsed_ms=page.elapsed_ms, redirects=page.redirects)
    facts.jsonld, facts.jsonld_errors = _jsonld(soup)
    facts.microdata = soup.find(attrs={"itemscope": True}) is not None
    facts.rdfa = soup.find(attrs={"typeof": True}) is not None or soup.find(attrs={"vocab": True}) is not None

    if soup.title and soup.title.string:
        facts.title = soup.title.string.strip()[:200]
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or meta.get("property") or "").lower()
        content = (meta.get("content") or "").strip()
        if name == "description":
            facts.meta_description = content[:400]
        elif name == "robots":
            facts.robots_meta = content.lower()
        elif name == "viewport":
            facts.viewport = content.lower()
        elif name in VERIFICATION_METAS:
            facts.verifications.add(VERIFICATION_METAS[name])
        elif name.startswith("og:"):
            facts.og_tags += 1
        elif name in ("author", "article:author"):
            facts.has_author = facts.has_author or bool(content)
        elif name in ("article:published_time", "article:modified_time", "date", "last-modified"):
            facts.dates.append(content[:10])
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    facts.canonical = canonical.get("href", "") if canonical else ""
    facts.hreflangs = [link.get("hreflang", "") for link in soup.find_all("link", hreflang=True)]
    facts.lang = (soup.html.get("lang", "") if soup.html else "").lower()
    if soup.find(rel=lambda v: v and "author" in v) or soup.find(class_=re.compile(r"author|byline", re.I)):
        facts.has_author = True
    for time_tag in soup.find_all("time"):
        if time_tag.get("datetime"):
            facts.dates.append(time_tag["datetime"][:10])

    head = soup.head
    if head:
        facts.blocking_scripts = sum(
            1 for s in head.find_all("script", src=True) if not s.has_attr("async") and not s.has_attr("defer")
            and (s.get("type") or "").lower() != "module"
        )
    facts.scripts = len(soup.find_all("script", src=True))
    for image in soup.find_all("img"):
        facts.images += 1
        facts.images_missing_alt += 0 if (image.get("alt") or "").strip() else 1
        facts.images_sized += 1 if image.get("width") and image.get("height") else 0
        facts.images_lazy += 1 if (image.get("loading") or "").lower() == "lazy" else 0
    for link in soup.find_all("a", href=True):
        href = link["href"]
        target = urlparse(href)
        if target.scheme in ("http", "https") and target.hostname and target.hostname != host:
            facts.external_links += 1
        elif not href.startswith(("#", "mailto:", "tel:", "javascript:")):
            facts.internal_links += 1
        label = f"{href} {link.get_text(' ', strip=True)}"
        facts.link_hints |= {key for key, pattern in LINK_HINTS.items() if pattern.search(label)}
        if href.startswith("mailto:"):
            facts.has_email = True
        if href.startswith("tel:"):
            facts.has_phone = True
    if soup.find("address") or any(isinstance(o, dict) and "address" in o for o in facts.jsonld):
        facts.has_address = True

    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        level = int(tag.name[1])
        facts.h1_count += level == 1
        facts.headings.append(text[:120])
        facts.heading_levels.append(level)
        facts.question_headings += bool(QUESTION_HEADING.search(text))
    facts.tables = len(soup.find_all("table"))
    facts.videos = len(soup.find_all("video")) + sum(
        1 for frame in soup.find_all("iframe", src=True) if re.search(r"youtube|vimeo|bilibili|youku", frame["src"], re.I))
    facts.lists = len(soup.find_all(["ul", "ol"]))

    for element in soup.find_all(["script", "style", "noscript", "svg"]):
        element.decompose()
    body = soup.body.get_text(" ", strip=True) if soup.body else ""
    facts.word_count = len(re.findall(r"[A-Za-z0-9']+", body)) + len(re.findall(r"[一-鿿]", body)) // 2
    facts.text_sample = body[:1500]
    facts.has_email = facts.has_email or bool(EMAIL.search(body))
    facts.has_phone = facts.has_phone or bool(PHONE.search(body))
    facts.first_person = len(FIRST_PERSON.findall(body))
    facts.stats = len(STAT.findall(body))
    facts.dates += [f"{y}-{int(m):02d}" for y, m in DATE_TEXT.findall(body)[:5]]
    facts.trust_words = {key for key, pattern in TRUST_WORDS.items() if pattern.search(body)}
    return facts


def schema_types(objects: list) -> list[str]:
    """Every @type in a list of JSON-LD objects, including nested @graph items."""
    types: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            for value in kind if isinstance(kind, list) else [kind]:
                if isinstance(value, str):
                    types.append(value)
            for key, value in node.items():
                if key != "@type":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(objects)
    return types


def find_typed(objects: list, *names: str) -> list[dict]:
    """JSON-LD nodes (at any depth) whose @type is one of `names`."""
    found: list[dict] = []
    wanted = {n.lower() for n in names}

    def walk(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(isinstance(k, str) and k.lower() in wanted for k in kinds):
                found.append(node)
            for key, value in node.items():
                if key != "@type":
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(objects)
    return found
