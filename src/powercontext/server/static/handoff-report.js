"use strict";

import {
  clearServerToken,
  fetchWithBearer,
  readServerToken,
  storeServerToken
} from "./auth.js?v=session-shell";
import {formatDateRange, resolvePeriodSelection, validateDateRange} from "./handoff-period.js";
import {createPageUi, createRequestGate} from "./page-ui.js?v=request-gate";

const selectedProjectKey = "powercontext.handoff-report.project";
const translations = {
  en: {
    pageTitle: "PowerContext Handoff Report",
    dashboardTitle: "Dashboard",
    handoffReportTitle: "Handoff Report",
    maintainedBy: "Maintained by OceanBase.",
    signOut: "Sign out",
    authTitle: "Connect to PowerContext",
    authIntro: "Enter the bearer token configured for this PowerContext Server. The token stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    refresh: "Refresh",
    downloadMarkdown: "Download Markdown",
    projects: "Projects",
    reportPeriod: "Report period",
    day: "Day",
    week: "Week",
    month: "Month",
    custom: "Custom",
    periodStart: "Start date",
    periodEnd: "End date",
    apply: "Apply",
    periodSummary: "{preset} · {range} · {timezone}",
    periodComparison: "Activity: {current} current / {previous} previous / {delta} change",
    periodBoundaryUnavailable: "Handoff status uses the current exact selection; this period filters Activity but cannot reconstruct historical Handoff boundaries.",
    periodDatesRequired: "Select both a start date and an end date.",
    periodInvalidRange: "The start date must not be later than the end date.",
    continuable: "Continuable",
    blocked: "Blocked",
    complete: "Complete",
    noHandoff: "No Handoff",
    coverage: "Report coverage",
    workstreams: "Workstreams",
    activities: "Activities",
    evidenceUnavailable: "Evidence unavailable",
    blockers: "Blockers",
    blockersSubtitle: "Workstreams that cannot be continued without intervention",
    workstreamsSubtitle: "Exact Handoff state and the next action for each scope",
    workstream: "Workstream",
    status: "Status",
    reporting: "Reporting",
    nextAction: "Next action",
    noWorkstreams: "No Workstreams are registered for this Project.",
    handoffContents: "Handoff content",
    handoffContentsSubtitle: "Objective, current state, next action, and known omissions for continuation",
    objective: "Objective",
    currentState: "Current state",
    omissions: "Known omissions",
    noOmissions: "No known omissions were declared.",
    noCommittedHandoff: "No committed Handoff is available for this Workstream.",
    metadata: "Report metadata",
    selectionConsistency: "Selection consistency",
    activityCoverage: "Activity coverage",
    selectionDigest: "Selection digest",
    reportDigest: "Report digest",
    dark: "Dark",
    light: "Light",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "中文",
    updated: "Updated {value}",
    projectOption: "{title} ({projectId})",
    coverageCaptured: "Captured Activity is included through cursor {cursor}. Counts describe observed events, not completion percentage.",
    coverageNotConfigured: "Activity adapters are not configured. Missing Activity must not be read as no work occurring.",
    coverageUnavailable: "Activity coverage is unavailable for this report.",
    noProjects: "No Handoff Report Projects are configured.",
    authRejected: "The Server rejected this token.",
    requestFailed: "The Handoff Report request failed with HTTP {status}.",
    serverUnavailable: "The Server is unavailable.",
    retry: "Retry",
    reportUnavailable: "The selected Project report is unavailable.",
    downloadFailed: "The Markdown download failed with HTTP {status}.",
    reported: "Reported",
    reported_with_omissions: "Reported with omissions",
    evidence_unavailable: "Evidence unavailable",
    no_handoff: "No Handoff",
    activity_after_handoff: "Activity after Handoff",
    activity_without_handoff: "Activity without Handoff",
    no_observed_activity: "No observed Activity",
    current_only: "Current only",
    unknown: "Unknown"
  },
  zh: {
    pageTitle: "PowerContext 项目交接报告",
    dashboardTitle: "仪表盘",
    handoffReportTitle: "交接报告",
    maintainedBy: "由 OceanBase 维护。",
    signOut: "退出",
    authTitle: "连接 PowerContext",
    authIntro: "请输入 PowerContext Server 配置的 bearer token。Token 仅保留在当前浏览器标签页。",
    tokenLabel: "服务器 Token",
    continue: "继续",
    refresh: "刷新",
    downloadMarkdown: "下载 Markdown",
    projects: "项目",
    reportPeriod: "报告周期",
    day: "日",
    week: "周",
    month: "月",
    custom: "自定义",
    periodStart: "开始日期",
    periodEnd: "结束日期",
    apply: "应用",
    periodSummary: "{preset} · {range} · {timezone}",
    periodComparison: "Activity：本期 {current} / 上期 {previous} / 变化 {delta}",
    periodBoundaryUnavailable: "Handoff 状态采用当前 exact selection；该周期只筛选 Activity，不能还原历史 Handoff 边界。",
    periodDatesRequired: "请选择开始日期和结束日期。",
    periodInvalidRange: "开始日期不能晚于结束日期。",
    continuable: "可继续",
    blocked: "阻塞",
    complete: "已完成",
    noHandoff: "无 Handoff",
    coverage: "报告覆盖范围",
    workstreams: "Workstreams",
    activities: "Activity",
    evidenceUnavailable: "Evidence 不可用",
    blockers: "阻塞事项",
    blockersSubtitle: "需要人工处理后才能继续的 Workstream",
    workstreamsSubtitle: "每个 Scope 的精确 Handoff 状态与下一步",
    workstream: "Workstream",
    status: "状态",
    reporting: "汇报状态",
    nextAction: "下一步",
    noWorkstreams: "该 Project 尚未登记 Workstream。",
    handoffContents: "Handoff 内容",
    handoffContentsSubtitle: "用于继续工作的目标、当前状态、下一步和已知缺失",
    objective: "目标",
    currentState: "当前状态",
    omissions: "已知缺失",
    noOmissions: "未声明已知缺失。",
    noCommittedHandoff: "该 Workstream 尚无已提交的 Handoff。",
    metadata: "报告元数据",
    selectionConsistency: "Selection 一致性",
    activityCoverage: "Activity 覆盖",
    selectionDigest: "Selection Digest",
    reportDigest: "Report Digest",
    dark: "深色",
    light: "浅色",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    updated: "更新于 {value}",
    projectOption: "{title}（{projectId}）",
    coverageCaptured: "已纳入游标 {cursor} 之前捕获的 Activity。数量表示已观察事件，不代表完成百分比。",
    coverageNotConfigured: "Activity Adapter 尚未配置；缺少 Activity 不能解释为没有发生工作。",
    coverageUnavailable: "当前报告无法取得 Activity 覆盖信息。",
    noProjects: "尚未配置 Handoff Report Project。",
    authRejected: "Server 拒绝了该 Token。",
    requestFailed: "Handoff Report 请求失败（HTTP {status}）。",
    serverUnavailable: "Server 无法访问。",
    retry: "重试",
    reportUnavailable: "当前 Project 的交接报告不可用。",
    downloadFailed: "Markdown 下载失败（HTTP {status}）。",
    reported: "已汇报",
    reported_with_omissions: "已汇报但有缺失",
    evidence_unavailable: "Evidence 不可用",
    no_handoff: "无 Handoff",
    activity_after_handoff: "Handoff 后有 Activity",
    activity_without_handoff: "有 Activity 但无 Handoff",
    no_observed_activity: "未观察到 Activity",
    current_only: "仅当前状态",
    unknown: "未知"
  }
};

