"""Schema & Structured Data, scored with schema-structured-data.md's 100-point rubric."""

from app.fullaudit.checks import Check, score_checks
from app.fullaudit.page_facts import PageFacts, find_typed, schema_types

ARTICLE_TYPES = ("Article", "BlogPosting", "NewsArticle", "TechArticle", "Report")
BUSINESS_TYPES = ("Product", "LocalBusiness", "SoftwareApplication", "Service", "FAQPage", "Store",
                  "Restaurant", "FinancialService", "BankOrCreditUnion", "Hotel", "Course", "Event", "Offer")
DEPRECATED = ("SpecialAnnouncement", "CourseInfo")
ORG_FIELDS = ("name", "url", "logo", "sameAs", "description", "contactPoint", "address", "foundingDate")


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else [value] if value else []


def analyze_schema(pages: list[PageFacts]) -> dict:
    objects = [obj for p in pages for obj in p.jsonld]
    types = schema_types(objects)
    unique_types = sorted(set(types))
    orgs = find_typed(objects, "Organization", "Corporation", "Person", "LocalBusiness", "OnlineStore", "Brand")
    org = max(orgs, key=lambda o: sum(1 for f in ORG_FIELDS if o.get(f)), default=None)
    org_fields = [f for f in ORG_FIELDS if org and org.get(f)]
    same_as = sorted({s for o in orgs for s in _as_list(o.get("sameAs")) if isinstance(s, str) and s.startswith("http")})
    articles = find_typed(objects, *ARTICLE_TYPES)
    business = find_typed(objects, *BUSINESS_TYPES)
    website = find_typed(objects, "WebSite")
    search_action = any(find_typed(w, "SearchAction") or "potentialAction" in w for w in website)
    inner = pages[1:]
    breadcrumbs = sum(1 for p in inner if "BreadcrumbList" in schema_types(p.jsonld))
    errors = sum(p.jsonld_errors for p in pages)
    microdata = any(p.microdata or p.rdfa for p in pages)
    knows_about = [k for o in orgs for k in _as_list(o.get("knowsAbout"))]
    deprecated = sorted({t for t in types if t in DEPRECATED})

    def author_points() -> float | None:
        if not articles:
            return None
        authors = [a for art in articles for a in _as_list(art.get("author"))]
        if any(isinstance(a, dict) and (a.get("url") or a.get("sameAs") or a.get("jobTitle")) for a in authors):
            return 10
        return 5 if authors else 0

    g = "结构化数据"
    checks = [
        Check(g, "Organization/Person 品牌实体标记", 15, 0 if not org else 15 if len(org_fields) >= 5 else 10,
              ("已标记字段：" + "、".join(org_fields)) if org else "未发现 Organization 结构化数据"),
        Check(g, "sameAs 关联官方账号（5个以上）", 15, min(15, 3 * len(same_as)), f"sameAs 链接 {len(same_as)} 个"),
        Check(g, "文章 Article 含作者信息", 10, author_points(),
              "未发现文章类页面，不适用" if not articles else f"发现 {len(articles)} 个文章标记"),
        Check(g, "业务类型专属标记（产品/服务/FAQ等）", 10,
              0 if not business else 10 if len(business) >= 2 or any(len(b) >= 5 for b in business) else 5,
              ("已发现：" + "、".join(sorted({t for t in types if t in BUSINESS_TYPES}))) if business else "未发现"),
        Check(g, "WebSite + SearchAction", 5, 5 if website and search_action else 2 if website else 0,
              "已标记站内搜索" if search_action else "已标记 WebSite，缺 SearchAction" if website else "未发现"),
        Check(g, "内页 BreadcrumbList 面包屑", 5, None if not inner else round(5 * breadcrumbs / len(inner), 1),
              f"{breadcrumbs}/{len(inner)} 个内页含面包屑标记"),
        Check(g, "使用 JSON-LD 格式", 5, 0 if not objects else 3 if microdata else 5,
              "JSON-LD" + ("（同时含 Microdata/RDFa）" if microdata and objects else "") if objects
              else ("仅 Microdata/RDFa" if microdata else "未发现结构化数据")),
        Check(g, "结构化数据位于服务端HTML中", 10, 10 if objects else 0,
              "原始HTML中可见" if objects else "原始HTML中未发现（可能缺失或由JS注入，AI爬虫难以读取）"),
        Check(g, "文章 speakable 属性", 5, None if not articles else 5 if any("speakable" in a for a in articles) else 0,
              "不适用" if not articles else ""),
        Check(g, "JSON 语法与类型有效", 10, None if not objects and not errors else 10 if not errors else 5 if errors <= 1 else 0,
              f"{errors} 处 JSON-LD 解析错误" if errors else "无解析错误"),
        Check(g, "knowsAbout 专业领域（3项以上）", 5, 5 if len(knows_about) >= 3 else 0, f"knowsAbout {len(knows_about)} 项"),
        Check(g, "无已弃用的标记类型", 5, 0 if deprecated else 5, ("发现：" + "、".join(deprecated)) if deprecated else "无"),
    ]
    faq_content = sum(p.question_headings for p in pages) >= 3
    return {
        "score": score_checks(checks),
        "checks": [c.to_dict() for c in checks],
        "types": unique_types,
        "same_as": same_as,
        "has_org": org is not None,
        "has_any": bool(objects or microdata),
        "faq_without_schema": faq_content and "FAQPage" not in types,
        "has_articles": bool(articles),
        "articles_without_author": bool(articles) and author_points() == 0,
    }
