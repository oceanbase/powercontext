/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

(function () {
  const initialized = new WeakSet();

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function animateCounter(element) {
    if (element.dataset.countAnimated === "true") {
      return;
    }
    element.dataset.countAnimated = "true";

    const target = Number.parseFloat(element.dataset.countTo || "0");
    const decimals = Number.parseInt(element.dataset.countDecimals || "0", 10);
    const suffix = element.dataset.countSuffix || "";

    if (!Number.isFinite(target) || prefersReducedMotion()) {
      element.textContent = `${target.toFixed(decimals)}${suffix}`;
      return;
    }

    const formatter = new Intl.NumberFormat(document.documentElement.lang || "en", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    const duration = 1050;
    const startedAt = performance.now();
    element.textContent = `${formatter.format(0)}${suffix}`;

    function frame(now) {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 4);
      element.textContent = `${formatter.format(target * eased)}${suffix}`;
      if (progress < 1 && document.documentElement.contains(element)) {
        window.requestAnimationFrame(frame);
      }
    }

    window.requestAnimationFrame(frame);
  }

  function reveal(element) {
    element.classList.add("is-visible");
    element.querySelectorAll("[data-count-to]").forEach(animateCounter);
    if (element.matches("[data-count-to]")) {
      animateCounter(element);
    }
  }

  function activateMetric(comparison, id, focusTab) {
    const tabs = Array.from(comparison.querySelectorAll("[data-metric-target]"));
    const panels = Array.from(comparison.querySelectorAll("[data-metric-panel]"));
    const activeTab = tabs.find((tab) => tab.dataset.metricTarget === id);
    const activePanel = panels.find((panel) => panel.dataset.metricPanel === id);

    if (!activeTab || !activePanel) {
      return;
    }

    tabs.forEach((tab) => {
      const selected = tab === activeTab;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.classList.remove("is-active", "is-playing");
      panel.hidden = panel !== activePanel;
    });

    activePanel.classList.add("is-active");
    window.requestAnimationFrame(() => activePanel.classList.add("is-playing"));
    if (focusTab) {
      activeTab.focus();
    }
  }

  function enhanceMetricComparison(comparison) {
    comparison.classList.add("is-enhanced");
    const tabs = Array.from(comparison.querySelectorAll("[data-metric-target]"));
    const initial = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0];
    if (!initial) {
      return;
    }

    activateMetric(comparison, initial.dataset.metricTarget, false);

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateMetric(comparison, tab.dataset.metricTarget, false));
      tab.addEventListener("keydown", (event) => {
        let nextIndex = index;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (index + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (index - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = tabs.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        activateMetric(comparison, tabs[nextIndex].dataset.metricTarget, true);
      });
    });
  }

  function activateLeaderboard(leaderboards, id, focusTab) {
    const tabs = Array.from(leaderboards.querySelectorAll("[data-leaderboard-target]"));
    const panels = Array.from(leaderboards.querySelectorAll("[data-leaderboard-panel]"));
    const activeTab = tabs.find((tab) => tab.dataset.leaderboardTarget === id);
    const activePanel = panels.find((panel) => panel.dataset.leaderboardPanel === id);

    if (!activeTab || !activePanel) {
      return;
    }

    tabs.forEach((tab) => {
      const selected = tab === activeTab;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.classList.remove("is-active", "is-playing");
      panel.hidden = panel !== activePanel;
    });

    activePanel.classList.add("is-active");
    window.requestAnimationFrame(() => activePanel.classList.add("is-playing"));
    if (focusTab) {
      activeTab.focus();
    }
  }

  function enhanceLeaderboards(leaderboards) {
    leaderboards.classList.add("is-enhanced");
    const tabs = Array.from(leaderboards.querySelectorAll("[data-leaderboard-target]"));
    const initial = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0];
    if (!initial) {
      return;
    }

    activateLeaderboard(leaderboards, initial.dataset.leaderboardTarget, false);

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateLeaderboard(leaderboards, tab.dataset.leaderboardTarget, false));
      tab.addEventListener("keydown", (event) => {
        let nextIndex = index;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (index + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (index - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = tabs.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        activateLeaderboard(leaderboards, tabs[nextIndex].dataset.leaderboardTarget, true);
      });
    });
  }

  function initBenchmark() {
    const page = document.querySelector(".pc-benchmark");
    if (!page || initialized.has(page)) {
      return;
    }
    initialized.add(page);

    page.querySelectorAll("[data-metric-comparison]").forEach(enhanceMetricComparison);
    page.querySelectorAll("[data-leaderboards]").forEach(enhanceLeaderboards);
    const revealTargets = Array.from(page.querySelectorAll("[data-benchmark-reveal]"));

    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      revealTargets.forEach(reveal);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) {
            return;
          }
          reveal(entry.target);
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -8%", threshold: 0.16 },
    );
    revealTargets.forEach((target) => observer.observe(target));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initBenchmark);
  } else {
    initBenchmark();
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(initBenchmark);
  }
})();
