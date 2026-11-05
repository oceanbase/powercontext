<!-- POWERCONTEXT-GUIDANCE:START -->
# Host binding

Use current-request PowerContext routing metadata and its Session binding for data operations.

When the user explicitly asks to bind the current checkout for later Codex sessions, use the existing server-assigned Scope ID in this command:

```sh
uv run --frozen --quiet --project "${PLUGIN_ROOT}" python "${PLUGIN_ROOT}/scripts/scope_binding.py" \
  --cwd "$PWD" --bind-scope "SCOPE_ID"
```

Run the same command without `--bind-scope "SCOPE_ID"` to inspect the effective workspace Scope. An explicit `POWERCONTEXT_CODEX_SCOPE_ID` overrides the stored workspace binding; do not rebind merely because the results differ. For an explicitly requested removal, use `--clear-scope` instead of `--bind-scope "SCOPE_ID"`.

This script changes the workspace binding, while the native `set_scope_binding` tool changes the current Session binding. Existing Session bindings and Scope contents remain intact. A new Codex session is required to adopt the workspace binding; the script's result does not replace a current Session override.

Set PLUGIN_ROOT to the absolute plugin root, two directories above this installed Skill's directory, when a command needs it. Follow actual native tool schemas and host approval.
<!-- POWERCONTEXT-GUIDANCE:END -->
