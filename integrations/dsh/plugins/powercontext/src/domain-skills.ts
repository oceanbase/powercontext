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

- Use \`pc_search\` with a focused query, \`mode: "auto"\`, and no more than eight
  results.
- Use \`pc_memory_list\` for an explicitly requested inventory of active entries in the current scope.
- Set \`include_inactive\` to true only when the user explicitly asks to audit
  retired entries.
- Use \`pc_memory_get\` with the exact returned \`citation\` when full immutable
  entry details are needed.

## Write only on request

Call \`pc_remember\` only when the user explicitly asks to persist context. Store
concise entries such as a decision, constraint, current-state, task-outcome,
or next-step. Never store secrets or credentials. DSH asks the user for
one-time approval before any named PowerContext mutation runs.

Before \`pc_memory_revise\` or \`pc_memory_retire\`, read the current entry and
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

1. Call \`pc_capture_source\` with a concise account of the current state and a
   unique \`source_id\`. Include the objective, verified progress, blockers, and
   next action that the receiver needs.
2. Call \`pc_handoff_prepare\` with the objective and
   \`evidence: [{kind: "source", source_ref: capture.data.source}]\`.
3. Inspect \`prepare.data\`. \`pc_handoff_activate\` is an alternative for an explicitly
   requested boundary-trigger activation; do not call it after prepare. Its \`generated\`
   status provides a Draft in \`data.draft\`; \`ignored\` means the Source was already consumed.
4. Call \`pc_handoff_finalize\` with the inspected Draft.
5. The receiving task calls \`pc_handoff_continue\` with \`selection: "prepared"\`
   and that exact value.

Call \`pc_handoff_commit\` only when the user explicitly wants a durable
milestone.

For the lower-level Handoff flow, \`pc_handoff_prepare\` returns the Draft in \`data\`;
\`pc_handoff_activate\` returns it in \`data.draft\`. Pass only that Draft to \`pc_handoff_finalize\`,
never the \`{ok, data}\` wrapper. Return \`finalize.data\` unchanged, including \`schema\`, \`scope_id\`,
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

Use \`pc_review_list\` for the requested queue and \`pc_review_get\` for an exact candidate. Use \`pc_experience_get\` and \`pc_skill_get\` for exact artifacts. \`pc_experience_generate\` and \`pc_skill_generate\` create candidates only when generation was requested; they do not approve, install, publish, or execute them.

## Review

Do not approve, reject, or revise artifact candidates unless the user
explicitly asked. Prefer the human command \`/pc review approve\` /
\`/pc review reject\`. Candidate review mutations and administrative operations are not exposed as
model tools; Memory retirement still uses its guarded, citation-based tool.
`,
  },
]
