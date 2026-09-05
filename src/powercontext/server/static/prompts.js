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

import {clearServerToken, fetchWithBearer, readServerToken, storeServerToken} from "./auth.js?v=optional-auth";
import {createPageUi, createRequestGate} from "./page-ui.js?v=locale-complete";

const translations = {
  en: {
    pageTitle: "PowerContext Prompts", promptsTitle: "Prompts", dashboardTitle: "Overview", skillsTitle: "Skills",
    reviewTitle: "Review", handoffReportTitle: "Handoff Report", brandHomeLabel: "PowerContext Overview",
    primaryNavigation: "Primary navigation", maintainedBy: "Maintained by OceanBase.", signOut: "Sign out",
    switchDark: "Switch to dark mode", switchLight: "Switch to light mode", switchChinese: "Switch to Chinese",
    switchEnglish: "Switch to English", languageChinese: "中文", languageEnglish: "EN",
    authTitle: "Connect to PowerContext", authIntro: "Enter this Server's bearer token. It stays in this browser tab.",
    tokenLabel: "Server token", continue: "Continue", selectScope: "Scope", operations: "Prompt operations",
    intro: "Customize operational guidance in one Scope. Every save creates an immutable revision.",
    mode: "Mode", custom: "Custom", instructions: "Instructions",
    autoNote: "Auto uses the deployed built-in guidance. It does not delete version history.",
    safetyNote: "Do not include credentials or secrets. Custom guidance cannot change schemas, tools, or Scope permissions.",
    count: "Count", generate: "Generate demonstrations", add: "Add demonstration", remove: "Remove",
    demonstrationHint: "Each demonstration contains complete JSON input and expected output. Suggestions are not saved automatically.",
    positive: "Positive demonstrations", negative: "Negative demonstrations (valid no-op)",
    save: "Save new revision", reload: "Reload current", history: "Version history",
    historyNote: "Restoring creates a new revision. It does not remove history or rerun previous operations.",
    loadMore: "Load more", restore: "Restore as new revision", input: "Input (JSON)", output: "Expected output (JSON)",
    supported: "Customization available", disabled: "Disabled", unsupported: "Externally managed",
    provider_not_configured: "No inference provider is configured.", operation_disabled: "The operation is disabled.",
    injected_component: "The injected component owns its prompts. Select Auto to use its existing behavior.",
    builtin: "Built-in", revision: "Revision {revision}", noHistory: "No saved revisions. Auto is in effect.",
    current: "Current: {version}", saved: "Revision {revision} saved.", generated: "Suggestions added. Review them before saving.",
    discard: "Discard unsaved edits?", restoreConfirm: "Restore revision {revision} as a new current revision?",
    error: "Request failed ({code}).", conflict: "The head changed. Your edits are kept; reload the current revision before saving.",
    invalidJson: "Demonstration input and expected output must be valid JSON.", limit: "At most 50 demonstrations are allowed.",
    authRejected: "Authentication failed. Enter the current Server token.", noScopes: "No Scope is available.",
    "memory.extract": "Memory extraction", "memory.rerank": "Memory reranking",
    "experience.incubate": "Experience incubation", "experience.generate": "Experience generation",
    "skill.generate": "Skill generation", "handoff.generate": "Handoff generation"
  },
  zh: {
    pageTitle: "PowerContext 提示词", promptsTitle: "提示词", dashboardTitle: "概览", skillsTitle: "技能",
    reviewTitle: "审核", handoffReportTitle: "交接报告", brandHomeLabel: "PowerContext 概览",
    primaryNavigation: "主导航", maintainedBy: "由 OceanBase 维护。", signOut: "退出登录",
    switchDark: "切换深色模式", switchLight: "切换浅色模式", switchChinese: "切换中文",
    switchEnglish: "切换英文", languageChinese: "中文", languageEnglish: "EN",
    authTitle: "连接 PowerContext", authIntro: "输入当前服务器的访问令牌，仅保存在此浏览器标签页。",
    tokenLabel: "服务器令牌", continue: "继续", selectScope: "Scope", operations: "提示词操作",
    intro: "按 Scope 自定义操作提示词，每次保存都会创建不可变的新版本。",
    mode: "模式", custom: "自定义", instructions: "提示词指令",
    autoNote: "Auto 使用当前部署的内置指令，不会删除历史版本。",
    safetyNote: "请勿填写凭据或密钥。自定义指令不能修改输出结构、工具或 Scope 权限。",
    count: "数量", generate: "生成案例", add: "添加案例", remove: "移除",
    demonstrationHint: "每条案例包含完整的 JSON 输入和期望输出。生成的建议不会自动保存。",
    positive: "正向案例", negative: "反向案例（合法的无操作输出）",
    save: "保存新版本", reload: "重新加载当前版本", history: "版本历史",
    historyNote: "恢复操作会创建新版本，不会删除历史或重新处理之前的数据。",
    loadMore: "加载更多", restore: "恢复为新版本", input: "输入（JSON）", output: "期望输出（JSON）",
    supported: "支持自定义", disabled: "未启用", unsupported: "由外部组件管理",
    provider_not_configured: "未配置推理服务。", operation_disabled: "当前操作未启用。",
    injected_component: "注入的组件自行管理提示词。请选择 Auto 使用其原有行为。",
    builtin: "内置", revision: "版本 {revision}", noHistory: "尚无已保存版本，当前使用 Auto。",
    current: "当前：{version}", saved: "已保存版本 {revision}。", generated: "已添加生成建议，请检查后保存。",
    discard: "放弃尚未保存的编辑？", restoreConfirm: "将版本 {revision} 的内容恢复为新的当前版本？",
    error: "请求失败（{code}）。", conflict: "当前版本已变化。编辑内容已保留，请重新加载后再保存。",
    invalidJson: "案例的输入和期望输出必须是合法 JSON。", limit: "最多允许 50 条案例。",
    authRejected: "认证失败，请输入当前服务器令牌。", noScopes: "暂无可用 Scope。",
    "memory.extract": "记忆抽取", "memory.rerank": "记忆重排", "experience.incubate": "经验孵化",
    "experience.generate": "经验生成", "skill.generate": "技能生成", "handoff.generate": "交接生成"
  }
};
const keys = ["memory.extract", "memory.rerank", "experience.incubate", "experience.generate", "skill.generate", "handoff.generate"];
const noops = new Set(["memory.extract", "experience.incubate", "experience.generate", "skill.generate"]);
const $ = (id) => document.getElementById(id);
const gate = createRequestGate();
const scopeGate = createRequestGate();
const state = {scope: "", key: keys[0], capabilities: {}, heads: new Map(), head: null, etag: null,
  demos: [], nextId: 0, dirty: false, busy: false, loaded: false, cursor: null, historical: null};
