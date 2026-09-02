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
    pageTitle: "PowerContext Skills Library",
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
    skillsLibraryTitle: "Skills Library",
    skillsIntro: "Browse approved PowerContext Skills and Agent-local packages available in this scope.",
    selectScope: "Scope",
    searchScopesPlaceholder: "Search by scope name or ID",
    scopeSearchCount: "{count} scopes",
    scopeSearchMatches: "{count} matching scopes",
    scopeSearchLimited: "Showing {shown} of {total} matching scopes",
    noMatchingScopes: "No scopes match this search.",
    skillsFilters: "Skills filters",
    searchSkills: "Search",
    searchSkillsPlaceholder: "Search by name, description, or identity",
    authority: "Authority",
    origin: "Origin",
    originPowerContext: "Generated",
    originExternalImport: "Imported",
    originExternalFork: "Forked",
    originExternal: "Local",
    sourceMachine: "Source machine",
    sourceAgent: "Source Agent",
    originalLocation: "Original location",
    externalIdentity: "External Skill ID",
    allSkills: "All Skills",
    managedSkill: "Managed",
    externalSkill: "External",
    refresh: "Refresh",
    skillsSummary: "Skills summary",
    managedSkills: "Managed Skills",
    externalSkills: "External Skills",
    authorityNote: "Managed Skills are governed Artifact Revisions. External Skills remain Agent-local packages.",
    libraryInventory: "Library inventory",
    loadedSkills: "{count} shown of {total}",
    noSkills: "No Skills match these filters.",
    noSkillsHint: "Try another search or refresh local discovery.",
    skillDetail: "Skill detail",
    selectSkill: "Select a Skill to inspect it.",
    selectSkillHint: "Content, authority, lineage, and availability will appear here.",
    overview: "Overview",
    description: "Description",
    status: "Status",
    approved: "Approved",
    active: "Active",
    deprecated: "Deprecated",
    retired: "Retired",
    available: "Available",
    unavailable: "Unavailable",
    artifact: "Artifact",
    revision: "Revision",
    candidate: "Candidate",
    provider: "Provider",
    installationScope: "Installation scope",
    host: "Host",
    fingerprint: "Fingerprint",
    locator: "Locator",
    entrypoint: "Entrypoint",
    instructions: "Instructions",
    validation: "Validation",
    packageContents: "Package contents",
    packageFiles: "Package files",
    packageLoading: "Loading verified package contents...",
    packageBinary: "Binary file preview is intentionally unavailable.",
    packageTruncated: "Preview is limited to the first 64 KiB.",
    packageLoadFailed: "Package contents could not be loaded. HTTP {status}.",
    governanceGeneration: "Governance generation",
    replacement: "Recommended replacement Skill",
    governance: "Governance",
    governanceIntro: "Deprecate or retire the logical Skill without changing immutable package bytes.",
    lifecycleState: "Lifecycle state",
    replacementSkill: "Recommended replacement Skill (optional)",
    noReplacementSkill: "No recommended replacement",
    applyLifecycle: "Apply lifecycle",
    lifecycleUpdating: "Updating lifecycle...",
    lifecycleUpdated: "Lifecycle updated to {state}.",
    lifecycleFailed: "The lifecycle could not be updated. HTTP {status}.",
    lifecycleConflict: "The Skill governance state changed. Refresh before trying again.",
    retireSkillTitle: "Retire this Skill?",
    retireSkillWarning: "Retirement is irreversible. Existing publication must be removed separately.",
    retireSkill: "Retire Skill",
    lineage: "Lineage",
    sourceReferences: "Source references",
    artifactReferences: "Artifact references",
    noSourceReferences: "No Source references",
    noArtifactReferences: "No Artifact references",
    delivery: "Delivery",
    deliveryIntro: "Install this approved Revision on this machine or distribute it to a connected remote machine.",
    deliveryLocation: "Delivery location",
    deliveryLocal: "This machine",
    deliveryRemote: "Remote machine",
    refreshRemoteStatus: "Refresh status",
    createSkillRevision: "Upload revision package",
    revisionUploading: "Uploading complete successor package...",
    revisionUploadFailed: "The successor package could not be proposed. HTTP {status}.",
    publishTarget: "Install for",
    agentCodex: "Codex",
    agentClaudeCode: "Claude Code",
    installationUser: "User",
    installationProject: "Project",
    installationPlugin: "Plugin",
    currentProject: "Current project",
    noPublishTargets: "No local Skill destination is available.",
    noPublishTargetsHint: "Set the Server workspace or configure an advanced local Agent target.",
    noRemoteTargets: "No remote machines are connected.",
    noRemoteTargetsHint: "Add a Codex or Claude Code project, then complete the one-time enrollment on that machine.",
    addRemoteMachine: "Add remote machine",
    addRemoteMachineHint: "Give this machine a recognizable name, then choose the Agent used by its project.",
    remoteMachineName: "Machine name",
    remoteMachineNamePlaceholder: "For example: Build machine - Hangzhou",
    remoteMachineNameRequired: "Enter a machine name.",
    renameRemoteMachine: "Rename",
    saveRemoteMachineName: "Save name",
    remoteRenaming: "Saving the machine name...",
    remoteRenameFailed: "The machine name could not be saved. HTTP {status}.",
    searchRemoteMachines: "Search by name, host, workspace, or ID",
    remoteSearchNoMatch: "No remote machine matches this search.",
    receiverConnection: "Receiver connection",
    receiverConnectionReady: "The remote CLI will connect to {url}.",
    receiverConnectionInsecure: "The remote CLI will connect to {url} over cleartext HTTP. Credentials and Skill packages are not encrypted in transit.",
    receiverConnectionNeedsSetup: "No remote-safe Server URL is available. Configure the remote CLI's Server URL before running enrollment.",
    insecureHttpEnabledTitle: "Cleartext HTTP enabled",
    insecureHttpEnabledWarning: "Receiver credentials and Skill packages are not encrypted in transit. Use this only on a protected private test network.",
    createRemoteMachine: "Create connection",
    connectRemoteMachine: "Connect the remote machine",
    remoteAgent: "Agent",
    remoteTarget: "Remote machine",
    remoteTargetId: "Target ID",
    remoteEnvironment: "Reported environment",
    remoteEnvironmentPending: "Available after enrollment",
    remoteEnrollment: "Connection",
    remoteDeliveryState: "Delivery state",
    remoteObservedRevision: "Installed revision",
    remoteLastSeen: "Last check-in",
    remoteNextStep: "Enable automatic sync on the remote machine",
    remoteTargetPending: "Waiting for enrollment",
    remoteTargetActive: "Connected",
    remoteTargetRevoked: "Revoked",
    remoteNoPublication: "Not distributed",
    remoteStateUnpublished: "Removed",
    remoteStatePending: "Waiting for the remote Receiver",
    remoteStateCurrent: "Current",
    remoteStateUpdateAvailable: "Update available",
    remoteStateDeliveryFailed: "Delivery failed",
    remoteStateConflict: "Target conflict",
    remoteStateDrifted: "Locally modified",
    remoteStateIncompatible: "Not compatible",
    remoteRevisionNone: "Not installed",
    remoteNeverSeen: "Never",
    remoteGuidancePending: "Finish enrollment with the one-time code. If it was closed before you saved it, revoke this connection and add the machine again.",
    remoteGuidanceReady: "Automatic sync checks this Server every few seconds. Choose Distribute Skill when ready.",
    remoteGuidanceSync: "The remote Receiver will automatically apply this requested change.",
    remoteGuidanceCurrent: "This revision is installed and automatic sync remains active.",
    remoteGuidanceProblem: "Inspect the remote project before retrying. PowerContext will not overwrite local changes.",
    remoteGuidanceRemoved: "The managed Skill is absent from this target.",
    publishRemoteSkill: "Distribute Skill",
    unpublishRemoteSkill: "Request removal",
    revokeRemoteMachine: "Revoke machine",
    revokeRemoteMachineTitle: "Revoke this remote machine?",
    revokeRemoteMachineWarning: "Its credential will stop working. Remove distributed Skills and wait for automatic synchronization before revoking.",
    remoteRevokeBlocked: "Remove every distributed Skill and wait for automatic synchronization before revoking this machine.",
    remoteTargetsLoading: "Loading remote machine status...",
    remoteTargetsFailed: "Remote machine status could not be loaded. HTTP {status}.",
    remoteCreating: "Creating the remote connection...",
    remoteCreateFailed: "The remote connection could not be created. HTTP {status}.",
    remotePublishing: "Creating the remote desired state...",
    remotePublishRequested: "Distribution requested. Waiting for the remote Receiver to install revision {revision}.",
    remotePublishFailed: "The Skill could not be distributed. HTTP {status}.",
    remoteUnpublishing: "Requesting remote removal...",
    remoteUnpublishRequested: "Removal requested. Waiting for the remote Receiver to finish.",
    remoteUnpublishFailed: "Remote removal could not be requested. HTTP {status}.",
    remotePublishSkillTitle: "Distribute this managed Skill to the remote machine?",
    remoteUnpublishSkillTitle: "Remove this managed Skill from the remote machine?",
    remoteRevoking: "Revoking the remote machine...",
    remoteRevoked: "The credential for {target} was revoked.",
    remoteRevokeFailed: "The remote machine could not be revoked. HTTP {status}.",
    remotePublishConfirmation: "Distribute revision {revision} to {target}. The automatic Receiver will install it.",
    remotePublishDeprecatedConfirmation: "This Skill is deprecated. Distribute revision {revision} to {target} anyway?",
    remoteUnpublishConfirmation: "Request removal from {target}. The automatic Receiver safely removes it if the managed package is intact.",
    enrollmentCodeWarning: "This enrollment code is shown once and expires soon. Complete these steps before closing.",
    expiresAt: "Expires",
    installReceiver: "Install the lightweight Receiver",
    runEnrollment: "Run enrollment in the remote project",
    enterEnrollmentCode: "Enter this one-time code when prompted",
    copy: "Copy",
    copyCode: "Copy code",
    copied: "Copied.",
    copyFailed: "Copy failed. Select the text and copy it manually.",
    done: "I finished or saved these steps",
    standardPackageRequired: "This approved Skill predates standard package snapshots. Upload and approve a complete package as its next revision before publishing.",
    publishedRevision: "Installed revision",
    destination: "Installation path",
    discovery: "Discovery",
    compatibility: "Compatibility",
    compatible: "Compatible",
    incompatible: "Incompatible",
    unknown: "Unknown",
    manual_review_required: "Manual review required",
    publishSkill: "Install on this machine",
    unpublishSkill: "Remove from this machine",
    unpublishSkillTitle: "Remove this managed Skill from this machine?",
    unpublishConfirmation: "Remove the exact intact package from {target}. The approved Revision and package history remain available.",
    unpublishing: "Removing Skill from this machine...",
    unpublicationSucceeded: "The managed package was safely removed from this machine.",
    unpublicationFailed: "The managed package could not be safely removed. HTTP {status}.",
    publishDeprecatedConfirmation: "This Skill is deprecated. Install revision {revision} for {target} anyway? Existing PowerContext-managed content may be safely updated; foreign or modified content is never overwritten.",
    updateSkill: "Install update",
    refreshDiscovery: "Refresh discovery",
    publishSkillCandidate: "Install this managed Skill on this machine?",
    publishConfirmation: "Install revision {revision} for {target}. Existing PowerContext-managed content may be safely updated; foreign or modified content is never overwritten.",
    cancel: "Cancel",
    projectionUnpublished: "Not installed",
    projectionCurrent: "Installed",
    projectionUpdateAvailable: "Update available",
    projectionConflict: "Target conflict",
    projectionDrifted: "Locally modified",
    projectionIncompatible: "Not compatible",
    projectionConflictHint: "The destination is occupied or a newer Revision is already present. PowerContext will not overwrite it.",
    projectionDriftedHint: "This PowerContext package was modified locally. Restore it or choose another target before publishing.",
    projectionIncompatibleHint: "Revise the Skill name or description to satisfy the package constraints, then approve a new Revision.",
    discoveryAvailable: "Available in the configured Agent target",
    discoveryUnavailable: "Package exists; discovery needs refresh",
    discoveryNotPublished: "Not yet available",
    publicationLoading: "Checking publication status...",
    publicationLoadFailed: "Publication status could not be loaded. HTTP {status}.",
    publicationSucceeded: "Managed Skill revision {revision} is installed and discoverable.",
    publicationFailed: "The managed Skill could not be installed. HTTP {status}.",
    publicationConflict: "The publication target changed or contains content PowerContext will not overwrite.",
    publishing: "Installing Skill on this machine...",
    loading: "Loading Skills...",
    refreshing: "Refreshing local discovery...",
    externalDiscoveryUnavailable: "Local Skill folders could not be refreshed. Managed Skills are still available.",
    externalSkillUnavailable: "This exact local package is no longer available at its registered fingerprint.",
    authRejected: "The Server rejected this token.",
    requestFailed: "The Skills request failed with HTTP {status}.",
    serverUnavailable: "The Server is unavailable.",
    retry: "Retry",
    noScopes: "There is no work to show here.",
    scopeUnavailable: "The selected scope is not available."
  },
  zh: {
    pageTitle: "PowerContext 技能库",
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
    skillsLibraryTitle: "技能库",
    skillsIntro: "浏览当前作用域中已批准的受管技能，以及代理本地可用的技能包。",
    selectScope: "作用域",
    searchScopesPlaceholder: "按作用域名称或标识符搜索",
    scopeSearchCount: "共 {count} 个作用域",
    scopeSearchMatches: "找到 {count} 个作用域",
    scopeSearchLimited: "显示 {total} 个匹配项中的前 {shown} 个",
    noMatchingScopes: "没有匹配的作用域。",
    skillsFilters: "技能筛选条件",
    searchSkills: "搜索",
    searchSkillsPlaceholder: "按名称、描述或标识搜索",
    authority: "权威来源",
    origin: "出处",
    originPowerContext: "自生成",
    originExternalImport: "接管",
    originExternalFork: "派生",
    originExternal: "本地",
    sourceMachine: "来源机器",
    sourceAgent: "来源代理",
    originalLocation: "原始位置",
    externalIdentity: "外部技能标识符",
    allSkills: "全部技能",
    managedSkill: "受管技能",
    externalSkill: "外部技能",
    refresh: "刷新",
    skillsSummary: "技能概览",
    managedSkills: "受管技能",
    externalSkills: "外部技能",
    authorityNote: "受管技能以制品修订为权威来源；外部技能以代理本地技能包为权威来源。",
    libraryInventory: "技能清单",
    loadedSkills: "显示 {total} 项中的 {count} 项",
    noSkills: "没有符合筛选条件的技能。",
    noSkillsHint: "请尝试其他搜索条件，或刷新本地发现状态。",
    skillDetail: "技能详情",
    selectSkill: "请选择一项技能进行检查。",
    selectSkillHint: "此处将显示内容、权威来源、沿袭关系和可用状态。",
    overview: "概览",
    description: "描述",
    status: "状态",
    approved: "已批准",
    active: "活跃",
    deprecated: "已废弃",
    retired: "已退役",
    available: "可用",
    unavailable: "不可用",
    artifact: "制品",
    revision: "修订",
    candidate: "候选",
    provider: "提供方",
    installationScope: "安装范围",
    host: "主机",
    fingerprint: "内容指纹",
    locator: "本地位置",
    entrypoint: "入口文件",
    instructions: "使用说明",
    validation: "验证要求",
    packageContents: "技能包内容",
    packageFiles: "技能包文件",
    packageLoading: "正在加载已校验的技能包内容...",
    packageBinary: "二进制文件不会在页面中预览。",
    packageTruncated: "预览仅显示前 64 千字节。",
    packageLoadFailed: "无法加载技能包内容（HTTP {status}）。",
    governanceGeneration: "治理代次",
    replacement: "推荐替代技能",
    governance: "治理",
    governanceIntro: "无需修改不可变的技能包内容，即可废弃或退役这项逻辑技能。",
    lifecycleState: "生命周期状态",
    replacementSkill: "推荐替代技能（可选）",
    noReplacementSkill: "不指定替代技能",
    applyLifecycle: "应用生命周期",
    lifecycleUpdating: "正在更新生命周期...",
    lifecycleUpdated: "生命周期已更新为“{state}”。",
    lifecycleFailed: "无法更新生命周期（HTTP {status}）。",
    lifecycleConflict: "技能治理状态已变化，请刷新后重试。",
    retireSkillTitle: "退役这项技能？",
    retireSkillWarning: "退役不可逆。已有发布需要单独安全下架。",
    retireSkill: "退役技能",
    lineage: "沿袭关系",
    sourceReferences: "数据源引用",
    artifactReferences: "制品引用",
    noSourceReferences: "无数据源引用",
    noArtifactReferences: "无制品引用",
    delivery: "交付",
    deliveryIntro: "将已批准修订安装到本机，或分发到已连接的远端机器。",
    deliveryLocation: "交付位置",
    deliveryLocal: "本机",
    deliveryRemote: "远端机器",
    refreshRemoteStatus: "刷新状态",
    createSkillRevision: "上传新修订包",
    revisionUploading: "正在上传完整的后继技能包...",
    revisionUploadFailed: "无法提交后继技能包（HTTP {status}）。",
    publishTarget: "安装到",
    agentCodex: "Codex",
    agentClaudeCode: "Claude Code",
    installationUser: "用户级",
    installationProject: "项目级",
    installationPlugin: "插件级",
    currentProject: "当前项目",
    noPublishTargets: "当前没有可用的本机技能目录。",
    noPublishTargetsHint: "请设置服务工作目录，或在高级配置中指定本机代理目标。",
    noRemoteTargets: "尚未连接远端机器。",
    noRemoteTargetsHint: "添加一个 Codex 或 Claude Code 项目，然后在目标机器完成一次性注册。",
    addRemoteMachine: "添加远端机器",
    addRemoteMachineHint: "先给机器起一个容易识别的名称，再选择远端项目使用的代理类型。",
    remoteMachineName: "机器名称",
    remoteMachineNamePlaceholder: "例如：杭州构建机",
    remoteMachineNameRequired: "请输入机器名称。",
    renameRemoteMachine: "重命名",
    saveRemoteMachineName: "保存名称",
    remoteRenaming: "正在保存机器名称...",
    remoteRenameFailed: "无法保存机器名称（HTTP {status}）。",
    searchRemoteMachines: "按名称、主机、工作区或技术标识搜索",
    remoteSearchNoMatch: "没有匹配的远端机器。",
    receiverConnection: "接收端连接",
    receiverConnectionReady: "远端命令行将连接到 {url}。",
    receiverConnectionInsecure: "远端命令行将通过明文 HTTP 连接到 {url}。注册凭据和技能包在传输过程中不会被加密。",
    receiverConnectionNeedsSetup: "当前没有适合远端连接的服务地址。请先为远端命令行配置服务地址，再执行注册。",
    insecureHttpEnabledTitle: "已启用明文 HTTP",
    insecureHttpEnabledWarning: "接收端凭据和技能包在传输过程中不会被加密。仅限受保护的内部测试网络使用。",
    createRemoteMachine: "创建连接",
    connectRemoteMachine: "连接远端机器",
    remoteAgent: "代理类型",
    remoteTarget: "远端机器",
    remoteTargetId: "目标标识",
    remoteEnvironment: "上报环境",
    remoteEnvironmentPending: "注册后自动显示",
    remoteEnrollment: "连接状态",
    remoteDeliveryState: "分发状态",
    remoteObservedRevision: "已安装修订",
    remoteLastSeen: "最后同步",
    remoteNextStep: "在远端机器启用自动同步",
    remoteTargetPending: "等待注册",
    remoteTargetActive: "已连接",
    remoteTargetRevoked: "已撤销",
    remoteNoPublication: "尚未分发",
    remoteStateUnpublished: "已移除",
    remoteStatePending: "等待远端接收端",
    remoteStateCurrent: "已是当前版本",
    remoteStateUpdateAvailable: "有更新可分发",
    remoteStateDeliveryFailed: "分发失败",
    remoteStateConflict: "目标存在冲突",
    remoteStateDrifted: "已被本地修改",
    remoteStateIncompatible: "格式不兼容",
    remoteRevisionNone: "尚未安装",
    remoteNeverSeen: "从未同步",
    remoteGuidancePending: "请使用一次性口令在远端项目完成注册。若关闭前未保存口令，请撤销这条连接后重新添加机器。",
    remoteGuidanceReady: "自动同步会每隔几秒检查服务。准备好后直接点击“分发技能”。",
    remoteGuidanceSync: "远端接收端会自动应用当前请求。",
    remoteGuidanceCurrent: "当前修订已经安装，自动同步仍在运行。",
    remoteGuidanceProblem: "请先检查远端项目再重试。PowerContext 不会覆盖本地改动。",
    remoteGuidanceRemoved: "该目标上已不存在这项受管技能。",
    publishRemoteSkill: "分发技能",
    unpublishRemoteSkill: "请求移除",
    revokeRemoteMachine: "撤销机器",
    revokeRemoteMachineTitle: "撤销这台远端机器？",
    revokeRemoteMachineWarning: "撤销后该机器的凭据会立即失效。请先移除已分发技能，并等待自动同步完成。",
    remoteRevokeBlocked: "请先移除这台机器上的所有受管技能，并等待自动同步完成后再撤销。",
    remoteTargetsLoading: "正在加载远端机器状态...",
    remoteTargetsFailed: "无法加载远端机器状态（HTTP {status}）。",
    remoteCreating: "正在创建远端连接...",
    remoteCreateFailed: "无法创建远端连接（HTTP {status}）。",
    remotePublishing: "正在创建远端期望状态...",
    remotePublishRequested: "已请求分发，正在等待远端接收端自动安装第 {revision} 版。",
    remotePublishFailed: "无法分发该技能（HTTP {status}）。",
    remoteUnpublishing: "正在请求远端移除...",
    remoteUnpublishRequested: "已请求移除，正在等待远端接收端自动完成操作。",
    remoteUnpublishFailed: "无法请求远端移除（HTTP {status}）。",
    remotePublishSkillTitle: "将这项受管技能分发到远端机器？",
    remoteUnpublishSkillTitle: "从远端机器移除这项受管技能？",
    remoteRevoking: "正在撤销远端机器...",
    remoteRevoked: "{target} 的远端连接凭据已撤销。",
    remoteRevokeFailed: "无法撤销远端机器（HTTP {status}）。",
    remotePublishConfirmation: "将第 {revision} 版分发到 {target}。远端接收端会自动安装。",
    remotePublishDeprecatedConfirmation: "这项技能已废弃。仍要将第 {revision} 版分发到 {target} 吗？",
    remoteUnpublishConfirmation: "请求从 {target} 移除。远端接收端会自动安全移除未被修改的受管包。",
    enrollmentCodeWarning: "注册口令只显示一次且会很快过期。请在关闭前完成以下步骤。",
    expiresAt: "过期时间",
    installReceiver: "安装轻量接收端",
    runEnrollment: "在远端项目中运行注册命令",
    enterEnrollmentCode: "出现提示后，输入这段一次性口令",
    copy: "复制",
    copyCode: "复制口令",
    copied: "已复制。",
    copyFailed: "复制失败，请选中文本后手动复制。",
    done: "已完成或妥善保存",
    standardPackageRequired: "这项已批准技能创建于标准技能包支持之前。请先上传完整技能包并批准为下一修订，再进行发布。",
    publishedRevision: "已安装修订",
    destination: "安装路径",
    discovery: "发现状态",
    compatibility: "兼容性",
    compatible: "兼容",
    incompatible: "不兼容",
    unknown: "未知",
    manual_review_required: "需要人工检查",
    publishSkill: "安装到本机",
    unpublishSkill: "从本机移除",
    unpublishSkillTitle: "从本机移除这项受管技能？",
    unpublishConfirmation: "从 {target} 安全移除完全一致且未被修改的技能包。已批准修订和历史包仍会保留。",
    unpublishing: "正在从本机移除技能...",
    unpublicationSucceeded: "已从本机安全移除受管技能包。",
    unpublicationFailed: "无法安全移除该受管技能包（HTTP {status}）。",
    publishDeprecatedConfirmation: "这项技能已废弃。仍要为 {target} 安装第 {revision} 版吗？系统只会安全更新由 PowerContext 管理的内容，不会覆盖外部内容或本地改动。",
    updateSkill: "安装更新",
    refreshDiscovery: "刷新发现状态",
    publishSkillCandidate: "将这项受管技能安装到本机？",
    publishConfirmation: "为 {target} 安装第 {revision} 版。系统只会安全更新由 PowerContext 管理的内容，不会覆盖外部内容或已被本地修改的内容。",
    cancel: "取消",
    projectionUnpublished: "尚未安装",
    projectionCurrent: "已安装",
    projectionUpdateAvailable: "有更新可发布",
    projectionConflict: "目标存在冲突",
    projectionDrifted: "已被本地修改",
    projectionIncompatible: "格式不兼容",
    projectionConflictHint: "目标位置已被占用，或其中已有更新的修订。系统不会覆盖该内容。",
    projectionDriftedHint: "该受管技能包已在本地被修改。请先恢复内容，或选择其他目标。",
    projectionIncompatibleHint: "请修订技能名称或描述以满足技能包约束，然后批准新的修订。",
    discoveryAvailable: "已在配置的技能目录中可用",
    discoveryUnavailable: "技能包已存在，需要刷新发现状态",
    discoveryNotPublished: "尚不可用",
    publicationLoading: "正在检查发布状态...",
    publicationLoadFailed: "无法加载发布状态（HTTP {status}）。",
    publicationSucceeded: "受管技能第 {revision} 版已安装并可被发现。",
    publicationFailed: "无法安装该受管技能（HTTP {status}）。",
    publicationConflict: "发布目标已经变化，或包含系统不会覆盖的内容。",
    publishing: "正在将技能安装到本机...",
    loading: "正在加载技能...",
    refreshing: "正在刷新本地发现状态...",
    externalDiscoveryUnavailable: "暂时无法刷新本机技能目录，受管技能仍可正常使用。",
    externalSkillUnavailable: "该本地技能包已无法按登记的内容指纹精确解析。",
    authRejected: "服务器拒绝了该访问令牌。",
    requestFailed: "技能请求失败（HTTP {status}）。",
    serverUnavailable: "服务器无法访问。",
    retry: "重试",
    noScopes: "这里还没有可查看的工作。",
    scopeUnavailable: "选中的作用域不可用。"
  }
};

