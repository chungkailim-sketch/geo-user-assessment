/* Results page: poll the audit job, then render the report. All external text uses textContent. */
(function () {
  "use strict";

  const app = document.getElementById("app");
  const jobId = app.dataset.jobId;
  const POLL_MS = 2000;
  const MAX_POLL_ERRORS = 5;
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
      if (response.status === 404) return showError("This assessment was not found or has expired.");
      if (!response.ok) throw new Error("HTTP " + response.status);
      pollErrors = 0;
      const job = await response.json();
      if (job.status === "done") return render(job.result);
      if (job.status === "error") return showError(job.error || "The assessment failed.");
      setProgress(job.progress, job.stage);
    } catch (err) {
      pollErrors += 1;
      if (pollErrors >= MAX_POLL_ERRORS) return showError("Lost connection to the server. Please refresh the page.");
    }
    setTimeout(poll, POLL_MS);
  }

  function setProgress(pct, stage) {
    $("progress-fill").style.width = Math.max(5, pct) + "%";
    $("progress").setAttribute("aria-valuenow", String(pct));
    $("loading-stage").textContent = stage || "Working…";
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
    document.title = result.brand_name + " · AI Readiness Score";
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
    $("audited-at").textContent = "Assessed " + new Date(r.audited_at).toLocaleString() +
      " · " + r.citability.pages_analyzed + " pages analysed";
    ReadinessCharts.gauge("gauge", r.score);

    const select = $("industry-select");
    select.replaceChildren(el("option", { value: "", text: "Choose industry…" }));
    r.industry.options.forEach((name) => {
      const option = el("option", { value: name, text: name });
      if (name === r.industry.selected) option.selected = true;
      select.appendChild(option);
    });
    const method = r.industry.method === "none" ? "not detected" : "detected via " + r.industry.method;
    $("industry-method").textContent = r.industry.selected === r.industry.detected ? method : "manually selected";
    select.onchange = () => select.value && rebenchmark(select.value);
  }

  function renderComponents(r) {
    const c = r.citability.score, a = r.authority.score;
    const cw = r.weights.citability, aw = r.weights.authority;
    $("cit-score").textContent = Math.round(c);
    $("auth-score").textContent = Math.round(a);
    $("cit-meter").style.width = c + "%";
    $("auth-meter").style.width = a + "%";
    $("cit-contribution").textContent = fmt(c) + " × " + cw + " = " + fmt(c * cw) + " points";
    $("auth-contribution").textContent = fmt(a) + " × " + aw + " = " + fmt(a * aw) + " points";

    const comp = $("composition");
    comp.replaceChildren(
      el("div", { className: "seg-cit", width: c * cw + "%", title: "Citability " + fmt(c * cw), text: "Citability " + fmt(c * cw) }),
      el("div", { className: "seg-auth", width: a * aw + "%", title: "Authority " + fmt(a * aw), text: "Authority " + fmt(a * aw) })
    );

    const labels = { critical: "Critical", high: "High", medium: "Medium", strength: "Strength", info: "Note" };
    $("findings").replaceChildren(...r.findings.map((f) =>
      el("li", {}, [el("span", { className: "sev sev-" + f.severity, text: labels[f.severity] || f.severity }), el("span", { text: f.text })])
    ));
  }

  function passageItem(p, withTip) {
    const heading = p.heading && p.heading !== "Introduction" ? p.heading : "(untitled section)";
    return el("li", {}, [
      el("strong", { text: heading + " · " + p.score + "/100 (" + p.grade + ")" }),
      el("span", { className: "preview", text: "“" + p.preview + "”" }),
      withTip ? el("span", { className: "tip", text: "Weakest: " + p.weakest + ". " + p.tip }) : null,
    ]);
  }

  function renderCitability(c) {
    $("cit-meta").textContent = c.pages_analyzed + " pages · " + c.blocks_analyzed + " blocks";
    const keys = Object.keys(c.subscores);
    ReadinessCharts.citabilityRadar("cit-radar", keys.map((k) => c.subscore_labels[k]), keys.map((k) => c.subscores[k]));
    ReadinessCharts.gradeBar("grade-bar", c.grade_distribution);
    $("coverage").textContent = Math.round(c.coverage_pct) + "%";
    $("optimal").textContent = Math.round(c.optimal_length_pct) + "%";
    $("blocks").textContent = c.blocks_analyzed;

    const empty = (text) => el("li", { className: "empty", text: text });
    $("top-passages").replaceChildren(...(c.top_passages.length ? c.top_passages.map((p) => passageItem(p, false)) : [empty("No readable passages found.")]));
    $("bottom-passages").replaceChildren(...(c.bottom_passages.length ? c.bottom_passages.map((p) => passageItem(p, true)) : [empty("No passages below 60. Nice work.")]));
  }

  function renderAuthority(a) {
    const badge = $("confidence");
    badge.className = "badge " + a.confidence;
    badge.textContent = a.confidence === "full" ? "Verified with live web search" : "Limited: on-site signals only";
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
      el("div", { className: "value" + (ahead ? " ahead" : ""), text: signed(gap) + " pts" }),
      el("div", { className: "detail", text: ahead ? "ahead of " + leaderName : "behind " + leaderName }),
    ]);
  }

  function renderBenchmark(b) {
    const status = $("benchmark-status");
    const body = $("benchmark-body");
    if (!b) {
      $("benchmark-title").textContent = "Compared with the industry leader";
      status.textContent = "Choose an industry above to compare against its leader.";
      body.hidden = true;
      return;
    }
    $("benchmark-title").textContent = "Compared with " + b.leader + " (" + b.industry + " leader)";
    if (b.status !== "ok") {
      status.textContent = "We couldn't assess " + b.leader + ", so the comparison is unavailable. " +
        (b.reason || "Try again later.");
      body.hidden = true;
      return;
    }
    body.hidden = false;
    const brand = report.brand_name;
    const leader = b.leader;
    $("legend-brand").textContent = brand;
    $("legend-leader").textContent = leader;
    const ls = b.leader_scores;

    if (b.is_leader) {
      status.textContent = "";
      $("gap-tiles").replaceChildren(el("div", { className: "leader-banner", text: "You are the category benchmark for " + b.industry + "." }));
    } else {
      status.textContent = "";
      $("gap-tiles").replaceChildren(
        gapTile("Overall readiness", b.gaps.overall, leader),
        gapTile("Citability", b.gaps.citability, leader),
        gapTile("Authority", b.gaps.authority, leader)
      );
    }

    const brandVals = [report.score, report.citability.score, report.authority.score];
    const leaderVals = [ls.score, ls.citability, ls.authority];
    ReadinessCharts.benchBar("bench-bar", brand, leader, brandVals, leaderVals);
    const platforms = report.authority.platforms;
    ReadinessCharts.benchRadar(
      "bench-radar", platforms.map((p) => p.label.split(" (")[0]), brand, leader,
      platforms.map((p) => p.score), platforms.map((p) => ls.platforms[p.key] || 0)
    );

    const rows = [["Overall", brandVals[0], leaderVals[0]], ["Citability", brandVals[1], leaderVals[1]], ["Authority", brandVals[2], leaderVals[2]]]
      .concat(platforms.map((p) => ["  " + p.label, p.score, ls.platforms[p.key] || 0]));
    $("bench-table").replaceChildren(
      el("thead", {}, [el("tr", {}, [el("th", { text: "Metric" }), el("th", { text: brand }), el("th", { text: leader }), el("th", { text: "Gap" })])]),
      el("tbody", {}, rows.map((row) => el("tr", {}, [
        el("td", { text: row[0] }), el("td", { text: fmt(row[1]) }), el("td", { text: fmt(row[2]) }), el("td", { text: signed(row[1] - row[2]) }),
      ])))
    );
  }

  function renderActions(actions) {
    $("actions").replaceChildren(...actions.map((a) => el("li", {}, [
      el("strong", { text: a.area + " (currently " + Math.round(a.current) + "/100)" }),
      el("span", { text: a.action + " " }),
      el("span", { className: "lift", text: "Up to +" + fmt(a.potential_lift) + " readiness points" }),
    ])));
  }

  async function rebenchmark(industry) {
    $("benchmark-status").textContent = "Assessing the " + industry + " leader… this can take up to a minute the first time.";
    $("benchmark-body").hidden = true;
    try {
      const response = await fetch("/api/benchmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audit_id: jobId, industry: industry }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const benchmark = await response.json();
      report = Object.assign({}, report, { benchmark: benchmark, industry: Object.assign({}, report.industry, { selected: industry }) });
      $("industry-method").textContent = report.industry.selected === report.industry.detected ? "detected" : "manually selected";
      renderBenchmark(benchmark);
    } catch (err) {
      $("benchmark-status").textContent = "Could not load the comparison. Please try again.";
    }
  }

  $("rerun").addEventListener("click", async () => {
    $("rerun").disabled = true;
    try {
      const response = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: report.url, industry: report.industry.selected, refresh: true }),
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
