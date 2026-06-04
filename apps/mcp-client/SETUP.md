# PowerMem — MCP client setup

This file is a setup checklist for humans and a prompt for AI coding agents. Use it for **Claude Desktop**, **Cline**, **Codex**, **OpenCode**, Roo Code, Goose, or any other MCP-compatible client. **Codex** and **OpenCode** belong here (not the VS Code extension flow); see [`../../docs/integrations/codex.md`](../../docs/integrations/codex.md) and [`../../docs/integrations/opencode.md`](../../docs/integrations/opencode.md).

If you are asking an AI coding agent to help, say:

> Read and follow `apps/mcp-client/SETUP.md` to set up PowerMem for my MCP client end-to-end with no manual steps.

First clone the repo and enter it (skip if you are already in the PowerMem checkout):

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

The procedure is idempotent: re-running it should reuse an existing healthy `powermem-mcp` server and update the target client's `powermem` MCP entry instead of duplicating it.

---

**AI agents:** complete **[Section 0](#0-fully-automated-setup-ai-agents)** in full. Do **not** ask the user to manually edit JSON, reload MCP servers, or restart the client unless automation failed and you are falling back to the human steps in Sections 1-5.

**Humans:** use Section 0 if an agent is available; otherwise follow Sections 1-5.

Set up PowerMem for one target MCP client on this machine. Use `powermem-mcp` directly, prefer SSE on port `8848`, and fall back to streamable HTTP or local stdio only when the target client requires it. Ask the user before editing secrets, API keys, or unrelated client config files.

## 0. Fully automated setup (AI agents)

Run subsections **in order** (0.1 -> 0.6).

Goal: PowerMem is reachable from the target MCP client as server name `powermem`, and tools such as `add_memory` and `search_memories` are visible after the client reloads.

### 0.1 — Detect target client and paths

Ask the user once which MCP client to configure if it is not obvious from the current IDE or request.

| Signal | Likely client |
|--------|----------------|
| User runs **Codex** or `~/.codex/context.json` exists | **Codex** |
| User runs **OpenCode** or `opencode.json` / `~/.config/opencode/` exists | **OpenCode** |
| Claude Desktop app | **Claude Desktop** |
| Cline extension in VS Code | **Cline** |
| Otherwise | Ask once |

Common config locations:

| Client | MCP config | Config key |
|--------|------------|------------|
| Claude Desktop | `$HOME/Library/Application Support/Claude/claude_desktop_config.json` (macOS) | `mcpServers.powermem` |
| **Codex** | `~/.codex/context.json` | `mcpServers.powermem` |
| **OpenCode** | `~/.config/opencode/opencode.json`, `opencode.json`, or `.opencode/opencode.json` | `mcp.powermem` |
| Cline / Roo Code | MCP settings JSON managed by the extension | `mcpServers.powermem` |
| Generic client | The client's documented MCP JSON file | Usually `mcpServers.powermem` |

Set `POWERMEM_TARGET_CLIENT` to `codex`, `opencode`, or `generic` before running the merge helper in Section 0.4. Use only the selected target client unless the user explicitly asks to configure multiple clients.

**Do not** use [`apps/vscode-extension/SETUP.md`](../vscode-extension/SETUP.md) for Codex or OpenCode — that path is for VS Code compatible IDEs (Cursor, VS Code, Windsurf, GitHub Copilot, Qoder).

### 0.2 - Prepare repo, environment, and package

Run from this repository:

```bash
export REPO_ROOT="$(pwd)"
export POWERMEM_MCP_URL="http://localhost:8848/mcp"
test -f "$REPO_ROOT/.env" || { echo "Missing $REPO_ROOT/.env"; exit 1; }
grep -qE '^LLM_PROVIDER=.+' "$REPO_ROOT/.env" || exit 1
grep -qE '^LLM_MODEL=.+' "$REPO_ROOT/.env" || exit 1
```

If `.env` is missing or incomplete, stop and ask the user for the provider/model/API key details needed by PowerMem. Do not invent secrets or overwrite existing keys.

Install PowerMem locally when commands are missing:

```bash
cd "$REPO_ROOT"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
if command -v uv >/dev/null 2>&1; then
  uv pip install -e ".[mcp,cli]"
else
  python -m pip install -U pip setuptools wheel
  python -m pip install -e ".[mcp,cli]"
fi
```

### 0.3 - Prefer reusable `powermem-mcp` SSE

Probe port `8848` before starting or killing anything:

```bash
probe_powermem_mcp_sse() {
  curl -sf -m 5 -H 'Accept: text/event-stream' "$POWERMEM_MCP_URL" >/dev/null || return 1
}

if probe_powermem_mcp_sse; then
  echo "Existing powermem-mcp SSE server is healthy."
  export POWERMEM_MCP_MODE="sse"
else
  export POWERMEM_MCP_MODE=""
fi
```

If the probe fails, start or replace the MCP server on the same port. Keep one PowerMem MCP process on `8848`; embedded storage allows one process at a time.

```bash
if [ -z "$POWERMEM_MCP_MODE" ]; then
  lsof -ti:8848 | xargs kill -9 2>/dev/null || true
  pkill -f 'powermem-mcp.*8848' 2>/dev/null || true
  sleep 2

  cd "$REPO_ROOT"
  source .venv/bin/activate
  export PMEM_MCP="$(command -v powermem-mcp || echo "$REPO_ROOT/.venv/bin/powermem-mcp")"

  if [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
    launchctl remove ai.powermem.mcp 2>/dev/null || true
    launchctl submit -l ai.powermem.mcp \
      -o /tmp/powermem-mcp.launchd.log \
      -e /tmp/powermem-mcp.launchd.err \
      -- /bin/zsh -lc "cd '$REPO_ROOT' && export POWERMEM_ENV_FILE='$REPO_ROOT/.env' && exec '$PMEM_MCP' sse 8848"
  else
    POWERMEM_ENV_FILE="$REPO_ROOT/.env" nohup "$PMEM_MCP" sse 8848 \
      >> /tmp/powermem-mcp.log 2>&1 &
  fi

  for i in $(seq 1 90); do
    probe_powermem_mcp_sse && { export POWERMEM_MCP_MODE="sse"; break; }
    sleep 2
  done
fi
```

If SSE cannot be used by the target client, choose one fallback:

- Use streamable HTTP when the client needs remote MCP but does not support SSE.
- Use local stdio when the client can launch a command and does not need a long-running HTTP server.

```bash
# Optional streamable HTTP fallback:
# export POWERMEM_MCP_MODE="streamable-http"
# POWERMEM_ENV_FILE="$REPO_ROOT/.env" "$PMEM_MCP" streamable-http 8848

# Optional stdio fallback:
# export POWERMEM_MCP_MODE="stdio"
```

### 0.4 - Write the target MCP config

Use server name `powermem`.

For SSE (preferred):

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

For streamable HTTP fallback, use the same URL shape after starting `powermem-mcp streamable-http 8848`.

For local stdio:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"],
      "env": {
        "POWERMEM_ENV_FILE": "/absolute/path/to/powermem/.env"
      }
    }
  }
}
```

When editing JSON, preserve unrelated servers and replace only `mcpServers.powermem`.
For OpenCode, preserve unrelated entries and replace only `mcp.powermem` instead:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "remote",
      "url": "http://localhost:8848/mcp",
      "enabled": true
    }
  }
}
```

