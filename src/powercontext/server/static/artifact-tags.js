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

import {fetchWithBearer} from "./auth.js?v=optional-auth";
import {createRequestGate} from "./page-ui.js?v=locale-complete";

export const tagTranslations = {
  en: {
    tagTitle: "Custom tags", tagIntro: "Choose one Scope and a logical target. Tags do not change content versions.",
    tagScope: "Scope", tagTargetType: "Target", tagArtifact: "Artifact", tagEntry: "Memory entry",
    tagLabels: "Labels, one per line (up to 32)", tagSave: "Save tags", tagReload: "Reload tags",
    tagClearHint: "Save an empty field to clear all labels for this target.",
    tagQuery: "Find by exact labels, one per line (up to 16)", tagMatch: "Match", tagAll: "All", tagAny: "Any",
    tagInactive: "Include inactive", tagSearch: "Find targets", tagMore: "Load more", tagSaved: "Tags saved.",
    tagConflict: "Tags changed elsewhere. Your text is preserved. Reload the current tags before saving again.",
    tagFailure: "The request failed. Check the selected target, label format, and server connection.",
    tagNoTargets: "No matching targets.", tagChoose: "Choose a target", tagLoaded: "Current tags loaded.",
  },
  zh: {
    tagTitle: "自定义标签", tagIntro: "选择一个 Scope 和逻辑制品或条目。标签不会修改内容版本。",
    tagScope: "Scope", tagTargetType: "标签对象", tagArtifact: "制品", tagEntry: "记忆条目",
    tagLabels: "标签，每行一个（最多 32 个）", tagSave: "保存标签", tagReload: "重新读取标签",
    tagClearHint: "清空输入框并保存，即可清除当前对象的全部标签。",
    tagQuery: "按精确标签查找，每行一个（最多 16 个）", tagMatch: "匹配方式", tagAll: "全部匹配", tagAny: "任一匹配",
    tagInactive: "包含非活跃对象", tagSearch: "查找对象", tagMore: "加载更多", tagSaved: "标签已保存。",
    tagConflict: "标签已被其他操作修改。输入内容已保留，请重新读取当前标签后再保存。",
    tagFailure: "请求失败，请检查所选对象、标签格式和服务器连接。",
    tagNoTargets: "没有匹配的对象。", tagChoose: "请选择对象", tagLoaded: "已读取当前标签。",
  },
};

