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
import {buildScopeSelectionChoices} from "./scope-selection.js?v=selection-v1";

const translations = {
  en: {
    pageTitle: "PowerContext Handoff Report",
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
    refresh: "Refresh",
    downloadMarkdown: "Download Markdown",
    scopeHandoffState: "Scope Handoff state",
    reportDescription: "An exact Handoff projection over the selected Scope view.",
    scopeView: "Scope view",
    scopeViewDescription: "All, subtree, and exact use the same selection semantics as Dashboard.",
    selectScope: "Scope",
    allScopes: "All",
    subtreeView: "{title} and descendants",
    exactFocus: "Focus: {title}",
    handoffSummary: "Handoff summary",
    continuable: "Continuable",
    blocked: "Blocked",
    complete: "Complete",
    noHandoff: "No Handoff",
    no_handoff: "No Handoff",
    selectedScopes: "Selected Scopes",
    selectedScopesDescription: "Parent describes organization; each row reports only that Scope's exact latest Handoff.",
    scope: "Scope",
    parent: "Parent",
    status: "Status",
    objective: "Objective",
    nextAction: "Next action",
    exactRevision: "Exact Revision",
    metadata: "Report metadata",
    generatedAt: "Generated at",
    selectionDigest: "Selection digest",
    reportDigest: "Report digest",
    noScopes: "No Scopes are available.",
    requestFailed: "The Handoff Report request failed with HTTP {status}.",
    serverUnavailable: "The Server is unavailable.",
    authRejected: "The Server rejected this token.",
    retry: "Retry",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "中文",
    languageEnglish: "EN"
  },
  zh: {
    pageTitle: "PowerContext 交接报告",
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
    refresh: "刷新",
    downloadMarkdown: "下载 Markdown",
    scopeHandoffState: "Scope 交接状态",
    reportDescription: "按所选 Scope 视图汇总各 Scope 的精确交接状态。",
    scopeView: "Scope 视图",
    scopeViewDescription: "全部、下级范围和精确聚焦与仪表盘使用相同的选择语义。",
    selectScope: "作用域",
    allScopes: "全部",
    subtreeView: "{title}及其下级",
    exactFocus: "聚焦：{title}",
    handoffSummary: "交接摘要",
    continuable: "可继续",
    blocked: "阻塞",
    complete: "已完成",
    noHandoff: "无交接",
    no_handoff: "无交接",
    selectedScopes: "所选 Scope",
    selectedScopesDescription: "Parent 只表达组织关系；每一行只报告该 Scope 自身最新的精确 Handoff。",
    scope: "Scope",
    parent: "上级",
    status: "状态",
    objective: "目标",
    nextAction: "下一步",
    exactRevision: "精确版本",
    metadata: "报告元数据",
    generatedAt: "生成时间",
    selectionDigest: "选择摘要",
    reportDigest: "报告摘要",
    noScopes: "当前没有可用 Scope。",
    requestFailed: "交接报告请求失败（HTTP {status}）。",
    serverUnavailable: "服务器无法访问。",
    authRejected: "服务器拒绝了该访问令牌。",
    retry: "重试",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    languageEnglish: "EN"
  }
};

const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const authShell = document.getElementById("auth-shell");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const tokenInput = document.getElementById("token");
const pageStatus = document.getElementById("page-status");
const pageStatusMessage = document.getElementById("page-status-message");
const pageStatusRetry = document.getElementById("page-status-retry");
const reportShell = document.getElementById("handoff-report");
const signOut = document.getElementById("sign-out");
const scopeSelect = document.getElementById("scope-select");
const refreshButton = document.getElementById("refresh-report");
const downloadButton = document.getElementById("download-report");
const requests = createRequestGate();
let scopes = [];
let selectedKey = "all";
let currentReport = null;
let currentStatus = null;

const ui = createPageUi(translations, () => {
  renderChoices();
  if (currentReport !== null) {
    renderReport(currentReport);
  }
  renderStatus();
});
const {formatDateTime, translate} = ui;

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await authenticate(tokenInput.value);
});
signOut.addEventListener("click", () => {
  clearServerToken();
  showLogin();
});
scopeSelect.addEventListener("change", async () => {
  selectedKey = scopeSelect.value;
  await loadReport(readServerToken());
});
refreshButton.addEventListener("click", async () => loadReport(readServerToken()));
pageStatusRetry.addEventListener("click", async () => authenticate(readServerToken()));
downloadButton.addEventListener("click", async () => downloadMarkdown(readServerToken()));

