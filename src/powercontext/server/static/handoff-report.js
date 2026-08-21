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
import {formatDateRange, resolvePeriodSelection, validateDateRange} from "./handoff-period.js";
import {createPageUi, createRequestGate} from "./page-ui.js?v=locale-complete";

const selectedProjectKey = "powercontext.handoff-report.project";
const selectedWorkKey = "powercontext.handoff-report.work";
const autoRefreshIntervalMilliseconds = 5_000;
const continuityTimelineRecentLimit = 6;
const workstreamSearchThreshold = 8;
const projectOptionRenderLimit = 50;
const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const translations = {
  en: {
    pageTitle: "PowerContext Handoff Report",
    dashboardTitle: "Dashboard",
    handoffReportTitle: "Handoff Report",
    brandHomeLabel: "PowerContext Dashboard",
    primaryNavigation: "Primary navigation",
    handoffSummary: "Handoff summary",
    maintainedBy: "Maintained by OceanBase.",
    signOut: "Sign out",
    authTitle: "Connect to PowerContext",
    authIntro: "Enter the bearer token configured for this PowerContext Server. The token stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    refresh: "Refresh",
    downloadMarkdown: "Download Markdown",
    projects: "Projects",
    searchProjectsPlaceholder: "Search by project name or ID",
    projectSearchCount: "{count} Projects",
    projectSearchMatches: "{count} matching Projects",
    projectSearchLimited: "Showing the first {shown} of {total} matches. Keep typing to narrow the list.",
    noMatchingProjects: "No Projects match this search.",
    reportPeriod: "Report period",
    activity: "Activity",
    activitySubtitle: "Period controls affect Activity counts only. Handoff status stays on the current exact selection.",
    activityPeriod: "Activity period",
    activityByWorkstream: "Activity by Workstream",
    day: "Day",
    week: "Week",
    month: "Month",
    custom: "Custom",
    periodStart: "Start date",
    periodEnd: "End date",
    apply: "Apply",
    periodSummary: "{preset} / {range} / {timezone}",
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
    workstreamNavigation: "Workstream navigation",
    workstreamPagination: "Workstream pagination",
    searchWorkstreams: "Search Workstreams",
    searchWorkstreamsPlaceholder: "Search by name or scope",
    previousWorkstream: "Previous",
    nextWorkstream: "Next",
    workstreamPosition: "{current} / {total}",
    workstreamMatchCount: "{count} matches",
    noMatchingWorkstreams: "No Workstreams match this search.",
    workstream: "Workstream",
    status: "Status",
    reporting: "Reporting",
    nextAction: "Next action",
    state: "Current state",
    next_action: "Next action",
    available: "Available",
    unavailable: "Unavailable",
    noWorkstreams: "No Workstreams are registered for this Project.",
    handoffContents: "Handoff content",
    handoffContentsSubtitle: "Edit the Handoff as one document. Every save creates a new Handoff Revision.",
    currentSnapshot: "Current Handoff snapshot",
    exactRevision: "Exact Revision",
    editHandoffContent: "Edit",
    editHandoffContentLabel: "Edit all Handoff content",
    cancelEdit: "Cancel",
    saveRevision: "Save Revision",
    createFirstRevision: "Create first Revision",
    firstRevisionNote: "The first Revision needs an Objective and at least one Current state item.",
    emptyField: "Not provided.",
    savingRevision: "Saving a new Handoff Revision...",
    revisionSaved: "Saved as Handoff Revision @{revision}.",
    revisionSaveFailed: "The Handoff Revision could not be saved (HTTP {status}).",
    objective: "Objective",
    currentState: "Current state",
    omissions: "Known omissions",
    currentStateLines: "Current state, one item per line",
    disposition: "Disposition",
    omissionLines: "Known omissions, one item per line",
    liveStateCheck: "Live workspace",
    capabilityCheck: "Capability",
    authorizationCheck: "Authorization",
    notChecked: "Not checked",
    confirmed: "Confirmed",
    mismatch: "Mismatch",
    insufficient: "Insufficient",
    receiverIdentity: "Receiver identity",
    continuityTimeline: "Continuity timeline",
    continuityOrderNote: "Ordered by Source journal position, not wall-clock time.",
    revisionHistory: "Handoff Revision history",
    revisionHistorySummary: "{total} Revisions. Latest first.",
    revisionHistoryTruncated: "Showing the latest {shown} of {total} Revisions.",
    revisionHistoryEmpty: "No committed Handoff Revisions are available.",
    revisionCurrent: "Current",
    revisionNextAction: "Next: {value}",
    revisionCounts: "{state} state items / {omissions} omissions",
    transferState: "Transfer",
    outcomeState: "Outcome",
    editorRequired: "Objective and at least one current-state item are required.",
    timelineEmpty: "No high-level Work continuity records are available for this scope.",
    timelineTruncated: "Only the latest {count} of {total} continuity events are shown.",
    timelineInvalid: "{count} Work record(s) could not be read and were excluded.",
    timelineShowEarlier: "Show {count} earlier events",
    timelineShowRecent: "Show latest {count}",
    eventSource: "Source",
    eventRevision: "Handoff Revision",
    eventReceipt: "Receipt",
    eventReceiverChecks: "Receiver checks",
    eventSchema: "Record schema",
    eventNoDetails: "No additional details were recorded.",
    autoRefreshActive: "Auto-refresh every 5 seconds",
    autoRefreshEditing: "Auto-refresh paused while Handoff content is being edited.",
    autoRefreshBusy: "Auto-refresh paused during this action.",
    autoRefreshing: "Refreshing report...",
    autoRefreshUpdated: "Auto-refreshed just now",
    autoRefreshFailed: "Auto-refresh failed. Use Refresh to retry.",
    eventActor: "Receiver: {actor}",
    "work-contract": "Delegation contract",
    "handoff-boundary": "Handoff sent",
    "handoff-receipt": "Receiver decision",
    "task-outcome": "Task outcome",
    delegated: "Delegated",
    accepted: "Accepted",
    needs_clarification: "Needs clarification",
    declined: "Declined",
    succeeded: "Succeeded",
    partial: "Partial",
    failed: "Failed",
    cancelled: "Cancelled",
    awaiting_receipt: "Awaiting receipt",
    awaiting_outcome: "Awaiting outcome",
    not_expected: "Not expected",
    not_applicable: "Not applicable",
    covered: "Covered",
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
    languageEnglish: "EN",
    updated: "Updated {value}",
    projectOption: "{title} ({projectId})",
    coverageCaptured: "Captured Activity is included through cursor {cursor}. Counts describe observed events, not completion percentage.",
    coverageNotConfigured: "Activity adapters are not configured. Missing Activity must not be read as no work occurring.",
    coverageUnavailable: "Activity coverage is unavailable for this report.",
    noProjects: "No Handoff Report Project is configured.",
    previewReportTitle: "Handoff Report template",
    preview: "Preview",
    previewNotice: "This data-free preview shows the report's main Handoff sections. Values shown as \u201c\u2014\u201d do not represent real Project status.",
    previewRetryHint: "Configure a Project, then retry to load its report.",
    previewPlaceholder: "\u2014",
    previewProjectSummary: "Project summary and scope",
    previewProjectSummarySubtitle: "Project identity, selected scope, and reporting period",
    previewProject: "Project",
    previewScope: "Scope",
    previewWorkstreamsTitle: "Workstreams and Handoff",
    previewWorkstreamsSubtitle: "Current Handoff state and continuation content for each scope",
    previewHandoffContentsSubtitle: "Objective, current state, disposition, next action, and known omissions",
    previewActivitySubtitle: "Coverage and period comparison become available with a real Project report.",
    previewCoverageDescription: "Coverage values appear here after a Project report is available.",
    previewActivityComparison: "Activity and period comparison",
    previewActivityComparisonSubtitle: "Observed Activity in the selected and previous periods",
    previewCurrentPeriod: "Current period",
    previewPreviousPeriod: "Previous period",
    previewChange: "Change",
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
    exact_input: "Exact input",
    optimistic_stable: "Optimistically stable",
    captured: "Captured",
    not_configured: "Not configured",
    unknown: "Unknown"
  },
  zh: {
    pageTitle: "PowerContext 项目交接报告",
    dashboardTitle: "仪表盘",
    handoffReportTitle: "交接报告",
    brandHomeLabel: "PowerContext 仪表盘",
    primaryNavigation: "主导航",
    handoffSummary: "交接摘要",
    maintainedBy: "由 OceanBase 维护。",
    signOut: "退出",
    authTitle: "连接 PowerContext",
    authIntro: "请输入 PowerContext 服务器配置的访问令牌。令牌仅保留在当前浏览器标签页。",
    tokenLabel: "服务器访问令牌",
    continue: "继续",
    refresh: "刷新",
    downloadMarkdown: "下载 Markdown",
    projects: "项目",
    searchProjectsPlaceholder: "按项目名称或标识搜索",
    projectSearchCount: "共 {count} 个项目",
    projectSearchMatches: "匹配 {count} 个项目",
    projectSearchLimited: "显示前 {shown} 个，共匹配 {total} 个。继续输入可缩小范围。",
    noMatchingProjects: "没有匹配的项目。",
    reportPeriod: "报告周期",
    activity: "活动",
    activitySubtitle: "周期控件只影响活动数量，交接状态始终采用当前精确选择。",
    activityPeriod: "活动周期",
    activityByWorkstream: "各工作项活动",
    day: "日",
    week: "周",
    month: "月",
    custom: "自定义",
    periodStart: "开始日期",
    periodEnd: "结束日期",
    apply: "应用",
    periodSummary: "{preset} / {range} / {timezone}",
    periodComparison: "活动：本期 {current} / 上期 {previous} / 变化 {delta}",
    periodBoundaryUnavailable: "交接状态采用当前精确选择；该周期只筛选活动，不能还原历史交接边界。",
    periodDatesRequired: "请选择开始日期和结束日期。",
    periodInvalidRange: "开始日期不能晚于结束日期。",
    continuable: "可继续",
    blocked: "阻塞",
    complete: "已完成",
    noHandoff: "无交接",
    coverage: "报告覆盖范围",
    workstreams: "工作项",
    activities: "活动",
    evidenceUnavailable: "证据不可用",
    blockers: "阻塞事项",
    blockersSubtitle: "需要人工处理后才能继续的工作项",
    workstreamsSubtitle: "每个工作范围的精确交接状态与下一步",
    workstreamNavigation: "工作项导航",
    workstreamPagination: "工作项翻页",
    searchWorkstreams: "搜索工作项",
    searchWorkstreamsPlaceholder: "按名称或范围标识搜索",
    previousWorkstream: "上一项",
    nextWorkstream: "下一项",
    workstreamPosition: "{current} / {total}",
    workstreamMatchCount: "匹配 {count} 项",
    noMatchingWorkstreams: "没有匹配的工作项。",
    workstream: "工作项",
    status: "状态",
    reporting: "汇报状态",
    nextAction: "下一步",
    state: "当前状态",
    next_action: "下一步",
    available: "可用",
    unavailable: "不可用",
    noWorkstreams: "该项目尚未登记工作项。",
    handoffContents: "交接内容",
    handoffContentsSubtitle: "统一编辑整份交接内容，每次保存都会生成新的交接版本。",
    currentSnapshot: "当前交接快照",
    exactRevision: "精确版本",
    editHandoffContent: "编辑",
    editHandoffContentLabel: "编辑全部交接内容",
    cancelEdit: "取消",
    saveRevision: "保存新版本",
    createFirstRevision: "创建首个版本",
    firstRevisionNote: "首个版本必须包含目标和至少一项当前状态。",
    emptyField: "未填写。",
    savingRevision: "正在保存新的交接版本...",
    revisionSaved: "已保存为交接版本 @{revision}。",
    revisionSaveFailed: "交接版本保存失败（HTTP {status}）。",
    objective: "目标",
    currentState: "当前状态",
    omissions: "已知缺失",
    currentStateLines: "当前状态，每行一项",
    disposition: "处置状态",
    omissionLines: "已知缺失，每行一项",
    liveStateCheck: "实时工作区",
    capabilityCheck: "能力",
    authorizationCheck: "授权",
    notChecked: "未检查",
    confirmed: "已确认",
    mismatch: "不匹配",
    insufficient: "不足",
    receiverIdentity: "接手方身份",
    continuityTimeline: "连续性时间线",
    continuityOrderNote: "按来源日志位置排序，不代表实际发生时间。",
    revisionHistory: "交接版本历史",
    revisionHistorySummary: "共 {total} 个版本，按最新优先显示。",
    revisionHistoryTruncated: "共 {total} 个版本，显示最近 {shown} 个。",
    revisionHistoryEmpty: "该工作项尚无已提交的交接版本。",
    revisionCurrent: "当前版本",
    revisionNextAction: "下一步：{value}",
    revisionCounts: "状态 {state} 项 / 缺失 {omissions} 项",
    transferState: "交接状态",
    outcomeState: "结果状态",
    editorRequired: "目标和至少一项当前状态不能为空。",
    timelineEmpty: "该工作范围尚无高层工作连续性记录。",
    timelineTruncated: "仅显示最近 {count} 条，共有 {total} 条连续性事件。",
    timelineInvalid: "有 {count} 条工作记录无法读取，已明确排除。",
    timelineShowEarlier: "查看更早的 {count} 条记录",
    timelineShowRecent: "收起，仅看最近 {count} 条",
    eventSource: "来源",
    eventRevision: "交接版本",
    eventReceipt: "接手回执",
    eventReceiverChecks: "接手检查",
    eventSchema: "记录格式",
    eventNoDetails: "该事件没有记录更多详情。",
    autoRefreshActive: "每 5 秒自动刷新",
    autoRefreshEditing: "正在编辑交接内容，自动刷新已暂停。",
    autoRefreshBusy: "当前操作进行中，自动刷新已暂停。",
    autoRefreshing: "正在刷新报告...",
    autoRefreshUpdated: "刚刚已自动刷新",
    autoRefreshFailed: "自动刷新失败，请使用刷新按钮重试。",
    eventActor: "接手方：{actor}",
    "work-contract": "委派契约",
    "handoff-boundary": "发送交接",
    "handoff-receipt": "接手选择",
    "task-outcome": "任务结果",
    delegated: "已委派",
    accepted: "已接手",
    needs_clarification: "需要补充",
    declined: "无法接手",
    succeeded: "成功",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
    awaiting_receipt: "等待接手选择",
    awaiting_outcome: "等待任务结果",
    not_expected: "暂不需要",
    not_applicable: "暂不适用",
    covered: "已覆盖",
    metadata: "报告元数据",
    selectionConsistency: "选择一致性",
    activityCoverage: "活动覆盖范围",
    selectionDigest: "选择摘要哈希",
    reportDigest: "报告摘要哈希",
    dark: "深色",
    light: "浅色",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    languageEnglish: "EN",
    updated: "更新于 {value}",
    projectOption: "{title}（{projectId}）",
    coverageCaptured: "已纳入游标 {cursor} 之前捕获的活动。数量表示已观察事件，不代表完成百分比。",
    coverageNotConfigured: "活动适配器尚未配置；缺少活动不能解释为没有发生工作。",
    coverageUnavailable: "当前报告无法取得活动覆盖信息。",
    noProjects: "尚未配置交接报告项目。",
    previewReportTitle: "交接报告模板",
    preview: "预览",
    previewNotice: "此无数据预览展示报告的主要交接部分。以“—”显示的值不代表真实项目状态。",
    previewRetryHint: "配置项目后，点击重试以加载真实报告。",
    previewPlaceholder: "—",
    previewProjectSummary: "项目摘要与范围",
    previewProjectSummarySubtitle: "项目身份、所选范围和报告周期",
    previewProject: "项目",
    previewScope: "范围",
    previewWorkstreamsTitle: "工作项与交接",
    previewWorkstreamsSubtitle: "每个范围的当前交接状态与继续工作所需内容",
    previewHandoffContentsSubtitle: "目标、当前状态、处置状态、下一步和已知缺失",
    previewActivitySubtitle: "配置真实项目后，将显示覆盖范围和周期对比。",
    previewCoverageDescription: "项目报告可用后，此处将显示覆盖数据。",
    previewActivityComparison: "活动与周期对比",
    previewActivityComparisonSubtitle: "所选周期与上一周期内观察到的活动",
    previewCurrentPeriod: "本期",
    previewPreviousPeriod: "上期",
    previewChange: "变化",
    authRejected: "服务器拒绝了该访问令牌。",
    requestFailed: "交接报告请求失败（HTTP {status}）。",
    serverUnavailable: "服务器无法访问。",
    retry: "重试",
    reportUnavailable: "当前项目的交接报告不可用。",
    downloadFailed: "Markdown 下载失败（HTTP {status}）。",
    reported: "已汇报",
    reported_with_omissions: "已汇报但有缺失",
    evidence_unavailable: "证据不可用",
    no_handoff: "无交接记录",
    activity_after_handoff: "交接后有活动",
    activity_without_handoff: "有活动但无交接记录",
    no_observed_activity: "未观察到活动",
    current_only: "仅当前状态",
    exact_input: "精确输入",
    optimistic_stable: "乐观稳定",
    captured: "已捕获",
    not_configured: "未配置",
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
const previewShell = document.getElementById("handoff-report-preview");
const previewRetryButton = document.getElementById("preview-retry");
const reportShell = document.getElementById("handoff-report");
const reportError = document.getElementById("report-error");
const projectCombobox = document.getElementById("project-combobox");
const projectSearchInput = document.getElementById("project-search");
const projectOptions = document.getElementById("project-options");
const projectSearchStatus = document.getElementById("project-search-status");
const refreshButton = document.getElementById("refresh-report");
const downloadButton = document.getElementById("download-report");
const periodButtons = Array.from(document.querySelectorAll("[data-period-mode]"));
const customPeriodForm = document.getElementById("custom-period-form");
const periodStartInput = document.getElementById("period-start");
const periodEndInput = document.getElementById("period-end");
const applyCustomPeriodButton = document.getElementById("apply-custom-period");
const periodError = document.getElementById("period-error");
const autoRefreshStatus = document.getElementById("auto-refresh-status");
const signOut = document.getElementById("sign-out");
const handoffSaveStatus = document.getElementById("handoff-save-status");
const handoffEditorActions = document.getElementById("handoff-editor-actions");
const editHandoffContentButton = document.getElementById("edit-handoff-content");
const saveHandoffRevisionButton = document.getElementById("save-handoff-revision");
const cancelHandoffEditButton = document.getElementById("cancel-handoff-edit");
const continuityTimelineToggle = document.getElementById("continuity-timeline-toggle");
const workstreamSwitcherToolbar = document.getElementById("workstream-switcher-toolbar");
const workstreamSearchField = document.getElementById("workstream-search-field");
const workstreamSearchInput = document.getElementById("workstream-search");
const workstreamSwitcherNavigation = document.getElementById("workstream-switcher-navigation");
const previousWorkstreamButton = document.getElementById("previous-workstream");
const nextWorkstreamButton = document.getElementById("next-workstream");
const workstreamPosition = document.getElementById("workstream-position");
const workstreamListPanel = document.querySelector(".workstream-list-panel");
const workstreamList = document.getElementById("workstream-list");
const workstreamFilterEmpty = document.getElementById("workstream-filter-empty");
let currentProjects = [];
let currentHandoffWorks = [];
let currentProject = null;
let currentReport = null;
let currentAuthError = null;
let currentPageStatus = null;
let currentPeriodMode = "day";
let currentPeriodSelection = null;
let appliedCustomRange = null;
let currentWorkstreamScope = null;
let revisionSaving = false;
let reportLoading = false;
let editorDirty = false;
let autoRefreshTimer = null;
let currentWorkstreamQuery = "";
let projectActiveIndex = -1;
let lastCenteredWorkstreamKey = null;
let pendingWorkstreamLayoutFrame = null;
const handoffDrafts = new Map();
const pendingHandoffAttempts = new Map();
const expandedContinuityScopes = new Set();
const openContinuityEvents = new Map();
const ui = createPageUi(translations, ({userInitiated = false} = {}) => {
  renderAuthError();
  renderPageStatus();
  if (currentProject !== null) {
    renderProjectCombobox(currentProjects, currentProject.project_id);
  }
  if (currentReport !== null) {
    renderReport(currentReport);
  } else {
    renderPeriodControls();
  }
  updateAutoRefreshStatus();
  if (userInitiated && currentProject !== null && readServerToken()) {
    void loadReport(readServerToken(), currentProject.project_id, {
      background: true,
      selectedScopeId: currentWorkstreamScope
    });
  }
});
const {formatDateTime, formatNumber, translate} = ui;
const reportRequests = createRequestGate();

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await authenticate(tokenInput.value);
});

