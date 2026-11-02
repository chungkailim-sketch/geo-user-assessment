"""Content E-E-A-T (content-eeat.md: four 25-point dimensions plus a topical-authority modifier).

`heuristic_eeat` scores the rubric from signals visible in the crawled pages. When Claude is available the
full-report analysis re-scores the four dimensions from page excerpts and these signals, and that judgement
replaces the heuristic (see `app.fullaudit.analysis`).
"""

from app.fullaudit.checks import clamp
from app.fullaudit.page_facts import PageFacts

DIMENSIONS = (("experience", "经验（Experience）"), ("expertise", "专业性（Expertise）"),
              ("authoritativeness", "权威性（Authoritativeness）"), ("trustworthiness", "可信度（Trustworthiness）"))


def eeat_signals(pages: list[PageFacts], authority: dict) -> dict:
    words = [p.word_count for p in pages]
    hints = set().union(*(p.link_hints for p in pages)) if pages else set()
    trust = set().union(*(p.trust_words for p in pages)) if pages else set()
    platforms = {p["key"]: p["score"] for p in authority["platforms"]}
    return {
        "pages": len(pages),
        "avg_words": round(sum(words) / len(words)) if words else 0,
        "first_person": sum(p.first_person for p in pages),
        "stats": sum(p.stats for p in pages),
        "author": any(p.has_author for p in pages),
        "dates": any(p.dates for p in pages),
        "email": any(p.has_email for p in pages),
        "phone": any(p.has_phone for p in pages),
        "address": any(p.has_address for p in pages),
        "privacy": "privacy" in hints,
        "terms": "terms" in hints,
        "about": "about" in hints,
        "contact_page": "contact" in hints,
        "https": all(p.url.startswith("https://") for p in pages),
        "case_study": "case_study" in trust,
        "award": "award" in trust,
        "testimonial": "testimonial" in trust,
        "research": "research" in trust,
        "methodology": "methodology" in trust,
        "editorial": "editorial" in trust,
        "disclosure": "disclosure" in trust,
        "images": sum(p.images for p in pages),
        "wikipedia_score": platforms.get("wikipedia", 0),
        "press_score": platforms.get("other", 0),
    }


def heuristic_eeat(s: dict) -> dict:
    experience = ((5 if s["first_person"] >= 10 else 3 if s["first_person"] else 0) + (5 if s["research"] else 0)
                  + (4 if s["case_study"] else 0) + (1 if s["images"] >= 5 else 0) + (2 if s["stats"] >= 5 else 0)
                  + (2 if s["methodology"] else 0))
    expertise = ((3 if s["author"] else 0) + (5 if s["avg_words"] >= 800 else 3 if s["avg_words"] >= 400 else 1)
                 + (2 if s["methodology"] else 0) + (4 if s["stats"] >= 10 else 2 if s["stats"] >= 3 else 0)
                 + 1 + (2 if s["about"] else 0))
    authoritativeness = ((5 if s["wikipedia_score"] >= 70 else 3 if s["wikipedia_score"] >= 30 else 0)
                         + (4 if s["press_score"] >= 70 else 2 if s["press_score"] >= 40 else 0)
                         + (3 if s["award"] else 0) + (2 if s["press_score"] >= 60 else 0)
                         + (3 if s["pages"] >= 8 else 1 if s["pages"] >= 4 else 0)
                         + (3 if s["wikipedia_score"] >= 50 else 0))
    trustworthiness = ((4 if s["phone"] and (s["address"] or s["email"]) else 2 if s["email"] or s["contact_page"] else 0)
                       + (2 if s["privacy"] else 0) + (1 if s["terms"] else 0) + (2 if s["https"] else 0)
                       + (3 if s["editorial"] else 1 if s["about"] else 0) + (3 if s["disclosure"] else 1)
                       + (3 if s["testimonial"] else 0) + 3 + (3 if s["disclosure"] else 0))
    modifier = 5 if s["pages"] >= 10 else 0 if s["pages"] >= 5 else -5
    return build_eeat(
        {"experience": experience, "expertise": expertise, "authoritativeness": authoritativeness,
         "trustworthiness": trustworthiness}, modifier, strengths=[], weaknesses=_weaknesses(s), source="heuristic")


def _weaknesses(s: dict) -> list[str]:
    notes = []
    if not s["author"]:
        notes.append("内容页缺少作者署名与资历介绍，AI难以判断内容专业性")
    if not s["dates"]:
        notes.append("页面未标注发布或更新日期，AI可能视为过时内容")
    if not (s["research"] or s["case_study"]):
        notes.append("缺少原创研究、独家数据或客户案例等一手经验证据")
    if not (s["email"] or s["phone"] or s["contact_page"]):
        notes.append("未发现清晰的联系方式（电话、邮箱或联系页面）")
    if not s["privacy"]:
        notes.append("未发现隐私政策链接")
    if s["stats"] < 3:
        notes.append("正文中可被引用的具体数据较少")
    return notes


def build_eeat(dims: dict, modifier: float, strengths: list[str], weaknesses: list[str], source: str) -> dict:
    scores = {key: round(clamp(float(dims.get(key, 0)), 0, 25), 1) for key, _ in DIMENSIONS}
    modifier = clamp(float(modifier), -5, 10)
    return {
        "score": round(clamp(sum(scores.values()) + modifier), 1),
        "dimensions": [{"key": k, "label": label, "score": scores[k], "max": 25} for k, label in DIMENSIONS],
        "topical_modifier": modifier,
        "strengths": strengths[:5],
        "weaknesses": weaknesses[:6],
        "source": source,
    }
