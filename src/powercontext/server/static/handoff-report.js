"use strict";

import {
  clearServerToken,
  fetchWithBearer,
  readServerToken,
  storeServerToken
} from "./auth.js";
import {formatDateRange, resolvePeriodSelection, validateDateRange} from "./handoff-period.js";

const themeKey = "powercontext.dashboard.theme";
const localeKey = "powercontext.dashboard.locale";
const selectedProjectKey = "powercontext.handoff-report.project";
const autoRefreshIntervalMilliseconds = 5_000;
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
    handoffContentsSubtitle: "Objective, current state, next action, and known omissions for continuation",
    objective: "Objective",
    currentState: "Current state",
    omissions: "Known omissions",
    noOmissions: "No known omissions were declared.",
    noCommittedHandoff: "No committed Handoff is available for this Workstream.",
    handoffWorkbench: "Handoff workspace",
    handoffWorkbenchSubtitle: "Edit before sending, preflight before accepting, and keep the result loop visible",
    selectedWorkstream: "Selected Workstream",
    senderStep: "Sender",
    editBeforeSend: "Edit before sending",
    editBeforeSendNote: "Nothing is committed until you send this card.",
    currentStateLines: "Current state, one item per line",
    disposition: "Disposition",
    omissionLines: "Known omissions, one item per line",
    sendHandoff: "Send Handoff",
    receiverStep: "Receiver",
    preflightTitle: "Automatic preflight",
    preflightNote: "Citation availability is checked automatically and stays separate from the receiver's live-state confirmations.",
    receiverChecks: "Receiver checks",
    liveStateCheck: "Live workspace",
    capabilityCheck: "Capability",
    authorizationCheck: "Authorization",
    notChecked: "Not checked",
    confirmed: "Confirmed",
    mismatch: "Mismatch",
    insufficient: "Insufficient",
    receiverIdentity: "Receiver identity",
    receiverMessage: "Clarification or decline reason",
    acceptHandoff: "Accept and continue",
    requestClarification: "Request clarification",
    declineHandoff: "Decline",
    handoffDecision: "Handoff decision",
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
    preflightRunning: "Checking",
    preflightReady: "References readable",
    preflightBlocked: "Evidence blocked",
    preflightEmpty: "No Handoff",
    preflightFailed: "Check failed",
    preflightStale: "Handoff changed",
    preflightReadySummary: "Exact {revision}. Citation availability: {available}/{total}. This does not verify current facts.",
    preflightBlockedSummary: "The Handoff resolved, but {count} evidence check(s) are unavailable. Acceptance is disabled.",
    preflightEmptySummary: "There is no committed Handoff to receive for this Workstream.",
    preflightFailedSummary: "Automatic preflight could not be completed.",
    preflightStaleSummary: "Latest now resolves to {revision}, which differs from the card in this report. Refresh before deciding.",
    editorRequired: "Objective and at least one current-state item are required.",
    sendingHandoff: "Preparing and committing the inspected card…",
    handoffSent: "Handoff committed. The report and preflight have been refreshed.",
    handoffSendFailed: "The Handoff could not be prepared (HTTP {status}). Retry keeps the same operation identity.",
    handoffCommitPending: "The boundary is prepared but not confirmed committed (HTTP {status}). Retry will commit the same exact Handoff.",
    receiverRequired: "Enter the receiver identity.",
    receiverMessageRequired: "Add a reason before requesting clarification or declining.",
    receiverChecksRequired: "Confirm live workspace, capability, and authorization before accepting.",
    recordingDecision: "Recording the exact preflight decision…",
    decisionRecorded: "Decision recorded. The continuity timeline has been refreshed.",
    decisionFailed: "The decision could not be recorded (HTTP {status}).",
    recordOutcome: "Record task outcome",
    recordOutcomeNote: "This result will link to the exact accepted Receipt shown above.",
    outcomeStatus: "Outcome status",
    outcomeSummary: "Result summary",
    outcomeObservation: "Observed result",
    outcomeRequired: "Add both a result summary and an observed result.",
    recordingOutcome: "Recording the result against the exact accepted Receipt…",
    outcomeRecorded: "Task outcome recorded against the accepted Receipt.",
    outcomeRecordFailed: "The task outcome could not be recorded (HTTP {status}).",
    timelineEmpty: "No high-level Work continuity records are available for this scope.",
    timelineTruncated: "Only the latest {count} of {total} continuity events are shown.",
    timelineInvalid: "{count} Work record(s) could not be read and were excluded.",
    autoRefreshActive: "Auto-refresh every 5 seconds",
    autoRefreshEditing: "Auto-refresh paused while this card has unsent changes.",
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
    updated: "Updated {value}",
    projectTab: "{title} ({projectId})",
    coverageCaptured: "Captured Activity is included through cursor {cursor}. Counts describe observed events, not completion percentage.",
    coverageNotConfigured: "Activity adapters are not configured. Missing Activity must not be read as no work occurring.",
    coverageUnavailable: "Activity coverage is unavailable for this report.",
    noProjects: "No Handoff Report Projects are configured.",
    authRejected: "The Server rejected this token.",
    requestFailed: "The Handoff Report request failed with HTTP {status}.",
    serverUnavailable: "The Server is unavailable.",
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
    periodSummary: "{preset} / {range} / {timezone}",
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
    state: "当前状态",
    next_action: "下一步",
    available: "可用",
    unavailable: "不可用",
    noWorkstreams: "该 Project 尚未登记 Workstream。",
    handoffContents: "Handoff 内容",
    handoffContentsSubtitle: "用于继续工作的目标、当前状态、下一步和已知缺失",
    objective: "目标",
    currentState: "当前状态",
    omissions: "已知缺失",
    noOmissions: "未声明已知缺失。",
    noCommittedHandoff: "该 Workstream 尚无已提交的 Handoff。",
    handoffWorkbench: "一屏交接工作台",
    handoffWorkbenchSubtitle: "发送前修改、接手前预检，并持续显示结果闭环",
    selectedWorkstream: "当前 Workstream",
    senderStep: "交接方",
    editBeforeSend: "发送前可修改",
    editBeforeSendNote: "点击发送前不会提交任何正式 Handoff。",
    currentStateLines: "当前状态，每行一项",
    disposition: "处置状态",
    omissionLines: "已知缺失，每行一项",
    sendHandoff: "发送交接",
    receiverStep: "接手方",
    preflightTitle: "自动接手预检",
    preflightNote: "系统自动检查引用可用性，并与接手方对实时状态的确认分开呈现。",
    receiverChecks: "接手方检查",
    liveStateCheck: "实时工作区",
    capabilityCheck: "能力",
    authorizationCheck: "授权",
    notChecked: "未检查",
    confirmed: "已确认",
    mismatch: "不匹配",
    insufficient: "不足",
    receiverIdentity: "接手方身份",
    receiverMessage: "补充说明或拒绝原因",
    acceptHandoff: "接手并继续",
    requestClarification: "要求补充",
    declineHandoff: "无法接手",
    handoffDecision: "交接选择",
    continuityTimeline: "连续性时间线",
    continuityOrderNote: "按 Source journal 位置排序，不代表墙上时钟时间。",
    revisionHistory: "Handoff Revision 历史",
    revisionHistorySummary: "共 {total} 个 Revision，按最新优先显示。",
    revisionHistoryTruncated: "共 {total} 个 Revision，显示最近 {shown} 个。",
    revisionHistoryEmpty: "该 Workstream 尚无已提交的 Handoff Revision。",
    revisionCurrent: "当前版本",
    revisionNextAction: "下一步：{value}",
    revisionCounts: "状态 {state} 项 / 缺失 {omissions} 项",
    transferState: "交接状态",
    outcomeState: "结果状态",
    preflightRunning: "检查中",
    preflightReady: "引用可读取",
    preflightBlocked: "Evidence 阻塞",
    preflightEmpty: "无 Handoff",
    preflightFailed: "检查失败",
    preflightStale: "Handoff 已变化",
    preflightReadySummary: "精确版本 {revision}。引用可用性：{available}/{total}；这不代表当前事实已经验证。",
    preflightBlockedSummary: "Handoff 已解析，但有 {count} 项 Evidence 不可用，因此不能直接接手。",
    preflightEmptySummary: "当前 Workstream 没有可接手的已提交 Handoff。",
    preflightFailedSummary: "自动接手预检未能完成。",
    preflightStaleSummary: "最新版本已变为 {revision}，与当前报告卡片不一致。请刷新后再选择。",
    editorRequired: "目标和至少一项当前状态不能为空。",
    sendingHandoff: "正在准备并提交已检查的交接卡…",
    handoffSent: "Handoff 已提交，报告和预检已刷新。",
    handoffSendFailed: "Handoff 准备失败（HTTP {status}）；重试会沿用同一个操作身份。",
    handoffCommitPending: "边界已准备，但尚未确认提交成功（HTTP {status}）；重试将提交同一个精确 Handoff。",
    receiverRequired: "请填写接手方身份。",
    receiverMessageRequired: "要求补充或无法接手时，请填写原因。",
    receiverChecksRequired: "接手前请确认实时工作区、能力和授权。",
    recordingDecision: "正在记录本次精确预检对应的选择…",
    decisionRecorded: "选择已记录，连续性时间线已刷新。",
    decisionFailed: "选择记录失败（HTTP {status}）。",
    recordOutcome: "记录任务结果",
    recordOutcomeNote: "该结果会精确关联上方已接受的 Receipt。",
    outcomeStatus: "结果状态",
    outcomeSummary: "结果摘要",
    outcomeObservation: "观察到的结果",
    outcomeRequired: "请填写结果摘要和观察到的结果。",
    recordingOutcome: "正在把结果记录到精确的已接受 Receipt…",
    outcomeRecorded: "任务结果已关联到已接受的 Receipt。",
    outcomeRecordFailed: "任务结果记录失败（HTTP {status}）。",
    timelineEmpty: "该 Scope 尚无高层 Work 连续性记录。",
    timelineTruncated: "仅显示最近 {count} 条，共有 {total} 条连续性事件。",
    timelineInvalid: "有 {count} 条 Work 记录无法读取，已明确排除。",
    autoRefreshActive: "每 5 秒自动刷新",
    autoRefreshEditing: "卡片存在未发送修改，自动刷新已暂停。",
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
    projectTab: "{title}（{projectId}）",
    coverageCaptured: "已纳入游标 {cursor} 之前捕获的 Activity。数量表示已观察事件，不代表完成百分比。",
    coverageNotConfigured: "Activity Adapter 尚未配置；缺少 Activity 不能解释为没有发生工作。",
    coverageUnavailable: "当前报告无法取得 Activity 覆盖信息。",
    noProjects: "尚未配置 Handoff Report Project。",
    authRejected: "Server 拒绝了该 Token。",
    requestFailed: "Handoff Report 请求失败（HTTP {status}）。",
    serverUnavailable: "Server 无法访问。",
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
const reportShell = document.getElementById("handoff-report");
const reportError = document.getElementById("report-error");
const projectTabs = document.getElementById("project-tabs");
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
const themeToggle = document.getElementById("theme-toggle");
const languageToggle = document.getElementById("language-toggle");
const handoffWorkstream = document.getElementById("handoff-workstream");
const handoffEditor = document.getElementById("handoff-editor");
const handoffObjective = document.getElementById("handoff-objective");
const handoffState = document.getElementById("handoff-state");
const handoffDisposition = document.getElementById("handoff-disposition");
const handoffNextAction = document.getElementById("handoff-next-action");
const handoffOmissions = document.getElementById("handoff-omissions");
const handoffReceiver = document.getElementById("handoff-receiver");
const handoffReceiverMessage = document.getElementById("handoff-receiver-message");
const receiverLiveState = document.getElementById("receiver-live-state");
const receiverCapability = document.getElementById("receiver-capability");
const receiverAuthorization = document.getElementById("receiver-authorization");
const handoffChoiceButtons = Array.from(document.querySelectorAll("[data-handoff-choice]"));
const taskOutcomeForm = document.getElementById("task-outcome-form");
const taskOutcomeStatus = document.getElementById("task-outcome-status");
const taskOutcomeSummary = document.getElementById("task-outcome-summary");
const taskOutcomeObservation = document.getElementById("task-outcome-observation");
let currentLocale = document.documentElement.lang === "zh" ? "zh" : "en";
let currentProjects = [];
let currentProject = null;
let currentReport = null;
let currentAuthError = null;
let currentPeriodMode = "day";
let currentPeriodSelection = null;
let appliedCustomRange = null;
let currentWorkstreamScope = null;
let currentEditorBase = null;
let currentPreflight = null;
let receiverCheckTarget = null;
let preflightSequence = 0;
let workbenchBusy = false;
let reportLoading = false;
let editorDirty = false;
let autoRefreshTimer = null;
const workbenchDrafts = new Map();
const pendingHandoffAttempts = new Map();
let currentOutcomeReceipt = null;

