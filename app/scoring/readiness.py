"""AI Readiness Score = Citability x 0.55 + Authority x 0.45, plus findings and actions."""

from app.scoring.authority import RECOMMENDATIONS
from app.scoring.citability import REWRITE_TIPS, SUBSCORE_LABELS

CITABILITY_WEIGHT = 0.55
AUTHORITY_WEIGHT = 0.45


def readiness_score(citability: float, authority: float) -> float:
    return round(citability * CITABILITY_WEIGHT + authority * AUTHORITY_WEIGHT, 1)


def readiness_label(score: float) -> str:
    """Score labels from the GEO skill (SKILL.md)."""
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Moderate"
    if score >= 40:
        return "Below average"
    return "Needs attention"


def build_findings(citability: dict, authority: dict) -> list[dict]:
    findings: list[dict] = []
    if citability["blocks_analyzed"] == 0:
        findings.append({
            "severity": "critical",
            "text": "No extractable text content found. The site may rely on JavaScript rendering that AI crawlers cannot read.",
        })
    elif citability["score"] < 50:
        findings.append({"severity": "high", "text": f"Average passage citability is {citability['score']:.0f}/100, below the 50-point threshold."})
    if citability["blocks_analyzed"] and citability["coverage_pct"] < 20:
        findings.append({"severity": "medium", "text": f"Only {citability['coverage_pct']:.0f}% of content blocks are highly citable (score above 70)."})
    if citability["pages_without_content"]:
        findings.append({"severity": "medium", "text": f"{len(citability['pages_without_content'])} page(s) had no readable passages for AI systems."})
    for platform in authority["platforms"]:
        if platform["score"] < 30:
            findings.append({"severity": "high", "text": f"Weak {platform['label']} presence ({platform['score']}/100)."})
        elif platform["score"] >= 85:
            findings.append({"severity": "strength", "text": f"Strong {platform['label']} presence ({platform['score']}/100)."})
    if authority["confidence"] == "limited":
        findings.append({"severity": "info", "text": "Off-site authority was estimated from on-site signals only (web search unavailable)."})
    return findings


def build_actions(citability: dict, authority: dict, limit: int = 5) -> list[dict]:
    """Rank improvement actions by weighted point gap (largest potential lift first)."""
    candidates: list[dict] = []
    for key, value in citability["subscores"].items():
        gap = (100 - value) * CITABILITY_WEIGHT * _citability_share(key)
        candidates.append({
            "area": f"Citability: {SUBSCORE_LABELS[key]}",
            "action": REWRITE_TIPS[key],
            "current": value,
            "potential_lift": round(gap, 1),
        })
    for platform in authority["platforms"]:
        gap = (100 - platform["score"]) * AUTHORITY_WEIGHT * platform["weight"]
        candidates.append({
            "area": f"Authority: {platform['label']}",
            "action": RECOMMENDATIONS[platform["key"]][0],
            "current": platform["score"],
            "potential_lift": round(gap, 1),
        })
    candidates.sort(key=lambda c: c["potential_lift"], reverse=True)
    return candidates[:limit]


def _citability_share(key: str) -> float:
    return {
        "answer_block_quality": 0.30,
        "self_containment": 0.25,
        "structural_readability": 0.20,
        "statistical_density": 0.15,
        "uniqueness_signals": 0.10,
    }[key]
