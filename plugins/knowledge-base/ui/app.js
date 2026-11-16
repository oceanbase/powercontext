/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

/* Knowledge-base pages use only the authenticated, restricted Dashboard bridge. */
"use strict";
const client = window.powercontextPluginClient();
const $ = (id) => document.getElementById(id);
let state = {
  space: null,
  default: null,
  kbs: [],
  selected: new Set(),
  kb: null,
  document: null,
  chunk: null,
  offset: 0,
  cursor: null,
};
let viewVersion = 0;
let documentLoad = 0;
let chunkLoad = 0;
let searchLoad = 0;
const key = () => crypto.randomUUID();
const show = (message, error = false) => {
  $("notice").textContent = message;
  $("notice").classList.toggle("error", error);
};
async function api(method, path, options = {}) {
  return responseBody(await client.request(method, path, options));
}
function responseBody(r) {
  if (!r.ok)
    throw new Error(
      `${r.status}: ${r.json?.error?.message || JSON.stringify(r.json) || "请求失败"}`,
    );
  return r.json;
}
// Requests may finish after navigation. Ignore their results and errors in the new view.
async function forView(request, current) {
  try {
    const result = await request;
    return current() ? result : null;
  } catch (error) {
    if (current()) throw error;
    return null;
  }
}
function artifactTarget() {
  return { scopeId: state.kb.scope_id, id: state.document.ref.artifact_id, revision: state.document.ref.revision };
}
async function artifactApi(method, target, suffix = "", options = {}) {
  return responseBody(await client.artifacts(method, `/${target.id}${suffix}`, { ...options, scopeId: target.scopeId }));
}
function button(text, action) {
  const b = document.createElement("button");
  b.textContent = text;
  b.onclick = () => run(action, b);
  return b;
}
async function run(action, element) {
  if (element) element.disabled = true;
  try {
    await action();
  } catch (e) {
    show(e.message, true);
  } finally {
    if (element) element.disabled = false;
  }
}
function ask(message, initial = null) {
  const dialog = $("question");
  if (dialog.open) throw new Error("请先完成当前操作");
  $("question-label").textContent = message;
  $("question-input").hidden = initial === null;
  $("question-input").value = initial ?? "";
  dialog.returnValue = "cancel";
  return new Promise((resolve) => {
    dialog.addEventListener(
      "close",
      () => {
        resolve(
          dialog.returnValue === "confirm"
            ? initial === null
              ? true
              : $("question-input").value
            : null,
        );
      },
      { once: true },
    );
    dialog.showModal();
  });
}
function scopePath() {
  if (!state.kb) throw new Error("请先选择知识库");
  return `/scopes/${state.kb.scope_id}`;
}
function chunksPath() {
  return `${scopePath()}/documents/${state.document.ref.artifact_id}/revisions/${$("revision").value}/chunks`;
}
async function refresh() {
  const version = ++viewVersion;
  state.document = null;
  state.kbs = [];
  state.selected.clear();
  $("libraries").textContent = "正在加载知识库…";
  $("library").hidden = true;
  $("new-kb").disabled = true;
  $("set-default").disabled = true;
  $("document").hidden = true;
  $("editor").close();
  $("hits").replaceChildren();
  const current = () => version === viewVersion;
  const selected = await forView(api("GET", "/me/default-space"), current);
  if (!selected) return;
  const data = await forView(api("GET", "/spaces"), current);
  if (!data) return;
  const spaces = data.items;
  state.default = selected;
  const preferred = state.space || selected.space_scope_id;
  state.space = spaces.some(s => s.space_scope_id === preferred) ? preferred : null;
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择可访问的空间";
  placeholder.disabled = true;
  $("spaces").replaceChildren(placeholder, ...spaces.map(s => {
    const option = document.createElement("option");
    option.value = s.space_scope_id;
    option.textContent = s.title;
    return option;
  }));
  $("spaces").value = state.space || "";
  $("set-default").disabled = !state.space;
  $("new-kb").disabled = !state.space;
  $("default-label").textContent = state.space === selected.space_scope_id ? "当前默认空间" : "";
  if (!state.space) {
    state.kb = null;
    state.document = null;
    state.kbs = [];
    state.selected.clear();
    $("library").hidden = true;
    $("document").hidden = true;
    $("hits").replaceChildren();
    renderLibraries();
    show("默认空间不可访问，请选择其他空间并设置默认空间。", true);
    return;
  }
  await libraries();
}
async function libraries() {
  const version = viewVersion;
  const space = state.space;
  const data = await forView(api("GET", "/knowledge-bases", {
    query: { space_scope_id: space },
  }), () => version === viewVersion && space === state.space);
  if (!data) return;
  state.kbs = data.items;
  state.selected = new Set(state.kbs.map(k => k.scope_id));
  renderLibraries();
  if (state.kb && state.kbs.some(k => k.scope_id === state.kb.scope_id)) await documents();
  else {
    $("library").hidden = true;
    $("document").hidden = true;
    state.kb = null;
  }
}
function renderLibraries() {
  const space = state.space;
  const version = viewVersion;
  const current = () => space === state.space && version === viewVersion;
  $("libraries").replaceChildren(
    ...state.kbs.map((k) => {
      const row = document.createElement("div");
      row.className =
        "library-row" + (state.kb?.scope_id === k.scope_id ? " active" : "");
      const c = document.createElement("input");
      c.type = "checkbox";
      c.checked = state.selected.has(k.scope_id);
      c.setAttribute("aria-label", "检索 " + k.value.title);
      c.onchange = () => {
        if (!current()) return;
        searchLoad += 1;
        $("hits").replaceChildren();
        if (c.checked) state.selected.add(k.scope_id);
        else state.selected.delete(k.scope_id);
      };
      row.append(
        c,
        button(k.value.title, async () => {
          if (!current()) return;
          viewVersion += 1;
          state.kb = k;
          state.document = null;
          $("library").hidden = true;
          $("editor").close();
          show("");
          state.cursor = null;
          $("document").hidden = true;
          renderLibraries();
          await documents();
        }),
      );
      return row;
    }),
  );
  if (!state.kbs.length) $("libraries").textContent = "此空间尚无可见知识库。";
}
async function documents(nextPage = false) {
  if (!state.kb) return;
  const kb = state.kb;
  const base = scopePath();
  const version = viewVersion;
  const load = ++documentLoad;
  const current = () => version === viewVersion && load === documentLoad && state.kb?.scope_id === kb.scope_id;
  const data = await forView(api("GET", base + "/documents", {
    query: nextPage && state.cursor ? { cursor: state.cursor } : {},
  }), current);
  if (!data) return;
  $("library").hidden = false;
  $("library-title").textContent = kb.value.title;
  $("documents").replaceChildren(
    ...data.items.map((doc) => {
      const row = document.createElement("div");
      row.className = "row";
      const label = document.createElement("span");
      label.textContent = `${doc.content.relative_path} · v${doc.ref.revision} · ${doc.content.chunk_count} chunks · ${doc.lifecycle_state}`;
      row.append(
        label,
        button("浏览", () => openDocument(doc)),
      );
      return row;
    }),
  );
  if (!data.items.length)
    $("documents").textContent = "暂无文档，上传或导入文件后等待后台解析。";
  state.cursor = data.next_cursor;
  $("more-documents").hidden = !state.cursor;
  $("requests").replaceChildren();
  for (const request of data.requests.slice(0, 20)) {
    const result = await forView(api("GET", `${base}/imports/${request.request_id}`), current);
    if (!result) return;
    const line = document.createElement("p");
    line.className = "muted";
    line.textContent = `${request.document_id.slice(0, 10)} · ${result.state}${result.error ? " · " + result.error : ""}${result.current_revision ? " · 可用版本 " + result.current_revision : ""}`;
    $("requests").append(line);
    if (result.state === "failed")
      $("requests").append(
        button("重新提交解析", async () => {
          if (!current()) return;
          await api(
            "POST",
            `${base}/imports/${request.request_id}/retry`,
            { json: { idempotency_key: key() } },
          );
          if (!current()) return;
          show("已创建新的处理请求");
          await documents();
        }),
      );
  }
}
async function openDocument(doc) {
  chunkLoad += 1;
  state.document = doc;
  $("document").hidden = false;
  $("document-title").textContent = doc.content.title;
  $("revision").value = doc.ref.revision;
  $("document-info").textContent =
    `${doc.content.relative_path} · 当前版本 ${doc.ref.revision} · ${doc.lifecycle_state} · 原文与历史版本保留`;
  $("forget").disabled = doc.lifecycle_state !== "active";
  state.offset = 0;
  $("chunks").replaceChildren();
  await chunks();
}
async function chunks() {
  const path = chunksPath();
  const version = viewVersion;
  const documentId = state.document.ref.artifact_id;
  const revision = $("revision").value;
  const sameDocument = () => version === viewVersion && state.document?.ref.artifact_id === documentId && $("revision").value === revision;
  const load = ++chunkLoad;
  const current = () => sameDocument() && load === chunkLoad;
  const data = await forView(api("GET", path, {
    query: { offset: state.offset },
  }), current);
  if (!data) return;
  if (!data.total) $("chunks").textContent = "此版本没有分块。";
  for (const item of data.items) {
    const box = document.createElement("div");
    box.className = "chunk";
    const label = document.createElement("small");
    label.textContent = `#${item.ordinal + 1} · ${item.heading_path.join(" / ")} · ${item.length} 字符${item.manually_modified ? " · 已人工编辑" : ""}`;
    const body = document.createElement("pre");
    body.id = `chunk-body-${item.chunk_id}`;
    body.hidden = true;
    let loaded = false;
    const toggle = button("展开正文", async () => {
      if (!sameDocument()) return;
      if (!loaded) {
        const full = await forView(api("GET", path + "/" + item.chunk_id), sameDocument);
        if (!full) return;
        body.textContent = full.text;
        loaded = true;
      }
      body.hidden = !body.hidden;
      toggle.textContent = body.hidden ? "展开正文" : "收起正文";
      toggle.setAttribute("aria-expanded", String(!body.hidden));
    });
    toggle.setAttribute("aria-controls", body.id);
    toggle.setAttribute("aria-expanded", "false");
    box.append(
      label,
      toggle,
      button("编辑", async () => {
        const full = await forView(api("GET", path + "/" + item.chunk_id), sameDocument);
        if (!full) return;
        state.chunk = full;
        $("chunk-text").value = full.text;
        $("editor").showModal();
      }),
      button("移除分块", async () => {
        if (!(await ask("移除此分块并生成新版本？历史版本仍保留。")) || !sameDocument()) return;
        await edit({ operation: "remove_chunk", chunk_id: item.chunk_id });
      }),
      body,
    );
    $("chunks").append(box);
  }
  state.offset = data.next_offset;
  $("more-chunks").hidden = state.offset === null;
}
async function edit(content) {
  if (Number($("revision").value) !== state.document.ref.revision)
    throw new Error("历史版本不可直接编辑，请先刷新当前文档");
  const target = artifactTarget();
  const version = viewVersion;
  await artifactApi("PUT", target, "", {
    json: { content },
    ifMatch: `"revision:${state.document.ref.revision}"`,
  });
  if (version !== viewVersion || state.document?.ref.artifact_id !== target.id) return;
  show("已保存新版本");
  const d = await forView(artifactApi("GET", target), () => version === viewVersion && state.document?.ref.artifact_id === target.id);
  if (!d) return;
  await openDocument({
    ...state.document,
    ref: { ...state.document.ref, revision: d.revision },
    content: d.content,
  });
  await documents();
}
async function upload(files) {
  const base = scopePath();
  for (const file of files) {
    show("正在上传 " + file.name);
    await api("POST", base + "/uploads", {
      query: {
        relative_path: file.webkitRelativePath || file.name,
        idempotency_key: key(),
      },
      body: await file.arrayBuffer(),
      contentType: "application/octet-stream",
    });
  }
  show("已接受上传，后台解析中。点击刷新查看结果。");
  await documents();
}
$("refresh").onclick = () => run(refresh);
$("spaces").onchange = () =>
  run(async () => {
    state.space = $("spaces").value;
    state.kb = null;
    state.document = null;
    $("editor").close();
    $("hits").replaceChildren();
    show("");
    await refresh();
  });
