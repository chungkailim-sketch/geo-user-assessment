"""AI crawler access (robots.txt + meta robots) and llms.txt, per the skill's crawler-access and
llms-txt modules."""

import re
from urllib.robotparser import RobotFileParser

from app.fullaudit.checks import clamp

# (user-agent token, platform, tier) from crawler-access.md's recommendation matrix.
AI_CRAWLERS = (
    ("GPTBot", "ChatGPT / OpenAI", 1),
    ("OAI-SearchBot", "ChatGPT 搜索", 1),
    ("ChatGPT-User", "ChatGPT 用户浏览", 1),
    ("ClaudeBot", "Claude / Anthropic", 1),
    ("PerplexityBot", "Perplexity", 1),
    ("Google-Extended", "Gemini / Google AI", 2),
    ("GoogleOther", "Google AI 研究", 2),
    ("Applebot-Extended", "Apple Intelligence", 2),
    ("Amazonbot", "Alexa / Amazon AI", 2),
    ("FacebookBot", "Meta AI", 2),
    ("CCBot", "Common Crawl（训练数据）", 3),
    ("Bytespider", "字节跳动（训练数据）", 3),
)
SEARCH_CRAWLERS = (("Googlebot", "Google 搜索 / AI概览"), ("Bingbot", "Bing / Copilot"))
KEY_CRAWLERS = ("GPTBot", "ClaudeBot", "PerplexityBot")


def parse_robots(robots_text: str | None) -> RobotFileParser | None:
    if robots_text is None:
        return None
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    return parser


def _allowed(parser: RobotFileParser | None, agent: str, url: str) -> bool:
    return True if parser is None else parser.can_fetch(agent, url)


def crawler_access(robots_text: str | None, homepage_url: str, robots_meta: str, llms_found: bool,
                   sitemap_found: bool) -> dict:
    """Per-crawler allow/block for the homepage and the AI Crawler Access score (0–100)."""
    parser = parse_robots(robots_text)
    meta_block = bool(re.search(r"\b(noai|noindex|none)\b", robots_meta or ""))
    rows = []
    for agent, platform, tier in AI_CRAWLERS:
        allowed = _allowed(parser, agent, homepage_url) and not meta_block
        rows.append({"agent": agent, "platform": platform, "tier": tier, "allowed": allowed,
                     "recommendation": _recommendation(agent, tier, allowed)})
    search = {agent: _allowed(parser, agent, homepage_url) for agent, _ in SEARCH_CRAWLERS}
    tier1 = sum(r["allowed"] for r in rows if r["tier"] == 1)
    tier2 = sum(r["allowed"] for r in rows if r["tier"] == 2)
    blanket = parser is not None and not parser.can_fetch("SomeUnlistedBot", homepage_url)
    score = (tier1 / 5 * 50) + (tier2 / 5 * 25) + (0 if blanket or meta_block else 15) \
        + (5 if llms_found else 0) + (5 if sitemap_found else 0)
    return {
        "score": round(clamp(score), 1),
        "robots_found": robots_text is not None,
        "blanket_block": blanket,
        "meta_block": meta_block,
        "crawlers": rows,
        "search": [{"agent": a, "platform": p, "allowed": search[a]} for a, p in SEARCH_CRAWLERS],
        "key_blocked": [r["agent"] for r in rows if r["agent"] in KEY_CRAWLERS and not r["allowed"]],
        "tier2_blocked": [r["agent"] for r in rows if r["tier"] == 2 and not r["allowed"]],
    }


def _recommendation(agent: str, tier: int, allowed: bool) -> str:
    if agent == "Bytespider":
        return "可按需屏蔽（抓取激进，收益较低）" if allowed else "保持屏蔽"
    if tier == 3:
        return "按数据授权策略决定"
    return "保持允许" if allowed else "建议放开，以进入AI搜索结果"


def robots_ai_points(access: dict) -> tuple[float, str]:
    """Technical-SEO 1.2 'AI crawler access' (5 points)."""
    search = {row["agent"]: row["allowed"] for row in access["search"]}
    if not search.get("Googlebot", True):
        return 0, "Googlebot 被屏蔽（严重）"
    if all(r["allowed"] for r in access["crawlers"] if r["tier"] < 3):
        return 5, "主要AI爬虫均已放行"
    if access["key_blocked"]:
        return 1, "GPTBot/ClaudeBot/PerplexityBot 中有被屏蔽的爬虫"
    return 3, "部分AI爬虫被屏蔽，但Googlebot与Bingbot可访问"


LLMS_LINK = re.compile(r"^\s*[-*]\s*\[[^\]]+\]\((https?://[^)]+)\)\s*(:\s*\S.*)?$")


def analyze_llms_txt(text: str | None, has_full: bool) -> dict:
    """Validate llms.txt structure (llms-txt.md step 2) and estimate a 0–100 quality score."""
    if text is None:
        return {"found": False, "score": 0, "full_found": has_full, "issues": ["网站根目录未发现 llms.txt 文件"],
                "entries": 0, "lines": 0}
    lines = [line.rstrip() for line in text.splitlines()]
    entries = [m for m in (LLMS_LINK.match(line) for line in lines) if m]
    checks = {
        "H1 标题（站点名称）": (any(re.match(r"^#\s+\S", line) for line in lines), 15),
        "引用块简介（> 一句话描述）": (any(line.startswith(">") for line in lines), 15),
        "至少一个 H2 分区": (any(re.match(r"^##\s+\S", line) for line in lines), 15),
        "至少 5 个页面条目": (len(entries) >= 5, 15),
        "条目均为绝对网址": (bool(entries), 10),
        "条目附有描述": (bool(entries) and all(m.group(2) for m in entries), 10),
        "关键事实（Key Facts）分区": (any(re.match(r"^##\s+(key facts|关键事实)", line, re.I) for line in lines), 10),
        "联系方式（Contact）分区": (any(re.match(r"^##\s+(contact|联系)", line, re.I) for line in lines), 5),
        "长度适中（30–200 行）": (30 <= len(lines) <= 200, 5),
    }
    score = sum(points for ok, points in checks.values() if ok)
    return {
        "found": True,
        "full_found": has_full,
        "score": score,
        "entries": len(entries),
        "lines": len(lines),
        "checks": [{"name": name, "ok": ok} for name, (ok, _) in checks.items()],
        "issues": [f"llms.txt 缺少：{name}" for name, (ok, _) in checks.items() if not ok],
    }
