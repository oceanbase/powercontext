# PowerMem — VS Code extension uninstall

This file removes the PowerMem VS Code extension integration. It is safe to re-run: missing extensions, settings, and config entries should be treated as already removed.

If you are asking an AI coding agent to help, say:

> Read and follow `apps/vscode-extension/UNINSTALL.md` to remove the PowerMem VS Code extension integration.

**AI agents:** complete **[Section 0](#0-fully-automated-uninstall-ai-agents)** in full. Do **not** ask the user to open the Extensions view, edit JSON files, or run `/mcp reload` unless automation failed and you are falling back to the human steps in Sections 1–6.

**Humans:** use Section 0 if an agent is available; otherwise follow Sections 1–6 (manual path).

Do not delete `.env`, API keys, or stored memories unless the user explicitly asks.

---

Remove the PowerMem VS Code extension integration from this machine. Follow the
reverse of [`SETUP.md`](SETUP.md): remove or disable the extension first, then clean
up the current IDE/client MCP config, then optionally stop backend processes that
were started only for this setup. Clean up only the current IDE or target client
unless the user explicitly asks to remove every PowerMem client config. Report what
was removed and what was already absent.

## 0. Fully automated uninstall (AI agents)

Run subsections **in order** (0.1 → 0.5). Each step is idempotent: treat missing
extensions, settings, and config entries as already removed.

**AI agents:** complete **all** steps below before telling the user to restart the IDE.
Do **not** ask the user to open the Extensions view, edit JSON, or run `/mcp reload`
unless automation failed.

### 0.1 — Detect IDE and config paths

| Signal | Current IDE |
|--------|-------------|
| `qoder` on `PATH` or Qoder user data dir exists | **Qoder** |
| `cursor` on `PATH` and agent runs inside Cursor | **Cursor** |
| `code` on `PATH`, not Cursor | **VS Code** |
| Otherwise | Ask the user once |

| Config | Qoder path (macOS) |
|--------|---------------------|
| Extension directory | `~/.qoder/extensions/oceanbase.powermem-vscode-0.1.0/` |
| Extension registry | `~/.qoder/extensions/extensions.json` |
| User settings | `$HOME/Library/Application Support/Qoder/User/settings.json` |
| MCP config | `$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` |

### 0.2 — Remove the extension

**Qoder** (no CLI — use Python):

```bash
python3 <<'PY'
import json, shutil
from pathlib import Path

ext_id = "OceanBase.powermem-vscode"

# 1. Remove from extensions.json
ext_json = Path.home() / ".qoder/extensions/extensions.json"
if ext_json.exists():
    extensions = json.loads(ext_json.read_text())
    before = len(extensions)
    extensions = [e for e in extensions if e.get("identifier", {}).get("id") != ext_id]
    ext_json.write_text(json.dumps(extensions, indent=2) + "\n")
    print(f"extensions.json: removed {before - len(extensions)} entry" if before > len(extensions) else "extensions.json: not registered")

# 2. Remove extension directory
ext_dir = Path.home() / ".qoder/extensions/oceanbase.powermem-vscode-0.1.0"
if ext_dir.exists():
    shutil.rmtree(ext_dir)
    print(f"Removed: {ext_dir}")
else:
    print(f"Already absent: {ext_dir}")
PY
```

**Cursor:** `cursor --uninstall-extension OceanBase.powermem-vscode`
**VS Code:** `code --uninstall-extension OceanBase.powermem-vscode`

### 0.3 — Remove extension settings

Remove all `powermem.*` keys from the IDE user settings file:

```bash
# Adjust SETTINGS for the current IDE (see table in 0.1)
export SETTINGS="$HOME/Library/Application Support/Qoder/User/settings.json"

python3 <<'PY'
import json, os
path = os.path.expanduser(os.environ["SETTINGS"])
if not os.path.isfile(path):
    print(f"Not found: {path}"); raise SystemExit
with open(path) as f:
    data = json.load(f)
removed = [k for k in list(data.keys()) if k.startswith("powermem.")]
for k in removed:
    del data[k]
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
print(f"Removed {len(removed)} powermem settings: {removed}" if removed else "No powermem settings")
PY
```

### 0.4 — Remove MCP config

Remove `mcpServers.powermem` from the IDE MCP config file:

```bash
# Adjust MCP_PATH for the current IDE
# Qoder: "$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"
# Cursor: "$HOME/.cursor/mcp.json"
export MCP_PATH="$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"

python3 <<'PY'
import json, os
from pathlib import Path
mcp_path = Path(os.path.expanduser(os.environ["MCP_PATH"]))
if not mcp_path.is_file():
    print(f"Not found: {mcp_path}"); raise SystemExit
data = json.loads(mcp_path.read_text())
mcps = data.get("mcpServers", {})
if "powermem" in mcps:
    del mcps["powermem"]
    data["mcpServers"] = mcps
    mcp_path.write_text(json.dumps(data, indent=4) + "\n")
    print("Removed powermem from MCP config")
else:
    print("No powermem MCP server in config")
PY
```

### 0.5 — Check backend and report

Check whether the backend on port `8848` is still in use by other clients:

```bash
lsof -i:8848 2>/dev/null
```

- If other IDEs (Cursor, VS Code, etc.) are connected, **leave the backend running**.
- If no other client is using it and the user confirms, stop it:

```bash
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID"
```

**Report** to the user:

1. Extension: removed or already absent.
2. Settings: which `powermem.*` keys were removed (or none found).
3. MCP config: `powermem` server removed (or not present).
4. Backend: still running (shared by other clients) or stopped.
5. **Restart the IDE once** to fully apply removal.

---

## 1. Disable or Uninstall the Extension

Do this first. The PowerMem commands and status bar are registered by the extension
from `src/extension.ts`; removing the extension removes those UI entry points.

### Extension Development Host from source

If setup used `npm run compile` and `F5`, close the Extension Development Host
window. Nothing is installed into the original IDE unless a VSIX was also installed.

Optionally clean local build output only if you do not need it:

```bash
rm -rf apps/vscode-extension/out
```

### Qoder

Qoder has no CLI for extension management. The extension is installed at
`~/.qoder/extensions/oceanbase.powermem-vscode-{version}/` and registered in
`~/.qoder/extensions/extensions.json`.

**Agent automation (preferred):**

```bash
python3 <<'PY'
import json, shutil
from pathlib import Path

ext_id = "OceanBase.powermem-vscode"

# 1. Remove from extensions.json
ext_json = Path.home() / ".qoder/extensions/extensions.json"
if ext_json.exists():
    extensions = json.loads(ext_json.read_text())
    before = len(extensions)
    extensions = [e for e in extensions if e.get("identifier", {}).get("id") != ext_id]
    ext_json.write_text(json.dumps(extensions, indent=2) + "\n")
    print(f"extensions.json: removed {before - len(extensions)} entry" if before > len(extensions) else "extensions.json: not registered")

# 2. Remove extension directory
ext_dir = Path.home() / ".qoder/extensions/oceanbase.powermem-vscode-0.1.0"
if ext_dir.exists():
    shutil.rmtree(ext_dir)
    print(f"Removed: {ext_dir}")
else:
    print(f"Already absent: {ext_dir}")
PY
```

**Manual uninstall:**
1. Open Qoder → Extensions view (`Cmd+Shift+X`).
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Restart Qoder.

### Installed VSIX or marketplace-style install (VS Code / Cursor)

In VS Code or Cursor:

1. Open the Extensions view.
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Reload the window or restart the IDE.

If installed from VSIX through the CLI, remove it with:

```bash
code --uninstall-extension OceanBase.powermem-vscode
```

If the exact extension id differs in your local build, use the Extensions view or `code --list-extensions` to identify it.

## 2. Remove Extension Settings

Open user settings JSON and remove the `powermem.*` keys you no longer want. These
settings belong to the extension UI and backend connection, not directly to MCP
client config:

```json
{
  "powermem.enabled": true,
  "powermem.backendUrl": "http://localhost:8848",
  "powermem.apiKey": "",
  "powermem.connectionMode": "mcp",
  "powermem.mcpServerPath": "",
  "powermem.userId": "",
  "powermem.projectName": "",
  "powermem.seamlessMode": true
}
```

**Settings file location by IDE:**

| IDE | Settings path (macOS) |
|-----|----------------------|
| Qoder | `$HOME/Library/Application Support/Qoder/User/settings.json` |
| Cursor | `$HOME/Library/Application Support/Cursor/User/settings.json` |
| VS Code | `$HOME/Library/Application Support/Code/User/settings.json` |

**Agent automation (all IDEs):** use Python to merge-remove:

```bash
# Set SETTINGS to the correct path for the current IDE
export SETTINGS="$HOME/Library/Application Support/Qoder/User/settings.json"

python3 <<'PY'
import json, os
path = os.path.expanduser(os.environ["SETTINGS"])
if not os.path.isfile(path):
    print(f"Not found: {path}")
    raise SystemExit
with open(path) as f:
    data = json.load(f)
removed = [k for k in list(data.keys()) if k.startswith("powermem.")]
for k in removed:
    del data[k]
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
print(f"Removed {len(removed)} powermem settings: {removed}")
PY
```

Leave settings in place if you only disabled the extension temporarily.

## 3. Remove Current IDE or Target-Client MCP Config

Remove only the config that matches the IDE/client you set up.

### Current IDE: VS Code

If you only used the extension UI in VS Code, there may be no external AI-tool config to remove. Removing the extension and optional `powermem.*` settings is enough.

### Current IDE: Cursor

Remove only `mcpServers.powermem` from:

```text
~/.cursor/mcp.json
```

This removes either setup shape:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

or MCP-only streamable HTTP mode from setup:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

or local stdio fallback:

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

Leave other MCP servers untouched. After editing `~/.cursor/mcp.json`, use
**Developer: Reload Window** or restart Cursor so the failed or removed MCP client is
recreated.

### Current IDE: Qoder

Qoder stores MCP config at:

```text
$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json
```

Remove `mcpServers.powermem` from that file. This removes either setup shape:

Streamable HTTP (preferred):

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
        "POWERMEM_ENV_FILE": "/path/to/repo/.env"
      }
    }
  }
}
```

**Agent automation (preferred):**

```bash
python3 <<'PY'
import json
from pathlib import Path

