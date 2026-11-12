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

import type { Language } from "./messages";
const text: Record<string, readonly [string, string]> = {
  state: ["本机状态", "Local state"],
  attempts: ["执行尝试历史", "Attempt history"],
  yes: ["是", "Yes"],
  no: ["否", "No"],
  diagnostics: ["本机诊断", "Local diagnostics"],
  request_failed: [
    "请求未完成。请检查本机状态和原操作结果，再选择受支持的下一步。",
    "The request did not complete. Inspect local state and the original operation result before choosing a supported next step.",
  ],
  timeout: [
    "请求超时。若已提交执行，请先查询持久操作结果，不要重复执行。",
    "The request timed out. If execution was submitted, query its durable result before retrying.",
  ],
  integrity_failure: [
    "可信资源校验失败。请核对发行包和固定资源，不能跳过校验执行。",
    "Trusted resource verification failed. Check the distribution package and pinned resources; verification cannot be bypassed.",
  ],
  configuration_error: [
    "配置缺失、无效或冲突。请检查已有配置并使用支持的修复流程，保留原配置和数据。",
    "Configuration is missing, invalid or conflicting. Inspect it and use supported repair while preserving configuration and data.",
  ],
  runtime_error: [
    "运行环境缺失或损坏。请检查实际状态并预检修复计划，保留用户数据。",
    "The runtime is missing or damaged. Inspect actual state and review a repair plan while retaining user data.",
  ],
  registration_missing: [
    "服务尚未注册。请检查实际安装后预检注册或安装计划。",
    "The service is not registered. Inspect the installation before reviewing registration or installation.",
  ],
  inspection_failed: [
    "本机检查失败。请检查权限和已有配置；不能据此判断服务已停止或未安装。",
    "Local inspection failed. Check permissions and existing configuration; this does not establish that the service is stopped or absent.",
  ],
  storage: [
    "本地状态读写失败。请检查磁盘和权限，保留操作记录后再检查。",
    "Local state could not be read or written. Check disk space and permissions and preserve operation records.",
  ],
  rejected_request: [
    "当前请求不受支持或未获授权。请使用匹配的发行资源和允许的操作，不会自动接管已有安装。",
    "The request is unsupported or unauthorized. Use matching distribution resources and supported operations; existing installations will not be adopted automatically.",
  ],
  process_failure: [
    "辅助进程通信或进度记录失败。请查询持久结果并检查本机状态，不要自动重放。",
    "Helper communication or progress recording failed. Query the durable result and inspect local state; do not replay automatically.",
  ],
  reference_limit: [
    "操作引用数量已达上限。请保留记录并联系维护者使用支持的维护流程。",
    "The operation-reference limit was reached. Preserve the records and use a supported maintenance procedure with the maintainer.",
  ],
  shutting_down: [
    "应用正在退出。请重新打开后检查操作结果。",
    "The application is shutting down. Reopen it and inspect the operation result.",
  ],
  attempt_conflict: [
    "操作尝试已变化。请查询最新状态并重新预检恢复计划。",
    "The operation attempt changed. Query the latest status and preflight a new recovery plan.",
  ],
  installationOwnership: ["安装归属", "Installation ownership"],
  classification_missing: ["新安装", "New installation"],
  classification_owned_current: [
    "已有托管安装",
    "Existing managed installation",
  ],
  classification_owned_repairable: [
    "可修复的托管安装",
    "Repairable managed installation",
  ],
  classification_pending_operation: [
    "未完成的安装操作",
    "Incomplete installation operation",
  ],
  title: ["Local Server", "Local Server"],
  intro: [
    "在本机准备个人 Server，或独立连接已有 Server。",
    "Set up a personal Server on this machine, or connect an existing Server independently.",
  ],
  refresh: ["重新检查本机状态", "Inspect local state"],
  install: ["安装本地 Server", "Install local Server"],
  repair: ["修复 runtime", "Repair runtime"],
  start: ["启动服务", "Start service"],
  stop: ["停止服务", "Stop service"],
  restart: ["重启服务", "Restart service"],
  register: ["更新登录启动设置", "Update login startup"],
  remove_registration: ["移除服务注册", "Remove service registration"],
  existing: ["连接已有 Server", "Connect an existing Server"],
  connect: ["连接并进入默认 Scope", "Connect and open default Scope"],
  connectConsent: [
    "将激活本机实际返回的 Server 地址，检查兼容性、身份和就绪状态（可能探测模型及依赖），再读取默认 Scope。是否继续？",
    "Activate the actual local Server endpoint, check compatibility, identity and readiness (which may probe models and dependencies), then read its default Scope. Continue?",
  ],
  preview: ["预检并查看计划", "Preflight and review plan"],
  plan: ["确认本机操作计划", "Review local operation plan"],
  approve: ["批准并执行", "Approve and execute"],
  dismiss: ["关闭计划", "Dismiss plan"],
  expiry: [
    "计划有效期为 10 分钟，执行时会再次校验；过期或状态变化后需重新预检。",
    "Plans expire after 10 minutes and are revalidated at execution. Preflight again after expiry or state changes.",
  ],
  starts: [
    "此操作可能启动或重启后台服务，并检查就绪状态。",
    "This operation may start or restart the background service and check readiness.",
  ],
  noStart: [
    "此计划不会启动后台服务。",
    "This plan will not start the background service.",
  ],
  preservation: [
    "保留配置和用户数据。移除服务注册不会卸载 Desktop，也不会删除用户数据。",
    "Configuration and user data are retained. Removing service registration does not uninstall Desktop or delete user data.",
  ],
  independent: [
    "关闭 Desktop 后，已注册的 Server 由操作系统独立管理。尚未完成的操作须重新打开后检查，不保证退出后安装仍继续。",
    "The OS manages the registered Server independently of Desktop. Reinspect unfinished operations after reopening; installation is not guaranteed to continue after Quit.",
  ],
  port: ["本地 HTTP 端口", "Local HTTP port"],
  login: ["登录时启动", "Start on login"],
  policy: [
    "新安装使用 loopback、SQLite 和个人数据目录；基础 Memory 功能无需配置模型。已有配置保留原有选择。",
    "New installations use loopback, SQLite and personal data storage. Basic Memory needs no model configuration. Existing configuration choices are preserved.",
  ],
  version: ["Server 版本", "Server version"],
  program: ["程序入口", "Program entry"],
  configPath: ["配置文件", "Configuration file"],
  dataPath: ["用户数据目录", "User data directory"],
  endpoint: ["Server 地址", "Server endpoint"],
  space: ["所需空间（字节）", "Required space (bytes)"],
  resources: ["runtime 资源", "Runtime resources"],
  configuration: ["配置", "Configuration"],
  registration: ["服务注册", "Service registration"],
  managerOwnership: ["服务归属", "Service ownership"],
  manager: ["进程状态", "Process state"],
  liveness: ["本机服务响应", "Local service response"],
  readiness: ["就绪探测", "Readiness probe"],
  installationStage: ["安装阶段", "Installation stage"],
  database: ["数据库兼容性", "Database compatibility"],
  operation: ["操作", "Operation"],
  config_apply: ["应用配置", "Apply configuration"],
  history: ["操作与恢复", "Operations and recovery"],
  select: ["选择历史操作", "Select recorded operation"],
  none: ["暂无操作记录", "No recorded operations"],
  attempt: ["执行尝试", "Attempt"],
  status: ["查询操作结果", "Query operation result"],
  resume: ["预检恢复计划", "Review recovery plan"],
  cancel: ["请求取消操作", "Request cancellation"],
  cancelHint: [
    "取消请求不代表操作已经停止；以持久结果为准。",
    "A cancellation request does not establish that work stopped; consult the durable result.",
  ],
  uncertainHint: [
    "结果尚未确认。先查询持久状态；不能自动重放或根据进度推断成功。",
    "The outcome is unconfirmed. Query durable status first; progress does not establish success and work is never automatically replayed.",
  ],
  recoveryHint: [
    "恢复前重新检查实际状态并批准新计划；旧尝试的结果仍会保留。",
    "Recovery reinspects actual state and requires approval of a fresh plan; earlier attempt results are retained.",
  ],
  legacyHint: [
    "此记录没有可验证的操作描述，只能查询。请检查本机状态后选择受支持的维护操作。",
    "This record has no verifiable action description and is query-only. Inspect local state before choosing a supported maintenance operation.",
  ],
  unavailable: [
    "本地安装资源不可用",
    "Local installation resources unavailable",
  ],
  unavailableHint: [
    "此 Desktop 需要发行方提供匹配且受信任的 Installer/Ops 资源。请使用包含这些资源的安装包；连接已有 Server 仍可使用。",
    "This Desktop needs matching trusted Installer/Ops resources from its distributor. Use a package containing those resources; existing-Server connections remain available.",
  ],
  loading: ["正在检查本机状态", "Inspecting local state"],
  executing: ["正在执行已批准操作", "Executing approved operation"],
  preflighting: [
    "正在预检，不会执行安装或服务修改",
    "Preflighting; no installation or service mutation",
  ],
  progressMissing: [
    "实时进度不可用；请重新检查本机状态以发现操作引用，再查询结果或请求取消。",
    "Live progress is unavailable. Inspect local state to discover the operation reference, then query its result or request cancellation.",
  ],
  unknown: [
    "未知或无法确认；请重新检查",
    "Unknown or unconfirmed; inspect again",
  ],
  succeeded: ["操作成功", "Operation succeeded"],
  pending: ["尚无终态结果", "No terminal result yet"],
  failed: ["失败", "Failed"],
  partial_failure: ["部分失败", "Partial failure"],
  uncertain: ["结果不确定", "Uncertain outcome"],
  cancelled: ["已取消", "Cancelled"],
  rejected: ["请求被拒绝", "Rejected"],
  supported: ["支持", "Supported"],
  unsupported: ["不支持", "Unsupported"],
  missing: ["需要下载", "Download required"],
  damaged: ["已损坏", "Damaged"],
  verified: ["校验通过", "Verified"],
  present: ["程序存在", "Program present"],
  missing_executable: [
    "程序缺失，需要修复",
    "Program missing; repair required",
  ],
  valid: ["有效", "Valid"],
  missing_or_invalid: ["缺失或无效", "Missing or invalid"],
  installed: ["已注册", "Registered"],
  not_installed: ["未注册", "Not registered"],
  invalid: ["无效", "Invalid"],
  current: ["当前有效", "Current"],
  owned: ["属于 PowerContext", "Owned by PowerContext"],
  foreign: ["其他程序或未知归属", "Foreign or unowned"],
  not_loaded: ["未加载", "Not loaded"],
  active: ["运行中", "Running"],
  inactive: ["未运行", "Stopped"],
  live: ["有响应", "Responding"],
  unreachable: ["无法访问", "Unreachable"],
  not_probed: ["未探测", "Not probed"],
  verified_during_operation: ["操作中已验证", "Verified during operation"],
  new: ["新数据库", "New database"],
  existing_ready: ["已确认兼容", "Compatibility confirmed"],
  existing_unknown: [
    "需要目标 Server 检查",
    "Target Server admission required",
  ],
  incompatible: ["不兼容，需要维护", "Incompatible; maintenance required"],
  accepted: ["操作已接收", "Accepted"],
  preparing: ["准备资源", "Preparing resources"],
  prepared: ["资源已准备", "Resources prepared"],
  publishing: ["发布本机资源", "Publishing local resources"],
  resources_published: ["资源已就位", "Resources in place"],
  configuration_prepared: ["配置已准备", "Configuration prepared"],
  configured: ["配置已写入", "Configured"],
  registering: ["注册后台服务", "Registering service"],
  registration_dispatching: ["正在交付注册操作", "Dispatching registration"],
  service: ["执行服务操作", "Applying service operation"],
  complete: ["安装阶段完成", "Installation stages complete"],
  finished: ["执行尝试已结束", "Attempt finished"],
  interrupted: [
    "执行进程已退出，尚无终态结果",
    "Executor exited without a terminal result",
  ],
  busy: [
    "其他操作正在执行；请检查操作状态后再试。",
    "Another operation is running; inspect its status before retrying.",
  ],
  stale_approval: [
    "计划已失效，请重新预检。",
    "The plan is stale; preflight again.",
  ],
  plan_stale: [
    "本机状态已变化，请重新预检。",
    "Local state changed; preflight again.",
  ],
  port_conflict: [
    "端口被占用。请检查已有 Server，或选择其他端口重新预检；不会接管未知监听程序。",
    "The port is occupied. Inspect the existing Server or preflight with another port; unknown listeners are never adopted.",
  ],
  existing_independent: [
    "发现独立安装。请通过连接页面使用，不会自动接管。",
    "An independent installation was found. Use Connections; it will not be taken over.",
  ],
  ownership_conflict: [
    "安装或服务归属冲突。请检查已有安装，不会覆盖或接管。",
    "Installation or service ownership conflicts. Inspect the existing installation; it will not be overwritten or adopted.",
  ],
  database_error: [
    "数据库检查未通过。请使用受支持的备份、迁移或维护流程；不要删除数据或反复重启。",
    "Database admission did not pass. Use supported backup, migration or maintenance; do not delete data or repeatedly restart.",
  ],
  resource_error: [
    "资源下载、校验或准备失败。请检查网络和发行资源，重新检查后预检恢复计划。",
    "Resource download, verification or preparation failed. Check connectivity and distribution resources, then inspect and review recovery.",
  ],
  different: [
    "此操作属于其他资源绑定，不能在当前资源下重放。",
    "This operation belongs to a different resource binding and cannot be replayed here.",
  ],
  foreign_binding: [
    "资源绑定已改变。需要匹配的可信发行资源才能查询或恢复。",
    "The resource binding changed. Matching trusted distribution resources are required to query or recover.",
  ],
  operation_missing: [
    "尚未找到持久操作。先检查本机实际状态，不能假定此前请求成功或自动重放。",
    "No durable operation was found. Inspect actual local state; do not assume success or automatically replay.",
  ],
};
export function localText(language: Language, key: string): string {
  return (
    text[key]?.[language === "zh" ? 0 : 1] ??
    text.unknown[language === "zh" ? 0 : 1]
  );
}
export function localError(error: unknown): string {
  const key =
    typeof error === "string"
      ? error
      : error && typeof error === "object" && "code" in error
        ? String(error.code)
        : "unknown";
  const aliases: Record<string, string> = {
    configuration_invalid: "configuration_error",
    configuration_conflict: "configuration_error",
    configuration_not_owned: "ownership_conflict",
    configuration_not_local: "ownership_conflict",
    configuration_path_unsupported: "rejected_request",
    runtime_damaged: "runtime_error",
    repair_required: "runtime_error",
    installation_missing: "runtime_error",
    installation_record_invalid: "storage",
    insufficient_space_or_permissions: "storage",
    option_unsupported: "rejected_request",
    forbidden: "rejected_request",
    invalid_input: "rejected_request",
    invalid_request: "rejected_request",
    invalid_response: "rejected_request",
    unsupported_action: "rejected_request",
    unsupported_protocol: "rejected_request",
    unsupported_platform: "rejected_request",
    invalid_input_or_binding: "rejected_request",
    operation_busy: "busy",
    plan_invalid: "stale_approval",
    operation_conflict: "attempt_conflict",
    operation_superseded: "attempt_conflict",
    attempt_limit: "attempt_conflict",
    unknown_operation: "operation_missing",
    progress_unavailable: "process_failure",
    checkpoint_failed: "process_failure",
    repair_ownership_conflict: "ownership_conflict",
    target_conflict: "ownership_conflict",
    record_invalid: "storage",
    record_unsupported: "storage",
    authorization_required: "rejected_request",
    invalid_target_binding: "rejected_request",
    invalid_managed_binding: "rejected_request",
    layout_invalid: "rejected_request",
  };
  if (key.startsWith("database_")) return "database_error";
  if (key.startsWith("resource_")) return "resource_error";
  if (["unavailable", "managed_installation_unavailable"].includes(key))
    return "unavailableHint";
  return aliases[key] ?? (Object.hasOwn(text, key) ? key : "request_failed");
}