signOut.addEventListener("click", () => {
  stopAutoRefresh();
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

previewRetryButton.addEventListener("click", async () => {
  await authenticate(readServerToken());
});

refreshButton.addEventListener("click", async () => {
  if (currentProject !== null) {
    await loadReport(readServerToken(), currentProject.project_id);
  }
});

editHandoffContentButton.addEventListener("click", () => {
  const item = currentReport?.workstreams.find(
    (candidate) => candidate.workstream.scope_id === currentWorkstreamScope
  ) || null;
  if (item !== null) {
    startHandoffEdit(item);
  }
});

cancelHandoffEditButton.addEventListener("click", () => {
  cancelHandoffEdit();
});

downloadButton.addEventListener("click", async () => {
  await downloadMarkdown();
});

projectSearchInput.addEventListener("focus", () => {
  if (projectOptions.hidden) {
    projectSearchInput.value = "";
  }
  openProjectOptions();
});

projectSearchInput.addEventListener("input", () => {
  projectActiveIndex = -1;
  renderProjectOptionsList();
  openProjectOptions();
});

projectSearchInput.addEventListener("keydown", (event) => {
  handleProjectSearchKeydown(event);
});

projectCombobox.addEventListener("focusout", (event) => {
  if (!projectCombobox.contains(event.relatedTarget)) {
    closeProjectOptions({restoreSelection: true});
  }
});

workstreamSearchInput.addEventListener("input", () => {
  currentWorkstreamQuery = normalizeWorkstreamQuery(workstreamSearchInput.value);
  lastCenteredWorkstreamKey = null;
  applyWorkstreamFilter();
});

workstreamSearchInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && workstreamSearchInput.value) {
    event.preventDefault();
    resetWorkstreamSearch();
    applyWorkstreamFilter();
    workstreamSearchInput.focus();
    return;
  }
  if (event.key === "Enter") {
    const visibleButtons = visibleWorkstreamButtons();
    const selected = visibleButtons.find((button) => button.getAttribute("aria-current") === "true");
    const target = selected || visibleButtons[0];
    if (target !== undefined) {
      event.preventDefault();
      activateWorkstream(target.dataset.scopeId);
    }
  }
});

