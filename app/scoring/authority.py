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
    "youtube": "YouTube", "reddit": "Reddit", "wikipedia": "Wikipedia / Wikidata",
    "linkedin": "LinkedIn", "other": "Other (news, reviews, Quora, GitHub)",
}

# Recommendation lists from the GEO skill's scripts/brand_scanner.py.
RECOMMENDATIONS = {
    "youtube": [
        "Publish educational/tutorial content related to your category on an official YouTube channel",
        "Encourage customers and creators to make review/demo videos",
        "Add timestamps, chapters and reviewed transcripts to improve AI parseability",
    ],
    "reddit": [
        "Participate authentically in relevant subreddits (no marketing speak)",
        "Create an official Reddit account for customer support and AMAs",
        "Answer category questions where buyers ask for recommendations",
    ],
    "wikipedia": [
        "Ensure a complete Wikidata entry, including the official website (P856)",
        "Build notability through independent press coverage, then pursue a Wikipedia article",
        "Add sameAs links in Organization schema pointing to Wikipedia/Wikidata",
    ],
    "linkedin": [
        "Optimise the LinkedIn company page and post thought leadership regularly",
        "Have leadership and employees publish long-form LinkedIn articles",
        "Add the LinkedIn company URL to the schema sameAs property",
    ],
    "other": [
        "Earn recent coverage in major news and trade publications",
        "Maintain review profiles (Trustpilot, G2) and encourage customer reviews",
        "Answer category questions on Quora and industry forums",
    ],
}


def authority_rating(score: float) -> str:
    if score >= 85:
        return "Dominant"
    if score >= 70:
        return "Strong"
    if score >= 50:
        return "Moderate"
    if score >= 30:
        return "Weak"
    return "Minimal"


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
            evidence.append(f"Official {found} linked from the website")
        if on_wikidata:
            evidence.append(f"Official {found} recorded on Wikidata")
        if on_site or on_wikidata:
            score = 55 if notable else 35
        else:
            score = 25 if notable else 5
            evidence.append(f"No official {found} found on the website or Wikidata")
        return {"score": score, "evidence": evidence, "sources": signals.profiles.get(platform, [])[:3]}

    youtube = linked("youtube", "YouTube channel")
    if wiki.youtube_subscribers:
        youtube["score"] = max(youtube["score"], youtube_band(wiki.youtube_subscribers))
        youtube["evidence"].append(f"{wiki.youtube_subscribers:,} YouTube subscribers (Wikidata)")

    reddit_has = signals.has("reddit") or bool(accounts.get("reddit"))
    reddit = {
        "score": (50 if notable else 10) + (10 if reddit_has else 0),
        "evidence": ["Reddit discussion volume not verified (web search unavailable)"]
        + (["Official subreddit or Reddit account found"] if reddit_has else []),
        "sources": signals.profiles.get("reddit", [])[:3],
    }
    other_platforms = {p for p in OTHER_PLATFORMS if signals.has(p)} | {p for p in ("x", "instagram", "facebook") if accounts.get(p)}
    other = {
        "score": min(80, 10 + 8 * len(other_platforms) + (15 if notable else 0)),
        "evidence": [f"{len(other_platforms)} other platform profile(s) found: {', '.join(sorted(other_platforms)) or 'none'}"],
        "sources": [url for p in ("instagram", "x", "facebook", "tiktok") for url in signals.profiles.get(p, [])[:1]],
    }
    return {
        "youtube": youtube,
        "reddit": reddit,
        "linkedin": linked("linkedin", "LinkedIn company page"),
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
        wiki_evidence.append(f"English Wikipedia article: {wiki.article_title} ({wiki.article_length:,} bytes)")
    if wiki.domain_citations:
        wiki_evidence.append(f"Website cited in {wiki.domain_citations} Wikipedia article(s)")
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
