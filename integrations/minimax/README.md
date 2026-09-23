# MiniMax integration

The source package and evaluation distribution use the same native MiniMax hook registration.
Use `powercontext setup minimax` to install, or `make agent-distributions` to produce a standalone package. Building requires local `uvx` and `npx`;
execution requires the installed client with `powercontext-hook` on PATH.
`UserPromptSubmit` resolves the server Scope and contributes bounded
historical context; it does not automatically capture prompts. See
the [distribution contract](../../docs/en/development/plugin-distribution.md).

[`plugins/powercontext`](plugins/powercontext) contains the PowerContext plugin for MiniMax Code. See the [package README](plugins/powercontext/README.md) for server setup, usage, and troubleshooting.

## Load locally

Use the shared Python installer from the repository root:

```bash
powercontext setup minimax --source .
powercontext doctor minimax --server
```

Setup generates MCP and Skill resources, installs the native package, and verifies discovery with
`mcode plugin list --marketplace local --json`. `MINIMAX_DATA_DIR` selects a separate data directory; the default
is `~/.minimax`. Existing private MCP overrides remain effective for both the prompt hook and diagnostics.
The same commands accept `--destination` for an explicit plugin directory; MiniMax must discover that directory.

## Maintain the package

Edit native adapters in `plugins/powercontext` and shared resources in `integrations/distribution/powercontext_integrations/assets/`.
The manifest is `.minimax-plugin/plugin.json`; it references the generated MCP configuration, Skill, hook document, and icon.
Edit hook bindings in `integrations/distribution/powercontext_integrations/assets/targets/minimax.toml`, then run
`make agent-resources` to regenerate the native hook configuration. Keep the Skill's supporting files under its `references/` directory and update the manifest version when releasing changed content.

## GitHub source

| Field | Value |
| --- | --- |
| Repository | `https://github.com/oceanbase/powercontext` |
| Ref | A public commit SHA or tag containing the package |
| Plugin subdirectory | `integrations/minimax/plugins/powercontext` |
