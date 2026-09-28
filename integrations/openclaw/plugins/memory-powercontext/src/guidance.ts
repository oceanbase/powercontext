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
import {
  POWERCONTEXT_WORK_CONTRACT_TOOL, POWERCONTEXT_HANDOFF_CURRENT_WORK_TOOL,
  POWERCONTEXT_HANDOFF_COMMIT_TOOL, POWERCONTEXT_HANDOFF_CONTINUE_TOOL,
  POWERCONTEXT_HANDOFF_ACKNOWLEDGE_TOOL, POWERCONTEXT_TASK_OUTCOME_TOOL,
} from "./work.js";

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
    [POWERCONTEXT_WORK_CONTRACT_TOOL,
      "Record an inspected baseline for explicitly delegated work. A contract grants no new execution authority."],
    [POWERCONTEXT_HANDOFF_CURRENT_WORK_TOOL,
      "Use for a requested transfer of current work. This captures its own Source; no preliminary search or capture " +
      "is needed. Each claim has text, basis, evidence; use declared with evidence=[] without prior exact citations. " +
      "next_action is one claim object or null; omissions is an array of strings. Return the handoff member unchanged " +
      "as the temporary carrier. Preparation does not commit or prove that the receiver acted."],
    [POWERCONTEXT_HANDOFF_COMMIT_TOOL,
      "Commit the exact prepared value only for an explicitly requested durable milestone, never just a temporary transfer."],
    [POWERCONTEXT_HANDOFF_CONTINUE_TOOL,
      "Read an exact prepared or committed Handoff, then verify its evidence against current instructions and live state."],
    [POWERCONTEXT_HANDOFF_ACKNOWLEDGE_TOOL,
      "Acknowledge only after checking evidence, live state, capability, and authorization. Never accept unchecked work."],
    [POWERCONTEXT_TASK_OUTCOME_TOOL,
      "Record observations at a real completion or interruption boundary. Preserve failed, unavailable, and unknown checks."],
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
    "If the optional powercontext-project-context Skill is available, read only the relevant workflow when needed. A Skill does not enable missing tools or grant write permission.",
    ...visible.map(([name, guidance]) => `${name}: ${guidance}`),
    "Only the listed tools are available through this provider. Do not infer Memory inventory, candidate Review, or " +
      "Skill installation capabilities. On failure identify the operation and safe returned reason, do not claim " +
      "saved/restored history or repeatedly retry, and continue ordinary work.",
    citationsMode === "off"
      ? "Keep exact citations for tool arguments; do not expose citations unless the user asks."
      : "Include the exact PowerContext citation when it helps the user verify a recalled fact.",
    "",
  ];
}
