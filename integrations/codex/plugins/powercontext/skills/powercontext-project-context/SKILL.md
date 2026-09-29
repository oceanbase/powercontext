---
name: powercontext-project-context
description: PowerContext memory search/save, inventory, work handoff, Experience/Skill synthesis, external Skill import and candidate review (搜索记忆、记住、盘点、交接、经验、技能、审查候选). Also use for binding or clearing a checkout Scope for later sessions (绑定项目 Scope). Use for explicit requests or missing project history; ordinary coding and current-context summaries need no Skill detour.
---

# PowerContext routing

Use current context directly for ordinary coding, sufficient-context continuation, conceptual questions, and previews.
No PowerContext call or Skill load is required before every response. An explicit request still needs its real operation.
Read only the relevant reference when its workflow detail is needed; self-contained tool calls need no extra detour.

| Intent / 意图 | Operation and detail |
| --- | --- |
| Bind or clear a checkout Scope / 绑定或清除项目 Scope | `scripts/scope_binding.py`; [Scope and Memory](references/scope-memory.md). MCP binding tools target only the current Session. |
| Find prior decisions / 搜索历史记忆 | `search_memory`; [Scope and Memory](references/scope-memory.md). |
| Inventory or audit / 盘点、列出记忆 | `list_memory_entries`; [Scope and Memory](references/scope-memory.md). Empty search does not authorize inventory. |
| Save, correct, retire / 记住、纠正、停用记忆 | `remember_memory` for explicit save; [Scope and Memory](references/scope-memory.md). |
| Transfer or resume work / 交接、接续工作 | `handoff_current_work`; [Work Handoff](references/work-handoff.md). Ordinary transfer is temporary; durable commit needs explicit intent. |
| Inspect candidates / 审查候选 | `list_artifact_candidates`; [Review and publication](references/review-publication.md). Inspection grants no decision authority. |
| Read or synthesize Experience / 读取、提炼经验 | `get_experience`, `generate_experience`, or `propose_experience`; [Experience and Skills](references/experience-skills.md). |
| Find, read or synthesize managed Skills / 查找、读取、提炼技能 | `list_managed_skills`, `get_skill`, `generate_skill`, or `propose_skill`; [Experience and Skills](references/experience-skills.md). |
| Inspect or import external Skills / 查看、导入外部技能 | `scan_external_skills`, `list_external_skills`, `resolve_external_skill`, or `import_external_skill`; [Experience and Skills](references/experience-skills.md). |

The integration owns Scope selection; preserve its resolved Scope in ordinary operations.

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