class SkillsRequestError extends Error {
  constructor(status, code = "", details = null) {
    super(`Skills request failed with HTTP ${status}`);
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
const library = document.getElementById("skills-library");
const signOut = document.getElementById("sign-out");
const scopeCombobox = document.getElementById("skills-scope-combobox");
const scopeSearchInput = document.getElementById("skills-scope-search");
const scopeOptions = document.getElementById("skills-scope-options");
const scopeSearchStatus = document.getElementById("skills-scope-search-status");
const searchInput = document.getElementById("skills-search");
const authorityFilter = document.getElementById("skills-authority-filter");
const refreshButton = document.getElementById("skills-refresh");
const liveStatus = document.getElementById("skills-live-status");
const managedCount = document.getElementById("skills-managed-count");
const externalCount = document.getElementById("skills-external-count");
const indexCaption = document.getElementById("skills-index-caption");
const loadingState = document.getElementById("skills-loading");
const skillList = document.getElementById("skills-list");
const emptyState = document.getElementById("skills-empty");
const detailEmpty = document.getElementById("skills-detail-empty");
const detailContent = document.getElementById("skills-detail-content");
const detailAuthority = document.getElementById("skills-detail-authority");
const detailName = document.getElementById("skills-detail-name");
const detailIdentity = document.getElementById("skills-detail-identity");
const detailStatus = document.getElementById("skills-detail-status");
const alert = document.getElementById("skills-alert");
const description = document.getElementById("skills-description");
const facts = document.getElementById("skills-facts");
const managedContent = document.getElementById("skills-managed-content");
const instructions = document.getElementById("skills-instructions");
const validation = document.getElementById("skills-validation");
const packageSection = document.getElementById("skills-package");
const packageStatus = document.getElementById("skills-package-status");
const packageFiles = document.getElementById("skills-package-files");
const packagePath = document.getElementById("skills-package-path");
const packagePreview = document.getElementById("skills-package-preview");
const governanceSection = document.getElementById("skills-governance");
const lifecycleState = document.getElementById("skills-lifecycle-state");
const replacementId = document.getElementById("skills-replacement-id");
const applyLifecycleButton = document.getElementById("skills-apply-lifecycle");
const governanceStatus = document.getElementById("skills-governance-status");
const lineage = document.getElementById("skills-lineage");
const sourceRefs = document.getElementById("skills-source-refs");
const artifactRefs = document.getElementById("skills-artifact-refs");
const delivery = document.getElementById("skills-delivery");
const deliveryMode = document.getElementById("skills-delivery-mode");
const projectionState = document.getElementById("skills-projection-state");
const deliveryStatus = document.getElementById("skills-delivery-status");
const localDelivery = document.getElementById("skills-local-delivery");
const deliveryEmpty = document.getElementById("skills-delivery-empty");
const deliveryContent = document.getElementById("skills-delivery-content");
const deliveryTarget = document.getElementById("skills-delivery-target");
const publishedRevision = document.getElementById("skills-published-revision");
const discovery = document.getElementById("skills-discovery");
const compatibility = document.getElementById("skills-compatibility");
const compatibilityReasons = document.getElementById("skills-compatibility-reasons");
const destination = document.getElementById("skills-destination");
const createRevisionButton = document.getElementById("skills-create-revision");
const revisionPackageInput = document.getElementById("skills-revision-package");
const unpublishButton = document.getElementById("skills-unpublish");
const publishButton = document.getElementById("skills-publish");
const remoteDelivery = document.getElementById("skills-remote-delivery");
const insecureHttpWarning = document.getElementById("skills-insecure-http-warning");
const remoteRefreshButton = document.getElementById("skills-remote-refresh");
const remoteEmpty = document.getElementById("skills-remote-empty");
const remoteContent = document.getElementById("skills-remote-content");
const remoteTarget = document.getElementById("skills-remote-target");
const remoteTargetSearch = document.getElementById("skills-remote-target-search");
const remoteEnrollment = document.getElementById("skills-remote-enrollment");
const remotePublicationState = document.getElementById("skills-remote-publication-state");
const remoteObservedRevision = document.getElementById("skills-remote-observed-revision");
const remoteLastSeen = document.getElementById("skills-remote-last-seen");
const remoteEnvironment = document.getElementById("skills-remote-environment");
const remoteTargetId = document.getElementById("skills-remote-target-id");
const remoteGuidance = document.getElementById("skills-remote-guidance");
const remoteAddButtons = [
  document.getElementById("skills-remote-add-empty"),
  document.getElementById("skills-remote-add")
];
const remotePublishButton = document.getElementById("skills-remote-publish");
const remoteUnpublishButton = document.getElementById("skills-remote-unpublish");
const remoteRevokeButton = document.getElementById("skills-remote-revoke");
const remoteRenameButton = document.getElementById("skills-remote-rename");
const publishDialog = document.getElementById("skills-publish-dialog");
const publishDialogTitle = publishDialog.querySelector("h2");
const publishConfirmation = document.getElementById("skills-publish-confirmation");
const confirmPublishButton = document.getElementById("skills-confirm-publish");
const retireDialog = document.getElementById("skills-retire-dialog");
const confirmRetireButton = document.getElementById("skills-confirm-retire");
const remoteCreateDialog = document.getElementById("skills-remote-create-dialog");
const remoteDisplayName = document.getElementById("skills-remote-display-name");
const remoteAgentKind = document.getElementById("skills-remote-agent-kind");
const remoteCreateError = document.getElementById("skills-remote-create-error");
const confirmRemoteCreateButton = document.getElementById("skills-confirm-remote-create");
const remoteRenameDialog = document.getElementById("skills-remote-rename-dialog");
const remoteRenameName = document.getElementById("skills-remote-rename-name");
const remoteRenameError = document.getElementById("skills-remote-rename-error");
const confirmRemoteRenameButton = document.getElementById("skills-confirm-remote-rename");
const remoteEnrollmentDialog = document.getElementById("skills-remote-enrollment-dialog");
const enrollmentTargetId = document.getElementById("skills-enrollment-target-id");
const enrollmentExpires = document.getElementById("skills-enrollment-expires");
const enrollmentConnection = document.getElementById("skills-enrollment-connection");
const enrollmentConnectionMessage = document.getElementById("skills-enrollment-connection-message");
const receiverInstallCommand = document.getElementById("skills-receiver-install-command");
const enrollmentCommand = document.getElementById("skills-enrollment-command");
const enrollmentCode = document.getElementById("skills-enrollment-code");
const copyStatus = document.getElementById("skills-copy-status");
const finishEnrollmentButton = document.getElementById("skills-finish-enrollment");
const remoteRevokeDialog = document.getElementById("skills-remote-revoke-dialog");
const confirmRemoteRevokeButton = document.getElementById("skills-confirm-remote-revoke");

const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const allowInsecureHttp = library.dataset.allowInsecureHttp === "true";
const scopePreferenceKey = "powercontext.skills.scope";
const deliveryModePreferenceKey = "powercontext.skills.delivery-mode";
const scopeOptionRenderLimit = 50;
const remoteFastRefreshMilliseconds = 2000;
const remoteIdleRefreshMilliseconds = 10000;

let scopes = [];
let records = [];
let currentScopeId = "";
let selectedKey = "";
let projectionView = null;
let remoteTargets = [];
let selectedRemoteTargetId = "";
let packageManifest = null;
let packageSelectedPath = "";
let packageError = null;
let pendingPublicationAction = "publish";
let pendingPublicationChannel = "local";
let remoteFeedback = null;
let currentAlert = null;
let currentPageStatus = null;
let currentAuthError = null;
let libraryBusy = false;
let projectionBusy = false;
let remoteBusy = false;
let remoteActionBusy = false;
let actionBusy = false;
let packageBusy = false;
let lifecycleBusy = false;
let revisionBusy = false;
let scopeActiveIndex = -1;
let remoteRefreshTimer = null;

const scopeRequests = createRequestGate();
const libraryRequests = createRequestGate();
const projectionRequests = createRequestGate();
const remoteRequests = createRequestGate();
const packageRequests = createRequestGate();
const packagePreviewRequests = createRequestGate();
const ui = createPageUi(translations, () => {
  renderAuthError();
  renderPageStatus();
  renderScopeCombobox();
  renderLibrary();
  renderDetail();
});
const {formatDateTime, formatNumber, translate} = ui;
deliveryMode.value = preferredDeliveryMode();

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

scopeSearchInput.addEventListener("keydown", handleScopeSearchKeydown);

scopeCombobox.addEventListener("focusout", (event) => {
  if (!scopeCombobox.contains(event.relatedTarget)) {
    closeScopeOptions({restoreSelection: true});
  }
});

searchInput.addEventListener("input", () => {
  selectedKey = filteredRecords().some((record) => record.key === selectedKey) ? selectedKey : "";
  renderLibrary();
  ensureSelection();
});

authorityFilter.addEventListener("change", () => {
  selectedKey = filteredRecords().some((record) => record.key === selectedKey) ? selectedKey : "";
  renderLibrary();
  ensureSelection();
});

refreshButton.addEventListener("click", () => {
  void loadLibrary({refreshDiscovery: true, preserveSelection: true});
});

deliveryMode.addEventListener("change", () => {
  rememberDeliveryMode(deliveryMode.value);
  renderDelivery();
  scheduleRemoteRefresh(0);
  if (deliveryMode.value === "remote" && !remoteBusy && remoteTargets.length === 0) {
    void loadRemoteTargets();
  }
});
deliveryTarget.addEventListener("change", renderDelivery);
remoteTarget.addEventListener("change", () => {
  selectedRemoteTargetId = remoteTarget.value;
  remoteFeedback = null;
  renderDelivery();
});
remoteTargetSearch.addEventListener("input", selectRemoteTargetFromSearch);
remoteRefreshButton.addEventListener("click", () => void loadRemoteTargets({preserveTarget: true}));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopRemoteRefresh();
    return;
  }
  scheduleRemoteRefresh(0);
});
for (const button of remoteAddButtons) {
  button.addEventListener("click", () => {
    remoteCreateError.textContent = "";
    remoteDisplayName.value = "";
    remoteCreateDialog.showModal();
    remoteDisplayName.focus();
  });
}
remoteRenameButton.addEventListener("click", () => {
  const status = selectedRemoteTargetStatus();
  if (!status) {
    return;
  }
  remoteRenameError.textContent = "";
  remoteRenameName.value = status.target.display_name;
  remoteRenameDialog.showModal();
  remoteRenameName.select();
});
lifecycleState.addEventListener("change", renderGovernanceControls);

