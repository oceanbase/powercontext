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
    pageTitle: "PowerContext Review",
    dashboardTitle: "Overview",
    skillsTitle: "Skills",
    reviewTitle: "Review",
    handoffReportTitle: "Handoff Report",
    brandHomeLabel: "PowerContext Overview",
    primaryNavigation: "Primary navigation",
    maintainedBy: "Maintained by OceanBase.",
    signOut: "Sign out",
    switchDark: "Switch to dark mode",
    switchLight: "Switch to light mode",
    switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English",
    languageChinese: "中文",
    languageEnglish: "EN",
    authTitle: "Connect to PowerContext",
    authIntro: "Enter the bearer token configured for this PowerContext Server. The token stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    reviewInboxTitle: "Experience and Skill review",
    reviewIntro: "Inspect evidence, revise proposals, and make explicit decisions.",
    selectScope: "Scope",
    searchScopesPlaceholder: "Search by scope name or ID",
    scopeSearchCount: "{count} scopes",
    scopeSearchMatches: "{count} matching scopes",
    scopeSearchLimited: "Showing {shown} of {total} matching scopes",
    noMatchingScopes: "No scopes match this search.",
    reviewFilters: "Review filters",
    family: "Family",
    allFamilies: "All families",
    experience: "Experience",
    skill: "Skill",
    status: "Status",
    pending: "Pending",
    approved: "Approved",
    rejected: "Rejected",
    refresh: "Refresh",
    candidateQueue: "Candidates",
    loadedCandidates: "{count} loaded",
    noCandidates: "No Candidates match these filters.",
    noCandidatesHint: "Try another family or status.",
    loadMore: "Load more",
    candidateDetail: "Candidate detail",
    selectCandidate: "Select a Candidate to inspect it.",
    selectCandidateHint: "The full proposal and exact evidence references will appear here.",
    proposal: "Proposal",
    evidence: "Evidence",
    sourceReferences: "Source references",
    artifactReferences: "Artifact references",
    noSourceReferences: "No Source references",
    noArtifactReferences: "No Artifact references",
    lineage: "Lineage and decision",
    reason: "Proposal reason",
    target: "Target Artifact",
    resultArtifact: "Result Artifact",
    decisionReason: "Decision reason",
    managedSkillPublication: "Managed Skill delivery",
    publicationIntro: "Create a reviewed revision or publish the approved revision to an Agent Skill target.",
    createSkillRevision: "Create revision",
    createSkillRevisionTitle: "Create a new Skill revision",
    createSkillRevisionNote: "Describe the change evidence, then edit the Skill. Saving creates a Pending Candidate and leaves the approved revision unchanged.",
    changeEvidence: "Change evidence",
    basedOnSkillRevision: "Based on Skill revision {revision}",
    createCandidate: "Create Candidate",
    publishTarget: "Publish target",
    agentCodex: "Codex",
    agentClaudeCode: "Claude Code",
    installationUser: "User",
    installationProject: "Project",
    installationPlugin: "Plugin",
    noPublishTargets: "No writable Skill target is configured.",
    noPublishTargetsHint: "Enable managed publication on an explicit local Agent target.",
    standardPackageRequired: "This approved Skill predates standard package snapshots. Create and approve a package-backed revision before publishing.",
    publishedRevision: "Published revision",
    destination: "Destination",
    discovery: "Discovery",
    publishSkill: "Publish Skill",
    updateSkill: "Publish update",
    refreshDiscovery: "Refresh discovery",
    publishSkillCandidate: "Publish this managed Skill?",
    publishConfirmation: "Publish revision {revision} to {target}. Existing PowerContext-managed content may be safely updated; foreign or modified content is never overwritten.",
    projectionUnpublished: "Not published",
    projectionCurrent: "Current",
    projectionUpdateAvailable: "Update available",
    projectionConflict: "Target conflict",
    projectionDrifted: "Locally modified",
    projectionIncompatible: "Not compatible",
    projectionConflictHint: "The destination is occupied or a newer Revision is already present. PowerContext will not overwrite it.",
    projectionDriftedHint: "This PowerContext package was modified locally. Restore it or choose another target before publishing.",
    projectionIncompatibleHint: "Revise the Skill name or description to satisfy the selected Agent's package constraints, then approve a new Revision.",
    discoveryAvailable: "Available in the configured Agent target",
    discoveryUnavailable: "Package exists; discovery needs refresh",
    discoveryNotPublished: "Not yet available",
    publicationLoading: "Checking publication status...",
    publicationLoadFailed: "Publication status could not be loaded. HTTP {status}.",
    publicationSucceeded: "Managed Skill revision {revision} is published and discoverable.",
    publicationFailed: "The managed Skill could not be published. HTTP {status}.",
    publicationConflict: "The publication target changed or contains content PowerContext will not overwrite.",
    publishing: "Publishing Skill...",
    creatingSkillRevision: "Creating revision Candidate...",
    skillRevisionCreated: "A new Skill revision Candidate was created and is ready for review.",
    skillRevisionTargetChanged: "A newer Skill revision is already current. Keep this draft, then open the latest approved Skill and create its next revision.",
    notProvided: "Not provided",
    version: "Version {version}",
    situation: "Situation",
    action: "Action",
    outcome: "Outcome",
    lesson: "Lesson",
    name: "Name",
    description: "Description",
    instructions: "Instructions",
    validation: "Validation",
    packageContents: "Package contents",
    packageFiles: "Package files",
    packageLoading: "Loading exact package files...",
    packageBinary: "Binary file preview is unavailable.",
    packageLoadFailed: "The exact package could not be loaded.",
    revise: "Revise",
    approve: "Approve",
    reject: "Reject",
    reviseProposal: "Revise proposal",
    revisionEvidenceNote: "Evidence and lineage references remain unchanged.",
    editingVersion: "Editing version {version}",
    saveRevision: "Save revision",
    cancel: "Cancel",
    addValidation: "Add validation item",
    removeValidation: "Remove",
    validationItem: "Validation item {number}",
    approveCandidate: "Approve Candidate?",
    approveConfirmation: "This creates or updates the managed Artifact from the current proposal.",
    rejectCandidate: "Reject Candidate?",
    rejectConfirmation: "Record a clear reason for rejecting this proposal.",
    rejectionReason: "Rejection reason",
    resumeDraft: "Resume revision",
    discardDraft: "Discard draft",
    revisionSaved: "Revision saved as version {version}.",
    candidateApproved: "Candidate approved.",
    candidateRejected: "Candidate rejected.",
    draftConflict: "The Candidate changed while you were editing. The latest version is shown. Resume to apply your draft to it, or discard the draft.",
    candidateChanged: "The Candidate changed. The latest version is shown; review it before deciding again.",
    candidateTerminal: "The Candidate was already decided. Its latest state is shown.",
    candidateMissing: "The Candidate no longer exists. The queue has been refreshed.",
    unsupportedCandidate: "This Candidate shape is not supported by this page. Decisions are disabled.",
    requestFailed: "The Review request failed with HTTP {status}.",
    validationFailed: "Check the proposal fields and try again.",
    serverUnavailable: "The Server is unavailable.",
    authRejected: "The Server rejected this token.",
    retry: "Retry",
    noScopes: "There is no work to show here.",
    scopeUnavailable: "The selected scope is not available.",
    loading: "Loading...",
    loadingMore: "Loading more...",
    saving: "Saving...",
    deciding: "Recording decision...",
    queueLoadFailed: "The Candidate queue could not be loaded. HTTP {status}.",
    detailLoadFailed: "The Candidate detail could not be loaded. HTTP {status}."
  },
  zh: {
    pageTitle: "PowerContext 审核",
    dashboardTitle: "概览",
    skillsTitle: "技能",
    reviewTitle: "审核",
    handoffReportTitle: "交接报告",
    brandHomeLabel: "PowerContext 概览",
    primaryNavigation: "主导航",
    maintainedBy: "由 OceanBase 维护。",
    signOut: "退出",
    switchDark: "切换至深色模式",
    switchLight: "切换至浅色模式",
    switchChinese: "切换至中文",
    switchEnglish: "切换至英文",
    languageChinese: "中文",
    languageEnglish: "EN",
    authTitle: "连接 PowerContext",
    authIntro: "请输入 PowerContext 服务器配置的访问令牌。令牌仅保留在当前浏览器标签页。",
    tokenLabel: "服务器访问令牌",
    continue: "继续",
    reviewInboxTitle: "经验与技能审核",
    reviewIntro: "检查证据、修订提案，并作出明确决策。",
    selectScope: "作用域",
    searchScopesPlaceholder: "按作用域名称或标识符搜索",
    scopeSearchCount: "共 {count} 个作用域",
    scopeSearchMatches: "找到 {count} 个作用域",
    scopeSearchLimited: "显示 {total} 个匹配项中的前 {shown} 个",
    noMatchingScopes: "没有匹配的作用域。",
    reviewFilters: "审核筛选条件",
    family: "类型",
    allFamilies: "全部类型",
    experience: "经验",
    skill: "技能",
    status: "状态",
    pending: "待审核",
    approved: "已批准",
    rejected: "已拒绝",
    refresh: "刷新",
    candidateQueue: "候选列表",
    loadedCandidates: "已加载 {count} 项",
    noCandidates: "没有符合筛选条件的候选。",
    noCandidatesHint: "请尝试其他类型或状态。",
    loadMore: "加载更多",
    candidateDetail: "候选详情",
    selectCandidate: "请选择一项候选进行检查。",
    selectCandidateHint: "此处将显示完整提案和准确的证据引用。",
    proposal: "提案",
    evidence: "证据",
    sourceReferences: "数据源引用",
    artifactReferences: "制品引用",
    noSourceReferences: "无数据源引用",
    noArtifactReferences: "无制品引用",
    lineage: "沿袭关系与决策",
    reason: "提案原因",
    target: "目标制品",
    resultArtifact: "结果制品",
    decisionReason: "决策原因",
    managedSkillPublication: "受管技能交付",
    publicationIntro: "创建待审核的新修订，或将已批准修订发布到代理技能目录。",
    createSkillRevision: "创建新修订",
    createSkillRevisionTitle: "创建新的技能修订",
    createSkillRevisionNote: "请先说明本次修改的证据，再编辑技能内容。保存后会创建一项待审核候选，已批准修订保持不变。",
    changeEvidence: "修改证据",
    basedOnSkillRevision: "基于技能第 {revision} 版",
    createCandidate: "创建候选",
    publishTarget: "发布目标",
    agentCodex: "Codex",
    agentClaudeCode: "Claude Code",
    installationUser: "用户级",
    installationProject: "项目级",
    installationPlugin: "插件级",
    noPublishTargets: "未配置可写的技能目标。",
    noPublishTargetsHint: "请在一个明确的本地技能目录上启用受管发布。",
    standardPackageRequired: "这项已批准技能创建于标准技能包支持之前。请先创建并批准一个由完整技能包支持的新修订，再进行发布。",
    publishedRevision: "已发布修订",
    destination: "目标位置",
    discovery: "发现状态",
    publishSkill: "发布技能",
    updateSkill: "发布更新",
    refreshDiscovery: "刷新发现状态",
    publishSkillCandidate: "发布这项受管技能？",
    publishConfirmation: "将第 {revision} 版发布到 {target}。系统只会安全更新由 PowerContext 管理的内容，不会覆盖外部内容或已被本地修改的内容。",
    projectionUnpublished: "尚未发布",
    projectionCurrent: "已是当前版本",
    projectionUpdateAvailable: "有更新可发布",
    projectionConflict: "目标存在冲突",
    projectionDrifted: "已被本地修改",
    projectionIncompatible: "格式不兼容",
    projectionConflictHint: "目标已被占用或包含更高修订，系统不会覆盖它。",
    projectionDriftedHint: "该受管技能包已在本地被修改。请先恢复它，或选择其他目标。",
    projectionIncompatibleHint: "请修订技能名称或说明以满足目标格式要求，再批准新的修订。",
    discoveryAvailable: "已在配置的技能目录中可用",
    discoveryUnavailable: "技能包已存在，需要刷新发现状态",
    discoveryNotPublished: "尚不可用",
    publicationLoading: "正在检查发布状态……",
    publicationLoadFailed: "无法加载发布状态（HTTP {status}）。",
    publicationSucceeded: "受管技能第 {revision} 版已发布并可被发现。",
    publicationFailed: "无法发布受管技能（HTTP {status}）。",
    publicationConflict: "发布目标已发生变化，或包含系统不会覆盖的内容。",
    publishing: "正在发布技能……",
    creatingSkillRevision: "正在创建修订候选……",
    skillRevisionCreated: "新的技能修订候选已创建，可以开始审核。",
    skillRevisionTargetChanged: "当前已有更新的技能修订。请保留该草稿，打开最新的已批准技能后再创建下一修订。",
    notProvided: "未提供",
    version: "第 {version} 版",
    situation: "情境",
    action: "行动",
    outcome: "结果",
    lesson: "经验总结",
    name: "名称",
    description: "说明",
    instructions: "使用指引",
    validation: "验证条件",
    packageContents: "技能包内容",
    packageFiles: "技能包文件",
    packageLoading: "正在加载精确技能包文件……",
    packageBinary: "二进制文件不提供预览。",
    packageLoadFailed: "无法加载精确技能包。",
    revise: "修订",
    approve: "批准",
    reject: "拒绝",
    reviseProposal: "修订提案",
    revisionEvidenceNote: "证据与沿袭引用保持不变。",
    editingVersion: "正在编辑第 {version} 版",
    saveRevision: "保存修订",
    cancel: "取消",
    addValidation: "添加验证条件",
    removeValidation: "移除",
    validationItem: "验证条件 {number}",
    approveCandidate: "批准这项候选？",
    approveConfirmation: "此操作将依据当前提案创建或更新受管理的制品。",
    rejectCandidate: "拒绝这项候选？",
    rejectConfirmation: "请记录拒绝该提案的明确原因。",
    rejectionReason: "拒绝原因",
    resumeDraft: "继续修订",
    discardDraft: "放弃草稿",
    revisionSaved: "修订已保存为第 {version} 版。",
    candidateApproved: "候选已批准。",
    candidateRejected: "候选已拒绝。",
    draftConflict: "你编辑期间候选已发生变化。当前显示最新内容。继续修订可将草稿应用到最新内容，也可以放弃草稿。",
    candidateChanged: "候选已发生变化。当前显示最新内容，请重新检查后再作决定。",
    candidateTerminal: "该候选已完成决策。当前显示其最新状态。",
    candidateMissing: "该候选已不存在，列表已刷新。",
    unsupportedCandidate: "该候选的数据结构暂不受此页面支持，决策操作已禁用。",
    requestFailed: "审核请求失败（HTTP {status}）。",
    validationFailed: "请检查提案字段后重试。",
    serverUnavailable: "服务器无法访问。",
    authRejected: "服务器拒绝了该访问令牌。",
    retry: "重试",
    noScopes: "这里还没有可查看的工作。",
    scopeUnavailable: "选中的作用域不可用。",
    loading: "正在加载……",
    loadingMore: "正在加载更多……",
    saving: "正在保存……",
    deciding: "正在记录决策……",
    queueLoadFailed: "无法加载候选列表（HTTP {status}）。",
    detailLoadFailed: "无法加载候选详情（HTTP {status}）。"
  }
};

