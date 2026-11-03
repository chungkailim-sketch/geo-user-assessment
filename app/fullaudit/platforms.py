"""AI platform readiness (platform-optimizer.md): Google AI Overviews, ChatGPT, Perplexity, Gemini and Bing
Copilot, each scored on its 100-point rubric over the criteria measurable from outside the site."""

from datetime import date

from app.fullaudit.checks import Check, score_checks
from app.fullaudit.page_facts import PageFacts

PLATFORM_NAMES = (("google_aio", "Google AI概览"), ("chatgpt", "ChatGPT 联网搜索"), ("perplexity", "Perplexity"),
                  ("gemini", "Google Gemini"), ("copilot", "Bing Copilot"))


def _band(score: float, high: float, low: float, full: float, half: float) -> float:
    return full if score >= high else half if score >= low else 0


def _months_since_latest(pages: list[PageFacts], today: date) -> int | None:
    stamps = sorted(d for p in pages for d in p.dates if len(d) >= 7 and d[:4].isdigit() and d[5:7].isdigit())
    if not stamps:
        return None
    year, month = int(stamps[-1][:4]), int(stamps[-1][5:7])
    return max(0, (today.year - year) * 12 + today.month - month)


def analyze_platforms(pages: list[PageFacts], citability: dict, authority: dict, schema: dict, eeat: dict,
                      today: date | None = None) -> dict:
    today = today or date.today()
    home = pages[0]
    plat = {p["key"]: p["score"] for p in authority["platforms"]}
    question = sum(p.question_headings for p in pages)
    stats = sum(p.stats for p in pages)
    images = sum(p.images for p in pages)
    missing_alt = sum(p.images_missing_alt for p in pages)
    with_desc = sum(1 for p in pages if p.meta_description)
    titled = sum(1 for p in pages if p.title and p.h1_count)
    quotable = sum(1 for b in citability.get("top_passages", []) if b["score"] >= 70)
    months = _months_since_latest(pages, today)
    verified = set().union(*(p.verifications for p in pages))
    clean_headings = home.h1_count == 1 and all(b - a <= 1 for a, b in zip(home.heading_levels, home.heading_levels[1:]))
    answer = citability["subscores"].get("answer_block_quality", 0)
    max_words = max(p.word_count for p in pages)
    external = sum(p.external_links for p in pages)

    checks = {
        "google_aio": [
            Check("google_aio", "目标问题排名前10", 20, None, "需结合搜索排名数据"),
            Check("google_aio", "问句式小标题", 10, min(10, 2 * question), f"{question} 个问句式小标题"),
            Check("google_aio", "小标题后直接给出答案", 15, round(15 * answer / 100, 1), f"答案块质量 {answer:.0f}/100"),
            Check("google_aio", "对比表格", 10, 10 if any(p.tables for p in pages) else 0, ""),
            Check("google_aio", "流程/功能列表", 10, 10 if sum(p.lists for p in pages) >= 3 else 5 if any(p.lists for p in pages) else 0, ""),
            Check("google_aio", "FAQ（5个以上问题）", 10, 10 if question >= 5 else 5 if question else 0, ""),
            Check("google_aio", "带出处的统计数据", 10, min(10, 2 * stats), f"{stats} 处数据"),
            Check("google_aio", "发布/更新日期", 5, 5 if months is not None and months <= 12 else 3 if months is not None else 0, ""),
            Check("google_aio", "作者署名", 5, 3 if any(p.has_author for p in pages) else 0, ""),
            Check("google_aio", "标题层级清晰（H1>H2>H3）", 5, 5 if clean_headings else 3 if home.h1_count else 0, ""),
        ],
        "chatgpt": [
            Check("chatgpt", "维基百科词条", 20, _band(plat.get("wikipedia", 0), 60, 25, 20, 10), ""),
            Check("chatgpt", "Wikidata 实体", 10, 10 if authority.get("wikidata_id") else 0, authority.get("wikidata_id") or "未找到"),
            Check("chatgpt", "Bing 索引覆盖", 10, None, "需 Bing Webmaster 数据"),
            Check("chatgpt", "Reddit 正面讨论", 10, _band(plat.get("reddit", 0), 70, 30, 10, 5), ""),
            Check("chatgpt", "YouTube 相关内容", 10, _band(plat.get("youtube", 0), 70, 30, 10, 5), ""),
            Check("chatgpt", "权威外链与媒体报道", 15, round(15 * plat.get("other", 0) / 100, 1), "以第三方信源得分估算"),
            Check("chatgpt", "跨平台实体一致性（sameAs）", 10, 10 if len(schema["same_as"]) >= 3 else 5 if schema["same_as"] else 0, ""),
            Check("chatgpt", "内容全面性（2000词以上）", 10, 10 if max_words >= 2000 else 5 if max_words >= 800 else 0, f"最长页面约 {max_words} 词"),
            Check("chatgpt", "Bing Webmaster 已验证", 5, 5 if "bing" in verified else None, "已发现验证标记" if "bing" in verified else "未能从外部确认"),
        ],
        "perplexity": [
            Check("perplexity", "Reddit 社区活跃度", 20, _band(plat.get("reddit", 0), 70, 30, 20, 10), ""),
            Check("perplexity", "论坛/问答社区提及", 10, _band(plat.get("other", 0), 60, 30, 10, 5), ""),
            Check("perplexity", "内容新鲜度（6个月内更新）", 10,
                  0 if months is None else 10 if months <= 6 else 5 if months <= 12 else 0,
                  "未发现日期" if months is None else f"最近日期约 {months} 个月前"),
            Check("perplexity", "原创研究/数据", 15, 15 if eeat.get("research") else 10 if eeat.get("case_study") else 5 if stats >= 5 else 0, ""),
            Check("perplexity", "YouTube 内容", 10, _band(plat.get("youtube", 0), 70, 30, 10, 5), ""),
            Check("perplexity", "可独立引用的段落", 10, min(10, 2 * quotable), f"{quotable} 段高分段落"),
            Check("perplexity", "多来源佐证", 10, 10 if external >= 10 else 5 if external >= 3 else 0, f"{external} 个外部链接"),
            Check("perplexity", "引发讨论的内容", 10, None, "需社交传播数据"),
            Check("perplexity", "维基百科/Wikidata", 5, 5 if plat.get("wikipedia", 0) >= 25 else 0, ""),
        ],
        "gemini": [
            Check("gemini", "Google 知识面板", 15, None, "需在 Google 搜索中人工确认"),
            Check("gemini", "Google 商家资料", 10, None, "需人工确认"),
            Check("gemini", "YouTube 频道", 20, _band(plat.get("youtube", 0), 70, 30, 20, 10), ""),
            Check("gemini", "Schema 结构化数据", 15, _band(schema["score"], 70, 40, 15, 10) or (5 if schema["has_any"] else 0), ""),
            Check("gemini", "Google 生态（学术/新闻/地图）", 10, None, "需人工确认"),
            Check("gemini", "图片 alt 文本", 10, round(10 * (images - missing_alt) / images, 1) if images else None,
                  f"{images - missing_alt}/{images} 张图片有 alt" if images else "无图片"),
            Check("gemini", "E-E-A-T 信号", 10, 10 if eeat["score"] >= 70 else 5 if eeat["score"] >= 40 else 0, ""),
            Check("gemini", "多模态内容（图文+视频）", 5, 5 if images and any(p.videos for p in pages) else 3 if images else 0, ""),
        ],
        "copilot": [
            Check("copilot", "Bing Webmaster + 站点地图", 15, 15 if "bing" in verified else None, "未能从外部确认" if "bing" not in verified else ""),
            Check("copilot", "IndexNow 协议", 15, None, "需站长确认"),
            Check("copilot", "Bing 索引覆盖", 10, None, "需 Bing Webmaster 数据"),
            Check("copilot", "领英公司主页", 10, _band(plat.get("linkedin", 0), 70, 30, 10, 5), ""),
            Check("copilot", "Meta 描述", 10, round(10 * with_desc / len(pages), 1), f"{with_desc}/{len(pages)} 个页面"),
            Check("copilot", "社交媒体互动", 10, _band(plat.get("other", 0), 60, 30, 10, 5), ""),
            Check("copilot", "标题与H1关键词", 10, round(10 * titled / len(pages), 1), f"{titled}/{len(pages)} 个页面有标题与H1"),
            Check("copilot", "页面速度（2秒内）", 10, 10 if home.elapsed_ms < 1000 else 5 if home.elapsed_ms < 2500 else 0,
                  f"首页响应约 {home.elapsed_ms} 毫秒"),
        ],
    }
    platforms = [{"key": key, "label": label, "score": score_checks(checks[key]),
                  "checks": [c.to_dict() for c in checks[key]]} for key, label in PLATFORM_NAMES]
    return {"score": round(sum(p["score"] for p in platforms) / len(platforms), 1), "platforms": platforms,
            "freshness_months": months}