Client-aware merge helper (set `MCP_PATH`, `POWERMEM_TARGET_CLIENT`, and `REPO_ROOT` first):

```bash
# Codex example:
export MCP_PATH="$HOME/.codex/context.json"
export POWERMEM_TARGET_CLIENT="codex"

# OpenCode example (global config):
export MCP_PATH="$HOME/.config/opencode/opencode.json"
export POWERMEM_TARGET_CLIENT="opencode"

python3 <<'PY'
import json
import os
from pathlib import Path

mcp_path = Path(os.path.expanduser(os.environ["MCP_PATH"]))
target = os.environ.get("POWERMEM_TARGET_CLIENT", "generic")
mode = os.environ.get("POWERMEM_MCP_MODE", "sse")
repo_root = os.environ.get("REPO_ROOT", "")
mcp_url = "http://localhost:8848/mcp"

mcp_path.parent.mkdir(parents=True, exist_ok=True)
data = {}
if mcp_path.is_file():
    data = json.loads(mcp_path.read_text())

if target == "opencode":
    mcp = data.setdefault("mcp", {})
    if mode in {"sse", "streamable-http"}:
        mcp["powermem"] = {
            "type": "remote",
            "url": mcp_url,
            "enabled": True,
        }
    else:
        mcp["powermem"] = {
            "type": "local",
            "command": ["powermem-mcp", "stdio"],
            "enabled": True,
        }
        if repo_root:
            mcp["powermem"]["environment"] = {"POWERMEM_ENV_FILE": f"{repo_root}/.env"}
else:
    servers = data.setdefault("mcpServers", {})
    if mode in {"sse", "streamable-http"}:
        servers["powermem"] = {"url": mcp_url}
    else:
        entry = {
            "command": "powermem-mcp",
            "args": ["stdio"],
        }
        if repo_root:
            entry["env"] = {"POWERMEM_ENV_FILE": f"{repo_root}/.env"}
        servers["powermem"] = entry

mcp_path.write_text(json.dumps(data, indent=4) + "\n")
print(f"Updated {mcp_path} for {target} (mode={mode})")
PY
```

