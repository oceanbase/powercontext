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
import {parseTopicDetailWithFreshness, purgeProtectedTopicDom} from "./topics-state.js?v=r7-state-v1";

const translations = {
  en: {
    pageTitle: "PowerContext Topics",
    dashboardTitle: "Overview",
    topicsTitle: "Topics",
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
    retry: "Retry",
    selectScope: "Scope",
    topicMemoryTitle: "Topic memory",
    topicsIntro: "Browse durable topics, then open an exact revision for its full detail and evidence references.",
    searchTopics: "Search",
    searchTopicsPlaceholder: "Search topic titles, summaries, and detail",
    searchTopicsAction: "Search",
    topicOrder: "Order",
    recentlyPublished: "Recently published",
    relevance: "Relevance",
    topicInventory: "Topic inventory",
    topicDetail: "Topic detail",
    selectTopic: "Select a topic to inspect its exact revision.",
    selectTopicHint: "Full detail, publication state, and direct Source references will appear here.",
    overview: "Overview",
    publishedAt: "Published",
    currentRevision: "Current revision",
    fullDetail: "Full detail",
    sourceReferences: "Source references",
    noSourceReferences: "No direct Source references.",
    noTopics: "No topics match this view.",
    noTopicsHint: "Try another focused query or choose another scope.",
    loadMore: "Load more",
    noScopes: "There are no configured Dashboard scopes.",
    authRejected: "The Server rejected this token.",
    scopesFailed: "Couldn't load Dashboard scopes. Try again.",
    topicsFailed: "Couldn't load topics. Try again.",
    detailFailed: "Couldn't load this exact topic revision.",
    serverUnavailable: "The Server is unavailable.",
    loadingTopics: "Loading topics…",
    loadingDetail: "Loading exact revision…",
    recentCount: "{count} recently published topics",
    searchCount: "{count} relevant topics · {mode}",
    sourceCount: "{count} sources",
    matchedBy: "Matched by {channels}",
    current: "Current",
    historical: "Historical",
    invalidExactRevision: "The Server returned a different topic revision."
  },
  zh: {
    pageTitle: "PowerContext 主题",
    dashboardTitle: "概览",
    topicsTitle: "主题",
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
    retry: "重试",
    selectScope: "范围",
    topicMemoryTitle: "主题记忆",
    topicsIntro: "浏览持久主题，再打开精确版本查看完整详情和证据引用。",
    searchTopics: "搜索",
    searchTopicsPlaceholder: "搜索主题标题、摘要和详情",
    searchTopicsAction: "搜索",
    topicOrder: "排序",
    recentlyPublished: "最近发布",
    relevance: "相关度",
    topicInventory: "主题列表",
    topicDetail: "主题详情",
    selectTopic: "请选择一个主题以查看其精确版本。",
    selectTopicHint: "这里将显示完整详情、发布状态和直接 Source 引用。",
    overview: "概览",
    publishedAt: "发布时间",
    currentRevision: "当前版本",
    fullDetail: "完整详情",
    sourceReferences: "Source 引用",
    noSourceReferences: "没有直接 Source 引用。",
    noTopics: "当前视图没有匹配的主题。",
    noTopicsHint: "请尝试更聚焦的查询或选择其他范围。",
    loadMore: "加载更多",
    noScopes: "尚未配置可供 Dashboard 发现的范围。",
    authRejected: "服务器拒绝了该访问令牌。",
    scopesFailed: "无法加载 Dashboard 范围，请重试。",
    topicsFailed: "无法加载主题，请重试。",
    detailFailed: "无法加载该精确主题版本。",
    serverUnavailable: "无法连接服务器。",
    loadingTopics: "正在加载主题…",
    loadingDetail: "正在加载精确版本…",
    recentCount: "最近发布的主题：{count} 个",
    searchCount: "相关主题：{count} 个 · {mode}",
    sourceCount: "{count} 个来源",
    matchedBy: "命中通道：{channels}",
    current: "当前版本",
    historical: "历史版本",
    invalidExactRevision: "服务器返回了不同的主题版本。"
  }
};

