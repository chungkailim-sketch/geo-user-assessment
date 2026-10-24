/* Chart builders. Colors come from CSS custom properties so light/dark stay in sync. */
(function (global) {
  "use strict";

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function theme() {
    return {
      s1: token("--series-1"),
      s2: token("--series-2"),
      grid: token("--grid"),
      axis: token("--axis"),
      text: token("--text-secondary"),
      muted: token("--text-muted"),
      surface: token("--surface"),
      track: token("--track"),
      seq: [token("--seq-650"), token("--seq-550"), token("--seq-450"), token("--seq-350"), token("--seq-250")],
    };
  }

  function alpha(hex, a) {
    const n = parseInt(hex.replace("#", ""), 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  function baseOptions(t) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      plugins: {
        legend: { display: false },
        tooltip: { padding: 10, cornerRadius: 8, displayColors: true, boxPadding: 4 },
      },
      font: { family: "system-ui, -apple-system, Segoe UI, sans-serif" },
      color: t.text,
    };
  }

  function radarScale(t) {
    return {
      r: {
        min: 0, max: 100, beginAtZero: true,
        ticks: { stepSize: 25, display: false },
        grid: { color: t.grid },
        angleLines: { color: t.grid },
        pointLabels: { color: t.text, font: { size: 11 } },
      },
    };
  }

  // Split long labels into two lines so radar/axis labels are not clipped on phones.
  function wrap(label) {
    if (label.length <= 14) return label;
    const words = label.split(" ");
    const mid = Math.ceil(words.length / 2);
    return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")];
  }

  const registry = {};
  function mount(id, config) {
    if (registry[id]) registry[id].destroy();
    registry[id] = new Chart(document.getElementById(id), config);
    return registry[id];
  }

  function gauge(id, score) {
    const t = theme();
    return mount(id, {
      type: "doughnut",
      data: {
        labels: ["Score", "Remaining"],
        datasets: [{
          data: [score, Math.max(0, 100 - score)],
          backgroundColor: [t.s1, t.track],
          borderWidth: 0, borderRadius: [4, 0], spacing: 2,
        }],
      },
      options: {
        responsive: false, rotation: -90, circumference: 180, cutout: "78%",
        animation: { duration: 900 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }

  function citabilityRadar(id, labels, values) {
    const t = theme();
    return mount(id, {
      type: "radar",
      data: {
        labels: labels.map(wrap),
        datasets: [{
          label: "Your site", data: values,
          borderColor: t.s1, backgroundColor: alpha(t.s1, 0.18), borderWidth: 2,
          pointBackgroundColor: t.s1, pointBorderColor: t.surface, pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
        }],
      },
      options: Object.assign(baseOptions(t), { scales: radarScale(t) }),
    });
  }

  function gradeBar(id, distribution) {
    const t = theme();
    const grades = ["A", "B", "C", "D", "F"];
    return mount(id, {
      type: "bar",
      data: {
        labels: grades.map((g) => "Grade " + g),
        datasets: [{
          label: "Blocks", data: grades.map((g) => distribution[g] || 0),
          backgroundColor: t.seq, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom",
          maxBarThickness: 44,
        }],
      },
      options: Object.assign(baseOptions(t), {
        scales: {
          x: { grid: { display: false }, ticks: { color: t.text }, border: { color: t.axis } },
          y: { beginAtZero: true, ticks: { color: t.muted, precision: 0 }, grid: { color: t.grid }, border: { display: false } },
        },
      }),
    });
  }

  function platformBar(id, platforms) {
    const t = theme();
    return mount(id, {
      type: "bar",
      data: {
        labels: platforms.map((p) => p.label.split(" (")[0].replace(" / Wikidata", "") + " · " + Math.round(p.weight * 100) + "%"),
        datasets: [{
          label: "Score", data: platforms.map((p) => p.score),
          backgroundColor: t.s1, borderRadius: { topRight: 4, bottomRight: 4 }, borderSkipped: "left",
          maxBarThickness: 22,
        }],
      },
      options: Object.assign(baseOptions(t), {
        indexAxis: "y",
        scales: {
          x: { min: 0, max: 100, ticks: { color: t.muted, stepSize: 25 }, grid: { color: t.grid }, border: { display: false } },
          y: { grid: { display: false }, ticks: { color: t.text }, border: { color: t.axis } },
        },
      }),
    });
  }

  function benchBar(id, brandName, leaderName, brandValues, leaderValues) {
    const t = theme();
    const ds = (label, data, color) => ({
      label: label, data: data, backgroundColor: color,
      borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 40,
    });
    return mount(id, {
      type: "bar",
      data: {
        labels: ["Overall", "Citability", "Authority"],
        datasets: [ds(brandName, brandValues, t.s1), ds(leaderName, leaderValues, t.s2)],
      },
      options: Object.assign(baseOptions(t), {
        datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.92 } },
        scales: {
          x: { grid: { display: false }, ticks: { color: t.text }, border: { color: t.axis } },
          y: { min: 0, max: 100, ticks: { color: t.muted, stepSize: 25 }, grid: { color: t.grid }, border: { display: false } },
        },
      }),
    });
  }

  function benchRadar(id, labels, brandName, leaderName, brandValues, leaderValues) {
    const t = theme();
    const ds = (label, data, color) => ({
      label: label, data: data, borderColor: color, backgroundColor: alpha(color, 0.12), borderWidth: 2,
      pointBackgroundColor: color, pointBorderColor: t.surface, pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
    });
    return mount(id, {
      type: "radar",
      data: { labels: labels.map(wrap), datasets: [ds(brandName, brandValues, t.s1), ds(leaderName, leaderValues, t.s2)] },
      options: Object.assign(baseOptions(t), { scales: radarScale(t) }),
    });
  }

  global.ReadinessCharts = { gauge, citabilityRadar, gradeBar, platformBar, benchBar, benchRadar };
})(window);
