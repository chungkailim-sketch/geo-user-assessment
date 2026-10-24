import httpx
import pytest

from app.config import Settings
from app.fetch.crawl import Crawler, parse_sitemap, pick_candidates, score_candidate
from app.fetch.safe_fetch import SafeFetcher

pytestmark = pytest.mark.unit


def test_pick_candidates_prioritises_and_filters():
    urls = [
        "https://acme.example/", "https://acme.example/login", "https://acme.example/about-us",
        "https://acme.example/faq", "https://other.example/about", "https://acme.example/brochure.pdf",
        "https://www.acme.example/blog/how-to-clean", "https://acme.example/faq#top", "mailto:hi@acme.example",
    ]
    picks = pick_candidates(urls, "acme.example", 10)
    assert "https://acme.example/faq" in picks
    assert "https://acme.example/about-us" in picks
    assert "https://www.acme.example/blog/how-to-clean" in picks
    assert all("login" not in p and "other.example" not in p and not p.endswith(".pdf") for p in picks)
    assert picks.count("https://acme.example/faq") == 1
    assert "https://acme.example" not in picks


def test_score_candidate_prefers_shallow_topical_pages():
    assert score_candidate("https://a.example/about") > score_candidate("https://a.example/x/y/z/about")
    assert score_candidate("https://a.example/") == -1


def test_parse_sitemap_index_and_urlset():
    index = "<sitemapindex><sitemap><loc>https://a.example/s1.xml</loc></sitemap></sitemapindex>"
    assert parse_sitemap(index) == ([], ["https://a.example/s1.xml"])
    urlset = "<urlset><url><loc> https://a.example/about </loc></url></urlset>"
    assert parse_sitemap(urlset) == (["https://a.example/about"], [])


async def test_crawler_respects_robots_and_uses_sitemap_index():
    html = {"content-type": "text/html"}
    xml = {"content-type": "application/xml"}

    def handler(request):
        path = request.url.path
        if path == "/":
            return httpx.Response(200, text='<a href="/about">About</a><a href="/private-faq">FAQ</a>', headers=html)
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private-faq", headers={"content-type": "text/plain"})
        if path == "/sitemap.xml":
            return httpx.Response(200, text="<sitemapindex><loc>https://acme.example/s1.xml</loc></sitemapindex>", headers=xml)
        if path == "/s1.xml":
            return httpx.Response(200, text="<urlset><loc>https://acme.example/guide</loc></urlset>", headers=xml)
        if path in ("/about", "/guide"):
            return httpx.Response(200, text="<p>page</p>", headers=html)
        return httpx.Response(404, headers=html)

    settings = Settings(per_host_delay_seconds=0, allow_private_hosts=True)
    fetcher = SafeFetcher(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await Crawler(fetcher, max_pages=5).crawl("https://acme.example/")
    urls = [p.url for p in result.pages]
    assert result.robots_found
    assert "https://acme.example/about" in urls and "https://acme.example/guide" in urls
    assert not any("private-faq" in u for u in urls)


async def test_crawler_records_page_errors():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<a href="/about">About</a>', headers={"content-type": "text/html"})
        return httpx.Response(500, headers={"content-type": "text/html"})

    settings = Settings(per_host_delay_seconds=0, allow_private_hosts=True)
    fetcher = SafeFetcher(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await Crawler(fetcher, max_pages=5).crawl("https://acme.example/")
    assert len(result.pages) == 1
    assert not result.robots_found
    assert result.errors and "about" in result.errors[0]


def test_exclusions_match_whole_segments_only():
    urls = ["https://acme.example/blog/steve-jobs-story", "https://acme.example/jobs", "https://acme.example/about/careers/",
            "https://acme.example/terms-of-awesome", "https://acme.example/guide.PDF"]
    picks = pick_candidates(urls, "acme.example", 10)
    assert "https://acme.example/blog/steve-jobs-story" in picks
    assert "https://acme.example/terms-of-awesome" in picks
    assert not any(p.endswith(("/jobs", "careers", ".PDF")) for p in picks)


async def test_robots_rules_for_our_bot_token_are_honoured():
    html = {"content-type": "text/html"}

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<a href="/about">About</a>', headers=html)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: AIReadinessBot\nDisallow: /about\n",
                                  headers={"content-type": "text/plain"})
        return httpx.Response(200, text="<p>x</p>", headers=html)

    settings = Settings(per_host_delay_seconds=0, allow_private_hosts=True)
    fetcher = SafeFetcher(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await Crawler(fetcher, max_pages=5).crawl("https://acme.example/")
    assert [p.url for p in result.pages] == ["https://acme.example/"]