const ui = createPageUi(translations, () => { renderKeys(); renderState(); renderDemonstrations(); });
const t = ui.translate;

function notice(key, error = false, values = {}) {
  $("prompt-notice").textContent = key ? t(key, values) : "";
  $("prompt-notice").dataset.error = String(error);
}
function make(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}
function path() {
  return "/v1/scopes/" + encodeURIComponent(state.scope) + "/artifacts/prompt/" + encodeURIComponent(state.key);
}
function mode() {
  return document.querySelector('input[name="mode"]:checked').value;
}
function capability() {
  return state.capabilities[state.key] || {status: "disabled", reason: "provider_not_configured"};
}
async function request(url, options = {}) {
  const response = await fetchWithBearer(url, readServerToken(), options);
  const value = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const error = new Error(value?.error?.code || String(response.status));
    error.status = response.status;
    if (response.status === 401) {
      clearServerToken();
      $("auth-error").textContent = t("authRejected");
    }
    throw error;
  }
  return {value, etag: response.headers.get("ETag")};
}
function report(error) {
  notice(error.status === 409 || error.status === 412 ? "conflict" : "error", true, {code: error.message});
}
function renderKeys() {
  $("prompt-keys").replaceChildren(...keys.map((key) => {
    const button = make("button", t(key));
    button.type = "button";
    button.setAttribute("aria-current", String(key === state.key));
    button.disabled = state.busy;
    const cap = state.capabilities[key];
    const head = state.heads.get(key);
    button.append(make("small", key + " · " + (head ? t("revision", {revision: head.revision}) : "Auto")));
    if (cap) button.append(make("small", t(cap.status)));
    button.addEventListener("click", () => {
      if (key === state.key || !discard()) return;
      state.key = key;
      void loadCurrent().catch(report);
    });
    return button;
  }));
}
function renderState() {
  const cap = capability();
  const custom = mode() === "custom";
  $("prompt-form").hidden = !state.loaded;
  $("prompt-history").hidden = !state.loaded;
  $("prompt-version").hidden = !state.loaded;
  $("prompt-title").textContent = t(state.key);
  $("prompt-capability").textContent = t(cap.status) + (cap.reason ? " · " + t(cap.reason) : "")
    + (cap.builtin_profile && cap.reason !== "injected_component" ? " · " + cap.builtin_profile : "");
  const selected = state.head ? t("revision", {revision: state.head.revision}) : "Auto";
  $("prompt-version").textContent = t("current", {version: selected})
    + (cap.builtin_version && cap.reason !== "injected_component" ? " · " + t("builtin") + ": " + cap.builtin_version : "");
  $("prompt-custom-mode").disabled = state.busy || !state.loaded || cap.status !== "supported";
  document.querySelector('input[name="mode"][value="auto"]').disabled = state.busy || !state.loaded;
  $("prompt-custom-fields").hidden = !custom;
  $("prompt-auto-note").hidden = custom;
  $("prompt-instructions").disabled = state.busy || !state.loaded || cap.status !== "supported";
  $("prompt-generate").disabled = state.busy || !state.loaded || cap.status !== "supported";
  $("prompt-add").disabled = state.busy || cap.status !== "supported" || state.demos.length >= 50;
  $("prompt-save").disabled = state.busy || !state.loaded || (custom && cap.status !== "supported");
  $("prompt-reload").disabled = state.busy || !state.scope;
  $("prompt-scope").disabled = state.busy;
  $("sign-out").disabled = state.busy;
  $("prompt-more").disabled = state.busy;
  $("prompt-negative-title").hidden = !noops.has(state.key);
  $("prompt-restore").disabled = state.busy || !state.historical
    || (state.historical.content.mode === "custom" && cap.status !== "supported");
}
function discard() {
  return !state.dirty || window.confirm(t("discard"));
}
function demoNegative(item) {
  try {
    const output = JSON.parse(item.output);
    return noops.has(state.key) && (Array.isArray(output?.candidates) && output.candidates.length === 0
      || Object.hasOwn(output || {}, "proposal") && output.proposal === null);
  } catch { return false; }
}
function appendDemos(values) {
  for (const value of values) {
    state.demos.push({id: state.nextId++, input: JSON.stringify(value.input, null, 2),
      output: JSON.stringify(value.expected_output, null, 2)});
  }
}
function renderDemonstrations() {
  $("prompt-positive").replaceChildren();
  $("prompt-negative").replaceChildren();
  for (const item of state.demos) {
    const card = make("section", "", "prompt-demonstration");
    for (const field of ["input", "output"]) {
      const label = make("label", t(field));
      const textarea = document.createElement("textarea");
      textarea.rows = 5;
      textarea.value = item[field];
      textarea.spellcheck = false;
      textarea.disabled = state.busy || !state.loaded || capability().status !== "supported";
      textarea.addEventListener("input", () => { item[field] = textarea.value; state.dirty = true; });
      label.append(textarea);
      card.append(label);
    }
    const remove = make("button", t("remove"), "secondary-button");
    remove.type = "button";
    remove.disabled = state.busy || capability().status !== "supported";
    remove.addEventListener("click", () => {
      state.demos = state.demos.filter((value) => value.id !== item.id);
      state.dirty = true;
      renderDemonstrations();
      renderState();
    });
    card.append(remove);
    $(demoNegative(item) ? "prompt-negative" : "prompt-positive").append(card);
  }
}
async function loadHistory() {
  if (!state.head) {
    $("prompt-revisions").replaceChildren(make("p", t("noHistory")));
    return;
  }
  const selection = state.scope + "/" + state.key;
  const suffix = state.cursor ? "?limit=20&cursor=" + encodeURIComponent(state.cursor) : "?limit=20";
  const {value} = await request(path() + "/revisions" + suffix);
  if (selection !== state.scope + "/" + state.key) return;
  for (const item of value.items) {
    const button = make("button", t("revision", {revision: item.revision}), "secondary-button");
    button.type = "button";
    button.addEventListener("click", async () => {
      try {
        const {value: revision} = await request(path() + "/revisions/" + item.revision);
        if (selection !== state.scope + "/" + state.key) return;
        state.historical = revision;
        $("prompt-history-title").textContent = t("revision", {revision: revision.revision});
        $("prompt-history-content").textContent = JSON.stringify(revision.content, null, 2);
        $("prompt-history-detail").hidden = false;
        renderState();
      } catch (error) { report(error); }
    });
    $("prompt-revisions").append(button);
  }
  state.cursor = value.next_cursor;
  $("prompt-more").hidden = !state.cursor;
}
async function loadCurrent() {
  const ticket = gate.start();
  state.loaded = false;
  state.head = null;
  state.etag = null;
  state.dirty = false;
  notice("");
  state.historical = null;
  state.cursor = null;
  $("prompt-history-detail").hidden = true;
  $("prompt-revisions").replaceChildren();
  $("prompt-more").hidden = true;
  renderKeys();
  renderState();
  let result;
  try { result = await request(path()); }
  catch (error) { if (error.status !== 404) throw error; result = {value: null, etag: null}; }
  if (!ticket.isCurrent()) return;
  state.head = result.value;
  state.etag = result.etag;
  state.loaded = true;
  const content = state.head?.content || {mode: "auto", instructions: "", demonstrations: []};
  document.querySelector('input[name="mode"][value="' + content.mode + '"]').checked = true;
  $("prompt-instructions").value = content.instructions;
  state.demos = [];
  appendDemos(content.demonstrations);
  renderState();
  renderDemonstrations();
  await loadHistory();
}
function editedContent() {
  if (mode() === "auto") return {schema_version: "powercontext.prompt.v1", mode: "auto", instructions: "", demonstrations: []};
  let demonstrations;
  try {
    // Keep persisted order independent of positive/negative visual grouping.
    demonstrations = state.demos.map((item) => ({input: JSON.parse(item.input), expected_output: JSON.parse(item.output)}));
  } catch { throw new Error(t("invalidJson")); }
  return {schema_version: "powercontext.prompt.v1", mode: "custom",
    instructions: $("prompt-instructions").value, demonstrations};
}
async function busy(operation) {
  if (state.busy) return;
  state.busy = true;
  renderState(); renderKeys(); renderDemonstrations();
  try { await operation(); } catch (error) { report(error); }
  finally { state.busy = false; renderState(); renderKeys(); renderDemonstrations(); }
}
async function save(content) {
  const existing = state.head !== null;
  const url = existing ? path() : "/v1/scopes/" + encodeURIComponent(state.scope) + "/artifacts";
  const headers = {"Content-Type": "application/json"};
  if (existing) headers["If-Match"] = state.etag;
  const body = existing ? {content} : {family: "prompt", prompt_key: state.key, content};
  const {value} = await request(url, {method: existing ? "PUT" : "POST", headers, body: JSON.stringify(body)});
  state.heads.set(state.key, value);
  await loadCurrent();
  notice("saved", false, {revision: value.revision});
}
async function initialize() {
  if (document.documentElement.dataset.serverAuthRequired === "true" && !readServerToken()) return;
  const ticket = gate.start();
  const [scopes, capabilities] = await Promise.all([request("/dashboard/scopes"), request("/v1/capabilities")]);
  if (!ticket.isCurrent()) return;
  state.capabilities = capabilities.value.prompts || {};
  $("sign-out").hidden = document.documentElement.dataset.serverAuthRequired !== "true";
  $("prompt-scope").replaceChildren(...scopes.value.map((scope) => {
    const option = make("option", scope.display_name + " · " + scope.scope_id);
    option.value = scope.scope_id;
    return option;
  }));
  state.scope = scopes.value[0]?.scope_id || "";
  if (!state.scope) { notice("noScopes"); renderKeys(); return; }
  await loadScope();
}
async function loadScope() {
  const ticket = scopeGate.start();
  const selection = state.scope;
  const {value} = await request("/v1/scopes/" + encodeURIComponent(selection) + "/artifacts/prompt?limit=100");
  if (!ticket.isCurrent() || selection !== state.scope) return;
  state.heads = new Map(value.items.map((item) => [item.artifact_id, item]));
  await loadCurrent();
}
$("prompt-form").addEventListener("input", () => { state.dirty = true; renderState(); });
$("prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!state.loaded) return;
  void busy(async () => save(editedContent()));
});
$("prompt-reload").addEventListener("click", () => { if (discard()) void busy(loadCurrent); });
$("prompt-scope").addEventListener("change", () => {
  if (!discard()) { $("prompt-scope").value = state.scope; return; }
  state.scope = $("prompt-scope").value;
  state.loaded = false;
  state.heads.clear();
  renderKeys();
  renderState();
  renderDemonstrations();
  gate.cancel();
  void loadScope().catch(report);
});
$("prompt-add").addEventListener("click", () => {
  if (state.demos.length >= 50) return;
  appendDemos([{input: {}, expected_output: {}}]);
  state.dirty = true; renderDemonstrations(); renderState();
});
$("prompt-generate").addEventListener("click", () => void busy(async () => {
  const count = Number($("prompt-count").value);
  if (!Number.isInteger(count) || count < 1 || count > 20) throw new Error("demonstration_count");
  if (state.demos.length + count > 50) throw new Error(t("limit"));
  const {value} = await request("/v1/scopes/" + encodeURIComponent(state.scope) + "/prompts/" + state.key + "/demonstrations", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({instructions: $("prompt-instructions").value, demonstration_count: count})
  });
  appendDemos(value.demonstrations);
  state.dirty = true;
  notice("generated");
}));
$("prompt-more").addEventListener("click", () => void busy(loadHistory));
$("prompt-restore").addEventListener("click", () => {
  if (!state.historical || !window.confirm(t("restoreConfirm", {revision: state.historical.revision}))) return;
  void busy(async () => save(state.historical.content));
});
$("auth-form").addEventListener("submit", (event) => {
  event.preventDefault(); storeServerToken($("token").value); $("token").value = "";
  void initialize().catch(report);
});
$("sign-out").addEventListener("click", () => {
  if (state.busy) return;
  if (!discard()) return;
  gate.cancel(); scopeGate.cancel(); clearServerToken(); state.loaded = false; state.demos = []; state.heads.clear();
  state.scope = ""; state.head = null; state.historical = null; state.dirty = false;
  $("prompt-instructions").value = ""; $("prompt-history-content").textContent = "";
  renderState(); renderDemonstrations(); renderKeys();
});
window.addEventListener("beforeunload", (event) => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ""; }
});
ui.initialize();
void initialize().catch(report);
