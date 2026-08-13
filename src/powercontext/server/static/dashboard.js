"use strict";

import {
  clearServerToken,
  fetchWithBearer,
  readServerToken,
  storeServerToken
} from "./auth.js?v=session-shell";
import {createPageUi, createRequestGate} from "./page-ui.js?v=request-gate";

const translations = {
  en: {
    pageTitle: "PowerContext Dashboard",
    dashboardTitle: "Dashboard",
    handoffReportTitle: "Handoff Report",
    maintainedBy: "Maintained by OceanBase.",
    signOut: "Sign out",
    authTitle: "Connect to PowerContext",
    authIntro: "Enter the bearer token configured for this PowerContext Server. The token stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    selectScope: "Scope",
    period30: "Last 30 days",
    estimatedReduction: "Estimated token reduction",
    sources: "Sources",
    memoryEntries: "Memory entries",
    artifacts: "Artifacts",
    pendingReview: "Pending review",
    artifactFamilies: "Artifact families",
    artifactSubtitle: "Current Artifacts and pending Candidates",
    family: "Family",
    currentArtifacts: "Current Artifacts",
    pendingCandidates: "Pending Candidates",
    experience: "Experience",
    handoff: "Handoff",
    memory: "Memory",
    skill: "Skill",
    dailyActivity: "Daily recall",
    noRecall: "No hit",
    hitNoReduction: "Hit · ≤0",
    reductionLow: "1–255",
    reductionMedium: "256–1,023",
    reductionHigh: "1,024+",
    recallTrend: "Recall savings trend",
    trendSubtitle: "Estimated token reduction over the last 30 days",
    estimatedReductionSeries: "Estimated reduction",
    dark: "Dark",
    light: "Light",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "\u4e2d\u6587",
    updated: "Updated {value}",
    recallHits: "{hits} recall hits from {preparations} preparations",
    activitySummary: "{hits} recall hits · estimated token reduction {savings} in the last 30 days",
    activityAria: "30 days of scoped recall hits and estimated token reduction",
    activityHit: "{date}: {hits} recall hits · estimated token reduction {savings}",
    trendDescription: "{hits} recall hits. Estimated token reduction {savings}.",
    authRejected: "The Server rejected this token.",
    requestFailed: "The Dashboard request failed with HTTP {status}.",
    serverUnavailable: "The Server is unavailable.",
    retry: "Retry",
    noScopes: "No Dashboard scopes are configured.",
    scopeUnavailable: "The selected scope is not available."
  },
  zh: {
    pageTitle: "PowerContext \u4eea\u8868\u76d8",
    dashboardTitle: "\u4eea\u8868\u76d8",
    handoffReportTitle: "\u4ea4\u63a5\u62a5\u544a",
    maintainedBy: "\u7531 OceanBase \u7ef4\u62a4\u3002",
    signOut: "\u9000\u51fa",
    authTitle: "\u8fde\u63a5 PowerContext",
    authIntro: "\u8bf7\u8f93\u5165 PowerContext Server \u914d\u7f6e\u7684 bearer token\u3002Token \u4ec5\u4fdd\u7559\u5728\u5f53\u524d\u6d4f\u89c8\u5668\u6807\u7b7e\u9875\u3002",
    tokenLabel: "\u670d\u52a1\u5668 Token",
    continue: "\u7ee7\u7eed",
    selectScope: "\u4f5c\u7528\u57df",
    period30: "\u8fc7\u53bb 30 \u5929",
    estimatedReduction: "\u9884\u4f30 Token \u51cf\u5c11\u91cf",
    sources: "\u6570\u636e\u6e90",
    memoryEntries: "Memory \u6761\u76ee",
    artifacts: "Artifacts",
    pendingReview: "\u5f85\u5ba1\u6838",
    artifactFamilies: "Artifact \u7c7b\u578b",
    artifactSubtitle: "\u5f53\u524d Artifact \u4e0e\u5f85\u5ba1\u6838 Candidate",
    family: "\u7c7b\u578b",
    currentArtifacts: "\u5f53\u524d Artifacts",
    pendingCandidates: "\u5f85\u5ba1\u6838 Candidates",
    experience: "Experience",
    handoff: "Handoff",
    memory: "Memory",
    skill: "Skill",
    dailyActivity: "\u6bcf\u65e5\u53ec\u56de",
    noRecall: "\u65e0\u547d\u4e2d",
    hitNoReduction: "\u547d\u4e2d \u00b7 \u22640",
    reductionLow: "1\u2013255",
    reductionMedium: "256\u20131,023",
    reductionHigh: "1,024+",
    recallTrend: "Recall \u8282\u7ea6\u8d8b\u52bf",
    trendSubtitle: "\u8fc7\u53bb 30 \u5929\u7684\u9884\u4f30 Token \u51cf\u5c11\u91cf",
    estimatedReductionSeries: "\u9884\u4f30\u51cf\u5c11\u91cf",
    dark: "\u6df1\u8272",
    light: "\u6d45\u8272",
    switchDark: "\u5207\u6362\u81f3\u6df1\u8272\u6a21\u5f0f",
    switchLight: "\u5207\u6362\u81f3\u6d45\u8272\u6a21\u5f0f",
    switchChinese: "\u5207\u6362\u81f3\u4e2d\u6587",
    switchEnglish: "\u5207\u6362\u81f3\u82f1\u6587",
    languageChinese: "\u4e2d\u6587",
    updated: "\u66f4\u65b0\u4e8e {value}",
    recallHits: "{preparations} \u6b21\u51c6\u5907\u4e2d\u547d\u4e2d {hits} \u6b21 Recall",
    activitySummary: "\u8fc7\u53bb 30 \u5929\u547d\u4e2d {hits} \u6b21 Recall \u00b7 \u9884\u4f30 Token \u51cf\u5c11\u91cf {savings}",
    activityAria: "\u5f53\u524d scope \u8fc7\u53bb 30 \u5929\u7684 Recall \u547d\u4e2d\u548c\u9884\u4f30 Token \u51cf\u5c11\u91cf",
    activityHit: "{date}\uff1a\u547d\u4e2d {hits} \u6b21 Recall \u00b7 \u9884\u4f30 Token \u51cf\u5c11\u91cf {savings}",
    trendDescription: "Recall \u547d\u4e2d {hits} \u6b21\uff0c\u9884\u4f30 Token \u51cf\u5c11\u91cf {savings}\u3002",
    authRejected: "Server \u62d2\u7edd\u4e86\u8be5 Token\u3002",
    requestFailed: "Dashboard \u8bf7\u6c42\u5931\u8d25\uff08HTTP {status}\uff09\u3002",
    serverUnavailable: "Server \u65e0\u6cd5\u8bbf\u95ee\u3002",
    retry: "\u91cd\u8bd5",
    noScopes: "\u672a\u914d\u7f6e Dashboard \u4f5c\u7528\u57df\u3002",
    scopeUnavailable: "\u9009\u4e2d\u7684\u4f5c\u7528\u57df\u4e0d\u53ef\u7528\u3002"
  }
};
const authShell = document.getElementById("auth-shell");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const tokenInput = document.getElementById("token");
const pageStatus = document.getElementById("page-status");
const pageStatusMessage = document.getElementById("page-status-message");
const pageStatusRetry = document.getElementById("page-status-retry");
const dashboard = document.getElementById("dashboard");
const signOut = document.getElementById("sign-out");
const scopeSelect = document.getElementById("scope-select");
const svgNamespace = "http://www.w3.org/2000/svg";
let currentView = null;
let currentScopes = [];
let currentAuthError = null;
let currentPageStatus = null;
let currentScopeId = "";
const ui = createPageUi(translations, () => {
  renderAuthError();
  renderPageStatus();
  if (currentView !== null) {
    renderDashboard(currentView);
  }
});
const {formatDateTime, formatNumber, translate} = ui;
const dashboardRequests = createRequestGate();