async function authenticate(token) {
  if (authenticationRequired && !token) {
    showLogin();
    return;
  }
  if (authenticationRequired) {
    storeServerToken(token);
  }
  tokenInput.value = "";
  const request = requests.start();
  try {
    const response = await fetchWithBearer("/dashboard/scopes", token);
    if (!request.isCurrent()) return;
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      showStatus("requestFailed", {status: response.status});
      return;
    }
    scopes = await response.json();
    if (scopes.length === 0) {
      showStatus("noScopes");
      return;
    }
    const choices = buildScopeSelectionChoices(scopes, translate);
    if (!choices.some((choice) => choice.key === selectedKey)) selectedKey = "all";
    renderChoices();
    await loadReport(token, request);
  } catch (error) {
    if (request.isCurrent()) showStatus("serverUnavailable");
  }
}

async function loadReport(token, request = requests.start()) {
  const choice = selectedChoice();
  if (choice === null) return;
  setBusy(true);
  try {
    const response = await fetchWithBearer("/v1/handoff-reports/get", token, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({selection: choice.selection, format: "json"})
    });
    if (!request.isCurrent()) return;
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      showStatus("requestFailed", {status: response.status});
      return;
    }
    const payload = await response.json();
    currentReport = payload.report;
    currentStatus = null;
    authShell.hidden = true;
    pageStatus.hidden = true;
    reportShell.hidden = false;
    signOut.hidden = !authenticationRequired;
    renderReport(currentReport);
  } catch (error) {
    if (request.isCurrent()) showStatus("serverUnavailable");
  } finally {
    if (request.isCurrent()) setBusy(false);
  }
}

async function downloadMarkdown(token) {
  const choice = selectedChoice();
  if (choice === null) return;
  setBusy(true);
  try {
    const response = await fetchWithBearer("/v1/handoff-reports/get", token, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({selection: choice.selection, format: "markdown", download: true})
    });
    if (!response.ok) {
      showStatus("requestFailed", {status: response.status});
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await response.blob());
    link.download = "handoff-report.md";
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    showStatus("serverUnavailable");
  } finally {
    setBusy(false);
  }
}

function selectedChoice() {
  return buildScopeSelectionChoices(scopes, translate).find((choice) => choice.key === selectedKey) || null;
}

function renderChoices() {
  if (scopeSelect === null) return;
  scopeSelect.replaceChildren();
  for (const choice of buildScopeSelectionChoices(scopes, translate)) {
    const option = document.createElement("option");
    option.value = choice.key;
    option.textContent = choice.label;
    option.selected = choice.key === selectedKey;
    scopeSelect.appendChild(option);
  }
}

function renderReport(report) {
  setText("continuable-count", report.summary.continuable_count);
  setText("blocked-count", report.summary.blocked_count);
  setText("complete-count", report.summary.complete_count);
  setText("no-handoff-count", report.summary.no_handoff_count);
  setText("generated-at", formatDateTime(report.generated_at));
  setText("selection-digest", report.selection_digest);
  setText("report-digest", report.report_digest);
  const rows = document.getElementById("scope-report-rows");
  rows.replaceChildren();
  for (const entry of report.scopes) {
    const row = document.createElement("tr");
    appendCell(row, entry.scope.title, entry.scope.scope_id);
    appendCell(row, entry.scope.parent_scope_id || "—");
    appendCell(row, translate(entry.status));
    appendCell(row, entry.content?.objective || "—");
    appendCell(row, entry.content?.next_action?.text || "—");
    appendCell(row, formatAddress(entry.handoff), null, true);
    rows.appendChild(row);
  }
}

function appendCell(row, value, detail = null, code = false) {
  const cell = document.createElement("td");
  const primary = document.createElement(code ? "code" : "span");
  primary.textContent = value;
  cell.appendChild(primary);
  if (detail !== null) {
    const secondary = document.createElement("code");
    secondary.textContent = detail;
    cell.appendChild(document.createElement("br"));
    cell.appendChild(secondary);
  }
  row.appendChild(cell);
}

function formatAddress(address) {
  if (address === null) return "—";
  const artifact = address.artifact;
  return `${address.scope_id}/${artifact.family}/${artifact.artifact_id}@${artifact.revision}`;
}

function showLogin(messageKey = "") {
  requests.cancel();
  currentReport = null;
  reportShell.hidden = true;
  pageStatus.hidden = true;
  authShell.hidden = false;
  signOut.hidden = true;
  authError.textContent = messageKey ? translate(messageKey) : "";
  tokenInput.focus();
}

function showStatus(key, values = {}) {
  currentStatus = {key, values};
  currentReport = null;
  authShell.hidden = true;
  reportShell.hidden = true;
  pageStatus.hidden = false;
  pageStatusRetry.hidden = false;
  renderStatus();
}

function renderStatus() {
  if (currentStatus !== null) {
    pageStatusMessage.textContent = translate(currentStatus.key, currentStatus.values);
  }
}

function setBusy(busy) {
  scopeSelect.disabled = busy;
  refreshButton.disabled = busy;
  downloadButton.disabled = busy;
}

function setText(id, value) {
  document.getElementById(id).textContent = String(value);
}

ui.initialize();
authenticate(readServerToken());