themeToggle.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

languageToggle.addEventListener("click", () => {
  applyLocale(currentLocale === "en" ? "zh" : "en");
});

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

refreshButton.addEventListener("click", async () => {
  if (currentProject !== null) {
    await loadReport(readServerToken(), currentProject.project_id);
  }
});

downloadButton.addEventListener("click", async () => {
  await downloadMarkdown();
});

handoffWorkstream.addEventListener("change", () => {
  activateWorkstream(handoffWorkstream.value);
});

handoffEditor.addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendHandoffCard();
});

handoffEditor.querySelectorAll("textarea, select").forEach((element) => {
  element.addEventListener("input", markEditorDirty);
  element.addEventListener("change", markEditorDirty);
});

for (const button of handoffChoiceButtons) {
  button.addEventListener("click", async () => {
    await recordHandoffChoice(button.dataset.handoffChoice);
  });
}

for (const input of [receiverLiveState, receiverCapability, receiverAuthorization]) {
  input.addEventListener("change", updateHandoffChoiceState);
}

taskOutcomeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await recordTaskOutcome();
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

function applyTheme(theme, persist = true) {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try {
      localStorage.setItem(themeKey, theme);
    } catch (error) {
      // The selected theme still applies to the current page.
    }
  }
  updateControlLabels();
}

