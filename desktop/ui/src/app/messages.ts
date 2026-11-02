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

export const messages = {
  zh: {
    confirmTitle: "确认操作",
    cancelAction: "取消",
    confirmAction: "确认",
    contextUpdating: "正在更新连接或 Scope 状态…",
    scopeUpdatingHint:
      "Scope 选择仍在进行。关闭对话框不会取消已提交的选择；完成前记忆编辑、保存、搜索、精确读取及再次切换 Scope 暂不可用。",
    setUpLocally: "本地设置",
    connectExisting: "连接已有 Server",
    startPathsHint:
      "可以查看本地 Server 准备说明，或连接已经运行的本地／远程 Server。两条路径都不会自动安装或激活连接。",
    localServer: "Local Server",
    localServerIntro: "即使尚未连接 Server，也可在此查看本地准备说明。",
    localServerUnavailable: "安装、恢复与服务管理尚未接入",
    localServerUnavailableHint:
      "此版本不能在 Desktop 内安装、修复、启动、停止、重启 Server 或设置登录启动。这不表示本机没有安装 Server，也不判断服务是否正在运行。",
    prepareServer: "独立准备 Server",
    prepareServerDocs:
      "通过下方“使用帮助”复制并打开文档，按指引在 Desktop 外独立安装、配置和运行 PowerContext Server。",
    prepareServerAddress:
      "确认实际 Server 地址、认证方式及受支持的兼容配置；已有独立运行的 Server 可直接连接，无需重复安装。",
    prepareServerConnect:
      "准备完成后，连接已有 Server，保存配置并显式激活，再选择精确 Scope。Desktop 不会自动连接或恢复 Scope。",
    localServerDiagnostics:
      "下方本机诊断可手动执行，但不能代替安装、恢复或服务启停；关闭 Desktop 不会停止独立运行的 Server。",
    connectionUpdating: "正在更新连接状态…",
    connectionUpdatingHint:
      "可以浏览其他页面。连接操作完成前，记忆编辑、保存、搜索、精确读取及范围切换暂不可用；完成后请核对当前连接与精确 Scope。",
    help: "使用帮助",
    helpPrepare:
      "请先按使用文档独立准备 PowerContext Server，或使用已运行的本地／远程 Server。Desktop 不安装或启停 Server。",
    helpConnect:
      "在“连接”中保存地址及认证配置，核对受支持的兼容配置后，显式点击“使用此连接”。选择配置不会自动激活连接。",
    helpScope:
      "通过顶部 Scope 选择器查找并选择精确范围。核对完整 scope_id，以区分同名 Scope；选择范围不会修改 Agent 绑定。",
    helpMemory:
      "在“记忆”中保存正文、按关键词搜索并读取结果的精确版本。搜索最多返回 10 条，不代表完整目录或库存。",
    helpRecovery:
      "权限错误需申请对应权限或选择有权限的连接／Scope。保存结果未知时先核对原目标，再决定是否再次提交，避免重复写入。",
    helpDocuments: "使用文档",
    helpIssues: "问题反馈",
    copyDocuments: "复制使用文档地址",
    copyIssues: "复制问题反馈地址",
    addressCopied: "地址已复制。",
    addressCopyFailed: "复制失败，请选择并手动复制下方地址。",
    helpAddressHint:
      "复制地址后，可在浏览器中打开。反馈时请勿包含凭据或私有记忆正文。",
    unknown: "未知",
    loadingState: "正在加载 Desktop 状态…",
    stateUnavailable: "Desktop 状态尚不可用",
    stateFailed: "无法加载 Desktop 状态",
    retryState: "重新加载本机状态",
    retryRead: "重试读取",
    restartScopeSearch: "从第一页重新查找",
    retryDiagnostics: "重新执行本机检查",
    operationPending: "操作正在进行，请稍候…",
    diagnosticIdle: "尚未执行本机检查",
    diagnosticEmpty: "本次检查没有返回诊断项目",
    connectionsEmptyHint:
      "填写右侧配置并保存，再显式检查和启用连接。保存配置不会激活连接。",
    recoverAccess:
      "请申请此操作所需的权限，或选择已有权限的连接或精确 Scope。健康状态不代表授权。",
    recoverCredentials:
      "请在连接页核对凭据和认证设置，再显式检查连接。不会自动降级为匿名访问。",
    recoverCompatibility:
      "请在连接页核对受支持的 Server 与兼容配置，再显式检查。健康状态不代表兼容性。",
    recoverScope:
      "请使用全局 Scope 选择器选择精确目标；这不会修改 Agent 绑定。",
    recoverContext:
      "请核对当前连接和精确 Scope。旧读取已失效，已提交写入仍属于原目标。",
    recoverCursor: "请从第一页重新查找，不再使用已过期的分页游标。",
    recoverOperation:
      "请检查相关输入、连接或本机配置，再显式执行受影响的操作。不会自动重放已提交写入。",
    globalContext: "当前连接与精确 Scope",
    activeContext: "连接已激活",
    liveness: "存活状态",
    lastChecked: "上次检查",
    loadingScopes: "正在查找范围…",
    selectingScope: "正在核对并选择精确 Scope…",
    scopeIdle: "尚未查找范围",
    scopeIdleHint: "输入名称并查找，只从 Server 返回的范围中选择精确目标。",
    chooseConnection: "管理连接与凭据",
    chooseScopeTitle: "请先选择精确 Scope",
    compatibilityNeeded: "当前连接的兼容性尚未验证",
    observation: "观察",
    runtime: "运行状态",
    inventory: "当前 Scope 库存与用量",
    session: "本次会话",
    observationUnavailable: "此观察视图尚未可用",
    runtimeDescription:
      "运行状态视图将展示所连接 Server 的健康、能力和处理观察，以及单独标注的本地服务事实。当前不会自动执行检查。",
    inventoryDescription:
      "此视图将展示当前精确 Scope 的库存和用量。尚未接入数据；未知值不表示为零。",
    sessionDescription:
      "此视图将展示本次 Desktop 会话的有限请求汇总。当前尚未提供请求统计或安全诊断摘要复制。",
    home: "总览",
    connections: "连接",
    memories: "记忆",
    settings: "设置与诊断",
    menu: "导航菜单",
    tagline: "确认连接状态，开始保存与检索记忆。",
    connectionIntro: "保存 Server 地址与凭据，在不同连接之间切换。",
    memoriesIntro: "在当前范围保存记忆，并通过全文检索找回。",
    settingsIntro: "语言、主题、本地诊断与桌面版本。",
    brandSub: "DESKTOP",
    foundation: "连接预览",
    quick: "快速记录",
    noteHint: "记下你的偏好、约定，或以后会用到的信息。",
    note: "记忆内容",
    save: "保存记忆",
    scope: "保存范围：尚未选择",
    current: "当前连接",
    disconnected: "尚未连接",
    connect: "连接已有服务",
    connectHint: "连接已有的 PowerContext Server 后，选择范围即可保存和查找。",
    foundationHint: "连接服务并选择范围后，即可保存、全文搜索和阅读记忆。",
    find: "查找记忆",
    query: "搜索关键词",
    search: "搜索",
    findHint: "输入关键词，找回明确保存过的内容。",
    empty: "还没有可查看的记忆",
    emptyHint:
      "连接服务并选择范围后，使用全文搜索查找内容。这里不会自动加载全部记忆。",
    noConnections: "尚未添加连接",
    boundary:
      "桌面不会安装或启动 Server。关闭窗口将退出桌面，不会停止独立运行的服务。",
    details: "连接状态详情",
    unverified: "未验证",
    identity: "身份与访问",
    readiness: "就绪状态",
    compatibility: "兼容性",
    tls: "TLS 信任",
    language: "语言",
    theme: "主题",
    light: "浅色",
    dark: "深色",
    system: "跟随系统",
    diagnostics: "本地诊断",
    diagnosticsHint: "尚未执行本地 CLI 检查。这不代表远程服务不可用。",
    version: "桌面版本",
    native: "原生宿主",
    unavailable: "未连接原生宿主",
    nativeReady: "已就绪",
    privacy: "正文和搜索结果只在内存中使用；凭据由系统凭据库或本次会话管理。",
    tip: "使用提示",
    tipBody: "一次记录一件具体的事，之后更容易找到。",
    searchLimit: "全文搜索，最多返回 10 条。",
    skip: "跳到主要内容",
    connected: "已连接",
    notConnected: "未连接",
    scopeLabel: "Scope",
    exactScope: "精确范围",
    chooseScope: "选择范围",
    manageConnections: "管理连接",
    switchHint: "在此查看不会切换活动连接。",
    identityDetails: "服务端返回的身份",
    accessMode: "访问控制",
    anonymousIdentity: "未认证（本机服务）",
    identityUnverified: "尚未验证身份",
    sidebarRunning: "退出桌面后服务继续运行",
    closeDialog: "关闭",
    refresh: "刷新状态",
    refreshing: "正在刷新…",
    manage: "管理连接",
    serviceReady: "服务就绪",
    serviceNotReady: "服务未就绪",
    identityLabel: "身份",
    authLabel: "认证",
    currentScopeLabel: "当前范围",
    compatibilityLabel: "兼容配置",
    noScopeSelected: "尚未选择",
    noCompatibilitySelected: "未选择",
    compatibilityVerified: "已验证",
    authBearer: "Bearer",
    authAnonymous: "未认证本机",
    readinessNote: "就绪状态与操作权限分别核验。",
    features: "当前可用功能",
    featureSave: "保存记忆",
    featureSaveHint: "显式保存到当前精确 Scope。",
    featureSearch: "全文检索",
    featureSearchHint: "无需配置模型即可搜索。",
    featureRead: "精确详情",
    featureReadHint: "查看正文与完整引用。",
    available: "可用",
    notAvailable: "不可用",
    catalogNote: "完整目录与历史记录暂不可用。",
    localEnv: "本地环境",
    envService: "个人服务",
    envOwnership: "服务归属",
    envAgent: "Agent 集成",
    envConfig: "集成配置",
    envHostLoad: "宿主加载",
    envCaptureRecall: "保存 / 召回",
    envNote: "配置存在不代表宿主已加载。",
    viewDiagnostics: "查看诊断",
    gettingStarted: "开始使用",
    gettingStartedHint: "在当前精确范围保存一条记忆，再通过全文检索找回。",
    stepSave: "保存一条记忆",
    stepSaveHint: "在当前精确 Scope 下保存项目文档。",
    stepSearch: "搜索并核对引用",
    stepSearchHint: "使用全文检索找到记忆，并查看完整引用。",
    openMemories: "打开记忆",
    overviewBoundary: "浏览范围不会修改 Agent 绑定；退出桌面不会停止 Server。",
    noActiveTitle: "尚未连接服务",
    noActiveBody:
      "连接已有的 PowerContext Server 并选择精确范围后，即可保存和查找记忆。",
  },
  en: {
    confirmTitle: "Confirm action",
    cancelAction: "Cancel",
    confirmAction: "Confirm",
    contextUpdating: "Updating connection or Scope state…",
    scopeUpdatingHint:
      "Scope selection is still pending. Closing the dialog does not cancel a submitted selection. Memory editing, saving, searching, exact reads and further Scope changes pause until it finishes.",
    setUpLocally: "Set up locally",
    connectExisting: "Connect an existing Server",
    startPathsHint:
      "Read the local Server preparation guide, or connect an existing local or remote Server. Neither path installs anything or activates a connection automatically.",
    localServer: "Local Server",
    localServerIntro:
      "Local preparation guidance remains available before connecting to a Server.",
    localServerUnavailable:
      "Installation, recovery and service management are not integrated",
    localServerUnavailableHint:
      "This version cannot install, repair, start, stop or restart a Server or configure login startup from Desktop. This does not mean a Server is absent or determine whether a service is running.",
    prepareServer: "Prepare a Server independently",
    prepareServerDocs:
      "Copy and open the documentation from Usage help below, then follow the guide to install, configure and run PowerContext Server outside Desktop.",
    prepareServerAddress:
      "Confirm the actual Server address, authentication method and supported compatibility profile. An independently running Server can be connected without reinstalling it.",
    prepareServerConnect:
      "When ready, connect the existing Server, save and explicitly activate its profile, then select an exact Scope. Desktop does not automatically connect or restore a Scope.",
    localServerDiagnostics:
      "You can run the local diagnostics below explicitly. They do not install, recover, start or stop services. Closing Desktop leaves an independently running Server active.",
    connectionUpdating: "Updating connection state…",
    connectionUpdatingHint:
      "You can browse other pages. Memory editing, saving, searching, exact reads and Scope switching are unavailable until the connection operation finishes. Then verify the current connection and exact Scope.",
    help: "Usage help",
    helpPrepare:
      "Prepare PowerContext Server independently using the usage guide, or use an existing local or remote Server. Desktop does not install, start or stop the Server.",
    helpConnect:
      "In Connections, save the address and authentication settings, verify a supported compatibility profile, then explicitly choose Use this connection. Selecting a profile does not activate it.",
    helpScope:
      "Find and select an exact Scope using the global selector. Check the complete scope_id to distinguish identical titles. Selecting a Scope does not change Agent bindings.",
    helpMemory:
      "In Memories, save text, search by keyword, and read the exact version of a result. Search returns at most 10 matches and does not represent a full directory or inventory.",
    helpRecovery:
      "For permission errors, request access or choose an authorized connection or Scope. If a save outcome is unknown, inspect its original target before deciding to submit again to avoid duplicate writes.",
    helpDocuments: "Usage documentation",
    helpIssues: "Report an issue",
    copyDocuments: "Copy documentation address",
    copyIssues: "Copy issue report address",
    addressCopied: "Address copied.",
    addressCopyFailed:
      "Copy failed. Select and copy the address below manually.",
    helpAddressHint:
      "Paste these addresses into your browser. Exclude credentials and private memory text from issue reports.",
    unknown: "Unknown",
    loadingState: "Loading Desktop state…",
    stateUnavailable: "Desktop state is unavailable",
    stateFailed: "Could not load Desktop state",
    retryState: "Reload local state",
    retryRead: "Retry read",
    restartScopeSearch: "Search again from the first page",
    retryDiagnostics: "Run local check again",
    operationPending: "An operation is in progress. Please wait…",
    diagnosticIdle: "No local check has run",
    diagnosticEmpty: "This check returned no diagnostic items",
    connectionsEmptyHint:
      "Fill in and save a configuration, then explicitly check and activate it. Saving does not activate a connection.",
    recoverAccess:
      "Request permission for this operation, or choose an authorized connection or exact Scope. Health does not grant access.",
    recoverCredentials:
      "Review credentials and authentication in Connections, then explicitly check the connection. There is no automatic anonymous downgrade.",
    recoverCompatibility:
      "Review the supported Server and compatibility profile in Connections, then explicitly check it. Health does not establish compatibility.",
    recoverScope:
      "Choose an exact target with the global Scope selector. This does not change Agent bindings.",
    recoverContext:
      "Review the current connection and exact Scope. Previous reads are invalid; submitted writes still belong to their original targets.",
    recoverCursor:
      "Search again from the first page without reusing the expired cursor.",
    recoverOperation:
      "Review the relevant input, connection or local configuration before explicitly performing the affected operation. Submitted writes are never replayed automatically.",
    globalContext: "Current connection and exact Scope",
    activeContext: "Connection active",
    liveness: "Liveness",
    lastChecked: "Last checked",
    loadingScopes: "Finding scopes…",
    selectingScope: "Verifying and selecting the exact Scope…",
    scopeIdle: "No Scope search has run",
    scopeIdleHint:
      "Enter a name and search, then select an exact target returned by the Server.",
    chooseConnection: "Manage connections and credentials",
    chooseScopeTitle: "Select an exact Scope first",
    compatibilityNeeded: "Connection compatibility is not verified",
    observation: "Observation",
    runtime: "Runtime state",
    inventory: "Current-Scope inventory & usage",
    session: "This session",
    observationUnavailable: "This observation view is not available yet",
    runtimeDescription:
      "This view will show connected-Server health, capabilities and processing observations, with separately labelled local service facts. It does not run checks automatically.",
    inventoryDescription:
      "This view will show inventory and usage for the current exact Scope. Data is not connected yet; unknown values are not zero.",
    sessionDescription:
      "This view will show bounded request summaries for this Desktop session. Request statistics and safe diagnostic summary copying are not available yet.",
    home: "Overview",
    connections: "Connections",
    memories: "Memories",
    settings: "Settings & diagnostics",
    menu: "Navigation",
    tagline: "Check the connection status, then save and search memories.",
    connectionIntro:
      "Save Server addresses and credentials, and switch between connections.",
    memoriesIntro:
      "Save memories in the current scope and find them by full-text search.",
    settingsIntro: "Language, theme, local diagnostics, and Desktop version.",
    brandSub: "DESKTOP",
    foundation: "Connection preview",
    quick: "Quick note",
    noteHint: "Record a preference, an agreement, or something to remember.",
    note: "Memory text",
    save: "Save memory",
    scope: "Scope: none selected",
    current: "Current connection",
    disconnected: "Not connected",
    connect: "Connect an existing server",
    connectHint:
      "Connect to an existing PowerContext Server, then select a scope to save and search.",
    foundationHint:
      "Connect a Server and select a Scope to save, search and read memories.",
    find: "Find memories",
    query: "Search keywords",
    search: "Search",
    findHint: "Use keywords to find content you explicitly saved.",
    empty: "No memories to display",
    emptyHint:
      "Connect a server and select a scope to search. This page does not automatically load all memories.",
    noConnections: "No connections added",
    boundary:
      "Desktop does not install or start a Server. Closing the window exits Desktop and leaves independent services running.",
    details: "Connection status details",
    unverified: "Not verified",
    identity: "Identity and access",
    readiness: "Readiness",
    compatibility: "Compatibility",
    tls: "TLS trust",
    language: "Language",
    theme: "Theme",
    light: "Light",
    dark: "Dark",
    system: "System",
    diagnostics: "Local diagnostics",
    diagnosticsHint:
      "Local CLI checks have not run. This does not indicate a remote service failure.",
    version: "Desktop version",
    native: "Native host",
    unavailable: "Native host unavailable",
    nativeReady: "Ready",
    privacy:
      "Memory text and search results stay in memory. Credentials use the system vault or this session.",
    tip: "A useful habit",
    tipBody: "Keep each note focused on one thing so it is easier to find.",
    searchLimit: "Full-text search, up to 10 results.",
    skip: "Skip to main content",
    connected: "Connected",
    notConnected: "Not connected",
    scopeLabel: "Scope",
    exactScope: "Exact scope",
    chooseScope: "Select scope",
    manageConnections: "Manage connections",
    switchHint: "Viewing here does not switch the active connection.",
    identityDetails: "Server-reported identity",
    accessMode: "Access control",
    anonymousIdentity: "Unauthenticated (loopback server)",
    identityUnverified: "Identity not verified yet",
    sidebarRunning: "Services keep running after Desktop exits",
    closeDialog: "Close",
    refresh: "Refresh status",
    refreshing: "Refreshing…",
    manage: "Manage connections",
    serviceReady: "Service ready",
    serviceNotReady: "Service not ready",
    identityLabel: "Identity",
    authLabel: "Authentication",
    currentScopeLabel: "Current scope",
    compatibilityLabel: "Compatibility profile",
    noScopeSelected: "None selected",
    noCompatibilitySelected: "Not selected",
    compatibilityVerified: "Verified",
    authBearer: "Bearer",
    authAnonymous: "Unauthenticated loopback",
    readinessNote:
      "Readiness and operation permissions are verified separately.",
    features: "Available features",
    featureSave: "Save memories",
    featureSaveHint: "Explicitly saved to the current exact Scope.",
    featureSearch: "Full-text search",
    featureSearchHint: "Search without configuring a model.",
    featureRead: "Exact details",
    featureReadHint: "Read the text and the complete reference.",
    available: "Available",
    notAvailable: "Unavailable",
    catalogNote: "A full catalog and history browsing are not available.",
    localEnv: "Local environment",
    envService: "Personal service",
    envOwnership: "Service ownership",
    envAgent: "Agent integration",
    envConfig: "Integration configuration",
    envHostLoad: "Host loading",
    envCaptureRecall: "Capture / recall",
    envNote: "A configuration existing does not mean the host has loaded it.",
    viewDiagnostics: "View diagnostics",
    gettingStarted: "Getting started",
    gettingStartedHint:
      "Save a memory in the current exact scope, then find it with full-text search.",
    stepSave: "Save a memory",
    stepSaveHint: "Save project documents under the current exact Scope.",
    stepSearch: "Search and verify references",
    stepSearchHint:
      "Find memories with full-text search and read the complete reference.",
    openMemories: "Open memories",
    overviewBoundary:
      "Browsing scopes does not change Agent bindings; exiting Desktop does not stop the Server.",
    noActiveTitle: "No service connected",
    noActiveBody:
      "Connect an existing PowerContext Server and select an exact scope to save and find memories.",
  },
};
export type Language = keyof typeof messages;