class ReviewRequestError extends Error {
  constructor(status, code = "", details = null) {
    super(`Review request failed with HTTP ${status}`);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const authShell = document.getElementById("auth-shell");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const tokenInput = document.getElementById("token");
const pageStatus = document.getElementById("page-status");
const pageStatusMessage = document.getElementById("page-status-message");
const pageStatusRetry = document.getElementById("page-status-retry");
const reviewInbox = document.getElementById("review-inbox");
const signOut = document.getElementById("sign-out");
const scopeCombobox = document.getElementById("review-scope-combobox");
const scopeSearchInput = document.getElementById("review-scope-search");
const scopeOptions = document.getElementById("review-scope-options");
const scopeSearchStatus = document.getElementById("review-scope-search-status");
const familyFilter = document.getElementById("review-family-filter");
const statusFilter = document.getElementById("review-status-filter");
const refreshButton = document.getElementById("review-refresh");
const liveStatus = document.getElementById("review-live-status");
const queueCaption = document.getElementById("review-queue-caption");
const candidateList = document.getElementById("review-list");
const emptyState = document.getElementById("review-empty");
const loadMoreButton = document.getElementById("review-load-more");
const detailEmpty = document.getElementById("review-detail-empty");
const detailContent = document.getElementById("review-detail-content");
const detailFamily = document.getElementById("review-detail-family");
const candidateTitle = document.getElementById("review-candidate-title");
const candidateId = document.getElementById("review-candidate-id");
const detailStatus = document.getElementById("review-detail-status");
const detailVersion = document.getElementById("review-detail-version");
const alertBox = document.getElementById("review-alert");
const conflictActions = document.getElementById("review-conflict-actions");
const resumeDraftButton = document.getElementById("review-resume-draft");
const discardDraftButton = document.getElementById("review-discard-draft");
const proposalFields = document.getElementById("review-proposal-fields");
const packageSection = document.getElementById("review-package");
const packageStatus = document.getElementById("review-package-status");
const packageFiles = document.getElementById("review-package-files");
const packagePath = document.getElementById("review-package-path");
const packagePreview = document.getElementById("review-package-preview");
const sourceRefs = document.getElementById("review-source-refs");
const artifactRefs = document.getElementById("review-artifact-refs");
const lineageFields = document.getElementById("review-lineage-fields");
const publicationSection = document.getElementById("review-publication");
const publicationState = document.getElementById("review-publication-state");
const publicationStatus = document.getElementById("review-publication-status");
const publicationEmpty = document.getElementById("review-publication-empty");
const publicationContent = document.getElementById("review-publication-content");
const publicationTarget = document.getElementById("review-publication-target");
const publishedRevision = document.getElementById("review-published-revision");
const publicationDestination = document.getElementById("review-publication-destination");
const publicationDiscovery = document.getElementById("review-publication-discovery");
const createSkillRevisionButton = document.getElementById("review-create-skill-revision");
const publishSkillButton = document.getElementById("review-publish-skill");
const reviewActions = document.getElementById("review-actions");
const editButton = document.getElementById("review-edit");
const approveButton = document.getElementById("review-approve");
const rejectButton = document.getElementById("review-reject");
const revisionForm = document.getElementById("review-revision-form");
const revisionTitle = document.getElementById("review-revision-title");
const revisionNote = document.getElementById("review-revision-note");
const revisionFields = document.getElementById("review-form-fields");
const draftVersion = document.getElementById("review-draft-version");
const saveRevisionButton = document.getElementById("review-save-revision");
const cancelRevisionButton = document.getElementById("review-cancel-revision");
const approveDialog = document.getElementById("review-approve-dialog");
const confirmApproveButton = document.getElementById("review-confirm-approve");
const rejectDialog = document.getElementById("review-reject-dialog");
const rejectForm = document.getElementById("review-reject-form");
const rejectReason = document.getElementById("review-reject-reason");
const cancelRejectButton = document.getElementById("review-cancel-reject");
const publishDialog = document.getElementById("review-publish-dialog");
const publishConfirmation = document.getElementById("review-publish-confirmation");
const confirmPublishButton = document.getElementById("review-confirm-publish");
const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const reviewDeepLink = readReviewDeepLink();

let scopes = [];
let candidates = [];
let nextCursor = null;
let selectedCandidate = null;
let selectedCandidateId = "";
let currentScopeId = "";
let currentAuthError = null;
let currentPageStatus = null;
let currentNotice = null;
let draft = null;
let conflictDraft = null;
let projectionView = null;
let projectionLoading = false;
let packageManifest = null;
let packageLoadingDigest = "";
let busy = false;
let scopeActiveIndex = -1;

const scopeOptionRenderLimit = 50;

const scopeRequests = createRequestGate();
const listRequests = createRequestGate();
const detailRequests = createRequestGate();
const actionRequests = createRequestGate();
const projectionRequests = createRequestGate();
const packageRequests = createRequestGate();
const ui = createPageUi(translations, () => {
  renderAuthError();
  renderPageStatus();
  renderScopeCombobox();
  renderFilters();
  renderQueue();
  renderDetail();
  renderNotice();
});
const {formatNumber, translate} = ui;

scopeSearchInput.addEventListener("focus", () => {
  if (scopeOptions.hidden) {
    scopeSearchInput.value = "";
  }
  openScopeOptions();
});

scopeSearchInput.addEventListener("input", () => {
  scopeActiveIndex = -1;
  renderScopeOptionsList();
  openScopeOptions();
});

scopeSearchInput.addEventListener("keydown", (event) => {
  handleScopeSearchKeydown(event);
});

scopeCombobox.addEventListener("focusout", (event) => {
  if (!scopeCombobox.contains(event.relatedTarget)) {
    closeScopeOptions({restoreSelection: true});
  }
});

familyFilter.addEventListener("change", () => {
  resetQueue();
  void loadCandidates(false);
});

statusFilter.addEventListener("change", () => {
  resetQueue();
  void loadCandidates(false);
});

refreshButton.addEventListener("click", () => {
  resetQueue();
  void loadCandidates(false);
});

loadMoreButton.addEventListener("click", () => {
  void loadCandidates(true);
});

pageStatusRetry.addEventListener("click", () => {
  void authenticate(readServerToken(), currentScopeId);
});

authForm.addEventListener("submit", (event) => {
  event.preventDefault();
  authError.textContent = "";
  void authenticate(tokenInput.value);
});

signOut.addEventListener("click", () => {
  clearServerToken();
  tokenInput.value = "";
  showLogin();
});

editButton.addEventListener("click", () => {
  if (isEditableCandidate(selectedCandidate)) {
    startRevision(selectedCandidate.proposal, selectedCandidate.version);
  }
});

cancelRevisionButton.addEventListener("click", () => {
  draft = null;
  revisionForm.hidden = true;
  renderDetail();
});

revisionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  void saveRevision();
});