createRevisionButton.addEventListener("click", () => {
  const record = selectedRecord();
  if (!record || record.authority !== "managed" || record.governance.lifecycle_state === "retired") {
    return;
  }
  revisionPackageInput.click();
});

revisionPackageInput.addEventListener("change", () => {
  const [file] = revisionPackageInput.files;
  revisionPackageInput.value = "";
  if (file) {
    void uploadRevisionPackage(file);
  }
});

applyLifecycleButton.addEventListener("click", () => {
  if (lifecycleState.value === "retired") {
    retireDialog.showModal();
    return;
  }
  void applyLifecycle();
});

confirmRetireButton.addEventListener("click", (event) => {
  event.preventDefault();
  retireDialog.close();
  void applyLifecycle();
});

publishButton.addEventListener("click", () => {
  const record = selectedRecord();
  const target = selectedProjectionTarget();
  if (!record || record.authority !== "managed" || !target || !canPublishProjection(target)) {
    return;
  }
  const targetLabel = localTargetLabel(target, record.name);
  publishConfirmation.textContent = translate("publishConfirmation", {
    revision: record.candidate.result_artifact.revision,
    target: targetLabel
  });
  if (record.governance.lifecycle_state === "deprecated") {
    publishConfirmation.textContent = translate("publishDeprecatedConfirmation", {
      revision: record.candidate.result_artifact.revision,
      target: targetLabel
    });
  }
  pendingPublicationAction = "publish";
  pendingPublicationChannel = "local";
  publishDialogTitle.textContent = translate("publishSkillCandidate");
  confirmPublishButton.textContent = translate("publishSkill");
  publishDialog.showModal();
});