const authShell = document.getElementById("auth-shell");
const authForm = document.getElementById("auth-form");
const authError = document.getElementById("auth-error");
const tokenInput = document.getElementById("token");
const pageStatus = document.getElementById("page-status");
const pageStatusMessage = document.getElementById("page-status-message");
const pageStatusRetry = document.getElementById("page-status-retry");
const topicsLibrary = document.getElementById("topics-library");
const signOut = document.getElementById("sign-out");
const scopeSelect = document.getElementById("topics-scope-select");
const searchForm = document.getElementById("topics-search-form");
const searchInput = document.getElementById("topics-search");
const orderSelect = document.getElementById("topics-order");
const liveStatus = document.getElementById("topics-live-status");
const list = document.getElementById("topics-list");
const loading = document.getElementById("topics-loading");
const empty = document.getElementById("topics-empty");
const listError = document.getElementById("topics-list-error");
const loadMore = document.getElementById("topics-load-more");
const detailEmpty = document.getElementById("topics-detail-empty");
const detailContent = document.getElementById("topics-detail-content");
const detailError = document.getElementById("topics-detail-error");
const detailTitle = document.getElementById("topics-detail-title");
const authenticationRequired = document.documentElement.dataset.serverAuthRequired === "true";
const sessionRequests = createRequestGate();
const listRequests = createRequestGate();
const detailRequests = createRequestGate();

let scopes = [];
let currentScopeId = "";
let currentQuery = "";
let currentItems = [];
let currentDetail = null;
let selectedArtifact = null;
let nextCursor = null;
let searchMode = "";
let listErrorKey = "";
let detailErrorKey = "";
let pageStatusState = null;
let authErrorKey = "";
let activeToken = "";

const ui = createPageUi(translations, () => {
  renderAuthError();
  renderPageStatus();
  renderScopes();
  renderList();
  renderDetail();
});
const {formatDateTime, formatNumber, translate} = ui;

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authErrorKey = "";
  await authenticate(tokenInput.value);
});

signOut.addEventListener("click", () => {
  clearServerToken();
  activeToken = "";
  tokenInput.value = "";
  showLogin();
});

pageStatusRetry.addEventListener("click", async () => {
  await authenticate(activeToken || readServerToken(), currentScopeId);
});

scopeSelect.addEventListener("change", async () => {
  currentScopeId = scopeSelect.value;
  currentQuery = "";
  searchInput.value = "";
  resetTopicSelection();
  await loadTopics(false);
});

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  currentQuery = searchInput.value.trim();
  searchInput.value = currentQuery;
  resetTopicSelection();
  await loadTopics(false);
});

loadMore.addEventListener("click", async () => {
  await loadTopics(true);
});

list.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") {
    return;
  }
  const buttons = Array.from(list.querySelectorAll(".topics-list-item"));
  const index = buttons.indexOf(event.target.closest(".topics-list-item"));
  if (index < 0 || buttons.length === 0) {
    return;
  }
  event.preventDefault();
  const direction = event.key === "ArrowDown" ? 1 : -1;
  buttons[(index + direction + buttons.length) % buttons.length].focus();
});

async function authenticate(token, preferredScopeId = "") {
  if (authenticationRequired && !token) {
    showLogin();
    return;
  }
  if (authenticationRequired) {
    storeServerToken(token);
  }
  activeToken = token || "";
  tokenInput.value = "";
  authErrorKey = "";
  const request = sessionRequests.start();
  scopeSelect.disabled = true;
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
      showPageStatus("scopesFailed", true);
      return;
    }
    scopes = await response.json();
    if (!request.isCurrent()) {
      return;
    }
    if (scopes.length === 0) {
      showPageStatus("noScopes", false);
      return;
    }
    currentScopeId = scopes.some((scope) => scope.scope_id === preferredScopeId)
      ? preferredScopeId
      : scopes[0].scope_id;
    showTopicsLibrary();
    renderScopes();
    await loadTopics(false);
  } catch (error) {
    if (request.isCurrent()) {
      showPageStatus("serverUnavailable", true);
    }
  } finally {
    if (request.isCurrent()) {
      scopeSelect.disabled = false;
    }
  }
}

async function loadTopics(append) {
  if (authenticationRequired && !activeToken && !readServerToken()) {
    showLogin();
    return;
  }
  if (!currentScopeId || (append && (currentQuery || !nextCursor))) {
    return;
  }
  const request = listRequests.start();
  const token = activeToken || readServerToken();
  const query = currentQuery;
  const scopeId = currentScopeId;
  listErrorKey = "";
  loading.hidden = append;
  loadMore.disabled = true;
  liveStatus.textContent = translate("loadingTopics");
  renderListError();
  if (!append) {
    currentItems = [];
    nextCursor = null;
    searchMode = "";
    renderList();
  }
  try {
    const payload = {scope_id: scopeId, limit: query ? 20 : 25};
    let path = "/dashboard/topic-memories/list";
    if (query) {
      path = "/v1/topic-memory/search";
      payload.query = query;
    } else if (append && nextCursor) {
      payload.cursor = nextCursor;
    }
    const response = await fetchWithBearer(path, token, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    if (!request.isCurrent() || scopeId !== currentScopeId || query !== currentQuery) {
      return;
    }
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      listErrorKey = "topicsFailed";
      renderListError();
      return;
    }
    const result = await response.json();
    if (!request.isCurrent()) {
      return;
    }
    const items = query ? result.hits : result.items;
    currentItems = append ? [...currentItems, ...items] : items;
    nextCursor = query ? null : result.next_cursor;
    searchMode = query ? result.mode : "";
    renderList();
  } catch (error) {
    if (request.isCurrent()) {
      listErrorKey = "serverUnavailable";
      renderListError();
    }
  } finally {
    if (request.isCurrent()) {
      loading.hidden = true;
      loadMore.disabled = false;
      liveStatus.textContent = "";
      renderList();
    }
  }
}

