<!-- POWERCONTEXT-GUIDANCE:START -->
# Host binding

Antigravity's `PreToolUse` hook automatically binds PowerContext MCP data-plane calls (`scope_id`, `selection`, `binding_keys`, and session binding `key`) to the active conversation and workspace.

When the user explicitly asks to inspect, bind, or clear the current workspace Scope for later Antigravity sessions, run:

```sh
"${PLUGIN_ROOT}/.venv/bin/python" "${PLUGIN_ROOT}/scripts/scope_binding.py" --cwd "$PWD" --bind-scope "SCOPE_ID"
```

Run the same command without `--bind-scope "SCOPE_ID"` to inspect the effective workspace Scope, or with `--clear-scope` to remove an explicit binding. An explicit `POWERCONTEXT_ANTIGRAVITY_SCOPE_ID` overrides the stored workspace binding; do not rebind merely because the results differ.

This script changes the workspace binding, while the native `set_scope_binding` tool changes the current Session binding. Existing Session bindings and Scope contents remain intact.

Set `PLUGIN_ROOT` to the absolute plugin root, two directories above this installed Skill's directory, when a command needs it. Follow actual native tool schemas and host approval.
<!-- POWERCONTEXT-GUIDANCE:END -->
