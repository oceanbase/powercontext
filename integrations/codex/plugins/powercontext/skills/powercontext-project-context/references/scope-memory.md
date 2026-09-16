# Scope and Memory

## Scope binding

The integration binds the current Codex Session before recall, capture, and
PowerContext MCP calls. Do not derive a Scope from the repository, directory,
branch, Agent, or prompt, and do not override the integration binding in an
ordinary data-plane call.

When the user explicitly asks to start an independent result, use
`resolve_scope_binding` to inspect the current Session Scope, then call
`create_scope` with a concise title, summary, stable idempotency key, and only
the Parent, Context References, or external references the user established.
Use the current Scope as Parent only when the new Scope is an independently
continuable sub-result of it. Then use `set_scope_binding`; the integration
replaces its binding key with the current Codex Session identity. Reuse an
existing Scope instead when the work does not need independent isolation,
continuation, delivery, or observation.

## Read

- Use `search_memory` with a focused query, `mode: "auto"`, and no more than
  eight results.
- Use `list_memory_entries` for an explicitly requested inventory of active entries in the current scope.
- Set `include_inactive` to `true` only when the user explicitly asks to audit
  retired entries or the complete current Memory snapshot.
- Use `get_memory_entry` with the exact returned `citation` when full immutable
  entry details are needed.
- Use `search_topic_memory` with a focused query and no more than eight results
  for durable topic summaries. The Server selects the retrieval mode.
- Use `get_topic_memory` with an exact returned Artifact reference only when
  full Topic Memory detail is needed. Do not request Topic Memory flushes from
  Codex.

## Explicit Memory Requests

The examples below are illustrative, not an exhaustive keyword allowlist. Match
the user's intent and equivalent paraphrases in English or Chinese.

### Save Memory

When the user asks to preserve a preference, decision, constraint, fact,
current state, task outcome, or next step for future reuse, call
`remember_memory`. This includes requests such as:

- `remember I prefer uv for Python`;
- `save this preference: use pytest`;
- `record this decision`;
- `keep this constraint for future work`;
- `please remember that deployments target OceanBase`;
- `记住我偏好使用 uv`;
- `保存这个偏好：测试使用 pytest`;
- `记录这个决定`；
- `把这个约束记下来`；
- `请记住部署目标是 OceanBase`。

Equivalent expressions such as `Please keep in mind that I use uv`, `Don't
forget that the deployment target is OceanBase`, or `请把这个方案作为以后工作
的默认方式保存` also express a save request. The phrase `From now on, use
pytest` by itself is a current-turn instruction; only persist it when the user
also asks to remember, save, or keep it for future work.

For an explicit save request:

1. Resolve the current `scope_id` using the resolver above and reuse it.
2. Choose a supported `kind` from the current schema: `decision`, `constraint`,
   `current-state`, `task-outcome`, `next-step`, or `agent-note`. Build concise, self-contained `text`.
3. Call `remember_memory` with that scope and content. Do not call
   `select_handoff_workstream` for this flow.
4. Wait for the tool result. Report that Memory was saved only after the tool
   returns successfully. A prompt Source captured by the Hook is not a Memory
   write and does not satisfy this request.

Never save secrets, credentials, tokens, or a verbatim copy of the full prompt.

### Search Memory

When the user asks to find, recall, search, or retrieve previously saved
Memory, call `search_memory`. This includes requests such as:

- `search my memories`;
- `what do you remember about deployment?`;
- `find the memory about the database decision`;
- `recall what we decided about migrations`;
- `搜索我的记忆`；
- `查找之前记录的数据库决定`；
- `回忆一下我们关于迁移的决定`；
- `你还记得 Python 版本约束吗？`。

For an explicit search request, resolve and reuse the current `scope_id`,
extract a focused query, call `search_memory` with `mode: "auto"` and at most
eight results, then answer from the returned hits. A successful empty result
means that no matching Memory was found; it does not authorize inventing
history. Do not call `select_handoff_workstream` for this flow.

Do not claim that Memory was saved or searched unless the corresponding
PowerContext tool was actually called and returned successfully. If the tool is
unavailable or fails, clearly say that the Memory was not saved or searched and
that the PowerContext operation could not be completed. Do not replace an
explicit Memory operation with a verbal acknowledgement.

Conceptual questions such as `How does PowerContext Memory work?`, preview
requests such as `Draft a preference entry, but do not save it`, and questions
about the difference between Memory and Handoff do not call a Memory tool.

## Write only on request

Call `remember_memory` only when the user explicitly asks to persist context.
Store concise, self-contained entries such as a decision, constraint,
current-state, task-outcome, or next-step. Never store secrets or credentials,
and never claim success until the tool returns successfully.

Before `revise_memory_entry` or `retire_memory_entry`, read the current entry.
Pass its exact `citation`; the citation's Memory revision is the concurrency
check. After a conflict, refresh the head and retry once only if the user's
requested change still applies.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
