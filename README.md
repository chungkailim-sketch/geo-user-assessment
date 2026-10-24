# 罗德出海GEO准备度评估

**Design:** follows the Ruder Finn brand (ruderfinn.com). It uses the Be Vietnam Pro typeface, RF green `#2da44a` headlines with tight tracking, pill-shaped inputs and buttons, a lime-to-green gradient (`#aaca43` → `#2da571`) and the rf. GEO logo. The theme is light only, like the RF site. Chart colours (green `#27933a` for the brand, deep yellow `#e8a200` for the leader) passed the colour-vision checks. Every report ends with a fixed PR and media strategy recommendation.

A web app that scores how ready a brand's website is for AI search (ChatGPT, Claude, Perplexity, Gemini) and benchmarks it against the leader in its industry.

```
AI Readiness = Citability × 0.55 + Authority × 0.45      (0–100)
```

**UI language:** Simplified Chinese (zh-CN). Terminology follows AIDSO (aidso.com), e.g. 品牌诊断, 被引用和推荐, 品牌提及, 行业标杆, 逐项GEO优化方案. Internal keys and API values (industry names, platform keys) stay in English, and Chinese display names live in `config/industries.json` (`name_zh`, `leader_zh`).

The scoring comes from two modules of the GEO (Generative Engine Optimization) audit skill:

| Component | Weight | What is measured | Source logic |
|---|---|---|---|
| **Citability** | 55% | How easily AI systems can quote passages from the site. Every content block on up to 6 key pages is scored on answer-block quality (30), self-containment (25), structural readability (20), statistical density (15) and uniqueness (10). | `citability_scorer.py`, ported verbatim to `app/scoring/citability.py` |
| **Authority** | 45% | Off-site entity signals that AI models rely on: YouTube 25%, Reddit 25%, Wikipedia/Wikidata 20%, LinkedIn 15%, Other (news, reviews, Quora, GitHub) 15% | `brand-mentions.md` rubric and `brand_scanner.py` |

Score labels: 85+ Excellent · 70–84 Good · 55–69 Moderate · 40–54 Below average · under 40 Needs attention.

### Industry leaders

| Industry | Leader |
|---|---|
| Automotive | Toyota |
| Smart Phone | Apple |
| Consumer Electronics | Sony (benchmarked via sony.net, because sony.com blocks automated access) |
| Smart Home IoT | Samsung |
| Smart Cleaning | Dyson |
| F&B | Starbucks |
| Culture / Lifestyle | Booking.com |

The industry is detected automatically and the user can change it on the results page. Leaders are edited in `config/industries.json`.

With `ANTHROPIC_API_KEY` set, Claude classifies every brand (a leader's own domain is still matched directly). If none of the seven categories fits, for example a bank or an airline, Claude names the brand's actual industry and that industry's global leader. The leader's URL is validated and reduced to its homepage, then the leader is audited like any other site. The AI-identified industry is added to the dropdown alongside the seven defaults. Without a key, industries are matched by keywords only.

To compare against an industry that isn't listed, the user picks **其他（手动输入）** in the dropdown and types the industry name, such as 银行 or 宠物食品. Claude maps the input to a catalogue industry when one matches; otherwise it identifies the industry's leader. The typed industry stays in the dropdown for that report, and the leader lookup is cached for 7 days. This requires an API key.

**核心策略建议 (core strategy recommendation).** Once the industry is known, Claude uses live web search to find the 5–8 media sources that AI assistants cite most for that industry in the brand's main market, such as news outlets, vertical media, review sites, Q&A communities and video platforms. It then writes a PR strategy built around those sources. The report shows the sources with their type and why each matters, followed by the tailored strategy. The result is cached for 24 hours per brand and industry, and is regenerated when the user switches industry. Without a key, or if the call fails, the generic strategy is shown instead.

A **术语表** (glossary) panel opens from the header or from the ⓘ buttons on the results page. It covers the score ranges (overall, passage grades, platform bands), the industry categories, and definitions of the terms used, which follow the GEO skill.

## How Authority is measured

1. **Wikipedia/Wikidata (always, deterministic).** The brand is matched to its Wikidata item through the item's *official website* (P856), so `apple.com` resolves to Apple Inc. and not the fruit. We also read the article size, how many Wikipedia articles cite the domain, and the official social accounts and YouTube subscriber counts recorded on Wikidata.
2. **Claude with web search (when `ANTHROPIC_API_KEY` is set).** Claude applies the skill's YouTube, Reddit, LinkedIn and Other rubric bands to live web evidence. It returns strict JSON, which is validated and clamped. The results page shows "Verified with live web search".
3. **Fallback (no key).** Those four platforms are estimated from profile links on the site and the Wikidata social accounts. The result is labelled **"Limited: on-site signals only"**. Reddit volume in particular can't be verified without web search, so set a key for production use.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env              # add ANTHROPIC_API_KEY for full authority scoring
uvicorn app.main:app --reload     # http://localhost:8000
python -m app.cli warm-leaders    # optional: pre-cache all 7 leader audits (~2 min)
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

The SQLite cache lives in the container, so it resets on every deploy. That's harmless, because leader audits are re-scored on first use.

Results are cached in SQLite (`data/cache.sqlite3`): brand audits for 24 h, leader audits for 7 days. Use "Re-run fresh audit" on the results page to bypass the cache.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/audit` `{url, industry?, refresh?}` | Start an audit; returns `job_id` and `results_url` |
| `GET` | `/api/audit/{job_id}` | Job status with progress and stage, then the full result JSON |
| `POST` | `/api/benchmark` `{audit_id, industry}` | Re-compare against a different industry leader |
| `GET` | `/api/industries` | Industry and leader catalogue |

## Safety and limits

- **SSRF protection.** Only http/https on standard ports. Every hop, including redirects, must resolve to public IPs only, with at most 5 redirects. Responses are capped at 5 MB and must be HTML.
- **Polite crawling.** robots.txt is respected, requests to the same host are spaced 1 s apart, and at most 6 pages are fetched.
- **Bot protection is respected, not bypassed.** Sites that answer with a bot challenge (403, 429, a 202 interstitial, or captcha pages) are reported as "blocks automated analysis" instead of being scored as empty.
- Strict CSP (no inline scripts, no eval). Chart.js is loaded from cdnjs with SRI. All crawled and model text is rendered with `textContent`.
- **DNS-rebinding safe.** The validated IP is pinned at connect time through a custom httpcore network backend, and env proxies are ignored.
- **Cost and abuse limits.** 5 audits per minute and 10 benchmark switches per minute per IP. At most 3 audits run at once and 20 can be queued (beyond that the API returns 503). Each page has a 30 s deadline and each audit 4 min. "Re-run fresh audit" is honoured at most hourly per domain.
- **Trustworthy baselines.** Every audit starts from the site's homepage, so one domain has one canonical result. Results built while Wikipedia or Claude was unavailable are flagged and never cached.
- Behind a reverse proxy, set `FORWARDED_ALLOW_IPS=<proxy ip>` so rate limiting sees real client IPs.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest --cov=app           # unit + integration (mocked network)
python -m playwright install chromium
python -m pytest -m e2e              # browser flow, screenshots saved to test-results/
```
