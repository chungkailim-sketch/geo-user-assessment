/* Results page: poll the audit job, then render the report. All external text uses textContent. */
(function () {
  "use strict";

  const app = document.getElementById("app");
  const jobId = app.dataset.jobId;
  const POLL_MS = 2000;
  const MAX_POLL_ERRORS = 5;
  const METHOD_LABELS = {
    "leader domain": "标杆官网识别",
    keywords: "关键词识别",
    "ai classification": "AI识别",
    "ai custom": "AI识别",
    ambiguous: "关键词识别（待确认）",
    none: "未识别",
  };
  const OTHER = "__other__";
  const $ = (id) => document.getElementById(id);
  let report = null;

  function el(tag, props, children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (key === "text") node.textContent = value;
      else if (key === "className") node.className = value;
      else if (key === "width") node.style.width = value; // CSSOM: allowed under the strict CSP
      else node.setAttribute(key, value);
    });
    (children || []).forEach((child) => child && node.appendChild(child));
    return node;
  }

  function safeHref(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch (e) {
      return null;
    }
  }

  function fmt(n) { return (Math.round(n * 10) / 10).toString(); }
  function signed(n) { return (n > 0 ? "+" : n < 0 ? "−" : "±") + fmt(Math.abs(n)); }

  // ---------- polling ----------
  let pollErrors = 0;
  async function poll() {
    try {
      const response = await fetch("/api/audit/" + encodeURIComponent(jobId), { cache: "no-store" });
      if (response.status === 404) return showError("未找到该诊断报告，或报告已过期。");
      if (!response.ok) throw new Error("HTTP " + response.status);
      pollErrors = 0;
      const job = await response.json();
      if (job.status === "done") return render(job.result);
      if (job.status === "error") return showError(job.error || "诊断失败。");
      setProgress(job.progress, job.stage);
    } catch (err) {
      pollErrors += 1;
      if (pollErrors >= MAX_POLL_ERRORS) return showError("与服务器的连接已中断，请刷新页面。");
    }
    setTimeout(poll, POLL_MS);
  }

  function setProgress(pct, stage) {
    $("progress-fill").style.width = Math.max(5, pct) + "%";
    $("progress").setAttribute("aria-valuenow", String(pct));
    $("loading-stage").textContent = stage || "处理中…";
  }

  function showError(message) {
    $("loading").hidden = true;
    $("error").hidden = false;
    $("error-message").textContent = message;
  }

  // ---------- rendering ----------
  function render(result) {
    report = result;
    $("loading").hidden = true;
    $("report").hidden = false;
    document.title = result.brand_name + " · 罗德出海GEO准备度评估";
    renderSummary(result);
    renderComponents(result);
    renderCitability(result.citability);
    renderAuthority(result.authority);
    renderBenchmark(result.benchmark);
    renderActions(result.actions);
  }

  function renderSummary(r) {
    $("brand-name").textContent = r.brand_name;
    const link = $("brand-url");
    link.textContent = r.url;
    const href = safeHref(r.url);
    if (href) link.href = href;
    $("score-value").textContent = Math.round(r.score);
    $("score-label").textContent = r.label;
    $("audited-at").textContent = "诊断时间 " + new Date(r.audited_at).toLocaleString("zh-CN") +
      " · 共分析 " + r.citability.pages_analyzed + " 个页面";
    ReadinessCharts.gauge("gauge", r.score);
    renderIndustry(r.industry);
  }

  function renderIndustry(industry) {
    const select = $("industry-select");
    select.replaceChildren(el("option", { value: "", text: "选择行业…" }));
    const labels = industry.option_labels || {};
    const detectedCustom = industry.custom ? industry.custom.name : null;
    const customNames = industry.custom_names || [];
    industry.options.forEach((name) => {
      const suffix = name === detectedCustom ? "（AI识别）" : customNames.includes(name) ? "（自定义）" : "";
      const option = el("option", { value: name, text: (labels[name] || name) + suffix });
      if (name === industry.selected) option.selected = true;
      select.appendChild(option);
    });
    select.appendChild(el("option", { value: OTHER, text: "其他（手动输入）…" }));
    $("industry-method").textContent = industry.selected === industry.detected
      ? (METHOD_LABELS[industry.method] || "自动识别")
      : customNames.includes(industry.selected) && industry.selected !== detectedCustom ? "手动输入" : "手动选择";
    $("custom-industry-form").hidden = true;
  }

  function renderComponents(r) {
    const c = r.citability.score, a = r.authority.score;
    const cw = r.weights.citability, aw = r.weights.authority;
    $("cit-score").textContent = Math.round(c);
    $("auth-score").textContent = Math.round(a);
    $("cit-meter").style.width = c + "%";
    $("auth-meter").style.width = a + "%";
    $("cit-contribution").textContent = fmt(c) + " × " + cw + " = 贡献 " + fmt(c * cw) + " 分";
    $("auth-contribution").textContent = fmt(a) + " × " + aw + " = 贡献 " + fmt(a * aw) + " 分";

    const comp = $("composition");
    comp.replaceChildren(
      el("div", { className: "seg-cit", width: c * cw + "%", title: "可引用性 " + fmt(c * cw), text: "可引用性 " + fmt(c * cw) }),
      el("div", { className: "seg-auth", width: a * aw + "%", title: "权威度 " + fmt(a * aw), text: "权威度 " + fmt(a * aw) })
    );

    const labels = { critical: "严重", high: "高", medium: "中", strength: "优势", info: "提示" };
    $("findings").replaceChildren(...r.findings.map((f) =>
      el("li", {}, [el("span", { className: "sev sev-" + f.severity, text: labels[f.severity] || f.severity }), el("span", { text: f.text })])
    ));
  }

  function passageItem(p, withTip) {
    const heading = p.heading && p.heading !== "Introduction" ? p.heading : "（无标题段落）";
    return el("li", {}, [
      el("strong", { text: heading + " · " + p.score + "/100 (" + p.grade + ")" }),
      el("span", { className: "preview", text: "“" + p.preview + "”" }),
      withTip ? el("span", { className: "tip", text: "短板：" + p.weakest + "。" + p.tip }) : null,
    ]);
  }

  function renderCitability(c) {
    $("cit-meta").textContent = c.pages_analyzed + " 个页面 · " + c.blocks_analyzed + " 个内容块";
    const keys = Object.keys(c.subscores);
    ReadinessCharts.citabilityRadar("cit-radar", keys.map((k) => c.subscore_labels[k]), keys.map((k) => c.subscores[k]));
    ReadinessCharts.gradeBar("grade-bar", c.grade_distribution);
    $("coverage").textContent = Math.round(c.coverage_pct) + "%";
    $("optimal").textContent = Math.round(c.optimal_length_pct) + "%";
    $("blocks").textContent = c.blocks_analyzed;

    const empty = (text) => el("li", { className: "empty", text: text });
    $("top-passages").replaceChildren(...(c.top_passages.length ? c.top_passages.map((p) => passageItem(p, false)) : [empty("未找到可读取的段落。")]));
    $("bottom-passages").replaceChildren(...(c.bottom_passages.length ? c.bottom_passages.map((p) => passageItem(p, true)) : [empty("暂无低于60分的段落，表现不错。")]));
  }

  function renderAuthority(a) {
    const badge = $("confidence");
    badge.className = "badge " + a.confidence;
    badge.textContent = a.confidence === "full" ? "已通过联网搜索核实" : "有限：仅基于官网与Wikidata信号";
    ReadinessCharts.platformBar("platform-bar", a.platforms);
    $("platforms").replaceChildren(...a.platforms.map((p) => {
      const sources = p.sources.map(safeHref).filter(Boolean).slice(0, 3).map((href) => {
        let host = href;
        try { host = new URL(href).hostname.replace(/^www\./, ""); } catch (e) { /* keep href */ }
        return el("a", { href: href, target: "_blank", rel: "noopener noreferrer", text: host });
      });
      return el("article", { className: "platform" }, [
        el("header", {}, [el("span", { text: p.label }), el("span", { text: p.score + "/100" })]),
        el("ul", {}, p.evidence.map((e) => el("li", { text: e }))),
        sources.length ? el("div", { className: "sources" }, sources) : null,
      ]);
    }));
  }

  function gapTile(label, gap, leaderName) {
    const ahead = gap >= 0;
    return el("div", { className: "gap-tile" }, [
      el("div", { className: "label", text: label }),
      el("div", { className: "value" + (ahead ? " ahead" : ""), text: signed(gap) + " 分" }),
      el("div", { className: "detail", text: ahead ? "领先" + leaderName : "落后" + leaderName }),
    ]);
  }

  function renderBenchmark(b) {
    const status = $("benchmark-status");
    const body = $("benchmark-body");
    if (!b) {
      $("benchmark-title").textContent = "行业标杆对比";
      status.textContent = "请在上方选择所属行业，与该行业标杆品牌进行对比。";
      body.hidden = true;
      return;
    }
    const leader = b.leader_zh || b.leader;
    $("benchmark-title").textContent = "对标" + (b.industry_zh || b.industry) + "行业标杆：" + leader;
    if (b.status !== "ok") {
      status.textContent = "暂时无法评估" + leader + "，对比数据不可用。" + (b.reason || "请稍后重试。");
      body.hidden = true;
      return;
    }
    body.hidden = false;
    const brand = report.brand_name;
    $("legend-brand").textContent = brand;
    $("legend-leader").textContent = leader;
    const ls = b.leader_scores;

    if (b.is_leader) {
      status.textContent = "";
      $("gap-tiles").replaceChildren(el("div", { className: "leader-banner", text: "你就是" + (b.industry_zh || b.industry) + "行业的标杆品牌。" }));
    } else {
      status.textContent = "";
      $("gap-tiles").replaceChildren(
        gapTile("综合就绪度", b.gaps.overall, leader),
        gapTile("内容可引用性", b.gaps.citability, leader),
        gapTile("品牌 AI 引用权威度", b.gaps.authority, leader)
      );
    }

    const brandVals = [report.score, report.citability.score, report.authority.score];
    const leaderVals = [ls.score, ls.citability, ls.authority];
    ReadinessCharts.benchBar("bench-bar", brand, leader, brandVals, leaderVals);
    const platforms = report.authority.platforms;
    ReadinessCharts.benchRadar(
      "bench-radar", platforms.map((p) => p.short_label || p.label), brand, leader,
      platforms.map((p) => p.score), platforms.map((p) => ls.platforms[p.key] || 0)
    );

    const rows = [["综合得分", brandVals[0], leaderVals[0]], ["内容可引用性", brandVals[1], leaderVals[1]], ["品牌 AI 引用权威度", brandVals[2], leaderVals[2]]]
      .concat(platforms.map((p) => ["  " + (p.short_label || p.label), p.score, ls.platforms[p.key] || 0]));
    $("bench-table").replaceChildren(
      el("thead", {}, [el("tr", {}, [el("th", { text: "指标" }), el("th", { text: brand }), el("th", { text: leader }), el("th", { text: "差距" })])]),
      el("tbody", {}, rows.map((row) => el("tr", {}, [
        el("td", { text: row[0] }), el("td", { text: fmt(row[1]) }), el("td", { text: fmt(row[2]) }), el("td", { text: signed(row[1] - row[2]) }),
      ])))
    );
  }

  function mediaItem(m) {
    const href = safeHref(m.url || "");
    const name = href
      ? el("a", { href: href, target: "_blank", rel: "noopener noreferrer", className: "media-name", text: m.name })
      : el("span", { className: "media-name", text: m.name });
    return el("li", {}, [
      el("div", { className: "media-head" }, [name, m.kind ? el("span", { className: "media-kind", text: m.kind }) : null]),
      m.reason ? el("span", { className: "media-reason", text: m.reason }) : null,
    ]);
  }

  function strategyItem(a) {
    const media = a.media && a.media.length
      ? el("div", { className: "media-sources" }, [
          el("p", { className: "media-title", text: "AI高频引用的媒体信源 · " + (a.industry_zh || "所属行业") + (a.market ? " · " + a.market : "") }),
          el("ul", { className: "media-list" }, a.media.map(mediaItem)),
          el("p", { className: "media-title", text: "策略建议" }),
        ])
      : null;
    return el("li", { className: "strategy" }, [
      el("span", { className: "strategy-tag", text: "核心策略建议" }),
      el("strong", { text: a.area }),
      media,
      el("span", { className: "strategy-text", text: a.action }),
      a.tailored ? el("span", { className: "strategy-note", text: "基于AI联网检索的行业媒体引用分析生成。生成的回答未经人工审核，仅供参考，可能包含错误、不准确或过时的信息，不构成任何专业建议。如有疑问，品牌可向罗德业务经理咨询。" }) : null,
    ]);
  }

  function renderActions(actions) {
    $("actions").replaceChildren(...actions.map((a) => a.kind === "strategy"
      ? strategyItem(a)
      : el("li", {}, [
          el("strong", { text: a.area + "（当前 " + Math.round(a.current) + "/100）" }),
          el("span", { text: a.action + " " }),
          el("span", { className: "lift", text: "预计最多提升 +" + fmt(a.potential_lift) + " 分" }),
        ])));
  }

  // Re-compare with another industry. The server also re-tailors the media strategy, so the stored
  // report is re-read afterwards and the industry, benchmark and actions re-rendered together.
  async function rebenchmark(payload, label) {
    const select = $("industry-select");
    select.disabled = true;
    $("custom-industry-submit").disabled = true;
    $("custom-industry-error").hidden = true;
    $("benchmark-status").textContent = "正在评估" + label + "行业标杆并分析该行业AI高频引用媒体…约需一至两分钟。";
    $("benchmark-body").hidden = true;
    try {
      const response = await fetch("/api/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.assign({ audit_id: jobId }, payload)),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "对比数据加载失败，请重试。";
        throw new Error(detail);
      }
      const refreshed = await fetch("/api/audit/" + encodeURIComponent(jobId), { cache: "no-store" });
      const job = await refreshed.json();
      report = job.result;
      renderIndustry(report.industry);
      renderBenchmark(report.benchmark);
      renderActions(report.actions);
    } catch (err) {
      const message = err && err.message && !/^HTTP|Failed to fetch/.test(err.message) ? err.message : "对比数据加载失败，请重试。";
      if (payload.custom_industry) {
        $("custom-industry-error").textContent = message;
        $("custom-industry-error").hidden = false;
        $("custom-industry-form").hidden = false;
      }
      $("benchmark-status").textContent = message;
    } finally {
      select.disabled = false;
      $("custom-industry-submit").disabled = false;
    }
  }

  $("industry-select").addEventListener("change", (event) => {
    const value = event.target.value;
    const form = $("custom-industry-form");
    if (value === OTHER) {
      form.hidden = false;
      $("custom-industry").focus();
      return;
    }
    form.hidden = true;
    $("custom-industry-error").hidden = true;
    if (value) rebenchmark({ industry: value }, (report.industry.option_labels || {})[value] || value);
  });

  $("custom-industry-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("custom-industry").value.trim();
    if (!text) {
      $("custom-industry-error").textContent = "请输入行业名称，例如：银行、宠物食品。";
      $("custom-industry-error").hidden = false;
      return;
    }
    rebenchmark({ custom_industry: text }, "“" + text + "”");
  });

  $("rerun").addEventListener("click", async () => {
    $("rerun").disabled = true;
    // Industries outside the catalogue are re-detected on re-run; only catalogue industries can be pinned.
    const selected = report.industry.selected;
    const pinned = (report.industry.custom_names || []).includes(selected) ? null : selected;
    try {
      const response = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: report.url, industry: pinned, refresh: true }),
      });
      const data = await response.json();
      if (response.ok) window.location.assign(data.results_url);
      else $("rerun").disabled = false;
    } catch (err) {
      $("rerun").disabled = false;
    }
  });

  poll();
})();
