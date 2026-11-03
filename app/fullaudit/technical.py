"""Technical GEO (technical-seo.md's 8 categories, 100 points), measured from the raw HTML and headers.

Lab-only proxies stand in for field Core Web Vitals; anything that needs site-owner access is marked as
not measured (and excluded from the score) rather than failed.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

from app.fullaudit.checks import Check, group_scores, score_checks
from app.fullaudit.crawlers import robots_ai_points
from app.fullaudit.page_facts import PageFacts

CDN_HEADERS = ("cf-ray", "x-amz-cf-id", "x-akamai-transformed", "x-served-by", "x-cache", "x-fastly-request-id",
               "x-vercel-id", "x-azure-ref", "fastly-debug-digest", "x-cdn")


@dataclass(frozen=True)
class SiteFiles:
    robots_text: str | None
    sitemap_found: bool
    sitemap_urls: int
    sitemap_lastmod: bool
    https_redirect: bool | None  # http:// → https:// (None: not checked / failed)


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 1.0


def _crawlability(home: PageFacts, pages: list[PageFacts], files: SiteFiles, access: dict) -> list[Check]:
    g = "可抓取性"
    robots_ok = files.robots_text is not None
    sitemap_ref = robots_ok and "sitemap:" in files.robots_text.lower()
    ai_points, ai_note = robots_ai_points(access)
    noindexed = [p.url for p in pages if "noindex" in p.robots_meta or "noindex" in p.headers.get("x-robots-tag", "")]
    return [
        Check(g, "robots.txt 存在且引用站点地图", 3, (2 if robots_ok else 0) + (1 if sitemap_ref else 0),
              "已发现 robots.txt" + ("，并声明了 Sitemap" if sitemap_ref else "，但未声明 Sitemap") if robots_ok
              else "未发现 robots.txt"),
        Check(g, "AI爬虫访问权限", 5, ai_points, ai_note),
        Check(g, "XML站点地图", 3, (2 if files.sitemap_found else 0) + (1 if files.sitemap_lastmod else 0),
              f"站点地图含 {files.sitemap_urls} 个网址" + ("，含 lastmod" if files.sitemap_lastmod else "")
              if files.sitemap_found else "未发现可读取的站点地图"),
        Check(g, "关键页面可在3次点击内到达", 2, 2 if home.internal_links >= 10 else 1 if home.internal_links else 0,
              f"首页有 {home.internal_links} 个站内链接"),
        Check(g, "无误用的 noindex 指令", 2, 0 if noindexed else 2,
              ("以下页面设置了 noindex：" + "、".join(noindexed[:3])) if noindexed else "未发现 noindex"),
    ]


def _indexability(pages: list[PageFacts], files: SiteFiles) -> list[Check]:
    g = "可索引性"
    with_canonical = sum(1 for p in pages if p.canonical)
    hreflang_pages = [p for p in pages if p.hreflangs]
    return [
        Check(g, "页面设置 canonical 规范链接", 3, round(3 * _ratio(with_canonical, len(pages)), 1),
              f"{with_canonical}/{len(pages)} 个页面设置了 canonical"),
        Check(g, "HTTP 跳转至 HTTPS（避免重复内容）", 3,
              None if files.https_redirect is None else 3 if files.https_redirect else 0,
              "未能检测" if files.https_redirect is None else
              ("http:// 已跳转至 https://" if files.https_redirect else "http:// 未跳转至 https://")),
        Check(g, "分页处理", 2, None, "需结合站点结构人工检查"),
        Check(g, "多语言 hreflang 设置", 2,
              None if not hreflang_pages else 2 if any("x-default" in p.hreflangs for p in hreflang_pages) else 1,
              "单语言站点，不适用" if not hreflang_pages else
              ("hreflang 含 x-default" if any("x-default" in p.hreflangs for p in hreflang_pages) else "hreflang 缺少 x-default")),
        Check(g, "无索引膨胀", 2, None, "需结合搜索引擎索引数据判断"),
    ]


def _security(home: PageFacts) -> list[Check]:
    g = "安全性"
    h = home.headers
    https = urlparse(home.url).scheme == "https"
    csp = h.get("content-security-policy", "")
    return [
        Check(g, "全站 HTTPS", 4, 4 if https else 0, "首页通过 HTTPS 访问" if https else "首页未使用 HTTPS"),
        Check(g, "HSTS 安全传输头", 2, 2 if "strict-transport-security" in h else 0, ""),
        Check(g, "X-Content-Type-Options", 1, 1 if h.get("x-content-type-options", "").lower() == "nosniff" else 0, ""),
        Check(g, "X-Frame-Options / frame-ancestors", 1, 1 if "x-frame-options" in h or "frame-ancestors" in csp else 0, ""),
        Check(g, "Referrer-Policy", 1, 1 if "referrer-policy" in h else 0, ""),
        Check(g, "Content-Security-Policy", 1, 1 if csp else 0, ""),
    ]


def _urls(home: PageFacts, pages: list[PageFacts]) -> list[Check]:
    g = "网址结构"
    clean = sum(1 for p in pages if not urlparse(p.url).query and len(urlparse(p.url).path) <= 115
                and urlparse(p.url).path == urlparse(p.url).path.lower())
    return [
        Check(g, "简洁、可读的网址", 4, round(4 * _ratio(clean, len(pages)), 1), f"{clean}/{len(pages)} 个网址简洁可读"),
        Check(g, "首页无多级重定向", 4, 4 if home.redirects <= 1 else 2 if home.redirects == 2 else 0,
              f"首页经过 {home.redirects} 次重定向"),
    ]


def _mobile(pages: list[PageFacts]) -> list[Check]:
    g = "移动端适配"
    responsive = sum(1 for p in pages if "width=device-width" in p.viewport)
    zoom_locked = any("user-scalable=no" in p.viewport or "maximum-scale=1" in p.viewport for p in pages)
    return [
        Check(g, "响应式 viewport 设置", 7, round(7 * _ratio(responsive, len(pages)), 1),
              f"{responsive}/{len(pages)} 个页面设置了响应式 viewport"),
        Check(g, "允许用户缩放", 3, 0 if zoom_locked else 3, "页面禁止缩放" if zoom_locked else "允许缩放"),
    ]


def _vitals(pages: list[PageFacts]) -> list[Check]:
    g = "核心网页指标（实验室估算）"
    images = sum(p.images for p in pages)
    sized = sum(p.images_sized for p in pages)
    heaviest = max(p.html_bytes for p in pages)
    blocking = max(p.blocking_scripts for p in pages)
    return [
        Check(g, "HTML 体积（影响LCP）", 5, 5 if heaviest < 300_000 else 3 if heaviest < 800_000 else 0,
              f"最大页面 HTML 约 {heaviest // 1024} KB"),
        Check(g, "阻塞渲染的脚本（影响INP/LCP）", 5, 5 if blocking <= 2 else 3 if blocking <= 6 else 0,
              f"<head> 中最多 {blocking} 个同步脚本"),
        Check(g, "图片声明宽高（影响CLS）", 5, round(5 * _ratio(sized, images), 1), f"{sized}/{images} 张图片声明了宽高"),
    ]


def _ssr(home: PageFacts, home_blocks: int) -> list[Check]:
    words = home.word_count
    if home_blocks >= 3 and words >= 300:
        points, note = 15, f"首页原始HTML含约 {words} 词正文，内容为服务端渲染"
    elif words >= 150:
        points, note = 10, f"首页原始HTML含约 {words} 词正文，部分内容可能依赖JS"
    elif words > 30:
        points, note = 5, f"首页原始HTML仅约 {words} 词正文，关键内容可能依赖JS渲染"
    else:
        points, note = 0, "首页原始HTML几乎没有正文，AI爬虫无法读取（需服务端渲染）"
    return [Check("服务端渲染（GEO关键）", "关键内容在原始HTML中可见", 15, points, note)]


def _speed(home: PageFacts, pages: list[PageFacts]) -> list[Check]:
    g = "页面速度与服务器"
    ttfb = home.elapsed_ms
    h = home.headers
    cdn = any(name in h for name in CDN_HEADERS) or any(s in h.get("server", "").lower() for s in ("cloudflare", "akamai", "cloudfront"))
    images = sum(p.images for p in pages)
    lazy = sum(p.images_lazy for p in pages)
    return [
        Check(g, "服务器响应时间（TTFB）", 5, 5 if ttfb < 800 else 3 if ttfb < 1800 else 0, f"首页响应约 {ttfb} 毫秒"),
        Check(g, "压缩传输（gzip/br）", 3, 3 if h.get("content-encoding") else 0, h.get("content-encoding") or "未启用"),
        Check(g, "缓存策略（Cache-Control）", 2, 2 if "cache-control" in h else 0, h.get("cache-control", "未设置")[:60]),
        Check(g, "图片懒加载", 2, round(2 * _ratio(lazy, images), 1) if images > 3 else 2, f"{lazy}/{images} 张图片懒加载"),
        Check(g, "CDN 加速", 3, 3 if cdn else 0, "检测到 CDN" if cdn else "未检测到 CDN 响应头"),
    ]


def analyze_technical(pages: list[PageFacts], files: SiteFiles, access: dict, home_blocks: int) -> dict:
    home = pages[0]
    ssr = _ssr(home, home_blocks)
    checks = (_crawlability(home, pages, files, access) + _indexability(pages, files) + _security(home)
              + _urls(home, pages) + _mobile(pages) + _vitals(pages) + ssr + _speed(home, pages))
    return {
        "score": score_checks(checks),
        "groups": group_scores(checks),
        "checks": [c.to_dict() for c in checks],
        "ssr_points": ssr[0].earned,
    }
