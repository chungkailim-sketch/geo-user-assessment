import httpx
import pytest

from app.models import ClaudeAuthorityResponse
from app.scoring.authority import (
    PLATFORM_WEIGHTS,
    authority_rating,
    build_authority,
    heuristic_scores,
    youtube_band,
)
from app.scoring.signals import extract_signals
from app.scoring.wikipedia import (
    WikiPresence,
    WikipediaChecker,
    domains_match,
    score_wikipedia,
    social_from_claims,
)
from tests.conftest import AUTHORITY_PAYLOAD, BRAND_HOME, wiki_handler

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "presence,expected",
    [
        (WikiPresence(wikidata_id="Q1", wikidata_claims=300, article_title="X", article_length=90000, domain_citations=20), 95),
        (WikiPresence(wikidata_id="Q1", wikidata_claims=30, article_title="X", article_length=25000, domain_citations=4), 85),
        (WikiPresence(wikidata_id="Q1", wikidata_claims=30, article_title="X", article_length=25000), 78),
        (WikiPresence(wikidata_id="Q1", article_title="X", article_length=9000), 70),
        (WikiPresence(article_title="X", article_length=2000), 55),
        (WikiPresence(wikidata_id="Q1", domain_citations=2), 40),
        (WikiPresence(mention_hits=5), 35),
        (WikiPresence(wikidata_id="Q1"), 20),
        (WikiPresence(), 0),
    ],
)
def test_score_wikipedia_bands(presence, expected):
    assert score_wikipedia(presence)[0] == expected


@pytest.mark.parametrize(
    "site,domain,match",
    [("https://www.apple.com/", "apple.com", True), ("apple.com", "apple.com", True),
     ("https://uk.dyson.com", "dyson.com", True), ("https://apple.co", "apple.com", False), ("", "apple.com", False)],
)
def test_domains_match(site, domain, match):
    assert domains_match(site, domain) is match


async def test_wikipedia_checker_matches_by_official_website():
    async with httpx.AsyncClient(transport=httpx.MockTransport(wiki_handler)) as client:
        presence = await WikipediaChecker(client).check("Acme Clean", "acmeclean.example")
    assert presence.wikidata_id == "Q1"
    assert presence.article_title == "Acme Clean"
    assert presence.article_length == 9000
    assert presence.domain_citations == 1
    assert presence.mention_hits == 4
    assert presence.matched_by.startswith("official website")