export function createTagPanel(root, {translate, token}) {
  const el = (id) => root.querySelector(`#tag-${id}`);
  const editorRequests = createRequestGate();
  const queryRequests = createRequestGate();
  let etag = null;
  let targetPath = null;
  let artifactCursor = null;
  let queryState = null;
  let scopeSignature = null;
  let statusKey = "";
  const status = (key) => { statusKey = key; el("status").textContent = key ? translate(key) : ""; };
  const labels = (value) => value === "" ? [] : value.split(/\r?\n/);
  const base = () => `/v1/scopes/${encodeURIComponent(el("scope").value)}`;
  const artifactPath = () => `${base()}/artifacts/${el("family").value}/${encodeURIComponent(el("artifact").value)}`;
  const option = (value, label) => {
    const node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  };
  const resetEditor = () => {
    editorRequests.cancel();
    etag = null;
    targetPath = null;
    el("labels").value = "";
    el("labels").disabled = true;
    el("save").disabled = true;
  };
  async function request(path, options = {}) {
    const response = await fetchWithBearer(path, token(), options);
    if (!response.ok) {
      const error = new Error("Tag request failed");
      error.status = response.status;
      throw error;
    }
    return {body: await response.json(), etag: response.headers.get("ETag")};
  }
  async function loadTags() {
    resetEditor();
    if (!el("artifact").value || (el("target-type").value === "memory_entry" && !el("entry").value)) return;
    const path = artifactPath() + (el("target-type").value === "memory_entry" ? `/entries/${encodeURIComponent(el("entry").value)}` : "") + "/tags";
    const gate = editorRequests.start();
    try {
      const result = await request(path);
      if (!gate.isCurrent()) return;
      targetPath = path;
      etag = result.etag;
      el("labels").value = result.body.tags.join("\n");
      el("labels").disabled = false;
      el("save").disabled = !etag;
      status("tagLoaded");
    } catch { if (gate.isCurrent()) status("tagFailure"); }
  }
  async function loadEntries(selected = "") {
    resetEditor();
    const isEntry = el("target-type").value === "memory_entry";
    el("entry-field").hidden = !isEntry;
    if (!isEntry) return loadTags();
    el("entry").replaceChildren();
    if (!el("artifact").value) return;
    const gate = editorRequests.start();
    try {
      const result = await request(artifactPath());
      if (!gate.isCurrent()) return;
      for (const entry of result.body.content.manifest.entries) {
        el("entry").append(option(entry.entry_id, `${entry.entry_id} (${entry.state})`));
      }
      if (selected) el("entry").value = selected;
      await loadTags();
    } catch { if (gate.isCurrent()) status("tagFailure"); }
  }
  async function loadArtifacts(more = false) {
    resetEditor();
    const gate = editorRequests.start();
    if (!more) { artifactCursor = null; el("artifact").replaceChildren(); }
    if (!el("scope").value) return;
    const params = new URLSearchParams({limit: "100"});
    if (artifactCursor) params.set("cursor", artifactCursor);
    try {
      const result = await request(`${base()}/artifacts/${el("family").value}?${params}`);
      if (!gate.isCurrent()) return;
      for (const artifact of result.body.items) el("artifact").append(option(artifact.artifact_id, `${artifact.artifact_id} (r${artifact.revision})`));
      artifactCursor = result.body.next_cursor;
      el("more-artifacts").hidden = !artifactCursor;
      await loadEntries();
    } catch { if (gate.isCurrent()) status("tagFailure"); }
  }
  async function findTargets(more = false) {
    const gate = queryRequests.start();
    if (!more) {
      queryState = {tags: labels(el("query").value), match: el("match").value, include_inactive: el("inactive").checked, limit: 50};
      el("results").replaceChildren();
    }
    el("next").hidden = true;
    try {
      const result = await request(`${base()}/artifact-tags/query`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(queryState)});
      if (!gate.isCurrent()) return;
      for (const item of result.body.items) {
        const li = document.createElement("li");
        const button = document.createElement("button");
        button.className = "secondary-button";
        button.type = "button";
        button.textContent = `${item.target.family} / ${item.target.artifact_id}${item.target.entry_id ? " / " + item.target.entry_id : ""} — ${item.tags.join(", ")}`;
        button.addEventListener("click", async () => {
          resetEditor();
          el("family").value = item.target.family;
          el("target-type").value = item.target.type;
          el("family").disabled = item.target.type === "memory_entry";
          el("artifact").replaceChildren(option(item.target.artifact_id, item.target.artifact_id));
          el("more-artifacts").hidden = true;
          await loadEntries(item.target.entry_id);
        });
        li.append(button);
        el("results").append(li);
      }
      queryState.cursor = result.body.next_cursor;
      el("next").hidden = !queryState.cursor;
      status(el("results").children.length ? "" : "tagNoTargets");
    } catch { if (gate.isCurrent()) status("tagFailure"); }
  }
  el("scope").addEventListener("change", () => {
    queryRequests.cancel(); queryState = null;
    el("results").replaceChildren(); el("next").hidden = true;
    loadArtifacts();
  });
  el("family").addEventListener("change", () => loadArtifacts());
  el("target-type").addEventListener("change", () => {
    const isEntry = el("target-type").value === "memory_entry";
    if (isEntry) el("family").value = "memory";
    el("family").disabled = isEntry;
    loadArtifacts();
  });
  el("artifact").addEventListener("change", () => loadEntries());
  el("entry").addEventListener("change", loadTags);
  el("reload").addEventListener("click", loadTags);
  el("more-artifacts").addEventListener("click", () => loadArtifacts(true));
  el("search").addEventListener("click", () => findTargets());
  el("next").addEventListener("click", () => findTargets(true));
  el("save").addEventListener("click", async () => {
    if (!etag || !targetPath) return;
    const gate = editorRequests.start();
    el("save").disabled = true;
    el("labels").disabled = true;
    try {
      const result = await request(targetPath, {method: "PUT", headers: {"Content-Type": "application/json", "If-Match": etag}, body: JSON.stringify({tags: labels(el("labels").value)})});
      if (!gate.isCurrent()) return;
      etag = result.etag;
      el("labels").value = result.body.tags.join("\n");
      status("tagSaved");
    } catch (error) {
      if (!gate.isCurrent()) return;
      if (error.status === 412) etag = null;
      status(error.status === 412 ? "tagConflict" : "tagFailure");
    } finally {
      if (gate.isCurrent()) {
        el("save").disabled = !etag;
        el("labels").disabled = false;
      }
    }
  });
  return {
    updateScopes(scopes) {
      status(statusKey);
      const signature = JSON.stringify(scopes.map((scope) => [scope.scope_id, scope.display_name]));
      if (signature === scopeSignature) return;
      scopeSignature = signature;
      queryRequests.cancel(); queryState = null;
      el("results").replaceChildren(); el("next").hidden = true;
      const selected = el("scope").value;
      el("scope").replaceChildren(...scopes.map((scope) => option(scope.scope_id, scope.display_name)));
      if (scopes.some((scope) => scope.scope_id === selected)) el("scope").value = selected;
      loadArtifacts();
    },
    reset() {
      resetEditor(); queryRequests.cancel(); scopeSignature = null; queryState = null;
      el("results").replaceChildren(); el("scope").replaceChildren(); status("");
    },
  };
}