function applyLocale(locale, persist = true) {
  currentLocale = locale;
  document.documentElement.lang = locale;
  if (persist) {
    try {
      localStorage.setItem(localeKey, locale);
    } catch (error) {
      // The selected locale still applies to the current page.
    }
  }
  document.title = translate("pageTitle");
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = translate(element.dataset.i18n);
  });
  updateControlLabels();
  renderAuthError();
  if (currentProject !== null) {
    renderProjects(currentProjects, currentProject.project_id);
  }
  if (currentReport !== null) {
    renderReport(currentReport);
  } else {
    renderPeriodControls();
  }
  updateAutoRefreshStatus();
}

function updateControlLabels() {
  const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  themeToggle.setAttribute("aria-label", translate(nextTheme === "dark" ? "switchDark" : "switchLight"));
  themeToggle.setAttribute("title", translate(nextTheme === "dark" ? "switchDark" : "switchLight"));
  languageToggle.textContent = currentLocale === "en" ? translate("languageChinese") : "EN";
  languageToggle.setAttribute("aria-label", translate(currentLocale === "en" ? "switchChinese" : "switchEnglish"));
}

async function authenticate(token) {
  if (!token) {
    showLogin();
    return;
  }
  setBusy(true);
  try {
    currentProjects = await listProjects(token);
    storeServerToken(token);
    tokenInput.value = "";
    currentAuthError = null;
    authShell.hidden = true;
    reportShell.hidden = false;
    signOut.hidden = false;
    if (currentProjects.length === 0) {
      stopAutoRefresh();
      currentProject = null;
      currentReport = null;
      renderProjects([], "");
      showReportError("noProjects");
      clearReport();
      return;
    }
    const remembered = readSelectedProject();
    const selected = currentProjects.find((project) => project.project_id === remembered) || currentProjects[0];
    await loadReport(token, selected.project_id);
    startAutoRefresh();
  } catch (error) {
    handleRequestError(error);
  } finally {
    setBusy(false);
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

async function loadReport(token, projectId, {background = false} = {}) {
  if (reportLoading) {
    return false;
  }
  if (!token) {
    showLogin();
    return false;
  }
  saveCurrentDraft();
  reportLoading = true;
  if (background) {
    setAutoRefreshStatus("refreshing");
  } else {
    setBusy(true);
    clearReportError();
  }
  try {
    const project = currentProjects.find((item) => item.project_id === projectId) || currentProject;
    const periodSelection = resolveSelectedPeriod(project);
    const response = await requestJson("/v1/handoff-reports/get", token, {
      project_id: projectId,
      locale: currentLocale === "zh" ? "zh-CN" : "en",
      include_evidence_checks: false,
      format: "json",
      include_archived: false,
      download: false,
      period: periodSelection.period
    });
    if (response.report === null) {
      throw new Error("reportUnavailable");
    }
    currentProject = currentProjects.find((project) => project.project_id === projectId) || response.report.project;
    currentReport = response.report;
    currentPeriodSelection = periodSelection;
    rememberSelectedProject(projectId);
    renderProjects(currentProjects, projectId);
    renderReport(currentReport);
    if (background) {
      setAutoRefreshStatus("updated");
    }
    return true;
  } catch (error) {
    if (background) {
      if (error.status === 401) {
        handleRequestError(error);
      } else {
        setAutoRefreshStatus("failed");
      }
      return false;
    }
    if (error.message === "reportUnavailable") {
      showReportError("reportUnavailable");
    } else {
      handleRequestError(error);
    }
    return false;
  } finally {
    reportLoading = false;
    if (!background) {
      setBusy(false);
      updateAutoRefreshStatus();
    }
  }
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
  if (typeof error.status === "number") {
    showReportOrLoginError("requestFailed", {status: error.status});
    return;
  }
  showReportOrLoginError("serverUnavailable");
}

function showReportOrLoginError(key, values = {}) {
  if (readServerToken()) {
    showReportError(key, values);
  } else {
    showLogin(key, values);
  }
}

function showLogin(messageKey = "", values = {}) {
  stopAutoRefresh();
  currentAuthError = messageKey ? {key: messageKey, values} : null;
  renderAuthError();
  authShell.hidden = false;
  reportShell.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function renderAuthError() {
  authError.textContent = currentAuthError === null
    ? ""
    : translate(currentAuthError.key, currentAuthError.values);
}

function renderProjects(projects, selectedProjectId) {
  projectTabs.replaceChildren();
  for (const project of projects) {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "project-tab";
    tab.dataset.projectId = project.project_id;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(project.project_id === selectedProjectId));
    tab.textContent = translate("projectTab", {title: project.title, projectId: project.project_id});
    tab.addEventListener("click", async () => {
      if (project.project_id !== currentProject?.project_id) {
        await loadReport(readServerToken(), project.project_id);
      }
    });
    projectTabs.appendChild(tab);
  }
}

function renderReport(report) {
  authShell.hidden = true;
  reportShell.hidden = false;
  signOut.hidden = false;
  clearReportError();
  setText("project-name", report.project.title);
  setText("project-identity", `${report.project.project_key} / ${report.project.project_id}`);
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
  setText("selection-digest", report.selection_digest || "-");
  setText("report-digest", report.report_digest || "-");
  renderPeriodControls(report);
  renderBlockers(report.workstreams.filter((item) => item.work_status === "blocked"));
  renderWorkstreams(report.workstreams);
  renderHandoffContents(report.workstreams);
  renderHandoffWorkbench(report.workstreams);
}

function clearReport() {
  setText("project-name", translate("handoffReportTitle"));
  setText("project-identity", "");
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
  renderWorkstreams([]);
  renderHandoffContents([]);
  renderHandoffWorkbench([]);
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
    next.textContent = item.content?.next_action?.text || "-";
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
    appendContentSection(card, translate("nextAction"), [item.content.next_action?.text || "-"]);
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

function renderHandoffWorkbench(workstreams) {
  const empty = document.getElementById("handoff-workbench-empty");
  const content = document.getElementById("handoff-workbench-content");
  handoffWorkstream.replaceChildren();
  empty.hidden = workstreams.length !== 0;
  content.hidden = workstreams.length === 0;
  if (workstreams.length === 0) {
    currentWorkstreamScope = null;
    currentEditorBase = null;
    currentPreflight = null;
    renderRevisionHistory(null);
    renderContinuity(null);
    renderPreflight(null);
    return;
  }

  for (const item of workstreams) {
    const option = document.createElement("option");
    option.value = item.workstream.scope_id;
    option.textContent = `${item.workstream.title} (${item.workstream.scope_id})`;
    handoffWorkstream.appendChild(option);
  }
  const selected = workstreams.some((item) => item.workstream.scope_id === currentWorkstreamScope)
    ? currentWorkstreamScope
    : workstreams[0].workstream.scope_id;
  handoffWorkstream.value = selected;
  activateWorkstream(selected, {saveCurrent: false});
}

function activateWorkstream(scopeId, {saveCurrent = true} = {}) {
  if (saveCurrent && currentWorkstreamScope !== null && currentWorkstreamScope !== scopeId) {
    saveCurrentDraft();
  }
  const item = currentReport?.workstreams.find((candidate) => candidate.workstream.scope_id === scopeId) || null;
  if (item === null) {
    return;
  }
  const base = handoffBase(item);
  if (currentWorkstreamScope !== scopeId || currentEditorBase !== base) {
    const saved = workbenchDrafts.get(scopeId);
    if (saved?.dirty) {
      workbenchDrafts.set(scopeId, {...saved, base});
      writeEditorDraft(saved.values, {dirty: true});
    } else if (saved?.base === base) {
      writeEditorDraft(saved.values);
    } else {
      workbenchDrafts.delete(scopeId);
      writeEditorDraft(draftFromWorkstream(item));
    }
  }
  currentWorkstreamScope = scopeId;
  currentEditorBase = base;
  handoffWorkstream.value = scopeId;
  setText("handoff-workbench-scope", scopeId);
  clearWorkbenchFeedback("send-handoff-status");
  clearWorkbenchFeedback("handoff-choice-status");
  renderRevisionHistory(item);
  renderContinuity(item.continuity || null);
  if (preflightMatches(item)) {
    renderPreflight(currentPreflight);
  } else {
    void runPreflight(item);
  }
}

function handoffBase(item) {
  const reference = item.handoff_ref;
  if (reference === null) {
    return `${item.workstream.scope_id}:none`;
  }
  return `${item.workstream.scope_id}:${reference.family}:${reference.artifact_id}:${reference.revision}`;
}

function preflightMatches(item) {
  if (currentPreflight === null || currentPreflight.scope_id !== item.workstream.scope_id) {
    return false;
  }
  const selected = currentPreflight.selected_revision;
  const reference = item.handoff_ref;
  if (selected === null || reference === null) {
    return selected === null && reference === null;
  }
  return artifactRefsEqual(selected, reference);
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

function readEditorDraft() {
  return {
    objective: handoffObjective.value,
    state: handoffState.value,
    disposition: handoffDisposition.value,
    nextAction: handoffNextAction.value,
    omissions: handoffOmissions.value
  };
}

function writeEditorDraft(draft, {dirty = false} = {}) {
  handoffObjective.value = draft.objective;
  handoffState.value = draft.state;
  handoffDisposition.value = draft.disposition;
  handoffNextAction.value = draft.nextAction;
  handoffOmissions.value = draft.omissions;
  editorDirty = dirty;
  updateAutoRefreshStatus();
}

function markEditorDirty() {
  editorDirty = true;
  saveCurrentDraft();
  updateAutoRefreshStatus();
}

function saveCurrentDraft() {
  if (!editorDirty || currentWorkstreamScope === null || currentEditorBase === null) {
    return;
  }
  workbenchDrafts.set(currentWorkstreamScope, {
    base: currentEditorBase,
    dirty: true,
    values: readEditorDraft()
  });
}

function normalizedLines(value) {
  return [...new Set(value.split("\n").map((line) => line.trim()).filter(Boolean))];
}

async function sendHandoffCard() {
  if (currentWorkstreamScope === null || workbenchBusy) {
    return;
  }
  const draft = readEditorDraft();
  const objective = draft.objective.trim();
  const state = normalizedLines(draft.state);
  if (!objective || state.length === 0) {
    setWorkbenchFeedback("send-handoff-status", "editorRequired", {}, true);
    return;
  }

  const scopeId = currentWorkstreamScope;
  const handoff = {
    schema: "powercontext.current-work-handoff.v1",
    trust: "untrusted_input",
    objective,
    state: state.map(declaredClaim),
    disposition: draft.disposition,
    next_action: draft.nextAction.trim() ? declaredClaim(draft.nextAction.trim()) : null,
    omissions: normalizedLines(draft.omissions)
  };
  const attempt = pendingHandoffAttempt(scopeId, handoff);
  setWorkbenchBusy(true);
  setWorkbenchFeedback("send-handoff-status", "sendingHandoff");
  try {
    const prepared = attempt.prepared || await requestJson(
      "/v1/work/handoffs/prepare-current",
      readServerToken(),
      {scope_id: scopeId, source_id: attempt.sourceId, handoff}
    );
    attempt.prepared = prepared;
    await requestJson("/v1/handoff/commit", readServerToken(), {
      scope_id: scopeId,
      handoff: prepared.handoff
    });
    pendingHandoffAttempts.delete(scopeId);
    workbenchDrafts.delete(scopeId);
    currentPreflight = null;
    editorDirty = false;
    await loadReport(readServerToken(), currentProject.project_id);
    setWorkbenchFeedback("send-handoff-status", "handoffSent");
  } catch (error) {
    if (error.status === 401) {
      handleRequestError(error);
      return;
    }
    saveCurrentDraft();
    setWorkbenchFeedback(
      "send-handoff-status",
      attempt.prepared ? "handoffCommitPending" : "handoffSendFailed",
      {status: error.status || "network"},
      true
    );
  } finally {
    setWorkbenchBusy(false);
  }
}

function pendingHandoffAttempt(scopeId, handoff) {
  const fingerprint = JSON.stringify(handoff);
  const existing = pendingHandoffAttempts.get(scopeId);
  if (existing?.fingerprint === fingerprint) {
    return existing;
  }
  const attempt = {
    fingerprint,
    sourceId: workbenchSourceId("handoff-boundary"),
    prepared: null
  };
  pendingHandoffAttempts.set(scopeId, attempt);
  return attempt;
}

function declaredClaim(text) {
  return {text, basis: "declared", evidence: []};
}

async function runPreflight(item) {
  const sequence = ++preflightSequence;
  currentPreflight = null;
  renderPreflight(null, {running: true});
  updateHandoffChoiceState();
  try {
    const resolution = await requestJson("/v1/handoff/continue", readServerToken(), {
      scope_id: item.workstream.scope_id,
      selection: "latest"
    });
    if (sequence !== preflightSequence || currentWorkstreamScope !== item.workstream.scope_id) {
      return;
    }
    if (resolution.status === "resolved" && !artifactRefsEqual(resolution.selected_revision, item.handoff_ref)) {
      resetReceiverChecks(null);
      renderPreflight(null, {staleRevision: resolution.selected_revision});
      updateHandoffChoiceState();
      return;
    }
    resetReceiverChecks(resolution.status === "resolved" ? resolution.selected_revision : null);
    currentPreflight = resolution;
    renderPreflight(resolution);
  } catch (error) {
    if (sequence !== preflightSequence) {
      return;
    }
    if (error.status === 401) {
      handleRequestError(error);
      return;
    }
    renderPreflight(null, {failed: true});
  }
  updateHandoffChoiceState();
}

function renderPreflight(resolution, {running = false, failed = false, staleRevision = null} = {}) {
  const state = document.getElementById("preflight-state");
  const summary = document.getElementById("preflight-summary");
  const checks = document.getElementById("preflight-checks");
  checks.replaceChildren();
  if (running) {
    state.dataset.state = "idle";
    state.textContent = translate("preflightRunning");
    summary.textContent = translate("preflightRunning");
    return;
  }
  if (failed) {
    state.dataset.state = "error";
    state.textContent = translate("preflightFailed");
    summary.textContent = translate("preflightFailedSummary");
    return;
  }
  if (staleRevision !== null) {
    state.dataset.state = "blocked";
    state.textContent = translate("preflightStale");
    summary.textContent = translate("preflightStaleSummary", {revision: formatArtifactRef(staleRevision)});
    return;
  }
  if (resolution === null || resolution.status === "empty") {
    state.dataset.state = "blocked";
    state.textContent = translate("preflightEmpty");
    summary.textContent = translate("preflightEmptySummary");
    return;
  }

  const unavailable = resolution.evidence_checks.filter((check) => check.status === "unavailable");
  const available = resolution.evidence_checks.length - unavailable.length;
  const ready = unavailable.length === 0;
  state.dataset.state = ready ? "ready" : "blocked";
  state.textContent = translate(ready ? "preflightReady" : "preflightBlocked");
  summary.textContent = translate(
    ready ? "preflightReadySummary" : "preflightBlockedSummary",
    ready
      ? {
        revision: formatArtifactRef(resolution.selected_revision),
        available,
        total: resolution.evidence_checks.length
      }
      : {count: unavailable.length}
  );
  for (const check of resolution.evidence_checks) {
    const row = document.createElement("li");
    row.dataset.status = check.status;
    const label = document.createElement("span");
    label.textContent = check.state_index === null
      ? statusLabel(check.claim)
      : `${statusLabel(check.claim)} #${check.state_index + 1}`;
    const result = document.createElement("code");
    result.textContent = statusLabel(check.status);
    row.append(label, result);
    checks.appendChild(row);
  }
}

async function recordHandoffChoice(status) {
  if (workbenchBusy || currentWorkstreamScope === null || currentPreflight?.status !== "resolved") {
    return;
  }
  const receiver = handoffReceiver.value.trim();
  const message = handoffReceiverMessage.value.trim();
  if (!receiver) {
    setWorkbenchFeedback("handoff-choice-status", "receiverRequired", {}, true);
    handoffReceiver.focus();
    return;
  }
  if (status !== "accepted" && !message) {
    setWorkbenchFeedback("handoff-choice-status", "receiverMessageRequired", {}, true);
    handoffReceiverMessage.focus();
    return;
  }
  if (status === "accepted" && !receiverChecksConfirmed()) {
    setWorkbenchFeedback("handoff-choice-status", "receiverChecksRequired", {}, true);
    receiverLiveState.focus();
    return;
  }
  if (status === "accepted" && !preflightCanAccept(currentPreflight)) {
    return;
  }

  const scopeId = currentWorkstreamScope;
  setWorkbenchBusy(true);
  setWorkbenchFeedback("handoff-choice-status", "recordingDecision");
  try {
    const payload = {
      scope_id: scopeId,
      source_id: workbenchSourceId("handoff-receipt"),
      receiver,
      status,
      selection: "exact",
      receiver_checks: readReceiverChecks(),
      revision: currentPreflight.selected_revision
    };
    if (message) {
      payload.message = message;
    }
    await requestJson("/v1/work/handoffs/acknowledge", readServerToken(), payload);
    currentPreflight = null;
    receiverCheckTarget = "recorded";
    resetReceiverChecks(null);
    handoffReceiverMessage.value = "";
    await loadReport(readServerToken(), currentProject.project_id);
    setWorkbenchFeedback("handoff-choice-status", "decisionRecorded");
  } catch (error) {
    if (error.status === 401) {
      handleRequestError(error);
      return;
    }
    setWorkbenchFeedback("handoff-choice-status", "decisionFailed", {status: error.status || "network"}, true);
  } finally {
    setWorkbenchBusy(false);
  }
}

function preflightCanAccept(resolution) {
  return resolution.status === "resolved"
    && resolution.selected_revision !== null
    && resolution.evidence_checks.every((check) => check.status === "available")
    && receiverChecksConfirmed();
}

function readReceiverChecks() {
  return {
    live_state: receiverLiveState.value,
    capability: receiverCapability.value,
    authorization: receiverAuthorization.value
  };
}

function receiverChecksConfirmed() {
  const checks = readReceiverChecks();
  return checks.live_state === "confirmed"
    && checks.capability === "confirmed"
    && checks.authorization === "confirmed";
}

function resetReceiverChecks(reference) {
  const target = reference === null ? null : formatArtifactRef(reference);
  if (receiverCheckTarget === target) {
    return;
  }
  receiverCheckTarget = target;
  receiverLiveState.value = "not_checked";
  receiverCapability.value = "not_checked";
  receiverAuthorization.value = "not_checked";
  updateHandoffChoiceState();
}

function updateHandoffChoiceState() {
  const resolved = currentPreflight?.status === "resolved" && currentPreflight.selected_revision !== null;
  for (const button of handoffChoiceButtons) {
    const accepts = button.dataset.handoffChoice === "accepted";
    button.disabled = workbenchBusy || !resolved || (accepts && !preflightCanAccept(currentPreflight));
  }
}

function setWorkbenchBusy(busy) {
  workbenchBusy = busy;
  handoffWorkstream.disabled = busy;
  handoffEditor.querySelectorAll("button, input, select, textarea").forEach((element) => {
    element.disabled = busy;
  });
  handoffReceiver.disabled = busy;
  handoffReceiverMessage.disabled = busy;
  receiverLiveState.disabled = busy;
  receiverCapability.disabled = busy;
  receiverAuthorization.disabled = busy;
  taskOutcomeForm.querySelectorAll("button, select, textarea").forEach((element) => {
    element.disabled = busy;
  });
  updateHandoffChoiceState();
  updateAutoRefreshStatus();
}

function setWorkbenchFeedback(id, key, values = {}, isError = false) {
  const element = document.getElementById(id);
  element.textContent = translate(key, values);
  element.classList.toggle("is-error", isError);
}

function clearWorkbenchFeedback(id) {
  const element = document.getElementById(id);
  element.textContent = "";
  element.classList.remove("is-error");
}

function workbenchSourceId(kind) {
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
  if (continuity === null) {
    transferState.textContent = "-";
    outcomeState.textContent = "-";
    transferState.removeAttribute("data-state");
    outcomeState.removeAttribute("data-state");
    note.textContent = translate("timelineEmpty");
    renderTaskOutcomeForm(null);
    return;
  }
  transferState.textContent = statusLabel(continuity.coverage.transfer_state);
  transferState.dataset.state = continuity.coverage.transfer_state;
  outcomeState.textContent = statusLabel(continuity.coverage.outcome_state);
  outcomeState.dataset.state = continuity.coverage.outcome_state;
  for (const event of continuity.events) {
    const item = document.createElement("li");
    const position = document.createElement("span");
    position.className = "continuity-position";
    position.textContent = `#${event.position}`;
    const title = document.createElement("strong");
    title.className = "continuity-event-title";
    title.textContent = statusLabel(event.kind);
    const status = document.createElement("span");
    status.className = "continuity-event-status";
    status.textContent = statusLabel(event.status);
    item.append(position, title, status);
    const detail = event.summary || (event.actor ? translate("eventActor", {actor: event.actor}) : "");
    if (detail) {
      const summary = document.createElement("p");
      summary.className = "continuity-event-summary";
      summary.textContent = detail;
      item.appendChild(summary);
    }
    timeline.appendChild(item);
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
  renderTaskOutcomeForm(continuity);
}

function renderTaskOutcomeForm(continuity) {
  if (continuity?.coverage.outcome_state !== "awaiting_outcome") {
    taskOutcomeForm.hidden = true;
    currentOutcomeReceipt = null;
    return;
  }
  const receiptRef = continuity.coverage.active_receipt_ref;
  if (receiptRef === null) {
    taskOutcomeForm.hidden = true;
    currentOutcomeReceipt = null;
    return;
  }
  if (!sourceRefsEqual(currentOutcomeReceipt, receiptRef)) {
    taskOutcomeStatus.value = "succeeded";
    taskOutcomeSummary.value = "";
    taskOutcomeObservation.value = "";
    clearWorkbenchFeedback("task-outcome-feedback");
  }
  currentOutcomeReceipt = receiptRef;
  taskOutcomeForm.hidden = false;
}

function sourceRefsEqual(left, right) {
  return left !== null
    && right !== null
    && left.source_type === right.source_type
    && left.source_id === right.source_id;
}

async function recordTaskOutcome() {
  if (workbenchBusy || currentWorkstreamScope === null || currentOutcomeReceipt === null) {
    return;
  }
  const summary = taskOutcomeSummary.value.trim();
  const observation = taskOutcomeObservation.value.trim();
  if (!summary || !observation) {
    setWorkbenchFeedback("task-outcome-feedback", "outcomeRequired", {}, true);
    return;
  }
  const item = currentReport?.workstreams.find(
    (candidate) => candidate.workstream.scope_id === currentWorkstreamScope
  );
  const objective = item?.content?.objective || handoffObjective.value.trim();
  const scopeId = currentWorkstreamScope;
  setWorkbenchBusy(true);
  setWorkbenchFeedback("task-outcome-feedback", "recordingOutcome");
  try {
    await requestJson("/v1/work/outcomes/record", readServerToken(), {
      scope_id: scopeId,
      source_id: workbenchSourceId("task-outcome"),
      outcome: {
        schema: "powercontext.task-outcome.v1",
        trust: "untrusted_observation",
        objective,
        status: taskOutcomeStatus.value,
        summary,
        handoff_receipt_ref: {
          name: currentOutcomeReceipt.source_type,
          source_id: currentOutcomeReceipt.source_id
        },
        observations: [declaredClaim(observation)],
        checks: [],
        produced_artifacts: [],
        remaining_work: []
      }
    });
    await loadReport(readServerToken(), currentProject.project_id);
    setWorkbenchFeedback("handoff-choice-status", "outcomeRecorded");
  } catch (error) {
    if (error.status === 401) {
      handleRequestError(error);
      return;
    }
    setWorkbenchFeedback(
      "task-outcome-feedback",
      "outcomeRecordFailed",
      {status: error.status || "network"},
      true
    );
  } finally {
    setWorkbenchBusy(false);
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
  const key = status === "continuable" || status === "blocked" || status === "complete"
    ? status
    : status;
  return translate(key);
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
        locale: currentLocale === "zh" ? "zh-CN" : "en",
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
  projectTabs.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
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
  if (editorDirty || workbenchBusy) {
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
  } else if (workbenchBusy) {
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
    range: formatDateRange(selection.startDate, selection.endDate, localeTag()),
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

function translate(key, values = {}) {
  const template = translations[currentLocale][key] || translations.en[key] || key;
  return template.replace(/\{([a-zA-Z]+)\}/g, (match, name) => String(values[name] ?? match));
}

function localeTag() {
  return currentLocale === "zh" ? "zh-CN" : "en";
}

function formatNumber(value) {
  return new Intl.NumberFormat(localeTag()).format(value);
}

function formatSignedNumber(value) {
  return new Intl.NumberFormat(localeTag(), {signDisplay: "always"}).format(value);
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat(localeTag(), {dateStyle: "medium", timeStyle: "short"}).format(new Date(value));
}

const initialTheme = document.documentElement.dataset.theme === "dark"
  ? "dark"
  : (document.documentElement.dataset.theme === "light"
    ? "light"
    : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
applyLocale(currentLocale, false);
applyTheme(initialTheme, false);
authenticate(readServerToken());
