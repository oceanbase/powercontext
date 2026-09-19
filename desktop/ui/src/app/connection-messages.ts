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

export const connectionMessages = {
  zh: {
    add: "添加连接",
    name: "连接名称",
    endpoint: "Server 地址",
    authentication: "认证传输",
    anonymous: "明确使用未认证的本机服务",
    bearer: "Bearer 凭据",
    secret: "新的 Bearer 凭据",
    storage: "凭据保存方式",
    persistent: "Windows 凭据管理器",
    session: "仅本次会话",
    keep: "保留现有凭据",
    ca: "显式 CA 证书（PEM，可选）",
    systemTrust: "留空使用系统证书信任；不支持跳过证书校验。",
    compatibility: "已验证兼容配置",
    unselected: "未选择：仅检查连接",
    compatibilityHint:
      "配置表示已测试的 Server 构建，不证明远端二进制身份。请先核对部署版本。",
    save: "保存配置",
    check: "检查连接",
    activate: "使用此连接",
    active: "当前使用",
    selected: "正在查看",
    remove: "移除连接",
    removeConfirm:
      "只移除此桌面配置和自有凭据，不停止服务或删除业务数据。继续？",
    cancel: "取消编辑",
    discard: "丢弃尚未保存的输入？",
    dirty: "尚未保存；保存后才能检查或使用。",
    wait: "正在检查…",
    saved: "配置已保存，尚未执行新的连接验证。",
    not_required: "无需凭据",
    stored: "已保存到系统凭据库",
    session_only: "仅本次会话可用",
    missing: "需要提供凭据",
    cleanup: "部分旧凭据等待清理，重新启动后会再次尝试。",
    live: "服务可达性",
    readiness: "就绪状态",
    identity: "服务返回的身份",
    access: "访问控制",
    capabilities: "服务声明的运行能力（不是授权列表）",
    checked: "检查时间",
    verified: "已验证",
    unverified: "未验证",
    noIdentity: "已明确使用未认证本机服务；Server 未提供 Principal。",
    noPermissionClaim: "具体操作仍由 Server 在每次请求时授权。",
    disconnect: "断开桌面连接",
    scope: "选择精确范围",
    scopeQuery: "按范围标题查找",
    find: "查找范围",
    more: "下一页",
    exact: "精确 Scope ID",
    choose: "选择范围",
    defaultScope: "查看默认范围建议",
    empty: "本页没有可访问的范围。",
    currentScope: "当前范围",
    scopeHint: "选择只影响此桌面，不创建范围或修改 Agent 绑定。",
    noConnection: "连接并明确启用服务后，才能选择范围。",
    noCompatibility: "请选择已验证的兼容配置并重新检查连接。",
    compatibility_unverified: "请选择已验证的兼容配置并重新检查连接。",
    not_connected: "请先启用连接。",
    scope_required: "请先选择精确范围。",
    error: "操作未完成，请检查连接或输入。",
    credential_unavailable: "系统凭据库不可用。可明确选择仅本次会话，或取消。",
    credential_missing: "请重新提供凭据；仅会话凭据会在退出后失效。",
    unauthorized: "Server 要求有效凭据。",
    forbidden: "当前身份无权执行此操作。",
    authentication_unavailable: "认证服务暂时不可用；不会改用匿名身份重试。",
    runtime_not_ready: "服务未提供此项运行能力。",
    tls: "证书或主机名验证失败。",
    redirect: "服务返回重定向，桌面未跟随。",
    insecure_transport: "远程连接必须使用 HTTPS。",
    invalid_endpoint: "地址无效或包含不允许的路径。",
    invalid_certificate: "CA 证书无效，不能包含私钥。",
    invalid_credential: "凭据无效；地址或信任变化后需要重新配置。",
    invalid_input: "请检查输入内容和长度。",
    duplicate_name: "已存在同名连接。",
    conflict: "配置已变化，请重新打开后编辑。",
    profile_corrupt:
      "本地连接配置损坏，未覆盖原文件。请备份并检查 profiles.json。",
    storage_error: "无法保存本地配置。",
    stale_context: "连接或范围已变化，请重新操作。",
    timeout: "请求超时。",
    network: "无法连接服务。",
    server: "服务暂时无法完成请求。",
    response_too_large: "响应超过桌面允许的大小。",
    invalid_response: "服务响应不符合已验证契约。",
    busy: "已有操作正在进行，请稍后再试。",
    not_found: "目标不存在或不可访问。",
    cursor_expired: "分页已失效，请从第一页重新查找。",
  },
  en: {
    add: "Add connection",
    name: "Connection name",
    endpoint: "Server address",
    authentication: "Authentication transport",
    anonymous: "Explicitly use an unauthenticated loopback server",
    bearer: "Bearer credential",
    secret: "New Bearer credential",
    storage: "Credential storage",
    persistent: "Windows Credential Manager",
    session: "This session only",
    keep: "Keep existing credential",
    ca: "Explicit CA certificate (PEM, optional)",
    systemTrust:
      "Leave blank for system trust. Certificate verification cannot be disabled.",
    compatibility: "Verified compatibility profile",
    unselected: "Not selected: connection checks only",
    compatibilityHint:
      "A profile identifies a tested Server build, not the identity of the remote binary. Check your deployment version.",
    save: "Save configuration",
    check: "Check connection",
    activate: "Use this connection",
    active: "Currently active",
    selected: "Viewing",
    remove: "Remove connection",
    removeConfirm:
      "Remove only this Desktop configuration and its credentials, without stopping the service or deleting business data?",
    cancel: "Cancel editing",
    discard: "Discard unsaved input?",
    dirty: "Unsaved changes: save before checking or activating.",
    wait: "Checking…",
    saved: "Configuration saved; a new connection check has not run.",
    not_required: "No credential required",
    stored: "Stored in the system vault",
    session_only: "Available for this session only",
    missing: "Credential required",
    cleanup: "Some old credentials await cleanup. Restart to try again.",
    live: "Server reachability",
    readiness: "Readiness",
    identity: "Server-reported identity",
    access: "Access control",
    capabilities: "Server-declared capabilities (not granted permissions)",
    checked: "Checked at",
    verified: "Verified",
    unverified: "Not verified",
    noIdentity:
      "Explicit unauthenticated loopback access; the Server does not provide a Principal.",
    noPermissionClaim:
      "The Server authorizes each actual operation independently.",
    disconnect: "Disconnect Desktop",
    scope: "Select an exact scope",
    scopeQuery: "Find scopes by title",
    find: "Find scopes",
    more: "Next page",
    exact: "Exact Scope ID",
    choose: "Select scope",
    defaultScope: "Inspect default scope suggestion",
    empty: "No accessible scopes on this page.",
    currentScope: "Current scope",
    scopeHint:
      "Selection affects only Desktop, without creating scopes or changing Agent bindings.",
    noConnection:
      "Connect and explicitly activate a server before choosing a scope.",
    noCompatibility:
      "Select a verified compatibility profile and check the connection again.",
    compatibility_unverified:
      "Select a verified compatibility profile and check the connection again.",
    not_connected: "Activate a connection first.",
    scope_required: "Select an exact scope first.",
    error: "The operation did not complete. Check the connection or input.",
    credential_unavailable:
      "The system vault is unavailable. Explicitly choose session-only storage or cancel.",
    credential_missing:
      "Provide a credential again. Session-only credentials expire when Desktop exits.",
    unauthorized: "The Server requires a valid credential.",
    forbidden: "This identity cannot perform this operation.",
    authentication_unavailable:
      "Authentication is temporarily unavailable. No anonymous retry was attempted.",
    runtime_not_ready: "The Server does not provide this runtime capability.",
    tls: "Certificate or hostname verification failed.",
    redirect: "The Server returned a redirect; Desktop did not follow it.",
    insecure_transport: "Remote connections require HTTPS.",
    invalid_endpoint: "The address is invalid or contains an unsupported path.",
    invalid_certificate:
      "The CA certificate is invalid or contains a private key.",
    invalid_credential:
      "Invalid credential. Reconfigure it after address or trust changes.",
    invalid_input: "Check the input and length limits.",
    duplicate_name: "A connection with this name already exists.",
    conflict: "Configuration changed. Reopen it before editing.",
    profile_corrupt:
      "Local connection configuration is corrupt and has not been overwritten. Back up and inspect profiles.json.",
    storage_error: "Unable to save local configuration.",
    stale_context: "The connection or scope changed. Try the operation again.",
    timeout: "The request timed out.",
    network: "Unable to reach the Server.",
    server: "The Server cannot complete this request right now.",
    response_too_large: "The response exceeds the Desktop size limit.",
    invalid_response: "The response does not match the verified contract.",
    busy: "Another operation is in progress. Try again shortly.",
    not_found: "The target does not exist or is inaccessible.",
    cursor_expired: "Pagination expired. Start again from the first page.",
  },
};
export function connectionError(error: unknown, language: "zh" | "en"): string {
  const text = connectionMessages[language];
  const code =
    typeof error === "string"
      ? error
      : error && typeof error === "object" && "code" in error
        ? error.code
        : null;
  const key = code === "storage" ? "storage_error" : code;
  return typeof key === "string" && Object.hasOwn(text, key)
    ? text[key as keyof typeof text]
    : text.error;
}
