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

export function purgeProtectedTopicDom(elements) {
  elements.scopeSelect.replaceChildren();
  elements.scopeSelect.value = "";
  elements.searchInput.value = "";
  elements.list.replaceChildren();
  elements.detailError.textContent = "";
  elements.detailTitle.textContent = "";
  elements.detailRef.textContent = "";
  elements.detailCurrent.textContent = "";
  elements.detailCurrent.removeAttribute("data-state");
  elements.detailSummary.textContent = "";
  elements.detailPublished.textContent = "";
  elements.detailHead.textContent = "";
  elements.detailBody.textContent = "";
  elements.sourceList.replaceChildren();
  elements.noSources.hidden = true;
}

export async function parseTopicDetailWithFreshness(response, isFresh) {
  const detail = await response.json();
  return {detail, fresh: isFresh()};
}
