import time

import pytest

from app.benchmark import compare, leader_summary
from app.cache import JsonCache
from app.industry import Industry
from app.scoring.readiness import build_actions, build_findings, readiness_label, readiness_score

pytestmark = pytest.mark.unit


def citability_fixture(score=40.0, blocks=10, coverage=10.0, empty_pages=()):
    return {
        "score": score, "blocks_analyzed": blocks, "coverage_pct": coverage,
        "pages_without_content": list(empty_pages),
        "subscores": {"answer_block_quality": 50.0, "self_containment": 60.0, "structural_readability": 40.0,
                      "statistical_density": 10.0, "uniqueness_signals": 20.0},
    }


def authority_fixture(scores=None, confidence="full"):
    scores = scores or {"youtube": 20, "reddit": 40, "wikipedia": 90, "linkedin": 50, "other": 30}
    weights = {"youtube": 0.20, "reddit": 0.20, "wikipedia": 0.15, "linkedin": 0.10, "other": 0.35}
    return {
        "score": sum(scores[k] * w for k, w in weights.items()),
        "confidence": confidence,
        "platforms": [{"key": k, "label": k.title(), "weight": w, "score": scores[k]} for k, w in weights.items()],
    }


def audit_fixture(domain, score, cit, auth):
    authority = authority_fixture()
    authority["score"] = auth
    return {"domain": domain, "brand_name": domain, "url": f"https://{domain}/", "score": score, "label": "x",
            "audited_at": "2026-01-01", "citability": {**citability_fixture(cit)}, "authority": authority}


def test_readiness_formula_uses_required_weights():
    assert readiness_score(80, 60) == round(80 * 0.55 + 60 * 0.45, 1)
    assert readiness_score(100, 100) == 100
    assert readiness_score(0, 0) == 0


@pytest.mark.parametrize("score,label", [(90, "优秀"), (85, "优秀"), (70, "良好"), (60, "中等"),
                                         (45, "偏低"), (39.9, "亟需优化")])
def test_readiness_labels(score, label):
    assert readiness_label(score) == label


def test_findings_flag_missing_content_and_weak_platforms():
    findings = build_findings(citability_fixture(score=0, blocks=0, coverage=0), authority_fixture(confidence="limited"))
    severities = [f["severity"] for f in findings]
    assert severities[0] == "critical"
    assert "strength" in severities  # wikipedia 90
    assert "info" in severities  # limited confidence
    assert any("Youtube" in f["text"] for f in findings if f["severity"] == "high")


def test_findings_for_low_citability_and_empty_pages():
    findings = build_findings(citability_fixture(score=30, empty_pages=["https://a/x"]), authority_fixture())
    texts = " ".join(f["text"] for f in findings)
    assert "低于50分基准线" in texts
    assert "1个页面" in texts


def test_actions_ranked_by_potential_lift():
    actions = build_actions(citability_fixture(), authority_fixture())
    ranked = actions[:-1]
    lifts = [a["potential_lift"] for a in ranked]
    assert lifts == sorted(lifts, reverse=True)
    assert len(ranked) == 5
    assert ranked[0]["area"].startswith(("可引用性", "品牌 AI 引用权威度"))


def test_pr_media_strategy_is_always_included():
    for cit, auth in ((citability_fixture(score=10), authority_fixture()),
                      (citability_fixture(score=95), authority_fixture({k: 95 for k in
                       ("youtube", "reddit", "wikipedia", "linkedin", "other")}))):
        strategy = build_actions(cit, auth)[-1]
        assert strategy["kind"] == "strategy"
        assert "公关与媒体" in strategy["area"]
        assert "品牌叙事" in strategy["action"] and "AI" in strategy["action"]


SMART_CLEANING = Industry("Smart Cleaning", "智能清洁", "Dyson", "戴森", "https://www.dyson.com/")
COFFEE = Industry("Coffee Chains", "咖啡连锁", "Starbucks", "星巴克", "https://www.starbucks.com/")


def test_compare_brand_with_leader():
    industry = SMART_CLEANING
    brand = audit_fixture("acme.example", 40, 35, 46)
    leader = audit_fixture("dyson.com", 70, 60, 82)
    result = compare(brand, leader, industry)
    assert result["status"] == "ok" and not result["is_leader"]
    assert result["gaps"]["overall"] == -30
    assert result["gaps"]["citability"] == -25
    assert result["leader_scores"]["citability"] == 60


def test_compare_when_brand_is_leader():
    industry = SMART_CLEANING
    brand = audit_fixture("dyson.com", 70, 60, 82)
    result = compare(brand, None, industry)
    assert result["is_leader"] and result["gaps"]["overall"] == 0


def test_compare_leader_unavailable():
    result = compare(audit_fixture("acme.example", 40, 35, 46), None, COFFEE)
    assert result["status"] == "unavailable" and result["gaps"] is None


def test_leader_summary_shape():
    summary = leader_summary(audit_fixture("dyson.com", 70, 60, 82))
    assert set(summary["platforms"]) == {"youtube", "reddit", "wikipedia", "linkedin", "other"}


def test_cache_roundtrip_and_ttl(tmp_path):
    cache = JsonCache(tmp_path / "c.sqlite3")
    cache.set("k", {"a": 1})
    assert cache.get("k", 60) == {"a": 1}
    assert cache.get("missing", 60) is None
    time.sleep(0.01)
    assert cache.get("k", 0.001) is None
    cache.close()


def test_compare_unavailable_carries_reason():
    result = compare(audit_fixture("acme.example", 40, 35, 46), None, COFFEE, reason="blocked")
    assert result["reason"] == "blocked"


def test_compare_reports_ai_identified_industry_and_leader():
    result = compare(audit_fixture("acme.example", 40, 35, 46), None, SMART_CLEANING)
    assert (result["industry_zh"], result["leader_zh"], result["leader_url"]) == ("智能清洁", "戴森", "https://www.dyson.com/")