approveButton.addEventListener("click", () => {
  if (canDecide(selectedCandidate)) {
    approveDialog.showModal();
  }
});

confirmApproveButton.addEventListener("click", (event) => {
  event.preventDefault();
  approveDialog.close();
  void decideCandidate("approve");
});

rejectButton.addEventListener("click", () => {
  if (canDecide(selectedCandidate)) {
    rejectReason.value = "";
    rejectDialog.showModal();
    rejectReason.focus();
  }
});

cancelRejectButton.addEventListener("click", () => {
  rejectDialog.close();
});

rejectForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!rejectForm.reportValidity()) {
    return;
  }
  rejectDialog.close();
  void decideCandidate("reject", rejectReason.value.trim());
});

publicationTarget.addEventListener("change", () => {
  renderPublication();
});

createSkillRevisionButton.addEventListener("click", () => {
  if (isPublishableCandidate(selectedCandidate)) {
    startSkillRevision(selectedCandidate);
  }
});

publishSkillButton.addEventListener("click", () => {
  const target = selectedProjectionTarget();
  if (!selectedCandidate?.result_artifact || !target || !canPublishProjection(target)) {
    return;
  }
  publishConfirmation.textContent = translate("publishConfirmation", {
    revision: selectedCandidate.result_artifact.revision,
    target: `${agentLabel(target.agent_kind)} · ${target.target_id}`
  });
  publishDialog.showModal();
});

confirmPublishButton.addEventListener("click", (event) => {
  event.preventDefault();
  publishDialog.close();
  void publishSelectedSkill();
});

resumeDraftButton.addEventListener("click", () => {
  if (!selectedCandidate || !conflictDraft) {
    return;
  }
  const preservedDraft = conflictDraft;
  const proposal = structuredClone(preservedDraft.proposal);
  conflictDraft = null;
  currentNotice = null;
  startRevision(proposal, selectedCandidate.version, preservedDraft.mode);
});

discardDraftButton.addEventListener("click", () => {
  conflictDraft = null;
  currentNotice = null;
  renderNotice();
});

