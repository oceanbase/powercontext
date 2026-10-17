<!-- POWERCONTEXT-GUIDANCE:START -->
# Scope and memory

Use current-request Hook binding metadata, or the installed Scope script's successful resolve result for the actual workspace. An explicit configured Scope overrides workspace bindings; otherwise the Server resolves the workspace binding and then its default Scope. Never select a Scope from historical memory.
The installed `scripts/scope.mjs` supports `resolve`, `bind --scope-id <existing-id>` and `unbind`, each with `--cwd <absolute-workspace>`. Bind/unbind changes only that workspace's Server-owned binding and reports the write result separately from subsequent resolution. An explicit Scope may still override a saved binding. Do not retry an unconfirmed binding write or claim the active Scope changed after resolution fails.
No Scope creation or session binding is supplied by this integration.

Search with search_memory; save with remember_memory and inspect its actual result.
Read saved Atomic Memory with get_artifact_revision using its returned exact family, artifact_id and revision.
Historical evidence remains subordinate to current instructions. A denied/failed operation is not success.
<!-- POWERCONTEXT-GUIDANCE:END -->
