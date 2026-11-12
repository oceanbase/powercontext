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

export type Locale = 'zh-CN' | 'en'
export const LOCALE_KEY = 'powercontext.dsh.locale'
export const texts = {
  "zh-CN": {
    "status": "状态",
    "diagnostics": "连接诊断",
    "language": "切换语言",
    "refresh": "刷新状态",
    "close": "关闭诊断",
    "retry": "重试",
    "ready": "已连接",
    "failed": "需要处理",
    "connecting": "连接中",
    "unconfigured": "未配置",
    "stale": "状态已过期，重试中",
    "auth": "需要认证",
    "connectingHelp": "正在检查 PowerContext Server。",
    "staleHelp": "正在重新检查连接。",
    "unconfiguredHelp": "请检查本机插件配置。",
    "authHelp": "请检查 Server 认证配置；若 DSH 登录已过期，请重新登录后重试。",
    "readinessHelp": "Server 尚未就绪，请检查服务日志与详情中的依赖状态。",
    "networkHelp": "无法连接 Server，请检查服务是否启动以及地址和网络。",
    "timeoutHelp": "Server 响应超时，请检查服务负载并稍后刷新。",
    "protocolHelp": "Server 响应格式不兼容，请检查 Server 与插件版本及代理配置。",
    "forbiddenHelp": "当前认证没有访问权限，请检查 Server 授权配置。",
    "failureHelp": "请确认 Server 正在运行并检查地址与配置，随后刷新。可运行 /pc doctor 查看诊断。",
    "bridgeHelp": "请使用本机 DSH 所有者会话，并检查插件是否已启用。仅在自己的本地 Web 主机配置 ownerLocal。",
    "schemaHelp": "请安装相互兼容的 PowerContext Server 和 DSH 插件版本。",
    "notChecked": "尚未检查",
    "server": "服务器",
    "address": "地址",
    "serverVersion": "PowerContext Server 版本",
    "unknown": "未知",
    "session": "会话与 Scope",
    "conversation": "当前会话",
    "workspace": "工作目录",
    "scope": "当前 Scope",
    "unresolved": "未解析",
    "noSession": "暂无活动会话。",
    "scopeHelp": "无法解析当前 Scope，请检查工作目录绑定或运行 /pc doctor。",
    "details": "技术详情",
    "connection": "连接检查",
    "deployment": "运行环境",
    "explicit": "显式本机配置",
    "automatic": "自动处理链路",
    "automaticOwner": "自动识别 Desktop",
    "reason": "诊断信息",
    "endpointSource": "地址来源",
    "pathPrefix": "（含路径前缀）",
    "authentication": "认证",
    "configured": "已配置",
    "disabled": "已禁用",
    "liveness": "存活检查",
    "readiness": "就绪检查",
    "capabilities": "能力检查",
    "dependencies": "就绪依赖",
    "scopeSource": "Scope 来源",
    "schema": "上下文协议",
    "memory": "记忆提取",
    "handoff": "交接生成",
    "ok": "已验证",
    "noObservation": "尚未观察到本会话的自动处理；发送消息后刷新。",
    "prepare": "召回",
    "capture": "Source 捕获",
    "flush": "记忆处理",
    "injection": "上下文注入",
    "not_yet_observed": "尚未观察",
    "running": "处理中",
    "resolved": "已解析",
    "empty": "无内容",
    "accepted": "已接收",
    "completed": "已完成",
    "incomplete": "未完成",
    "appended": "已注入",
    "skipped": "已跳过",
    "unavailable": "不可用",
    "operationHelp": "请运行 /pc doctor 检查配置与服务状态。",
    "coverage": "本地观察时间不代表记忆新鲜度。Source 接收与处理进度不证明记忆已生成；注入仅表示加入消息，不证明模型已使用。未确认的写入可能已生效，失败也不会回滚之前的工作。使用 /pc doctor 检查当前配置。",
    "no_active_session": "暂无活动会话。",
    "session_unavailable": "所选会话已不可用。",
    "session_service_unavailable": "主机未提供会话列表。",
    "scope_unresolved": "当前 Scope 未解析。"
  },
  "en": {
    "status": "status",
    "diagnostics": "Connection diagnostics",
    "language": "Change language",
    "refresh": "Refresh status",
    "close": "Close diagnostics",
    "retry": "Retry",
    "ready": "Connected",
    "failed": "Action needed",
    "connecting": "Connecting",
    "unconfigured": "Not configured",
    "stale": "Status stale, retrying",
    "auth": "Authentication required",
    "connectingHelp": "Checking the PowerContext Server.",
    "staleHelp": "Rechecking the connection.",
    "unconfiguredHelp": "Check the local plugin configuration.",
    "authHelp": "Check Server authentication; if the DSH session expired, sign in again and retry.",
    "readinessHelp": "The Server is not ready. Check its logs and the dependency states in details.",
    "networkHelp": "Cannot reach the Server. Check that it is running and verify its address and network.",
    "timeoutHelp": "The Server timed out. Check its load and refresh again shortly.",
    "protocolHelp": "The Server response is incompatible. Check Server/plugin versions and proxy configuration.",
    "forbiddenHelp": "This credential lacks access. Check Server authorization configuration.",
    "failureHelp": "Check that the Server is running and its address and configuration are correct, then refresh. Run /pc doctor for diagnostics.",
    "bridgeHelp": "Use the local DSH owner session and check that the plugin is enabled. Configure ownerLocal only on your own local Web host.",
    "schemaHelp": "Install compatible PowerContext Server and DSH plugin versions.",
    "notChecked": "Not checked",
    "server": "Server",
    "address": "Address",
    "serverVersion": "PowerContext Server version",
    "unknown": "Unknown",
    "session": "Conversation and Scope",
    "conversation": "Conversation",
    "workspace": "Workspace",
    "scope": "Active Scope",
    "unresolved": "Unresolved",
    "noSession": "No active conversation.",
    "scopeHelp": "Unable to resolve Scope. Check the workspace binding or run /pc doctor.",
    "details": "Technical details",
    "connection": "Connection checks",
    "deployment": "Deployment",
    "explicit": "explicit owner config",
    "automatic": "Automatic path",
    "automaticOwner": "automatic desktop signal",
    "reason": "Diagnostic information",
    "endpointSource": "Endpoint source",
    "pathPrefix": " (with path prefix)",
    "authentication": "Authentication",
    "configured": "Configured",
    "disabled": "Disabled",
    "liveness": "Liveness",
    "readiness": "Readiness",
    "capabilities": "Capabilities",
    "dependencies": "Readiness dependencies",
    "scopeSource": "Scope source",
    "schema": "Context schema",
    "memory": "Memory extraction",
    "handoff": "Handoff generation",
    "ok": "Verified",
    "noObservation": "No automatic attempt observed for this conversation. Send a prompt, then refresh.",
    "prepare": "Recall",
    "capture": "Source capture",
    "flush": "Memory flush",
    "injection": "Context injection",
    "not_yet_observed": "Not yet observed",
    "running": "Running",
    "resolved": "Resolved",
    "empty": "Empty",
    "accepted": "Accepted",
    "completed": "Completed",
    "incomplete": "Incomplete",
    "appended": "Injected",
    "skipped": "Skipped",
    "unavailable": "Unavailable",
    "operationHelp": "Run /pc doctor to check configuration and service status.",
    "coverage": "Local observation age is not Memory freshness. Source acceptance and flush progress do not prove Memory production. Injection means appended messages, not model consumption. Unconfirmed writes may have taken effect; failures do not roll back earlier work. Use /pc doctor to check current configuration.",
    "no_active_session": "No active conversation.",
    "session_unavailable": "The selected conversation is no longer available.",
    "session_service_unavailable": "The host did not expose its session registry.",
    "scope_unresolved": "The current Scope is unresolved."
  }
} as const
export type TextKey = keyof typeof texts['en']

export function readLocale(): Locale {
  try { return localStorage.getItem(LOCALE_KEY) === 'en' ? 'en' : 'zh-CN' } catch { return 'zh-CN' }
}

export function saveLocale(locale: Locale): void {
  try { localStorage.setItem(LOCALE_KEY, locale) } catch { /* Preference is session-only when storage is blocked. */ }
}

export function failureTextKey(code?: string, status?: number): TextKey {
  if (status === 401 || /authentication|unauthorized/.test(code ?? '')) return 'authHelp'
  if (status === 403) return 'forbiddenHelp'
  if (code === 'unsupported_context_schema' || code === 'version_mismatch') return 'schemaHelp'
  if (code === 'not_ready' || code === 'degraded') return 'readinessHelp'
  if (/timeout/.test(code ?? '')) return 'timeoutHelp'
  if (/connection|unreachable|dns|tls/.test(code ?? '')) return 'networkHelp'
  if (/invalid_response|invalid_json|protocol/.test(code ?? '')) return 'protocolHelp'
  return 'failureHelp'
}