$("set-default").onclick = () =>
  run(async () => {
    state.default = await api("PUT", "/me/default-space", {
      json: { space_scope_id: state.space, version: state.default.version },
    });
    await refresh();
    show("默认空间已切换");
  });
$("new-space").onclick = () =>
  run(async () => {
    const title = await ask("空间名称", "");
    if (!title) return;
    const created = await api("POST", "/spaces", {
      json: { title, idempotency_key: key() },
    });
    state.space = created.space_scope_id;
    await refresh();
  });
let pendingCreation = null;
$("new-kb").onclick = () =>
  run(async () => {
    if (!pendingCreation) {
      const title = await ask("知识库名称", "");
      if (!title) return;
      pendingCreation = {
        title,
        space_scope_id: state.space,
        idempotency_key: key(),
      };
    }
    await api("POST", "/knowledge-bases", { json: pendingCreation });
    state.space = pendingCreation.space_scope_id;
    pendingCreation = null;
    await refresh();
    show("知识库已创建");
  });
$("files").onchange = () => run(() => upload([...$("files").files]));
$("drop").ondragover = (e) => {
  e.preventDefault();
  $("drop").classList.add("drag");
};
$("drop").ondragleave = () => $("drop").classList.remove("drag");
$("drop").ondrop = (e) => {
  e.preventDefault();
  $("drop").classList.remove("drag");
  run(() => upload([...e.dataTransfer.files]));
};
$("local-import").onclick = () =>
  run(async () => {
    const root = await ask("Server 上已允许的目录绝对路径", "");
    if (!root) return;
    const paths = await ask("相对于目录的文件路径，每行一个", "");
    if (!paths) return;
    const result = await api("POST", scopePath() + "/imports", {
      json: {
        root,
        paths: paths.split("\n").filter(Boolean),
        idempotency_key: key(),
      },
    });
    show(
      JSON.stringify(
        result.items.map((i) => ({
          path: i.path,
          accepted: i.accepted,
          error: i.error,
        })),
        null,
        2,
      ),
    );
    await documents();
  });
