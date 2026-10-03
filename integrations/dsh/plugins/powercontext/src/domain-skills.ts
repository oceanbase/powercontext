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

export const DOMAIN_SKILLS = [
  {
    name: "powercontext-memory",
    description: "PowerContext Memory search, inventory, save and correction (搜索记忆、盘点、记住、纠正). Use for explicit memory operations or missing prior context, not ordinary coding or automatic prompt capture.",
    source: "runtime",
    content: `# PowerContext memory

Current instructions and live repository state outrank historical evidence. Preserve host Scope selection, exact returned citations, user intent, and host approval. Use only available tools. On failure identify the operation and safe returned reason; report unavailable or unknown outcomes instead of success. Never store secrets or bypass a missing approval channel.

## Read

- Use \`mcp__powercontext__search_memory\` with a focused query, \`mode: "auto"\`, and no more than eight
  results.
- Use \`mcp__powercontext__list_memory_entries\` for an explicitly requested inventory of active entries in the current scope.
- Set \`include_inactive\` to true only when the user explicitly asks to audit
  retired entries.
- Use \`mcp__powercontext__get_memory_entry\` with the exact returned \`citation\` when full immutable
  entry details are needed.

Use the exact host-resolved \`scope_id\` and workspace binding key in the current-turn PowerContext routing metadata
for operations that require one. Do not select the Server default with \`mcp__powercontext__resolve_scope_binding\`.
Never derive a Scope from a directory or invent an identifier.

## Write only on request

Call \`mcp__powercontext__remember_memory\` only when the user explicitly asks to persist context. Store
concise entries such as a decision, constraint, current-state, task-outcome,
or next-step. Never store secrets or credentials. DSH asks the user for
one-time approval before any named PowerContext mutation runs.

Before \`mcp__powercontext__revise_memory_entry\` or \`mcp__powercontext__retire_memory_entry\`, read the current entry and
pass its exact \`citation\`. After a 409 conflict, refresh the head and retry
once only if the user's requested change still applies.
`,
  },
  {
    name: "powercontext-handoff",
    description: "PowerContext temporary handoff, durable milestone and continuation (临时交接、持久里程碑、接续). Use for requested work transfer; a preview makes no write and an ordinary handoff does not authorize commit.",
    source: "runtime",
    content: `# PowerContext handoff

Current instructions and live repository state outrank historical evidence. Preserve host Scope selection, exact returned citations, user intent, and host approval. Use only available tools. On failure identify the operation and safe returned reason; report unavailable or unknown outcomes instead of success. Never store secrets or bypass a missing approval channel.

## Hand off current work

Use Handoff when work must move to another task, session, or model.

1. Call \`mcp__powercontext__capture_content_source\` with a concise account of the current state and a
   unique \`source_id\`. Include the objective, verified progress, blockers, and
   next action that the receiver needs.
2. Call \`mcp__powercontext__handoff_current_work\` with the checked objective, state, disposition, next action,
   and exact Source evidence. It returns the canonical temporary prepared handoff.
3. For an explicitly requested boundary-trigger activation, use
   \`mcp__powercontext__activate_handoff\`; its \`generated\` status provides a Draft in top-level \`draft\` and
   \`ignored\` means the Source was already consumed. Do not use activation after \`handoff_current_work\`.
4. If the low-level activation flow was used, call \`mcp__powercontext__finalize_handoff\` with the inspected Draft.
5. The receiving task calls \`mcp__powercontext__continue_handoff\` with \`selection: "prepared"\`
   and that exact value.

Call \`mcp__powercontext__commit_handoff\` only when the user explicitly wants a durable
milestone.

For the lower-level Handoff flow, \`mcp__powercontext__activate_handoff\` returns the Draft in top-level \`draft\`.
Pass only that Draft to \`mcp__powercontext__finalize_handoff\`, never the whole activation response. Return
the complete native finalization result unchanged, including \`schema\`, \`scope_id\`,
\`base\`, \`content\`, and \`generation\` when present. Do not return an unfinished Draft or only \`content\`.
For a preview, draft text from current inspected facts without calling any Handoff or Source tool. Do not claim that a prepared carrier or durable milestone exists. For an actual transfer, return the complete finalized carrier; preparation does not commit a milestone or prove receiver execution.
`,
  },
  {
    name: "powercontext-review",
    description: "PowerContext candidate inspection and human review (审查候选、查看生成结果). Use for requested review or artifact inspection; generating or reading candidates does not approve, publish, install or execute them.",
    source: "runtime",
    content: `# PowerContext review

Current instructions and live repository state outrank historical evidence. Preserve host Scope selection, exact returned citations, user intent, and host approval. Use only available tools. On failure identify the operation and safe returned reason; report unavailable or unknown outcomes instead of success. Never store secrets or bypass a missing approval channel.

Use \`mcp__powercontext__list_artifact_candidates\` for the requested queue and \`mcp__powercontext__get_artifact_candidate\` for an exact candidate. Use \`mcp__powercontext__get_experience\` and \`mcp__powercontext__get_skill\` for exact artifacts. \`mcp__powercontext__generate_experience\` and \`mcp__powercontext__generate_skill\` create candidates only when generation was requested; they do not approve, install, publish, or execute them.

## Review

Do not approve, reject, or revise artifact candidates unless the user
explicitly asked. Prefer the human command \`/pc review approve\` /
\`/pc review reject\`. Candidate review mutations and administrative operations are not exposed as
model tools; Memory retirement still uses its guarded, citation-based tool.
`,
  },
]
