import pytest

from app.industry import Industry, resolve_industry
from app.scoring.signals import choose_brand_name, extract_signals, registrable_domain
from tests.conftest import BRAND_HOME

pytestmark = pytest.mark.unit


def test_extract_signals_reads_schema_and_profiles():
    signals = extract_signals(BRAND_HOME, "https://www.acmeclean.example/")
    assert signals.domain == "acmeclean.example"
    assert signals.brand_name == "Acme Clean"
    assert signals.has_organization_schema
    assert signals.has("youtube") and signals.has("linkedin") and signals.has("instagram")
    assert not signals.has("reddit")
    assert signals.other_platform_count() == 1
    assert "youtube" in signals.to_dict()["profiles"]


def test_extract_signals_uses_identity_domain_after_geo_redirect():
    html = "<title>Dyson Singapore | Official Site</title>"
    signals = extract_signals(html, "https://www.dyson.com.sg/", "dyson.com")
    assert signals.domain == "dyson.com"
    assert signals.brand_name == "Dyson"


@pytest.mark.parametrize(
    "candidates,domain,expected",
    [
        (["Apple"], "apple.com", "Apple"),
        (["", "Booking.com", "Hotels"], "booking.com", "Booking.com"),
        (["Official Site", "Dyson Singapore"], "dyson.com", "Dyson"),
        (["Home", "Welcome"], "acme-tools.com", "Acme Tools"),
        (["Acme Tools Online"], "acme-tools.com", "Acme Tools"),
    ],
)
def test_choose_brand_name(candidates, domain, expected):
    assert choose_brand_name(candidates, domain) == expected


def test_registrable_domain_strips_www():
    assert registrable_domain("https://WWW.Example.com/path") == "example.com"


DYSON = {"name": "Smart Cleaning", "name_zh": "智能清洁", "leader": "Dyson",
         "leader_zh": "戴森", "leader_url": "https://www.dyson.com/"}


def test_industry_from_ai_answer():
    industry = Industry.from_dict(DYSON)
    assert industry.leader_domain == "dyson.com"
    assert industry.is_leader("dyson.com") and not industry.is_leader("acme.example")
    assert industry.to_dict() == {**DYSON, "alternates": []}


def test_industry_falls_back_to_english_names():
    industry = Industry.from_dict({"name": "Banking", "leader": "JPMorgan Chase", "leader_url": "https://www.jpmorganchase.com"})
    assert industry.name_zh == "Banking" and industry.leader_zh == "JPMorgan Chase"


def test_resolve_industry_among_audit_candidates():
    typed = {**DYSON, "name": "Pet Food", "leader": "Purina", "leader_url": "https://www.purina.com"}
    assert resolve_industry("smart cleaning", DYSON, typed).leader == "Dyson"
    assert resolve_industry("Pet Food", DYSON, None, typed).leader == "Purina"
    assert resolve_industry("Automotive", DYSON) is None  # no fixed catalogue any more
    assert resolve_industry(None, DYSON) is None

def test_candidates_rank_leader_first_and_skip_duplicate_domains():
    industry = Industry.from_dict({**DYSON, "alternates": [
        {"leader": "Dyson CN", "leader_zh": "戴森", "leader_url": "https://www.dyson.com/"},
        {"leader": "Ecovacs", "leader_zh": "科沃斯", "leader_url": "https://www.ecovacs.com/"},
    ]})
    assert [c["leader"] for c in industry.candidates()] == ["Dyson", "Ecovacs"]
    stand_in = industry.with_leader(industry.candidates()[1])
    assert (stand_in.leader_zh, stand_in.leader_domain, stand_in.name) == ("科沃斯", "ecovacs.com", "Smart Cleaning")
    assert industry.leader == "Dyson"  # the original is unchanged
