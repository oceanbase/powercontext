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

export function buildScopeSelectionChoices(scopes, translate) {
  const choices = [{key: "all", label: translate("allScopes"), selection: {mode: "all"}}];
  for (const scope of scopes.filter((item) => item.parent_scope_id === null)) {
    choices.push({
      key: `subtree:${scope.scope_id}`,
      label: translate("subtreeView", {title: scope.display_name || scope.title}),
      selection: {mode: "subtree", root_scope_id: scope.scope_id}
    });
  }
  for (const scope of scopes) {
    choices.push({
      key: `exact:${scope.scope_id}`,
      label: translate("exactFocus", {title: scope.display_name || scope.title}),
      selection: {mode: "exact", scope_ids: [scope.scope_id]}
    });
  }
  return choices;
}