mcp_path = Path.home() / "Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"
if not mcp_path.is_file():
    print(f"Not found: {mcp_path}")
    raise SystemExit

data = json.loads(mcp_path.read_text())
mcps = data.get("mcpServers", {})
if "powermem" in mcps:
    del mcps["powermem"]
    data["mcpServers"] = mcps
    mcp_path.write_text(json.dumps(data, indent=4) + "\n")
    print("Removed powermem from Qoder MCP config")
else:
    print("No powermem MCP server in Qoder config")
PY
```

**Manual removal:** open the MCP config file above, delete the `powermem` key from `mcpServers`, leave other servers untouched. Restart Qoder or run `/mcp reload`.

Leave other MCP servers untouched. After editing, restart Qoder so the removed MCP client is cleaned up.

### Codex, OpenCode, and generic MCP clients

Codex and OpenCode are not managed by the VS Code extension uninstall flow. Use
the generic MCP client cleanup instead:

```text
Read and follow apps/mcp-client/UNINSTALL.md to remove PowerMem from this MCP client.
```

### Other supported clients

If you configured another client, remove only that client's PowerMem entry:

| Client | Config location | Removal |
|--------|-----------------|---------|
| Claude Desktop / Code MCP provider | `~/.claude/providers/powermem.json` | Delete this file if it only contains PowerMem |
| Windsurf | `~/.windsurf/context/powermem.json` | Delete this file |
| GitHub Copilot | `~/.github/copilot/powermem.json` | Delete this file |

If you used **PowerMem: Link to AI Tools** and intentionally wrote all supported configs, review the table above and remove each PowerMem entry the user approves. Before editing shared JSON files, back them up or show the user the exact diff. Do not remove other clients' MCP servers or context providers.

## 4. Optional: Stop PowerMem Processes

Only stop the backend if it was started solely for this setup and no other IDE/client is using it. Another IDE may be sharing the same `powermem-server`, so check before stopping anything.

For the HTTP API server on port `8848`, stop it only if no extension UI, dashboard,
or other integration is using `http://localhost:8848/api/v1/system/health`:

