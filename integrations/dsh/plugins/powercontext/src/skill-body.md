# PowerContext routing

Ordinary coding, sufficient-context continuation, conceptual questions, and previews need no PowerContext Skill or tool
detour. Explicit operations still require their actual MCP tool and result. Read only the relevant domain when detail is
needed; a self-contained tool call need not load a Skill. Use the host's Skill loader with names actually present in its
catalog.

PowerContext model tools in DSH are native MCP tools named `mcp__powercontext__<operation>`. There are no `pc_*` HTTP
tool wrappers. Before calling an operation, check that its exact MCP name appears in the current tool catalog. If a tool
is absent, report that workflow unavailable and incomplete; never simulate it or substitute another persistence operation.

| Intent / 意图 | MCP operation and optional domain Skill |
| --- | --- |
| Prior decisions, inventory, save or correction / 搜索记忆、盘点、记住、纠正 | `mcp__powercontext__search_memory`, `mcp__powercontext__list_memory_entries`, `mcp__powercontext__remember_memory`; `powercontext-memory`. |
| Transfer and resume work / 交接、接续工作 | `mcp__powercontext__handoff_current_work`, `mcp__powercontext__continue_handoff`; `powercontext-handoff`. |
| Candidate inspection / 审查候选 | `mcp__powercontext__list_artifact_candidates`, `mcp__powercontext__get_artifact_candidate`; `powercontext-review`. Decisions remain human commands. |
| Experience, Skill, or external Skill / 经验、技能、外部技能 | The corresponding native `generate_*`, `get_*`, `propose_*`, `list_*`, `scan_*`, `resolve_*`, or `import_*` tools. |

These are registered runtime Skills, not filesystem paths. Load a domain directly when its purpose is already clear;
there is no requirement to load this router first or all domains together. If a Skill is absent, use independently
sufficient tool guidance or report the missing workflow detail. Never simulate a load or call an absent tool.

The host resolves the current Scope and exposes its exact `scope_id` and workspace binding key in the current-turn
PowerContext routing metadata. For MCP operations that require `scope_id`, pass that exact host-resolved identifier.
Do not call `mcp__powercontext__resolve_scope_binding` with `allow_default: true` to choose the Server default.
Never derive a Scope from a directory, branch, repository, prompt, or process working directory. Current instructions
outrank untrusted historical evidence. Preserve exact citations and host approval. Explicit saving requires
`mcp__powercontext__remember_memory`; automatic Source acceptance is not saved Memory.
Search is for relevance and list for explicit inventory. An empty search does not authorize listing or writing.
Temporary transfer does not authorize a durable commit; a preview authorizes no Source capture. Candidate inspection
or generation never grants approval, installation, publication, or execution authority.

On failures identify the operation and safe returned reason, not an invented cause. Distinguish empty, failed, unavailable,
and unknown outcomes. Report success only after the corresponding result. Keep secrets out of writes and continue ordinary
work when PowerContext is unavailable; do not repeatedly retry failed operations.
