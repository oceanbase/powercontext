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
    period30: "Last 30 days",
    estimatedReduction: "Estimated change in context length",
    sources: "Recorded materials",
    memoryEntries: "Memory",
    artifacts: "Saved content",
    pendingReview: "Awaiting review",
    artifactFamilies: "Saved content",
    artifactSubtitle: "What is available now and what still needs review",
    family: "Type",
    currentArtifacts: "Available",
    pendingCandidates: "Awaiting review",
    experience: "Experience",
    handoff: "Handoff",
    memory: "Memory",
    skill: "Skill",
    dailyActivity: "Daily context use",
    noRecall: "No context",
    hitNoReduction: "Context · no reduction",
    reductionLow: "1-255 fewer",
    reductionMedium: "256-1,023 fewer",
    reductionHigh: "1,024+ fewer",
    recallTrend: "Context length comparison",
    trendSubtitle: "Positive values mean fewer tokens; negative values mean more.",
    estimatedReductionSeries: "Estimated fewer tokens",
    dark: "Dark",
    light: "Light",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "中文",
    languageEnglish: "EN",
    updated: "Updated {value}",
    recallHits: "{hits} of {preparations} preparations returned context. Positive values mean fewer tokens.",
    activitySummary: "Context returned {hits} times in the last 30 days. Estimated difference: {savings} tokens. Positive means fewer.",
    activityAria: "30 days of returned context and estimated change in length",
    activityHit: "{date}: context returned {hits} times · estimated difference {savings} tokens",
    trendDescription: "Context returned {hits} times. Estimated difference: {savings} tokens. Positive values mean fewer tokens.",
    authRejected: "The Server rejected this token.",
    requestFailed: "The Overview request failed with HTTP {status}.",
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
    period30: "过去 30 天",
    estimatedReduction: "预估上下文长度变化",
    sources: "已记录材料",
    memoryEntries: "记忆",
    artifacts: "已保存内容",
    pendingReview: "待审核",
    artifactFamilies: "已保存内容",
    artifactSubtitle: "查看当前可用内容和待审核内容",
    family: "类型",
    currentArtifacts: "当前可用",
    pendingCandidates: "待审核",
    experience: "经验",
    handoff: "交接",
    memory: "记忆",
    skill: "技能",
    dailyActivity: "每日上下文使用",
    noRecall: "未返回上下文",
    hitNoReduction: "已返回 · 未减少",
    reductionLow: "少 1-255 Token",
    reductionMedium: "少 256-1,023 Token",
    reductionHigh: "少 1,024+ Token",
    recallTrend: "上下文长度对比",
    trendSubtitle: "正数表示少带入 Token，负数表示多带入。",
    estimatedReductionSeries: "预计少带入的 Token",
    dark: "深色",
    light: "浅色",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    languageEnglish: "EN",
    updated: "更新于 {value}",
    recallHits: "{preparations} 次上下文准备中有 {hits} 次返回内容。正数表示少带入 Token。",
    activitySummary: "过去 30 天有 {hits} 次返回内容。预估差值为 {savings} Token，正数表示少带入。",
    activityAria: "过去 30 天返回上下文的次数和预估长度变化",
    activityHit: "{date}：返回内容 {hits} 次 · 预估差值 {savings} Token",
    trendDescription: "返回内容 {hits} 次。预估差值为 {savings} Token，正数表示少带入。",
    authRejected: "服务器拒绝了该访问令牌。",
    requestFailed: "概览请求失败（HTTP {status}）。",
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
  authShell.hidden = true;
  pageStatus.hidden = true;
  dashboard.hidden = false;
  signOut.hidden = !authenticationRequired;

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
