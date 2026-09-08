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

import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const modulePath = new URL("../src/powercontext/server/static/topics-state.js", import.meta.url);
const moduleSource = await readFile(modulePath, "utf8");
const stateModule = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`);
const {parseTopicDetailWithFreshness, purgeProtectedTopicDom} = stateModule;
const topicsPagePath = new URL("../src/powercontext/server/static/topics.js", import.meta.url);

class FakeElement {
  constructor() {
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.disabled = false;
    this.focused = false;
    this.hidden = false;
    this.listeners = new Map();
    this.textContent = "";
    this.type = "";
    this.value = "";
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  emit(name, event = {}) {
    return this.listeners.get(name)?.({preventDefault() {}, ...event});
  }

  focus() {
    this.focused = true;
  }

  querySelectorAll() {
    return [];
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "data-state") {
      delete this.dataset.state;
    }
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function createTopicsEnvironment({authenticationRequired}) {
  const elements = new Map();
  const documentElement = new FakeElement();
  documentElement.dataset.serverAuthRequired = String(authenticationRequired);
  documentElement.dataset.theme = "light";
  documentElement.lang = "en";
  const document = {
    documentElement,
    title: "",
    createElement() {
      return new FakeElement();
    },
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, new FakeElement());
      }
      return elements.get(id);
    },
    querySelectorAll() {
      return [];
    }
  };
  const storage = new Map();
  const sessionStorage = {
    getItem(key) {
      return storage.get(key) ?? null;
    },
    removeItem(key) {
      storage.delete(key);
    },
    setItem(key, value) {
      storage.set(key, value);
    }
  };

  globalThis.document = document;
  globalThis.localStorage = sessionStorage;
  globalThis.sessionStorage = sessionStorage;
  globalThis.window = {matchMedia: () => ({matches: false})};
  return {documentElement, elements};
}

async function loadTopicsPage(label) {
  await import(`${topicsPagePath.href}?test=${label}`);
}

function sensitiveElement({textContent = "protected", value = "protected", children = ["protected"]} = {}) {
  return {
    children,
    dataset: {state: "current"},
    hidden: false,
    textContent,
    value,
    removeAttribute(name) {
      if (name === "data-state") {
        delete this.dataset.state;
      }
    },
    replaceChildren(...nextChildren) {
      this.children = nextChildren;
    }
  };
}

test("sign-out purges protected scope, query, topic detail, and Source data", () => {
  const elements = {
    scopeSelect: sensitiveElement(),
    searchInput: sensitiveElement(),
    list: sensitiveElement(),
    detailError: sensitiveElement(),
    detailTitle: sensitiveElement(),
    detailRef: sensitiveElement(),
    detailCurrent: sensitiveElement(),
    detailSummary: sensitiveElement(),
    detailPublished: sensitiveElement(),
    detailHead: sensitiveElement(),
    detailBody: sensitiveElement(),
    sourceList: sensitiveElement(),
    noSources: sensitiveElement()
  };

  purgeProtectedTopicDom(elements);

  assert.equal(elements.scopeSelect.value, "");
  assert.deepEqual(elements.scopeSelect.children, []);
  assert.equal(elements.searchInput.value, "");
  assert.deepEqual(elements.list.children, []);
  for (const key of [
    "detailError",
    "detailTitle",
    "detailRef",
    "detailCurrent",
    "detailSummary",
    "detailPublished",
    "detailHead",
    "detailBody"
  ]) {
    assert.equal(elements[key].textContent, "");
  }
  assert.equal("state" in elements.detailCurrent.dataset, false);
  assert.deepEqual(elements.sourceList.children, []);
  assert.equal(elements.noSources.hidden, true);
});

test("detail parsed after cancellation, scope change, or selection change stays stale", async () => {
  const scenarios = [
    (state) => {
      state.generation += 1;
    },
    (state) => {
      state.scopeId = "scope-b";
    },
    (state) => {
      state.artifactId = "topic-b";
    }
  ];

  for (const makeStale of scenarios) {
    const state = {artifactId: "topic-a", generation: 1, scopeId: "scope-a"};
    let resolveBody;
    const response = {
      json() {
        return new Promise((resolve) => {
          resolveBody = resolve;
        });
      }
    };
    const parsed = parseTopicDetailWithFreshness(
      response,
      () => state.generation === 1 && state.scopeId === "scope-a" && state.artifactId === "topic-a"
    );

    makeStale(state);
    resolveBody({detail: "protected-old-detail", source_refs: [{source_id: "protected-old-source"}]});

    const result = await parsed;
    assert.equal(result.fresh, false);
  }
});

test("a still-current detail remains renderable after parsing", async () => {
  const detail = {detail: "current-detail", source_refs: []};
  const result = await parseTopicDetailWithFreshness({json: async () => detail}, () => true);

  assert.equal(result.fresh, true);
  assert.equal(result.detail, detail);
});

test("the Topics sign-out event purges rendered protected data", async () => {
  const {documentElement, elements} = createTopicsEnvironment({authenticationRequired: true});
  await loadTopicsPage("sign-out");
  const protectedIds = [
    "topics-detail-title",
    "topics-detail-ref",
    "topics-detail-current",
    "topics-detail-summary",
    "topics-detail-published",
    "topics-detail-head",
    "topics-detail-body",
    "topics-detail-error"
  ];
  const scopeSelect = elements.get("topics-scope-select");
  const searchInput = elements.get("topics-search");
  const list = elements.get("topics-list");
  const sourceList = elements.get("topics-source-refs");
  scopeSelect.value = "protected-scope-id";
  scopeSelect.children = [sensitiveElement({textContent: "Protected scope name"})];
  searchInput.value = "protected query";
  list.children = [sensitiveElement({textContent: "Protected topic"})];
  sourceList.children = [sensitiveElement({textContent: "protected-source"})];
  for (const id of protectedIds) {
    elements.get(id).textContent = `protected:${id}`;
  }

  elements.get("sign-out").emit("click");

  assert.equal(documentElement.dataset.serverSession, "missing");
  assert.equal(scopeSelect.value, "");
  assert.deepEqual(scopeSelect.children, []);
  assert.equal(searchInput.value, "");
  assert.deepEqual(list.children, []);
  assert.deepEqual(sourceList.children, []);
  for (const id of protectedIds) {
    assert.equal(elements.get(id).textContent, "");
  }
});

test("a superseded selection cannot render after delayed detail parsing", async () => {
  const {elements} = createTopicsEnvironment({authenticationRequired: false});
  const artifactA = {artifact_id: "topic-a", family: "topic-memory", revision: 1};
  const artifactB = {artifact_id: "topic-b", family: "topic-memory", revision: 1};
  const topicA = {
    artifact: artifactA,
    published_at: "2026-09-06T01:02:03Z",
    source_count: 1,
    summary: "Summary A",
    title: "Topic A"
  };
  const topicB = {...topicA, artifact: artifactB, summary: "Summary B", title: "Topic B"};
  let resolveOldBody;
  const oldBody = new Promise((resolve) => {
    resolveOldBody = resolve;
  });
  const responses = [
    {body: [{display_name: "Scope A", scope_id: "scope-a"}], path: "/dashboard/scopes"},
    {body: {items: [topicA, topicB], next_cursor: null}, path: "/dashboard/topic-memories/list"},
    {body: oldBody, path: "/dashboard/topic-memories/get"},
    {body: {}, ok: false, path: "/dashboard/topic-memories/get", status: 500}
  ];
  globalThis.fetch = async (path) => {
    const expected = responses.shift();
    assert.equal(path, expected.path);
    return {
      ok: expected.ok ?? true,
      status: expected.status ?? 200,
      json: async () => expected.body
    };
  };

  await loadTopicsPage("detail-race");
  const buttons = elements.get("topics-list").children;
  assert.equal(buttons.length, 2);
  const oldSelection = buttons[0].emit("click");
  await Promise.resolve();
  await Promise.resolve();
  const newSelection = buttons[1].emit("click");
  await newSelection;
  resolveOldBody({
    artifact: artifactA,
    current_artifact: artifactA,
    detail: "protected-old-detail",
    is_current: true,
    published_at: "2026-09-06T01:02:03Z",
    source_refs: [{source_id: "protected-old-source", source_type: "content"}],
    summary: "Protected old summary",
    title: "Protected old title"
  });
  await oldSelection;

  assert.equal(elements.get("topics-detail-title").textContent, "Topic B");
  assert.equal(elements.get("topics-detail-body").textContent, "");
  assert.deepEqual(elements.get("topics-source-refs").children, []);
  assert.equal(responses.length, 0);
});