async function selectTopic(item) {
  selectedArtifact = item.artifact;
  currentDetail = null;
  detailErrorKey = "";
  renderList();
  renderDetailLoading(item);
  const request = detailRequests.start();
  const scopeId = currentScopeId;
  try {
    const response = await fetchWithBearer("/dashboard/topic-memories/get", activeToken || readServerToken(), {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scope_id: scopeId, artifact: item.artifact})
    });
    if (!request.isCurrent() || scopeId !== currentScopeId || !sameArtifact(selectedArtifact, item.artifact)) {
      return;
    }
    if (response.status === 401) {
      clearServerToken();
      showLogin("authRejected");
      return;
    }
    if (!response.ok) {
      detailErrorKey = "detailFailed";
      renderDetail();
      return;
    }
    const parsed = await parseTopicDetailWithFreshness(
      response,
      () => request.isCurrent()
        && scopeId === currentScopeId
        && sameArtifact(selectedArtifact, item.artifact)
    );
    if (!parsed.fresh) {
      return;
    }
    const detail = parsed.detail;
    if (!sameArtifact(detail.artifact, item.artifact)) {
      detailErrorKey = "invalidExactRevision";
      renderDetail();
      return;
    }
    currentDetail = detail;
    renderDetail();
    detailTitle.focus();
  } catch (error) {
    if (request.isCurrent()) {
      detailErrorKey = "serverUnavailable";
      renderDetail();
    }
  }
}

function showLogin(messageKey = "") {
  sessionRequests.cancel();
  listRequests.cancel();
  detailRequests.cancel();
  scopes = [];
  currentScopeId = "";
  currentQuery = "";
  activeToken = "";
  authErrorKey = messageKey;
  pageStatusState = null;
  resetTopicSelection();
  purgeProtectedTopicDom({
    scopeSelect,
    searchInput,
    list,
    detailError,
    detailTitle,
    detailRef: document.getElementById("topics-detail-ref"),
    detailCurrent: document.getElementById("topics-detail-current"),
    detailSummary: document.getElementById("topics-detail-summary"),
    detailPublished: document.getElementById("topics-detail-published"),
    detailHead: document.getElementById("topics-detail-head"),
    detailBody: document.getElementById("topics-detail-body"),
    sourceList: document.getElementById("topics-source-refs"),
    noSources: document.getElementById("topics-no-sources")
  });
  renderAuthError();
  authShell.hidden = false;
  pageStatus.hidden = true;
  topicsLibrary.hidden = true;
  signOut.hidden = true;
  tokenInput.focus();
}

function showPageStatus(messageKey, retryable) {
  pageStatusState = {messageKey, retryable};
  renderPageStatus();
  authShell.hidden = true;
  pageStatus.hidden = false;
  topicsLibrary.hidden = true;
  signOut.hidden = !authenticationRequired;
}

function showTopicsLibrary() {
  pageStatusState = null;
  authShell.hidden = true;
  pageStatus.hidden = true;
  topicsLibrary.hidden = false;
  signOut.hidden = !authenticationRequired;
}

function renderAuthError() {
  authError.textContent = authErrorKey ? translate(authErrorKey) : "";
}

function renderPageStatus() {
  pageStatusMessage.textContent = pageStatusState ? translate(pageStatusState.messageKey) : "";
  pageStatusRetry.hidden = !pageStatusState?.retryable;
}

function renderScopes() {
  scopeSelect.replaceChildren();
  for (const scope of scopes) {
    const option = document.createElement("option");
    option.value = scope.scope_id;
    option.textContent = scope.display_name;
    option.selected = scope.scope_id === currentScopeId;
    scopeSelect.appendChild(option);
  }
}

