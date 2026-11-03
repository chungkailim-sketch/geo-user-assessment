# 罗德出海GEO准备度评估

**Design:** follows the Ruder Finn brand (ruderfinn.com). It uses the Be Vietnam Pro typeface, RF green `#2da44a` headlines with tight tracking, pill-shaped inputs and buttons, a lime-to-green gradient (`#aaca43` → `#2da571`) and the rf. GEO logo. The theme is light only, like the RF site. Chart colours (green `#27933a` for the brand, deep yellow `#e8a200` for the leader) passed the colour-vision checks. Every report ends with a fixed PR and media strategy recommendation.

A web app that scores how ready a brand's website is for AI search (ChatGPT, Claude, Perplexity, Gemini) and benchmarks it against the leader in its industry.

```
AI Readiness = Citability × 0.55 + Authority × 0.45      (0–100)
```

**UI language:** Simplified Chinese (zh-CN). Terminology follows AIDSO (aidso.com), e.g. 品牌诊断, 被引用和推荐, 品牌提及, 行业标杆, 逐项GEO优化方案. Internal keys (platform keys, industry names) stay in English; Claude returns Chinese display names for the industry and its leader (`name_zh`, `leader_zh`).

The scoring comes from two modules of the GEO (Generative Engine Optimization) audit skill:

| Component | Weight | What is measured | Source logic |
|---|---|---|---|
| **Citability** | 55% | How easily AI systems can quote passages from the site. Every content block on up to 6 key pages is scored on answer-block quality (30), self-containment (25), structural readability (20), statistical density (15) and uniqueness (10). | `citability_scorer.py`, ported verbatim to `app/scoring/citability.py` |
| **Authority** | 45% | Off-site entity signals that AI models rely on: YouTube 20%, Reddit 20%, Wikipedia/Wikidata 15%, LinkedIn 10%, Other (news, media, reviews, Q&A) 35% | `brand-mentions.md` rubric and `brand_scanner.py`; weights re-balanced towards news and media coverage |

Score labels: 85+ Excellent · 70–84 Good · 55–69 Moderate · 40–54 Below average · under 40 Needs attention.

### Industry and benchmark leader

There is no fixed list of industries or leaders. For every audit, Claude reads the brand's URL and site content, identifies its industry (as specific as "Smart Cleaning Appliances" or "Retail Banking"), and names the brand AI assistants most often cite and recommend for that industry. The leader's URL is validated and reduced to its homepage. The leader's site is then put through the same GEO audit (crawl, citability, authority), and the two results are compared. If the brand is the leader, the report says so.

**Fallback when the leader blocks audits.** Claude also ranks the next three strongest brands in the industry. If the leader's site can't be audited, the benchmark moves to the next brand in rank order. That covers a bot wall (HTTP 401/403/429 or a challenge page), any other fetch error, or a page with no readable content, such as a site that only renders in the browser. The report then says which leader was replaced. If every named brand fails, Claude is asked once for more brands, up to 7 candidates in all. Only after that is the benchmark shown as unavailable. A blocked site is remembered for 6 hours, so later reports go straight to the next brand. The brand being audited is never used as its own runner-up benchmark.

If the industry is wrong, the user can pick **其他（手动输入）** in the dropdown and type the industry, such as 银行 or 宠物食品. Claude then finds that industry's leader and the comparison and strategy are re-run. The typed industry stays in the dropdown for that report, and the leader lookup is cached for 7 days.

All of this needs `ANTHROPIC_API_KEY`. Without it the report has no industry or benchmark. An audit whose industry can't be identified is not cached, so the next run tries again.
**核心策略建议 (core strategy recommendation).** Once the industry is known, Claude uses live web search to find the 5–8 media sources that AI assistants cite most for that industry in the brand's main market, such as news outlets, vertical media, review sites, Q&A communities and video platforms. It then writes a PR strategy built around those sources. The report shows the sources with their type and why each matters, followed by the tailored strategy. The result is cached for 24 hours per brand and industry, and is regenerated when the user switches industry. Without a key, or if the call fails, the generic strategy is shown instead.

