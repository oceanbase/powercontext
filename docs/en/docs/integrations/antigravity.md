---
title: Antigravity
description: Install the PowerContext Antigravity (AGY) plugin and configure automatic recall, scope-bound MCP tools, and diagnostics.
status: community
---

# Antigravity (AGY)

`community`

This integration targets Google Antigravity (`agy` CLI and Antigravity IDE) using a thin stdlib `python3` shell under `~/.gemini/config/plugins/powercontext` backed by the shared `powercontext_lifecycle` package.

## Prerequisites

Install PowerContext and Antigravity, and verify `python3` is available on `PATH`:

```bash
powercontext --version
agy --version
python3 --version
```

The plugin hooks depend only on the Python standard library (validated with `python3 -S`). Use the same PowerContext checkout for both Server and plugin so Hook contracts stay aligned.

## Install or update the plugin

```bash
set -a
. ~/.config/powercontext/.env
set +a
powercontext setup antigravity --source /path/to/powercontext
```

Setup copies the plugin to `~/.gemini/config/plugins/powercontext`, writes a `.powercontext-owned` marker, updates `powercontext.json` (`server_url`, `allow_insecure_http`, `prompt_recall`, `capture_prompts`) and `mcp_config.json`, and calls Antigravity's `RefreshMcpServers` RPC when `ANTIGRAVITY_LS_ADDRESS` and `ANTIGRAVITY_CSRF_TOKEN` are present in the environment. If `~/.gemini/config/plugins/powercontext` is a recognized development symlink, setup replaces it with the managed copy while preserving user skill customizations.

## Diagnose installation and connectivity

```bash
powercontext doctor antigravity
```

This checks the Antigravity binary/app path, managed plugin directory (`prompt_recall` and `capture_prompts` settings), stdlib Python runtime, plugin memory protocol compatibility, `hooks.json` registration with a live `python -S` smoke probe, `mcp_config.json` endpoint/credential parity, duplicate global MCP detection, hook diagnostic state, Server `/health/ready`, and background extraction schedule visibility (`/v1/capabilities`).

## Plugin lifecycle behavior

- **SessionStart (`hooks/hook.py session-start`)**: Resolves and persists the workspace/session Scope via `run_session_start`. Bootstrap context injection is opt-in (`POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_CONTEXT=true`, `false` by default).
- **PreInvocation (`hooks/hook.py pre-invocation`)**: Translates Antigravity `workspacePaths`/`lastUserInput`/`transcriptPath`, deduplicates multi-step model wakeups (`invocationNum > 0` and transcript/unindexed step state), delegates recall and prompt capture (`run_user_prompt_submit`) to `powercontext_lifecycle`, and emits prepared context/diagnostics in `injectSteps` without altering the Server's trust envelope. Enable background Memory extraction on the Server via `POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60` in `~/.config/powercontext/.env`.
- **PreToolUse (`hooks/bind_tools.py`)**: Matches `call_mcp_tool|mcp_powercontext.*` and rewrites `scope_id` (`overwrite`) to the bound Antigravity workspace Scope while preserving `"decision": "ask"` host permission rules.
- **Gateway toggle (`prompt_recall`)**: When model traffic goes through the PowerContext Gateway, disable prompt-time hook recall via `powercontext setup antigravity --no-prompt-recall`, `POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL=false`, or `"prompt_recall": false` in `powercontext.json`.