async function authenticate(token, preferredScopeId = "") {
  if (authenticationRequired && !token) {
    showLogin();
    return;
  }
  if (authenticationRequired) {
    storeServerToken(token);
  }
  tokenInput.value = "";
  currentAuthError = null;
  const request = scopeRequests.start();
  scopeSearchInput.disabled = true;
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
    scopes = await response.json();
    if (!request.isCurrent()) {
      return;
    }
    if (scopes.length === 0) {
      showPageStatus("noScopes");
      return;
    }
    const selectedScopeId = scopes.some((scope) => scope.scope_id === preferredScopeId)
      ? preferredScopeId
      : scopes[0].scope_id;
    currentScopeId = selectedScopeId;
    showReview();
    renderScopeCombobox();
    if (reviewDeepLink.family === "experience" || reviewDeepLink.family === "skill") {
      familyFilter.value = reviewDeepLink.family;
    }
    if (["pending", "approved", "rejected"].includes(reviewDeepLink.status)) {
      statusFilter.value = reviewDeepLink.status;
    }
    renderFilters();
    resetQueue();
    await loadCandidates(false, reviewDeepLink.candidate);
    if (
      reviewDeepLink.action === "create-revision"
      && selectedCandidate?.candidate_id === reviewDeepLink.candidate
    ) {
      startSkillRevision(selectedCandidate);
      reviewDeepLink.action = "";
    }
  } catch (error) {
    if (request.isCurrent()) {
      showPageStatus("serverUnavailable", {}, true);
    }
  } finally {
    if (request.isCurrent()) {
      scopeSearchInput.disabled = false;
    }
  }
}

async function loadCandidates(append, preferredCandidateId = "") {
  if (!currentScopeId || busy) {
    return;
  }
  const request = listRequests.start();
  const cursor = append ? nextCursor : null;
  setBusy(true, append ? "loadingMore" : "loading");
  try {
    const body = {
      scope_id: currentScopeId,
      status: statusFilter.value,
      limit: 50
    };
    if (familyFilter.value) {
      body.family = familyFilter.value;
    }
    if (cursor) {
      body.cursor = cursor;
    }
    const page = await requestJson("/v1/artifact-candidates/list", body);
    if (!request.isCurrent()) {
      return;
    }
    candidates = append ? candidates.concat(page.candidates) : page.candidates;
    nextCursor = page.next_cursor;
    currentNotice = null;
    let preferred = candidates.find((candidate) => candidate.candidate_id === preferredCandidateId) || null;
    if (!append && preferredCandidateId && !preferred) {
      try {
        const candidate = await requestJson("/v1/artifact-candidates/get", {
          scope_id: currentScopeId,
          candidate_id: preferredCandidateId
        });
        if (
          candidate.status === statusFilter.value
          && (!familyFilter.value || candidate.family === familyFilter.value)
        ) {
          candidates.unshift(candidate);
          preferred = candidate;
        }
      } catch (error) {
        if (!(error instanceof ReviewRequestError) || error.status !== 404) {
          throw error;
        }
      }
    }
    renderQueue();
    if (!append || !selectedCandidateId) {
      const first = preferred || candidates[0];
      if (first) {
        await selectCandidate(first.candidate_id);
      } else {
        clearDetail();
      }
    }
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    setNotice(error instanceof ReviewRequestError ? "queueLoadFailed" : "serverUnavailable", {
      status: error.status
    });
    renderQueue();
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
    }
  }
}

async function selectCandidate(id) {
  selectedCandidateId = id;
  draft = null;
  conflictDraft = null;
  revisionForm.hidden = true;
  projectionRequests.cancel();
  projectionView = null;
  projectionLoading = false;
  currentNotice = null;
  renderQueue();
  const request = detailRequests.start();
  setBusy(true, "loading");
  try {
    const candidate = await requestJson("/v1/artifact-candidates/get", {
      scope_id: currentScopeId,
      candidate_id: id
    });
    if (!request.isCurrent() || selectedCandidateId !== id) {
      return;
    }
    selectedCandidate = candidate;
    replaceCandidate(candidate);
    renderQueue();
    renderDetail();
    await loadProjectionStatus(candidate);
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 404) {
      removeCandidate(id);
      await selectFirstAvailable();
      setNotice("candidateMissing");
      return;
    }
    setNotice(error instanceof ReviewRequestError ? "detailLoadFailed" : "serverUnavailable", {
      status: error.status
    });
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
    }
  }
}

async function loadProjectionStatus(candidate) {
  if (!isPublishableCandidate(candidate)) {
    projectionView = null;
    projectionLoading = false;
    renderPublication();
    return;
  }
  const request = projectionRequests.start();
  projectionLoading = true;
  projectionView = null;
  renderPublication();
  try {
    const view = await requestJson("/dashboard/skill-projections/status", {
      scope_id: currentScopeId,
      candidate_id: candidate.candidate_id,
      artifact: candidate.result_artifact
    });
    if (!request.isCurrent() || selectedCandidateId !== candidate.candidate_id) {
      return;
    }
    projectionView = view;
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    setNotice(error instanceof ReviewRequestError ? "publicationLoadFailed" : "serverUnavailable", {
      status: error.status
    });
  } finally {
    if (request.isCurrent()) {
      projectionLoading = false;
      renderPublication();
    }
  }
}

async function publishSelectedSkill() {
  const candidate = selectedCandidate;
  const target = selectedProjectionTarget();
  if (!isPublishableCandidate(candidate) || !target || !canPublishProjection(target)) {
    return;
  }
  const request = projectionRequests.start();
  let refreshAfterConflict = false;
  setBusy(true, "publishing");
  try {
    const view = await requestJson("/dashboard/skill-projections/publish", {
      scope_id: currentScopeId,
      candidate_id: candidate.candidate_id,
      artifact: candidate.result_artifact,
      target_id: target.target_id
    });
    if (!request.isCurrent() || selectedCandidateId !== candidate.candidate_id) {
      return;
    }
    projectionView = view;
    setNotice("publicationSucceeded", {revision: candidate.result_artifact.revision}, "success");
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 409) {
      setNotice("publicationConflict");
      refreshAfterConflict = true;
    } else {
      setNotice(error instanceof ReviewRequestError ? "publicationFailed" : "serverUnavailable", {
        status: error.status
      });
    }
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
      renderPublication();
      if (refreshAfterConflict) {
        void loadProjectionStatus(candidate);
      }
    }
  }
}

async function saveRevision() {
  const createsSkillRevision = draft?.mode === "create-skill-revision";
  const canSave = createsSkillRevision
    ? isPublishableCandidate(selectedCandidate)
    : canDecide(selectedCandidate);
  if (!canSave || !revisionForm.reportValidity()) {
    return;
  }
  const proposal = collectProposal();
  if (!proposal) {
    setNotice("validationFailed");
    return;
  }
  const changeEvidence = createsSkillRevision
    ? revisionForm.elements.namedItem("changeEvidence").value.trim()
    : null;
  if (createsSkillRevision && !changeEvidence) {
    setNotice("validationFailed");
    return;
  }
  draft = {
    ...draft,
    candidateId: selectedCandidate.candidate_id,
    baseVersion: selectedCandidate.version,
    proposal: structuredClone(proposal),
    changeEvidence
  };
  if (createsSkillRevision) {
    await createSkillRevisionCandidate(proposal, changeEvidence);
    return;
  }
  setBusy(true, "saving");
  const request = actionRequests.start();
  try {
    const revised = await requestJson("/v1/artifact-candidates/revise", {
      scope_id: currentScopeId,
      candidate_id: selectedCandidate.candidate_id,
      expected_version: selectedCandidate.version,
      proposal,
      source_refs: selectedCandidate.source_refs,
      artifact_refs: selectedCandidate.artifact_refs,
      target: selectedCandidate.target,
      reason: selectedCandidate.reason
    });
    if (!request.isCurrent()) {
      return;
    }
    selectedCandidate = revised;
    draft = null;
    conflictDraft = null;
    replaceCandidate(revised);
    revisionForm.hidden = true;
    setNotice("revisionSaved", {version: revised.version}, "success");
    renderQueue();
    renderDetail();
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    if (isCandidateConflict(error)) {
      const preservedDraft = draft;
      await loadLatestCandidate(selectedCandidate.candidate_id);
      conflictDraft = preservedDraft;
      draft = null;
      revisionForm.hidden = true;
      setNotice(error.code === "candidate_terminal" ? "candidateTerminal" : "draftConflict");
      renderDetail();
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 404) {
      draft = null;
      await handleMissingCandidate();
      return;
    }
    setNotice(
      error instanceof ReviewRequestError && error.status === 422 ? "validationFailed" :
        (error instanceof ReviewRequestError ? "requestFailed" : "serverUnavailable"),
      {status: error.status}
    );
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
    }
  }
}