const authShell = document.getElementById("auth-shell");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const tokenInput = document.getElementById("token");
const pageStatus = document.getElementById("page-status");
const pageStatusMessage = document.getElementById("page-status-message");
const pageStatusRetry = document.getElementById("page-status-retry");
const reportShell = document.getElementById("handoff-report");
const reportError = document.getElementById("report-error");
const projectSelect = document.getElementById("project-select");
const refreshButton = document.getElementById("refresh-report");
const downloadButton = document.getElementById("download-report");
const periodButtons = Array.from(document.querySelectorAll("[data-period-mode]"));
const customPeriodForm = document.getElementById("custom-period-form");
const periodStartInput = document.getElementById("period-start");
const periodEndInput = document.getElementById("period-end");
const applyCustomPeriodButton = document.getElementById("apply-custom-period");
const periodError = document.getElementById("period-error");
const signOut = document.getElementById("sign-out");
let currentProjects = [];
let currentProject = null;
let currentReport = null;
let currentAuthError = null;
let currentPageStatus = null;
let currentPeriodMode = "day";
let currentPeriodSelection = null;
let appliedCustomRange = null;
const ui = createPageUi(translations, () => {
  renderAuthError();
  renderPageStatus();
  if (currentProject !== null) {
    renderProjectOptions(currentProjects, currentProject.project_id);
  }
  if (currentReport !== null) {
    renderReport(currentReport);
  } else {
    renderPeriodControls();
  }
});
const {formatDateTime, formatNumber, translate} = ui;
const reportRequests = createRequestGate();

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await authenticate(tokenInput.value);
});

