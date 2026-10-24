"""Brand Authority score (GEO brand-mentions module).

Authority = YouTube*0.25 + Reddit*0.25 + Wikipedia*0.20 + LinkedIn*0.15 + Other*0.15

Wikipedia is always scored deterministically from the Wikimedia APIs. The other
platforms are scored by Claude + web search when an API key is configured, and
fall back to on-site signals (marked "limited" confidence) otherwise.
"""

from app.models import ClaudeAuthorityResponse
from app.scoring.signals import OTHER_PLATFORMS, BrandSignals
from app.scoring.wikipedia import WikiPresence, score_wikipedia

PLATFORM_WEIGHTS = {"youtube": 0.25, "reddit": 0.25, "wikipedia": 0.20, "linkedin": 0.15, "other": 0.15}
PLATFORM_LABELS = {
    "youtube": "YouTube", "reddit": "Reddit", "wikipedia": "维基百科 / Wikidata",
    "linkedin": "领英 LinkedIn", "other": "其他信源（新闻、点评、问答、GitHub）",
}
PLATFORM_SHORT_LABELS = {
    "youtube": "YouTube", "reddit": "Reddit", "wikipedia": "维基百科", "linkedin": "领英", "other": "其他信源",
}

# Recommendation lists from the GEO skill's scripts/brand_scanner.py.
RECOMMENDATIONS = {
    "youtube": [
        "在官方YouTube频道持续发布与品类相关的教程和科普内容",
        "鼓励用户与创作者发布测评、演示视频",
        "为视频添加时间戳、章节和经校对的字幕，便于AI解析",
    ],
    "reddit": [
        "在相关Subreddit中真诚参与讨论，避免营销腔",
        "开设官方Reddit账号，用于客户支持与AMA问答",
        "在用户求推荐的帖子中解答品类问题",
    ],
    "wikipedia": [
        "完善Wikidata条目，包括官方网站（P856）等关键属性",
        "通过独立媒体报道积累知名度，再争取建立维基百科词条",
        "在Organization结构化数据中添加指向维基百科/Wikidata的sameAs链接",
    ],
    "linkedin": [
        "优化领英公司主页，定期发布行业洞察内容",
        "推动管理层与员工发布领英长文",
        "在结构化数据的sameAs属性中添加领英公司主页链接",
    ],
    "other": [
        "争取主流新闻与行业媒体的近期报道",
        "维护点评平台（Trustpilot、G2）资料并鼓励用户评价",
        "在Quora与行业论坛中解答品类问题",
    ],
}


def authority_rating(score: float) -> str:
    if score >= 85:
        return "领先"
    if score >= 70:
        return "较强"
    if score >= 50:
        return "中等"
    if score >= 30:
        return "较弱"
    return "极弱"


def youtube_band(subscribers: int) -> int:
    """Subscriber-only mapping onto the YouTube rubric (third-party video volume is unverified)."""
    for threshold, score in ((1_000_000, 85), (100_000, 75), (10_000, 65), (1_000, 50)):
        if subscribers >= threshold:
            return score
    return 0


def heuristic_scores(signals: BrandSignals, wiki: WikiPresence, wiki_score: int) -> dict[str, dict]:
    """Fallback scores from on-site links and Wikidata social accounts when web search is unavailable."""
    notable = wiki_score >= 70
    accounts = wiki.social_accounts

    def linked(platform: str, found: str) -> dict:
        on_site, on_wikidata = signals.has(platform), bool(accounts.get(platform))
        evidence = []
        if on_site:
            evidence.append(f"官网已链接官方{found}")
        if on_wikidata:
            evidence.append(f"Wikidata已收录官方{found}")
        if on_site or on_wikidata:
            score = 55 if notable else 35
        else:
            score = 25 if notable else 5
            evidence.append(f"官网及Wikidata均未发现官方{found}")
        return {"score": score, "evidence": evidence, "sources": signals.profiles.get(platform, [])[:3]}

    youtube = linked("youtube", "YouTube频道")
    if wiki.youtube_subscribers:
        youtube["score"] = max(youtube["score"], youtube_band(wiki.youtube_subscribers))
        youtube["evidence"].append(f"YouTube订阅数 {wiki.youtube_subscribers:,}（来源：Wikidata）")

    reddit_has = signals.has("reddit") or bool(accounts.get("reddit"))
    reddit = {
        "score": (50 if notable else 10) + (10 if reddit_has else 0),
        "evidence": ["未能核实Reddit讨论量（联网搜索不可用）"]
        + (["已发现官方Subreddit或Reddit账号"] if reddit_has else []),
        "sources": signals.profiles.get("reddit", [])[:3],
    }
    other_platforms = {p for p in OTHER_PLATFORMS if signals.has(p)} | {p for p in ("x", "instagram", "facebook") if accounts.get(p)}
    other = {
        "score": min(80, 10 + 8 * len(other_platforms) + (15 if notable else 0)),
        "evidence": [f"发现{len(other_platforms)}个其他平台官方账号：{'、'.join(sorted(other_platforms)) or '无'}"],
        "sources": [url for p in ("instagram", "x", "facebook", "tiktok") for url in signals.profiles.get(p, [])[:1]],
    }
    return {
        "youtube": youtube,
        "reddit": reddit,
        "linkedin": linked("linkedin", "领英公司主页"),
        "other": other,
    }


def build_authority(
    signals: BrandSignals,
    wiki: WikiPresence,
    claude: ClaudeAuthorityResponse | None,
) -> dict:
    wiki_score, wiki_reason = score_wikipedia(wiki)
    wiki_evidence = [wiki_reason]
    if wiki.article_title:
        wiki_evidence.append(f"英文维基百科词条：{wiki.article_title}（{wiki.article_length:,} 字节）")
    if wiki.domain_citations:
        wiki_evidence.append(f"官网被{wiki.domain_citations}篇维基百科词条引用")
    wiki_sources = []
    if wiki.article_title:
        wiki_sources.append("https://en.wikipedia.org/wiki/" + wiki.article_title.replace(" ", "_"))
    if wiki.wikidata_id:
        wiki_sources.append(f"https://www.wikidata.org/wiki/{wiki.wikidata_id}")

    if claude is not None:
        platforms = {name: getattr(claude, name).model_dump() for name in ("youtube", "reddit", "linkedin", "other")}
        confidence = "full"
    else:
        platforms = heuristic_scores(signals, wiki, wiki_score)
        confidence = "limited"
    platforms["wikipedia"] = {"score": wiki_score, "evidence": wiki_evidence, "sources": wiki_sources}

    composite = sum(platforms[name]["score"] * weight for name, weight in PLATFORM_WEIGHTS.items())
    ordered = [
        {
            "key": name,
            "label": PLATFORM_LABELS[name],
            "short_label": PLATFORM_SHORT_LABELS[name],
            "weight": weight,
            "score": platforms[name]["score"],
            "weighted": round(platforms[name]["score"] * weight, 1),
            "evidence": platforms[name]["evidence"],
            "sources": platforms[name]["sources"],
        }
        for name, weight in PLATFORM_WEIGHTS.items()
    ]
    return {
        "score": round(composite, 1),
        "rating": authority_rating(composite),
        "confidence": confidence,
        "brand_name": signals.brand_name,
        "platforms": ordered,
        "wikidata_id": wiki.wikidata_id,
        "profiles_found": sorted(signals.profiles),
    }