async function createSkillRevisionCandidate(proposal, changeEvidence) {
  const candidate = selectedCandidate;
  if (!isPublishableCandidate(candidate)) {
    return;
  }
  const target = candidate.result_artifact;
  const request = actionRequests.start();
  setBusy(true, "creatingSkillRevision");
  try {
    let source = draft?.capturedEvidence === changeEvidence ? draft.sourceRef : null;
    if (!source) {
      const captured = await requestJson("/v1/sources/content", {
        scope_id: currentScopeId,
        source_id: `review-skill-revision-${crypto.randomUUID()}`,
        content: changeEvidence
      });
      if (!request.isCurrent()) {
        return;
      }
      source = captured.source;
      draft = {...draft, sourceRef: source, capturedEvidence: changeEvidence};
    }
    const created = await requestJson("/v1/skill/propose", {
      scope_id: currentScopeId,
      proposal,
      source_refs: [source],
      artifact_refs: [target],
      target,
      reason: changeEvidence
    });
    if (!request.isCurrent()) {
      return;
    }
    draft = null;
    revisionForm.hidden = true;
    setBusy(false);
    familyFilter.value = "skill";
    statusFilter.value = "pending";
    resetQueue(false);
    await loadCandidates(false, created.candidate_id);
    setNotice("skillRevisionCreated", {}, "success");
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 409 && error.code === "artifact_conflict") {
      setNotice("skillRevisionTargetChanged");
      return;
    }
    setNotice(
      error instanceof ReviewRequestError && error.status === 422 ? "validationFailed" :
        (error instanceof ReviewRequestError ? "requestFailed" : "serverUnavailable"),
      {status: error.status}
    );
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
    }
  }
}

async function decideCandidate(decision, reason = "") {
  if (!canDecide(selectedCandidate)) {
    return;
  }
  const id = selectedCandidate.candidate_id;
  const body = {
    scope_id: currentScopeId,
    candidate_id: id,
    expected_version: selectedCandidate.version
  };
  if (decision === "reject") {
    body.reason = reason;
  }
  const request = actionRequests.start();
  setBusy(true, "deciding");
  try {
    const decided = await requestJson(`/v1/artifact-candidates/${decision}`, body);
    if (!request.isCurrent()) {
      return;
    }
    const noticeKey = decision === "approve" ? "candidateApproved" : "candidateRejected";
    setBusy(false);
    statusFilter.value = decided.status;
    resetQueue(false);
    await loadCandidates(false, id);
    setNotice(noticeKey, {}, "success");
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    if (isCandidateConflict(error)) {
      await loadLatestCandidate(id);
      setNotice(error.code === "candidate_terminal" ? "candidateTerminal" : "candidateChanged");
      renderDetail();
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 404) {
      await handleMissingCandidate();
      return;
    }
    setNotice(error instanceof ReviewRequestError ? "requestFailed" : "serverUnavailable", {
      status: error.status
    });
  } finally {
    if (request.isCurrent()) {
      setBusy(false);
    }
  }
}

async function loadLatestCandidate(id) {
  try {
    const candidate = await requestJson("/v1/artifact-candidates/get", {
      scope_id: currentScopeId,
      candidate_id: id
    });
    selectedCandidate = candidate;
    selectedCandidateId = candidate.candidate_id;
    replaceCandidate(candidate);
    renderQueue();
    renderDetail();
    await loadProjectionStatus(candidate);
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    if (error instanceof ReviewRequestError && error.status === 404) {
      await handleMissingCandidate();
      return;
    }
    setNotice(error instanceof ReviewRequestError ? "detailLoadFailed" : "serverUnavailable", {
      status: error.status
    });
  }
}

async function handleMissingCandidate() {
  removeCandidate(selectedCandidateId);
  await selectFirstAvailable();
  setNotice("candidateMissing");
}

async function selectFirstAvailable() {
  const first = candidates[0];
  if (first) {
    await selectCandidate(first.candidate_id);
  } else {
    clearDetail();
    renderQueue();
  }
}

async function requestJson(path, body) {
  const response = await fetchWithBearer(path, readServerToken(), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    // The status code still gives the page a safe error path.
  }
  if (!response.ok) {
    throw new ReviewRequestError(
      response.status,
      payload?.error?.code || "",
      payload?.error?.details || null
    );
  }
  return payload;
}

function handleAuthenticationError(error) {
  if (!(error instanceof ReviewRequestError) || error.status !== 401) {
    return false;
  }
  clearServerToken();
  showLogin("authRejected");
  return true;
}

function isCandidateConflict(error) {
  return error instanceof ReviewRequestError && error.status === 409 && [
    "candidate_conflict",
    "artifact_conflict",
    "candidate_terminal"
  ].includes(error.code);
}

function resetQueue(clearNotice = true) {
  listRequests.cancel();
  detailRequests.cancel();
  actionRequests.cancel();
  candidates = [];
  nextCursor = null;
  selectedCandidate = null;
  selectedCandidateId = "";
  draft = null;
  conflictDraft = null;
  if (clearNotice) {
    currentNotice = null;
  }
  clearDetail();
  renderQueue();
}

function showLogin(messageKey = "") {
  scopeRequests.cancel();
  resetQueue();
  scopes = [];
  currentScopeId = "";
  currentPageStatus = null;
  currentAuthError = messageKey ? {key: messageKey, values: {}} : null;
  renderAuthError();
  authShell.hidden = false;
  pageStatus.hidden = true;
  reviewInbox.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, values = {}, retryable = false) {
  currentPageStatus = {key: messageKey, values, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  reviewInbox.hidden = true;
  signOut.hidden = !authenticationRequired;
}

function showReview() {
  currentPageStatus = null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  reviewInbox.hidden = false;
  signOut.hidden = !authenticationRequired;
}

function renderAuthError() {
  authError.textContent = currentAuthError ? translate(currentAuthError.key, currentAuthError.values) : "";
}

function renderPageStatus() {
  if (!currentPageStatus) {
    pageStatusMessage.textContent = "";
    pageStatusRetry.hidden = true;
    return;
  }
  pageStatusMessage.textContent = translate(currentPageStatus.key, currentPageStatus.values);
  pageStatusRetry.hidden = !currentPageStatus.retryable;
}

function renderScopeCombobox() {
  const selected = scopes.find((scope) => scope.scope_id === currentScopeId) || null;
  if (scopeOptions.hidden) {
    scopeSearchInput.value = selected?.display_name || "";
    scopeSearchStatus.textContent = translate("scopeSearchCount", {count: formatNumber(scopes.length)});
    return;
  }
  renderScopeOptionsList();
}

function normalizedScopeQuery(value) {
  return value.trim().toLocaleLowerCase();
}

function matchingScopes() {
  const query = normalizedScopeQuery(scopeSearchInput.value);
  if (!query) {
    return scopes;
  }
  return scopes.filter((scope) => (
    `${scope.display_name}\n${scope.scope_id}`.toLocaleLowerCase().includes(query)
  ));
}

function renderScopeOptionsList() {
  const matches = matchingScopes();
  const visible = matches.slice(0, scopeOptionRenderLimit);
  scopeOptions.replaceChildren();
  scopeActiveIndex = Math.min(scopeActiveIndex, visible.length - 1);
  for (const [index, scope] of visible.entries()) {
    const option = document.createElement("button");
    option.className = "scope-option";
    option.id = `review-scope-option-${index}`;
    option.type = "button";
    option.role = "option";
    option.tabIndex = -1;
    option.dataset.scopeId = scope.scope_id;
    option.setAttribute("aria-selected", String(scope.scope_id === currentScopeId));

    const name = document.createElement("strong");
    name.textContent = scope.display_name;
    const identity = document.createElement("code");
    identity.textContent = scope.scope_id;
    option.append(name, identity);
    option.addEventListener("click", () => {
      void selectScope(scope.scope_id);
    });
    scopeOptions.append(option);
  }
  if (matches.length === 0) {
    const empty = document.createElement("p");
    empty.className = "scope-options-empty";
    empty.textContent = translate("noMatchingScopes");
    scopeOptions.append(empty);
  }
  if (matches.length > visible.length) {
    scopeSearchStatus.textContent = translate("scopeSearchLimited", {
      shown: formatNumber(visible.length),
      total: formatNumber(matches.length)
    });
  } else {
    scopeSearchStatus.textContent = translate(
      scopeSearchInput.value ? "scopeSearchMatches" : "scopeSearchCount",
      {count: formatNumber(matches.length)}
    );
  }
  updateActiveScopeOption();
}

function openScopeOptions() {
  if (scopeSearchInput.disabled || scopes.length === 0) {
    return;
  }
  scopeOptions.hidden = false;
  scopeSearchInput.setAttribute("aria-expanded", "true");
  renderScopeOptionsList();
}

function closeScopeOptions({restoreSelection = false} = {}) {
  scopeOptions.hidden = true;
  scopeSearchInput.setAttribute("aria-expanded", "false");
  scopeSearchInput.removeAttribute("aria-activedescendant");
  scopeActiveIndex = -1;
  if (restoreSelection) {
    const selected = scopes.find((scope) => scope.scope_id === currentScopeId);
    scopeSearchInput.value = selected?.display_name || "";
    scopeSearchStatus.textContent = translate("scopeSearchCount", {count: formatNumber(scopes.length)});
  }
}

function handleScopeSearchKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeScopeOptions({restoreSelection: true});
    return;
  }
  if (!["ArrowDown", "ArrowUp", "Enter", "Home", "End"].includes(event.key)) {
    return;
  }
  if (scopeOptions.hidden) {
    openScopeOptions();
  }
  const options = Array.from(scopeOptions.querySelectorAll(".scope-option"));
  if (options.length === 0) {
    return;
  }
  event.preventDefault();
  if (event.key === "Enter") {
    const target = options[scopeActiveIndex] || options[0];
    void selectScope(target.dataset.scopeId);
    return;
  }
  if (event.key === "Home") {
    scopeActiveIndex = 0;
  } else if (event.key === "End") {
    scopeActiveIndex = options.length - 1;
  } else if (event.key === "ArrowDown") {
    scopeActiveIndex = Math.min(scopeActiveIndex + 1, options.length - 1);
  } else {
    scopeActiveIndex = scopeActiveIndex <= 0 ? options.length - 1 : scopeActiveIndex - 1;
  }
  updateActiveScopeOption();
}

