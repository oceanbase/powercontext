"use strict";

import {
  clearServerToken,
  fetchWithBearer,
  readServerToken,
  storeServerToken
} from "./auth.js?v=session-shell";
import {formatDateRange, resolvePeriodSelection, validateDateRange} from "./handoff-period.js";
import {createPageUi, createRequestGate} from "./page-ui.js?v=locale-complete";

const selectedProjectKey = "powercontext.handoff-report.project";
const selectedWorkKey = "powercontext.handoff-report.work";
const autoRefreshIntervalMilliseconds = 5_000;
const continuityTimelineRecentLimit = 6;
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
    selectedWorkstream: "Handoff work",
    workSwitcherHelp: "Choose any registered Workstream. Unsent drafts stay with each work.",
    chooseHandoffWork: "Choose work to hand off",
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
    timelineShowEarlier: "Show {count} earlier events",
    timelineShowRecent: "Show latest {count}",
    eventSource: "Source",
    eventRevision: "Handoff Revision",
    eventReceipt: "Receipt",
    eventReceiverChecks: "Receiver checks",
    eventSchema: "Record schema",
    eventNoDetails: "No additional details were recorded.",
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
    languageEnglish: "EN",
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
    reportPeriod: "报告周期",
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
    handoffContentsSubtitle: "用于继续工作的目标、当前状态、下一步和已知缺失",
    objective: "目标",
    currentState: "当前状态",
    omissions: "已知缺失",
    noOmissions: "未声明已知缺失。",
    noCommittedHandoff: "该工作项尚无已提交的交接记录。",
    handoffWorkbench: "一屏交接工作台",
    handoffWorkbenchSubtitle: "发送前修改、接手前预检，并持续显示结果闭环",
    selectedWorkstream: "交接工作",
    workSwitcherHelp: "可切换任意已登记的工作项，未发送的修改会分别保留。",
    chooseHandoffWork: "选择要交接的工作",
    senderStep: "交接方",
    editBeforeSend: "发送前可修改",
    editBeforeSendNote: "点击发送前不会提交任何正式交接记录。",
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
    preflightRunning: "检查中",
    preflightReady: "引用可读取",
    preflightBlocked: "证据阻塞",
    preflightEmpty: "无交接记录",
    preflightFailed: "检查失败",
    preflightStale: "交接记录已变化",
    preflightReadySummary: "精确版本 {revision}。引用可用性：{available}/{total}；这不代表当前事实已经验证。",
    preflightBlockedSummary: "交接记录已解析，但有 {count} 项证据不可用，因此不能直接接手。",
    preflightEmptySummary: "当前工作项没有可接手的已提交交接记录。",
    preflightFailedSummary: "自动接手预检未能完成。",
    preflightStaleSummary: "最新版本已变为 {revision}，与当前报告卡片不一致。请刷新后再选择。",
    editorRequired: "目标和至少一项当前状态不能为空。",
    sendingHandoff: "正在准备并提交已检查的交接卡…",
    handoffSent: "交接记录已提交，报告和预检已刷新。",
    handoffSendFailed: "交接记录准备失败（HTTP {status}）；重试会沿用同一个操作身份。",
    handoffCommitPending: "交接边界已准备，但尚未确认提交成功（HTTP {status}）；重试将提交同一个精确交接版本。",
    receiverRequired: "请填写接手方身份。",
    receiverMessageRequired: "要求补充或无法接手时，请填写原因。",
    receiverChecksRequired: "接手前请确认实时工作区、能力和授权。",
    recordingDecision: "正在记录本次精确预检对应的选择…",
    decisionRecorded: "选择已记录，连续性时间线已刷新。",
    decisionFailed: "选择记录失败（HTTP {status}）。",
    recordOutcome: "记录任务结果",
    recordOutcomeNote: "该结果会精确关联上方已接受的接手回执。",
    outcomeStatus: "结果状态",
    outcomeSummary: "结果摘要",
    outcomeObservation: "观察到的结果",
    outcomeRequired: "请填写结果摘要和观察到的结果。",
    recordingOutcome: "正在把结果记录到精确的已接受接手回执…",
    outcomeRecorded: "任务结果已关联到已接受的接手回执。",
    outcomeRecordFailed: "任务结果记录失败（HTTP {status}）。",
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
const autoRefreshStatus = document.getElementById("auto-refresh-status");
const signOut = document.getElementById("sign-out");
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
const continuityTimelineToggle = document.getElementById("continuity-timeline-toggle");
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
const expandedContinuityScopes = new Set();
const openContinuityEvents = new Map();
let currentOutcomeReceipt = null;
const ui = createPageUi(translations, ({userInitiated = false} = {}) => {
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

refreshButton.addEventListener("click", async () => {
  if (currentProject !== null) {
    await loadReport(readServerToken(), currentProject.project_id);
  }
});

downloadButton.addEventListener("click", async () => {
  await downloadMarkdown();
});

handoffWorkstream.addEventListener("change", async () => {
  const selected = currentHandoffWorks.find((item) => handoffWorkValue(item) === handoffWorkstream.value);
  if (selected === undefined) {
    return;
  }
  if (selected.project.project_id === currentProject?.project_id) {
    activateWorkstream(selected.workstream.scope_id);
    return;
  }
  await loadReport(readServerToken(), selected.project.project_id, {
    selectedScopeId: selected.workstream.scope_id
  });
});

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
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    void autoRefreshReport();
  }
});

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
      stopAutoRefresh();
      currentProject = null;
      currentReport = null;
      showPageStatus("noProjects", {}, true);
      return;
    }
    currentHandoffWorks = await listHandoffWorks(token, currentProjects);
    if (!request.isCurrent()) {
      return;
    }
    const rememberedWork = currentHandoffWorks.find((item) => handoffWorkValue(item) === readSelectedWork());
    const remembered = readSelectedProject();
    const rememberedProject = currentProjects.find((project) => project.project_id === remembered) || null;
    const selectedWork = rememberedWork
      || currentHandoffWorks.find((item) => item.project.project_id === rememberedProject?.project_id)
      || currentHandoffWorks[0]
      || null;
    const selectedProject = selectedWork?.project || rememberedProject || currentProjects[0];
    await loadReportData(token, selectedProject.project_id, request, {
      selectedScopeId: selectedWork?.workstream.scope_id || null
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
  return projects;
}

async function listHandoffWorks(token, projects) {
  const works = [];
  for (const project of projects) {
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
  }
  return works;
}

async function loadReport(token, projectId, {background = false, selectedScopeId = null} = {}) {
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
    clearReportError();
  }
  const request = beginReportRequest({busy: !background});
  try {
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
      renderProjectOptions(currentProjects, currentProject.project_id);
      renderHandoffWorkOptions(currentProject.project_id, currentWorkstreamScope);
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
    handoffWorkstream.disabled = currentHandoffWorks.length === 0 || workbenchBusy;
    request.finish();
    if (!background && request.isCurrent()) {
      updateAutoRefreshStatus();
    }
  }
}

