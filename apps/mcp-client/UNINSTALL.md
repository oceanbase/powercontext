# PowerMem — MCP client uninstall

This file removes the PowerMem MCP client integration for Claude Desktop, Cline,
**Codex**, **OpenCode**, Roo Code, Goose, or another MCP-compatible client. **Codex**
and **OpenCode** are cleaned up here (not via the VS Code extension uninstall). It is
safe to re-run: missing config files and missing `powermem` entries should be treated
as already removed.

If you are asking an AI coding agent to help, say:

> Read and follow `apps/mcp-client/UNINSTALL.md` to remove PowerMem from my MCP client.

Client-specific notes: [`../../docs/integrations/codex.md`](../../docs/integrations/codex.md), [`../../docs/integrations/opencode.md`](../../docs/integrations/opencode.md).

**AI agents:** complete **[Section 0](#0-fully-automated-uninstall-ai-agents)** in full. Do **not** ask the user to manually edit JSON or reload MCP servers unless automation failed and you are falling back to the human steps in Sections 1-4.

**Humans:** use Section 0 if an agent is available; otherwise follow Sections 1-4.

Do not delete `.env`, API keys, stored memories, virtual environments, or PowerMem data unless the user explicitly asks.

---

Remove PowerMem from one target MCP client on this machine. Clean up only the target client's `powermem` MCP server entry unless the user explicitly asks to remove every PowerMem client config. Stop backend processes only when they were started only for this client and the user confirms no other client needs them.

## 0. Fully automated uninstall (AI agents)

Run subsections **in order** (0.1 -> 0.4). Each step is idempotent.

### 0.1 — Detect target client and config path

Ask the user once which MCP client to clean up if it is not obvious from the current IDE or request.

| Client | MCP config | Remove |
|--------|------------|--------|
| Claude Desktop | `$HOME/Library/Application Support/Claude/claude_desktop_config.json` (macOS) | `mcpServers.powermem` |
| **Codex** | `~/.codex/context.json` | `mcpServers.powermem` (and `contextProviders.powermem` if present) |
| **OpenCode** | `~/.config/opencode/opencode.json`, `opencode.json`, or `.opencode/opencode.json` | `mcp.powermem` |
| Cline / Roo Code | MCP settings JSON managed by the extension | `mcpServers.powermem` |
| Generic client | The client's documented MCP JSON file | Usually `mcpServers.powermem` |

Set `POWERMEM_TARGET_CLIENT` to `codex`, `opencode`, or `generic` before running Section 0.2. Use only the selected target client unless the user explicitly asks to remove multiple clients.

**Do not** use [`apps/vscode-extension/UNINSTALL.md`](../vscode-extension/UNINSTALL.md) for Codex or OpenCode.

### 0.2 — Remove PowerMem MCP entry

Preserve every unrelated server entry. Remove only the PowerMem key for the target client.

```bash
# Codex example:
export MCP_PATH="$HOME/.codex/context.json"
export POWERMEM_TARGET_CLIENT="codex"

# OpenCode example:
export MCP_PATH="$HOME/.config/opencode/opencode.json"
export POWERMEM_TARGET_CLIENT="opencode"

python3 <<'PY'
import json
import os
from pathlib import Path

mcp_path = Path(os.path.expanduser(os.environ["MCP_PATH"]))
target = os.environ.get("POWERMEM_TARGET_CLIENT", "generic")

if not mcp_path.is_file():
    print(f"Already absent: {mcp_path}")
    raise SystemExit

data = json.loads(mcp_path.read_text())
removed = []

if target == "opencode":
    mcp = data.get("mcp")
    if isinstance(mcp, dict) and "powermem" in mcp:
        del mcp["powermem"]
        removed.append("mcp.powermem")
else:
    servers = data.get("mcpServers")
    if isinstance(servers, dict) and "powermem" in servers:
        del servers["powermem"]
        removed.append("mcpServers.powermem")
    providers = data.get("contextProviders")
    if isinstance(providers, dict) and "powermem" in providers:
        del providers["powermem"]
        removed.append("contextProviders.powermem")

if not removed:
    print("No powermem entry found")
    raise SystemExit

mcp_path.write_text(json.dumps(data, indent=4) + "\n")
print(f"Removed from {mcp_path}: {', '.join(removed)}")
PY
```

If the target client stores MCP config in an extension UI rather than a known file, remove the server named `powermem` from that client's MCP settings and leave other servers unchanged.

#### Target client: Codex

Remove `mcpServers.powermem` from `~/.codex/context.json`. If an older setup added HTTP context, also remove `contextProviders.powermem`. Restart Codex.

#### Target client: OpenCode

Remove `mcp.powermem` from the OpenCode config file you edited (`~/.config/opencode/opencode.json`, project `opencode.json`, or `.opencode/opencode.json`). Restart OpenCode or reload MCP servers.

### 0.3 - Check backend before stopping anything

Check whether a PowerMem process is still listening on `8848`:

```bash
lsof -i:8848 2>/dev/null
```

Leave the backend running when any other IDE, MCP client, extension, or workflow may still use it.

If the user confirms the MCP server was only for this client, stop it:

```bash
launchctl remove ai.powermem.mcp 2>/dev/null || true
launchctl remove ai.powermem.server 2>/dev/null || true
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID"
```

Do not remove repository files, `.venv`, `.env`, `seekdb_data`, or memories unless the user explicitly asks.

### 0.4 - Agent completion message

Report only:

1. Target client and config path checked.
2. Whether `powermem` was removed or already absent.
3. Whether the backend was left running or stopped.
4. Whether a client reload/restart is still needed.

## 1. Manual Removal

Open the target client's MCP settings and remove only the server named `powermem`.

**Codex** (`~/.codex/context.json`) — remove:

```json
"mcpServers": {
  "powermem": { "url": "http://localhost:8848/mcp" }
}
```

**OpenCode** — remove the whole `mcp.powermem` object:

```json
"mcp": {
  "powermem": {
    "type": "remote",
    "url": "http://localhost:8848/mcp",
    "enabled": true
  }
}
```

**Other clients** — typical shape before removal:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

After removal, keep the rest of `mcpServers` or `mcp` intact.

## 2. Reload Client

Reload or restart the target MCP client. Confirm the `powermem` server and PowerMem tools no longer appear.

## 3. Optional Backend Shutdown

Only stop the MCP server if no other client uses PowerMem on port `8848`:

```bash
launchctl remove ai.powermem.mcp 2>/dev/null || true
launchctl remove ai.powermem.server 2>/dev/null || true
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID"
```

For installation, see [`SETUP.md`](SETUP.md).

## 4. What Not To Remove By Default

- `.env` and API keys.
- Stored memories and `seekdb_data`.
- The local repository.
- `.venv` or installed Python packages.
- MCP entries for other clients.