A **术语表** (glossary) panel opens from the header or from the ⓘ buttons on the results page. It covers the score ranges (overall, passage grades, platform bands) and definitions of the terms used, which follow the GEO skill.

## Full PDF report by email

Below the score on the results page, a **发送完整PDF报告至我的邮箱** call to action takes an email address. The server then runs the full audit from the GEO skill (geo.zip, `references/modules/audit.md`), builds a PDF and emails it as an attachment. This takes about 2–4 minutes, and the page shows progress until the email is sent.

The full audit scores six categories and combines them into the **GEO composite score**: Citability × 25% + Brand Authority × 20% + Content E-E-A-T × 20% + Technical GEO × 15% + Schema × 10% + Platform optimization × 10%. Ratings are 90+ 优秀, 75–89 良好, 60–74 一般, 40–59 较弱 and below 40 亟需优化.

| Category | Source |
|---|---|
| AI citability and brand authority | Taken from the quick audit, which uses the same methodology |
| Content E-E-A-T | The `content-eeat` rubric: four 25-point dimensions plus a topical-authority modifier. Claude judges it from page excerpts and on-site signals. Without a key, it is estimated from those signals. |
| Technical GEO | 70% from the `technical-seo` 100-point rubric (crawlability, indexability, security headers, URL structure, mobile, Core Web Vitals proxies, server-side rendering, speed/CDN). 20% from AI crawler access: 12 AI crawlers checked against robots.txt and meta robots, scored with the `crawler-access` formula. 10% from llms.txt structure. |
| Schema | The `schema-structured-data` 100-point rubric: Organization, sameAs, Article/author, business types, WebSite + SearchAction, breadcrumbs, format, validity, knowsAbout, deprecated types |
| Platform optimization | The `platform-optimizer` rubrics for Google AI Overviews, ChatGPT, Perplexity, Gemini and Bing Copilot |

Checks that need site-owner access, such as Bing Webmaster Tools, field Core Web Vitals or the Knowledge Panel, are marked as not measured and left out of the score. They are not scored as failures. Findings are classified as critical, high, medium or low using the severity rules in `audit.md`.

