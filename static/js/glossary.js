/* Glossary side panel: opens from the header button or any [data-glossary] info button and
   scrolls to the requested entry. Modal while open (focus trapped, Esc / backdrop close). */
(function () {
  "use strict";

  const panel = document.getElementById("glossary");
  const backdrop = document.getElementById("glossary-backdrop");
  const body = document.getElementById("glossary-body");
  const closeButton = document.getElementById("glossary-close");
  const toggle = document.querySelector(".glossary-toggle");
  if (!panel) return;

  let returnFocus = null;
  let closeTimer = null;

  function focusables() {
    return Array.from(panel.querySelectorAll("a[href], button, summary"))
      .filter((node) => node.offsetParent !== null);
  }

  function reveal(targetId) {
    const target = targetId && document.getElementById(targetId);
    if (!target) return;
    const section = target.closest("details");
    if (section) section.open = true;
    if (target.tagName === "DETAILS") target.open = true;
    target.scrollIntoView({ block: "start" });
    target.classList.remove("flash");
    void target.offsetWidth; // restart the highlight animation
    target.classList.add("flash");
  }

  function open(targetId, opener) {
    clearTimeout(closeTimer);
    returnFocus = opener || document.activeElement;
    panel.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("glossary-open");
    toggle.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => {
      panel.classList.add("open");
      backdrop.classList.add("open");
      if (targetId) reveal(targetId);
      else body.scrollTop = 0;
      closeButton.focus({ preventScroll: true });
    });
  }

  function close() {
    panel.classList.remove("open");
    backdrop.classList.remove("open");
    document.body.classList.remove("glossary-open");
    toggle.setAttribute("aria-expanded", "false");
    closeTimer = setTimeout(() => { panel.hidden = true; backdrop.hidden = true; }, 220);
    if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
  }

  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-glossary]");
    if (!opener) return;
    event.preventDefault();
    if (opener === toggle && panel.classList.contains("open")) return close();
    open(opener.dataset.glossary || null, opener);
  });

  panel.querySelectorAll(".glossary-nav a").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      reveal(link.getAttribute("href").slice(1));
    });
  });

  closeButton.addEventListener("click", close);
  backdrop.addEventListener("click", close);

  document.addEventListener("keydown", (event) => {
    if (!panel.classList.contains("open")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