unpublishButton.addEventListener("click", () => {
  const record = selectedRecord();
  const target = selectedProjectionTarget();
  if (!record || !target || !canUnpublishProjection(target)) {
    return;
  }
  pendingPublicationAction = "unpublish";
  pendingPublicationChannel = "local";
  publishDialogTitle.textContent = translate("unpublishSkillTitle");
  publishConfirmation.textContent = translate("unpublishConfirmation", {
    target: localTargetLabel(target, record.name)
  });
  confirmPublishButton.textContent = translate("unpublishSkill");
  publishDialog.showModal();
});

remotePublishButton.addEventListener("click", () => openRemotePublicationDialog("publish"));
remoteUnpublishButton.addEventListener("click", () => openRemotePublicationDialog("unpublish"));
remoteRevokeButton.addEventListener("click", () => {
  const status = selectedRemoteTargetStatus();
  if (!status || !canRevokeRemoteTarget(status)) {
    remoteFeedback = {key: "remoteRevokeBlocked", tone: "error"};
    renderDelivery();
    return;
  }
  remoteRevokeDialog.showModal();
});

confirmPublishButton.addEventListener("click", (event) => {
  event.preventDefault();
  publishDialog.close();
  if (pendingPublicationChannel === "remote") {
    void (pendingPublicationAction === "unpublish" ? unpublishRemoteSkill() : publishRemoteSkill());
    return;
  }
  void (pendingPublicationAction === "unpublish" ? unpublishSelectedSkill() : publishSelectedSkill());
});

confirmRemoteCreateButton.addEventListener("click", (event) => {
  event.preventDefault();
  void createRemoteTarget();
});

confirmRemoteRenameButton.addEventListener("click", (event) => {
  event.preventDefault();
  void renameRemoteTarget();
});

confirmRemoteRevokeButton.addEventListener("click", (event) => {
  event.preventDefault();
  remoteRevokeDialog.close();
  void revokeRemoteTarget();
});

document.getElementById("skills-copy-install-command").addEventListener("click", () => {
  void copyEnrollmentValue(receiverInstallCommand.textContent);
});
document.getElementById("skills-copy-enrollment-command").addEventListener("click", () => {
  void copyEnrollmentValue(enrollmentCommand.textContent);
});
document.getElementById("skills-copy-enrollment-code").addEventListener("click", () => {
  void copyEnrollmentValue(enrollmentCode.textContent);
});
finishEnrollmentButton.addEventListener("click", () => void loadRemoteTargets({preserveTarget: true}));
remoteEnrollmentDialog.addEventListener("close", clearEnrollmentSecrets);

pageStatusRetry.addEventListener("click", () => {
  void authenticate(readServerToken(), currentScopeId);
});

authForm.addEventListener("submit", (event) => {
  event.preventDefault();
  authError.textContent = "";
  void authenticate(tokenInput.value, preferredScopeId());
});

signOut.addEventListener("click", () => {
  clearServerToken();
  tokenInput.value = "";
  showLogin();
});