**PDF contents**, in Simplified Chinese on A4, with each section starting on a new page:
- Cover with a score gauge
- Executive summary and score breakdown
- AI citability (subscores and the passages to rewrite)
- Brand AI citation authority (platform scores and evidence)
- Content E-E-A-T
- Technical GEO (AI crawler access table and llms.txt)
- AI platform readiness
- Industry benchmark, when one is available
- Appendix of pages analysed, with the methodology
- A closing page linking to the GEO advisory offering: **欲了解更多，请点击这里** (https://www.ruderfinnasia.com/zh/products/ai-visibility-geo-advisory)

Schema and the technical-SEO checks still count towards the GEO score, but their detail tables aren't printed. The executive summary is written by Claude when a key is set. Without a key it comes from a template.

The email attachment is named `Ruder Finn for <brand>_GEO Readiness Audit_<ddmmyyyy>.pdf`. The name is written on one header line, never split into RFC 2231 continuations, so Outlook and webmail clients keep the `.pdf` name.

**Fonts.** The Docker image installs `fonts-wqy-microhei`, which is embedded as a subset so Chinese renders in every PDF viewer. Locally, Microsoft YaHei is used on Windows. `PDF_FONT_PATH` overrides both.

**Email** is sent over SMTP and is switched off until `SMTP_HOST` and `SMTP_FROM` are set. Until then, the send button is disabled.

**Limits:**
- 3 requests per day per visitor IP, and at most 3 sends per report.
- 2 full audits run at a time, with a 7-minute deadline each.
- The full audit is cached for 24 hours, so a repeat send doesn't re-crawl the site.

## How Authority is measured

1. **Wikipedia/Wikidata (always, deterministic).** The brand is matched to its Wikidata item through the item's *official website* (P856), so `apple.com` resolves to Apple Inc. and not the fruit. We also read the article size, how many Wikipedia articles cite the domain, and the official social accounts and YouTube subscriber counts recorded on Wikidata.
2. **Claude with web search (when `ANTHROPIC_API_KEY` is set).** Claude applies the skill's YouTube, Reddit, LinkedIn and Other rubric bands to live web evidence. It returns strict JSON, which is validated and clamped. The results page shows "Verified with live web search".
3. **Fallback (no key).** Those four platforms are estimated from profile links on the site and the Wikidata social accounts. The result is labelled **"Limited: on-site signals only"**. Reddit volume in particular can't be verified without web search, so set a key for production use.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env              # add ANTHROPIC_API_KEY for full authority scoring
uvicorn app.main:app --reload     # http://localhost:8000
python -m app.cli audit https://www.example.com   # one audit from the terminal
```

Or with Docker: `docker build -t ai-readiness . && docker run -p 8000:8000 --env-file .env ai-readiness`

### Deploy to Railway

The repo includes `railway.json`, which builds the Dockerfile and health-checks `/healthz`. The container listens on Railway's `$PORT`. Set these service variables:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key (leave empty to run in limited mode) |
| `CLAUDE_MODEL` | `claude-opus-5-5` |
| `TRUST_PROXY_HEADERS` | `true`, so rate limits use the real client IP behind Railway's proxy |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` | Your mail server, for example `smtp.office365.com`, `587`, `starttls`. Use `ssl` for port 465. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Mailbox login or API credentials, such as an app password or a SendGrid API key |
| `SMTP_FROM`, `SMTP_FROM_NAME`, `SMTP_REPLY_TO` | Sender address and name, and an optional reply-to address |
| `REPORT_BCC` | Optional. Internal addresses that get a copy of every report sent (comma-separated). |

The SQLite cache lives in the container, so it resets on every deploy. That's harmless, because leader audits are re-scored on first use.

Results are cached in SQLite (`data/cache.sqlite3`): brand audits for 24 h, leader audits for 7 days. Use "Re-run fresh audit" on the results page to bypass the cache.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/audit` `{url, refresh?}` | Start an audit; returns `job_id` and `results_url` |
| `GET` | `/api/audit/{job_id}` | Job status with progress and stage, then the full result JSON |
| `POST` | `/api/benchmark` `{audit_id, industry}` or `{audit_id, custom_industry}` | Re-compare against another industry on the report, or one the user types in |
| `GET` | `/api/report/config` | `{enabled}`: whether email delivery is configured |
| `POST` | `/api/report` `{audit_id, email}` | Start the full audit, then the PDF, then the email. Returns `job_id`. |
| `GET` | `/api/report/{job_id}` | Report job status: `queued`, `running`, `sent` or `error`, with the stage and progress |

## Safety and limits

- **SSRF protection.** Only http/https on standard ports. Every hop, including redirects, must resolve to public IPs only, with at most 5 redirects. Responses are capped at 5 MB and must be HTML.
- **Polite crawling.** robots.txt is respected, requests to the same host are spaced 1 s apart, and at most 6 pages are fetched.
- **Bot protection is respected, not bypassed.** Sites that answer with a bot challenge (403, 429, a 202 interstitial, or captcha pages) are reported as "blocks automated analysis" instead of being scored as empty.
- Strict CSP (no inline scripts, no eval). Chart.js is loaded from cdnjs with SRI. All crawled and model text is rendered with `textContent`.
- **DNS-rebinding safe.** The validated IP is pinned at connect time through a custom httpcore network backend, and env proxies are ignored.
- **Cost and abuse limits.** Each visitor (client IP) can start at most 5 audits per minute. Each site can be freshly audited at most 3 times per day across all visitors. Opening a result that is still cached doesn't count, but "重新诊断" does. The daily cap resets at midnight UTC+8. Benchmark switches are limited to 10 per minute per IP. At most 3 audits run at once and 20 can be queued (beyond that the API returns 503). Each page has a 30 s deadline and each audit 4 min. "Re-run fresh audit" is honoured at most hourly per domain.
- **Trustworthy baselines.** Every audit starts from the site's homepage, so one domain has one canonical result. Results built while Wikipedia or Claude was unavailable are flagged and never cached.
- Behind a reverse proxy, set `FORWARDED_ALLOW_IPS=<proxy ip>` so rate limiting sees real client IPs.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest --cov=app           # unit + integration (mocked network)
python -m playwright install chromium
python -m pytest -m e2e              # browser flow, screenshots saved to test-results/
```