function updateActiveScopeOption() {
  const options = Array.from(scopeOptions.querySelectorAll(".scope-option"));
  for (const [index, option] of options.entries()) {
    option.dataset.active = String(index === scopeActiveIndex);
  }
  const active = options[scopeActiveIndex];
  if (!active) {
    scopeSearchInput.removeAttribute("aria-activedescendant");
    return;
  }
  scopeSearchInput.setAttribute("aria-activedescendant", active.id);
  active.scrollIntoView({block: "nearest"});
}

async function selectScope(scopeId) {
  const selected = scopes.find((scope) => scope.scope_id === scopeId);
  if (!selected) {
    return;
  }
  scopeSearchInput.value = selected.display_name;
  closeScopeOptions();
  if (scopeId === currentScopeId) {
    return;
  }
  currentScopeId = scopeId;
  resetQueue();
  await loadCandidates(false);
}

function renderFilters() {
  for (const option of familyFilter.options) {
    if (option.dataset.i18n) {
      option.textContent = translate(option.dataset.i18n);
    }
  }
  for (const option of statusFilter.options) {
    if (option.dataset.i18n) {
      option.textContent = translate(option.dataset.i18n);
    }
  }
}

function renderQueue() {
  candidateList.replaceChildren();
  for (const candidate of candidates) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "review-list-item";
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(candidate.candidate_id === selectedCandidateId));
    row.addEventListener("click", () => void selectCandidate(candidate.candidate_id));

    const heading = document.createElement("span");
    heading.className = "review-list-heading";
    const family = document.createElement("span");
    family.className = "review-family-label";
    family.textContent = translate(candidate.family);
    const status = document.createElement("span");
    status.className = `status-badge review-status-${candidate.status}`;
    status.textContent = translate(candidate.status);
    heading.append(family, status);

    const title = document.createElement("strong");
    title.textContent = candidateDisplayTitle(candidate);
    const summary = document.createElement("span");
    summary.className = "review-list-summary";
    summary.textContent = candidateSummary(candidate);
    const version = document.createElement("span");
    version.className = "review-list-version";
    version.textContent = translate("version", {version: candidate.version});
    row.append(heading, title, summary, version);
    candidateList.append(row);
  }
  queueCaption.textContent = translate("loadedCandidates", {count: formatNumber(candidates.length)});
  emptyState.hidden = candidates.length !== 0 || busy;
  loadMoreButton.hidden = !nextCursor;
  loadMoreButton.disabled = busy;
}

function renderDetail() {
  if (!selectedCandidate) {
    clearDetail();
    return;
  }
  const candidate = selectedCandidate;
  detailEmpty.hidden = true;
  detailContent.hidden = false;
  detailFamily.textContent = translate(candidate.family);
  candidateTitle.textContent = candidateDisplayTitle(candidate);
  candidateId.textContent = candidate.candidate_id;
  detailStatus.className = `status-badge review-status-${candidate.status}`;
  detailStatus.textContent = translate(candidate.status);
  detailVersion.textContent = translate("version", {version: candidate.version});
  renderProposal(candidate);
  renderPackage(candidate);
  renderEvidence(candidate);
  renderLineage(candidate);
  renderPublication();
  if (!revisionForm.hidden && draft) {
    renderRevisionFormHeader(draft.mode, draft.baseVersion);
  }
  const decisionEnabled = canDecide(candidate);
  reviewActions.hidden = !decisionEnabled || !revisionForm.hidden;
  editButton.hidden = !isEditableCandidate(candidate);
  editButton.disabled = busy;
  approveButton.disabled = busy;
  rejectButton.disabled = busy;
  if (!isSupportedCandidate(candidate) && !currentNotice) {
    currentNotice = {key: "unsupportedCandidate", values: {}, tone: "error"};
  }
  renderNotice();
}

function clearDetail() {
  selectedCandidate = null;
  selectedCandidateId = "";
  detailEmpty.hidden = false;
  detailContent.hidden = true;
  revisionForm.hidden = true;
  proposalFields.replaceChildren();
  packageRequests.cancel();
  packageManifest = null;
  packageLoadingDigest = "";
  packageSection.hidden = true;
  packageFiles.replaceChildren();
  packagePreview.textContent = "";
  sourceRefs.replaceChildren();
  artifactRefs.replaceChildren();
  lineageFields.replaceChildren();
  projectionRequests.cancel();
  projectionView = null;
  projectionLoading = false;
  publicationSection.hidden = true;
  renderNotice();
}

function renderProposal(candidate) {
  proposalFields.replaceChildren();
  if (!isSupportedCandidate(candidate)) {
    appendDefinition(proposalFields, "proposal", JSON.stringify(candidate.proposal));
    return;
  }
  const keys = candidate.family === "experience"
    ? ["situation", "action", "outcome", "lesson"]
    : ["name", "description", "instructions", "validation"];
  for (const key of keys) {
    const value = candidate.proposal[key];
    appendDefinition(proposalFields, key, Array.isArray(value) ? value.join("\n") : value);
  }
}

function renderPackage(candidate) {
  const reference = candidate.family === "skill" ? candidate.proposal.package : null;
  packageSection.hidden = !reference;
  if (!reference) {
    packageManifest = null;
    packageFiles.replaceChildren();
    packagePreview.textContent = "";
    return;
  }
  if (packageManifest?.package?.tree_digest === reference.tree_digest) {
    renderPackageFiles(candidate);
    return;
  }
  if (packageLoadingDigest === reference.tree_digest) {
    return;
  }
  packageLoadingDigest = reference.tree_digest;
  packageStatus.textContent = translate("packageLoading");
  packageFiles.replaceChildren();
  packagePreview.textContent = "";
  void loadPackageManifest(candidate, reference);
}

