---
description: Initialize PowerMem for Claude Code after the plugin is installed. Use when the user asks to set up, initialize, or repair PowerMem.
---

Initialize PowerMem for Claude Code.

Read `apps/claude-code-plugin/SETUP.md`, section "Installed plugin initialization",
and follow only that section.

Do not run the source/developer setup flow from `SETUP.md`: do not build hook
binaries, do not stage the plugin, do not run `claude plugin marketplace add`, do
not run `claude plugin install`, and do not build the dashboard.

Use the plugin scripts as directed by that section:

- `scripts/status.sh`
- `scripts/init.sh`

Remember that `scripts/init.sh` installs `powermem` from PyPI by default. If the
user is testing unpublished backend changes, run the script with
`POWERMEM_INIT_PACKAGE='powermem @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>'`
instead of using the default PyPI package.

If values are missing, ask only for the missing values and pass them through
`POWERMEM_INIT_*` environment variables. Never print API keys; mask secrets in
summaries.

If the user is in a network where HuggingFace may be slow or blocked, offer to
run init with `POWERMEM_INIT_PRELOAD_MODEL=1` so the script downloads the default
embedding model through ModelScope and bridges it into the HuggingFace cache.