#### Target client: Codex

1. Set `MCP_PATH=~/.codex/context.json` and `POWERMEM_TARGET_CLIENT=codex`.
2. Run the merge helper above (remote URL preferred when SSE/streamable HTTP is healthy).
3. Restart Codex so it reloads `~/.codex/context.json`.
4. Probe: add memory `PowerMem Codex probe: dragonfruit-zx9`, then search for `dragonfruit-zx9`.

Manual reference: [`../../docs/integrations/codex.md`](../../docs/integrations/codex.md).

#### Target client: OpenCode

1. Pick the config file the user actually uses (`~/.config/opencode/opencode.json` for global setup).
2. Set `POWERMEM_TARGET_CLIENT=opencode` and run the merge helper.
3. Restart OpenCode or reload MCP servers.
4. Probe: add memory `PowerMem OpenCode probe: dragonfruit-zx9`, then search for `dragonfruit-zx9`.

For local-only OpenCode, prefer stdio (`type: local`) when remote MCP is unavailable. Manual reference: [`../../docs/integrations/opencode.md`](../../docs/integrations/opencode.md).

### 0.5 - Verify

Verify the MCP server process is listening:

```bash
lsof -i:8848 2>/dev/null
```

(The SSE transport keeps the connection open, so `curl` will hang rather than report success. A listening socket on `8848` confirmed by `lsof` is the correct health signal.)

Then reload or restart only the target MCP client and confirm:

1. The `powermem` server is connected.
2. Tools are visible: `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, `list_memories`.
3. A probe memory can be added and searched from the client.

### 0.6 - Agent completion message

Report only:

1. Target client and config path updated.
2. MCP mode: SSE, streamable HTTP, or stdio.
3. MCP URL or stdio command.
4. Whether a client reload/restart is still needed.

## 1. Prerequisites

- PowerMem repository is available locally.
- `.env` contains real LLM provider, model, and API key values.
- The selected MCP client supports SSE, streamable HTTP, or local stdio MCP.

## 2. Choose Transport

Prefer SSE on port `8848`:

```bash
powermem-mcp sse 8848
```

Use the standard URL shape in the MCP client:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

Use stdio when the client launches local commands:

```bash
powermem-mcp stdio
```

Use streamable HTTP when the client expects that transport instead of SSE:

```bash
powermem-mcp streamable-http 8848
```

## 3. Configure Client

Add or replace only the `powermem` entry. Most clients use `mcpServers.powermem`; **OpenCode** uses `mcp.powermem`.

### Codex (`~/.codex/context.json`)

Remote MCP (preferred when `powermem-mcp` is on port `8848`):

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

Local stdio fallback:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"],
      "env": {
        "POWERMEM_ENV_FILE": "/absolute/path/to/powermem/.env"
      }
    }
  }
}
```

### OpenCode (`opencode.json`)

Remote MCP:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "remote",
      "url": "http://localhost:8848/mcp",
      "enabled": true
    }
  }
}
```

Local stdio:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "local",
      "command": ["powermem-mcp", "stdio"],
      "enabled": true,
      "environment": {
        "POWERMEM_ENV_FILE": "/absolute/path/to/powermem/.env"
      }
    }
  }
}
```

### Other MCP clients (`mcpServers`)

SSE or streamable HTTP:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

Stdio:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"]
    }
  }
}
```

## 4. Verify

Reload the MCP client, confirm the `powermem` server connects, and verify the memory tools are listed.

## 5. Troubleshooting

- If SSE fails, run `powermem-mcp sse 8848` in a terminal and inspect stderr.
- If streamable HTTP fails, run `powermem-mcp streamable-http 8848` and confirm the client URL matches the port.
- If stdio fails, run `powermem-mcp stdio` in a terminal and inspect stderr.
- If memory operations fail, check `.env`, provider credentials, embedding configuration, and `/tmp/powermem-mcp.log` or `/tmp/powermem-mcp.launchd.err`.
- If port `8848` is busy, do not start a second PowerMem MCP process against the same data directory. Reuse or replace the existing process.
- **Codex:** validate `~/.codex/context.json` is valid JSON and contains `mcpServers.powermem`; restart Codex after edits.
- **OpenCode:** validate the `mcp.powermem` block (not `mcpServers`); use `type: remote` + URL or `type: local` + command per [OpenCode MCP docs](https://opencode.ai/docs/mcp-servers/).

## Uninstall

To remove PowerMem from the target MCP client, follow [`UNINSTALL.md`](UNINSTALL.md).
