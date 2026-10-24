(function () {
  "use strict";

  const form = document.getElementById("audit-form");
  const urlInput = document.getElementById("url");
  const industry = document.getElementById("industry");
  const submit = document.getElementById("submit");
  const errorBox = document.getElementById("form-error");

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    submit.disabled = false;
    submit.textContent = "Assess";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.hidden = true;
    const url = urlInput.value.trim();
    if (!url) {
      showError("Please enter a website URL.");
      urlInput.focus();
      return;
    }
    submit.disabled = true;
    submit.textContent = "Starting…";
    try {
      const response = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url, industry: industry.value || null }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = typeof data.detail === "string" ? data.detail : "Please check the URL and try again.";
        showError(detail);
        return;
      }
      window.location.assign(data.results_url);
    } catch (err) {
      showError("Could not reach the server. Please try again.");
    }
  });
})();