async function authenticate(token, preferred = "") {
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
    currentScopeId = scopes.some((scope) => scope.scope_id === preferred)
      ? preferred
      : scopes[0].scope_id;
    rememberScope(currentScopeId);
    showLibrary();
    renderScopeCombobox();
    await loadLibrary();
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

async function loadLibrary({refreshDiscovery = false, preserveSelection = false} = {}) {
  if (!currentScopeId || libraryBusy) {
    return;
  }
  const request = libraryRequests.start();
  const previousSelection = preserveSelection ? selectedKey : "";
  setLibraryBusy(true, refreshDiscovery ? "refreshing" : "loading");
  currentAlert = null;
  try {
    const managedPromise = loadApprovedManagedSkills();
    const externalPromise = loadExternalSkills(refreshDiscovery);
    const [managedResult, externalResult] = await Promise.allSettled([managedPromise, externalPromise]);
    if (!request.isCurrent()) {
      return;
    }
    if (managedResult.status === "rejected") {
      throw managedResult.reason;
    }
    const externalRecords = externalResult.status === "fulfilled" ? externalResult.value : [];
    if (externalResult.status === "rejected") {
      if (handleAuthenticationError(externalResult.reason)) {
        return;
      }
      if (!(externalResult.reason instanceof SkillsRequestError)
        || externalResult.reason.code !== "external_skill_registry_unavailable") {
        currentAlert = {key: "externalDiscoveryUnavailable", tone: "warning"};
      }
    }
    records = [...managedResult.value, ...externalRecords].sort(compareRecords);
    selectedKey = records.some((record) => record.key === previousSelection)
      ? previousSelection
      : (filteredRecords()[0]?.key || records[0]?.key || "");
    projectionView = null;
    packageManifest = null;
    packageSelectedPath = "";
    packageError = null;
    lifecycleState.dataset.recordKey = "";
    renderLibrary();
    renderDetail();
    await Promise.all([loadProjectionStatus(), loadPackageManifest(), loadRemoteTargets({preserveTarget: true})]);
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    showPageStatus(error instanceof SkillsRequestError ? "requestFailed" : "serverUnavailable", {
      status: error.status
    }, true);
  } finally {
    if (request.isCurrent()) {
      setLibraryBusy(false);
    }
  }
}

async function loadApprovedManagedSkills() {
  const entries = await requestJson("/dashboard/skills/library", {
    scope_id: currentScopeId,
    include_deprecated: true,
    limit: 200
  });
  return entries.map((entry) => {
    const candidate = {
      candidate_id: null,
      family: "skill",
      proposal: entry.content,
      result_artifact: entry.artifact,
      source_refs: entry.sources.map((reference) => ({
        name: reference.source_type,
        source_id: reference.source_id
      })),
      artifact_refs: entry.artifacts
    };
    return {
      authority: "managed",
      origin: entry.origin,
      candidate,
      governance: entry.governance,
      key: `managed:${candidate.result_artifact.artifact_id}`,
      name: candidate.proposal.name,
      description: candidate.proposal.description,
      identity: formatArtifactReference(candidate.result_artifact),
      searchText: [
        candidate.proposal.name,
        candidate.proposal.description,
        candidate.candidate_id,
        formatArtifactReference(candidate.result_artifact),
        entry.origin.kind,
        entry.origin.registration?.host_id,
        entry.origin.registration?.agent_kind,
        entry.origin.registration?.locator,
        entry.origin.registration?.external_skill_id
      ].join("\n").toLocaleLowerCase()
    };
  });
}

async function loadExternalSkills(refreshDiscovery) {
  if (refreshDiscovery) {
    await requestJson("/v1/external-skills/scan", {scope_id: currentScopeId});
  }
  const response = await requestJson("/v1/external-skills/list", {
    scope_id: currentScopeId,
    include_unavailable: true
  });
  return response.skills.map((resolution) => {
    const registration = resolution.registration;
    return {
      authority: "external",
      origin: {kind: "external", registration},
      resolution,
      key: `external:${registration.external_skill_id}`,
      name: registration.name,
      description: registration.description,
      identity: registration.external_skill_id,
      searchText: [
        registration.name,
        registration.description,
        registration.external_skill_id,
        registration.host_id,
        registration.agent_kind,
        registration.locator,
        registration.fingerprint
      ].join("\n").toLocaleLowerCase()
    };
  });
}

async function loadProjectionStatus() {
  projectionRequests.cancel();
  projectionView = null;
  const record = selectedRecord();
  renderDelivery();
  if (!record || record.authority !== "managed") {
    return;
  }
  const request = projectionRequests.start();
  projectionBusy = true;
  renderDelivery();
  try {
    const view = await requestJson("/dashboard/skill-projections/status", {
      scope_id: currentScopeId,
      candidate_id: record.candidate.candidate_id,
      artifact: record.candidate.result_artifact
    });
    if (!request.isCurrent() || selectedKey !== record.key) {
      return;
    }
    projectionView = view;
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    currentAlert = {
      key: error instanceof SkillsRequestError ? "publicationLoadFailed" : "serverUnavailable",
      values: {status: error.status},
      tone: "error"
    };
  } finally {
    if (request.isCurrent()) {
      projectionBusy = false;
      renderDetail();
    }
  }
}

async function loadRemoteTargets({preserveTarget = false, silent = false} = {}) {
  if (!currentScopeId) {
    return;
  }
  if (remoteBusy) {
    scheduleRemoteRefresh(1000);
    return;
  }
  const request = remoteRequests.start();
  const previousTargetId = preserveTarget ? selectedRemoteTargetId : "";
  remoteBusy = true;
  if (!silent) {
    remoteFeedback = null;
    renderDelivery();
  }
  try {
    const response = await requestJson("/v1/skill/remote/targets", {
      scope_id: currentScopeId,
      limit: 200
    });
    if (!request.isCurrent()) {
      return;
    }
    remoteTargets = response.targets;
    const availableTargets = remoteTargets.filter((status) => status.target.state !== "revoked");
    selectedRemoteTargetId = availableTargets.some((status) => status.target.target_id === previousTargetId)
      ? previousTargetId
      : (availableTargets[0]?.target.target_id || "");
    settleRemoteFeedback();
  } catch (error) {
    if (!request.isCurrent()) {
      return;
    }
    if (handleAuthenticationError(error)) {
      remoteBusy = false;
      return;
    }
    if (!silent) {
      remoteFeedback = {
        key: error instanceof SkillsRequestError ? "remoteTargetsFailed" : "serverUnavailable",
        values: {status: error.status},
        tone: "error"
      };
    }
  } finally {
    if (request.isCurrent()) {
      remoteBusy = false;
      renderDelivery();
      scheduleRemoteRefresh();
    }
  }
}

function settleRemoteFeedback() {
  if (!["remotePublishRequested", "remoteUnpublishRequested"].includes(remoteFeedback?.key)) {
    return;
  }
  const publication = selectedRemotePublication();
  if (publication && !["pending", "update_available"].includes(publication.state)) {
    remoteFeedback = null;
  }
}

function scheduleRemoteRefresh(delay = remoteRefreshDelay()) {
  stopRemoteRefresh();
  if (!shouldAutoRefreshRemoteTargets()) {
    return;
  }
  remoteRefreshTimer = window.setTimeout(() => {
    remoteRefreshTimer = null;
    void loadRemoteTargets({preserveTarget: true, silent: true});
  }, delay);
}

function stopRemoteRefresh() {
  if (remoteRefreshTimer !== null) {
    window.clearTimeout(remoteRefreshTimer);
    remoteRefreshTimer = null;
  }
}

function shouldAutoRefreshRemoteTargets() {
  return Boolean(currentScopeId && deliveryMode.value === "remote" && !document.hidden && !library.hidden);
}

function remoteRefreshDelay() {
  const pending = remoteTargets.some((status) => (
    status.target.state === "pending"
    || status.publications.some((publication) => ["pending", "update_available"].includes(publication.state))
  ));
  return pending ? remoteFastRefreshMilliseconds : remoteIdleRefreshMilliseconds;
}

async function loadPackageManifest() {
  packageRequests.cancel();
  packagePreviewRequests.cancel();
  packageManifest = null;
  packageSelectedPath = "";
  packageError = null;
  const record = selectedRecord();
  renderPackageBrowser();
  if (!record || record.authority !== "managed" || !record.candidate.proposal.package) {
    return;
  }
  const request = packageRequests.start();
  packageBusy = true;
  renderPackageBrowser();
  try {
    const manifest = await requestJson("/dashboard/skill-packages/manifest", {
      scope_id: currentScopeId,
      package: record.candidate.proposal.package
    });
    if (!request.isCurrent() || selectedKey !== record.key) {
      return;
    }
    packageManifest = manifest;
    packageSelectedPath = manifest.files.some((file) => file.path === "SKILL.md")
      ? "SKILL.md"
      : (manifest.files[0]?.path || "");
    renderPackageBrowser();
    if (packageSelectedPath) {
      await loadPackagePreview(packageSelectedPath);
    }
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    packageError = {
      key: error instanceof SkillsRequestError ? "packageLoadFailed" : "serverUnavailable",
      values: {status: error.status}
    };
  } finally {
    if (request.isCurrent()) {
      packageBusy = false;
      renderPackageBrowser();
    }
  }
}

async function loadPackagePreview(path) {
  const record = selectedRecord();
  if (!record || record.authority !== "managed" || !record.candidate.proposal.package) {
    return;
  }
  const request = packagePreviewRequests.start();
  packageSelectedPath = path;
  packagePath.textContent = path;
  packagePreview.textContent = translate("packageLoading");
  renderPackageFileSelection();
  try {
    const preview = await requestJson("/dashboard/skill-packages/preview", {
      scope_id: currentScopeId,
      package: record.candidate.proposal.package,
      path
    });
    if (!request.isCurrent() || selectedKey !== record.key || packageSelectedPath !== path) {
      return;
    }
    const body = preview.binary ? translate("packageBinary") : (preview.content || "");
    packagePreview.textContent = preview.truncated
      ? `${body}\n\n${translate("packageTruncated")}`
      : body;
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    packagePreview.textContent = translate(
      error instanceof SkillsRequestError ? "packageLoadFailed" : "serverUnavailable",
      {status: error.status}
    );
  }
}

async function publishSelectedSkill() {
  const record = selectedRecord();
  const target = selectedProjectionTarget();
  if (!record || record.authority !== "managed" || !target || !canPublishProjection(target)) {
    return;
  }
  const request = projectionRequests.start();
  actionBusy = true;
  currentAlert = null;
  liveStatus.textContent = translate("publishing");
  renderDelivery();
  try {
    const view = await requestJson("/dashboard/skill-projections/publish", {
      scope_id: currentScopeId,
      candidate_id: record.candidate.candidate_id,
      artifact: record.candidate.result_artifact,
      target_id: target.target_id,
      allow_deprecated: record.governance.lifecycle_state === "deprecated"
    });
    if (!request.isCurrent() || selectedKey !== record.key) {
      return;
    }
    projectionView = view;
    currentAlert = {
      key: "publicationSucceeded",
      values: {revision: record.candidate.result_artifact.revision},
      tone: "success"
    };
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    currentAlert = error instanceof SkillsRequestError && error.status === 409
      ? {key: "publicationConflict", tone: "error"}
      : {
        key: error instanceof SkillsRequestError ? "publicationFailed" : "serverUnavailable",
        values: {status: error.status},
        tone: "error"
      };
  } finally {
    if (request.isCurrent()) {
      actionBusy = false;
      liveStatus.textContent = "";
      renderDetail();
    }
  }
}

async function unpublishSelectedSkill() {
  const record = selectedRecord();
  const target = selectedProjectionTarget();
  if (!record || record.authority !== "managed" || !target || !canUnpublishProjection(target)) {
    return;
  }
  const request = projectionRequests.start();
  actionBusy = true;
  currentAlert = null;
  liveStatus.textContent = translate("unpublishing");
  renderDelivery();
  try {
    const view = await requestJson("/dashboard/skill-projections/unpublish", {
      scope_id: currentScopeId,
      candidate_id: record.candidate.candidate_id,
      artifact: record.candidate.result_artifact,
      target_id: target.target_id
    });
    if (!request.isCurrent() || selectedKey !== record.key) {
      return;
    }
    projectionView = view;
    currentAlert = {key: "unpublicationSucceeded", tone: "success"};
  } catch (error) {
    if (!request.isCurrent() || handleAuthenticationError(error)) {
      return;
    }
    currentAlert = error instanceof SkillsRequestError && error.status === 409
      ? {key: "publicationConflict", tone: "error"}
      : {
        key: error instanceof SkillsRequestError ? "unpublicationFailed" : "serverUnavailable",
        values: {status: error.status},
        tone: "error"
      };
  } finally {
    if (request.isCurrent()) {
      actionBusy = false;
      liveStatus.textContent = "";
      renderDetail();
    }
  }
}

function openRemotePublicationDialog(action) {
  const record = selectedRecord();
  const status = selectedRemoteTargetStatus();
  const publication = selectedRemotePublication(status, record);
  if (!record || record.authority !== "managed" || !status) {
    return;
  }
  if (action === "publish" && !canPublishRemote(record, status, publication)) {
    return;
  }
  if (action === "unpublish" && (!publication || publication.desired_state !== "published")) {
    return;
  }
  const target = remoteTargetLabel(status.target);
  pendingPublicationAction = action;
  pendingPublicationChannel = "remote";
  if (action === "unpublish") {
    publishDialogTitle.textContent = translate("remoteUnpublishSkillTitle");
    publishConfirmation.textContent = translate("remoteUnpublishConfirmation", {target});
    confirmPublishButton.textContent = translate("unpublishRemoteSkill");
  } else {
    publishDialogTitle.textContent = translate("remotePublishSkillTitle");
    const confirmationKey = record.governance.lifecycle_state === "deprecated"
      ? "remotePublishDeprecatedConfirmation"
      : "remotePublishConfirmation";
    publishConfirmation.textContent = translate(confirmationKey, {
      revision: record.candidate.result_artifact.revision,
      target
    });
    confirmPublishButton.textContent = translate("publishRemoteSkill");
  }
  publishDialog.showModal();
}

async function createRemoteTarget() {
  const displayName = remoteDisplayName.value.trim();
  if (!displayName) {
    remoteCreateError.textContent = translate("remoteMachineNameRequired");
    remoteDisplayName.focus();
    return;
  }
  remoteActionBusy = true;
  remoteCreateError.textContent = translate("remoteCreating");
  confirmRemoteCreateButton.disabled = true;
  try {
    const enrollment = await requestJson("/v1/skill/remote/target/create", {
      scope_id: currentScopeId,
      agent_kind: remoteAgentKind.value,
      display_name: displayName
    });
    remoteTargets.push({target: enrollment.target, publications: []});
    selectedRemoteTargetId = enrollment.target.target_id;
    remoteCreateDialog.close();
    showRemoteEnrollment(enrollment);
    remoteFeedback = null;
    renderDelivery();
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    remoteCreateError.textContent = translate(
      error instanceof SkillsRequestError ? "remoteCreateFailed" : "serverUnavailable",
      {status: error.status}
    );
  } finally {
    remoteActionBusy = false;
    confirmRemoteCreateButton.disabled = false;
    renderDelivery();
  }
}

async function renameRemoteTarget() {
  const status = selectedRemoteTargetStatus();
  const displayName = remoteRenameName.value.trim();
  if (!status) {
    return;
  }
  if (!displayName) {
    remoteRenameError.textContent = translate("remoteMachineNameRequired");
    remoteRenameName.focus();
    return;
  }
  remoteActionBusy = true;
  remoteRenameError.textContent = translate("remoteRenaming");
  confirmRemoteRenameButton.disabled = true;
  try {
    status.target = await requestJson("/v1/skill/remote/target/rename", {
      scope_id: currentScopeId,
      target_id: status.target.target_id,
      display_name: displayName,
      expected_generation: status.target.generation
    });
    remoteRenameDialog.close();
    remoteFeedback = null;
    renderDelivery();
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    remoteRenameError.textContent = translate(
      error instanceof SkillsRequestError ? "remoteRenameFailed" : "serverUnavailable",
      {status: error.status}
    );
  } finally {
    remoteActionBusy = false;
    confirmRemoteRenameButton.disabled = false;
    renderDelivery();
  }
}

function showRemoteEnrollment(enrollment) {
  const serverUrl = resolvedRemoteServerUrl();
  const insecureHttp = usesInsecureRemoteHttp(serverUrl);
  enrollmentTargetId.textContent = enrollment.target.target_id;
  enrollmentExpires.dateTime = enrollment.enrollment_expires_at;
  enrollmentExpires.textContent = formatDateTime(enrollment.enrollment_expires_at);
  enrollmentConnection.dataset.tone = serverUrl && !insecureHttp ? "ready" : "warning";
  enrollmentConnectionMessage.textContent = insecureHttp
    ? translate("receiverConnectionInsecure", {url: serverUrl})
    : serverUrl
      ? translate("receiverConnectionReady", {url: serverUrl})
    : translate("receiverConnectionNeedsSetup");
  enrollmentCommand.textContent = serverUrl
    ? `powercontext --server-url ${shellQuote(serverUrl)} skill remote-enroll --workspace "$PWD" --install-service${insecureHttp ? " --allow-insecure-http" : ""}`
    : `powercontext skill remote-enroll --workspace "$PWD" --install-service`;
  enrollmentCode.textContent = enrollment.enrollment_code;
  copyStatus.textContent = "";
  remoteEnrollmentDialog.showModal();
}

async function copyEnrollmentValue(value) {
  try {
    await navigator.clipboard.writeText(value || "");
    copyStatus.textContent = translate("copied");
  } catch (error) {
    copyStatus.textContent = translate("copyFailed");
  }
}

function clearEnrollmentSecrets() {
  enrollmentCode.textContent = "";
  enrollmentCommand.textContent = "";
  enrollmentTargetId.textContent = "";
  enrollmentExpires.textContent = "";
  enrollmentExpires.removeAttribute("datetime");
  enrollmentConnection.dataset.tone = "";
  enrollmentConnectionMessage.textContent = "";
  copyStatus.textContent = "";
}

async function publishRemoteSkill() {
  const record = selectedRecord();
  const status = selectedRemoteTargetStatus();
  const publication = selectedRemotePublication(status, record);
  if (!record || record.authority !== "managed" || !status || !canPublishRemote(record, status, publication)) {
    return;
  }
  remoteActionBusy = true;
  remoteFeedback = {key: "remotePublishing"};
  renderDelivery();
  try {
    const updated = await requestJson("/v1/skill/remote/publication/publish", {
      scope_id: currentScopeId,
      target_id: status.target.target_id,
      artifact: record.candidate.result_artifact,
      expected_generation: publication?.generation ?? null,
      allow_deprecated: record.governance.lifecycle_state === "deprecated"
    });
    replaceRemotePublication(status, updated);
    remoteFeedback = {
      key: "remotePublishRequested",
      values: {revision: record.candidate.result_artifact.revision},
      tone: "success"
    };
    scheduleRemoteRefresh(500);
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    remoteFeedback = {
      key: error instanceof SkillsRequestError ? "remotePublishFailed" : "serverUnavailable",
      values: {status: error.status},
      tone: "error"
    };
  } finally {
    remoteActionBusy = false;
    renderDelivery();
  }
}

async function unpublishRemoteSkill() {
  const record = selectedRecord();
  const status = selectedRemoteTargetStatus();
  const publication = selectedRemotePublication(status, record);
  if (!record || record.authority !== "managed" || !status || !publication) {
    return;
  }
  remoteActionBusy = true;
  remoteFeedback = {key: "remoteUnpublishing"};
  renderDelivery();
  try {
    const updated = await requestJson("/v1/skill/remote/publication/unpublish", {
      scope_id: currentScopeId,
      target_id: status.target.target_id,
      artifact_id: record.candidate.result_artifact.artifact_id,
      expected_generation: publication.generation
    });
    replaceRemotePublication(status, updated);
    remoteFeedback = {key: "remoteUnpublishRequested", tone: "success"};
    scheduleRemoteRefresh(500);
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    remoteFeedback = {
      key: error instanceof SkillsRequestError ? "remoteUnpublishFailed" : "serverUnavailable",
      values: {status: error.status},
      tone: "error"
    };
  } finally {
    remoteActionBusy = false;
    renderDelivery();
  }
}

async function revokeRemoteTarget() {
  const status = selectedRemoteTargetStatus();
  if (!status || !canRevokeRemoteTarget(status)) {
    return;
  }
  const revokedTargetLabel = remoteTargetLabel(status.target);
  remoteActionBusy = true;
  remoteFeedback = {key: "remoteRevoking"};
  renderDelivery();
  try {
    status.target = await requestJson("/v1/skill/remote/target/revoke", {
      scope_id: currentScopeId,
      target_id: status.target.target_id,
      expected_generation: status.target.generation
    });
    const next = remoteTargets.find((candidate) => candidate.target.state !== "revoked");
    selectedRemoteTargetId = next?.target.target_id || "";
    remoteFeedback = {key: "remoteRevoked", values: {target: revokedTargetLabel}, tone: "success"};
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    remoteFeedback = {
      key: error instanceof SkillsRequestError ? "remoteRevokeFailed" : "serverUnavailable",
      values: {status: error.status},
      tone: "error"
    };
  } finally {
    remoteActionBusy = false;
    renderDelivery();
  }
}

function replaceRemotePublication(status, publication) {
  const index = status.publications.findIndex((candidate) => candidate.artifact_id === publication.artifact_id);
  if (index === -1) {
    status.publications.push(publication);
  } else {
    status.publications[index] = publication;
  }
}

function normalizeRemoteServerUrl(value) {
  try {
    const url = new URL(value.trim());
    const loopback = isLoopbackHostname(url.hostname);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && (loopback || allowInsecureHttp))) {
      return "";
    }
    if (url.username || url.password || url.search || url.hash) {
      return "";
    }
    return url.toString().replace(/\/$/, "");
  } catch (error) {
    return "";
  }
}

