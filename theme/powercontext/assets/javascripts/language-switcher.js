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

(function () {
  const languages = new Set(["en", "zh"]);

  function localizedPath(targetLanguage) {
    const segments = window.location.pathname.split("/");
    const languageIndex = segments.findIndex((segment) => languages.has(segment));

    if (languageIndex >= 0) {
      segments[languageIndex] = targetLanguage;
      return segments.join("/") + window.location.search + window.location.hash;
    }

    const base = window.location.pathname.endsWith("/")
      ? window.location.pathname
      : `${window.location.pathname}/`;
    return `${base}${targetLanguage}/`;
  }

  function updateLanguageLinks() {
    document.querySelectorAll(".pc-language-switcher[hreflang]").forEach((link) => {
      const targetLanguage = link.getAttribute("hreflang");
      if (!languages.has(targetLanguage)) {
        return;
      }
      link.setAttribute("href", localizedPath(targetLanguage));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", updateLanguageLinks);
  } else {
    updateLanguageLinks();
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(updateLanguageLinks);
  }
})();
