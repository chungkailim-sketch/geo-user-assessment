"""Classify audit issues by severity (audit.md 'Issue Severity Classification') and the template action plan
used when Claude isn't available."""

from app.fullaudit.page_facts import PageFacts

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_LABELS = {"critical": "严重（立即修复）", "high": "高（1周内修复）", "medium": "中（1个月内修复）", "low": "低（择机优化）"}


def _finding(severity: str, title: str, description: str, fix: str) -> dict:
    return {"severity": severity, "severity_label": SEVERITY_LABELS[severity], "title": title,
            "description": description, "fix": fix}


def _critical(access: dict, technical: dict, schema: dict, quick: dict, pages: list[PageFacts]) -> list[dict]:
    found = []
    tier1 = [c for c in access["crawlers"] if c["tier"] == 1]
    if tier1 and not any(c["allowed"] for c in tier1):
        found.append(_finding("critical", "所有主流AI爬虫均被屏蔽",
                              "robots.txt 或页面 meta 屏蔽了 GPTBot、ClaudeBot、PerplexityBot 等全部一级AI爬虫，品牌内容无法进入AI搜索答案。",
                              "在 robots.txt 中为一级AI爬虫添加 Allow 规则，并移除 noai/noindex 元标签。"))
    if technical["ssr_points"] == 0:
        found.append(_finding("critical", "关键内容无法被AI读取",
                              "首页原始HTML几乎没有正文，内容依赖浏览器端JavaScript渲染，AI爬虫通常不执行JS。",
                              "对首页与关键页面启用服务端渲染（SSR）或静态预渲染。"))
    if "noindex" in pages[0].robots_meta or "noindex" in pages[0].headers.get("x-robots-tag", ""):
        found.append(_finding("critical", "首页设置了 noindex", "首页禁止被索引，搜索引擎与AI均不会收录。",
                              "移除首页的 noindex 指令。"))
    if not schema["has_any"]:
        found.append(_finding("critical", "完全没有结构化数据", "网站未使用任何 Schema.org 结构化数据，AI难以准确识别品牌实体。",
                              "首先在首页部署 Organization JSON-LD（含 name、url、logo、sameAs）。"))
    if not quick["authority"].get("wikidata_id") and quick["authority"]["score"] < 25:
        found.append(_finding("critical", "品牌尚未被识别为实体", "未找到品牌的 Wikidata 条目，且第三方平台存在感很弱，AI可能无法确认“品牌是谁”。",
                              "建立 Wikidata 条目与权威第三方资料，并在官网 sameAs 中关联。"))
    return found


def _high(access: dict, llms: dict, quick: dict, schema: dict) -> list[dict]:
    found = []
    blocked = access["key_blocked"]
    tier1_open = any(c["allowed"] for c in access["crawlers"] if c["tier"] == 1)
    if blocked and tier1_open:
        found.append(_finding("high", "关键AI爬虫被屏蔽", f"以下关键AI爬虫被屏蔽：{'、'.join(blocked)}。",
                              "在 robots.txt 中放开这些爬虫，确保品牌内容可被 ChatGPT、Claude、Perplexity 检索引用。"))
    if not llms["found"]:
        found.append(_finding("high", "缺少 llms.txt", "网站根目录没有 llms.txt，AI系统无法快速了解网站结构与核心页面。",
                              "按 llms.txt 规范创建文件：H1站点名、简介、核心页面清单、关键事实与联系方式。"))
    if quick["citability"]["subscores"].get("answer_block_quality", 0) < 30:
        found.append(_finding("high", "缺少直接回答问题的内容块", "关键页面几乎没有“问题—直接答案”式段落，AI难以直接摘录。",
                              "围绕目标受众的高频问题，增加问句式小标题，并在首句直接给出答案。"))
    if not schema["has_org"]:
        found.append(_finding("high", "缺少 Organization 结构化数据", "未发现描述品牌本身的 Organization 标记。",
                              "在首页添加完整的 Organization JSON-LD，并列出所有官方账号的 sameAs。"))
    if schema["articles_without_author"]:
        found.append(_finding("high", "文章缺少作者信息", "文章页结构化数据中没有作者，削弱专业性信号。",
                              "为文章添加 author（Person，含姓名、职位、个人主页）。"))
    return found