function resolvedRemoteServerUrl() {
  const configured = normalizeRemoteServerUrl(library.dataset.publicServerUrl || "");
  if (configured) {
    return configured;
  }
  try {
    return normalizeRemoteServerUrl(window.location.origin);
  } catch (error) {
    return "";
  }
}

function usesInsecureRemoteHttp(value) {
  if (!value) {
    return false;
  }
  try {
    const url = new URL(value);
    return url.protocol === "http:" && !isLoopbackHostname(url.hostname);
  } catch (error) {
    return false;
  }
}

function isLoopbackHostname(hostname) {
  return ["127.0.0.1", "::1", "[::1]", "localhost"].includes(hostname.toLocaleLowerCase());
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'"'"'`)}'`;
}

async function applyLifecycle() {
  const record = selectedRecord();
  if (!record || record.authority !== "managed" || lifecycleBusy) {
    return;
  }
  lifecycleBusy = true;
  governanceStatus.textContent = translate("lifecycleUpdating");
  renderGovernanceControls();
  try {
    const governance = await requestJson("/dashboard/skills/lifecycle", {
      scope_id: currentScopeId,
      artifact_id: record.candidate.result_artifact.artifact_id,
      expected_generation: record.governance.governance_generation,
      lifecycle_state: lifecycleState.value,
      replacement_artifact_id: lifecycleState.value === "deprecated"
        ? (replacementId.value.trim() || null)
        : null
    });
    if (selectedKey !== record.key) {
      return;
    }
    record.governance = governance;
    lifecycleState.dataset.recordKey = "";
    governanceStatus.textContent = translate("lifecycleUpdated", {
      state: translate(governance.lifecycle_state)
    });
    renderLibrary();
    renderDetail();
  } catch (error) {
    if (handleAuthenticationError(error)) {
      return;
    }
    governanceStatus.textContent = translate(
      error instanceof SkillsRequestError && error.status === 409
        ? "lifecycleConflict"
        : (error instanceof SkillsRequestError ? "lifecycleFailed" : "serverUnavailable"),
      {status: error.status}
    );
  } finally {
    lifecycleBusy = false;
    renderGovernanceControls();
  }
}

async function uploadRevisionPackage(file) {
  const record = selectedRecord();
  if (!record || record.authority !== "managed" || revisionBusy) {
    return;
  }
  revisionBusy = true;
  liveStatus.textContent = translate("revisionUploading");
  renderDelivery();
  try {
    const archive = new Uint8Array(await file.arrayBuffer());
    const candidate = await requestJson("/v1/skill/package/propose", {
      scope_id: currentScopeId,
      archive_base64: bytesToBase64(archive),
      reason: "Complete successor package uploaded from the Skills Library.",
      target: record.candidate.result_artifact
    });
    const params = new URLSearchParams({
      scope: currentScopeId,
      family: "skill",
      status: "pending",
      candidate: candidate.candidate_id
    });
    window.location.assign(`/reviews?${params.toString()}`);
  } catch (error) {
    if (!handleAuthenticationError(error)) {
      currentAlert = {
        key: error instanceof SkillsRequestError ? "revisionUploadFailed" : "serverUnavailable",
        values: {status: error.status},
        tone: "error"
      };
      renderDetail();
    }
  } finally {
    revisionBusy = false;
    liveStatus.textContent = "";
    renderDelivery();
  }
}

function bytesToBase64(bytes) {
  const chunks = [];
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 0x8000)));
  }
  return btoa(chunks.join(""));
}

function renderLibrary() {
  const filtered = filteredRecords();
  const managedTotal = records.filter((record) => record.authority === "managed").length;
  const externalTotal = records.filter((record) => record.authority === "external").length;
  managedCount.textContent = formatNumber(managedTotal);
  externalCount.textContent = formatNumber(externalTotal);
  indexCaption.textContent = translate("loadedSkills", {
    count: formatNumber(filtered.length),
    total: formatNumber(records.length)
  });
  loadingState.hidden = !libraryBusy || records.length > 0;
  skillList.replaceChildren();
  for (const record of filtered) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "skills-list-item";
    row.role = "option";
    row.setAttribute("aria-selected", String(record.key === selectedKey));
    row.addEventListener("click", () => selectRecord(record.key));

    const heading = document.createElement("span");
    heading.className = "skills-list-heading";
    const labels = document.createElement("span");
    labels.className = "skills-list-labels";
    const authority = document.createElement("span");
    authority.className = `skills-authority-label skills-authority-${record.authority}`;
    authority.textContent = translate(record.authority === "managed" ? "managedSkill" : "externalSkill");
    const origin = document.createElement("span");
    origin.className = `status-badge skills-origin-badge skills-origin-${record.origin.kind}`;
    origin.textContent = originSummary(record, true);
    labels.append(authority, origin);
    const state = document.createElement("span");
    state.className = `status-badge skills-status-${recordStatus(record)}`;
    state.textContent = translate(recordStatus(record));
    heading.append(labels, state);

    const name = document.createElement("strong");
    name.textContent = record.name;
    const summary = document.createElement("span");
    summary.className = "skills-list-summary";
    summary.textContent = compactText(record.description, 132);
    const identity = document.createElement("code");
    identity.textContent = record.identity;
    row.append(heading, name, summary, identity);
    skillList.append(row);
  }
  emptyState.hidden = filtered.length !== 0 || libraryBusy;
}

function renderDetail() {
  const record = selectedRecord();
  if (!record) {
    clearDetail();
    return;
  }
  detailEmpty.hidden = true;
  detailContent.hidden = false;
  detailAuthority.className = `skills-authority-label skills-authority-${record.authority}`;
  detailAuthority.textContent = translate(record.authority === "managed" ? "managedSkill" : "externalSkill");
  detailName.textContent = record.name;
  detailIdentity.textContent = record.identity;
  detailStatus.className = `status-badge skills-status-${recordStatus(record)}`;
  detailStatus.textContent = translate(recordStatus(record));
  description.textContent = record.description;
  renderFacts(record);
  renderAlert(record);

  const isManaged = record.authority === "managed";
  managedContent.hidden = !isManaged;
  packageSection.hidden = !isManaged || !record.candidate.proposal.package;
  governanceSection.hidden = !isManaged;
  lineage.hidden = !isManaged;
  delivery.hidden = !isManaged;
  if (isManaged) {
    instructions.textContent = record.candidate.proposal.instructions;
    renderValidation(record.candidate.proposal.validation);
    renderReferences(sourceRefs, record.candidate.source_refs, formatSourceReference, "noSourceReferences");
    renderReferences(artifactRefs, record.candidate.artifact_refs, formatArtifactReference, "noArtifactReferences");
    renderPackageBrowser();
    renderGovernanceControls();
    renderDelivery();
  }
}

function clearDetail() {
  detailEmpty.hidden = false;
  detailContent.hidden = true;
  facts.replaceChildren();
  validation.replaceChildren();
  sourceRefs.replaceChildren();
  artifactRefs.replaceChildren();
  projectionRequests.cancel();
  packageRequests.cancel();
  packagePreviewRequests.cancel();
  projectionView = null;
  packageManifest = null;
  packageSelectedPath = "";
  packageError = null;
}