async function loadReportData(token, projectId, request, {selectedScopeId = null} = {}) {
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
  currentProject = currentProjects.find((item) => item.project_id === projectId) || response.report.project;
  currentReport = response.report;
  currentPeriodSelection = periodSelection;
  if (selectedScopeId !== null) {
    currentWorkstreamScope = selectedScopeId;
  }
  rememberSelectedProject(projectId);
  renderProjectOptions(currentProjects, projectId);
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
  reportShell.hidden = false;
  signOut.hidden = false;
  showReportError(key, values);
}

function showLogin(messageKey = "", values = {}) {
  stopAutoRefresh();
  reportRequests.cancel();
  setBusy(false);
  reportLoading = false;
  currentProjects = [];
  currentHandoffWorks = [];
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

function renderHandoffWorkOptions(selectedProjectId, selectedScopeId) {
  handoffWorkstream.replaceChildren();
  const selectedValue = selectedScopeId === null
    ? null
    : handoffWorkValue({project: {project_id: selectedProjectId}, workstream: {scope_id: selectedScopeId}});
  if (selectedValue === null || !currentHandoffWorks.some((item) => handoffWorkValue(item) === selectedValue)) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    placeholder.textContent = translate("chooseHandoffWork");
    handoffWorkstream.appendChild(placeholder);
  }
  for (const project of currentProjects) {
    const works = currentHandoffWorks.filter((item) => item.project.project_id === project.project_id);
    if (works.length === 0) {
      continue;
    }
    const group = document.createElement("optgroup");
    group.label = project.title;
    for (const work of works) {
      const option = document.createElement("option");
      option.value = handoffWorkValue(work);
      option.selected = option.value === selectedValue;
      option.textContent = `${work.workstream.title} (${work.workstream.scope_id})`;
      option.dataset.projectId = project.project_id;
      option.dataset.scopeId = work.workstream.scope_id;
      group.appendChild(option);
    }
    handoffWorkstream.appendChild(group);
  }
  handoffWorkstream.disabled = currentHandoffWorks.length === 0 || reportLoading || workbenchBusy;
}

function handoffWorkValue(item) {
  return JSON.stringify([item.project.project_id, item.workstream.scope_id]);
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
  setText("selection-consistency", statusLabel(report.selection_consistency));
  setText("activity-coverage", statusLabel(report.coverage.activity_coverage));
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
  const selected = workstreams.some((item) => item.workstream.scope_id === currentWorkstreamScope)
    ? currentWorkstreamScope
    : workstreams[0]?.workstream.scope_id || null;
  renderHandoffWorkOptions(currentProject?.project_id || "", selected);
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
  handoffWorkstream.value = handoffWorkValue({
    project: {project_id: currentProject.project_id},
    workstream: {scope_id: scopeId}
  });
  rememberSelectedWork(currentProject.project_id, scopeId);
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
  handoffWorkstream.disabled = busy || reportLoading || currentHandoffWorks.length === 0;
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
  continuityTimelineToggle.hidden = true;
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
  renderTaskOutcomeForm(continuity);
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
  handoffWorkstream.disabled = busy || currentHandoffWorks.length === 0 || workbenchBusy;
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
    sessionStorage.setItem(selectedWorkKey, handoffWorkValue({
      project: {project_id: projectId},
      workstream: {scope_id: scopeId}
    }));
  } catch (error) {
    // Work selection remains valid for the current render.
  }
}

function readSelectedWork() {
  try {
    return sessionStorage.getItem(selectedWorkKey);
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