previousWorkstreamButton.addEventListener("click", () => {
  activateAdjacentWorkstream(-1);
});

nextWorkstreamButton.addEventListener("click", () => {
  activateAdjacentWorkstream(1);
});

new ResizeObserver(() => {
  scheduleWorkstreamLayoutUpdate();
}).observe(workstreamListPanel);

continuityTimelineToggle.addEventListener("click", () => {
  if (currentWorkstreamScope === null) {
    return;
  }
  if (expandedContinuityScopes.has(currentWorkstreamScope)) {
    expandedContinuityScopes.delete(currentWorkstreamScope);
  } else {
    expandedContinuityScopes.add(currentWorkstreamScope);
  }
  const item = currentReport?.workstreams.find(
    (candidate) => candidate.workstream.scope_id === currentWorkstreamScope
  ) || null;
  renderContinuity(item?.continuity || null);
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
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    void autoRefreshReport();
  }
});

async function authenticate(token) {
  if (authenticationRequired && !token) {
    showLogin();
    return;
  }
  if (authenticationRequired) {
    storeServerToken(token);
  }
  tokenInput.value = "";
  currentAuthError = null;
  const request = beginReportRequest();
  try {
    const projects = await listProjects(token);
    if (!request.isCurrent()) {
      return;
    }
    currentProjects = projects;
    if (currentProjects.length === 0) {
      stopAutoRefresh();
      currentHandoffWorks = [];
      currentProject = null;
      currentReport = null;
      currentWorkstreamScope = null;
      showReportPreview();
      return;
    }
    const rememberedProjectId = readSelectedProject();
    const rememberedWork = readSelectedWorkLocation();
    const selectedProject = currentProjects.find(
      (project) => project.project_id === rememberedWork?.projectId
    ) || currentProjects.find(
      (project) => project.project_id === rememberedProjectId
    ) || currentProjects[0];
    currentHandoffWorks = await listHandoffWorks(token, selectedProject);
    if (!request.isCurrent()) {
      return;
    }
    const selectedScopeId = rememberedWork?.projectId === selectedProject.project_id
      && currentHandoffWorks.some((item) => item.workstream.scope_id === rememberedWork.scopeId)
      ? rememberedWork.scopeId
      : currentHandoffWorks[0]?.workstream.scope_id || null;
    await loadReportData(token, selectedProject.project_id, request, {
      selectedScopeId
    });
    if (request.isCurrent()) {
      startAutoRefresh();
    }
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
  return projects.sort((left, right) => (
    left.title.localeCompare(right.title, ui.localeTag(), {numeric: true, sensitivity: "base"})
    || left.project_id.localeCompare(right.project_id)
  ));
}

async function listHandoffWorks(token, project) {
  const works = [];
  let cursor = null;
  do {
    const payload = {project_id: project.project_id, limit: 100, include_archived: false};
    if (cursor !== null) {
      payload.cursor = cursor;
    }
    const page = await requestJson("/v1/handoff-reports/workstreams/list", token, payload);
    works.push(...page.items.map((workstream) => ({project, workstream})));
    cursor = page.next_cursor;
  } while (cursor !== null);
  return works;
}

async function loadReport(token, projectId, {background = false, selectedScopeId = null} = {}) {
  if (reportLoading) {
    return false;
  }
  if (authenticationRequired && !token) {
    showLogin();
    return false;
  }
  reportLoading = true;
  if (background) {
    setAutoRefreshStatus("refreshing");
  } else {
    clearReportError();
  }
  const request = beginReportRequest({busy: !background});
  try {
    if (currentProject?.project_id !== projectId) {
      const project = currentProjects.find((item) => item.project_id === projectId);
      if (project === undefined) {
        throw new Error("reportUnavailable");
      }
      currentHandoffWorks = await listHandoffWorks(token, project);
      if (!request.isCurrent()) {
        return false;
      }
    }
    await loadReportData(token, projectId, request, {selectedScopeId});
    if (!request.isCurrent()) {
      return false;
    }
    if (background) {
      setAutoRefreshStatus("updated");
    }
    return true;
  } catch (error) {
    if (!request.isCurrent()) {
      return false;
    }
    if (currentProject !== null) {
      renderProjectCombobox(currentProjects, currentProject.project_id);
    }
    if (background) {
      if (error.status === 401) {
        handleRequestError(error);
      } else {
        setAutoRefreshStatus("failed");
      }
      return false;
    }
    handleRequestError(error);
    return false;
  } finally {
    reportLoading = false;
    request.finish();
    syncHandoffEditingState();
    if (!background && request.isCurrent()) {
      updateAutoRefreshStatus();
    }
  }
}

async function loadReportData(token, projectId, request, {selectedScopeId = null} = {}) {
  const projectChanged = currentProject?.project_id !== projectId;
  const project = currentProjects.find((item) => item.project_id === projectId) || currentProject;
  const defaultLocale = projectUiLocale(project);
  if (!ui.hasLocalePreference() && defaultLocale !== null && defaultLocale !== ui.locale()) {
    ui.applyLocale(defaultLocale, false);
  }
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
  if (projectChanged) {
    resetWorkstreamSearch();
    lastCenteredWorkstreamKey = null;
  }
  currentProject = currentProjects.find((item) => item.project_id === projectId) || response.report.project;
  currentReport = response.report;
  currentPeriodSelection = periodSelection;
  if (selectedScopeId !== null) {
    currentWorkstreamScope = selectedScopeId;
  }
  rememberSelectedProject(projectId);
  renderProjectCombobox(currentProjects, projectId);
  renderReport(currentReport);
}

function projectUiLocale(project) {
  if (typeof project?.default_locale !== "string") {
    return null;
  }
  return project.default_locale.toLowerCase().startsWith("zh") ? "zh" : "en";
}

function beginReportRequest({busy = true} = {}) {
  if (busy) {
    setBusy(true);
  }
  const request = reportRequests.start();
  return {
    finish() {
      if (busy && request.isCurrent()) {
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
  previewShell.hidden = true;
  reportShell.hidden = false;
  signOut.hidden = !authenticationRequired;
  showReportError(key, values);
}

function showLogin(messageKey = "", values = {}) {
  stopAutoRefresh();
  reportRequests.cancel();
  setBusy(false);
  reportLoading = false;
  revisionSaving = false;
  handoffDrafts.clear();
  pendingHandoffAttempts.clear();
  editorDirty = false;
  currentProjects = [];
  currentHandoffWorks = [];
  currentProject = null;
  currentReport = null;
  currentWorkstreamScope = null;
  currentPeriodSelection = null;
  currentPageStatus = null;
  currentAuthError = messageKey ? {key: messageKey, values} : null;
  closeProjectOptions();
  projectSearchInput.value = "";
  projectSearchInput.disabled = false;
  projectSearchStatus.textContent = "";
  handoffSaveStatus.textContent = "";
  renderAuthError();
  clearReport();
  authShell.hidden = false;
  pageStatus.hidden = true;
  previewShell.hidden = true;
  reportShell.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, values = {}, retryable = false) {
  currentPageStatus = {key: messageKey, values, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  previewShell.hidden = true;
  reportShell.hidden = true;
  signOut.hidden = !authenticationRequired;
}

function showReportPreview() {
  currentPageStatus = null;
  clearReport();
  authShell.hidden = true;
  pageStatus.hidden = true;
  previewShell.hidden = false;
  reportShell.hidden = true;
  signOut.hidden = !authenticationRequired;
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

function renderProjectCombobox(projects, selectedProjectId) {
  const selected = projects.find((project) => project.project_id === selectedProjectId) || null;
  if (projectOptions.hidden) {
    projectSearchInput.value = selected === null ? "" : projectOptionLabel(selected);
    projectSearchStatus.textContent = translate("projectSearchCount", {count: formatNumber(projects.length)});
    return;
  }
  renderProjectOptionsList();
}

function projectOptionLabel(project) {
  return translate("projectOption", {title: project.title, projectId: project.project_id});
}

function normalizedProjectQuery(value) {
  return value.trim().toLocaleLowerCase();
}

function matchingProjects() {
  const query = normalizedProjectQuery(projectSearchInput.value);
  if (!query) {
    return currentProjects;
  }
  return currentProjects.filter((project) => (
    `${project.title}\n${project.project_id}\n${project.project_key}`.toLocaleLowerCase().includes(query)
  ));
}

function renderProjectOptionsList() {
  const matches = matchingProjects();
  const visible = matches.slice(0, projectOptionRenderLimit);
  projectOptions.replaceChildren();
  projectActiveIndex = Math.min(projectActiveIndex, visible.length - 1);
  for (const [index, project] of visible.entries()) {
    const option = document.createElement("button");
    option.className = "project-option";
    option.id = `project-option-${index}`;
    option.type = "button";
    option.role = "option";
    option.dataset.projectId = project.project_id;
    option.setAttribute("aria-selected", String(project.project_id === currentProject?.project_id));

    const title = document.createElement("strong");
    title.textContent = project.title;
    const identity = document.createElement("code");
    identity.textContent = project.project_id;
    option.append(title, identity);
    option.addEventListener("click", () => {
      void selectProject(project.project_id);
    });
    projectOptions.appendChild(option);
  }
  if (matches.length === 0) {
    const empty = document.createElement("p");
    empty.className = "project-options-empty";
    empty.textContent = translate("noMatchingProjects");
    projectOptions.appendChild(empty);
  }
  if (matches.length > visible.length) {
    projectSearchStatus.textContent = translate("projectSearchLimited", {
      shown: formatNumber(visible.length),
      total: formatNumber(matches.length)
    });
  } else {
    projectSearchStatus.textContent = translate(
      projectSearchInput.value ? "projectSearchMatches" : "projectSearchCount",
      {count: formatNumber(matches.length)}
    );
  }
  updateActiveProjectOption();
}

function openProjectOptions() {
  projectOptions.hidden = false;
  projectSearchInput.setAttribute("aria-expanded", "true");
  renderProjectOptionsList();
}

function closeProjectOptions({restoreSelection = false} = {}) {
  projectOptions.hidden = true;
  projectSearchInput.setAttribute("aria-expanded", "false");
  projectSearchInput.removeAttribute("aria-activedescendant");
  projectActiveIndex = -1;
  if (restoreSelection) {
    const selected = currentProjects.find((project) => project.project_id === currentProject?.project_id);
    projectSearchInput.value = selected === undefined ? "" : projectOptionLabel(selected);
    projectSearchStatus.textContent = translate("projectSearchCount", {count: formatNumber(currentProjects.length)});
  }
}

function handleProjectSearchKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeProjectOptions({restoreSelection: true});
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Enter", "Home", "End"].includes(event.key)) {
    return;
  }
  const options = Array.from(projectOptions.querySelectorAll(".project-option"));
  if (projectOptions.hidden) {
    openProjectOptions();
  }
  if (options.length === 0) {
    return;
  }
  event.preventDefault();
  if (event.key === "Enter") {
    const target = options[projectActiveIndex] || options[0];
    void selectProject(target.dataset.projectId);
    return;
  }
  if (event.key === "Home") {
    projectActiveIndex = 0;
  } else if (event.key === "End") {
    projectActiveIndex = options.length - 1;
  } else if (event.key === "ArrowDown") {
    projectActiveIndex = Math.min(projectActiveIndex + 1, options.length - 1);
  } else {
    projectActiveIndex = projectActiveIndex <= 0 ? options.length - 1 : projectActiveIndex - 1;
  }
  updateActiveProjectOption();
}

function updateActiveProjectOption() {
  const options = Array.from(projectOptions.querySelectorAll(".project-option"));
  options.forEach((option, index) => {
    option.dataset.active = String(index === projectActiveIndex);
  });
  const active = options[projectActiveIndex];
  if (active === undefined) {
    projectSearchInput.removeAttribute("aria-activedescendant");
    return;
  }
  projectSearchInput.setAttribute("aria-activedescendant", active.id);
  active.scrollIntoView({block: "nearest"});
}

async function selectProject(projectId) {
  const selected = currentProjects.find((project) => project.project_id === projectId);
  if (selected === undefined) {
    return;
  }
  projectSearchInput.value = projectOptionLabel(selected);
  closeProjectOptions();
  if (projectId !== currentProject?.project_id) {
    currentWorkstreamScope = null;
    await loadReport(readServerToken(), projectId);
  }
}

function renderReport(report) {
  currentPageStatus = null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  previewShell.hidden = true;
  reportShell.hidden = false;
  signOut.hidden = !authenticationRequired;
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
  setText("selection-consistency", statusLabel(report.selection_consistency));
  setText("activity-coverage", statusLabel(report.coverage.activity_coverage));
  setText("selection-digest", report.selection_digest || "-");
  setText("report-digest", report.report_digest || "-");
  renderPeriodControls(report);
  renderBlockers(report.workstreams.filter((item) => item.work_status === "blocked"));
  renderHandoffWorkstreams(report.workstreams);
  renderActivityBreakdown(report.workstreams);
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
  setText("selection-consistency", "-");
  setText("activity-coverage", "-");
  setText("selection-digest", "-");
  setText("report-digest", "-");
  currentPeriodSelection = null;
  renderPeriodControls();
  renderBlockers([]);
  renderHandoffWorkstreams([]);
  renderActivityBreakdown([]);
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
  const empty = document.getElementById("workstream-empty");
  const existingButtons = new Map(
    Array.from(workstreamList.querySelectorAll(".workstream-list-item"))
      .map((button) => [button.dataset.scopeId, button])
  );
  empty.hidden = workstreams.length !== 0;
  workstreamSearchField.hidden = workstreams.length <= workstreamSearchThreshold;
  if (workstreamSearchField.hidden && currentWorkstreamQuery) {
    resetWorkstreamSearch();
  }
  for (const item of workstreams) {
    const selected = item.workstream.scope_id === currentWorkstreamScope;
    const button = existingButtons.get(item.workstream.scope_id) || document.createElement("button");
    if (!button.classList.contains("workstream-list-item")) {
      button.className = "workstream-list-item";
      button.type = "button";
      button.addEventListener("click", () => {
        activateWorkstream(button.dataset.scopeId);
      });
    }
    button.dataset.scopeId = item.workstream.scope_id;
    button.dataset.searchText = normalizeWorkstreamQuery(`${item.workstream.title}\n${item.workstream.scope_id}`);
    button.setAttribute("aria-current", String(selected));

    const header = document.createElement("span");
    header.className = "workstream-list-item-header";
    const title = document.createElement("strong");
    title.textContent = item.workstream.title;
    header.append(title, statusBadge(item.work_status));

    const scope = document.createElement("code");
    scope.textContent = item.workstream.scope_id;
    button.replaceChildren(header, scope);
    workstreamList.appendChild(button);
    existingButtons.delete(item.workstream.scope_id);
  }
  for (const button of existingButtons.values()) {
    button.remove();
  }
  applyWorkstreamFilter();
}

function normalizeWorkstreamQuery(value) {
  return value.trim().toLocaleLowerCase();
}

function resetWorkstreamSearch() {
  currentWorkstreamQuery = "";
  workstreamSearchInput.value = "";
}

function visibleWorkstreamButtons() {
  return Array.from(workstreamList.querySelectorAll(".workstream-list-item:not([hidden])"));
}

function applyWorkstreamFilter() {
  const buttons = Array.from(workstreamList.querySelectorAll(".workstream-list-item"));
  for (const button of buttons) {
    button.hidden = currentWorkstreamQuery !== "" && !button.dataset.searchText.includes(currentWorkstreamQuery);
  }
  workstreamFilterEmpty.hidden = buttons.length === 0 || visibleWorkstreamButtons().length !== 0;
  scheduleWorkstreamLayoutUpdate();
}

function scheduleWorkstreamLayoutUpdate() {
  if (pendingWorkstreamLayoutFrame !== null) {
    window.cancelAnimationFrame(pendingWorkstreamLayoutFrame);
  }
  pendingWorkstreamLayoutFrame = window.requestAnimationFrame(() => {
    pendingWorkstreamLayoutFrame = null;
    updateWorkstreamSwitcherControls();
    centerSelectedWorkstream();
  });
}

function updateWorkstreamSwitcherControls() {
  const buttons = visibleWorkstreamButtons();
  const selectedIndex = buttons.findIndex((button) => button.getAttribute("aria-current") === "true");
  const overflowing = workstreamList.scrollWidth > workstreamListPanel.clientWidth + 1;
  workstreamSwitcherNavigation.hidden = !overflowing;
  workstreamSwitcherToolbar.hidden = workstreamSearchField.hidden && workstreamSwitcherNavigation.hidden;
  if (buttons.length === 0) {
    workstreamPosition.textContent = translate("noMatchingWorkstreams");
  } else if (selectedIndex === -1) {
    workstreamPosition.textContent = translate("workstreamMatchCount", {count: formatNumber(buttons.length)});
  } else {
    workstreamPosition.textContent = translate("workstreamPosition", {
      current: formatNumber(selectedIndex + 1),
      total: formatNumber(buttons.length)
    });
  }
  previousWorkstreamButton.disabled = editorDirty || revisionSaving || buttons.length === 0 || selectedIndex === 0;
  nextWorkstreamButton.disabled = editorDirty
    || revisionSaving
    || buttons.length === 0
    || selectedIndex === buttons.length - 1;
}

function activateAdjacentWorkstream(direction) {
  const buttons = visibleWorkstreamButtons();
  if (buttons.length === 0) {
    return;
  }
  const selectedIndex = buttons.findIndex((button) => button.getAttribute("aria-current") === "true");
  const targetIndex = selectedIndex === -1
    ? (direction < 0 ? buttons.length - 1 : 0)
    : selectedIndex + direction;
  if (targetIndex < 0 || targetIndex >= buttons.length) {
    return;
  }
  activateWorkstream(buttons[targetIndex].dataset.scopeId);
}

function centerSelectedWorkstream() {
  const selected = workstreamList.querySelector('.workstream-list-item[aria-current="true"]:not([hidden])');
  if (selected === null || currentProject === null) {
    return;
  }
  const selectionKey = `${currentProject.project_id}:${selected.dataset.scopeId}`;
  if (selectionKey === lastCenteredWorkstreamKey) {
    return;
  }
  selected.scrollIntoView({block: "nearest", inline: "center"});
  lastCenteredWorkstreamKey = selectionKey;
}

function renderHandoffContents(workstreams) {
  const list = document.getElementById("handoff-content-list");
  list.replaceChildren();
  const item = workstreams.find((candidate) => candidate.workstream.scope_id === currentWorkstreamScope)
    || workstreams[0]
    || null;
  if (item === null) {
    const empty = document.createElement("p");
    empty.className = "handoff-content-empty";
    empty.textContent = translate("noWorkstreams");
    list.appendChild(empty);
    renderHandoffEditorActions(null, null);
    return;
  }

  const card = document.createElement("div");
  card.className = "handoff-content-card";
  const header = document.createElement("header");
  const identity = document.createElement("div");
  const title = document.createElement("h4");
  title.className = "handoff-content-title";
  title.textContent = item.workstream.title;
  const scope = document.createElement("code");
  scope.textContent = item.workstream.scope_id;
  identity.append(title, scope);
  const state = document.createElement("div");
  state.className = "handoff-snapshot-state";
  state.appendChild(statusBadge(item.work_status));
  const reference = document.createElement("code");
  reference.textContent = `${translate("exactRevision")}: ${formatArtifactRef(item.handoff_ref)}`;
  state.appendChild(reference);
  header.append(identity, state);
  card.appendChild(header);

  const draft = handoffDrafts.get(item.workstream.scope_id) || null;
  if (draft !== null) {
    card.appendChild(createHandoffEditor(item, draft));
  } else {
    if (item.content === null) {
      const note = document.createElement("p");
      note.className = "handoff-content-empty";
      note.textContent = translate("firstRevisionNote");
      card.appendChild(note);
    }
    for (const field of handoffFieldDefinitions(item)) {
      appendHandoffBlock(card, field);
    }
  }
  list.appendChild(card);
  renderHandoffEditorActions(item, draft);
  syncHandoffEditingState();
}

function handoffFieldDefinitions(item, values = draftFromWorkstream(item)) {
  return [
    {name: "objective", label: translate("objective"), kind: "text", rows: 3, value: values.objective},
    {name: "state", label: translate("currentState"), kind: "lines", rows: 5, value: values.state},
    {name: "disposition", label: translate("disposition"), kind: "disposition", value: values.disposition},
    {name: "nextAction", label: translate("nextAction"), kind: "text", rows: 3, value: values.nextAction},
    {name: "omissions", label: translate("omissions"), kind: "lines", rows: 3, value: values.omissions}
  ];
}

function appendHandoffBlock(card, field) {
  const section = document.createElement("section");
  section.className = "handoff-content-block";
  section.dataset.field = field.name;
  const header = document.createElement("header");
  const heading = document.createElement("h4");
  heading.textContent = field.label;
  header.appendChild(heading);
  section.appendChild(header);
  appendHandoffFieldValue(section, field);
  card.appendChild(section);
}

function appendHandoffFieldValue(section, field) {
  const value = field.value.trim();
  if (field.kind === "disposition") {
    section.appendChild(statusBadge(field.value));
    return;
  }
  const entries = field.kind === "lines" ? normalizedLines(value) : [value];
  if (!value || entries.length === 0) {
    const empty = document.createElement("p");
    empty.className = "handoff-block-empty";
    empty.textContent = translate("emptyField");
    section.appendChild(empty);
    return;
  }
  if (field.kind === "lines") {
    const list = document.createElement("ul");
    for (const entry of entries) {
      const row = document.createElement("li");
      row.textContent = entry;
      list.appendChild(row);
    }
    section.appendChild(list);
    return;
  }
  const paragraph = document.createElement("p");
  paragraph.textContent = value;
  section.appendChild(paragraph);
}

function createHandoffEditor(item, draft) {
  const form = document.createElement("form");
  form.className = "handoff-content-editor";
  form.id = "handoff-content-editor";
  if (item.content === null) {
    const note = document.createElement("p");
    note.className = "handoff-content-empty";
    note.textContent = translate("firstRevisionNote");
    form.appendChild(note);
  }
  for (const field of handoffFieldDefinitions(item)) {
    const section = document.createElement("section");
    section.className = "handoff-content-block is-editing";
    section.dataset.field = field.name;
    const label = document.createElement("label");
    const text = document.createElement("span");
    text.textContent = field.kind === "lines" && field.name === "state"
      ? translate("currentStateLines")
      : field.kind === "lines" && field.name === "omissions"
        ? translate("omissionLines")
        : field.label;
    const control = createHandoffControl(
      field,
      draft.values[field.name],
      `${item.workstream.scope_id}-${field.name}`
    );
    label.htmlFor = control.id;
    const update = () => {
      draft.values[field.name] = control.value;
      draft.dirty = handoffDraftChanged(draft);
      syncHandoffEditingState();
    };
    control.addEventListener("input", update);
    control.addEventListener("change", update);
    label.appendChild(text);
    section.append(label, control);
    form.appendChild(section);
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void saveHandoffRevision(item, draft);
  });
  return form;
}

function createHandoffControl(field, value, id) {
  let control;
  if (field.kind === "disposition") {
    control = document.createElement("select");
    for (const status of ["continuable", "blocked", "complete"]) {
      const option = document.createElement("option");
      option.value = status;
      option.textContent = statusLabel(status);
      option.selected = status === value;
      control.appendChild(option);
    }
  } else {
    control = document.createElement("textarea");
    control.rows = field.rows;
    control.maxLength = 8192;
    control.value = value;
  }
  control.id = id.replaceAll(/[^a-zA-Z0-9_-]/g, "-");
  control.setAttribute("aria-label", field.label);
  return control;
}

function startHandoffEdit(item) {
  const values = draftFromWorkstream(item);
  handoffDrafts.clear();
  handoffDrafts.set(item.workstream.scope_id, {
    initialValues: {...values},
    values: {...values},
    dirty: false
  });
  syncHandoffEditingState();
  handoffSaveStatus.textContent = "";
  handoffSaveStatus.classList.remove("is-error");
  renderHandoffContents(currentReport?.workstreams || []);
  document.querySelector(".handoff-content-editor :is(textarea, select)")?.focus();
}

function cancelHandoffEdit() {
  if (currentWorkstreamScope === null || revisionSaving) {
    return;
  }
  handoffDrafts.delete(currentWorkstreamScope);
  pendingHandoffAttempts.delete(currentWorkstreamScope);
  syncHandoffEditingState();
  renderHandoffContents(currentReport?.workstreams || []);
  editHandoffContentButton.focus();
}

function handoffDraftChanged(draft) {
  return Object.keys(draft.values).some((name) => draft.values[name] !== draft.initialValues[name]);
}

function renderHandoffEditorActions(item, draft) {
  const available = item !== null;
  const editing = draft !== null;
  handoffEditorActions.hidden = !available;
  editHandoffContentButton.hidden = !available || editing;
  saveHandoffRevisionButton.hidden = !editing;
  cancelHandoffEditButton.hidden = !editing;
  if (available) {
    const editKey = item.content === null ? "createFirstRevision" : "editHandoffContent";
    editHandoffContentButton.textContent = translate(editKey);
    editHandoffContentButton.setAttribute(
      "aria-label",
      translate(item.content === null ? "createFirstRevision" : "editHandoffContentLabel")
    );
  }
  editHandoffContentButton.disabled = revisionSaving || reportLoading;
  saveHandoffRevisionButton.disabled = revisionSaving || !draft?.dirty;
  cancelHandoffEditButton.disabled = revisionSaving;
}

function syncHandoffEditingState() {
  editorDirty = handoffDrafts.size > 0;
  projectSearchInput.disabled = reportLoading || revisionSaving || editorDirty;
  workstreamSearchInput.disabled = revisionSaving || editorDirty;
  document.querySelectorAll(".workstream-list-item").forEach((button) => {
    button.disabled = revisionSaving || editorDirty;
  });
  const activeDraft = currentWorkstreamScope === null ? null : handoffDrafts.get(currentWorkstreamScope) || null;
  const item = currentReport?.workstreams.find(
    (candidate) => candidate.workstream.scope_id === currentWorkstreamScope
  ) || null;
  renderHandoffEditorActions(item, activeDraft);
  updateWorkstreamSwitcherControls();
  updateAutoRefreshStatus();
}

function renderActivityBreakdown(workstreams) {
  const list = document.getElementById("activity-breakdown-list");
  list.replaceChildren();
  if (workstreams.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = translate("noWorkstreams");
    list.appendChild(empty);
    return;
  }
  for (const item of workstreams) {
    const row = document.createElement("div");
    row.className = "activity-breakdown-item";
    const identity = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.workstream.title;
    const scope = document.createElement("code");
    scope.textContent = item.workstream.scope_id;
    identity.append(title, scope);
    const reporting = document.createElement("span");
    reporting.textContent = statusLabel(item.reporting_status);
    const count = document.createElement("strong");
    count.textContent = formatNumber(item.observed_activity_count);
    row.append(identity, reporting, count);
    list.appendChild(row);
  }
}

function renderHandoffWorkstreams(workstreams) {
  const selected = workstreams.some((item) => item.workstream.scope_id === currentWorkstreamScope)
    ? currentWorkstreamScope
    : workstreams[0]?.workstream.scope_id || null;
  if (workstreams.length === 0) {
    currentWorkstreamScope = null;
    renderWorkstreams([]);
    renderHandoffContents([]);
    renderRevisionHistory(null);
    renderContinuity(null);
    return;
  }

  activateWorkstream(selected);
}

function activateWorkstream(scopeId) {
  const item = currentReport?.workstreams.find((candidate) => candidate.workstream.scope_id === scopeId) || null;
  if (item === null) {
    return;
  }
  const scopeChanged = currentWorkstreamScope !== scopeId;
  currentWorkstreamScope = scopeId;
  rememberSelectedWork(currentProject.project_id, scopeId);
  if (scopeChanged) {
    handoffSaveStatus.textContent = "";
    handoffSaveStatus.classList.remove("is-error");
  }
  renderWorkstreams(currentReport.workstreams);
  renderHandoffContents(currentReport.workstreams);
  renderRevisionHistory(item);
  renderContinuity(item.continuity || null);
}

function artifactRefsEqual(left, right) {
  return left !== null
    && right !== null
    && left.family === right.family
    && left.artifact_id === right.artifact_id
    && left.revision === right.revision;
}

function formatArtifactRef(reference) {
  return reference === null ? "-" : `${reference.family}/${reference.artifact_id}@${reference.revision}`;
}

function draftFromWorkstream(item) {
  const content = item.content;
  if (content === null) {
    return {objective: "", state: "", disposition: "continuable", nextAction: "", omissions: ""};
  }
  return {
    objective: content.objective,
    state: content.state.map((statement) => statement.text).join("\n"),
    disposition: content.disposition,
    nextAction: content.next_action?.text || "",
    omissions: content.omissions.map((omission) => omission.text).join("\n")
  };
}

function normalizedLines(value) {
  return [...new Set(value.split("\n").map((line) => line.trim()).filter(Boolean))];
}

async function saveHandoffRevision(item, draft) {
  if (currentWorkstreamScope === null || revisionSaving || currentWorkstreamScope !== item.workstream.scope_id) {
    return;
  }
  const values = {...draft.values};
  const objective = values.objective.trim();
  const state = normalizedLines(values.state);
  if (!objective || state.length === 0) {
    setHandoffSaveStatus("editorRequired", {}, true);
    return;
  }

  const scopeId = currentWorkstreamScope;
  const handoff = {
    schema: "powercontext.current-work-handoff.v1",
    trust: "untrusted_input",
    objective,
    state: state.map(declaredClaim),
    disposition: values.disposition,
    next_action: values.nextAction.trim() ? declaredClaim(values.nextAction.trim()) : null,
    omissions: normalizedLines(values.omissions)
  };
  const attempt = pendingHandoffAttempt(scopeId, handoff);
  setRevisionSaving(true);
  setHandoffSaveStatus("savingRevision");
  try {
    const prepared = attempt.prepared || await requestJson(
      "/v1/work/handoffs/prepare-current",
      readServerToken(),
      {scope_id: scopeId, source_id: attempt.sourceId, handoff}
    );
    attempt.prepared = prepared;
    const committed = await requestJson("/v1/handoff/commit", readServerToken(), {
      scope_id: scopeId,
      handoff: prepared.handoff
    });
    pendingHandoffAttempts.delete(scopeId);
    handoffDrafts.delete(scopeId);
    syncHandoffEditingState();
    await loadReport(readServerToken(), currentProject.project_id, {selectedScopeId: scopeId});
    setHandoffSaveStatus("revisionSaved", {revision: committed.reference.revision});
  } catch (error) {
    if (error.status === 401) {
      handleRequestError(error);
      return;
    }
    setHandoffSaveStatus("revisionSaveFailed", {status: error.status || "network"}, true);
  } finally {
    setRevisionSaving(false);
  }
}

function setHandoffSaveStatus(key, values = {}, isError = false) {
  handoffSaveStatus.textContent = translate(key, values);
  handoffSaveStatus.classList.toggle("is-error", isError);
}

function pendingHandoffAttempt(scopeId, handoff) {
  const fingerprint = JSON.stringify(handoff);
  const existing = pendingHandoffAttempts.get(scopeId);
  if (existing?.fingerprint === fingerprint) {
    return existing;
  }
  const attempt = {
    fingerprint,
    sourceId: revisionSourceId("handoff-boundary"),
    prepared: null
  };
  pendingHandoffAttempts.set(scopeId, attempt);
  return attempt;
}

function declaredClaim(text) {
  return {text, basis: "declared", evidence: []};
}

function setRevisionSaving(saving) {
  revisionSaving = saving;
  document.querySelectorAll(
    ".handoff-content-card :is(button, input, select, textarea), .handoff-editor-actions button"
  ).forEach((element) => {
    element.disabled = saving;
  });
  projectSearchInput.disabled = saving || reportLoading || editorDirty;
  syncHandoffEditingState();
}

function revisionSourceId(kind) {
  const unique = typeof window.crypto?.randomUUID === "function"
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `handoff-report:${kind}:${unique}`;
}

function renderRevisionHistory(item) {
  const list = document.getElementById("handoff-revision-history");
  const summary = document.getElementById("revision-history-summary");
  list.replaceChildren();
  const history = Array.isArray(item?.handoff_history) ? item.handoff_history : [];
  if (history.length === 0) {
    summary.textContent = translate("revisionHistoryEmpty");
    return;
  }
  const total = Number(item.handoff_revision_count) || history.length;
  summary.textContent = translate(
    item.handoff_history_truncated ? "revisionHistoryTruncated" : "revisionHistorySummary",
    {shown: history.length, total}
  );
  for (const revision of [...history].reverse()) {
    const current = artifactRefsEqual(revision.reference, item.handoff_ref);
    const row = document.createElement("li");
    row.className = "revision-history-item";
    row.dataset.current = String(current);
    if (current) {
      row.setAttribute("aria-current", "true");
    }

    const header = document.createElement("div");
    header.className = "revision-history-item-header";
    const reference = document.createElement("code");
    reference.textContent = `@${revision.reference.revision}`;
    const disposition = document.createElement("span");
    disposition.className = "revision-history-disposition";
    disposition.textContent = statusLabel(revision.disposition);
    header.append(reference, disposition);
    if (current) {
      const currentLabel = document.createElement("strong");
      currentLabel.textContent = translate("revisionCurrent");
      header.appendChild(currentLabel);
    }

    const objective = document.createElement("p");
    objective.className = "revision-history-objective";
    objective.textContent = revision.objective_excerpt;
    row.append(header, objective);
    if (revision.next_action_excerpt) {
      const nextAction = document.createElement("p");
      nextAction.className = "revision-history-next";
      nextAction.textContent = translate("revisionNextAction", {value: revision.next_action_excerpt});
      row.appendChild(nextAction);
    }
    const counts = document.createElement("p");
    counts.className = "revision-history-counts";
    counts.textContent = translate("revisionCounts", {
      state: formatNumber(revision.state_count),
      omissions: formatNumber(revision.omission_count)
    });
    row.appendChild(counts);
    list.appendChild(row);
  }
}

function renderContinuity(continuity) {
  const timeline = document.getElementById("continuity-timeline");
  const note = document.getElementById("continuity-note");
  const transferState = document.getElementById("transfer-state-status");
  const outcomeState = document.getElementById("outcome-state-status");
  timeline.replaceChildren();
  continuityTimelineToggle.hidden = true;
  if (continuity === null) {
    transferState.textContent = "-";
    outcomeState.textContent = "-";
    transferState.removeAttribute("data-state");
    outcomeState.removeAttribute("data-state");
    note.textContent = translate("timelineEmpty");
    return;
  }
  transferState.textContent = statusLabel(continuity.coverage.transfer_state);
  transferState.dataset.state = continuity.coverage.transfer_state;
  outcomeState.textContent = statusLabel(continuity.coverage.outcome_state);
  outcomeState.dataset.state = continuity.coverage.outcome_state;
  const expanded = expandedContinuityScopes.has(continuity.scope_id);
  const hiddenEventCount = Math.max(0, continuity.events.length - continuityTimelineRecentLimit);
  const visibleEvents = expanded
    ? continuity.events
    : continuity.events.slice(-continuityTimelineRecentLimit);
  for (const event of visibleEvents) {
    timeline.appendChild(renderContinuityEvent(continuity.scope_id, event, timeline));
  }
  if (hiddenEventCount > 0) {
    continuityTimelineToggle.hidden = false;
    continuityTimelineToggle.setAttribute("aria-expanded", String(expanded));
    continuityTimelineToggle.textContent = translate(
      expanded ? "timelineShowRecent" : "timelineShowEarlier",
      expanded ? {count: continuityTimelineRecentLimit} : {count: hiddenEventCount}
    );
  }
  const notes = [];
  if (continuity.events.length === 0) {
    notes.push(translate("timelineEmpty"));
  }
  if (continuity.truncated) {
    notes.push(translate("timelineTruncated", {
      count: continuity.events.length,
      total: continuity.total_event_count
    }));
  }
  if (continuity.invalid_record_count > 0) {
    notes.push(translate("timelineInvalid", {count: continuity.invalid_record_count}));
  }
  note.textContent = notes.join(" ");
}

function renderContinuityEvent(scopeId, event, timeline) {
  const item = document.createElement("li");
  item.dataset.kind = event.kind;
  item.dataset.status = event.status;

  const disclosure = document.createElement("details");
  disclosure.className = "continuity-event";
  disclosure.open = openContinuityEvents.get(scopeId) === event.position;
  item.dataset.open = String(disclosure.open);

  const toggle = document.createElement("summary");
  const position = document.createElement("span");
  position.className = "continuity-position";
  position.textContent = `#${event.position}`;
  const heading = document.createElement("span");
  heading.className = "continuity-event-heading";
  const title = document.createElement("strong");
  title.className = "continuity-event-title";
  title.textContent = statusLabel(event.kind);
  const detail = event.summary || (event.actor ? translate("eventActor", {actor: event.actor}) : "");
  const preview = document.createElement("span");
  preview.className = "continuity-event-preview";
  preview.textContent = detail || translate("eventNoDetails");
  heading.append(title, preview);
  const status = document.createElement("span");
  status.className = "continuity-event-status";
  status.textContent = statusLabel(event.status);
  const arrow = document.createElement("span");
  arrow.className = "continuity-event-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "↘";
  toggle.append(position, heading, status, arrow);

  const body = document.createElement("div");
  body.className = "continuity-event-body";
  const metadata = document.createElement("dl");
  metadata.className = "continuity-event-meta";
  if (event.actor && event.summary) {
    appendContinuityMeta(metadata, translate("receiverIdentity"), event.actor);
  }
  if (event.selected_revision !== null) {
    appendContinuityMeta(metadata, translate("eventRevision"), formatArtifactRef(event.selected_revision), {code: true});
  }
  if (event.handoff_receipt_ref !== null) {
    appendContinuityMeta(metadata, translate("eventReceipt"), formatSourceRef(event.handoff_receipt_ref), {code: true});
  }
  if (event.receiver_checks !== null) {
    appendContinuityMeta(metadata, translate("eventReceiverChecks"), formatReceiverChecks(event.receiver_checks));
  }
  appendContinuityMeta(metadata, translate("eventSchema"), event.record_schema, {code: true});
  appendContinuityMeta(metadata, translate("eventSource"), formatSourceRef(event.source_ref), {code: true});
  body.appendChild(metadata);

  disclosure.append(toggle, body);
  disclosure.addEventListener("toggle", () => {
    item.dataset.open = String(disclosure.open);
    if (disclosure.open) {
      openContinuityEvents.set(scopeId, event.position);
      for (const other of timeline.querySelectorAll("details[open]")) {
        if (other !== disclosure) {
          other.open = false;
        }
      }
    } else if (openContinuityEvents.get(scopeId) === event.position) {
      openContinuityEvents.delete(scopeId);
    }
  });
  item.appendChild(disclosure);
  return item;
}

function appendContinuityMeta(metadata, labelText, value, {code = false} = {}) {
  const item = document.createElement("div");
  const label = document.createElement("dt");
  label.textContent = labelText;
  const detail = document.createElement("dd");
  const content = document.createElement(code ? "code" : "span");
  content.textContent = value;
  detail.appendChild(content);
  item.append(label, detail);
  metadata.appendChild(item);
}

function formatSourceRef(reference) {
  const sourceType = reference.source_type || reference.name;
  return `${sourceType}/${reference.source_id}`;
}

function formatReceiverChecks(checks) {
  return [
    `${translate("liveStateCheck")}: ${statusLabel(checks.live_state)}`,
    `${translate("capabilityCheck")}: ${statusLabel(checks.capability)}`,
    `${translate("authorizationCheck")}: ${statusLabel(checks.authorization)}`
  ].join(" / ");
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
  previewRetryButton.disabled = busy;
  refreshButton.disabled = busy;
  downloadButton.disabled = busy;
  applyCustomPeriodButton.disabled = busy;
  periodStartInput.disabled = busy;
  periodEndInput.disabled = busy;
  periodButtons.forEach((button) => {
    button.disabled = busy;
  });
  projectSearchInput.disabled = busy || revisionSaving || editorDirty;
  if (busy) {
    closeProjectOptions({restoreSelection: true});
  }
}

function startAutoRefresh() {
  if (autoRefreshTimer === null) {
    autoRefreshTimer = window.setInterval(() => {
      void autoRefreshReport();
    }, autoRefreshIntervalMilliseconds);
  }
  updateAutoRefreshStatus();
}

function stopAutoRefresh() {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  autoRefreshStatus.textContent = "";
  autoRefreshStatus.dataset.state = "inactive";
}

async function autoRefreshReport() {
  const token = readServerToken();
  if (document.hidden || reportLoading || token === null || currentProject === null) {
    return;
  }
  if (editorDirty || revisionSaving) {
    updateAutoRefreshStatus();
    return;
  }
  await loadReport(token, currentProject.project_id, {background: true});
}

function updateAutoRefreshStatus() {
  if (autoRefreshTimer === null) {
    return;
  }
  if (editorDirty) {
    setAutoRefreshStatus("editing");
  } else if (revisionSaving) {
    setAutoRefreshStatus("busy");
  } else {
    setAutoRefreshStatus("active");
  }
}

function setAutoRefreshStatus(state) {
  const translationKeys = {
    active: "autoRefreshActive",
    busy: "autoRefreshBusy",
    editing: "autoRefreshEditing",
    failed: "autoRefreshFailed",
    refreshing: "autoRefreshing",
    updated: "autoRefreshUpdated"
  };
  autoRefreshStatus.dataset.state = state;
  autoRefreshStatus.textContent = translate(translationKeys[state]);
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

function rememberSelectedWork(projectId, scopeId) {
  try {
    sessionStorage.setItem(selectedWorkKey, JSON.stringify([projectId, scopeId]));
  } catch (error) {
    // Work selection remains valid for the current render.
  }
}

function readSelectedWorkLocation() {
  try {
    const value = JSON.parse(sessionStorage.getItem(selectedWorkKey));
    return Array.isArray(value) && value.length === 2 && value.every((part) => typeof part === "string")
      ? {projectId: value[0], scopeId: value[1]}
      : null;
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