$("settings").onclick = () =>
  run(async () => {
    const max = await ask("最大分块字符数", state.kb.value.chunking.max_chars);
    if (!max) return;
    const overlap = await ask("重叠字符数", state.kb.value.chunking.overlap);
    if (overlap === null) return;
    const kb = state.kb;
    const version = viewVersion;
    const updated = await forView(api("PATCH", scopePath() + "/settings", {
      json: {
        version: kb.version,
        chunking: { max_chars: Number(max), overlap: Number(overlap) },
      },
    }), () => version === viewVersion && state.kb?.scope_id === kb.scope_id);
    if (!updated) return;
    state.kb = updated;
    state.kbs = state.kbs.map(k => k.scope_id === updated.scope_id ? updated : k);
    renderLibraries();
    show("配置已保存。已有文档需显式重新解析。");
  });
$("search").onsubmit = (e) => {
  e.preventDefault();
  run(async () => {
    if (!state.space) throw new Error("请先选择可访问的空间");
    const version = viewVersion;
    const load = ++searchLoad;
    const libraries = state.kbs;
    const result = await forView(api("POST", "/retrieve", {
      json: {
        query: $("query").value,
        space_scope_id: state.space,
        scope_ids: [...state.selected],
        mode: $("mode").value,
        limit: 10,
      },
    }), () => version === viewVersion && load === searchLoad);
    if (!result) return;
    $("hits").replaceChildren(
      ...result.hits.map((hit) => {
        const div = document.createElement("div");
        div.className = "hit";
        const label = document.createElement("small");
        label.textContent = `${libraries.find((k) => k.scope_id === hit.scope_id)?.value.title || hit.scope_id} · ${hit.heading_path.join(" / ")} · v${hit.document.revision} · ${hit.chunk_id.slice(0, 10)}`;
        const pre = document.createElement("pre");
        pre.textContent = hit.text;
        div.append(label, pre);
        return div;
      }),
    );
    if (!result.hits.length) $("hits").textContent = "没有检索结果。";
  });
};
$("load-revision").onclick = () =>
  run(async () => {
    state.offset = 0;
    $("chunks").replaceChildren();
    await chunks();
  });
