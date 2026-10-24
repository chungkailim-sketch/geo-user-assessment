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
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "中等"
    if score >= 40:
        return "偏低"
    return "亟需优化"


def build_findings(citability: dict, authority: dict) -> list[dict]:
    findings: list[dict] = []
    if citability["blocks_analyzed"] == 0:
        findings.append({
            "severity": "critical",
            "text": "未检测到可提取的文本内容。网站可能依赖JavaScript渲染，AI爬虫难以读取（存在感问题）。",
        })
    elif citability["score"] < 50:
        findings.append({"severity": "high", "text": f"段落平均可引用性为{citability['score']:.0f}/100，低于50分基准线。"})
    if citability["blocks_analyzed"] and citability["coverage_pct"] < 20:
        findings.append({"severity": "medium", "text": f"仅{citability['coverage_pct']:.0f}%的内容块具备高可引用性（得分高于70）。"})
    if citability["pages_without_content"]:
        findings.append({"severity": "medium", "text": f"{len(citability['pages_without_content'])}个页面没有可供AI读取的段落。"})
    for platform in authority["platforms"]:
        if platform["score"] < 30:
            findings.append({"severity": "high", "text": f"{platform['label']}存在感较弱（{platform['score']}/100）。"})
        elif platform["score"] >= 85:
            findings.append({"severity": "strength", "text": f"{platform['label']}表现突出（{platform['score']}/100）。"})
    if authority["confidence"] == "limited":
        findings.append({"severity": "info", "text": "站外权威度仅基于官网与Wikidata信号估算（联网搜索不可用）。"})
    return findings


def build_actions(citability: dict, authority: dict, limit: int = 5) -> list[dict]:
    """Rank improvement actions by weighted point gap (largest potential lift first)."""
    candidates: list[dict] = []
    for key, value in citability["subscores"].items():
        gap = (100 - value) * CITABILITY_WEIGHT * _citability_share(key)
        candidates.append({
            "area": f"可引用性：{SUBSCORE_LABELS[key]}",
            "action": REWRITE_TIPS[key],
            "current": value,
            "potential_lift": round(gap, 1),
        })
    for platform in authority["platforms"]:
        gap = (100 - platform["score"]) * AUTHORITY_WEIGHT * platform["weight"]
        candidates.append({
            "area": f"品牌权威度：{platform['label']}",
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