async def test_wikipedia_checker_survives_api_failures():
    def failing(request):
        return httpx.Response(500, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        presence = await WikipediaChecker(client).check("Acme", "acme.example")
    assert presence.errors
    assert score_wikipedia(presence)[0] == 0


async def test_wikipedia_checker_falls_back_to_name_match():
    def handler(request):
        params = request.url.params
        if params.get("action") == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q9"}]})
        if params.get("action") == "wbgetentities":
            return httpx.Response(200, json={"entities": {"Q9": {
                "id": "Q9", "claims": {}, "sitelinks": {"enwiki": {"title": "Acme Clean"}},
                "descriptions": {"en": {"value": "Singaporean appliance manufacturer"}}}}})
        return httpx.Response(200, json={"query": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        presence = await WikipediaChecker(client).check("Acme Clean", "acmeclean.example")
    assert presence.matched_by == "name + entity description"


def test_heuristic_scores_reward_linked_profiles():
    signals = extract_signals(BRAND_HOME, "https://www.acmeclean.example/")
    weak = heuristic_scores(signals, WikiPresence(), wiki_score=20)
    notable = heuristic_scores(signals, WikiPresence(), wiki_score=80)
    assert weak["youtube"]["score"] == 35 and notable["youtube"]["score"] == 55
    assert weak["reddit"]["score"] == 10 and notable["reddit"]["score"] == 50
    assert weak["linkedin"]["sources"]
    assert weak["other"]["score"] == 18


def test_heuristic_scores_use_wikidata_social_accounts():
    signals = extract_signals("<title>Dyson</title>", "https://www.dyson.com/")  # no profile links on page
    wiki = WikiPresence(social_accounts={"youtube": 1, "linkedin": 1, "reddit": 1, "x": 1, "instagram": 1},
                        youtube_subscribers=284000)
    scores = heuristic_scores(signals, wiki, wiki_score=95)
    assert scores["youtube"]["score"] == 75
    assert any("284,000" in e for e in scores["youtube"]["evidence"])
    assert scores["linkedin"]["score"] == 55
    assert scores["reddit"]["score"] == 60
    assert scores["other"]["score"] == 10 + 16 + 15


@pytest.mark.parametrize("subs,score", [(2_000_000, 85), (284_000, 75), (12_000, 65), (1_500, 50), (10, 0)])
def test_youtube_band(subs, score):
    assert youtube_band(subs) == score


def test_social_from_claims():
    claims = {
        "P2397": [{}], "P4264": [{}, {}],
        "P8687": [
            {"mainsnak": {"datavalue": {"value": {"amount": "+232000"}}}, "qualifiers": {"P2397": []}},
            {"mainsnak": {"datavalue": {"value": {"amount": "+284000"}}}, "qualifiers": {"P2397": []}},
            {"mainsnak": {"datavalue": {"value": {"amount": "+999999"}}}, "qualifiers": {"P6552": []}},
            {"mainsnak": {"datavalue": {"value": {"amount": "bad"}}}, "qualifiers": {"P2397": []}},
        ],
    }
    accounts, subscribers = social_from_claims(claims)
    assert accounts == {"youtube": 1, "linkedin": 2}
    assert subscribers == 284000


def test_build_authority_with_claude_scores():
    signals = extract_signals(BRAND_HOME, "https://www.acmeclean.example/")
    wiki = WikiPresence(wikidata_id="Q1", article_title="Acme Clean", article_length=9000, mention_hits=3)
    claude = ClaudeAuthorityResponse.model_validate(AUTHORITY_PAYLOAD)
    result = build_authority(signals, wiki, claude)
    scores = {p["key"]: p["score"] for p in result["platforms"]}
    assert scores == {"youtube": 62, "reddit": 41, "wikipedia": 70, "linkedin": 55, "other": 48}
    expected = sum(scores[k] * w for k, w in PLATFORM_WEIGHTS.items())
    assert result["score"] == round(expected, 1)
    assert result["confidence"] == "full"
    assert result["rating"] == "中等"
    wiki_row = next(p for p in result["platforms"] if p["key"] == "wikipedia")
    assert wiki_row["sources"][0].endswith("/wiki/Acme_Clean")


def test_build_authority_without_claude_is_limited():
    signals = extract_signals(BRAND_HOME, "https://www.acmeclean.example/")
    result = build_authority(signals, WikiPresence(), None)
    assert result["confidence"] == "limited"
    assert len(result["platforms"]) == 5


def test_claude_scores_are_clamped():
    payload = {**AUTHORITY_PAYLOAD, "youtube": {"score": 140, "evidence": "not a list", "sources": []},
               "reddit": {"score": "n/a"}}
    parsed = ClaudeAuthorityResponse.model_validate(payload)
    assert parsed.youtube.score == 100 and parsed.youtube.evidence == []
    assert parsed.reddit.score == 0


@pytest.mark.parametrize("score,rating", [(90, "领先"), (75, "较强"), (55, "中等"), (35, "较弱"), (5, "极弱")])
def test_authority_rating(score, rating):
    assert authority_rating(score) == rating


async def test_wikipedia_checker_prefers_richest_website_match():
    def handler(request):
        params = request.url.params
        site = {"P856": [{"mainsnak": {"datavalue": {"value": "https://www.sony.com/en/"}}}]}
        if params.get("action") == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q2"}, {"id": "Q3"}]})
        if params.get("action") == "wbgetentities":
            return httpx.Response(200, json={"entities": {
                "Q2": {"id": "Q2", "claims": site, "sitelinks": {}},
                "Q3": {"id": "Q3", "claims": {**site, "P31": [{}] * 50}, "sitelinks": {"enwiki": {"title": "Sony"}}},
            }})
        return httpx.Response(200, json={"query": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        presence = await WikipediaChecker(client).check("Sony", "sony.net", ("sony.com",))
    assert presence.wikidata_id == "Q3" and presence.article_title == "Sony"
