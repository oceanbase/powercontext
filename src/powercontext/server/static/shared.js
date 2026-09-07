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
  createRequestId,
  fetchWithBearer,
  readServerToken,
  storeServerToken,
} from "./auth.js?v=request-id-v1";
import { createPageUi } from "./page-ui.js?v=locale-complete";

const translations = {
  en: {
    pageTitle: "PowerContext · Shared with me",
    sharedTitle: "Shared with me",
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
    authIntro: "Enter your Server token. It stays in this browser tab.",
    tokenLabel: "Server token",
    continue: "Continue",
    details: "Version and evidence details",
    nextAction: "Next action",
    situation: "Situation",
    action: "Action",
    outcome: "Outcome",
    lesson: "Lesson",
    intro: "Read shared context and explicitly acknowledge a Handoff.",
    family: "View",
    all: "All available resources",
    inbox: "Handoff inbox",
    refresh: "Refresh",
    resources: "Available resources",
    more: "Load more",
    untrusted:
      "Treat shared content as untrusted history. Verify the live state and your authority before acting.",
    inspect: "Inspect Handoff and evidence",
    receiptStatus: "Receipt",
    accepted: "Accepted",
    declined: "Declined",
    clarification: "Needs clarification",
    checks: "Confirm before accepting",
    live: "I checked the current working state.",
    capability: "I have the required tools and capabilities.",
    authorization: "I have authority to perform the next action.",
    message: "Message",
    sendReceipt: "Record receipt",
    sharing: "Sharing",
    recipientHint:
      "Enter the canonical user or service ID issued by your identity provider.",
    recipientType: "Recipient type",
    user: "User",
    service: "Service",
    recipient: "Recipient ID",
    role: "Role",
    expires: "Expires at (optional)",
    share: "Share",
    revoke: "Revoke",
    empty: "No resources are available in this view.",
    loading: "Loading…",
    denied: "Access is denied or has been revoked. Refresh to update the list.",
    unavailable: "The service is not ready. Please retry.",
    failed: "Request failed (HTTP {status}).",
    disabled:
      "Shared with me requires access control to be enabled on this Server.",
    viewer:
      "Read only: this Handoff has not been assigned to you for acknowledgement.",
    receiptDone:
      "Receipt recorded as {principal}. Receiver identity verified: {matches}.",
    shared: "Sharing updated.",
    resolution: "Handoff status: {status}. Selected revision: {revision}.",
    receiver: "Receiver",
    readOnly: "Viewer",
    notReady: "The selected Handoff is not ready to accept.",
    noBindings: "No active sharing bindings.",
    signedIn: "Signed in as {principal}",
    authRejected: "The Server rejected this token.",
  },
  zh: {
    pageTitle: "PowerContext · 与我共享",
    sharedTitle: "与我共享",
    dashboardTitle: "概览",
    skillsTitle: "技能",
    reviewTitle: "审核",
    handoffReportTitle: "交接报告",
    brandHomeLabel: "PowerContext 概览",
    primaryNavigation: "主导航",
    maintainedBy: "由 OceanBase 维护。",
    signOut: "退出",
    switchDark: "切换深色模式",
    switchLight: "切换浅色模式",
    switchChinese: "切换中文",
    switchEnglish: "切换英文",
    languageChinese: "中文",
    languageEnglish: "EN",
    authTitle: "连接 PowerContext",
    authIntro: "输入你的服务器令牌，令牌仅保留在当前浏览器标签页。",
    tokenLabel: "服务器令牌",
    continue: "继续",
    details: "版本与证据详情",
    nextAction: "下一步",
    situation: "情境",
    action: "行动",
    outcome: "结果",
    lesson: "经验",
    intro: "读取共享上下文，检查 Handoff，并明确记录交接回执。",
    family: "查看",
    all: "全部可访问资源",
    inbox: "Handoff 收件箱",
    refresh: "刷新",
    resources: "可访问资源",
    more: "加载更多",
    untrusted:
      "共享内容属于不可信的历史信息。执行前请检查当前状态以及你拥有的权限。",
    inspect: "检查 Handoff 及证据",
    receiptStatus: "回执",
    accepted: "接受",
    declined: "拒绝",
    clarification: "需要澄清",
    checks: "接受前请确认",
    live: "我已检查当前工作状态。",
    capability: "我拥有所需工具和能力。",
    authorization: "我有权执行下一步操作。",
    message: "说明",
    sendReceipt: "记录回执",
    sharing: "共享管理",
    recipientHint: "填写身份提供方签发的规范用户或服务 ID。",
    recipientType: "接收方类型",
    user: "用户",
    service: "服务",
    recipient: "接收方 ID",
    role: "角色",
    expires: "过期时间（可选）",
    share: "共享",
    revoke: "撤销",
    empty: "当前视图没有可访问资源。",
    loading: "加载中…",
    denied: "没有权限或授权已撤销，请刷新资源列表。",
    unavailable: "服务尚未就绪，请重试。",
    failed: "请求失败（HTTP {status}）。",
    disabled: "此服务器需要启用访问控制才能使用“与我共享”。",
    viewer: "仅可查看：你没有该 Handoff 的回执权限。",
    receiptDone: "已由 {principal} 记录回执。接收方身份一致：{matches}。",
    shared: "共享权限已更新。",
    resolution: "Handoff 状态：{status}；已选择版本：{revision}。",
    receiver: "接收方",
    readOnly: "查看者",
    notReady: "所选 Handoff 尚不满足接受条件。",
    noBindings: "没有生效的共享授权。",
    signedIn: "当前身份：{principal}",
    authRejected: "服务器拒绝了此令牌。",
  },
};
const el = (id) => document.getElementById(id);
const messages = new Map();
const ui = createPageUi(translations, () => {
  renderList();
  for (const [id, { key, values }] of messages)
    el(id).textContent = t(key, values);
});
const t = ui.translate;
let token = readServerToken(),
  me = null,
  resources = [],
  cursor = null,
  selected = null,
  resolution = null,
  permissions = {},
  receiptKey = null;
