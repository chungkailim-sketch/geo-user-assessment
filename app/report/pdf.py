"""Render the full GEO audit as a client-ready PDF (Simplified Chinese, A4), following the section order of
the GEO skill's PDF report (geo.zip `references/modules/pdf-report.md`): cover with score gauge, executive
summary, score breakdown, key findings by severity, category deep dives, AI crawler access, platform
readiness, benchmark and media strategy, prioritised action plan, appendix, and a closing call to action.

Every string that comes from a crawled site or a model is XML-escaped before it reaches a Paragraph.
"""

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import ROOT_DIR
from app.report.charts import GREEN, GREEN_DEEP, MUTED, gauge, score_bars, score_color
from app.report.fonts import register_fonts

OFFERING_URL = "https://www.ruderfinnasia.com/zh/products/ai-visibility-geo-advisory"
LOGO = ROOT_DIR / "static" / "img" / "rf-geo-logo.png"
TITLE = "罗德出海GEO全面诊断报告"
LIGHT = colors.HexColor("#f4f8f2")
BORDER = colors.HexColor("#e3e3e3")
SEVERITY_COLORS = {"critical": colors.HexColor("#d03b3b"), "high": colors.HexColor("#ec835a"),
                   "medium": colors.HexColor("#e8a200"), "low": colors.HexColor("#727272")}
STATUS_COLORS = {"通过": GREEN_DEEP, "部分": colors.HexColor("#b07800"), "未通过": colors.HexColor("#d03b3b"),
                 "未检测": MUTED}
CONTENT_WIDTH = A4[0] - 36 * mm


def t(value: object) -> str:
    """Escape untrusted text for a ReportLab Paragraph."""
    return escape(str(value if value is not None else ""))


class Styles:
    def __init__(self, font: str, bold: str):
        self.font, self.bold = font, bold
        base = {"fontName": font, "wordWrap": "CJK", "leading": 16, "fontSize": 10, "textColor": colors.HexColor("#1a1a1a")}
        self.body = ParagraphStyle("body", **base)
        self.small = ParagraphStyle("small", **{**base, "fontSize": 8.5, "leading": 12, "textColor": MUTED})
        self.cell = ParagraphStyle("cell", **{**base, "fontSize": 8.5, "leading": 12})
        self.cell_bold = ParagraphStyle("cellb", **{**base, "fontName": bold, "fontSize": 8.5, "leading": 12})
        self.h1 = ParagraphStyle("h1", **{**base, "fontName": bold, "fontSize": 18, "leading": 24,
                                          "textColor": GREEN_DEEP, "spaceBefore": 4, "spaceAfter": 8})
        self.h2 = ParagraphStyle("h2", **{**base, "fontName": bold, "fontSize": 12.5, "leading": 18,
                                          "textColor": colors.HexColor("#1a1a1a"), "spaceBefore": 10, "spaceAfter": 4})
        self.cover_title = ParagraphStyle("ct", **{**base, "fontName": bold, "fontSize": 26, "leading": 34,
                                                   "textColor": GREEN_DEEP, "alignment": TA_CENTER})
        self.center = ParagraphStyle("center", **{**base, "alignment": TA_CENTER, "fontSize": 11, "leading": 18})
        self.cta = ParagraphStyle("cta", **{**base, "fontName": bold, "fontSize": 16, "leading": 22,
                                            "alignment": TA_CENTER, "textColor": colors.white})


def _table(rows: list[list], widths: list[float], header: bool = True, zebra: bool = True) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), LIGHT), ("LINEBELOW", (0, 0), (-1, 0), 0.8, GREEN)]
    if zebra:
        style += [("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#fafafa")])]
    table.setStyle(TableStyle(style))
    return table


