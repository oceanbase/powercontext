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

export const sessionMessages = {
  zh: {
    title: "本次 Desktop 会话",
    scope:
      "覆盖此 Desktop 进程中的所有连接与 Scope。不是全局 Server/Agent 链路，也不能证明记忆已持久化。",
    refresh: "刷新会话摘要",
    preview: "预览安全诊断摘要",
    reset: "重置会话观察",
    resetConfirm:
      "仅清除此 Desktop 的会话观察，不会删除记忆或取消正在执行的操作。重置前开始的观察不会重新进入新窗口；采集丢失总数继续保留。",
    resetting: "正在重置会话观察…",
    resetDone: "会话观察已重置。记忆和正在执行的操作不受影响。",
    resetFailed: "无法确认会话观察已重置。请刷新摘要检查，再决定是否重试。",
    loading: "正在读取会话摘要…",
    readFailed: "无法读取会话摘要",
    retryHint:
      "请稍后重试读取本机观察。无需重新连接 Server，也不会自动重放业务操作。",
    retry: "重试读取摘要",
    unsupported: "当前摘要版本无法显示",
    unsupportedHint:
      "此页面支持会话摘要版本 2。请使用匹配的 Desktop 界面和原生宿主。",
    empty: "尚无已完成的观察",
    emptyHint:
      "可在连接或记忆页面显式执行操作，然后刷新此页。进行中的操作尚未显示；空窗口不代表 Server 没有活动。",
    sessionStart: "Desktop 会话开始（UTC）",
    windowStart: "观察窗口开始（UTC）",
    snapshotAt: "摘要生成时间（UTC）",
    retained: "当前保留的终态记录",
    capacity: "终态记录上限",
    evicted: "重置以来已淘汰",
    dropped: "进程启动以来采集丢失",
    bounds:
      "两种测量单位共享保留上限。只保存在内存，退出 Desktop 后清空；统计仅根据当前保留窗口计算。",
    incomplete: "观察覆盖不完整",
    incompleteHint:
      "已淘汰或采集丢失的记录未进入下方统计。采集丢失总数不会因重置清零。",
    http: "HTTP 请求",
    actions: "用户操作（原生边界）",
    httpHint:
      "每次交给 HTTP 客户端的请求单独计数，包含身份复核和连接检查。耗时包含网络、响应读取和 JSON 解码，不是 Server 内部处理时间。",
    actionHint:
      "每次 Memory/Scope 原生操作单独计数，包含上下文与身份检查；不包含界面确认等待或防抖时间。一次操作可产生多个 HTTP 请求。",
    outcomesHint:
      "成功、失败、已取消和结果未知分别统计。HTTP 成功不保证保存成功；请求取消不代表远端写入已取消。",
    timingHint:
      "最小、平均和最大耗时的单位均为毫秒，包含该组内已取消和结果未知的样本。",
    noSamples: "当前窗口没有此类终态样本。",
    operation: "操作",
    samples: "样本数",
    success: "成功",
    failure: "失败",
    cancelled: "已取消",
    unknown: "结果未知",
    minimum: "最小（ms）",
    mean: "平均（ms）",
    maximum: "最大（ms）",
    scrollHint: "窄窗口中可聚焦表格区域并横向滚动。",
    coverage: "采集范围",
    operationCoverage: "查看纳入统计的操作",
    localOps: "本地安装与运维",
    localOpsUnavailable:
      "原生 Installer/Ops 观察源尚未接入，无法确认本地安装或运维结果。",
    localOpsHint:
      "展示本次 Desktop 会话中原生 Ops 调用返回时的安全快照，独立于 HTTP 请求和用户操作的数量与延迟。下载、安装和服务工作不会计入 HTTP 延迟。不读取 journal，也不自动查询或重试操作。",
    opsReferenceHint:
      "可使用操作引用向本机 Ops 查询状态。阶段表示最后观测到的服务阶段，不证明后续状态或数据持久化。分配引用之前的预检或授权失败由安装流程显示，此处不会生成虚构引用。",
    opsEmpty: "当前窗口尚无 Ops 调用快照；这不表示本机从未安装或发生故障。",
    opsIncomplete: "Ops 观察覆盖不完整：部分快照已被淘汰或采集丢失。",
    opsRetained: "保留的 Ops 调用快照",
    opsCapacity: "Ops 快照上限",
    opsReference: "不透明操作引用",
    opsAction: "Ops 调用",
    opsAttempt: "尝试次数",
    opsPhase: "最后观测到的服务阶段",
    opsAttemptPhase: "尝试阶段",
    opsOutcome: "Ops 结果",
    opsError: "安全错误码",
    opsObservedAt: "观测时间（UTC）",
    notObserved: "未观测到",
    opsActions: {
      inspect: "检查",
      config_validate: "验证配置",
      preflight: "预检",
      apply: "应用",
      resume: "恢复",
      operation_status: "查询状态",
      cancel: "请求取消",
    },
    opsPhases: {
      accepted: "已接受",
      registration_dispatching: "正在派发注册",
      service: "服务操作",
      preparing: "准备资源",
      prepared: "资源准备完成",
      publishing: "发布资源",
      resources_published: "资源已发布",
      configuration_prepared: "配置准备完成",
      configured: "配置完成",
      registering: "注册服务",
      complete: "阶段完成",
    },
    opsAttemptPhases: {
      accepted: "已接受",
      executing: "执行中",
      finished: "已结束",
    },
    opsOutcomes: {
      succeeded: "成功",
      pending: "待完成",
      cancelled: "已取消",
      failed: "失败",
      partial_failure: "部分失败",
      uncertain: "结果不确定",
      rejected: "已拒绝",
      unknown: "结果未知",
    },
    previewTitle: "安全诊断摘要预览",
    previewHint:
      "以下文本由原生宿主按白名单生成，不含记忆内容、查询、凭据、原始地址、身份/Scope ID、私有路径或原始异常。复制内容与下方文本完全一致；这是生成时的快照，不会自动更新或上传。",
    previewLoading: "正在生成安全诊断预览…",
    previewFailed: "无法生成安全诊断预览",
    previewRetry: "重新生成预览",
    previewText: "将要复制的安全诊断文本",
    copy: "复制预览文本",
    copying: "正在复制…",
    copied: "安全诊断摘要已复制。",
    copyFailed: "复制失败。请选中上方文本后手动复制。",
    close: "关闭预览",
    operations: {
      get_liveness: "检查存活状态",
      get_server_info: "读取 Server 信息",
      get_readiness: "检查就绪状态",
      get_capabilities: "读取能力",
      get_access_principal: "核对身份",
      list_scopes: "查找 Scope",
      get_scope: "核对并选择 Scope",
      get_default_scope: "解析默认 Scope",
      remember_memory: "保存记忆",
      search_memory: "搜索记忆",
      get_artifact_revision: "读取精确版本",
    },
  },
  en: {
    title: "This Desktop session",
    scope:
      "Covers all connections and Scopes in this Desktop process. It is not global Server/Agent tracing or proof that Memory was persisted.",
    refresh: "Refresh session summary",
    preview: "Preview safe diagnostic summary",
    reset: "Reset session observations",
    resetConfirm:
      "Clear only this Desktop's session observations. Memory and running operations are unaffected. Observations begun before reset will not reappear in the new window; the lifetime collection-loss count is retained.",
    resetting: "Resetting session observations…",
    resetDone:
      "Session observations reset. Memory and running operations are unaffected.",
    resetFailed:
      "The observation reset could not be confirmed. Refresh the summary to check before deciding to retry.",
    loading: "Reading session summary…",
    readFailed: "Unable to read session summary",
    retryHint:
      "Try reading the local observations again later. No Server reconnection is needed and business operations will not be replayed.",
    retry: "Retry reading summary",
    unsupported: "This summary version cannot be displayed",
    unsupportedHint:
      "This page supports session summary version 2. Use matching Desktop UI and native host versions.",
    empty: "No completed observations yet",
    emptyHint:
      "Explicitly use Connections or Memory, then refresh this page. Work in progress is not shown; an empty window does not mean the Server has no activity.",
    sessionStart: "Desktop session started (UTC)",
    windowStart: "Observation window started (UTC)",
    snapshotAt: "Summary generated (UTC)",
    retained: "Retained terminal records",
    capacity: "Terminal record limit",
    evicted: "Evicted since reset",
    dropped: "Collection losses since process start",
    bounds:
      "Both measurement units share the retention limit. Records stay in memory and clear when Desktop exits; statistics use only the retained window.",
    incomplete: "Observation coverage is incomplete",
    incompleteHint:
      "Evicted or lost observations are absent from the statistics below. Reset does not clear the lifetime collection-loss count.",
    http: "HTTP requests",
    actions: "User operations (native boundary)",
    httpHint:
      "Counts each request handed to the HTTP client, including identity revalidation and connection checks. Timing includes network, response reading and JSON decoding; it is not Server processing time.",
    actionHint:
      "Counts each native Memory/Scope invocation, including context and identity checks. Timing excludes UI confirmation and debounce. One action can produce multiple HTTP requests.",
    outcomesHint:
      "Success, failure, cancellation and unknown outcomes stay separate. HTTP success does not guarantee a successful save; cancelling a request does not prove that a remote write was cancelled.",
    timingHint:
      "Minimum, mean and maximum durations are milliseconds, including cancelled and unknown samples in each group.",
    noSamples: "No terminal samples of this unit in the current window.",
    operation: "Operation",
    samples: "Samples",
    success: "Success",
    failure: "Failure",
    cancelled: "Cancelled",
    unknown: "Unknown outcome",
    minimum: "Minimum (ms)",
    mean: "Mean (ms)",
    maximum: "Maximum (ms)",
    scrollHint:
      "In narrow windows, focus the table region to scroll horizontally.",
    coverage: "Collection coverage",
    operationCoverage: "Operations included in collection",
    localOps: "Local setup and operations",
    localOpsUnavailable:
      "The native Installer/Ops observation source is not connected. Local setup and operation outcomes are unavailable.",
    localOpsHint:
      "Safe snapshots returned by native Ops calls in this Desktop session, separate from HTTP request and user-action counts and latency. Downloads, installation and service work do not contribute to HTTP latency. No journal reads, automatic status queries or retries.",
    opsReferenceHint:
      "Use an operation reference to query local Ops status. The service phase is the last observed phase, not evidence of later state or persistence. The setup flow reports preflight or authorization failures before a reference exists; this view never invents one.",
    opsEmpty:
      "No Ops call snapshots in this window. This does not mean the machine has never been installed or encountered failures.",
    opsIncomplete:
      "Ops observation coverage is incomplete: snapshots were evicted or lost during collection.",
    opsRetained: "Retained Ops call snapshots",
    opsCapacity: "Ops snapshot capacity",
    opsReference: "Opaque operation reference",
    opsAction: "Ops call",
    opsAttempt: "Attempt",
    opsPhase: "Last observed service phase",
    opsAttemptPhase: "Attempt phase",
    opsOutcome: "Ops outcome",
    opsError: "Safe error codes",
    opsObservedAt: "Observed at (UTC)",
    notObserved: "Not observed",
    opsActions: {
      inspect: "Inspect",
      config_validate: "Validate configuration",
      preflight: "Preflight",
      apply: "Apply",
      resume: "Resume",
      operation_status: "Query status",
      cancel: "Request cancellation",
    },
    opsPhases: {
      accepted: "Accepted",
      registration_dispatching: "Dispatching registration",
      service: "Service operation",
      preparing: "Preparing resources",
      prepared: "Resources prepared",
      publishing: "Publishing resources",
      resources_published: "Resources published",
      configuration_prepared: "Configuration prepared",
      configured: "Configured",
      registering: "Registering service",
      complete: "Phase complete",
    },
    opsAttemptPhases: {
      accepted: "Accepted",
      executing: "Executing",
      finished: "Finished",
    },
    opsOutcomes: {
      succeeded: "Succeeded",
      pending: "Pending",
      cancelled: "Cancelled",
      failed: "Failed",
      partial_failure: "Partial failure",
      uncertain: "Uncertain",
      rejected: "Rejected",
      unknown: "Unknown",
    },
    previewTitle: "Safe diagnostic summary preview",
    previewHint:
      "The native host builds this text from allowlisted fields, excluding Memory content, queries, credentials, raw endpoints, identity/Scope IDs, private paths and raw exceptions. Copy uses exactly the text below. This snapshot does not update or upload automatically.",
    previewLoading: "Generating safe diagnostic preview…",
    previewFailed: "Unable to generate safe diagnostic preview",
    previewRetry: "Generate preview again",
    previewText: "Safe diagnostic text to copy",
    copy: "Copy preview text",
    copying: "Copying…",
    copied: "Safe diagnostic summary copied.",
    copyFailed: "Copy failed. Select the text above and copy it manually.",
    close: "Close preview",
    operations: {
      get_liveness: "Check liveness",
      get_server_info: "Read Server information",
      get_readiness: "Check readiness",
      get_capabilities: "Read capabilities",
      get_access_principal: "Verify identity",
      list_scopes: "Find Scopes",
      get_scope: "Verify and select Scope",
      get_default_scope: "Resolve default Scope",
      remember_memory: "Save Memory",
      search_memory: "Search Memory",
      get_artifact_revision: "Read exact revision",
    },
  },
} as const;