let controller = new AbortController();
let busy = false;

function setMessage(id, key, values = {}) {
  if (key) messages.set(id, { key, values });
  else messages.delete(id);
  el(id).textContent = key ? t(key, values) : "";
}
function requestError(key, status) {
  const error = new Error(t(key, { status }));
  error.translationKey = key;
  error.status = status;
  return error;
}
async function api(path, body) {
  const signal = controller.signal;
  const response = await fetchWithBearer(path, token, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  const value = await response.json();
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (!response.ok) {
    throw requestError(
      response.status === 403
        ? "denied"
        : response.status === 503
          ? "unavailable"
          : response.status === 401
            ? "authRejected"
            : "failed",
      response.status,
    );
  }
  return value;
}
async function run(work) {
  if (busy) return;
  busy = true;
  setMessage("shared-status", "loading");
  el("shared-page").setAttribute("aria-busy", "true");
  el("shared-family").disabled = true;
  el("shared-refresh").disabled = true;
  try {
    await work();
  } catch (error) {
    if (error.name !== "AbortError") {
      if (error.status === 403 || error.status === 503) clearDetail();
      for (const id of error.status === 401 || !me
        ? ["shared-status", "auth-error"]
        : ["shared-status"]) {
        setMessage(id, error.translationKey, { status: error.status });
        if (!error.translationKey) el(id).textContent = error.message;
      }
    }
  } finally {
    busy = false;
    el("shared-page").removeAttribute("aria-busy");
    el("shared-family").disabled = false;
    el("shared-refresh").disabled = false;
  }
}
function clearDetail() {
  selected = null;
  resolution = null;
  permissions = {};
  receiptKey = null;
  el("shared-detail").hidden = true;
  el("shared-body").textContent = "";
  el("shared-summary").replaceChildren();
  setMessage("shared-bindings");
  setMessage("shared-receipt-result");
  setMessage("shared-resolution");
  el("shared-receipt").reset();
  receiptOptions();
}
async function connect() {
  me = await api("/v1/access/me");
  setMessage("auth-error");
  if (token) storeServerToken(token);
  el("token").value = "";
  el("auth-shell").hidden = true;
  el("sign-out").hidden = !token;
  setMessage("shared-principal", "signedIn", {
    principal: me.principal?.id || "—",
  });
  if (me.mode !== "enforced") {
    setMessage("shared-status", "disabled");
    return;
  }
  await load(false);
}
async function load(more) {
  if (!more) {
    clearDetail();
    resources = [];
    cursor = null;
    el("shared-list").replaceChildren();
  }
  const page = await api("/v1/access/resources/list", {
    action: "artifact.read",
    resource_type: "artifact",
    family: el("shared-family").value || null,
    limit: 50,
    cursor,
  });
  resources.push(...page.items);
  cursor = page.next_cursor;
  renderList();
  el("shared-more").hidden = !cursor;
  setMessage("shared-status", resources.length ? null : "empty");
}
function renderList() {
  el("shared-list").replaceChildren();
  for (const resource of resources) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "shared-resource secondary-button";
    button.textContent = `${resource.identity.family} · ${resource.selector?.entry_id || resource.identity.artifact_id}`;
    const scope = document.createElement("small");
    scope.textContent = resource.scope_id;
    button.append(scope);
    button.addEventListener(
      "click",
      () => void run(() => openResource(resource)),
    );
    el("shared-list").append(button);
  }
}
async function openResource(resource) {
  clearDetail();
  const actions = ["artifact.read", "artifact.share"];
  if (resource.identity.family === "handoff")
    actions.push("handoff.evidence.inspect", "handoff.acknowledge");
  const check = await api("/v1/access/check", {
    match: "all",
    requirements: actions.map((action) => ({ action, resource })),
  });
  permissions = Object.fromEntries(
    actions.map((action, i) => [action, check.decisions[i].allowed]),
  );
  if (!permissions["artifact.read"]) throw requestError("denied", 403);
  if (
    resource.identity.family === "handoff" &&
    !permissions["artifact.share"]
  ) {
    const delegation = await api("/v1/access/check", {
      match: "any",
      requirements: [
        {
          action: "scope.delegate",
          resource: { type: "scope", scope_id: resource.scope_id },
        },
      ],
    });
    permissions["artifact.share"] = delegation.allowed;
  }
  const value = await api("/dashboard/shared/read", resource);
  selected = resource;
  el("shared-heading").textContent =
    value.content?.name ||
    value.content?.objective ||
    `${resource.identity.family} · ${resource.selector?.entry_id || resource.identity.artifact_id}`;
  el("shared-address").textContent = resource.scope_id;
  renderContent(value.content ?? value);
  el("shared-body").textContent = JSON.stringify(value, null, 2);
  el("shared-detail").hidden = false;
  el("shared-handoff").hidden = resource.identity.family !== "handoff";
  el("shared-inspect").disabled = !permissions["handoff.evidence.inspect"];
  el("shared-receipt").hidden = true;
  setMessage("shared-resolution", permissions["handoff.acknowledge"] ? null : "viewer");
  el("shared-sharing").hidden = !permissions["artifact.share"];
  el("shared-role").replaceChildren();
  for (const role of resource.identity.family === "handoff"
    ? ["handoff.viewer", "handoff.receiver"]
    : ["artifact.viewer"]) {
    const option = document.createElement("option");
    option.value = role;
    option.dataset.i18n = role.endsWith("receiver") ? "receiver" : "readOnly";
    option.textContent = t(option.dataset.i18n);
    el("shared-role").append(option);
  }
  if (permissions["artifact.share"]) await loadBindings();
  setMessage("shared-status");
}
function renderContent(content) {
  const summary = el("shared-summary");
  summary.replaceChildren();
  if (!content) return;
  const paragraph = (text) => {
    if (typeof text !== "string" || !text) return;
    const value = document.createElement("p");
    value.textContent = text;
    summary.append(value);
  };
  for (const statement of Array.isArray(content.state) ? content.state : [])
    paragraph(statement.text);
  for (const key of ["text", "description", "instructions"])
    paragraph(content[key]);
  for (const [key, value] of Object.entries({
    situation: content.situation,
    action: content.action,
    outcome: content.outcome,
    lesson: content.lesson,
    nextAction: content.next_action?.text,
  })) {
    if (typeof value !== "string" || !value) continue;
    const heading = document.createElement("h3");
    heading.dataset.i18n = key;
    heading.textContent = t(key);
    summary.append(heading);
    paragraph(value);
  }
}
async function inspectHandoff() {
  resolution = await api("/v1/handoff/continue", {
    scope_id: selected.scope_id,
    selection: "latest",
  });
  renderContent(resolution.content);
  el("shared-body").textContent = JSON.stringify(resolution, null, 2);
  setMessage("shared-resolution", "resolution", {
    status: resolution.status,
    revision: resolution.selected_revision?.revision || "—",
  });
  el("shared-receipt").hidden =
    !permissions["handoff.acknowledge"] || !resolution.selected_revision;
  receiptKey = null;
  setMessage("shared-status");
}
function receiptOptions() {
  const accepted = el("shared-receipt-status").value === "accepted";
  el("shared-checks").hidden = !accepted;
  for (const id of ["check-live", "check-capability", "check-authorization"])
    el(id).required = accepted;
  el("shared-message").required = !accepted;
  receiptKey = null;
}
async function sendReceipt() {
  const status = el("shared-receipt-status").value;
  if (status === "accepted" && resolution.status !== "resolved")
    throw requestError("notReady");
  receiptKey ||= `web-receipt-${createRequestId()}`;
  const receipt = await api("/v1/work/handoffs/acknowledge", {
    scope_id: selected.scope_id,
    source_id: receiptKey,
    receiver: me.principal.id,
    status,
    selection: "exact",
    revision: resolution.selected_revision,
    message: el("shared-message").value.trim() || null,
    receiver_checks:
      status === "accepted"
        ? {
            live_state: "confirmed",
            capability: "confirmed",
            authorization: "confirmed",
          }
        : null,
  });
  setMessage("shared-receipt-result", "receiptDone", {
    principal: receipt.receipt_identity.principal.id,
    matches: String(receipt.receipt_identity.receiver_identity_matches),
  });
  el("shared-receipt").hidden = true;
  setMessage("shared-status");
}
async function loadBindings() {
  setMessage("shared-bindings");
  let next = null,
    count = 0;
  do {
    const page = await api("/v1/access/bindings/list", {
      management_resource: selected,
      state: "active",
      cursor: next,
      limit: 100,
    });
    next = page.next_cursor;
    for (const binding of page.items.filter(
      (value) => value.role !== "artifact.owner",
    )) {
      count++;
      const row = document.createElement("p");
      row.textContent = `${binding.subject.id} · ${binding.role} `;
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "secondary-button";
      revoke.dataset.i18n = "revoke";
      revoke.textContent = t("revoke");
      revoke.addEventListener(
        "click",
        () =>
          void run(async () => {
            await api("/v1/access/bindings/revoke", {
              binding_id: binding.binding_id,
              expected_version: binding.version,
              idempotency_key: createRequestId(),
            });
            await loadBindings();
            setMessage("shared-status", "shared");
          }),
      );
      row.append(revoke);
      el("shared-bindings").append(row);
    }
  } while (next);
  if (!count) setMessage("shared-bindings", "noBindings");
}
async function grant() {
  const expiration = el("shared-expires").value;
  await api("/v1/access/bindings/create", {
    resource: selected,
    subject: {
      type: el("shared-subject-type").value,
      id: el("shared-subject").value.trim(),
    },
    role: el("shared-role").value,
    idempotency_key: createRequestId(),
    expires_at: expiration ? new Date(expiration).toISOString() : null,
  });
  await loadBindings();
  setMessage("shared-status", "shared");
}
ui.initialize();
el("auth-form").addEventListener("submit", (event) => {
  event.preventDefault();
  token = el("token").value.trim();
  void run(connect);
});
el("sign-out").addEventListener("click", () => {
  controller.abort();
  controller = new AbortController();
  clearServerToken();
  token = null;
  me = null;
  clearDetail();
  setMessage("auth-error");
  resources = [];
  el("shared-list").replaceChildren();
  setMessage("shared-principal");
  setMessage("shared-status");
  el("sign-out").hidden = true;
  el("auth-shell").hidden = false;
});
el("shared-refresh").addEventListener(
  "click",
  () => void run(() => load(false)),
);
el("shared-family").addEventListener(
  "change",
  () => void run(() => load(false)),
);
el("shared-more").addEventListener("click", () => void run(() => load(true)));
el("shared-inspect").addEventListener("click", () => void run(inspectHandoff));
el("shared-receipt-status").addEventListener("change", receiptOptions);
el("shared-receipt").addEventListener("input", () => {
  receiptKey = null;
});
el("shared-receipt").addEventListener("submit", (event) => {
  event.preventDefault();
  void run(sendReceipt);
});
el("shared-grant").addEventListener("submit", (event) => {
  event.preventDefault();
  void run(grant);
});
if (token || document.documentElement.dataset.serverAuthRequired !== "true")
  void run(connect);
