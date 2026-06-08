# PowerMem — VS Code extension uninstall

This file removes the PowerMem VS Code extension integration. It is safe to re-run: missing extensions, settings, and config entries should be treated as already removed.

If you are asking an AI coding agent to help, say:

> Read and follow `apps/vscode-extension/UNINSTALL.md` to remove the PowerMem VS Code extension integration.

**AI agents:** complete **[Section 0](#0-fully-automated-uninstall-ai-agents)** in full. Do **not** ask the user to open the Extensions view, edit JSON files, or run `/mcp reload` unless automation failed and you are falling back to the human steps in Sections 1–6.

**Humans:** use Section 0 if an agent is available; otherwise follow Sections 1–6 (manual path).

Do not delete `.env`, API keys, or stored memories unless the user explicitly asks.

---

Supported IDEs (priority order): **Cursor → VS Code → Qoder → CodeFuse**. Each has
its own extension, settings, and MCP cleanup. Follow the reverse of
[`SETUP.md`](SETUP.md): remove the extension, clean up settings and MCP config,
then optionally stop or fully uninstall the backend service. Clean up only the
current IDE unless the user explicitly asks to remove every PowerMem client config.

## 0. Fully automated uninstall (AI agents)

**Flow:** [0.1](#01--detect-os-and-ide) detect → [shared helpers](#shared-helpers-0205) →
**one IDE block** in priority order ([0.2](#02--cursor-uninstall) Cursor →
[0.3](#03--vs-code-uninstall) VS Code → [0.4](#04--qoder-uninstall) Qoder →
[0.5](#05--codefuse-uninstall) CodeFuse) → [0.6](#06--check-backend-usage) backend check →
[0.7](#07--ask-user-stop-or-uninstall-backend-service) user choice → [0.8](#08--report) report.

Each step is idempotent: treat missing extensions, settings, and config entries as already removed.

**AI agents:** complete **all** steps before telling the user to restart the IDE.
Do **not** ask the user to open the Extensions view, edit JSON, or run `/mcp reload`
unless automation failed. **Do** ask the user once in [0.7](#07--ask-user-stop-or-uninstall-backend-service)
about backend/service cleanup — do not skip that question.

### 0.1 — Detect OS and IDE

Automated uninstall is supported on **macOS** and **Linux**. On Windows, use the
IDE UI/manual removal path and do not run the service commands below.

```bash
EXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/../.." && pwd)"
OS_NAME="$(uname -s)"
case "$OS_NAME" in
  Darwin) POWERMEM_OS=macos ;;
  Linux)  POWERMEM_OS=linux ;;
  *)      echo "Unsupported automated uninstall OS: $OS_NAME"; exit 1 ;;
esac
export EXT_DIR REPO_ROOT POWERMEM_OS
```

Configure **one current IDE** unless the user explicitly asks for every client.
Detect the IDE (priority: env vars → active process → CLI), then run its matching
subsection:

| IDE | Detection signal | Run |
|-----|------------------|-----|
| Cursor | `$CURSOR` set, agent runs inside Cursor, or `cursor` on `PATH` | [0.2](#02--cursor-uninstall) |
| VS Code | `$VSCODE_PID` set (not Cursor), or `code` on `PATH` without Cursor | [0.3](#03--vs-code-uninstall) |
| Qoder | `$QODER_IDE`/`$QODER_AGENT`/`$QODER` set, Qoder user data dir + active process, or `qoder` on `PATH` | [0.4](#04--qoder-uninstall) |
| CodeFuse | `$CODEFUSE` set, CodeFuse user data dir + active process, or `codefuse` on `PATH` | [0.5](#05--codefuse-uninstall) |
| Unknown | none of the above | Ask the user once |

```bash
detect_current_ide() {
  # Priority 1: Environment variables (most reliable - set by the IDE itself)
  # Qoder-specific: QODER_IDE and QODER_AGENT are set by Qoder even though $QODER is not
  [ -n "${QODER_IDE:-}" ] && { echo qoder; return; }
  [ -n "${QODER_AGENT:-}" ] && { echo qoder; return; }
  [ -n "${QODER:-}" ] && { echo qoder; return; }
  [ -n "${CURSOR:-}" ] && { echo cursor; return; }
  [ -n "${VSCODE_PID:-}" ] && [ -z "${CURSOR:-}" ] && { echo vscode; return; }
  [ -n "${CODEFUSE:-}" ] && { echo codefuse; return; }

  # Priority 2: Walk parent process chain to find the spawning IDE
  # This is definitive: traces the current shell's ancestors to the IDE that launched it
  _pid=$$
  while [ "$_pid" -gt 1 ] 2>/dev/null; do
    _ppid=$(ps -p "$_pid" -o ppid= 2>/dev/null | tr -d ' ')
    [ -z "$_ppid" ] && break
    _cmd=$(ps -p "$_ppid" -o command= 2>/dev/null)
    case "$_cmd" in
      *Qoder.app*|*/Qoder|*qoder*) echo qoder; return ;;
      *Cursor.app*|*/Cursor|*cursor*) echo cursor; return ;;
      *CodeFuse*|*codefuse*) echo codefuse; return ;;
      *"/Code "*|*/Code|*"Code Helper"*) echo vscode; return ;;
    esac
    [ "$_ppid" -le 1 ] 2>/dev/null && break
    _pid=$_ppid
  done

  # Priority 3: Active process + user data dir (fallback, priority: Cursor → VS Code → Qoder → CodeFuse)
  if { [ -d "$HOME/Library/Application Support/Cursor" ] || [ -d "$HOME/.config/Cursor" ]; } \
     && pgrep -f "Cursor" >/dev/null 2>&1; then echo cursor; return; fi
  if { [ -d "$HOME/Library/Application Support/Code" ] || [ -d "$HOME/.config/Code" ]; } \
     && pgrep -f "/Code" >/dev/null 2>&1 && ! pgrep -f "Cursor" >/dev/null 2>&1; then echo vscode; return; fi
  if [ -d "$HOME/.qoder" ] && pgrep -f "Qoder" >/dev/null 2>&1; then echo qoder; return; fi
  if { [ -d "$HOME/.codefuse" ] || [ -d "$HOME/Library/Application Support/CodeFuse" ]; } \
     && pgrep -f "CodeFuse" >/dev/null 2>&1; then echo codefuse; return; fi

  # Priority 4: CLI fallback (same priority order)
  command -v cursor >/dev/null 2>&1 && { echo cursor; return; }
  command -v code >/dev/null 2>&1 && { echo vscode; return; }
  command -v qoder >/dev/null 2>&1 && { echo qoder; return; }
  command -v codefuse >/dev/null 2>&1 && { echo codefuse; return; }

  echo unknown
}

POWERMEM_IDE="$(detect_current_ide)"
echo "Detected IDE: $POWERMEM_IDE"
export POWERMEM_IDE
```

**AI agents:** if detection returns `unknown`, ask the user once. If the user says
"all", run [0.2](#02--cursor-uninstall) through [0.5](#05--codefuse-uninstall) in order.

### Shared helpers (0.2–0.5)

IDE blocks below call these helpers. Each helper is idempotent.

#### Remove `powermem.*` settings

Set `POWERMEM_IDE` to `Cursor`, `Code`, `Qoder`, or `CodeFuse` before running:

```bash
remove_powermem_settings() {
  POWERMEM_IDE="$1" python3 <<'PY'
import json, os, platform

ide = os.environ["POWERMEM_IDE"]
if platform.system() == "Darwin":
    base = os.path.expanduser(f"~/Library/Application Support/{ide}/User")
else:
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    base = os.path.join(xdg, f"{ide}/User")
path = os.path.join(base, "settings.json")

if not os.path.isfile(path):
    print(f"Not found: {path}")
    raise SystemExit(0)

with open(path) as f:
    data = json.load(f)
removed = [k for k in list(data.keys()) if k.startswith("powermem.")]
for k in removed:
    del data[k]
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
print(f"Removed {len(removed)} powermem settings: {removed}" if removed else "No powermem settings found")
PY
}
```

#### Remove `mcpServers.powermem`

Set `POWERMEM_MCP_PATH` to the MCP config file path:

```bash
remove_powermem_mcp() {
  POWERMEM_MCP_PATH="$1" python3 <<'PY'
import json, os
from pathlib import Path

mcp_path = Path(os.environ["POWERMEM_MCP_PATH"])
if not mcp_path.is_file():
    print(f"Not found: {mcp_path}")
    raise SystemExit(0)

data = json.loads(mcp_path.read_text())
mcps = data.get("mcpServers", {})
if "powermem" in mcps:
    del mcps["powermem"]
    data["mcpServers"] = mcps
    mcp_path.write_text(json.dumps(data, indent=4) + "\n")
    print(f"Removed powermem from {mcp_path}")
else:
    print(f"No powermem MCP server in {mcp_path}")
PY
}
```

#### Remove shared VS Code extension cache

Run **once** after any IDE uninstall to prevent cross-IDE pollution:

```bash
remove_shared_vscode_cache() {
  rm -rf ~/.vscode/extensions/oceanbase.powermem-vscode-* 2>/dev/null || true
  echo "Shared ~/.vscode/extensions cache cleaned"
}
```

### 0.2 — Cursor uninstall

Run when `POWERMEM_IDE=cursor` or user chose Cursor.

#### 0.2.1 — Remove extension

```bash
cursor --uninstall-extension OceanBase.powermem-vscode 2>/dev/null || true
remove_shared_vscode_cache
```

#### 0.2.2 — Remove settings

```bash
remove_powermem_settings Cursor
```

#### 0.2.3 — Remove MCP config

```bash
remove_powermem_mcp "$HOME/.cursor/mcp.json"
```

Leave other MCP servers untouched. Restart Cursor after removal.

### 0.3 — VS Code uninstall

Run when `POWERMEM_IDE=vscode` or user chose VS Code.

#### 0.3.1 — Remove extension

```bash
code --uninstall-extension OceanBase.powermem-vscode 2>/dev/null || true
remove_shared_vscode_cache
```

#### 0.3.2 — Remove settings

```bash
remove_powermem_settings Code
```

#### 0.3.3 — Remove MCP config (if applicable)

VS Code usually has no external MCP config for the extension UI. If workspace/user
MCP was configured separately, remove `mcpServers.powermem` from the relevant file.

### 0.4 — Qoder uninstall

Run when `POWERMEM_IDE=qoder` or user chose Qoder. Qoder has no extension CLI.

#### 0.4.1 — Remove extension (directory + extensions.json)

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
    removed_count = before - len(extensions)
    print(f"extensions.json: removed {removed_count} entry" if removed_count else "extensions.json: not registered")
else:
    print(f"Not found: {ext_json}")

# 2. Remove extension directory (glob for any version)
ext_base = Path.home() / ".qoder/extensions"
for d in ext_base.glob("oceanbase.powermem-vscode-*"):
    shutil.rmtree(d)
    print(f"Removed: {d}")
if not list(ext_base.glob("oceanbase.powermem-vscode-*")):
    print("Extension directory already absent")

PY
remove_shared_vscode_cache
```

#### 0.4.2 — Remove settings

```bash
remove_powermem_settings Qoder
```

#### 0.4.3 — Remove MCP config

```bash
if [ "$(uname -s)" = "Darwin" ]; then
  QODER_MCP="$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"
else
  QODER_MCP="$HOME/.qoder/mcp.json"
fi
remove_powermem_mcp "$QODER_MCP"
```

Leave other MCP servers untouched. Restart Qoder after removal.

### 0.5 — CodeFuse uninstall

Run when `POWERMEM_IDE=codefuse` or user chose CodeFuse.

#### 0.5.1 — Remove extension

```bash
codefuse --uninstall-extension OceanBase.powermem-vscode 2>/dev/null || true
remove_shared_vscode_cache
```

If the CLI is unavailable, fall back to manual cleanup:

```bash
python3 <<'PY'
import json, shutil
from pathlib import Path

ext_id = "OceanBase.powermem-vscode"

# 1. Remove from extensions.json
ext_json = Path.home() / ".codefuse/extensions/extensions.json"
if ext_json.exists():
    extensions = json.loads(ext_json.read_text())
    before = len(extensions)
    extensions = [e for e in extensions if e.get("identifier", {}).get("id") != ext_id]
    ext_json.write_text(json.dumps(extensions, indent=2) + "\n")
    removed_count = before - len(extensions)
    print(f"extensions.json: removed {removed_count} entry" if removed_count else "extensions.json: not registered")
else:
    print(f"Not found: {ext_json}")

# 2. Remove extension directory (glob for any version)
ext_base = Path.home() / ".codefuse/extensions"
for d in ext_base.glob("oceanbase.powermem-vscode-*"):
    shutil.rmtree(d)
    print(f"Removed: {d}")
if not list(ext_base.glob("oceanbase.powermem-vscode-*")):
    print("Extension directory already absent")

PY
remove_shared_vscode_cache
```

#### 0.5.2 — Remove settings

```bash
remove_powermem_settings CodeFuse
```

#### 0.5.3 — Remove MCP config

```bash
if [ "$(uname -s)" = "Darwin" ]; then
  CODEFUSE_MCP="$HOME/Library/Application Support/CodeFuse/User/globalStorage/mcp.json"
else
  CODEFUSE_MCP="$HOME/.codefuse/mcp.json"
fi
remove_powermem_mcp "$CODEFUSE_MCP"
```

Leave other MCP servers untouched. Restart CodeFuse after removal.

### 0.6 — Check backend usage

Check whether the backend on port `8848` is still in use:

```bash
lsof -i:8848 2>/dev/null || true
bash "$REPO_ROOT/scripts/powermem-server-service.sh" status 2>/dev/null || true
```

Note whether PowerMem is healthy, which persistence mode is active (LaunchAgent,
systemd, detached), and whether non-PowerMem processes also hold port `8848`.

### 0.7 — Ask user: stop or uninstall backend service

**AI agents:** ask the user **once** before running any backend commands. Present
these options clearly:

| Choice | When to recommend | Action |
|--------|-------------------|--------|
| **A — Leave running** | Other IDEs/clients still use port `8848`, or user wants the backend after removing only this IDE extension | Do nothing |
| **B — Stop only** | User may reinstall later; stop process but keep LaunchAgent/systemd registration | [0.7.1](#071--stop-backend-only) |
| **C — Uninstall service completely** | User is done with PowerMem on this machine; remove auto-start and service files | [0.7.2](#072--uninstall-backend-service-completely) |

Default guidance:

- If other clients are using the backend → recommend **A**.
- If this was the only client and the user did not specify → recommend **C** or **B** and let them choose.
- If port `8848` is held by a non-PowerMem process → warn the user; do not kill it automatically.

Do not stop or uninstall the service without an explicit user choice (**B** or **C**).

#### 0.7.1 — Stop backend only

Stops the running process and unloads the active LaunchAgent job, but leaves
persistence files in place so `powermem-server-service.sh start` can restore the
service later.

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" stop 2>/dev/null || true
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
```

#### 0.7.2 — Uninstall backend service completely

Removes persistence registration and installed wrapper files. Does **not** delete
`.env`, memory storage, or the Python virtualenv.

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" uninstall 2>/dev/null || true
```

This command (macOS and Linux):

1. Stops the backend process.
2. Unloads LaunchAgent / stops and disables systemd user unit.
3. Removes `~/Library/LaunchAgents/ai.powermem.server.plist` (macOS).
4. Removes `~/.config/systemd/user/powermem-server.service` (Linux).
5. Removes `~/bin/powermem-server-launch.sh` (macOS).
6. Removes `/tmp/powermem-server.launchd.log`, `.launchd.err`, and `.pid`.

Verify:

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" status 2>/dev/null || echo "backend stopped"
# macOS — LaunchAgent plist should be gone
[ "$POWERMEM_OS" = "macos" ] && test ! -f "$HOME/Library/LaunchAgents/ai.powermem.server.plist" && echo "LaunchAgent removed"
# Linux — systemd unit should be gone
[ "$POWERMEM_OS" = "linux" ] && test ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/powermem-server.service" && echo "systemd unit removed"
```

### 0.8 — Report

**Report** to the user:

1. Extension: removed or already absent.
2. Settings: which `powermem.*` keys were removed (or none found).
3. MCP config: `powermem` server removed (or not present).
4. Backend/service: left running, stopped only, or fully uninstalled (per user choice in 0.7).
5. **Restart the IDE once** to fully apply removal.

---

## 1. Disable or Uninstall the Extension (manual)

Do this first. The PowerMem commands and status bar are registered by the extension
from `src/extension.ts`; removing the extension removes those UI entry points.

### Extension Development Host from source

If setup used `npm run compile` and `F5`, close the Extension Development Host
window. Nothing is installed into the original IDE unless a VSIX was also installed.

Optionally clean local build output only if you do not need it:

```bash
rm -rf apps/vscode-extension/out
```

### Cursor

1. Open the Extensions view.
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Reload the window or restart Cursor.

CLI alternative:

```bash
cursor --uninstall-extension OceanBase.powermem-vscode
remove_shared_vscode_cache   # see [Shared helpers](#shared-helpers-0205)
```

### VS Code

1. Open the Extensions view.
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Reload the window or restart VS Code.

CLI alternative:

```bash
code --uninstall-extension OceanBase.powermem-vscode
remove_shared_vscode_cache
```

### Qoder

**Agent automation (preferred):** see [Section 0.4.1](#041--remove-extension-directory--extensionsjson).

**Manual uninstall:**
1. Open Qoder → Extensions view (`Cmd+Shift+X`).
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Restart Qoder.

**Important**: Also clean the shared VS Code extension cache:

```bash
rm -rf ~/.vscode/extensions/oceanbase.powermem-vscode-*
```

### CodeFuse

1. Open the Extensions view.
2. Find **PowerMem for VS Code**.
3. Choose **Disable** or **Uninstall**.
4. Reload the window or restart CodeFuse.

CLI alternative:

```bash
codefuse --uninstall-extension OceanBase.powermem-vscode
```

**Important**: Also remove the shared VS Code extension cache:

```bash
rm -rf ~/.vscode/extensions/oceanbase.powermem-vscode-*
```

## 2. Remove Extension Settings (manual)

Open user settings JSON and remove the `powermem.*` keys you no longer want:

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

**Settings file location by IDE and OS:**

| IDE | macOS | Linux |
|-----|-------|-------|
| Cursor | `~/Library/Application Support/Cursor/User/settings.json` | `${XDG_CONFIG_HOME:-~/.config}/Cursor/User/settings.json` |
| VS Code | `~/Library/Application Support/Code/User/settings.json` | `${XDG_CONFIG_HOME:-~/.config}/Code/User/settings.json` |
| Qoder | `~/Library/Application Support/Qoder/User/settings.json` | `${XDG_CONFIG_HOME:-~/.config}/Qoder/User/settings.json` |
| CodeFuse | `~/Library/Application Support/CodeFuse/User/settings.json` | `${XDG_CONFIG_HOME:-~/.config}/CodeFuse/User/settings.json` |

Leave settings in place if you only disabled the extension temporarily.

## 3. Remove MCP Config (manual)

Remove only the config that matches the IDE/client you set up.

### Cursor

Remove `mcpServers.powermem` from `~/.cursor/mcp.json`. Leave other MCP servers
untouched. After editing, use **Developer: Reload Window** or restart Cursor.

### VS Code

If you only used the extension UI in VS Code, there may be no external AI-tool
config to remove. Removing the extension and optional `powermem.*` settings is
enough.

### Qoder

**MCP config location by OS:**

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` |
| Linux | `~/.qoder/mcp.json` |

Remove `mcpServers.powermem` from the file. Leave other MCP servers untouched.
Restart Qoder after editing.

### CodeFuse

**MCP config location by OS:**

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/CodeFuse/User/globalStorage/mcp.json` |
| Linux | `~/.codefuse/mcp.json` |

Remove `mcpServers.powermem` from the file. Leave other MCP servers untouched.
Restart CodeFuse after editing.

### Codex, OpenCode, and generic MCP clients

These are not managed by the VS Code extension uninstall flow. Use:

```text
Read and follow apps/mcp-client/UNINSTALL.md to remove PowerMem from this MCP client.
```

### Other supported clients

| Client | Config location | Removal |
|--------|-----------------|---------|
| Claude Desktop / Code MCP provider | `~/.claude/providers/powermem.json` | Delete this file if it only contains PowerMem |
| Windsurf | `~/.windsurf/context/powermem.json` | Delete this file |
| GitHub Copilot | `~/.github/copilot/powermem.json` | Delete this file |

Do not remove other clients' MCP servers or context providers.

## 4. Optional: Stop or uninstall backend service

Only change the backend if it was started for this setup and no other
IDE/client needs it — or the user explicitly asks.

**Choose one:**

**Stop only** (process stops; LaunchAgent/systemd files remain):

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" stop 2>/dev/null || true
PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
```

**Uninstall service completely** (stop + remove LaunchAgent/systemd unit, launch
wrapper, and service logs — see [0.7.2](#072--uninstall-backend-service-completely)):

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" uninstall 2>/dev/null || true
```

## 5. Optional: Remove package data

Do not delete these without explicit confirmation:

- `.env` or any file containing LLM/API secrets.
- `seekdb_data/`, `sqlite_data/`, or other memory storage.
- PowerMem Python packages used by other integrations.

## 6. Verify Removal

Reload or restart the current IDE and confirm the PowerMem status bar item or MCP
server entry is gone.

### Troubleshooting: Extension still showing after uninstall?

If the PowerMem extension still appears in your IDE after running the uninstall steps,
it's likely due to **cross-IDE extension cache pollution**. All VS Code-based IDEs
(Cursor, VS Code, Qoder, CodeFuse) share a common extension cache at:

```
~/.vscode/extensions/
```

**Solution**: Remove the shared cache directory:

```bash
rm -rf ~/.vscode/extensions/oceanbase.powermem-vscode-*
```

Then **fully quit and restart** your IDE (don't just reload the window).

### Cursor

1. Extension gone: no "PowerMem" status bar item in the bottom-right area.
2. Commands gone: open command palette (`Cmd+Shift+P`), search "PowerMem" — no results.
3. MCP gone: open `~/.cursor/mcp.json` and confirm `powermem` is no longer listed.
4. **Shared cache clean**: `ls ~/.vscode/extensions/ | grep powermem` returns nothing.

### VS Code

1. Extension gone: no "PowerMem" status bar item.
2. Commands gone: command palette search "PowerMem" — no results.
3. **Shared cache clean**: `ls ~/.vscode/extensions/ | grep powermem` returns nothing.

### Qoder

1. Extension gone: no "PowerMem" status bar item in the bottom-right area.
2. Commands gone: open command palette (`Cmd+Shift+P`), search "PowerMem" — no results.
3. MCP gone: open MCP settings, confirm `powermem` is no longer listed.
4. Extension directory absent: `ls ~/.qoder/extensions/ | grep powermem` returns nothing.
5. **Shared cache clean**: `ls ~/.vscode/extensions/ | grep powermem` returns nothing.

### CodeFuse

Requires a **full quit and reopen** (not just "Reload Window"):

1. Extension gone: no "PowerMem" status bar item in the bottom-right area.
2. Commands gone: open command palette (`Cmd+Shift+P`), search "PowerMem" — no results.
3. MCP gone: open MCP settings, confirm `powermem` is no longer listed.
4. `codefuse --list-extensions` does not include `oceanbase.powermem-vscode`.
5. Extension directory absent: `ls ~/.codefuse/extensions/ | grep powermem` returns nothing.
6. **Shared cache clean**: `ls ~/.vscode/extensions/ | grep powermem` returns nothing.

### Backend / service

If the backend was stopped or the service was uninstalled:

```bash
curl -s -m 3 http://localhost:8848/api/v1/system/health >/dev/null && echo "still running" || echo "stopped"
# After full uninstall — persistence files should be gone
test ! -f "$HOME/Library/LaunchAgents/ai.powermem.server.plist" 2>/dev/null && echo "LaunchAgent absent" || true
test ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/powermem-server.service" 2>/dev/null && echo "systemd unit absent" || true
test ! -f "$HOME/bin/powermem-server-launch.sh" 2>/dev/null && echo "launch wrapper absent" || true
```

For installation, see [`SETUP.md`](SETUP.md).
