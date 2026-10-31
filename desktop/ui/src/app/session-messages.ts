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
      "此页面支持会话摘要版本 1。请使用匹配的 Desktop 界面和原生宿主。",
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
      "本地安装、运维及 CLI 诊断尚未纳入此摘要，也不计入 HTTP 延迟。",
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
      "This page supports session summary version 1. Use matching Desktop UI and native host versions.",
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
      "Local installation, operations and CLI diagnostics are not included in this summary or HTTP latency.",
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
