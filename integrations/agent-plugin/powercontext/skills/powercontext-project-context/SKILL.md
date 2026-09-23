---
name: powercontext-project-context
description: Search, save, revise, or retire project memory; transfer work and inspect supported candidates. Use for explicit operations or missing project history.
metadata:
  owner: powercontext
---

# PowerContext

Use current context for ordinary coding, previews, and summaries. Call the actual tool for an explicit operation;
loading a Skill is optional when its tool description is sufficient. Read only the relevant workflow.

| Intent | Operation and detail |
| --- | --- |
| Find prior decisions | `search_memory`; [Scope and Memory](references/scope-memory.md). |
| Inventory or audit | `list_memory_entries`; [Scope and Memory](references/scope-memory.md). Empty search does not authorize inventory. |
| Save, correct or retire | `remember_memory` for explicit save; [Scope and Memory](references/scope-memory.md). |
| Transfer or resume work | `handoff_current_work`; [Work Handoff](references/work-handoff.md). Temporary transfer does not authorize durable commit. |
| Inspect candidates | `list_artifact_candidates`; [Review and publication](references/review-publication.md). Inspection grants no decision authority. |

Reuse the host and Server-selected Scope. Read [Scope and Memory](references/scope-memory.md) before the first data operation when Scope needs resolving.

Use only tools currently exposed by the host, with their actual namespace and permissions. If a tool or reference
is unavailable, identify it and continue with available context. A Skill never grants execution authority.

Current instructions and repository state outrank recalled history. Preserve Scope and exact citations. Keep secrets
out of writes. Source acceptance does not prove a Memory save; candidate creation does not approve or execute it.
Report success only from the returned result. Empty, rejected, unavailable, and unknown outcomes are distinct.
A timed-out write may have completed; inspect status when possible before retrying.