scopeSelect.addEventListener("change", async () => {
  await loadStatistics(readServerToken(), scopeSelect.value);
});

pageStatusRetry.addEventListener("click", async () => {
  await authenticate(readServerToken(), currentScopeId);
});

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authError.textContent = "";
  await authenticate(tokenInput.value);
});

signOut.addEventListener("click", () => {
  clearServerToken();
  tokenInput.value = "";
  showLogin();
});

async function authenticate(token, scopeId = "") {
  if (!token) {
    showLogin();
    return;
  }

  storeServerToken(token);
  tokenInput.value = "";
  currentAuthError = null;
  const request = dashboardRequests.start();
  scopeSelect.disabled = true;
  try {
    const response = await fetchWithBearer("/dashboard/scopes", token);
    if (!request.isCurrent()) {
      return;
    }
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      showPageStatus("requestFailed", {status: response.status}, true);
      return;
    }
    currentScopes = await response.json();
    if (!request.isCurrent()) {
      return;
    }
    if (currentScopes.length === 0) {
      showPageStatus("noScopes", {}, true);
      return;
    }
    const selectedScopeId = currentScopes.some((scope) => scope.scope_id === scopeId)
      ? scopeId
      : currentScopes[0].scope_id;
    currentScopeId = selectedScopeId;
    await loadStatistics(token, selectedScopeId, request);
  } catch (error) {
    if (request.isCurrent()) {
      showPageStatus("serverUnavailable", {}, true);
    }
  } finally {
    if (request.isCurrent()) {
      scopeSelect.disabled = false;
    }
  }
}

