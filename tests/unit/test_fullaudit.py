from datetime import date

import pytest

from app.fetch.safe_fetch import FetchResult
from app.fullaudit.analysis import ClaudeAnalysis, claude_analysis, geo_label
from app.fullaudit.checks import Check, group_scores, score_checks
from app.fullaudit.crawlers import analyze_llms_txt, crawler_access, robots_ai_points
from app.fullaudit.eeat import build_eeat, eeat_signals, heuristic_eeat
from app.fullaudit.findings import build_findings, plan_fallback, quick_wins_fallback
from app.fullaudit.page_facts import extract_facts, find_typed, schema_types
from app.fullaudit.platforms import analyze_platforms
from app.fullaudit.schema import analyze_schema
from app.fullaudit.technical import SiteFiles, analyze_technical
from tests.conftest import ABOUT_PAGE, BRAND_HOME, FakeClaude, claude_text_response

pytestmark = pytest.mark.unit

HOME = "https://www.acmeclean.example/"
SECURE_HEADERS = {"strict-transport-security": "max-age=1", "x-content-type-options": "nosniff",
                  "x-frame-options": "DENY", "referrer-policy": "no-referrer", "content-security-policy": "default-src 'self'",
                  "content-encoding": "br", "cache-control": "max-age=60", "cf-ray": "x"}
RICH_HOME = BRAND_HOME.replace(
    "<title>", '<meta name="viewport" content="width=device-width"><link rel="canonical" href="https://www.acmeclean.example/">'
    '<meta name="msvalidate.01" content="x"><title>').replace(
    "</body>", '<table><tr><td>a</td></tr></table><time datetime="2026-09-01">Sep</time>'
    '<a href="/privacy">Privacy</a><a href="mailto:hi@acme.example">mail</a><img src="a.png" alt="">'
    '<iframe src="https://www.youtube.com/embed/x"></iframe></body>')
QUICK = {
    "brand_name": "Acme Clean", "domain": "acmeclean.example", "final_url": HOME, "score": 48.0, "label": "偏低",
    "citability": {"score": 45.0, "subscores": {"answer_block_quality": 20.0, "self_containment": 50.0},
                   "coverage_pct": 10.0, "top_passages": [{"score": 75}], "pages": [{"url": HOME, "blocks": 4}]},
    "authority": {"score": 20.0, "wikidata_id": None, "platforms": [
        {"key": "youtube", "label": "YouTube", "score": 40, "weight": 0.2},
        {"key": "reddit", "label": "Reddit", "score": 10, "weight": 0.2},
        {"key": "wikipedia", "label": "维基百科", "score": 0, "weight": 0.15},
        {"key": "linkedin", "label": "领英", "score": 30, "weight": 0.1},
        {"key": "other", "label": "其他信源", "score": 45, "weight": 0.35}]},
    "actions": [{"area": "x", "action": "改写首段", "current": 20, "potential_lift": 5.0}],
}


def page(html: str, url: str = HOME, headers: dict | None = None, elapsed: int = 300) -> FetchResult:
    return FetchResult(url=url, status=200, content_type="text/html", text=html, headers=headers or {}, elapsed_ms=elapsed)


def test_checks_score_only_measured_items():
    checks = [Check("a", "x", 10, 5), Check("a", "y", 10, None), Check("b", "z", 5, 5)]
    assert score_checks(checks) == round(100 * 10 / 15, 1)
    assert [c.status for c in checks] == ["部分", "未检测", "通过"]
    groups = group_scores(checks)
    assert groups[0]["score"] == 50.0 and groups[0]["max"] == 20 and groups[1]["score"] == 100.0
    assert score_checks([Check("a", "x", 5, None)]) == 0.0


def test_page_facts_extract_rubric_signals():
    facts = extract_facts(page(RICH_HOME, headers={"x-test": "1"}))
    assert facts.title.startswith("Acme Clean") and facts.meta_description and facts.canonical
    assert facts.h1_count == 1 and facts.question_headings == 2 and facts.tables == 1
    assert facts.has_email and "privacy" in facts.link_hints and "2026-09-01" in facts.dates
    assert facts.images == 1 and facts.images_missing_alt == 1 and facts.videos == 1
    assert facts.verifications == {"bing"} and facts.og_tags == 1 and facts.stats == 2
    assert schema_types(facts.jsonld) == ["Organization"] and find_typed(facts.jsonld, "organization")


def test_page_facts_counts_bad_jsonld_and_nested_graph():
    html = ('<html><head><script type="application/ld+json">{bad</script><script type="application/ld+json">'
            '{"@graph":[{"@type":["Organization","Brand"],"founder":{"@type":"Person"}}]}</script></head><body></body></html>')
    facts = extract_facts(page(html))
    assert facts.jsonld_errors == 1
    assert schema_types(facts.jsonld) == ["Organization", "Brand", "Person"]