signOut.addEventListener("click", () => {
  clearServerToken();
  tokenInput.value = "";
  showLogin();
});

pageStatusRetry.addEventListener("click", async () => {
  const token = readServerToken();
  if (currentProject === null) {
    await authenticate(token);
  } else {
    await loadReport(token, currentProject.project_id);
  }
});

refreshButton.addEventListener("click", async () => {
  if (currentProject !== null) {
    await loadReport(readServerToken(), currentProject.project_id);
  }
});

downloadButton.addEventListener("click", async () => {
  await downloadMarkdown();
});

projectSelect.addEventListener("change", async () => {
  if (projectSelect.value !== currentProject?.project_id) {
    await loadReport(readServerToken(), projectSelect.value);
  }
});

for (const button of periodButtons) {
  button.addEventListener("click", async () => {
    currentPeriodMode = button.dataset.periodMode;
    clearPeriodError();
    if (currentProject !== null) {
      await loadReport(readServerToken(), currentProject.project_id);
    }
  });
}

customPeriodForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    validateDateRange(periodStartInput.value, periodEndInput.value);
  } catch (error) {
    showPeriodError(error.message);
    return;
  }
  appliedCustomRange = {startDate: periodStartInput.value, endDate: periodEndInput.value};
  currentPeriodMode = "custom";
  clearPeriodError();
  if (currentProject !== null) {
    await loadReport(readServerToken(), currentProject.project_id);
  }
});

periodStartInput.addEventListener("change", updatePeriodInputBounds);
periodEndInput.addEventListener("change", updatePeriodInputBounds);

async function authenticate(token) {
  if (!token) {
    showLogin();
    return;
  }
  storeServerToken(token);
  tokenInput.value = "";
  currentAuthError = null;
  const request = beginReportRequest();
  try {
    currentProjects = await listProjects(token);
    if (!request.isCurrent()) {
      return;
    }
    if (currentProjects.length === 0) {
      currentProject = null;
      currentReport = null;
      showPageStatus("noProjects", {}, true);
      return;
    }
    const remembered = readSelectedProject();
    const selected = currentProjects.find((project) => project.project_id === remembered) || currentProjects[0];
    await loadReportData(token, selected.project_id, request);
  } catch (error) {
    if (request.isCurrent()) {
      handleRequestError(error);
    }
  } finally {
    request.finish();
  }
}