async function loadStatistics(token, scopeId, request = null) {
  if (!token) {
    showLogin();
    return;
  }

  const activeRequest = request || dashboardRequests.start();
  currentScopeId = scopeId;
  scopeSelect.disabled = true;
  try {
    const url = new URL("/v1/stats", window.location.origin);
    url.searchParams.set("scope_id", scopeId);
    url.searchParams.set("period", "30d");
    const response = await fetchWithBearer(url, token);
    if (!activeRequest.isCurrent()) {
      return;
    }
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      showPageStatus("requestFailed", {status: response.status}, true);
      return;
    }
    const statistics = await response.json();
    if (!activeRequest.isCurrent()) {
      return;
    }
    const selectedScope = currentScopes.find((scope) => scope.scope_id === statistics.scope_id);
    if (!selectedScope) {
      showPageStatus("scopeUnavailable", {}, true);
      return;
    }
    renderDashboard({scopes: currentScopes, selectedScope, statistics});
  } catch (error) {
    if (activeRequest.isCurrent()) {
      showPageStatus("serverUnavailable", {}, true);
    }
  } finally {
    if (activeRequest.isCurrent()) {
      scopeSelect.disabled = false;
    }
  }
}

function showLogin(messageKey = "", values = {}) {
  dashboardRequests.cancel();
  scopeSelect.disabled = false;
  currentView = null;
  currentScopes = [];
  currentScopeId = "";
  currentPageStatus = null;
  currentAuthError = messageKey ? {key: messageKey, values} : null;
  renderAuthError();
  authShell.hidden = false;
  pageStatus.hidden = true;
  dashboard.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, values = {}, retryable = false) {
  currentView = null;
  currentPageStatus = {key: messageKey, values, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  dashboard.hidden = true;
  signOut.hidden = false;
}

function renderPageStatus() {
  if (currentPageStatus === null) {
    pageStatusMessage.textContent = "";
    pageStatusRetry.hidden = true;
    return;
  }
  pageStatusMessage.textContent = translate(
    currentPageStatus.key,
    currentPageStatus.values
  );
  pageStatusRetry.hidden = !currentPageStatus.retryable;
}

function renderAuthError() {
  authError.textContent = currentAuthError === null
    ? ""
    : translate(currentAuthError.key, currentAuthError.values);
}

function renderDashboard(view) {
  currentView = view;
  currentPageStatus = null;
  const statistics = view.statistics;
  const inventory = statistics.inventory;
  const recall = statistics.recall;
  authShell.hidden = true;
  pageStatus.hidden = true;
  dashboard.hidden = false;
  signOut.hidden = false;

  renderScopes(view.scopes, statistics.scope_id);
  setText("dashboard-name", view.selectedScope.display_name);
  setText("as-of", translate("updated", {value: formatDateTime(statistics.as_of)}));
  setText("sources", formatNumber(inventory.sources.total));
  setText("memory-entries", formatNumber(inventory.memory.entries.total));
  setText("artifacts", formatNumber(inventory.artifacts.total));
  setText("pending-reviews", formatNumber(inventory.candidates.pending));
  setText("token-reduction", formatCompact(recall.totals.token_reduction));

  setText("recall-hits", translate("recallHits", {
    hits: formatNumber(recall.totals.ready_preparations),
    preparations: formatNumber(recall.totals.preparations)
  }));

  renderArtifactFamilies(inventory);
  renderHeatmap(recall.daily);
  renderTrend(recall.daily);
}

function renderScopes(scopes, selectedScopeId) {
  scopeSelect.replaceChildren();
  for (const scope of scopes) {
    const option = document.createElement("option");
    option.value = scope.scope_id;
    option.textContent = `${scope.display_name} (${scope.scope_id})`;
    option.selected = scope.scope_id === selectedScopeId;
    scopeSelect.appendChild(option);
  }
}

function renderArtifactFamilies(inventory) {
  const rows = document.getElementById("family-rows");
  rows.replaceChildren();
  const families = new Map();
  for (const family of inventory.artifacts.by_family) {
    families.set(family.family, {family: family.family, total: family.total, pending: 0});
  }
  for (const candidate of inventory.candidates.by_family) {
    const family = families.get(candidate.family) || {family: candidate.family, total: 0, pending: 0};
    family.pending = candidate.pending;
    families.set(candidate.family, family);
  }
  for (const family of [...families.values()].sort((left, right) => left.family.localeCompare(right.family))) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const total = document.createElement("td");
    const pending = document.createElement("td");
    name.textContent = formatFamily(family.family);
    total.textContent = formatNumber(family.total);
    pending.textContent = formatNumber(family.pending);
    row.append(name, total, pending);
    rows.appendChild(row);
  }
}

