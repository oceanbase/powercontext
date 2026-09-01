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

"use strict";

import {
  clearServerToken,
  fetchWithBearer,
  readServerToken,
  storeServerToken
} from "./auth.js?v=optional-auth";
import {createPageUi, createRequestGate} from "./page-ui.js?v=locale-complete";

const translations = {
  en: {
    pageTitle: "PowerContext Overview",
    dashboardTitle: "Overview",
    skillsTitle: "Skills",
    reviewTitle: "Review",
    handoffReportTitle: "Handoff Report",
    brandHomeLabel: "PowerContext Overview",
    primaryNavigation: "Primary navigation",
    maintainedBy: "Maintained by OceanBase.",
    signOut: "Sign out",
    authTitle: "Connect to PowerContext",
    authIntro: "Enter the bearer token configured for this PowerContext Server. The token stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    selectScope: "View",
    estimatedReduction: "Compared with using the original materials directly",
    sources: "Work materials",
    memoryEntries: "Memory",
    artifacts: "Saved content",
    pendingReview: "Awaiting review",
    artifactFamilies: "By type",
    family: "Type",
    currentArtifacts: "Saved",
    pendingCandidates: "Awaiting review",
    experience: "Experience",
    handoff: "Handoff",
    memory: "Memory",
    skill: "Skill",
    other: "Other",
    noContent: "No content yet",
    dailyActivity: "Use over the last 30 days",
    noActivity: "No use",
    moreActivity: "More use",
    recallTrend: "Tokens saved or used each day",
    dark: "Dark",
    light: "Light",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "中文",
    languageEnglish: "EN",
    updated: "As of {value}",
    recallCoverage: "In the last 30 days, {comparable} of {preparations} uses could be compared.",
    noPreparations: "No use in the last 30 days.",
    tokensSaved: "Saved about {tokens} tokens",
    tokensAdded: "Used about {tokens} more tokens",
    tokensUnchanged: "Token use was about the same",
    noComparison: "Not enough data to compare",
    activitySummary: "Used {preparations} times in the last 30 days; content was found {hits} times.",
    activityUnavailable: "Usage data is not available.",
    activityAria: "Context use over the last 30 days",
    activityHit: "{date}: used {preparations} times; content found {hits} times",
    trendHit: "{date}: {comparable} of {preparations} uses could be compared; {comparison}",
    trendDescription: "In the last 30 days, {comparable} of {preparations} uses could be compared. {comparison}.",
    axisSaved: "Saved {tokens}",
    axisAdded: "Used {tokens}",
    authRejected: "The Server rejected this token.",
    requestFailed: "Couldn't load the Overview. Try again.",
    serverUnavailable: "The Server is unavailable.",
    retry: "Retry",
    noScopes: "There is no work to show here.",
    scopeUnavailable: "The selected work is not available.",
    scopeOverview: "Overview for the selected work"
  },
  zh: {
    pageTitle: "PowerContext 概览",
    dashboardTitle: "概览",
    skillsTitle: "技能",
    reviewTitle: "审核",
    handoffReportTitle: "交接报告",
    brandHomeLabel: "PowerContext 概览",
    primaryNavigation: "主导航",
    maintainedBy: "由 OceanBase 维护。",
    signOut: "退出",
    authTitle: "连接 PowerContext",
    authIntro: "请输入 PowerContext 服务器配置的访问令牌。令牌仅保留在当前浏览器标签页。",
    tokenLabel: "服务器访问令牌",
    continue: "继续",
    selectScope: "查看",
    estimatedReduction: "相比直接使用原始材料",
    sources: "工作材料",
    memoryEntries: "记忆",
    artifacts: "已保存内容",
    pendingReview: "待审核",
    artifactFamilies: "按类型查看",
    family: "类型",
    currentArtifacts: "已保存",
    pendingCandidates: "待审核",
    experience: "经验",
    handoff: "交接",
    memory: "记忆",
    skill: "技能",
    other: "其他",
    noContent: "暂无内容",
    dailyActivity: "过去 30 天的使用情况",
    noActivity: "未使用",
    moreActivity: "使用更多",
    recallTrend: "每天节省或多用的 Token",
    dark: "深色",
    light: "浅色",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    languageEnglish: "EN",
    updated: "截至 {value}",
    recallCoverage: "过去 30 天共使用 {preparations} 次，其中 {comparable} 次可以比较。",
    noPreparations: "过去 30 天暂无使用记录。",
    tokensSaved: "节省约 {tokens} Token",
    tokensAdded: "多用约 {tokens} Token",
    tokensUnchanged: "Token 用量基本相同",
    noComparison: "暂时无法比较",
    activitySummary: "过去 30 天共使用 {preparations} 次，其中 {hits} 次找到内容。",
    activityUnavailable: "暂无使用数据。",
    activityAria: "过去 30 天的上下文使用情况",
    activityHit: "{date}：使用 {preparations} 次，找到内容 {hits} 次",
    trendHit: "{date}：共使用 {preparations} 次，其中 {comparable} 次可以比较，{comparison}",
    trendDescription: "过去 30 天共使用 {preparations} 次，其中 {comparable} 次可以比较。{comparison}。",
    axisSaved: "节省 {tokens}",
    axisAdded: "多用 {tokens}",
    authRejected: "服务器拒绝了该访问令牌。",
    requestFailed: "无法加载概览，请重试。",
    serverUnavailable: "无法连接服务器。",
    retry: "重试",
    noScopes: "这里还没有可查看的工作。",
    scopeUnavailable: "无法查看这项工作。",
    scopeOverview: "所选工作的概览"
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
const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const svgNamespace = "http://www.w3.org/2000/svg";
const productArtifactFamilies = new Set(["experience", "handoff", "skill"]);
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
  if (authenticationRequired && !token) {
    showLogin();
    return;
  }

  if (authenticationRequired) {
    storeServerToken(token);
  }
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
      showPageStatus("requestFailed", {}, true);
      return;
    }
    currentScopes = await response.json();
    if (!request.isCurrent()) {
      return;
    }
    if (currentScopes.length === 0) {
      showPageStatus("noScopes");
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
  if (authenticationRequired && !token) {
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
      showPageStatus("requestFailed", {}, true);
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
  signOut.hidden = !authenticationRequired;
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
  const comparisonAvailable = recall.estimator !== null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  dashboard.hidden = false;
  signOut.hidden = !authenticationRequired;

  renderScopes(view.scopes, statistics.scope_id);
  setText("dashboard-name", view.selectedScope.display_name);
  setText("as-of", translate("updated", {value: formatDateTime(statistics.as_of)}));
  setText("sources", formatNumber(inventory.sources.total));
  setText("memory-entries", formatNumber(inventory.memory.entries.active));
  setText("artifacts", formatNumber(savedArtifactCount(inventory)));
  setText("pending-reviews", formatNumber(inventory.candidates.pending));
  setText("token-reduction", !comparisonAvailable || recall.totals.comparable_preparations === 0
    ? translate("noComparison")
    : formatTokenComparison(recall.totals.token_reduction));
  setText("recall-hits", formatRecallCoverage(recall.totals, comparisonAvailable));

  renderArtifactFamilies(inventory);
  renderHeatmap(recall.daily, comparisonAvailable);
  renderTrend(recall.daily, comparisonAvailable);
}

function renderScopes(scopes, selectedScopeId) {
  scopeSelect.replaceChildren();
  for (const scope of scopes) {
    const option = document.createElement("option");
    option.value = scope.scope_id;
    option.textContent = scope.display_name;
    option.selected = scope.scope_id === selectedScopeId;
    scopeSelect.appendChild(option);
  }
}

function renderArtifactFamilies(inventory) {
  const rows = document.getElementById("family-rows");
  rows.replaceChildren();
  const families = new Map();
  for (const family of inventory.artifacts.by_family) {
    const displayFamily = productArtifactFamily(family.family);
    if (displayFamily === null) {
      continue;
    }
    const current = families.get(displayFamily) || {family: displayFamily, total: 0, pending: 0};
    current.total += family.total;
    families.set(displayFamily, current);
  }
  for (const candidate of inventory.candidates.by_family) {
    const displayFamily = productArtifactFamily(candidate.family);
    if (displayFamily === null) {
      continue;
    }
    const family = families.get(displayFamily) || {family: displayFamily, total: 0, pending: 0};
    family.pending += candidate.pending;
    families.set(displayFamily, family);
  }
  if (families.size === 0) {
    const row = document.createElement("tr");
    const empty = document.createElement("td");
    empty.colSpan = 3;
    empty.textContent = translate("noContent");
    row.appendChild(empty);
    rows.appendChild(row);
    return;
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

function savedArtifactCount(inventory) {
  return inventory.artifacts.by_family.reduce(
    (total, family) => total + (family.family === "memory" ? 0 : family.total),
    0
  );
}

function productArtifactFamily(family) {
  if (family === "memory") {
    return null;
  }
  return productArtifactFamilies.has(family) ? family : "other";
}

function renderHeatmap(days, dataAvailable) {
  const heatmap = document.getElementById("heatmap");
  const tooltip = document.getElementById("activity-tooltip");
  const chart = document.getElementById("activity-chart");
  const legend = document.getElementById("activity-legend");
  heatmap.replaceChildren();
  tooltip.hidden = true;
  if (!dataAvailable) {
    chart.hidden = true;
    legend.hidden = true;
    setText("activity-summary", translate("activityUnavailable"));
    return;
  }
  chart.hidden = false;
  legend.hidden = false;
  let totalPreparations = 0;
  let totalHits = 0;

  for (const day of days) {
    const preparations = day.preparations;
    const hits = day.ready_preparations;
    totalPreparations += preparations;
    totalHits += hits;
    const cell = document.createElement("span");
    const level = heatmapLevel(preparations);
    cell.className = `activity-cell level-${level}`;
    const label = translate("activityHit", {
      date: formatDate(day.date),
      preparations: formatNumber(preparations),
      hits: formatNumber(hits)
    });
    cell.addEventListener("pointerenter", (event) => showTooltip(tooltip, event, label));
    cell.addEventListener("pointermove", (event) => positionTooltip(tooltip, event));
    cell.addEventListener("pointerleave", () => hideTooltip(tooltip));
    cell.setAttribute("aria-hidden", "true");
    heatmap.appendChild(cell);
  }

  heatmap.setAttribute("aria-label", translate("activityAria"));
  setText("activity-summary", totalPreparations === 0
    ? translate("noPreparations")
    : translate("activitySummary", {
        preparations: formatNumber(totalPreparations),
        hits: formatNumber(totalHits)
      }));
}

function heatmapLevel(preparations) {
  return Math.min(preparations, 4);
}

function renderTrend(days, dataAvailable) {
  const chart = document.getElementById("trend-chart");
  const tooltip = document.getElementById("trend-tooltip");
  const empty = document.getElementById("trend-empty");
  const wrap = document.getElementById("trend-wrap");
  chart.replaceChildren();
  tooltip.hidden = true;
  const comparableDays = days.filter((day) => day.comparable_preparations > 0);
  if (!dataAvailable || comparableDays.length === 0) {
    const message = translate("noComparison");
    empty.textContent = message;
    empty.hidden = false;
    wrap.hidden = true;
    setText("trend-description", message);
    return;
  }
  empty.hidden = true;
  wrap.hidden = false;
  const width = 720;
  const height = 220;
  const insetLeft = 104;
  const insetRight = 8;
  const insetY = 12;
  const plotHeight = height - insetY * 2;
  const reductions = comparableDays.map((day) => day.token_reduction);
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
    label.textContent = formatTokenAxis(value);
    label.setAttribute("x", String(insetLeft - 8));
    label.setAttribute("y", String(y + 4));
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("text-anchor", "end");
    chart.appendChild(label);
  }

  chart.appendChild(series(
    days,
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

  const preparations = days.reduce((sum, day) => sum + day.preparations, 0);
  const comparisons = days.reduce((sum, day) => sum + day.comparable_preparations, 0);
  const savings = days.reduce((sum, day) => sum + day.token_reduction, 0);
  setText("trend-description", translate("trendDescription", {
    preparations: formatNumber(preparations),
    comparable: formatNumber(comparisons),
    comparison: formatTokenComparison(savings)
  }));
}

function series(days, minValue, maxValue, width, height, insetLeft, insetRight, insetY) {
  const line = document.createElementNS(svgNamespace, "path");
  const plotWidth = width - insetLeft - insetRight;
  const plotHeight = height - insetY * 2;
  let drawing = false;
  const commands = [];
  days.forEach((day, index) => {
    if (day.comparable_preparations === 0) {
      drawing = false;
      return;
    }
    const x = insetLeft + plotWidth * index / Math.max(days.length - 1, 1);
    const y = chartY(day.token_reduction, minValue, maxValue, plotHeight, insetY);
    commands.push(`${drawing ? "L" : "M"} ${x.toFixed(2)} ${y.toFixed(2)}`);
    drawing = true;
  });
  line.setAttribute("d", commands.join(" "));
  line.setAttribute("class", "chart-savings");
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
    if (day.comparable_preparations === 0) {
      return;
    }
    const point = document.createElementNS(svgNamespace, "circle");
    const label = translate("trendHit", {
      date: formatDate(day.date),
      preparations: formatNumber(day.preparations),
      comparable: formatNumber(day.comparable_preparations),
      comparison: formatTokenComparison(day.token_reduction)
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
  return translate(value);
}

function formatCompact(value) {
  return new Intl.NumberFormat(ui.localeTag(), {notation: "compact", maximumFractionDigits: 1}).format(value);
}

function formatRecallCoverage(totals, dataAvailable) {
  if (!dataAvailable) {
    return "";
  }
  if (totals.preparations === 0) {
    return translate("noPreparations");
  }
  return translate("recallCoverage", {
    preparations: formatNumber(totals.preparations),
    comparable: formatNumber(totals.comparable_preparations)
  });
}

function formatTokenComparison(value) {
  if (value > 0) {
    return translate("tokensSaved", {tokens: formatCompact(value)});
  }
  if (value < 0) {
    return translate("tokensAdded", {tokens: formatCompact(Math.abs(value))});
  }
  return translate("tokensUnchanged");
}

function formatTokenAxis(value) {
  if (value > 0) {
    return translate("axisSaved", {tokens: formatCompact(value)});
  }
  if (value < 0) {
    return translate("axisAdded", {tokens: formatCompact(Math.abs(value))});
  }
  return "0";
}

function formatDate(value) {
  return new Intl.DateTimeFormat(ui.localeTag(), {dateStyle: "medium", timeZone: "UTC"}).format(new Date(`${value}T00:00:00Z`));
}

function formatShortDate(value) {
  return new Intl.DateTimeFormat(ui.localeTag(), {month: "short", day: "numeric", timeZone: "UTC"}).format(new Date(`${value}T00:00:00Z`));
}

ui.initialize();
authenticate(readServerToken());
