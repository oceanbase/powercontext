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

export const memoryMessages = {
  zh: {
    quick: "快速记录",
    add: "记一条",
    note: "记忆内容",
    save: "保存记忆",
    saving: "正在提交…",
    target: "本次保存目标",
    noScope: "请先启用连接并选择精确范围。",
    budget: "UTF-8 字节，最多 8192；不会截断正文。",
    hint: "保存为纯文本笔记。Enter 用于换行，点击按钮才会提交。",
    empty: "请输入非空正文。",
    success: "保存成功。",
    noEntry:
      "保存操作成功，Server 未返回可打开的条目；不据此认定创建了新记录。",
    unknown:
      "提交结果未知。请不要直接重复保存；可以回到原连接和范围查询，但相同文字不能证明本次提交成功。",
    retry: "上次提交结果未知，再次保存可能产生重复记录。仍要提交这次输入吗？",
    failed: "保存未完成，输入已保留。",
    original: "上次提交目标",
    pending: "原目标的提交仍在进行。",
    changed: "连接或范围已改变，旧正文已隐藏。",
    search: "查找",
    query: "全文搜索关键词",
    searchHint:
      "仅在当前范围全文搜索，每次最多返回 10 条；不是完整目录或历史。",
    searching: "正在查找…",
    none: "没有匹配的记忆。",
    start: "输入关键词查找已有记忆。",
    limit: "已返回本次上限 10 条，可细化关键词；这不是总数。",
    read: "阅读精确版本",
    loading: "正在读取精确版本…",
    detail: "记忆正文",
    sources: "来源引用",
    noSources: "Server 未返回来源引用。",
    reference: "精确引用",
    copyText: "复制正文",
    copyReference: "复制引用",
    copied: "已复制",
    copyFailed: "复制失败，请选择文本手动复制。",
    close: "关闭阅读",
    bytesError: "正文超过 8192 UTF-8 字节，请手动缩短后再保存。",
    inputError: "请输入非空内容，且不超过 8192 UTF-8 字节。",
  },
  en: {
    quick: "Quick note",
    add: "Add a note",
    note: "Memory text",
    save: "Save memory",
    saving: "Submitting…",
    target: "Save target",
    noScope: "Activate a connection and select an exact scope first.",
    budget: "UTF-8 bytes, maximum 8192; text is never truncated.",
    hint: "Save as a plain-text note. Enter adds a line; only the button submits.",
    empty: "Enter nonblank text.",
    success: "Saved successfully.",
    noEntry:
      "The save succeeded, but the Server returned no entry to open. This does not establish that a new record was created.",
    unknown:
      "The submission outcome is unknown. Avoid saving again immediately. You can inspect the original connection and scope, but matching text does not prove this submission succeeded.",
    retry:
      "The last submission has an unknown outcome. Saving again may create a duplicate. Submit this input anyway?",
    failed: "Saving did not complete; your input is preserved.",
    original: "Previous submission target",
    pending: "Submission to the original target is still in progress.",
    changed: "The connection or scope changed; previous text is hidden.",
    search: "Find",
    query: "Full-text search keywords",
    searchHint:
      "Search this scope by full text, up to 10 matches per request. This is not a complete directory or history.",
    searching: "Searching…",
    none: "No matching memories.",
    start: "Enter keywords to find existing memories.",
    limit:
      "This request returned its limit of 10 matches. Refine your keywords; this is not a total count.",
    read: "Read exact version",
    loading: "Reading exact version…",
    detail: "Memory text",
    sources: "Source references",
    noSources: "The Server returned no source references.",
    reference: "Exact reference",
    copyText: "Copy text",
    copyReference: "Copy reference",
    copied: "Copied",
    copyFailed: "Copy failed. Select and copy the text manually.",
    close: "Close reader",
    bytesError:
      "Text exceeds 8192 UTF-8 bytes. Shorten it manually before saving.",
    inputError: "Enter nonblank text within 8192 UTF-8 bytes.",
  },
};