function renderHeatmap(days) {
  const heatmap = document.getElementById("heatmap");
  const tooltip = document.getElementById("activity-tooltip");
  heatmap.replaceChildren();
  tooltip.hidden = true;
  let totalHits = 0;
  let totalSavings = 0;

  for (const day of days) {
    const hits = day.ready_preparations;
    const savings = day.token_reduction;
    totalHits += hits;
    totalSavings += savings;
    const cell = document.createElement("span");
    const level = heatmapLevel(hits, savings);
    cell.className = `activity-cell level-${level}`;
    const label = translate("activityHit", {
      date: formatDate(day.date),
      hits: formatNumber(hits),
      savings: formatNumber(savings)
    });
    cell.addEventListener("pointerenter", (event) => showTooltip(tooltip, event, label));
    cell.addEventListener("pointermove", (event) => positionTooltip(tooltip, event));
    cell.addEventListener("pointerleave", () => hideTooltip(tooltip));
    cell.setAttribute("aria-hidden", "true");
    heatmap.appendChild(cell);
  }

  heatmap.setAttribute("aria-label", translate("activityAria"));
  setText("activity-summary", translate("activitySummary", {
    hits: formatNumber(totalHits),
    savings: formatNumber(totalSavings)
  }));
}

function heatmapLevel(hits, savings) {
  if (hits === 0) {
    return 0;
  }
  if (savings <= 0) {
    return 1;
  }
  if (savings < 256) {
    return 2;
  }
  if (savings < 1024) {
    return 3;
  }
  return 4;
}

function renderTrend(days) {
  const chart = document.getElementById("trend-chart");
  const tooltip = document.getElementById("trend-tooltip");
  chart.replaceChildren();
  tooltip.hidden = true;
  const width = 720;
  const height = 220;
  const insetLeft = 50;
  const insetRight = 8;
  const insetY = 12;
  const plotHeight = height - insetY * 2;
  const reductions = days.map((day) => day.token_reduction);
  const observedMin = Math.min(0, ...reductions);
  const observedMax = Math.max(0, ...reductions);
  const minValue = observedMin;
  let maxValue = observedMax;
  if (minValue === maxValue) {
    maxValue = minValue + 1;
  }

  const gridValues = observedMin === observedMax
    ? [0]
    : [...new Set([observedMax, 0, observedMin])];
  for (const value of gridValues) {
    const line = document.createElementNS(svgNamespace, "line");
    const y = chartY(value, minValue, maxValue, plotHeight, insetY);
    line.setAttribute("x1", String(insetLeft));
    line.setAttribute("x2", String(width - insetRight));
    line.setAttribute("y1", String(y));
    line.setAttribute("y2", String(y));
    line.setAttribute("class", value === 0 ? "chart-grid chart-zero" : "chart-grid");
    chart.appendChild(line);

    const label = document.createElementNS(svgNamespace, "text");
    label.textContent = formatCompact(value);
    label.setAttribute("x", String(insetLeft - 8));
    label.setAttribute("y", String(y + 4));
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("text-anchor", "end");
    chart.appendChild(label);
  }

  chart.appendChild(series(
    days,
    "token_reduction",
    "chart-savings",
    minValue,
    maxValue,
    width,
    height,
    insetLeft,
    insetRight,
    insetY
  ));
  renderTrendPoints(days, tooltip, minValue, maxValue, width, height, insetLeft, insetRight, insetY, chart);

  setText("trend-start", formatShortDate(days[0].date));
  setText("trend-middle", formatShortDate(days[Math.floor(days.length / 2)].date));
  setText("trend-end", formatShortDate(days[days.length - 1].date));

  const hits = days.reduce((sum, day) => sum + day.ready_preparations, 0);
  const savings = days.reduce((sum, day) => sum + day.token_reduction, 0);
  setText("trend-description", translate("trendDescription", {
    hits: formatNumber(hits),
    savings: formatNumber(savings)
  }));
}