class ReportBuilder:
    def __init__(self, report: dict, font_path: str = ""):
        self.r = report
        font, bold = register_fonts(font_path)
        self.s = Styles(font, bold)
        self.story: list = []

    # -- helpers -----------------------------------------------------------------------------------------
    def p(self, text: str, style=None) -> Paragraph:
        return Paragraph(text, style or self.s.body)

    def cell(self, text: object, bold: bool = False, color=None) -> Paragraph:
        markup = t(text)
        if color is not None:
            markup = f'<font color="{color.hexval()}">{markup}</font>'
        return Paragraph(markup, self.s.cell_bold if bold else self.s.cell)

    def section(self, title: str) -> None:
        self.story += [CondPageBreak(60 * mm), self.p(t(title), self.s.h1)]

    def sub(self, title: str) -> None:
        self.story.append(self.p(t(title), self.s.h2))

    def bullets(self, items: list[str]) -> None:
        for item in items:
            self.story.append(self.p("• " + t(item)))

    # -- sections ----------------------------------------------------------------------------------------
    def cover(self) -> None:
        r = self.r
        if LOGO.is_file():
            self.story.append(Image(str(LOGO), width=48 * mm, height=15 * mm, kind="proportional"))
        generated = datetime.fromisoformat(r["generated_at"]).strftime("%Y年%m月%d日")
        self.story += [
            Spacer(1, 28 * mm), self.p(TITLE, self.s.cover_title), Spacer(1, 6 * mm),
            self.p(f"<b>{t(r['brand_name'])}</b>", self.s.center), self.p(t(r["url"]), self.s.center),
            Spacer(1, 12 * mm), gauge(r["geo_score"], f"GEO综合得分 · {r['geo_label']}", self.s.font, self.s.bold, 260),
            Spacer(1, 8 * mm),
            self.p(f"快速诊断「AI搜索就绪度」：{r['readiness_score']}（{t(r['readiness_label'])}）", self.s.center),
            self.p(f"诊断日期：{generated} · 分析页面：{len(r['pages'])} 个", self.s.center),
            Spacer(1, 30 * mm),
            self.p("本报告由罗德出海GEO准备度评估工具自动生成，依据GEO审计方法论对网站进行六大维度诊断。", self.s.small),
            PageBreak(),
        ]

    def summary(self) -> None:
        r = self.r
        self.section("执行摘要")
        self.story.append(self.p(t(r["executive_summary"])))
        self.sub("得分构成")
        rows = [[self.cell("维度", True), self.cell("得分", True), self.cell("权重", True), self.cell("加权得分", True)]]
        for c in r["categories"]:
            rows.append([self.cell(c["label"]), self.cell(f"{c['score']:.0f}/100", color=score_color(c["score"])),
                         self.cell(f"{c['weight'] * 100:.0f}%"), self.cell(f"{c['weighted']:.1f}")])
        rows.append([self.cell("GEO综合得分", True), self.cell(f"{r['geo_score']:.0f}/100", True), self.cell(""),
                     self.cell(f"{r['geo_score']:.1f}", True)])
        self.story += [_table(rows, [CONTENT_WIDTH * f for f in (0.46, 0.18, 0.14, 0.22)]), Spacer(1, 4 * mm),
                       score_bars([(c["label"], c["score"]) for c in r["categories"]], self.s.font, CONTENT_WIDTH)]
        self.story.append(self.p("GEO综合得分 = 可引用性×25% + 品牌权威度×20% + E-E-A-T×20% + 技术GEO×15% + 结构化数据×10% + 平台适配×10%。"
                                 "评级：90+优秀，75–89良好，60–74一般，40–59较弱，40以下亟需优化。", self.s.small))

    def findings(self) -> None:
        self.section("关键问题（按严重程度）")
        findings = self.r["findings"]
        if not findings:
            self.story.append(self.p("未发现需要优先处理的问题。"))
            return
        for f in findings:
            color = SEVERITY_COLORS[f["severity"]]
            box = Table([[self.cell(f["severity_label"], True, color), self.cell(f["title"], True)],
                         ["", self.cell(f["description"])], ["", self.cell("建议：" + f["fix"])]],
                        colWidths=[30 * mm, CONTENT_WIDTH - 30 * mm])
            box.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 3, color), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                     ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            self.story += [KeepTogether(box), Spacer(1, 2.5 * mm)]

    def citability(self) -> None:
        c = self.r["citability"]
        self.section(f"AI内容可引用性（{c['score']:.0f}/100）")
        self.story.append(self.p(f"共分析 {c['pages_analyzed']} 个页面、{c['blocks_analyzed']} 个内容块；高可引用性内容块占比 "
                                 f"{c['coverage_pct']}%，处于最佳篇幅区间（134–167词）的占 {c['optimal_length_pct']}%。"))
        labels = c.get("subscore_labels", {})
        self.story.append(score_bars([(labels.get(k, k), v) for k, v in c["subscores"].items()], self.s.font, CONTENT_WIDTH))
        if c.get("bottom_passages"):
            self.sub("待改写的低分段落")
            rows = [[self.cell("段落", True), self.cell("得分", True), self.cell("改写建议", True)]]
            for b in c["bottom_passages"][:5]:
                rows.append([self.cell(f"{b['heading']}\n{b['preview'][:90]}"), self.cell(f"{b['score']}（{b['grade']}）"),
                             self.cell(b.get("tip", ""))])
            self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.45, 0.13, 0.42)]))

    def authority(self) -> None:
        a = self.r["authority"]
        self.section(f"品牌 AI 引用权威度（{a['score']:.0f}/100）")
        self.story.append(score_bars([(f"{p['label']}（{p['weight'] * 100:.0f}%）", p["score"]) for p in a["platforms"]],
                                     self.s.font, CONTENT_WIDTH))
        rows = [[self.cell("平台", True), self.cell("得分", True), self.cell("证据", True)]]
        for p in a["platforms"]:
            rows.append([self.cell(p["label"]), self.cell(p["score"], color=score_color(p["score"])),
                         self.cell("；".join(p.get("evidence", [])[:3]) or "—")])
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.2, 0.1, 0.7)]))

    def eeat(self) -> None:
        e = self.r["eeat"]
        self.section(f"内容质量 E-E-A-T（{e['score']:.0f}/100）")
        self.story.append(score_bars([(d["label"], d["score"] * 4) for d in e["dimensions"]], self.s.font, CONTENT_WIDTH, 180))
        self.story.append(self.p(f"各维度满分25分（图中换算为百分制）；主题权威度修正 {e['topical_modifier']:+.0f} 分。"
                                 + ("评分由AI结合页面内容判断。" if e["source"] == "ai" else "评分依据页面可见信号估算。"), self.s.small))
        if e["strengths"]:
            self.sub("优势")
            self.bullets(e["strengths"])
        if e["weaknesses"]:
            self.sub("待改进")
            self.bullets(e["weaknesses"])

    def technical(self) -> None:
        tech, access, llms = self.r["technical"], self.r["crawlers"], self.r["llms"]
        self.section(f"技术GEO基础（{tech['score']:.0f}/100）")
        self.story.append(self.p(f"技术GEO = 技术SEO基础（{tech['seo_score']:.0f}）×70% + AI爬虫访问（{access['score']:.0f}）×20% "
                                 f"+ llms.txt（{llms['score']:.0f}）×10%。", self.s.small))
        self.sub("AI爬虫访问权限")
        rows = [[self.cell("爬虫", True), self.cell("对应平台", True), self.cell("状态", True), self.cell("建议", True)]]
        for c in access["crawlers"]:
            status = "允许" if c["allowed"] else "屏蔽"
            rows.append([self.cell(c["agent"]), self.cell(c["platform"]),
                         self.cell(status, True, GREEN_DEEP if c["allowed"] else SEVERITY_COLORS["critical"]),
                         self.cell(c["recommendation"])])
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.22, 0.28, 0.12, 0.38)]))
        if not access["robots_found"]:
            self.story.append(self.p("未发现 robots.txt，默认允许所有爬虫访问。", self.s.small))
        self.sub("llms.txt")
        if llms["found"]:
            self.story.append(self.p(f"已发现 llms.txt（{llms['lines']} 行、{llms['entries']} 个页面条目），结构得分 {llms['score']}/100。"))
            self.bullets(llms["issues"][:5])
        else:
            self.story.append(self.p("未发现 llms.txt。该文件以 Markdown 向AI系统概述网站用途、核心页面与关键事实，建议尽快创建。"))
        self.sub("技术SEO检查明细")
        rows = [[self.cell("类别", True), self.cell("检查项", True), self.cell("得分", True), self.cell("说明", True)]]
        for c in tech["checks"]:
            earned = "—" if c["earned"] is None else f"{c['earned']:g}/{c['points']:g}"
            rows.append([self.cell(c["group"]), self.cell(c["name"]), self.cell(earned, color=STATUS_COLORS[c["status"]]),
                         self.cell(c["detail"] or c["status"])])
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.2, 0.3, 0.12, 0.38)]))

    def schema(self) -> None:
        sc = self.r["schema"]
        self.section(f"结构化数据 Schema（{sc['score']:.0f}/100）")
        types = "、".join(sc["types"][:20]) or "无"
        self.story.append(self.p(f"已发现的 Schema 类型：{t(types)}；sameAs 关联 {len(sc['same_as'])} 个平台。"))
        rows = [[self.cell("评分项", True), self.cell("得分", True), self.cell("说明", True)]]
        for c in sc["checks"]:
            earned = "不适用" if c["earned"] is None else f"{c['earned']:g}/{c['points']:g}"
            rows.append([self.cell(c["name"]), self.cell(earned, color=STATUS_COLORS[c["status"]]), self.cell(c["detail"])])
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.38, 0.14, 0.48)]))

    def platforms(self) -> None:
        pl = self.r["platforms"]
        self.section(f"AI平台适配度（{pl['score']:.0f}/100）")
        self.story.append(score_bars([(p["label"], p["score"]) for p in pl["platforms"]], self.s.font, CONTENT_WIDTH))
        self.story.append(self.p("各平台按其评分标准中可从外部检测的项目计分；需站长后台数据的项目（如 Bing Webmaster、知识面板）未计入。", self.s.small))
        for p in pl["platforms"]:
            weak = [c for c in p["checks"] if c["status"] in ("未通过", "部分")][:3]
            if weak:
                self.story.append(self.p(f"<b>{t(p['label'])}</b>：待提升 — " + t("；".join(c["name"] for c in weak))))

    def benchmark(self) -> None:
        b = self.r.get("benchmark")
        if not b or b.get("status") != "ok" or not b.get("leader_scores"):
            return
        leader = b.get("leader_zh") or b["leader"]
        self.section(f"行业标杆对比：{leader}")
        if b.get("is_leader"):
            self.story.append(self.p(f"{t(self.r['brand_name'])}即为{t(b.get('industry_zh') or b['industry'])}行业的标杆品牌。"))
            return
        ls = b["leader_scores"]
        brand = self.r
        rows = [("AI搜索就绪度", brand["readiness_score"], ls["score"]),
                ("内容可引用性", brand["citability"]["score"], ls["citability"]),
                ("品牌 AI 引用权威度", brand["authority"]["score"], ls["authority"])]
        self.story.append(score_bars([(label, mine) for label, mine, _ in rows], self.s.font, CONTENT_WIDTH,
                                     compare=[theirs for *_, theirs in rows]))
        self.story.append(self.p(f"绿色：{t(brand['brand_name'])}；橙色：{t(leader)}（{t(b['leader_url'])}）。", self.s.small))
        if b.get("fallback_from"):
            original = b["fallback_from"].get("leader_zh") or b["fallback_from"]["leader"]
            self.story.append(self.p(f"原行业标杆{t(original)}的官网限制自动访问，已改为对标行业内的下一位领先品牌。", self.s.small))

    def strategy(self) -> None:
        s = self.r.get("strategy")
        if not s:
            return
        self.section("核心策略建议：公关与媒体")
        self.story.append(self.p(t(s["action"])))
        if s.get("media"):
            self.sub(f"AI高频引用的媒体信源{('（' + t(s['market']) + '）') if s.get('market') else ''}")
            rows = [[self.cell("信源", True), self.cell("类型", True), self.cell("为何重要", True)]]
            rows += [[self.cell(m["name"]), self.cell(m.get("kind", "")), self.cell(m.get("reason", ""))] for m in s["media"]]
            self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.25, 0.17, 0.58)]))
            self.story.append(self.p("基于AI联网检索的行业媒体引用分析生成，未经人工审核，仅供参考。", self.s.small))

    def action_plan(self) -> None:
        r = self.r
        self.section("优先行动计划")
        self.sub("本周可完成的快速优化")
        for index, item in enumerate(r["quick_wins"], 1):
            self.story.append(self.p(f"{index}. {t(item)}"))
        self.sub("30天行动计划")
        rows = [[self.cell("周次", True), self.cell("主题", True), self.cell("行动项", True)]]
        for index, week in enumerate(r["plan"], 1):
            rows.append([self.cell(f"第{index}周"), self.cell(week["theme"], True),
                         self.p("<br/>".join("□ " + t(i) for i in week["items"]), self.s.cell)])
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.12, 0.25, 0.63)]))
        if r.get("actions"):
            self.sub("逐项优化方案（按预计提升排序）")
            rows = [[self.cell("优化方向", True), self.cell("当前", True), self.cell("最多提升", True), self.cell("建议", True)]]
            for a in r["actions"]:
                rows.append([self.cell(a["area"]), self.cell(a["current"]), self.cell(f"+{a['potential_lift']}"), self.cell(a["action"])])
            self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.24, 0.1, 0.12, 0.54)]))

    def appendix(self) -> None:
        r = self.r
        self.section("附录：分析页面与方法说明")
        rows = [[self.cell("网址", True), self.cell("标题", True), self.cell("正文词数", True)]]
        rows += [[self.cell(p["url"]), self.cell(p["title"][:60]), self.cell(p["words"])] for p in r["pages"]]
        self.story.append(_table(rows, [CONTENT_WIDTH * f for f in (0.48, 0.38, 0.14)]))
        self.story.append(Spacer(1, 4 * mm))
        self.story.append(self.p(
            "方法说明：本报告依据GEO（生成式引擎优化）审计方法论，从AI内容可引用性、品牌 AI 引用权威度、内容质量E-E-A-T、"
            "技术GEO基础、结构化数据与AI平台适配度六个维度评估网站。内容与技术指标来自对网站公开页面、robots.txt、"
            "站点地图与 llms.txt 的自动抓取；品牌权威度结合维基百科/Wikidata 数据与AI联网检索。核心网页指标为基于HTML的实验室估算，"
            "需站长后台数据的项目未计入得分。分数反映诊断当日的网站状态。", self.s.small))

    def closing(self) -> None:
        self.story.append(PageBreak())
        self.story.append(Spacer(1, 40 * mm))
        self.story.append(self.p("让AI更准确、更频繁地推荐您的品牌", self.s.cover_title))
        self.story.append(Spacer(1, 8 * mm))
        self.story.append(self.p("罗德出海GEO顾问团队可协助您将本报告中的发现转化为可执行的优化方案，"
                                 "涵盖内容改写、结构化数据部署、AI爬虫与 llms.txt 配置，以及面向AI高频引用媒体的公关传播。", self.s.center))
        self.story.append(Spacer(1, 12 * mm))
        link = f'<a href="{OFFERING_URL}" color="#ffffff"><u>欲了解更多，请点击这里</u></a>'
        button = Table([[Paragraph(link, self.s.cta)]], colWidths=[100 * mm], rowHeights=[16 * mm])
        button.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREEN), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("ROUNDEDCORNERS", [8, 8, 8, 8])]))
        self.story += [button, Spacer(1, 6 * mm),
                       self.p(f'<a href="{OFFERING_URL}" color="#1b7531">{OFFERING_URL}</a>', self.s.center),
                       Spacer(1, 30 * mm),
                       self.p("免责声明：本报告由自动化工具与AI生成，未经人工审核，仅供参考，可能包含错误、不准确或过时的信息，"
                              "不构成任何专业建议。如有疑问，品牌可向罗德业务经理咨询。", self.s.small)]

    # -- page furniture ----------------------------------------------------------------------------------
    def _decorate(self, canvas, doc) -> None:
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(GREEN)
        canvas.setLineWidth(1.2)
        canvas.line(18 * mm, height - 14 * mm, width - 18 * mm, height - 14 * mm)
        canvas.setFont(self.s.font, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, height - 12 * mm, f"{TITLE} · {self.r['brand_name'][:40]}")
        if LOGO.is_file():
            canvas.drawImage(str(LOGO), width - 18 * mm - 22 * mm, height - 12.6 * mm, 22 * mm, 7 * mm,
                             preserveAspectRatio=True, mask="auto", anchor="e")
        canvas.drawString(18 * mm, 10 * mm, "机密 · 仅供收件品牌内部参考")
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    def _first_page(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.setFillColor(GREEN)
        canvas.rect(0, 0, A4[0], 8 * mm, stroke=0, fill=1)
        canvas.restoreState()

    def build(self) -> bytes:
        for part in (self.cover, self.summary, self.findings, self.citability, self.authority, self.eeat, self.technical,
                     self.schema, self.platforms, self.benchmark, self.strategy, self.action_plan, self.appendix, self.closing):
            part()
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                                bottomMargin=18 * mm, title=f"{TITLE} - {self.r['brand_name']}", author="罗德出海 Ruder Finn",
                                subject="GEO Audit Report")
        doc.build(self.story, onFirstPage=self._first_page, onLaterPages=self._decorate)
        return buffer.getvalue()


def render_report_pdf(report: dict, font_path: str = "") -> bytes:
    return ReportBuilder(report, font_path).build()
