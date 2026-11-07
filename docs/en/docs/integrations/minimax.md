---
status: community
title: MiniMax Code
description: Install the native MiniMax plugin and distinguish package discovery from Server access.
---

# MiniMax Code

`community` · experimental

PowerContext installs a native MiniMax plugin containing the `powercontext-project-context` Skill and a streamable HTTP MCP connection. MiniMax owns the MCP session. The plugin does not start the Server or automatically capture conversations.

## Prerequisites and versions

Use Python 3.11+, the PowerContext CLI, and a running PowerContext Server. Install the CLI, Server, and plugin from the same repository revision. For a pinned release, use its matching repository tag with `--ref`; the plugin manifest version is independent of the Python package version. Remote refs follow the existing branch/tag convention. To install a specific commit, check it out locally and use `--source`.

Native tests cover the official npm CLI `@minimax-ai/code` versions 0.6.9 and 0.7.2 on Linux x86_64 with Node.js 22.23.3. Plugin-only MCP tool injection is not reliably qualified in either version: the host can report a connected plugin without making its tools available to the model, including through `tool_search`. Upgrading to 0.7.2, or passing setup or doctor, does not resolve this limitation. Desktop discovery and other platforms require separate qualification. See the [acceptance record](https://github.com/oceanbase/powercontext/blob/master/integrations/minimax/acceptance.md) for commands, diagnostic artifacts, and the evidence boundary.

The verified official 0.7.2 installation flow keeps the private `powercontext` MCP entry created by setup enabled together with the complete plugin. This provides MCP tools through the private connection and retains the plugin Skill. Removing or disabling the private entry in the official npm versions relies on the unqualified plugin-only path. Setup preserves an existing disabled preference; explicitly enable that private entry in `mcp.json` if you choose to use this verified path, then run `powercontext config agent minimax` and restart MiniMax.

## Install and select a target

```bash
powercontext setup minimax

# Repository root or the native plugin directory
powercontext setup minimax --source /path/to/powercontext

# A branch or tag containing the plugin, matched to your Server revision
powercontext setup minimax --source oceanbase/powercontext --ref <matching-tag>

# Explicit MiniMax data root and executable launcher
powercontext setup minimax --source /path/to/powercontext \
  --minimax-data-dir "/path/to/MiniMax data" \
  --minimax-command /path/to/mcode
```

The destination is `<data root>/plugins/powercontext`, including hidden files, Skill references, icon, and license. `--minimax-data-dir` takes precedence over nonempty `MINIMAX_DATA_DIR`, then `MAVIS_DATA_DIR`, then the saved installation target. Without these choices, the installer asks the selected CLI's `plugin marketplace list --json` for its local directory, including native profile/build selection. If the CLI is missing, the default is `~/.minimax`; a selected `MAVIS_PROFILE` requires an explicit data root in that case. Older or custom builds whose marketplace response cannot be interpreted require `--minimax-data-dir`.

The launcher comes from `--minimax-command`, the saved launcher for the same data root, or `mcode` on PATH. An invalid explicit or saved launcher fails before installation. Launch MiniMax with the selected data directory. Native discovery checks run against a disposable snapshot of that installation, with its local plugin enablement state. Setup saves the target for later `doctor minimax` and `config agent minimax` commands. Environment directory overrides still take precedence over that saved target.

Interactive `powercontext setup` includes MiniMax. Multiple agents share the same setup behavior:

```bash
powercontext setup codex minimax --source /path/to/powercontext --json
powercontext doctor minimax --json
```

## Run the plugin-only source fix

The [MiniMax readiness fix](https://github.com/hidb4ai/minimax-code/commit/0ea69bd04c90cc151cae2eff9d5b7b4465d3c7dc) waits for plugin MCP discovery and inventory publication before capturing a turn's tools. The Linux source build passes plugin-only invocation in the affected environment, including 6-second and 11-second inventory delays. This is a pinned community source build based on MiniMax's public 0.7.2 synchronization branch; the official npm package does not contain this patch. [Upstream issue #570](https://github.com/MiniMax-AI/minimax-code/issues/570) tracks adoption.

Use Node.js 22.23.3 and the repository's pinned pnpm version to build the launcher:

```bash
git clone --branch fix/plugin-mcp-turn-readiness-0.7.2 https://github.com/hidb4ai/minimax-code.git
cd minimax-code
git checkout 0ea69bd04c90cc151cae2eff9d5b7b4465d3c7dc
pnpm install --frozen-lockfile
pnpm build
powercontext setup minimax --source /path/to/powercontext \
  --minimax-data-dir "/path/to/MiniMax data" \
  --minimax-command "$PWD/dist/cli.js"
```

Launch this same `dist/cli.js` with `MINIMAX_DATA_DIR` set to the selected data directory. Setup saves that launcher for subsequent configuration and diagnostics. Keep the plugin enabled and its URL/credentials synchronized before choosing to disable the private entry for plugin-only execution. Returning to an unpatched launcher restores the official-version qualification limit. See the [acceptance record](https://github.com/oceanbase/powercontext/blob/master/integrations/minimax/acceptance.md) for the source revisions and validation boundary.

## Configure a protected or remote Server

Setup defaults to the local Server or preserves an existing native MCP endpoint. To select a different endpoint:

```bash
powercontext setup minimax --source /path/to/powercontext \
  --server-url https://memory.example
```

Supply credentials through `POWERCONTEXT_MINIMAX_AUTHORIZATION`, or the shared `POWERCONTEXT_CLIENT_API_TOKEN`, in the process environment or setup `--env-file`. The installer stores a literal Bearer header in `<data root>/mcp.json` and synchronizes enabled private connection settings into the installed `plugins/powercontext/powercontext.mcp.json`. The source/distributed package and installation record contain no credentials. Enter secrets without exposing them in shell history. On Linux/macOS, setup creates or updates these connection files with owner-only permissions; an unchanged disabled private configuration retains its existing permissions. Protect both installed connection files with appropriate ACLs on Windows.

Setup merges only the PowerContext server entry, preserving other MCP servers, unknown fields, headers, and an existing disabled preference. Existing credentials are retained. Upgrades also retain the installed plugin connection when the private entry is absent or disabled. A disabled private entry keeps its URL unless an endpoint is explicitly supplied through `--server-url` or the supported setup URL environment/file settings. With no entry changes, its entire private configuration file is left byte-for-byte unchanged. Supplying only a new credential rotates that entry’s credential at its existing URL without enabling it. The setup/config result reports `private_mcp_state`; a disabled entry remains inactive until the user enables it, and doctor continues to probe the effective bundled connection. Moving an authenticated entry to another endpoint requires an explicit replacement credential; setup will not silently forward the old credential. Malformed private configuration fails before package installation.

MiniMax exposes private and plugin MCP connections independently, potentially with names such as `powercontext` and `powercontext_2`. Setup/config synchronize their URLs and headers when the private entry is enabled, so both tool sets reach the selected Server. With a disabled private entry and no explicit endpoint, the plugin connection is preserved, and rotating a dormant private credential does not copy it into the active plugin connection. An explicit endpoint also configures the active plugin connection. Doctor reports mismatched enabled connections as a failure; rerun setup or `config agent minimax` to reconcile them. Configure literal URLs and headers; the supported private configuration does not expand `${TOKEN}` placeholders. Setup's remote HTTP consent governs PowerContext's own configuration and diagnostic requests; it does not bypass MiniMax's native HTTP/TLS restrictions. Prefer HTTPS. Doctor reads the native endpoint and headers, rather than treating a global PowerContext token as the host's credentials.

## Reinstall, upgrade, and recover

Repeat the setup command with the desired source/ref. The installer records its managed file hashes under the PowerContext data root, keyed by MiniMax data directory. It updates those files, removes unchanged obsolete managed files, and preserves user-created files and unrelated plugins. A modified managed file or a new package path colliding with user content, a directory, or a file used as its parent causes a conflict before replacement. The staged package is checked before publication; user-created files are not added to the managed inventory.

An identical manually copied package can be adopted while keeping extra user files. A different unmanaged package is rejected. Back it up outside the MiniMax `plugins` directory, preserve its private connection settings, and reconcile custom changes before retrying. Do not leave a second PowerContext package under another immediate plugin directory.

Package publication restores the prior package and ownership record on failure. Connection publication restores the prior private/shared configuration on failure. Setup reports a completed package installation separately if subsequent connection configuration fails. If interrupted publication leaves a recovery backup, restore the indicated backup before retrying.

## Understand verification results

- `package`: the native manifest and referenced local resources are present.
- `native`: MiniMax loads a disposable snapshot of the local `powercontext` plugin and its enablement state, checking the expected version and at least the required Skill/MCP counts. Counts do not establish resource names or an active MCP session. A successful command that omits the plugin fails this check.
- `server_liveness` / `server_readiness`: the selected Server responds and reports its readiness.
- `server_access`: the CLI accesses Server info with the effective native headers. It does not prove that MiniMax established a connection or invoked a tool.

Files copied with no CLI are reported as installed with degraded native verification and a nonzero overall exit. For a desktop installation, install the supported CLI or pass an existing executable launcher with `--minimax-command`; package copying alone is not desktop qualification. Doctor does not install, rewrite configuration, enable plugins, or initialize the real data directory. The CLI writes runtime records only inside a private temporary snapshot, which is removed afterward. Verification needs temporary space for the plugin and a copy of the native state database/WAL used to read enablement; unrelated sessions and provider state are not imported into the diagnostic runtime. Unsupported native state formats fail explicitly. Before the first setup, supply `--minimax-data-dir` or a directory environment override when using a native launcher; doctor does not start that launcher against the real home directory to discover a target.

Refresh the Local catalog with `Ctrl+R` in `/plugins`, or restart MiniMax, using the same data directory. Preserve user disablement during upgrades. To enable it deliberately, run the selected launcher's `plugin enable powercontext@local --json`, with the target data directory in `MINIMAX_DATA_DIR`, then rerun doctor. Restart after changing a connection that has already been used. Verify a real MCP tool result in MiniMax separately from the CLI checks.
