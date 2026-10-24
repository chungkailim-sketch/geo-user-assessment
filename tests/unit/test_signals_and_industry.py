import pytest

from app.industry import detect_industry, get_industry, industry_for_leader_domain, keyword_scores, load_industries
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


def test_industries_catalogue_matches_requirements():
    leaders = {i.name: i.leader for i in load_industries()}
    assert leaders == {
        "Automotive": "Toyota", "Smart Phone": "Apple", "Consumer Electronics": "Sony",
        "Smart Home IoT": "Samsung", "Smart Cleaning": "Dyson", "F&B": "Starbucks",
        "Culture / Lifestyle": "Booking.com",
    }


def test_get_industry_is_case_insensitive():
    assert get_industry("f&b").leader == "Starbucks"
    assert get_industry("nope") is None
    assert get_industry(None) is None


def test_leader_domain_detection():
    assert industry_for_leader_domain("toyota.com").name == "Automotive"
    assert detect_industry("booking.com", "").method == "leader domain"


def test_keyword_detection():
    text = "Robot vacuum and cordless vacuum cleaning. Our vacuum cleaner has strong suction."
    detection = detect_industry("acmeclean.example", text)
    assert detection.industry == "Smart Cleaning"
    assert detection.method == "keywords"


def test_keyword_detection_ambiguous_and_none():
    assert detect_industry("x.example", "nothing relevant here") is None
    ambiguous = detect_industry("x.example", "coffee travel")
    assert ambiguous.method == "ambiguous"


def test_keyword_scores_respect_word_boundaries():
    scores = keyword_scores("scar carpet")  # 'car' must not match inside words
    assert scores["Automotive"] == 0


def test_leader_aliases():
    sony = get_industry("Consumer Electronics")
    assert sony.leader_domain == "sony.net"
    assert sony.is_leader("sony.com") and sony.is_leader("sony.net")
    assert industry_for_leader_domain("dyson.com.sg").leader == "Dyson"