async function listProjects(token) {
  const projects = [];
  let cursor = null;
  do {
    const payload = {limit: 100, include_archived: false};
    if (cursor !== null) {
      payload.cursor = cursor;
    }
    const page = await requestJson("/v1/handoff-reports/projects/list", token, payload);
    projects.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return projects;
}

async function loadReport(token, projectId) {
  if (!token) {
    showLogin();
    return;
  }
  const request = beginReportRequest();
  clearReportError();
  try {
    await loadReportData(token, projectId, request);
  } catch (error) {
    if (request.isCurrent()) {
      if (currentProject !== null) {
        renderProjectOptions(currentProjects, currentProject.project_id);
      }
      handleRequestError(error);
    }
  } finally {
    request.finish();
  }
}

async function loadReportData(token, projectId, request) {
  const project = currentProjects.find((item) => item.project_id === projectId) || currentProject;
  const periodSelection = resolveSelectedPeriod(project);
  const response = await requestJson("/v1/handoff-reports/get", token, {
    project_id: projectId,
    locale: ui.locale() === "zh" ? "zh-CN" : "en",
    include_evidence_checks: false,
    format: "json",
    include_archived: false,
    download: false,
    period: periodSelection.period
  });
  if (!request.isCurrent()) {
    return;
  }
  if (response.report === null) {
    throw new Error("reportUnavailable");
  }
  currentProject = currentProjects.find((item) => item.project_id === projectId) || response.report.project;
  currentReport = response.report;
  currentPeriodSelection = periodSelection;
  rememberSelectedProject(projectId);
  renderProjectOptions(currentProjects, projectId);
  renderReport(currentReport);
}

function beginReportRequest() {
  setBusy(true);
  const request = reportRequests.start();
  return {
    finish() {
      if (request.isCurrent()) {
        setBusy(false);
      }
    },
    isCurrent: request.isCurrent
  };
}

async function requestJson(path, token, payload) {
  const response = await fetchWithBearer(path, token, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  });
  if (response.status === 401) {
    const error = new Error("authRejected");
    error.status = 401;
    throw error;
  }
  if (!response.ok) {
    const error = new Error("requestFailed");
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function handleRequestError(error) {
  if (error.status === 401) {
    clearServerToken();
    showLogin("authRejected");
    return;
  }
  const key = error.message === "reportUnavailable" ? "reportUnavailable" : "serverUnavailable";
  if (typeof error.status === "number") {
    showReportFailure("requestFailed", {status: error.status});
    return;
  }
  showReportFailure(key);
}

function showReportFailure(key, values = {}) {
  if (currentReport === null) {
    showPageStatus(key, values, true);
    return;
  }
  currentPageStatus = null;
  pageStatus.hidden = true;
  reportShell.hidden = false;
  signOut.hidden = false;
  showReportError(key, values);
}

function showLogin(messageKey = "", values = {}) {
  reportRequests.cancel();
  setBusy(false);
  currentProjects = [];
  currentProject = null;
  currentReport = null;
  currentPeriodSelection = null;
  currentPageStatus = null;
  currentAuthError = messageKey ? {key: messageKey, values} : null;
  renderAuthError();
  clearReport();
  authShell.hidden = false;
  pageStatus.hidden = true;
  reportShell.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, values = {}, retryable = false) {
  currentPageStatus = {key: messageKey, values, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  reportShell.hidden = true;
  signOut.hidden = false;
}

function renderPageStatus() {
  if (currentPageStatus === null) {
    pageStatusMessage.textContent = "";
    pageStatusRetry.hidden = true;
    return;
  }
  pageStatusMessage.textContent = translate(currentPageStatus.key, currentPageStatus.values);
  pageStatusRetry.hidden = !currentPageStatus.retryable;
}

function renderAuthError() {
  authError.textContent = currentAuthError === null
    ? ""
    : translate(currentAuthError.key, currentAuthError.values);
}

function renderProjectOptions(projects, selectedProjectId) {
  projectSelect.replaceChildren();
  for (const project of projects) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.selected = project.project_id === selectedProjectId;
    option.textContent = translate("projectOption", {title: project.title, projectId: project.project_id});
    projectSelect.appendChild(option);
  }
}

function renderReport(report) {
  currentPageStatus = null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  reportShell.hidden = false;
  signOut.hidden = false;
  clearReportError();
  setText("project-name", report.project.title);
  setText("report-updated", translate("updated", {value: formatDateTime(report.generated_at)}));
  setText("continuable-count", formatNumber(report.summary.continuable_count));
  setText("blocked-count", formatNumber(report.summary.blocked_count));
  setText("complete-count", formatNumber(report.summary.complete_count));
  setText("no-handoff-count", formatNumber(report.summary.no_handoff_count));
  setText("selected-workstreams", formatNumber(report.coverage.selected_workstreams));
  setText("activity-count", formatNumber(report.activity_selection.length));
  setText("evidence-unavailable", formatNumber(report.coverage.unavailable_evidence_workstreams));
  setText("coverage-description", coverageDescription(report));
  setText("selection-consistency", report.selection_consistency);
  setText("activity-coverage", report.coverage.activity_coverage);
  setText("selection-digest", report.selection_digest || "—");
  setText("report-digest", report.report_digest || "—");
  renderPeriodControls(report);
  renderBlockers(report.workstreams.filter((item) => item.work_status === "blocked"));
  renderWorkstreams(report.workstreams);
  renderHandoffContents(report.workstreams);
}

function clearReport() {
  setText("project-name", translate("handoffReportTitle"));
  setText("report-updated", "");
  for (const id of [
    "continuable-count",
    "blocked-count",
    "complete-count",
    "no-handoff-count",
    "selected-workstreams",
    "activity-count",
    "evidence-unavailable"
  ]) {
    setText(id, "0");
  }
  setText("coverage-description", "");
  setText("selection-consistency", "—");
  setText("activity-coverage", "—");
  setText("selection-digest", "—");
  setText("report-digest", "—");
  currentPeriodSelection = null;
  renderPeriodControls();
  renderBlockers([]);
  renderWorkstreams([]);
  renderHandoffContents([]);
}

function coverageDescription(report) {
  const status = report.coverage.activity_coverage;
  if (status === "captured") {
    return translate("coverageCaptured", {cursor: formatNumber(report.activity_cursor)});
  }
  if (status === "not_configured") {
    return translate("coverageNotConfigured");
  }
  return translate("coverageUnavailable");
}

function renderBlockers(blockers) {
  const section = document.getElementById("blockers-section");
  const list = document.getElementById("blocker-list");
  list.replaceChildren();
  section.hidden = blockers.length === 0;
  for (const item of blockers) {
    const card = document.createElement("article");
    card.className = "blocker-card";
    const heading = document.createElement("h3");
    heading.textContent = item.workstream.title;
    const scope = document.createElement("code");
    scope.textContent = item.workstream.scope_id;
    const detail = document.createElement("p");
    detail.textContent = item.content?.next_action?.text || item.content?.objective || translate("blocked");
    card.append(heading, scope, detail);
    list.appendChild(card);
  }
}

function renderWorkstreams(workstreams) {
  const rows = document.getElementById("workstream-rows");
  const empty = document.getElementById("workstream-empty");
  rows.replaceChildren();
  empty.hidden = workstreams.length !== 0;
  for (const item of workstreams) {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    const title = document.createElement("strong");
    title.textContent = item.workstream.title;
    const scope = document.createElement("code");
    scope.textContent = item.workstream.scope_id;
    identity.append(title, scope);

    const status = document.createElement("td");
    status.appendChild(statusBadge(item.work_status));
    const reporting = document.createElement("td");
    reporting.textContent = statusLabel(item.reporting_status);
    const activities = document.createElement("td");
    activities.textContent = formatNumber(item.observed_activity_count);
    const next = document.createElement("td");
    next.textContent = item.content?.next_action?.text || "—";
    row.append(identity, status, reporting, activities, next);
    rows.appendChild(row);
  }
}

function renderHandoffContents(workstreams) {
  const list = document.getElementById("handoff-content-list");
  list.replaceChildren();
  for (const item of workstreams) {
    const card = document.createElement("article");
    card.className = "handoff-content-card";

    const header = document.createElement("header");
    const identity = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = item.workstream.title;
    const scope = document.createElement("code");
    scope.textContent = item.workstream.scope_id;
    identity.append(title, scope);
    header.append(identity, statusBadge(item.work_status));
    card.appendChild(header);

    if (item.content === null) {
      const empty = document.createElement("p");
      empty.className = "handoff-content-empty";
      empty.textContent = translate("noCommittedHandoff");
      card.appendChild(empty);
      list.appendChild(card);
      continue;
    }

    appendContentSection(card, translate("objective"), [item.content.objective]);
    appendContentSection(card, translate("currentState"), item.content.state.map((statement) => statement.text));
    appendContentSection(card, translate("nextAction"), [item.content.next_action?.text || "—"]);
    appendContentSection(
      card,
      translate("omissions"),
      item.content.omissions.length === 0
        ? [translate("noOmissions")]
        : item.content.omissions.map((omission) => omission.text)
    );
    list.appendChild(card);
  }
}

function appendContentSection(card, headingText, entries) {
  const section = document.createElement("section");
  const heading = document.createElement("h4");
  heading.textContent = headingText;
  section.appendChild(heading);
  if (entries.length === 1) {
    const paragraph = document.createElement("p");
    paragraph.textContent = entries[0];
    section.appendChild(paragraph);
  } else {
    const list = document.createElement("ul");
    for (const entry of entries) {
      const item = document.createElement("li");
      item.textContent = entry;
      list.appendChild(item);
    }
    section.appendChild(list);
  }
  card.appendChild(section);
}

function statusBadge(status) {
  const badge = document.createElement("span");
  badge.className = `status-badge status-${status.replaceAll("_", "-")}`;
  badge.textContent = statusLabel(status);
  return badge;
}

function statusLabel(status) {
  return translate(status);
}

async function downloadMarkdown() {
  const token = readServerToken();
  if (!token || currentProject === null) {
    showLogin();
    return;
  }
  setBusy(true);
  clearReportError();
  try {
    const periodSelection = resolveSelectedPeriod(currentProject);
    const response = await fetchWithBearer("/v1/handoff-reports/get", token, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        project_id: currentProject.project_id,
        locale: ui.locale() === "zh" ? "zh-CN" : "en",
        include_evidence_checks: true,
        format: "markdown",
        include_archived: false,
        download: true,
        period: periodSelection.period
      })
    });
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      showReportError("downloadFailed", {status: response.status});
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "handoff-report.md";
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    showReportError("serverUnavailable");
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  refreshButton.disabled = busy;
  downloadButton.disabled = busy;
  applyCustomPeriodButton.disabled = busy;
  periodStartInput.disabled = busy;
  periodEndInput.disabled = busy;
  periodButtons.forEach((button) => {
    button.disabled = busy;
  });
  projectSelect.disabled = busy;
}

