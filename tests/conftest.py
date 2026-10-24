import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings

BRAND_HOME = """
<html><head>
<title>Acme Clean | Robot Vacuums and Cordless Cleaning</title>
<meta name="description" content="Acme Clean makes robot vacuum cleaners and cordless vacuum cleaning tools.">
<meta property="og:site_name" content="Acme Clean">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme Clean",
 "sameAs":["https://www.youtube.com/@acmeclean","https://www.linkedin.com/company/acme-clean"]}
</script>
</head><body>
<nav><a href="/about">About</a><a href="/faq">FAQ</a><a href="/login">Login</a>
<a href="https://www.instagram.com/acmeclean">Instagram</a></nav>
<h1>What is a robot vacuum?</h1>
<p>A robot vacuum is an autonomous cleaning device that uses sensors and suction to clean floors without
manual effort. According to Acme Clean research, 72% of households using a robot vacuum in 2025 reported
cleaning floors three times more often. The Acme Clean S9 robot vacuum maps rooms with LiDAR navigation.</p>
<p>For example, our study of 1,200 customers found that scheduled cleaning cut weekly vacuuming time by 4 hours.
Cordless cleaning with the Acme Clean V5 vacuum uses 150 air watts of suction power.</p>
<h2>Why choose Acme Clean?</h2>
<p>Acme Clean vacuum cleaners are designed in Singapore and sold in 14 countries across Asia and Europe.</p>
<p>First, every vacuum includes a two year warranty. Second, filters capture 99.9% of particles.</p>
</body></html>
"""

ABOUT_PAGE = """
<html><head><title>About Acme Clean</title></head><body>
<h1>About Acme Clean</h1>
<p>Acme Clean is a Singapore cleaning technology company founded in 2015. The company builds robot vacuum
cleaners, cordless vacuums and air purifiers for homes. Acme Clean employs 300 people and sells through
retail partners including Lazada and Shopee.</p>
</body></html>
"""

LEADER_HOME = """
<html><head><title>Dyson | Official Site</title>
<script type="application/ld+json">{"@type":"Organization","name":"Dyson"}</script></head>
<body><h1>Dyson vacuum cleaners</h1>
<p>Dyson is a British technology company that designs cordless vacuum cleaners, air purifiers and hair care.
According to Dyson, its V15 Detect vacuum measures particles 15,000 times a second with a laser in 2024.</p>
<a href="https://www.youtube.com/user/dyson">YouTube</a></body></html>
"""


def wiki_handler(request: httpx.Request) -> httpx.Response | None:
    params = request.url.params
    host = request.url.host
    if host == "www.wikidata.org" and params.get("action") == "wbsearchentities":
        return httpx.Response(200, json={"search": [{"id": "Q1"}]})
    if host == "www.wikidata.org" and params.get("action") == "wbgetentities":
        return httpx.Response(200, json={"entities": {"Q1": {
            "id": "Q1",
            "claims": {"P856": [{"mainsnak": {"datavalue": {"value": "https://www.acmeclean.example"}}}]},
            "sitelinks": {"enwiki": {"title": "Acme Clean"}},
            "descriptions": {"en": {"value": "cleaning company"}},
        }}})
    if host == "en.wikipedia.org" and params.get("prop") == "info":
        return httpx.Response(200, json={"query": {"pages": {"1": {"length": 9000}}}})
    if host == "en.wikipedia.org" and params.get("list") == "exturlusage":
        return httpx.Response(200, json={"query": {"exturlusage": [{"title": "Vacuum cleaner"}]}})
    if host == "en.wikipedia.org" and params.get("list") == "search":
        return httpx.Response(200, json={"query": {"searchinfo": {"totalhits": 4}}})
    return None


def site_handler(request: httpx.Request) -> httpx.Response:
    wiki = wiki_handler(request)
    if wiki is not None:
        return wiki
    host, path = request.url.host, request.url.path
    html = {"content-type": "text/html; charset=utf-8"}
    if host == "www.acmeclean.example":
        pages = {"/": BRAND_HOME, "/about": ABOUT_PAGE, "/faq": ABOUT_PAGE}
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /faq\n", headers={"content-type": "text/plain"})
        if path in pages:
            return httpx.Response(200, text=pages[path], headers=html)
        return httpx.Response(404, text="missing", headers=html)
    if host == "www.dyson.com":
        if path == "/":
            return httpx.Response(200, text=LEADER_HOME, headers=html)
        return httpx.Response(404, text="missing", headers=html)
    return httpx.Response(404, text="unknown host")


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    return httpx.MockTransport(site_handler)


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        anthropic_api_key="",
        cache_path=tmp_path / "cache.sqlite3",
        per_host_delay_seconds=0,
        allow_private_hosts=True,
        rate_limit="off",
        max_pages=4,
    )


def claude_text_response(payload: dict | str, stop_reason: str = "end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


class FakeClaude:
    """Minimal stand-in for anthropic.AsyncAnthropic returning queued responses."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0) if self._responses else claude_text_response("None")
        if isinstance(item, Exception):
            raise item
        return item


AUTHORITY_PAYLOAD = {
    "youtube": {"score": 62, "evidence": ["Channel with 3K subscribers"], "sources": ["https://youtube.com/@acmeclean"]},
    "reddit": {"score": 41, "evidence": ["Mentioned in r/RobotVacuums"], "sources": []},
    "linkedin": {"score": 55, "evidence": ["2K followers"], "sources": []},
    "other": {"score": 48, "evidence": ["Trade press coverage"], "sources": []},
}