function renderList() {
  orderSelect.value = currentQuery ? "relevance" : "recent";
  list.replaceChildren();
  for (const item of currentItems) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "topics-list-item";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(sameArtifact(selectedArtifact, item.artifact)));
    button.addEventListener("click", () => selectTopic(item));

    const heading = document.createElement("span");
    heading.className = "topics-list-heading";
    const title = document.createElement("strong");
    title.textContent = item.title;
    const context = document.createElement("span");
    context.className = "topics-list-time";
    context.textContent = item.published_at
      ? formatDateTime(item.published_at)
      : translate("matchedBy", {channels: item.matched_by.join(", ")});
    heading.append(title, context);

    const summary = document.createElement("p");
    summary.className = "topics-list-summary";
    summary.textContent = item.summary;
    button.append(heading, summary);
    if (item.snippet) {
      const snippet = document.createElement("p");
      snippet.className = "topics-list-snippet";
      snippet.textContent = item.snippet;
      button.appendChild(snippet);
    }
    const reference = document.createElement("code");
    reference.textContent = formatArtifact(item.artifact);
    if (Number.isInteger(item.source_count)) {
      reference.textContent += ` · ${translate("sourceCount", {count: formatNumber(item.source_count)})}`;
    }
    button.appendChild(reference);
    list.appendChild(button);
  }
  empty.hidden = currentItems.length !== 0 || !loading.hidden || Boolean(listErrorKey);
  loadMore.hidden = Boolean(currentQuery) || !nextCursor || Boolean(listErrorKey);
  const captionKey = currentQuery ? "searchCount" : "recentCount";
  const values = currentQuery
    ? {count: formatNumber(currentItems.length), mode: searchMode || "—"}
    : {count: formatNumber(currentItems.length)};
  document.getElementById("topics-index-caption").textContent = translate(captionKey, values);
  renderListError();
}

function renderListError() {
  listError.hidden = !listErrorKey;
  listError.textContent = listErrorKey ? translate(listErrorKey) : "";
}

function renderDetailLoading(item) {
  detailEmpty.hidden = true;
  detailContent.hidden = false;
  detailError.hidden = true;
  detailError.textContent = "";
  detailTitle.textContent = item.title;
  document.getElementById("topics-detail-ref").textContent = formatArtifact(item.artifact);
  document.getElementById("topics-detail-current").textContent = translate("loadingDetail");
  document.getElementById("topics-detail-summary").textContent = "";
  document.getElementById("topics-detail-published").textContent = "";
  document.getElementById("topics-detail-head").textContent = "";
  document.getElementById("topics-detail-body").textContent = "";
  document.getElementById("topics-source-refs").replaceChildren();
  document.getElementById("topics-no-sources").hidden = true;
}

function renderDetail() {
  if (!selectedArtifact) {
    detailEmpty.hidden = false;
    detailContent.hidden = true;
    return;
  }
  detailEmpty.hidden = true;
  detailContent.hidden = false;
  detailError.hidden = !detailErrorKey;
  detailError.textContent = detailErrorKey ? translate(detailErrorKey) : "";
  if (!currentDetail) {
    return;
  }
  detailTitle.textContent = currentDetail.title;
  document.getElementById("topics-detail-ref").textContent = formatArtifact(currentDetail.artifact);
  const currentBadge = document.getElementById("topics-detail-current");
  currentBadge.textContent = translate(currentDetail.is_current ? "current" : "historical");
  currentBadge.dataset.state = currentDetail.is_current ? "current" : "historical";
  document.getElementById("topics-detail-summary").textContent = currentDetail.summary;
  document.getElementById("topics-detail-published").textContent = formatDateTime(currentDetail.published_at);
  document.getElementById("topics-detail-head").textContent = formatArtifact(currentDetail.current_artifact);
  document.getElementById("topics-detail-body").textContent = currentDetail.detail;
  const sourceList = document.getElementById("topics-source-refs");
  sourceList.replaceChildren();
  for (const source of currentDetail.source_refs) {
    const item = document.createElement("li");
    item.textContent = `${source.source_type}:${source.source_id}`;
    sourceList.appendChild(item);
  }
  document.getElementById("topics-no-sources").hidden = currentDetail.source_refs.length !== 0;
}

function resetTopicSelection() {
  listRequests.cancel();
  detailRequests.cancel();
  currentItems = [];
  currentDetail = null;
  selectedArtifact = null;
  nextCursor = null;
  searchMode = "";
  listErrorKey = "";
  detailErrorKey = "";
  renderList();
  renderDetail();
}

function sameArtifact(left, right) {
  return Boolean(left && right)
    && left.family === right.family
    && left.artifact_id === right.artifact_id
    && left.revision === right.revision;
}

function formatArtifact(artifact) {
  return `${artifact.family}:${artifact.artifact_id}@${artifact.revision}`;
}

ui.initialize();
await authenticate(readServerToken());