function resolveSelectedPeriod(project) {
  if (project === null || project === undefined) {
    throw new Error("reportUnavailable");
  }
  return resolvePeriodSelection(
    currentPeriodMode,
    project.timezone,
    appliedCustomRange || {startDate: periodStartInput.value, endDate: periodEndInput.value}
  );
}

function renderPeriodControls(report = null) {
  periodButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.periodMode === currentPeriodMode));
  });
  customPeriodForm.classList.toggle("is-active", currentPeriodMode === "custom");
  const project = currentProject || currentProjects[0] || null;
  if (currentPeriodSelection === null && project !== null && currentPeriodMode !== "custom") {
    currentPeriodSelection = resolveSelectedPeriod(project);
  }
  const selection = currentPeriodSelection;
  if (selection === null) {
    setText("period-summary-label", "");
    setText("period-comparison", "");
    setText("period-boundary-note", "");
    return;
  }
  if (currentPeriodMode !== "custom") {
    periodStartInput.value = selection.startDate;
    periodEndInput.value = selection.endDate;
  }
  updatePeriodInputBounds();
  setText("period-summary-label", translate("periodSummary", {
    preset: translate(currentPeriodMode),
    range: formatDateRange(selection.startDate, selection.endDate, ui.localeTag()),
    timezone: selection.period.timezone
  }));
  const comparison = report?.period_comparison;
  setText("period-comparison", comparison === null || comparison === undefined
    ? ""
    : translate("periodComparison", {
      current: formatNumber(comparison.current_activity_count),
      previous: formatNumber(comparison.previous_activity_count),
      delta: formatSignedNumber(comparison.activity_delta)
    }));
  setText("period-boundary-note", comparison?.handoff_boundary_coverage === "unavailable"
    ? translate("periodBoundaryUnavailable")
    : "");
}

function updatePeriodInputBounds() {
  periodStartInput.setAttribute("aria-invalid", "false");
  periodEndInput.setAttribute("aria-invalid", "false");
}

function showPeriodError(key) {
  periodError.textContent = translate(key);
  periodStartInput.setAttribute("aria-invalid", "true");
  periodEndInput.setAttribute("aria-invalid", "true");
}

function clearPeriodError() {
  periodError.textContent = "";
  updatePeriodInputBounds();
}

function showReportError(key, values = {}) {
  reportError.textContent = translate(key, values);
}

function clearReportError() {
  reportError.textContent = "";
}

function rememberSelectedProject(projectId) {
  try {
    sessionStorage.setItem(selectedProjectKey, projectId);
  } catch (error) {
    // Project selection remains valid for the current render.
  }
}

function readSelectedProject() {
  try {
    return sessionStorage.getItem(selectedProjectKey);
  } catch (error) {
    return null;
  }
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function formatSignedNumber(value) {
  return new Intl.NumberFormat(ui.localeTag(), {signDisplay: "always"}).format(value);
}

ui.initialize();
authenticate(readServerToken());