function series(days, field, className, minValue, maxValue, width, height, insetLeft, insetRight, insetY) {
  const line = document.createElementNS(svgNamespace, "polyline");
  const plotWidth = width - insetLeft - insetRight;
  const plotHeight = height - insetY * 2;
  const points = days.map((day, index) => {
    const x = insetLeft + plotWidth * index / Math.max(days.length - 1, 1);
    const y = chartY(day[field], minValue, maxValue, plotHeight, insetY);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  line.setAttribute("points", points.join(" "));
  line.setAttribute("class", className);
  return line;
}

function renderTrendPoints(
  days,
  tooltip,
  minValue,
  maxValue,
  width,
  height,
  insetLeft,
  insetRight,
  insetY,
  chart
) {
  const plotWidth = width - insetLeft - insetRight;
  const plotHeight = height - insetY * 2;
  days.forEach((day, index) => {
    const point = document.createElementNS(svgNamespace, "circle");
    const label = translate("activityHit", {
      date: formatDate(day.date),
      hits: formatNumber(day.ready_preparations),
      savings: formatNumber(day.token_reduction)
    });
    point.setAttribute("cx", String(insetLeft + plotWidth * index / Math.max(days.length - 1, 1)));
    point.setAttribute("cy", String(chartY(day.token_reduction, minValue, maxValue, plotHeight, insetY)));
    point.setAttribute("r", "7");
    point.setAttribute("class", "chart-point");
    point.addEventListener("pointerenter", (event) => showTooltip(tooltip, event, label));
    point.addEventListener("pointermove", (event) => positionTooltip(tooltip, event));
    point.addEventListener("pointerleave", () => hideTooltip(tooltip));
    chart.appendChild(point);
  });
}

function showTooltip(tooltip, event, label) {
  tooltip.textContent = label;
  tooltip.hidden = false;
  positionTooltip(tooltip, event);
}

function positionTooltip(tooltip, event) {
  const margin = 12;
  const offset = 14;
  const left = Math.min(event.clientX + offset, window.innerWidth - tooltip.offsetWidth - margin);
  const preferredTop = event.clientY - tooltip.offsetHeight - offset;
  const top = preferredTop >= margin ? preferredTop : event.clientY + offset;
  tooltip.style.left = `${Math.max(margin, left)}px`;
  tooltip.style.top = `${top}px`;
}

function hideTooltip(tooltip) {
  tooltip.hidden = true;
}

function chartY(value, minValue, maxValue, plotHeight, insetY) {
  return insetY + plotHeight * (maxValue - value) / (maxValue - minValue);
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatFamily(value) {
  if (translations[ui.locale()][value] || translations.en[value]) {
    return translate(value);
  }
  return value
    .split("_")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function formatCompact(value) {
  return new Intl.NumberFormat(ui.localeTag(), {notation: "compact", maximumFractionDigits: 1}).format(value);
}

function formatDate(value) {
  return new Intl.DateTimeFormat(ui.localeTag(), {dateStyle: "medium", timeZone: "UTC"}).format(new Date(`${value}T00:00:00Z`));
}

function formatShortDate(value) {
  return new Intl.DateTimeFormat(ui.localeTag(), {month: "short", day: "numeric", timeZone: "UTC"}).format(new Date(`${value}T00:00:00Z`));
}

ui.initialize();
authenticate(readServerToken());