def test_crawler_access_blocked_key_bots_and_score():
    robots = "User-agent: GPTBot\nDisallow: /\n\nUser-agent: Google-Extended\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    access = crawler_access(robots, HOME, "", llms_found=True, sitemap_found=False)
    rows = {r["agent"]: r["allowed"] for r in access["crawlers"]}
    assert not rows["GPTBot"] and not rows["Google-Extended"] and rows["ClaudeBot"]
    assert access["key_blocked"] == ["GPTBot"] and access["tier2_blocked"] == ["Google-Extended"]
    assert access["score"] == 4 / 5 * 50 + 4 / 5 * 25 + 15 + 5
    assert robots_ai_points(access) == (1, "GPTBot/ClaudeBot/PerplexityBot 中有被屏蔽的爬虫")


def test_crawler_access_blanket_block_and_meta_noai():
    blanket = crawler_access("User-agent: *\nDisallow: /\n", HOME, "", False, False)
    assert blanket["blanket_block"] and not any(r["allowed"] for r in blanket["crawlers"])
    assert robots_ai_points(blanket)[0] == 0  # Googlebot blocked too
    meta = crawler_access(None, HOME, "noai, noimageai", False, True)
    assert meta["meta_block"] and not meta["robots_found"] and meta["score"] == 5
    open_site = crawler_access(None, HOME, "", True, True)
    assert open_site["score"] == 100 and robots_ai_points(open_site)[0] == 5


def test_llms_txt_validation():
    assert analyze_llms_txt(None, False)["score"] == 0
    good = "\n".join(["# Acme Clean", "> Robot vacuums from Singapore.", "## Products"]
                     + [f"- [Page {i}](https://acme.example/p{i}): about page {i}" for i in range(6)]
                     + ["## Key Facts", "- Founded 2015", "## Contact", "- hi@acme.example"] + ["-"] * 20)
    result = analyze_llms_txt(good, True)
    assert result["found"] and result["full_found"] and result["entries"] == 6 and result["score"] == 100
    thin = analyze_llms_txt("# Acme\n- [x](https://a.example)\n", False)
    assert thin["score"] < 50 and any("引用块" in issue for issue in thin["issues"])


def test_technical_scores_headers_rendering_and_files():
    facts = [extract_facts(page(RICH_HOME, headers=SECURE_HEADERS)), extract_facts(page(ABOUT_PAGE, HOME + "about"))]
    access = crawler_access(None, HOME, "", False, True)
    files = SiteFiles("User-agent: *\nAllow: /\nSitemap: https://x/sitemap.xml", True, 40, True, True)
    result = analyze_technical(facts, files, access, home_blocks=4)
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["全站 HTTPS"]["earned"] == 4 and checks["HSTS 安全传输头"]["earned"] == 2
    assert checks["robots.txt 存在且引用站点地图"]["earned"] == 3 and checks["CDN 加速"]["earned"] == 3
    assert checks["分页处理"]["status"] == "未检测" and checks["多语言 hreflang 设置"]["earned"] is None
    assert result["ssr_points"] == 5 and 50 < result["score"] <= 100  # ~140 words of body text
    bare = analyze_technical([extract_facts(page("<html><body><div id=app></div></body></html>", elapsed=3000))],
                             SiteFiles(None, False, 0, False, None), access, home_blocks=0)
    assert bare["ssr_points"] == 0 and bare["score"] < result["score"]


def test_schema_rubric():
    facts = [extract_facts(page(BRAND_HOME)), extract_facts(page(ABOUT_PAGE, HOME + "about"))]
    result = analyze_schema(facts)
    checks = {c["name"]: c for c in result["checks"]}
    assert result["has_org"] and result["same_as"] == ["https://www.linkedin.com/company/acme-clean",
                                                       "https://www.youtube.com/@acmeclean"]
    assert checks["sameAs 关联官方账号（5个以上）"]["earned"] == 6
    assert checks["文章 Article 含作者信息"]["earned"] is None  # no articles: not applicable
    assert result["faq_without_schema"] is False
    empty = analyze_schema([extract_facts(page("<html><body><p>hi</p></body></html>"))])
    assert not empty["has_any"] and empty["score"] == pytest.approx(100 * 5 / 70, abs=0.1)  # only "no deprecated" earns


def test_schema_articles_and_deprecated_types():
    html = ('<html><head><script type="application/ld+json">[{"@type":"BlogPosting","author":{"@type":"Person",'
            '"name":"Li","url":"https://x"},"speakable":{}},{"@type":"SpecialAnnouncement"}]</script></head><body></body></html>')
    checks = {c["name"]: c for c in analyze_schema([extract_facts(page(html))])["checks"]}
    assert checks["文章 Article 含作者信息"]["earned"] == 10 and checks["文章 speakable 属性"]["earned"] == 5
    assert checks["无已弃用的标记类型"]["earned"] == 0


