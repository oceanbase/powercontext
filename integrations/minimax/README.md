# MiniMax integration

[`plugins/powercontext`](plugins/powercontext) contains the PowerContext plugin for MiniMax Code. See the [package README](plugins/powercontext/README.md) for server setup, usage, and troubleshooting.

## Install with PowerContext

```bash
powercontext setup minimax --source /path/to/powercontext
powercontext doctor minimax --json
```

Use `--minimax-data-dir` for an explicit MiniMax data root and `--minimax-command` for an executable launcher outside PATH. See the [English guide](../../docs/en/docs/integrations/minimax.md), [中文指南](../../docs/zh/docs/integrations/minimax.md), and [native acceptance record](acceptance.md) for directory precedence, private configuration, upgrades, and verification limits.

## Load locally

From the repository root, copy the package into a separate MiniMax data directory:

```bash
test_root="$(mktemp -d)"
mkdir -p "$test_root/plugins"
cp -R integrations/minimax/plugins/powercontext "$test_root/plugins/powercontext"
MINIMAX_DATA_DIR="$test_root" mcode plugin list --marketplace local --json
```

The listing should show `powercontext` enabled with one Skill and one MCP server. The separate data directory needs its own account or model configuration for model calls.

This command exercises MiniMax Code's native manifest loader. Inspect the listing: an invalid package can be omitted even when the command exits successfully. Loading checks package discovery and references; it does not verify MCP connectivity or model calls.

MiniMax's public [`validate.mjs`](https://github.com/MiniMax-AI/MiniMax-Code-Plugins/blob/main/scripts/validate.mjs) checks portable Agent Plugins with a root `plugin.json`. It does not validate this package's native `.minimax-plugin/plugin.json` format.

For regular use, copy the package to `plugins/powercontext` under your MiniMax data directory. The standard default is `~/.minimax`; profiles/builds may select another directory. `MINIMAX_DATA_DIR` overrides it, with `MAVIS_DATA_DIR` as a fallback. Preserve any existing user configuration when updating the package.

## Maintain the package

Edit files directly in `plugins/powercontext`. The manifest is `.minimax-plugin/plugin.json`; it references the MCP configuration, Skill, and icon within the package. Keep the Skill's supporting files under its `references/` directory and update the manifest version when releasing changed content.

## GitHub source

| Field | Value |
| --- | --- |
| Repository | `https://github.com/oceanbase/powercontext` |
| Ref | A public commit SHA or tag containing the package |
| Plugin subdirectory | `integrations/minimax/plugins/powercontext` |
