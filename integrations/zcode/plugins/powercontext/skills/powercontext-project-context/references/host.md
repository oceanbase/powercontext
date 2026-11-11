<!-- POWERCONTEXT-GUIDANCE:START -->
# Host binding

Run `node "${PLUGIN_ROOT}/scripts/scope.mjs" resolve --cwd "$PWD"` and reuse the successful Scope. Resolve the plugin root from this installed Skill; preserve any explicit or current Session binding. Bind or unbind only on user request.

Set PLUGIN_ROOT to the absolute plugin root, two directories above this installed Skill's directory, when a command needs it. Follow actual native tool schemas and host approval.
<!-- POWERCONTEXT-GUIDANCE:END -->