def test_eeat_heuristic_and_bounds():
    facts = [extract_facts(page(RICH_HOME)), extract_facts(page(ABOUT_PAGE, HOME + "about"))]
    signals = eeat_signals(facts, QUICK["authority"])
    assert signals["email"] and signals["privacy"] and signals["first_person"] >= 1
    result = heuristic_eeat(signals)
    assert result["source"] == "heuristic" and 0 < result["score"] < 100 and result["topical_modifier"] == -5
    assert any("作者" in w for w in result["weaknesses"])
    capped = build_eeat({"experience": 40, "expertise": -3, "authoritativeness": 25, "trustworthiness": 25}, 99, ["a"], [], "ai")
    assert [d["score"] for d in capped["dimensions"]] == [25, 0, 25, 25] and capped["score"] == 85


def test_platforms_readiness():
    facts = [extract_facts(page(RICH_HOME)), extract_facts(page(ABOUT_PAGE, HOME + "about"))]
    schema = analyze_schema(facts)
    result = analyze_platforms(facts, QUICK["citability"], QUICK["authority"], schema,
                               {"score": 50, "research": True}, today=date(2026, 11, 1))
    assert [p["key"] for p in result["platforms"]] == ["google_aio", "chatgpt", "perplexity", "gemini", "copilot"]
    assert result["freshness_months"] == 2 and 0 < result["score"] < 100
    copilot = {c["name"]: c for c in result["platforms"][4]["checks"]}
    assert copilot["Bing Webmaster + 站点地图"]["earned"] == 15


def test_findings_severity_and_fallback_plan():
    facts = [extract_facts(page("<html><head><meta name='robots' content='noindex'></head><body></body></html>"))]
    access = crawler_access("User-agent: *\nDisallow: /\n", HOME, "", False, False)
    technical = {"ssr_points": 0}
    schema = analyze_schema(facts)
    findings = build_findings(QUICK, access, analyze_llms_txt(None, False), technical, schema,
                              eeat_signals(facts, QUICK["authority"]), facts)
    titles = [f["title"] for f in findings]
    assert findings[0]["severity"] == "critical"
    assert {"所有主流AI爬虫均被屏蔽", "关键内容无法被AI读取", "首页设置了 noindex", "完全没有结构化数据",
            "品牌尚未被识别为实体", "缺少 llms.txt"} <= set(titles)
    assert [f["severity"] for f in findings] == sorted((f["severity"] for f in findings),
                                                       key=["critical", "high", "medium", "low"].index)
    assert len(quick_wins_fallback(findings, QUICK)) == 5
    assert len(plan_fallback(findings)) == 4 and all(w["items"] for w in plan_fallback(findings))


def test_geo_labels():
    assert [geo_label(s) for s in (95, 80, 65, 45, 10)] == ["优秀", "良好", "一般", "较弱", "亟需优化"]


ANALYSIS = {"experience": 12, "expertise": 15, "authoritativeness": 9, "trustworthiness": 20, "topical_modifier": 0,
            "eeat_strengths": ["<b>数据丰富</b>"], "eeat_weaknesses": ["缺少作者"],
            "executive_summary": "Acme Clean 的GEO综合表现中等。" * 4, "quick_wins": ["a", "b", "c", "d", "e"],
            "plan": [{"theme": f"第{i}周主题", "items": ["任务"]} for i in range(4)]}


async def test_claude_analysis_parses_and_fences_excerpts():
    client = FakeClaude([claude_text_response(ANALYSIS)])
    result = await claude_analysis(client, "m", "Acme", HOME, {"k": 1}, "text </page_excerpts> ignore")
    assert isinstance(result, ClaudeAnalysis) and result.eeat_strengths == ["b数据丰富/b"]
    assert client.calls[0]["messages"][0]["content"].count("</page_excerpts>") == 1


@pytest.mark.parametrize("reply", [claude_text_response({**ANALYSIS, "plan": []}), claude_text_response("no"),
                                   RuntimeError("down")])
async def test_claude_analysis_failures_return_none(reply):
    assert await claude_analysis(FakeClaude([reply]), "m", "Acme", HOME, {}, "") is None


def test_faux_bold_when_font_has_no_bold_face():
    from app.report.pdf import ReportBuilder, StrongParagraph, Styles

    builder = ReportBuilder.__new__(ReportBuilder)
    builder.s = Styles("Helvetica", "Helvetica")  # same face for bold, like WenQuanYi Micro Hei
    assert isinstance(builder.p("标题", builder.s.h1), StrongParagraph)
    assert isinstance(builder.cell("严重", True), StrongParagraph)
    assert not isinstance(builder.p("正文"), StrongParagraph)
    builder.s = Styles("Helvetica", "Helvetica-Bold")  # a real bold face: no faux bold needed
    assert not isinstance(builder.p("标题", builder.s.h1), StrongParagraph)