function renderFacts(record) {
  facts.replaceChildren();
  appendDefinition(facts, "authority", translate(record.authority === "managed" ? "managedSkill" : "externalSkill"));
  appendDefinition(facts, "origin", originSummary(record, false));
  appendDefinition(facts, "status", translate(recordStatus(record)));
  if (record.authority === "managed") {
    appendDefinition(facts, "artifact", formatArtifactReference(record.candidate.result_artifact), true);
    appendDefinition(facts, "revision", record.candidate.result_artifact.revision);
    appendDefinition(facts, "governanceGeneration", record.governance.governance_generation);
    if (record.governance.replacement_artifact_id) {
      appendDefinition(facts, "replacement", record.governance.replacement_artifact_id, true);
    }
    appendOriginEvidence(facts, record.origin);
    return;
  }
  const registration = record.resolution.registration;
  appendDefinition(facts, "provider", registration.provider);
  appendDefinition(facts, "installationScope", translate(`installation${capitalize(registration.installation_scope)}`));
  appendDefinition(facts, "sourceMachine", registration.host_id, true);
  appendDefinition(facts, "sourceAgent", agentLabel(registration.agent_kind));
  appendDefinition(facts, "fingerprint", registration.fingerprint, true);
  appendDefinition(facts, "originalLocation", registration.locator, true);
  appendDefinition(facts, "entrypoint", record.resolution.entrypoint || translate("unavailable"), true);
}

function appendOriginEvidence(list, origin) {
  const registration = origin.registration;
  if (!registration) {
    return;
  }
  appendDefinition(list, "sourceMachine", registration.host_id, true);
  appendDefinition(list, "sourceAgent", agentLabel(registration.agent_kind));
  appendDefinition(list, "externalIdentity", registration.external_skill_id, true);
  appendDefinition(list, "installationScope", translate(`installation${capitalize(registration.installation_scope)}`));
  appendDefinition(list, "originalLocation", registration.locator, true);
}

function renderPackageBrowser() {
  const record = selectedRecord();
  const visible = Boolean(
    record && record.authority === "managed" && record.candidate.proposal.package
  );
  packageSection.hidden = !visible;
  if (!visible) {
    packageStatus.textContent = "";
    packageFiles.replaceChildren();
    packagePath.textContent = "";
    packagePreview.textContent = "";
    return;
  }
  packageStatus.textContent = packageBusy
    ? translate("packageLoading")
    : (packageError ? translate(packageError.key, packageError.values) : "");
  packageFiles.replaceChildren();
  if (!packageManifest) {
    packagePath.textContent = "";
    packagePreview.textContent = packageBusy ? translate("packageLoading") : "";
    return;
  }
  renderPackageFileSelection();
}

