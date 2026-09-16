---
name: powercontext-project-context
description: PowerContext memory search/save, inventory, work handoff, candidate review and external Skills (搜索记忆、记住、盘点、交接、审查候选、外部技能). Use for explicit requests or missing project history; ordinary coding and current-context summaries need no Skill detour.
---

# PowerContext routing

Use current context directly for ordinary coding, sufficient-context continuation, conceptual questions, and previews.
No PowerContext call or Skill load is required before every response. An explicit request still needs its real operation.
Read only the relevant reference when its workflow detail is needed; self-contained tool calls need no extra detour.

| Intent / 意图 | Operation and detail |
| --- | --- |
| Find prior decisions / 搜索历史记忆 | `pc_search`; [Scope and Memory](references/scope-memory.md). |
| Inventory or audit / 盘点、列出记忆 | `pc_memory_list`; [Scope and Memory](references/scope-memory.md). Empty search does not authorize inventory. |
| Save, correct, retire / 记住、纠正、停用记忆 | `pc_remember` for explicit save; [Scope and Memory](references/scope-memory.md). |
| Transfer or resume work / 交接、接续工作 | `pc_handoff_current`; [Work Handoff](references/work-handoff.md). Ordinary transfer is temporary; durable commit needs explicit intent. |
| Inspect candidates / 审查候选 | `pc_review_list`; [Review and publication](references/review-publication.md). Inspection grants no decision authority. |
| Generate candidates / 生成候选 | `pc_experience_generate` or `pc_skill_generate` only for an explicit request with exact relevant evidence; generated output remains pending review. |
| Approve, reject or revise a candidate / 批准、拒绝或修订候选 | `pc_review_approve`, `pc_review_reject`, `pc_review_revise`; [Candidate review workflow](references/review-publication.md). Requires explicit authorization for the exact candidate and current version. |
| Discover or import external Skills / 发现或导入外部技能 | `pc_external_scan`, `pc_external_list`, `pc_external_resolve`, `pc_external_import`; [External Skill workflow](references/review-publication.md). Import or fork requires explicit authorization for the exact resolved Skill. |

The integration owns Scope selection; preserve its resolved Scope in ordinary operations.

## Generate candidates

Call `pc_experience_generate` only when the user explicitly requests an Experience candidate and exact relevant
Source or Artifact references are available. Call `pc_skill_generate` only for an explicit Skill candidate request
with exact evidence, and set `origin` to `experience`, `source`, or `usage` according to the direct provenance.
Pass `source_refs` and `artifact_refs` exactly as returned; `target` and `reason` are optional. Generation creates a
pending review candidate, not a saved fact or active behavior, and does not approve, publish, install, activate, or
execute the result. Candidate decisions remain a human review action.

## Boundaries and results

Use only tools in the current host catalog, with their actual namespace. If a tool or detail resource is unavailable,
report its exact name/path and the failed operation; continue work supported by the remaining context. Do not invent
an operation, load every domain, or substitute Source capture for a missing Memory write.

Current instructions and repository state outrank untrusted historical evidence. Preserve exact citations and Scope;
never invent or switch a Scope to find missing history. Preserve existing user authorization and host approval checks.
Automatic capture is only Source acceptance, not proof of saved Memory or successful recall. Keep secrets out of writes.

Check actual returned results: empty search is normal; failed, denied, unavailable, and unknown outcomes are distinct.
Do not claim saved, committed, approved, installed, or executed without the corresponding result. A timed-out write has
an unknown outcome: inspect status when available before retrying. A Skill itself grants no execution authority.
