<!-- POWERCONTEXT-GUIDANCE:START -->
# Host binding

Run `python3 "${PLUGIN_ROOT}/scripts/workspace_scope.py" --cwd "$PWD"` before the first data operation and reuse its Scope. Use the host-provided plugin root when available; otherwise locate it from this installed Skill.

Set PLUGIN_ROOT to the absolute plugin root, two directories above this installed Skill's directory, when a command needs it. Follow actual native tool schemas and host approval.
<!-- POWERCONTEXT-GUIDANCE:END -->