$("more-chunks").onclick = () => run(chunks);
$("more-documents").onclick = () => run(() => documents(true));
$("save-chunk").onclick = () =>
  run(async () => {
    await edit({
      operation: "edit_chunk",
      chunk_id: state.chunk.chunk_id,
      text: $("chunk-text").value,
    });
    $("editor").close();
  });
$("reprocess").onclick = () =>
  run(async () => {
    const replace = state.document.content.manually_modified;
    if (
      !(await ask(
        replace
          ? "重新解析将替换人工修改，是否继续？"
          : "按当前配置重新解析文档？",
      ))
    )
      return;
    await api(
      "POST",
      `${scopePath()}/documents/${state.document.ref.artifact_id}/reprocess`,
      {
        json: {
          idempotency_key: key(),
          manual_edits_policy: replace ? "replace" : "reject",
        },
      },
    );
    show("已接受重新解析");
    await documents();
  });
$("forget").onclick = () =>
  run(async () => {
    if (!(await ask("遗忘后将不再参与普通检索，原文与历史版本仍保留。继续？")))
      return;
    const target = artifactTarget();
    const version = viewVersion;
    const governance = await artifactApi("GET", target, "/governance");
    await artifactApi("POST", target, "/forget", {
      json: {
        revision: target.revision,
        state_version: governance.state_version,
      },
    });
    if (version !== viewVersion || state.document?.ref.artifact_id !== target.id) return;
    show("文档已遗忘");
    $("document").hidden = true;
    await documents();
  });
run(refresh);
