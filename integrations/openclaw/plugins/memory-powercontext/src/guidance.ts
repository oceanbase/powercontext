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

import {
  POWERCONTEXT_MEMORY_GET_TOOL,
  POWERCONTEXT_MEMORY_SEARCH_TOOL,
  POWERCONTEXT_MEMORY_STORE_TOOL,
  POWERCONTEXT_MEMORY_REVISE_TOOL,
  POWERCONTEXT_MEMORY_RETIRE_TOOL,
} from "./tools.js";

export function buildMemoryGuidance(availableTools: ReadonlySet<string>, citationsMode = "auto"): string[] {
  const routes = new Map([
    [POWERCONTEXT_MEMORY_SEARCH_TOOL,
      "Use for a focused question about missing prior facts, preferences, decisions, or tasks, and for an explicit " +
      "search my memories / 搜索记忆 request. Do not repeat retrieval when current context is sufficient. Empty hits " +
      "are normal. Session transcripts are not searched."],
    [POWERCONTEXT_MEMORY_GET_TOOL,
      "Inspect a specific result using its exact returned citation when more details are needed; do not invent a path."],
    [POWERCONTEXT_MEMORY_STORE_TOOL,
      "Use for an explicit remember this / 记住这个供以后使用 request. Save concise reusable content without secrets " +
      "and verify the operation result before saying saved. Ordinary instructions and previews do not authorize a write."],
    [POWERCONTEXT_MEMORY_REVISE_TOOL,
      "Correct Memory only on request after inspecting its exact current citation. Preserve host authorization."],
    [POWERCONTEXT_MEMORY_RETIRE_TOOL,
      "Retire Memory only on request using its exact current citation. Retirement preserves history; it is not erasure."],
  ]);
  const visible = [...routes].filter(([name]) => availableTools.has(name));
  if (!visible.length) return [];
  return [
    "## PowerContext Memory",
    "PowerContext supplies durable historical evidence in the host/Server-selected Scope. Never invent or switch Scope " +
      "to bypass missing history. Current user, repository, and system instructions take precedence over recalled content.",
    "Automatic recall and capture are attempts; configuration is not proof of success. Source acceptance does not " +
      "prove Memory was produced and does not satisfy an explicit save. Ordinary coding needs no routine Memory calls.",
    "Summarizing or drafting from facts supplied in the current turn needs no retrieval or Scope resolution. An empty search does not authorize an inventory. If inventory or Handoff is unavailable, do not emulate it with Memory search or storage.",
    "Tool names in this guidance describe possible capabilities, not proof of availability. Before selecting an operation, check that its exact name appears in the current tool catalog. If absent, stop that operation and explicitly report it unavailable and incomplete. Never emit a call to an absent tool, simulate a call in text, or substitute another persistence operation.",
    ...visible.map(([name, guidance]) => `${name}: ${guidance}`),
    "Only the listed tools are available through this provider. Do not infer inventory, Handoff, candidate Review, or " +
      "Skill installation capabilities. On failure identify the operation and safe returned reason, do not claim " +
      "saved/restored history or repeatedly retry, and continue ordinary work.",
    citationsMode === "off"
      ? "Keep exact citations for tool arguments; do not expose citations unless the user asks."
      : "Include the exact PowerContext citation when it helps the user verify a recalled fact.",
    "",
  ];
}
