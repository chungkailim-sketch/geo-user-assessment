(function () {
  "use strict";

  const PREFIX = "https://www.";
  const SUFFIX = ".com";
  const TEMPLATE = PREFIX + SUFFIX;

  const form = document.getElementById("audit-form");
  const urlInput = document.getElementById("url");
  const submit = document.getElementById("submit");
  const errorBox = document.getElementById("form-error");

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    submit.disabled = false;
  }

  // Put the caret between "https://www." and ".com" so the user only types the company name.
  function placeCaretForName() {
    const value = urlInput.value;
    if (value.startsWith(PREFIX) && value.endsWith(SUFFIX)) {
      const pos = value.length - SUFFIX.length;
      urlInput.setSelectionRange(pos, pos);
    }
  }

  urlInput.addEventListener("focus", () => requestAnimationFrame(placeCaretForName));
  urlInput.addEventListener("click", () => {
    if (urlInput.value === TEMPLATE) placeCaretForName();
  });

  // Pasting a full address (e.g. "dyson.co.uk" or "https://...") replaces the template.
  urlInput.addEventListener("paste", (event) => {
    const pasted = (event.clipboardData || window.clipboardData).getData("text").trim();
    if (urlInput.value === TEMPLATE && (pasted.includes(".") || pasted.includes("://"))) {
      event.preventDefault();
      urlInput.value = pasted;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const url = urlInput.value.trim();
    if (!url || url === TEMPLATE || url === PREFIX) {
      showError("请在 www. 与 .com 之间输入公司名称。");
      urlInput.focus();
      return;
    }
    submit.disabled = true;
    try {
      const response = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "请检查网址后重试。";
        showError(detail);
        return;
      }
      window.location.assign(data.results_url);
    } catch (err) {
      showError("无法连接服务器，请稍后重试。");
    }
  });
})();
