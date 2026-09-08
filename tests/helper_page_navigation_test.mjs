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

const staticRoot = new URL("../src/powercontext/server/static/", import.meta.url);
const pageUiSource = await readFile(new URL("page-ui.js", staticRoot), "utf8");
const {createPageUi} = await import(`data:text/javascript;base64,${Buffer.from(pageUiSource).toString("base64")}`);
const helperPages = ["dashboard.js", "skills.js", "review.js", "handoff-report.js"];

class FakeElement {
  constructor(dataset = {}) {
    this.attributes = {};
    this.dataset = dataset;
    this.listeners = new Map();
    this.textContent = "";
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

function extractTranslations(source) {
  const marker = "const translations = ";
  const declaration = source.indexOf(marker);
  assert.notEqual(declaration, -1);
  const objectStart = source.indexOf("{", declaration + marker.length);
  const objectEnd = source.indexOf("\n};", objectStart);
  assert.notEqual(objectEnd, -1);
  const objectLiteral = source.slice(objectStart, objectEnd + 2);
  return Function("tagTranslations", `"use strict"; return (${objectLiteral});`)({en: {}, zh: {}});
}

function initializeTopicsNavigation(translations, locale) {
  const topicsNavigation = new FakeElement({i18n: "topicsTitle"});
  const themeToggle = new FakeElement();
  const languageToggle = new FakeElement();
  const documentElement = {dataset: {theme: "light"}, lang: locale};

  globalThis.document = {
    documentElement,
    title: "",
    getElementById(id) {
      return id === "theme-toggle" ? themeToggle : languageToggle;
    },
    querySelectorAll(selector) {
      return selector === "[data-i18n]" ? [topicsNavigation] : [];
    }
  };
  globalThis.localStorage = {
    getItem() {
      return null;
    },
    setItem() {}
  };
  globalThis.window = {matchMedia: () => ({matches: false})};

  createPageUi(translations).initialize();
  return topicsNavigation.textContent;
}

test("helper pages translate the shared Topics navigation in both locales", async (context) => {
  for (const helperPage of helperPages) {
    await context.test(helperPage, async () => {
      const translations = extractTranslations(await readFile(new URL(helperPage, staticRoot), "utf8"));

      assert.equal(initializeTopicsNavigation(translations, "en"), "Topics");
      assert.equal(initializeTopicsNavigation(translations, "zh"), "主题");
    });
  }
});