def _medium(access: dict, llms: dict, quick: dict, schema: dict) -> list[dict]:
    found = []
    if access["tier2_blocked"]:
        found.append(_finding("medium", "部分AI爬虫被屏蔽", f"以下二级AI爬虫被屏蔽：{'、'.join(access['tier2_blocked'])}。",
                              "评估数据授权策略后，建议放开 Google-Extended、Applebot-Extended 等爬虫。"))
    if llms["found"] and llms["score"] < 70:
        found.append(_finding("medium", "llms.txt 不完整", "；".join(llms["issues"][:3]) or "llms.txt 结构不完整。",
                              "补齐 llms.txt 的简介、页面描述、关键事实与联系方式分区。"))
    if quick["citability"]["score"] < 50:
        found.append(_finding("medium", "内容可引用性偏低", f"内容块平均可引用性为 {quick['citability']['score']} 分，低于50分。",
                              "将核心页面改写为134–167词、可独立成段、含具体数据的答案式段落。"))
    if schema["faq_without_schema"]:
        found.append(_finding("medium", "FAQ内容未使用 FAQPage 标记", "页面含多个问答式小标题，但未使用 FAQPage 结构化数据。",
                              "为问答内容添加 FAQPage JSON-LD。"))
    platforms = {p["key"]: p["score"] for p in quick["authority"]["platforms"]}
    if platforms.get("wikipedia", 0) < 25 or platforms.get("reddit", 0) < 25:
        missing = [n for k, n in (("wikipedia", "维基百科"), ("reddit", "Reddit")) if platforms.get(k, 0) < 25]
        found.append(_finding("medium", f"{'与'.join(missing)}存在感不足", "AI高度依赖这些平台确认品牌信息与口碑。",
                              "按平台规则建立或完善词条与社区参与，避免广告式内容。"))
    return found


def _low(pages: list[PageFacts], quick: dict) -> list[dict]:
    found = []
    images = sum(p.images for p in pages)
    missing = sum(p.images_missing_alt for p in pages)
    if images and missing / images > 0.2:
        found.append(_finding("low", "部分图片缺少 alt 文本", f"{missing}/{images} 张图片没有 alt 描述。", "为产品与内容图片补充描述性 alt 文本。"))
    no_og = [p.url for p in pages if p.og_tags < 3]
    if no_og:
        found.append(_finding("low", "Open Graph 标签缺失", f"{len(no_og)} 个页面缺少完整的 Open Graph 标签。", "补齐 og:title、og:description、og:image。"))
    no_desc = [p.url for p in pages if not p.meta_description]
    if no_desc:
        found.append(_finding("low", "Meta 描述缺失", f"{len(no_desc)} 个页面没有 meta description。", "为每个关键页面撰写150字以内的准确描述。"))
    bad_h1 = [p.url for p in pages if p.h1_count != 1]
    if bad_h1:
        found.append(_finding("low", "标题层级不规范", f"{len(bad_h1)} 个页面没有或有多个 H1。", "每页保留一个 H1，并按 H2、H3 顺序组织小标题。"))
    linkedin = next((p["score"] for p in quick["authority"]["platforms"] if p["key"] == "linkedin"), 100)
    if linkedin < 50:
        found.append(_finding("low", "领英公司主页待完善", "领英主页存在感较弱，影响 Bing Copilot 等平台的品牌识别。", "完善公司主页资料并保持定期发布。"))
    return found


def build_findings(quick: dict, access: dict, llms: dict, technical: dict, schema: dict, signals: dict,
                   pages: list[PageFacts]) -> list[dict]:
    findings = (_critical(access, technical, schema, quick, pages) + _high(access, llms, quick, schema)
                + _medium(access, llms, quick, schema) + _low(pages, quick))
    if not signals["author"]:
        findings.append(_finding("medium", "缺少作者署名", "内容页没有可识别的作者署名与资历。", "为内容页添加作者署名、简介与作者页面。"))
    return sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])


def quick_wins_fallback(findings: list[dict], quick: dict) -> list[str]:
    wins = [f["fix"] for f in findings if f["severity"] in ("critical", "high")]
    wins += [a["action"] for a in quick.get("actions", []) if a.get("kind") != "strategy"]
    return list(dict.fromkeys(wins))[:5]


def plan_fallback(findings: list[dict]) -> list[dict]:
    by = {s: [f["fix"] for f in findings if f["severity"] == s] for s in SEVERITY_ORDER}
    return [
        {"theme": "扫清AI抓取与识别障碍", "items": (by["critical"] + by["high"])[:3] or ["复核 robots.txt 与 llms.txt 配置"]},
        {"theme": "完善结构化数据与品牌实体", "items": ["部署完整的 Organization 与 WebSite 结构化数据", "在 sameAs 中关联所有官方平台账号"]},
        {"theme": "改写核心页面为答案式内容", "items": ["为高频问题撰写问句式小标题与直接答案", "在正文中补充具体数据与出处"]},
        {"theme": "拓展站外权威信源", "items": (by["medium"] + by["low"])[:2] or ["在AI高频引用的媒体与社区持续发布品牌内容"]},
    ]