```bash
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID"
```

If setup used `launchctl submit` (macOS), also remove the launchd job:

```bash
launchctl remove ai.powermem.server 2>/dev/null || true
```

MCP-only streamable HTTP mode also uses port `8848` in the setup flow, so the same
port check above applies. If the backend is used by VS Code, Cursor, Qoder,
OpenCode, Claude Code, or another MCP client, leave it running.

## 5. Optional: remove package data

Do not delete these without explicit confirmation:

- `.env` or any file containing LLM/API secrets.
- `seekdb_data/`, `sqlite_data/`, or other memory storage.
- PowerMem Python packages used by other integrations.

## 6. Verify Removal

- Reload or restart the current IDE and confirm the PowerMem status bar item or MCP
  server entry is gone.
- Confirm only the target AI tool no longer lists a `powermem` MCP server or context provider.
- If the backend was stopped, confirm the health endpoint no longer answers:

```bash
curl -s -m 3 http://localhost:8848/api/v1/system/health >/dev/null && echo "still running" || echo "stopped"
```

**For Qoder**, run these checks after restart:

1. Extension gone: no "PowerMem" status bar item in the bottom-right area.
2. Commands gone: open command palette (`Cmd+Shift+P`), search "PowerMem" — no results.
3. MCP gone: open MCP settings, confirm `powermem` is no longer listed.
4. Extension directory absent: `ls ~/.qoder/extensions/ | grep powermem` returns nothing.
5. `extensions.json` clean: `python3 -c "import json; exts=json.load(open('$HOME/.qoder/extensions/extensions.json')); print([e['identifier']['id'] for e in exts])"` does not include `OceanBase.powermem-vscode`.

For MCP-only fallback, confirm the target client no longer lists `powermem` after
reload.

For installation, see [`SETUP.md`](SETUP.md).