async function loadPackageManifest(candidate, reference) {
  const request = packageRequests.start();
  try {
    const manifest = await requestJson("/dashboard/skill-packages/manifest", {
      scope_id: currentScopeId,
      package: reference
    });
    if (!request.isCurrent() || selectedCandidateId !== candidate.candidate_id) {
      return;
    }
    packageManifest = manifest;
    packageLoadingDigest = "";
    packageStatus.textContent = "";
    renderPackageFiles(candidate);
    if (manifest.files.length) {
      void loadPackagePreview(candidate, reference, manifest.files[0].path);
    }
  } catch (error) {
    if (request.isCurrent() && selectedCandidateId === candidate.candidate_id) {
      packageLoadingDigest = "";
      packageStatus.textContent = translate("packageLoadFailed");
    }
  }
}

function renderPackageFiles(candidate) {
  packageFiles.replaceChildren();
  for (const file of packageManifest?.files || []) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${file.path} · ${formatNumber(file.size)} B${file.executable ? " · executable" : ""}`;
    button.addEventListener("click", () => {
      void loadPackagePreview(candidate, candidate.proposal.package, file.path);
    });
    item.append(button);
    packageFiles.append(item);
  }
}

async function loadPackagePreview(candidate, reference, path) {
  const request = packageRequests.start();
  packagePath.textContent = path;
  packagePreview.textContent = "";
  try {
    const preview = await requestJson("/dashboard/skill-packages/preview", {
      scope_id: currentScopeId,
      package: reference,
      path
    });
    if (!request.isCurrent() || selectedCandidateId !== candidate.candidate_id) {
      return;
    }
    packagePreview.textContent = preview.binary ? translate("packageBinary") : (preview.content || "");
  } catch (error) {
    if (request.isCurrent() && selectedCandidateId === candidate.candidate_id) {
      packagePreview.textContent = translate("packageLoadFailed");
    }
  }
}

function renderEvidence(candidate) {
  renderReferenceList(
    sourceRefs,
    candidate.source_refs,
    (reference) => `${reference.name}/${reference.source_id}`,
    "noSourceReferences"
  );
  renderReferenceList(
    artifactRefs,
    candidate.artifact_refs,
    formatArtifactReference,
    "noArtifactReferences"
  );
}

function renderReferenceList(list, references, formatter, emptyKey) {
  list.replaceChildren();
  if (!references.length) {
    const item = document.createElement("li");
    item.className = "review-reference-empty";
    item.textContent = translate(emptyKey);
    list.append(item);
    return;
  }
  for (const reference of references) {
    const item = document.createElement("li");
    const code = document.createElement("code");
    code.textContent = formatter(reference);
    item.append(code);
    list.append(item);
  }
}

function renderLineage(candidate) {
  lineageFields.replaceChildren();
  appendDefinition(lineageFields, "reason", candidate.reason || translate("notProvided"));
  appendDefinition(lineageFields, "target", candidate.target ? formatArtifactReference(candidate.target) : translate("notProvided"));
  appendDefinition(
    lineageFields,
    "resultArtifact",
    candidate.result_artifact ? formatArtifactReference(candidate.result_artifact) : translate("notProvided")
  );
  appendDefinition(lineageFields, "decisionReason", candidate.decision_reason || translate("notProvided"));
}

function renderPublication() {
  if (!isPublishableCandidate(selectedCandidate) || !revisionForm.hidden) {
    publicationSection.hidden = true;
    return;
  }
  publicationSection.hidden = false;
  createSkillRevisionButton.disabled = busy;
  publishSkillButton.hidden = true;
  publicationStatus.textContent = projectionLoading ? translate("publicationLoading") : "";
  publicationState.hidden = projectionLoading || !projectionView;
  publicationEmpty.hidden = true;
  publicationContent.hidden = true;
  if (projectionLoading || !projectionView) {
    return;
  }
  if (projectionView.blocker === "standard_package_required") {
    publicationStatus.textContent = translate("standardPackageRequired");
    return;
  }
  if (projectionView.targets.length === 0) {
    publicationEmpty.hidden = false;
    return;
  }

  const selectedTargetId = projectionView.targets.some((target) => target.target_id === publicationTarget.value)
    ? publicationTarget.value
    : projectionView.targets[0].target_id;
  publicationTarget.replaceChildren();
  for (const target of projectionView.targets) {
    const option = document.createElement("option");
    option.value = target.target_id;
    option.textContent = `${agentLabel(target.agent_kind)} · ${target.target_id} · ${translate(`installation${capitalize(target.installation_scope)}`)}`;
    option.selected = target.target_id === selectedTargetId;
    publicationTarget.append(option);
  }
  const target = selectedProjectionTarget();
  if (!target) {
    return;
  }
  publicationContent.hidden = false;
  publicationState.hidden = false;
  publicationState.className = `status-badge review-projection-${target.state}`;
  publicationState.textContent = translate(projectionStateKey(target.state));
  publicationStatus.textContent = projectionHintKey(target.state)
    ? translate(projectionHintKey(target.state))
    : "";
  publishedRevision.textContent = target.published_revision === null
    ? translate("notProvided")
    : translate("version", {version: target.published_revision});
  publicationDestination.textContent = target.destination;
  publicationDiscovery.textContent = translate(discoveryStateKey(target.discovery));
  publishSkillButton.textContent = translate(publicationActionKey(target));
  const canPublish = canPublishProjection(target);
  publishSkillButton.hidden = !canPublish;
  publishSkillButton.disabled = busy || !canPublish;
}

function selectedProjectionTarget() {
  if (!projectionView) {
    return null;
  }
  return projectionView.targets.find((target) => target.target_id === publicationTarget.value)
    || projectionView.targets[0]
    || null;
}

function agentLabel(agentKind) {
  return translate(agentKind === "claude_code" ? "agentClaudeCode" : "agentCodex");
}

function isPublishableCandidate(candidate) {
  return Boolean(
    candidate
    && candidate.family === "skill"
    && candidate.status === "approved"
    && candidate.result_artifact
  );
}

function canPublishProjection(target) {
  return target.compatibility !== "incompatible" && (
    ["unpublished", "update_available"].includes(target.state)
    || (target.state === "current" && target.discovery !== "available")
  );
}

function publicationActionKey(target) {
  if (target.state === "update_available") {
    return "updateSkill";
  }
  if (target.state === "current") {
    return "refreshDiscovery";
  }
  return "publishSkill";
}

function projectionStateKey(state) {
  return {
    unpublished: "projectionUnpublished",
    current: "projectionCurrent",
    update_available: "projectionUpdateAvailable",
    conflict: "projectionConflict",
    drifted: "projectionDrifted",
    incompatible: "projectionIncompatible"
  }[state] || "projectionConflict";
}

function projectionHintKey(state) {
  return {
    conflict: "projectionConflictHint",
    drifted: "projectionDriftedHint",
    incompatible: "projectionIncompatibleHint"
  }[state] || "";
}

function discoveryStateKey(state) {
  return {
    available: "discoveryAvailable",
    unavailable: "discoveryUnavailable",
    not_published: "discoveryNotPublished"
  }[state] || "discoveryNotPublished";
}

function capitalize(value) {
  return `${value.charAt(0).toUpperCase()}${value.slice(1)}`;
}

function appendDefinition(list, key, value) {
  const term = document.createElement("dt");
  term.textContent = translate(key);
  const description = document.createElement("dd");
  description.textContent = String(value ?? "");
  list.append(term, description);
}

function startRevision(proposal, version, mode = "revise-candidate") {
  if (!selectedCandidate) {
    return;
  }
  draft = {
    mode,
    candidateId: selectedCandidate.candidate_id,
    baseVersion: version,
    proposal: structuredClone(proposal),
    changeEvidence: mode === "create-skill-revision" ? "" : null,
    sourceRef: null,
    capturedEvidence: null
  };
  currentNotice = null;
  renderRevisionFields(selectedCandidate.family, draft.proposal, mode, draft.changeEvidence);
  renderRevisionFormHeader(mode, version);
  revisionForm.hidden = false;
  reviewActions.hidden = true;
  renderPublication();
  renderNotice();
  revisionForm.scrollIntoView({block: "start"});
  revisionForm.querySelector("input, textarea")?.focus();
}

function startSkillRevision(candidate) {
  if (!isPublishableCandidate(candidate) || !isSupportedCandidate(candidate)) {
    return;
  }
  startRevision(candidate.proposal, candidate.result_artifact.revision, "create-skill-revision");
}

function renderRevisionFormHeader(mode, version) {
  const createsSkillRevision = mode === "create-skill-revision";
  revisionTitle.textContent = translate(createsSkillRevision ? "createSkillRevisionTitle" : "reviseProposal");
  revisionNote.textContent = translate(createsSkillRevision ? "createSkillRevisionNote" : "revisionEvidenceNote");
  draftVersion.textContent = translate(
    createsSkillRevision ? "basedOnSkillRevision" : "editingVersion",
    createsSkillRevision ? {revision: version} : {version}
  );
  saveRevisionButton.textContent = translate(createsSkillRevision ? "createCandidate" : "saveRevision");
}

function renderRevisionFields(family, proposal, mode = "revise-candidate", changeEvidence = "") {
  revisionFields.replaceChildren();
  if (family === "experience") {
    for (const key of ["situation", "action", "outcome", "lesson"]) {
      addTextareaField(key, proposal[key], 8000);
    }
    return;
  }
  if (mode === "create-skill-revision") {
    addTextareaField("changeEvidence", changeEvidence, 2000, "review-short-textarea");
  }
  addInputField("name", proposal.name, 128);
  addTextareaField("description", proposal.description, 2000, "review-short-textarea");
  addTextareaField("instructions", proposal.instructions, 32000, "review-tall-textarea");
  const group = document.createElement("fieldset");
  group.className = "review-validation-editor";
  const legend = document.createElement("legend");
  legend.textContent = translate("validation");
  const list = document.createElement("div");
  list.id = "review-validation-items";
  const values = Array.isArray(proposal.validation) && proposal.validation.length ? proposal.validation : [""];
  for (const value of values) {
    addValidationRow(list, value);
  }
  const add = document.createElement("button");
  add.type = "button";
  add.className = "secondary-button review-add-validation";
  add.textContent = translate("addValidation");
  add.addEventListener("click", () => {
    if (list.children.length < 32) {
      addValidationRow(list, "");
      updateValidationRows(list);
      list.lastElementChild.querySelector("input").focus();
    }
  });
  group.append(legend, list, add);
  revisionFields.append(group);
  updateValidationRows(list);
}

function addInputField(key, value, maxLength) {
  const label = document.createElement("label");
  label.className = "review-form-field";
  const caption = document.createElement("span");
  caption.textContent = translate(key);
  const input = document.createElement("input");
  input.name = key;
  input.value = value;
  input.maxLength = maxLength;
  input.required = true;
  label.append(caption, input);
  revisionFields.append(label);
}

function addTextareaField(key, value, maxLength, className = "") {
  const label = document.createElement("label");
  label.className = "review-form-field";
  const caption = document.createElement("span");
  caption.textContent = translate(key);
  const textarea = document.createElement("textarea");
  textarea.name = key;
  textarea.value = value;
  textarea.maxLength = maxLength;
  textarea.required = true;
  textarea.className = className;
  label.append(caption, textarea);
  revisionFields.append(label);
}

function addValidationRow(list, value) {
  const row = document.createElement("div");
  row.className = "review-validation-row";
  const label = document.createElement("label");
  const caption = document.createElement("span");
  const input = document.createElement("input");
  input.value = value;
  input.maxLength = 2000;
  input.required = true;
  label.append(caption, input);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary-button";
  remove.textContent = translate("removeValidation");
  remove.addEventListener("click", () => {
    if (list.children.length > 1) {
      row.remove();
      updateValidationRows(list);
    }
  });
  row.append(label, remove);
  list.append(row);
}

function updateValidationRows(list) {
  [...list.children].forEach((row, index) => {
    const label = translate("validationItem", {number: index + 1});
    row.querySelector("span").textContent = label;
    row.querySelector("input").setAttribute("aria-label", label);
    row.querySelector("button").disabled = list.children.length === 1;
  });
}

function collectProposal() {
  if (!selectedCandidate || !draft) {
    return null;
  }
  const readField = (name) => revisionForm.elements.namedItem(name).value.trim();
  if (selectedCandidate.family === "experience") {
    const proposal = {
      situation: readField("situation"),
      action: readField("action"),
      outcome: readField("outcome"),
      lesson: readField("lesson")
    };
    return Object.values(proposal).every(Boolean) ? proposal : null;
  }
  if (selectedCandidate.family === "skill") {
    const validation = [...document.querySelectorAll("#review-validation-items input")]
      .map((input) => input.value.trim());
    const proposal = {
      name: readField("name"),
      description: readField("description"),
      instructions: readField("instructions"),
      validation
    };
    return proposal.name && proposal.description && proposal.instructions && validation.every(Boolean) ? proposal : null;
  }
  return null;
}

function renderNotice() {
  if (!currentNotice || !selectedCandidate) {
    alertBox.hidden = true;
    alertBox.textContent = "";
  } else {
    alertBox.hidden = false;
    alertBox.dataset.tone = currentNotice.tone || "error";
    alertBox.textContent = translate(currentNotice.key, currentNotice.values || {});
  }
  conflictActions.hidden = !conflictDraft || !selectedCandidate;
}

function setNotice(key, values = {}, tone = "error") {
  currentNotice = {key, values, tone};
  liveStatus.textContent = selectedCandidate ? "" : translate(key, values);
  renderNotice();
}

function setBusy(value, statusKey = "") {
  busy = value;
  liveStatus.textContent = statusKey ? translate(statusKey) : "";
  scopeSearchInput.disabled = value;
  if (value) {
    closeScopeOptions({restoreSelection: true});
  }
  familyFilter.disabled = value;
  statusFilter.disabled = value;
  refreshButton.disabled = value;
  loadMoreButton.disabled = value;
  saveRevisionButton.disabled = value;
  editButton.disabled = value;
  approveButton.disabled = value;
  rejectButton.disabled = value;
  createSkillRevisionButton.disabled = value;
  publishSkillButton.disabled = value;
  renderQueue();
  renderPublication();
}

function replaceCandidate(candidate) {
  const index = candidates.findIndex((item) => item.candidate_id === candidate.candidate_id);
  if (index >= 0) {
    candidates[index] = candidate;
  }
}

function removeCandidate(id) {
  candidates = candidates.filter((candidate) => candidate.candidate_id !== id);
  if (selectedCandidateId === id) {
    selectedCandidate = null;
    selectedCandidateId = "";
  }
}

function candidateDisplayTitle(candidate) {
  if (candidate.family === "skill" && typeof candidate.proposal?.name === "string") {
    return candidate.proposal.name;
  }
  if (candidate.family === "experience" && typeof candidate.proposal?.situation === "string") {
    return compactText(candidate.proposal.situation, 88);
  }
  return candidate.candidate_id;
}

function candidateSummary(candidate) {
  if (candidate.family === "skill") {
    return compactText(candidate.proposal?.description || "", 132);
  }
  if (candidate.family === "experience") {
    return compactText(candidate.proposal?.lesson || candidate.proposal?.outcome || "", 132);
  }
  return "";
}

function compactText(value, limit) {
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function formatArtifactReference(reference) {
  return `${reference.family}/${reference.artifact_id}@${reference.revision}`;
}

function readReviewDeepLink() {
  const params = new URLSearchParams(window.location.search);
  return {
    scope: params.get("scope") || "",
    family: params.get("family") || "",
    status: params.get("status") || "",
    candidate: params.get("candidate") || "",
    action: params.get("action") || ""
  };
}

function canDecide(candidate) {
  return Boolean(candidate && candidate.status === "pending" && isSupportedCandidate(candidate));
}

function isEditableCandidate(candidate) {
  return Boolean(
    isSupportedCandidate(candidate)
    && !(candidate.family === "skill" && candidate.proposal.package)
  );
}

function isSupportedCandidate(candidate) {
  if (!candidate || !candidate.proposal) {
    return false;
  }
  if (candidate.family === "experience") {
    return ["situation", "action", "outcome", "lesson"].every(
      (key) => typeof candidate.proposal[key] === "string"
    );
  }
  if (candidate.family === "skill") {
    return ["name", "description", "instructions"].every(
      (key) => typeof candidate.proposal[key] === "string"
    ) && Array.isArray(candidate.proposal.validation) && candidate.proposal.validation.every(
      (item) => typeof item === "string"
    );
  }
  return false;
}

renderFilters();
ui.initialize();
void authenticate(readServerToken(), reviewDeepLink.scope);