function renderPackageFileSelection() {
  packageFiles.replaceChildren();
  if (!packageManifest) {
    return;
  }
  for (const file of packageManifest.files) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${file.path}${file.executable ? "  ·  +x" : ""}`;
    button.setAttribute("aria-current", String(file.path === packageSelectedPath));
    button.addEventListener("click", () => void loadPackagePreview(file.path));
    item.append(button);
    packageFiles.append(item);
  }
}

function renderGovernanceControls() {
  const record = selectedRecord();
  const visible = Boolean(record && record.authority === "managed");
  governanceSection.hidden = !visible;
  if (!visible) {
    return;
  }
  const recordChanged = lifecycleState.dataset.recordKey !== record.key;
  if (recordChanged) {
    lifecycleState.dataset.recordKey = record.key;
    lifecycleState.value = record.governance.lifecycle_state;
    governanceStatus.textContent = "";
  }
  renderReplacementSkills(
    record,
    recordChanged ? (record.governance.replacement_artifact_id || "") : replacementId.value
  );
  const retired = record.governance.lifecycle_state === "retired";
  lifecycleState.disabled = retired || lifecycleBusy || actionBusy;
  replacementId.disabled = retired || lifecycleBusy || lifecycleState.value !== "deprecated";
  applyLifecycleButton.disabled = retired || lifecycleBusy || actionBusy;
}

function renderReplacementSkills(record, selectedReplacement) {
  const artifactId = record.candidate.result_artifact.artifact_id;
  const candidates = records.filter((candidate) => (
    candidate.authority === "managed"
    && candidate.candidate.result_artifact.artifact_id !== artifactId
    && candidate.governance.lifecycle_state !== "retired"
  ));

  replacementId.replaceChildren();
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = translate("noReplacementSkill");
  replacementId.append(emptyOption);

  for (const candidate of candidates) {
    const option = document.createElement("option");
    option.value = candidate.candidate.result_artifact.artifact_id;
    option.textContent = `${candidate.name} · ${option.value}`;
    replacementId.append(option);
  }

  if (selectedReplacement && !candidates.some(
    (candidate) => candidate.candidate.result_artifact.artifact_id === selectedReplacement
  )) {
    const unavailableOption = document.createElement("option");
    unavailableOption.value = selectedReplacement;
    unavailableOption.textContent = `${translate("unavailable")} · ${selectedReplacement}`;
    replacementId.append(unavailableOption);
  }
  replacementId.value = selectedReplacement;
}

function renderValidation(items) {
  validation.replaceChildren();
  for (const item of items) {
    const row = document.createElement("li");
    row.textContent = item;
    validation.append(row);
  }
}

function renderReferences(list, references, formatter, emptyKey) {
  list.replaceChildren();
  if (!references.length) {
    const item = document.createElement("li");
    item.className = "skills-reference-empty";
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

function renderAlert(record) {
  const recordAlert = record.authority === "external" && record.resolution.status === "unavailable"
    ? {key: "externalSkillUnavailable", tone: "warning"}
    : null;
  const notice = currentAlert || recordAlert;
  alert.hidden = !notice;
  if (!notice) {
    alert.textContent = "";
    return;
  }
  alert.dataset.tone = notice.tone || "error";
  alert.textContent = translate(notice.key, notice.values || {});
}

function renderDelivery() {
  const record = selectedRecord();
  if (!record || record.authority !== "managed") {
    delivery.hidden = true;
    return;
  }
  delivery.hidden = false;
  const retired = record.governance.lifecycle_state === "retired";
  createRevisionButton.disabled = retired || libraryBusy || projectionBusy || actionBusy || remoteActionBusy || revisionBusy;
  const remoteMode = deliveryMode.value === "remote";
  localDelivery.hidden = remoteMode;
  remoteDelivery.hidden = !remoteMode;
  insecureHttpWarning.hidden = !allowInsecureHttp;
  remoteRefreshButton.hidden = !remoteMode;
  remoteRefreshButton.disabled = remoteBusy || remoteActionBusy;
  if (remoteMode) {
    renderRemoteDelivery(record, retired);
    return;
  }
  renderLocalDelivery(record, retired);
}

function renderLocalDelivery(record, retired) {
  publishButton.hidden = true;
  unpublishButton.hidden = true;
  deliveryStatus.textContent = projectionBusy ? translate("publicationLoading") : "";
  deliveryStatus.dataset.tone = "";
  projectionState.hidden = projectionBusy || !projectionView;
  deliveryEmpty.hidden = true;
  deliveryContent.hidden = true;
  if (projectionBusy || !projectionView) {
    return;
  }
  if (projectionView.blocker === "standard_package_required") {
    deliveryStatus.textContent = translate("standardPackageRequired");
    return;
  }
  if (projectionView.targets.length === 0) {
    deliveryEmpty.hidden = false;
    return;
  }
  const selectedTargetId = projectionView.targets.some((target) => target.target_id === deliveryTarget.value)
    ? deliveryTarget.value
    : projectionView.targets[0].target_id;
  deliveryTarget.replaceChildren();
  for (const target of projectionView.targets) {
    const option = document.createElement("option");
    option.value = target.target_id;
    option.textContent = localTargetLabel(target, record.name);
    option.selected = target.target_id === selectedTargetId;
    deliveryTarget.append(option);
  }
  const target = selectedProjectionTarget();
  if (!target) {
    return;
  }
  deliveryContent.hidden = false;
  projectionState.hidden = false;
  projectionState.className = `status-badge skills-projection-${target.state}`;
  projectionState.textContent = translate(projectionStateKey(target.state));
  deliveryStatus.textContent = projectionHintKey(target.state) ? translate(projectionHintKey(target.state)) : "";
  publishedRevision.textContent = target.published_revision === null
    ? translate("unavailable")
    : String(target.published_revision);
  discovery.textContent = translate(discoveryStateKey(target.discovery));
  compatibility.textContent = translate(target.compatibility);
  compatibilityReasons.replaceChildren();
  for (const reason of target.compatibility_reasons) {
    const item = document.createElement("li");
    item.textContent = reason;
    compatibilityReasons.append(item);
  }
  destination.textContent = target.destination;
  publishButton.textContent = translate(publicationActionKey(target));
  const canPublish = canPublishProjection(target);
  publishButton.hidden = retired || !canPublish;
  publishButton.disabled = libraryBusy || projectionBusy || actionBusy || !canPublish;
  const canUnpublish = canUnpublishProjection(target);
  unpublishButton.hidden = !canUnpublish;
  unpublishButton.disabled = libraryBusy || projectionBusy || actionBusy || !canUnpublish;
}

function renderRemoteDelivery(record, retired) {
  remoteEmpty.hidden = true;
  remoteContent.hidden = true;
  remotePublishButton.hidden = true;
  remoteUnpublishButton.hidden = true;
  projectionState.hidden = true;
  deliveryStatus.textContent = remoteFeedback
    ? translate(remoteFeedback.key, remoteFeedback.values || {})
    : (remoteBusy ? translate("remoteTargetsLoading") : "");
  deliveryStatus.dataset.tone = remoteFeedback?.tone || "";
  for (const button of remoteAddButtons) {
    button.disabled = remoteBusy || remoteActionBusy;
  }
  remoteRenameButton.disabled = remoteBusy || remoteActionBusy;

  const availableTargets = remoteTargets.filter((status) => status.target.state !== "revoked");
  if (!availableTargets.length) {
    remoteEmpty.hidden = remoteBusy;
    return;
  }
  if (!availableTargets.some((status) => status.target.target_id === selectedRemoteTargetId)) {
    selectedRemoteTargetId = availableTargets[0].target.target_id;
  }

  remoteTarget.replaceChildren();
  for (const status of availableTargets) {
    const target = status.target;
    const option = document.createElement("option");
    option.value = target.target_id;
    const environment = remoteTargetEnvironmentLabel(target);
    option.textContent = [
      target.display_name,
      agentLabel(target.agent_kind),
      environment,
      translate(remoteTargetStateKey(target.state))
    ].filter(Boolean).join(" · ");
    option.selected = target.target_id === selectedRemoteTargetId;
    remoteTarget.append(option);
  }

  const status = selectedRemoteTargetStatus();
  if (!status) {
    return;
  }
  const target = status.target;
  const publication = selectedRemotePublication(status, record);
  remoteContent.hidden = false;
  remoteEnrollment.textContent = translate(remoteTargetStateKey(target.state));
  remotePublicationState.textContent = publication
    ? translate(remotePublicationStateKey(publication.state))
    : translate("remoteNoPublication");
  remoteObservedRevision.textContent = publication?.observed_revision === null || !publication
    ? translate("remoteRevisionNone")
    : String(publication.observed_revision);
  remoteLastSeen.textContent = target.last_seen_at ? formatDateTime(target.last_seen_at) : translate("remoteNeverSeen");
  remoteEnvironment.textContent = remoteTargetEnvironmentLabel(target) || translate("remoteEnvironmentPending");
  remoteTargetId.textContent = target.target_id;
  const canRevoke = canRevokeRemoteTarget(status);
  remoteGuidance.textContent = translate(remoteGuidanceKey(target, publication));
  if (!canRevoke) {
    remoteGuidance.textContent = `${remoteGuidance.textContent} ${translate("remoteRevokeBlocked")}`;
  }

  const state = publication?.state || "unpublished";
  projectionState.hidden = false;
  projectionState.className = `status-badge skills-projection-${state}`;
  projectionState.textContent = publication
    ? translate(remotePublicationStateKey(publication.state))
    : translate("remoteNoPublication");

  const packageBacked = Boolean(record.candidate.proposal.package);
  if (!packageBacked && !remoteFeedback) {
    deliveryStatus.textContent = translate("standardPackageRequired");
  }
  const canPublish = canPublishRemote(record, status, publication);
  remotePublishButton.hidden = retired || !canPublish;
  remotePublishButton.disabled = remoteBusy || remoteActionBusy || !canPublish;
  const canUnpublish = Boolean(publication && publication.desired_state === "published");
  remoteUnpublishButton.hidden = !canUnpublish;
  remoteUnpublishButton.disabled = remoteBusy || remoteActionBusy || !canUnpublish;
  remoteRevokeButton.disabled = remoteBusy || remoteActionBusy || !canRevoke;
  remoteRevokeButton.title = canRevoke ? "" : translate("remoteRevokeBlocked");
}

function selectRecord(key) {
  if (key === selectedKey) {
    return;
  }
  selectedKey = key;
  projectionView = null;
  packageManifest = null;
  packageSelectedPath = "";
  packageError = null;
  currentAlert = null;
  renderLibrary();
  renderDetail();
  void loadProjectionStatus();
  void loadPackageManifest();
}

function ensureSelection() {
  const filtered = filteredRecords();
  if (!filtered.some((record) => record.key === selectedKey)) {
    selectedKey = filtered[0]?.key || "";
    projectionView = null;
    packageManifest = null;
    packageSelectedPath = "";
    packageError = null;
  }
  renderLibrary();
  renderDetail();
  void loadProjectionStatus();
  void loadPackageManifest();
}

function filteredRecords() {
  const query = searchInput.value.trim().toLocaleLowerCase();
  return records.filter((record) => (
    (!authorityFilter.value || record.authority === authorityFilter.value)
    && (!query || record.searchText.includes(query))
  ));
}

function selectedRecord() {
  return records.find((record) => record.key === selectedKey) || null;
}

function selectedProjectionTarget() {
  if (!projectionView) {
    return null;
  }
  return projectionView.targets.find((target) => target.target_id === deliveryTarget.value)
    || projectionView.targets[0]
    || null;
}

function selectedRemoteTargetStatus() {
  return remoteTargets.find((status) => status.target.target_id === selectedRemoteTargetId)
    || remoteTargets.find((status) => status.target.state !== "revoked")
    || null;
}

function selectRemoteTargetFromSearch() {
  const query = remoteTargetSearch.value.trim().toLocaleLowerCase();
  if (!query) {
    deliveryStatus.textContent = remoteFeedback ? translate(remoteFeedback.key, remoteFeedback.values || {}) : "";
    deliveryStatus.dataset.tone = remoteFeedback?.tone || "";
    return;
  }
  const match = remoteTargets.find((status) => (
    status.target.state !== "revoked" && remoteTargetSearchText(status.target).includes(query)
  ));
  if (!match) {
    deliveryStatus.textContent = translate("remoteSearchNoMatch");
    deliveryStatus.dataset.tone = "";
    return;
  }
  selectedRemoteTargetId = match.target.target_id;
  remoteFeedback = null;
  renderDelivery();
}

function remoteTargetSearchText(target) {
  return [
    target.display_name,
    target.machine_hostname,
    target.workspace_name,
    target.target_id,
    target.installation_id,
    agentLabel(target.agent_kind)
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

function remoteTargetEnvironmentLabel(target) {
  return [target.machine_hostname, target.workspace_name].filter(Boolean).join(" / ");
}

function remoteTargetLabel(target) {
  const environment = remoteTargetEnvironmentLabel(target);
  return [target.display_name, environment, agentLabel(target.agent_kind)].filter(Boolean).join(" · ");
}

function selectedRemotePublication(status = selectedRemoteTargetStatus(), record = selectedRecord()) {
  if (!status || !record || record.authority !== "managed") {
    return null;
  }
  const artifactId = record.candidate.result_artifact.artifact_id;
  return status.publications.find((publication) => publication.artifact_id === artifactId) || null;
}

function canPublishRemote(record, status, publication) {
  if (
    status.target.state !== "active"
    || record.governance.lifecycle_state === "retired"
    || !record.candidate.proposal.package
  ) {
    return false;
  }
  return !publication
    || publication.desired_state === "unpublished"
    || publication.desired_revision !== record.candidate.result_artifact.revision;
}

function canRevokeRemoteTarget(status) {
  return status.publications.every((publication) => (
    publication.desired_state === "unpublished" && publication.state === "unpublished"
  ));
}

function remoteTargetStateKey(state) {
  return {
    pending: "remoteTargetPending",
    active: "remoteTargetActive",
    revoked: "remoteTargetRevoked"
  }[state] || "unknown";
}

function remotePublicationStateKey(state) {
  return {
    unpublished: "remoteStateUnpublished",
    pending: "remoteStatePending",
    current: "remoteStateCurrent",
    update_available: "remoteStateUpdateAvailable",
    delivery_failed: "remoteStateDeliveryFailed",
    conflict: "remoteStateConflict",
    drifted: "remoteStateDrifted",
    incompatible: "remoteStateIncompatible"
  }[state] || "unknown";
}

function remoteGuidanceKey(target, publication) {
  if (target.state === "pending") {
    return "remoteGuidancePending";
  }
  if (!publication) {
    return "remoteGuidanceReady";
  }
  if (["pending", "update_available"].includes(publication.state)) {
    return "remoteGuidanceSync";
  }
  if (publication.state === "current") {
    return "remoteGuidanceCurrent";
  }
  if (publication.state === "unpublished") {
    return "remoteGuidanceRemoved";
  }
  return "remoteGuidanceProblem";
}

function agentLabel(agentKind) {
  return translate(agentKind === "claude_code" ? "agentClaudeCode" : "agentCodex");
}

function localTargetLabel(target, skillName) {
  const standardRoot = target.agent_kind === "claude_code" ? ".claude/skills" : ".agents/skills";
  const normalizedDestination = target.destination.replaceAll("\\", "/");
  if (normalizedDestination.endsWith(`/${standardRoot}/${skillName}`)) {
    return `${agentLabel(target.agent_kind)} · ${translate("currentProject")} · ${standardRoot}`;
  }
  return `${agentLabel(target.agent_kind)} · ${target.target_id} / ${translate(`installation${capitalize(target.installation_scope)}`)}`;
}

function recordStatus(record) {
  return record.authority === "managed" ? record.governance.lifecycle_state : record.resolution.status;
}

function originSummary(record, includeHost) {
  const key = {
    powercontext: "originPowerContext",
    external_import: "originExternalImport",
    external_fork: "originExternalFork",
    external: "originExternal"
  }[record.origin.kind] || "unknown";
  const label = translate(key);
  const host = record.origin.registration?.host_id;
  return includeHost && host ? `${label} · ${host}` : label;
}

function compareRecords(left, right) {
  const byName = left.name.localeCompare(right.name, undefined, {sensitivity: "base"});
  return byName || left.authority.localeCompare(right.authority) || left.identity.localeCompare(right.identity);
}

function compactText(value, limit) {
  const text = String(value).replace(/\s+/g, " ").trim();
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

function formatArtifactReference(reference) {
  return `${reference.family}/${reference.artifact_id}@${reference.revision}`;
}

function formatSourceReference(reference) {
  return `${reference.name}/${reference.source_id}`;
}

function appendDefinition(list, key, value, code = false) {
  const container = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = translate(key);
  const description = document.createElement("dd");
  if (code) {
    const codeElement = document.createElement("code");
    codeElement.textContent = String(value ?? "");
    description.append(codeElement);
  } else {
    description.textContent = String(value ?? "");
  }
  container.append(term, description);
  list.append(container);
}

function canPublishProjection(target) {
  return target.compatibility !== "incompatible" && (
    ["unpublished", "update_available"].includes(target.state)
    || (target.state === "current" && target.discovery !== "available")
  );
}

function canUnpublishProjection(target) {
  return ["current", "update_available"].includes(target.state);
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
    // The response status still gives the page a safe error path.
  }
  if (!response.ok) {
    throw new SkillsRequestError(
      response.status,
      payload?.error?.code || "",
      payload?.error?.details || null
    );
  }
  return payload;
}

function handleAuthenticationError(error) {
  if (!(error instanceof SkillsRequestError) || error.status !== 401) {
    return false;
  }
  clearServerToken();
  showLogin("authRejected");
  return true;
}

function setLibraryBusy(value, statusKey = "") {
  libraryBusy = value;
  liveStatus.textContent = statusKey ? translate(statusKey) : "";
  scopeSearchInput.disabled = value;
  searchInput.disabled = value;
  authorityFilter.disabled = value;
  refreshButton.disabled = value;
  if (value) {
    closeScopeOptions({restoreSelection: true});
  }
  renderLibrary();
  renderDelivery();
}

function showLogin(messageKey = "") {
  scopeRequests.cancel();
  libraryRequests.cancel();
  projectionRequests.cancel();
  remoteRequests.cancel();
  packageRequests.cancel();
  packagePreviewRequests.cancel();
  stopRemoteRefresh();
  remoteBusy = false;
  currentScopeId = "";
  currentAuthError = messageKey ? {key: messageKey, values: {}} : null;
  renderAuthError();
  authShell.hidden = false;
  pageStatus.hidden = true;
  library.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, values = {}, retryable = false) {
  currentPageStatus = {key: messageKey, values, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  library.hidden = true;
  signOut.hidden = !authenticationRequired;
}

function showLibrary() {
  currentPageStatus = null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  library.hidden = false;
  signOut.hidden = !authenticationRequired;
  scheduleRemoteRefresh(0);
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

function matchingScopes() {
  const query = scopeSearchInput.value.trim().toLocaleLowerCase();
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
    option.id = `skills-scope-option-${index}`;
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
    option.addEventListener("click", () => void selectScope(scope.scope_id));
    scopeOptions.append(option);
  }
  if (matches.length === 0) {
    const empty = document.createElement("p");
    empty.className = "scope-options-empty";
    empty.textContent = translate("noMatchingScopes");
    scopeOptions.append(empty);
  }
  scopeSearchStatus.textContent = matches.length > scopeOptionRenderLimit
    ? translate("scopeSearchLimited", {shown: formatNumber(visible.length), total: formatNumber(matches.length)})
    : translate(scopeSearchInput.value ? "scopeSearchMatches" : "scopeSearchCount", {
      count: formatNumber(matches.length)
    });
  updateScopeActiveDescendant();
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
  event.preventDefault();
  if (scopeOptions.hidden) {
    openScopeOptions();
  }
  const options = Array.from(scopeOptions.querySelectorAll(".scope-option"));
  if (!options.length) {
    return;
  }
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
  updateScopeActiveDescendant();
}

function updateScopeActiveDescendant() {
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
    showPageStatus("scopeUnavailable", {}, true);
    return;
  }
  closeScopeOptions();
  scopeSearchInput.value = selected.display_name;
  if (scopeId === currentScopeId) {
    return;
  }
  currentScopeId = scopeId;
  rememberScope(scopeId);
  records = [];
  selectedKey = "";
  projectionView = null;
  remoteRequests.cancel();
  remoteBusy = false;
  remoteTargets = [];
  selectedRemoteTargetId = "";
  remoteFeedback = null;
  renderLibrary();
  renderDetail();
  await loadLibrary();
}

function preferredScopeId() {
  const queryScope = new URLSearchParams(window.location.search).get("scope");
  if (queryScope) {
    return queryScope;
  }
  try {
    return sessionStorage.getItem(scopePreferenceKey) || "";
  } catch (error) {
    return "";
  }
}

function rememberScope(scopeId) {
  try {
    sessionStorage.setItem(scopePreferenceKey, scopeId);
  } catch (error) {
    // The current page still retains the selected scope.
  }
}

function preferredDeliveryMode() {
  try {
    return sessionStorage.getItem(deliveryModePreferenceKey) === "remote" ? "remote" : "local";
  } catch (error) {
    return "local";
  }
}

function rememberDeliveryMode(mode) {
  try {
    sessionStorage.setItem(deliveryModePreferenceKey, mode === "remote" ? "remote" : "local");
  } catch (error) {
    // The current page still retains the selected delivery mode.
  }
}

ui.initialize();
void authenticate(readServerToken(), preferredScopeId());
