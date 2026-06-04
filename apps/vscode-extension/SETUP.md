# PowerMem — VS Code extension setup

This file is a setup checklist for humans and a prompt for AI coding agents. In VS Code, Cursor, Qoder, or another compatible IDE, connect PowerMem through the best available backend and configure only the current IDE unless the user explicitly asks for every supported client. For Codex, OpenCode, and other generic MCP clients, use `apps/mcp-client/SETUP.md`.

If you are asking an AI coding agent to help, say:

> Read and follow `apps/vscode-extension/SETUP.md` to set up the PowerMem VS Code extension end-to-end with no manual steps.

The procedure is idempotent: re-running it should reuse existing settings and refresh generated client configs instead of duplicating them.

---

**AI agents:** complete **[Section 0](#0-fully-automated-setup-ai-agents)** in full. Do **not** ask the user to press F5, open **PowerMem: Setup**, run **Test connection**, use **Quick Note**, or run **Developer: Reload Window** unless automation failed and you are falling back to the human steps in Sections 1–5.

**Humans:** use Section 0 if an agent is available; otherwise follow Sections 1–5 (manual UI path).

Set up the PowerMem VS Code extension on this machine. Prefer an existing or new
`powermem-server` HTTP API backend and use a streamable HTTP MCP endpoint for
MCP-capable AI tools when `{backendUrl}/mcp` responds. Use MCP-only only when the
HTTP API cannot be made healthy. Ask the user before editing secrets, API keys, or
existing client config files (except merging non-secret paths into IDE settings).

## 0. Fully automated setup (AI agents)

Run subsections **in order** (0.1 → 0.7). Complete **0.2** (`.env` + healthy backend) **before**
**0.3** (install extension). The status bar checks HTTP health at activation; if the backend is
not running yet, the bar shows disconnected until you restart the IDE or use **Reconnect** after
the server is up.

**Critical: Complete ALL setup steps (0.1–0.6) BEFORE restarting the IDE.** This ensures:
- Backend is running and healthy
- Extension is installed and registered
- IDE settings are written
- MCP configuration is linked
- **Only ONE restart needed** after everything is configured

Goal: after **one** IDE restart (only when the extension was newly installed in 0.3),
PowerMem backend, MCP, extension settings, and status bar work without further manual setup.

**Execution order:**
1. Sections 0.1–0.2: Ensure backend running
2. Section 0.3: Install extension (writes to disk, does NOT require restart yet)
3. Sections 0.4–0.5: Write IDE settings + MCP config (takes effect on next start)
4. Section 0.6: Verify everything works (HTTP probes)
5. **Then** tell user: "Restart Qoder/Cursor/VS Code once"
6. After restart: everything is active, no further steps needed

### 0.1 — Detect IDE and repo paths

```bash
# Run from the extension directory (or set EXT_DIR to its absolute path)
EXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/../.." && pwd)"
BACKEND_URL="http://localhost:8848"
```

| Signal | Current IDE |
|--------|-------------|
| `qoder` on `PATH` or Qoder user data dir exists | **Qoder** |
| `cursor` on `PATH` and agent runs inside Cursor | **Cursor** |
| `code` on `PATH`, not Cursor | **VS Code** |
| Otherwise | Ask the user once |

| Settings file | Path |
|---------------|------|
| Qoder | `$HOME/Library/Application Support/Qoder/User/settings.json` (macOS) or Linux/Windows equivalents under Qoder's user data dir |
| Cursor | `$HOME/Library/Application Support/Cursor/User/settings.json` (macOS) or Linux/Windows equivalents under Cursor's user data dir |
| VS Code | `$HOME/Library/Application Support/Code/User/settings.json` (macOS) |

| MCP config file | Path |
|-----------------|------|
| Qoder | `$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` (macOS) or `~/.qoder/mcp.json` |
| Cursor | `~/.cursor/mcp.json` or via `cursor --add-mcp` CLI |
| VS Code | Workspace or user MCP config (varies by build) |

| Extension directory | Path |
|---------------------|------|
| Qoder | `~/.qoder/extensions/oceanbase.powermem-vscode-{version}/` |
| Cursor | `$HOME/.cursor/extensions/` or managed by Cursor |
| VS Code | `~/.vscode/extensions/` or managed by VS Code |

Use the same `settings.json` merge pattern for all three (Section 0.4).

### 0.2 — Ensure backend is running (`.env` required)

Do this **before** installing or restarting the extension (0.3). The extension marks the status bar
**connected** only when `GET {backendUrl}/api/v1/system/health` succeeds; MCP-only on port 8848 does
**not** satisfy that check.

#### 0.2.0 — Probe existing backend first (idempotent auto-fix)

Always probe the current `powermem-server` before starting or killing anything.
If an existing server already passes health, memory write, search, and `/mcp`
checks, **reuse it** and skip the start step. This keeps setup safe to run
multiple times and also lets `apps/claude-code-plugin/SETUP.md` share the same
backend without fighting for port `8848`.

```bash
probe_powermem_backend() {
  curl -sf -m 5 "$BACKEND_URL/api/v1/system/health" >/dev/null || return 1
  curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories" \
    -H 'Content-Type: application/json' \
    -d '{"content":"PowerMem setup probe: dragonfruit-zx9","infer":false}' >/dev/null || return 1
  curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories/search" \
    -H 'Content-Type: application/json' \
    -d '{"query":"dragonfruit-zx9","limit":5}' | grep -q 'dragonfruit-zx9' || return 1
  curl -s -o /dev/null -w "%{http_code}" -m 5 -X POST "$BACKEND_URL/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup-probe","version":"0"}}}' \
    | grep -Eq '^(200|202)$' || return 1
}

if probe_powermem_backend; then
  echo "Existing powermem-server is healthy; skipping backend restart."
  POWERMEM_BACKEND_READY=1
else
  POWERMEM_BACKEND_READY=0
fi
```

Only when the probe fails should automation stop stale listeners on `8848` and
start a replacement server. Embedded seekdb allows **one** process at a time, so
never run a second `powermem-server` against the same `./seekdb_data`.

#### 0.2.1 — `.env` must exist before starting the server

PowerMem reads `.env` from the directory you use as `WorkingDirectory` when starting
`powermem-server` (for automation, use `$REPO_ROOT`).

1. **Verify** `$REPO_ROOT/.env` exists and has real values (not placeholders) for at least
   `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL`. See [Section 2.2](#step-22--configure-env-interactive)
   for the interactive flow if the file is missing or incomplete.
2. **Do not** start `powermem-server` until `.env` is written and confirmed — without LLM settings the
   process may exit immediately or fail memory operations later.
3. Quick check (adjust path if not using the repo root):

```bash
test -f "$REPO_ROOT/.env" || { echo "Missing $REPO_ROOT/.env — complete Section 2.2 first"; exit 1; }
grep -qE '^LLM_PROVIDER=.+' "$REPO_ROOT/.env" || exit 1
grep -qE '^LLM_MODEL=.+' "$REPO_ROOT/.env" || exit 1
# LLM_API_KEY may be empty only for local providers (ollama/vllm); otherwise require a non-placeholder key
```

Install `powermem-server` when missing ([Section 2.3](#step-23--install-powermem-server-when-needed)).

#### 0.2.2 — Start or reuse HTTP API

Reuse or start `powermem-server` ([Section 3.1–3.2](#3-configure-the-backend)).
Run [0.2.0](#020--probe-existing-backend-first-idempotent-auto-fix) first. Use
only the repo `.venv` virtual environment; do not create or source `venv`.
Always `cd` to `$REPO_ROOT` (or the directory that contains `.env`) before
starting. Prefer `uv pip` for dependency installation when available; it avoids
long serial `pip` installs on large dependencies. For agent automation, do not
rely on a plain foreground command, `nohup`, or a tool-managed background job:
those processes may be shut down when the agent command finishes. On macOS,
submit the server to `launchctl` for the current GUI session so a single Cursor
restart can use the already-running backend.

```bash
if [ "${POWERMEM_BACKEND_READY:-0}" != "1" ]; then
  lsof -ti:8848 | xargs kill -9 2>/dev/null || true
  pkill -f 'powermem-server.*8848' 2>/dev/null || true
  sleep 2
  cd "$REPO_ROOT"
  [ -d .venv ] || python3 -m venv .venv
  source .venv/bin/activate
  if command -v uv >/dev/null 2>&1; then
    uv pip install -e ".[server,mcp,cli]"
  else
    python -m pip install -U pip setuptools wheel
    python -m pip install -e ".[server,mcp,cli]"
  fi

  if [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
    launchctl remove ai.powermem.server 2>/dev/null || true
    launchctl submit -l ai.powermem.server \
      -o /tmp/powermem-server.launchd.log \
      -e /tmp/powermem-server.launchd.err \
      -- /bin/zsh -lc "cd '$REPO_ROOT' && exec .venv/bin/powermem-server --host 0.0.0.0 --port 8848"
  else
    nohup "$REPO_ROOT/.venv/bin/powermem-server" --host 0.0.0.0 --port 8848 \
      >> /tmp/powermem-server.log 2>&1 &
  fi

  for i in $(seq 1 90); do
    curl -sf -m 3 "$BACKEND_URL/api/v1/system/health" && break
    sleep 2
  done
fi
curl -sf -m 5 "$BACKEND_URL/api/v1/system/health" || {
  echo "Backend still unhealthy — check /tmp/powermem-server.log, /tmp/powermem-server.launchd.err, and .env"; exit 1
}
# Memory API must work (not just health); if this fails, re-run 0.2.0 and inspect logs:
curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories" \
  -H 'Content-Type: application/json' \
  -d '{"content":"PowerMem setup probe: dragonfruit-zx9","infer":false}' || {
  echo "Memory API failed — see /tmp/powermem-server.log (often seekdb lock or bad .env)"; exit 1
}
```

When installed with `uv pip install -e ".[server,mcp,cli]"` (or the `pip`
fallback), `powermem-server` also
exposes streamable HTTP MCP at `{backendUrl}/mcp` on the **same port** (preferred
path for extension → server). Probe it in [0.4](#04--probe-mcp-and-write-ide-settings-replaces-powermem-setup).

If you also run `apps/claude-code-plugin/SETUP.md`, keep using the same
`$REPO_ROOT/.env`, `$REPO_ROOT/.venv`, and `http://localhost:8848` backend. Both
setup flows are safe to re-run: each should probe and reuse the existing healthy
server, refresh only its own client/plugin config, and preserve unrelated client
configuration.

Optional (macOS): if the user wants PowerMem to survive logout/reboot, install a
persistent LaunchAgent plist. Use the same `powermem-server` path and
`WorkingDirectory=$REPO_ROOT` so `.env` is found. If the agent cannot write
`~/Library/LaunchAgents`, `launchctl submit` is still enough for the current
login session and for a Cursor restart.

### 0.3 — Install or refresh the extension (no F5)

Run only after [0.2](#02--ensure-backend-is-running-env-required) reports a healthy HTTP API.

Skip if `cursor --list-extensions` (or `code --list-extensions`) already shows `oceanbase.powermem-vscode`.

```bash
cd "$EXT_DIR"
npm install
npm run compile
npx --yes @vscode/vsce package --no-dependencies --allow-missing-repository --no-rewrite-relative-links
VSIX="$(ls -t powermem-vscode-*.vsix | head -1)"
```

**Qoder:** (Qoder has no CLI for extension install; use Python script)

```bash
# Qoder extension auto-installer
python3 <<'PY'
import json, os, subprocess, shutil, tempfile
from pathlib import Path

vsix = os.path.join(os.environ.get("EXT_DIR", ""), os.environ.get("VSIX", ""))
if not os.path.isfile(vsix):
    # Fallback to known path
    vsix = "$EXT_DIR/$VSIX"

ext_dir = Path.home() / ".qoder/extensions/oceanbase.powermem-vscode-0.1.0"
ext_json = Path.home() / ".qoder/extensions/extensions.json"

# Clean and recreate
if ext_dir.exists():
    shutil.rmtree(ext_dir, ignore_errors=True)
ext_dir.mkdir(parents=True, exist_ok=True)

# Extract VSIX
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(["unzip", "-q", vsix, "-d", tmp], check=True)
    src = Path(tmp) / "extension"
    if src.exists():
        for item in src.iterdir():
            dest = ext_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    manifest = Path(tmp) / "extension.vsixmanifest"
    if manifest.exists():
        shutil.copy2(manifest, ext_dir)

# Register in extensions.json
with open(ext_dir / "package.json") as f:
    pkg = json.load(f)

ext_id = f"{pkg['publisher']}.{pkg['name']}"
version = pkg['version']

extensions = json.load(open(ext_json)) if ext_json.exists() else []
if not any(e.get("identifier", {}).get("id") == ext_id for e in extensions):
    extensions.append({
        "identifier": {"id": ext_id, "uuid": None},
        "version": version,
        "location": {
            "$mid": 1, "fsPath": str(ext_dir),
            "external": f"file://{ext_dir}",
            "path": str(ext_dir), "scheme": "file"
        },
        "relativeLocation": f"oceanbase.powermem-vscode-{version}",
        "metadata": {
            "isApplicationScoped": False, "isMachineScoped": False,
            "isBuiltin": False, "pinned": False, "source": "vsix",
            "id": ext_id, "publisherDisplayName": "OceanBase",
            "targetPlatform": "undefined", "updated": False,
            "private": False, "isPreReleaseVersion": False,
            "hasPreReleaseVersion": False
        }
    })
    with open(ext_json, "w") as f:
        json.dump(extensions, f, indent=0)

print(f"✓ Installed {ext_id} v{version}")
PY
```

**Cursor:**

```bash
cursor --install-extension "$EXT_DIR/$VSIX" --force
```

**VS Code:**

```bash
code --install-extension "$EXT_DIR/$VSIX" --force
```

Extension id: `OceanBase.powermem-vscode`. Activation is `onStartupFinished` — it loads on the **next** IDE window start. Tell the user only if install succeeded: **restart Cursor/VS Code/Qoder once** (quit and reopen). Do not use Extension Development Host (F5) for production setup. If the backend was started in 0.2, the status bar should show connected after that restart; otherwise use **PowerMem → Reconnect** once the server is healthy.

### 0.4 — Probe MCP and write IDE settings (replaces PowerMem: Setup)

```bash
MCP_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -m 5 -X POST "$BACKEND_URL/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup-probe","version":"0"}}}')
```

Merge into the IDE user `settings.json` (create `{}` if missing). Use Python or `jq`; preserve other keys.

| Probe result | `powermem.mcpServerPath` | `powermem.connectionMode` |
|--------------|--------------------------|---------------------------|
| HTTP 200/202 or valid MCP body | `""` (empty — use remote `{backendUrl}/mcp`) | `mcp` |
| 404 or other failure | `"stdio"` (any non-empty value — extension launches `powermem-mcp stdio`) | `mcp` |

Always set at minimum:

```json
{
  "powermem.enabled": true,
  "powermem.backendUrl": "http://localhost:8848",
  "powermem.connectionMode": "mcp",
  "powermem.seamlessMode": true
}
```

**Qoder example** (macOS):

```bash
# macOS Qoder example; adjust path for Linux/Windows
export SETTINGS="$HOME/Library/Application Support/Qoder/User/settings.json"

python3 <<'PY'
import json, os
path = os.path.expanduser(os.environ["SETTINGS"])
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.isfile(path):
    with open(path) as f:
        data = json.load(f)
data.update({
    "powermem.enabled": True,
    "powermem.backendUrl": "http://localhost:8848",
    "powermem.connectionMode": "mcp",
    "powermem.mcpServerPath": os.environ.get("PMEM_MCP_PATH", ""),
    "powermem.seamlessMode": True,
})
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
PY
```

Example merge (macOS Cursor):

```bash
# macOS Cursor example; adjust path for VS Code or Linux
export SETTINGS="$HOME/Library/Application Support/Cursor/User/settings.json"
# When POST /mcp returns 404:
# export PMEM_MCP_PATH=stdio

python3 <<'PY'
import json, os
path = os.path.expanduser(os.environ["SETTINGS"])
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.isfile(path):
    with open(path) as f:
        data = json.load(f)
data.update({
    "powermem.enabled": True,
    "powermem.backendUrl": "http://localhost:8848",
    "powermem.connectionMode": "mcp",
    "powermem.mcpServerPath": os.environ.get("PMEM_MCP_PATH", ""),
    "powermem.seamlessMode": True,
})
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
PY
```

Set `PMEM_MCP_PATH=stdio` when the `/mcp` probe failed; omit it when streamable HTTP works.

### 0.5 — Link MCP for the current IDE (replaces manual `mcp.json` + Reload Window)

**Qoder — write MCP config file** (Qoder has no CLI like `cursor --add-mcp`):

When `/mcp` works:

```bash
# Qoder MCP config path (macOS)
QODER_MCP="$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"

python3 <<'PY'
import json, os
from pathlib import Path

mcp_path = Path(os.path.expanduser(os.environ.get("QODER_MCP", "")))
mcp_path.parent.mkdir(parents=True, exist_ok=True)

data = {"mcpServers": {}}
if mcp_path.is_file():
    with open(mcp_path) as f:
        data = json.load(f)

# Ensure mcpServers exists
if "mcpServers" not in data:
    data["mcpServers"] = {}

# Add or update powermem
data["mcpServers"]["powermem"] = {
    "url": "http://localhost:8848/mcp"
}

with open(mcp_path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")

print(f"Updated {mcp_path}")
PY
```

When `/mcp` returns 404, use local stdio:

```bash
QODER_MCP="$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"
PMEM_MCP="$(command -v powermem-mcp || echo "$REPO_ROOT/.venv/bin/powermem-mcp")"

python3 <<'PY'
import json, os
from pathlib import Path

mcp_path = Path(os.path.expanduser(os.environ.get("QODER_MCP", "")))
mcp_path.parent.mkdir(parents=True, exist_ok=True)

data = {"mcpServers": {}}
if mcp_path.is_file():
    with open(mcp_path) as f:
        data = json.load(f)

if "mcpServers" not in data:
    data["mcpServers"] = {}

pmem_mcp = os.environ.get("PMEM_MCP", "powermem-mcp")
repo_root = os.environ.get("REPO_ROOT", "")

data["mcpServers"]["powermem"] = {
    "command": pmem_mcp,
    "args": ["stdio"],
    "env": {
        "POWERMEM_ENV_FILE": f"{repo_root}/.env" if repo_root else ""
    }
}

with open(mcp_path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")

print(f"Updated {mcp_path} with stdio MCP")
PY
```

**Cursor — prefer CLI** (merges into the user MCP profile; avoids hand-editing JSON when possible):

When `/mcp` works:

```bash
cursor --add-mcp '{"name":"powermem","url":"http://localhost:8848/mcp"}'
```

When `/mcp` returns 404, use local stdio and point at `.env`:

```bash
PMEM_MCP="$(command -v powermem-mcp || echo "$REPO_ROOT/.venv/bin/powermem-mcp")"
cursor --add-mcp "{\"name\":\"powermem\",\"command\":\"$PMEM_MCP\",\"args\":[\"stdio\"],\"env\":{\"POWERMEM_ENV_FILE\":\"$REPO_ROOT/.env\"}}"
```

If `--add-mcp` is unavailable or fails, merge into `~/.cursor/mcp.json` instead (preserve existing servers). Match the shape other entries use (`type`, `command`, `env`).

**VS Code:** merge the same `powermem` entry into the workspace or user MCP config file your VS Code build uses (if any). The extension UI does not require MCP for its own HTTP commands.

Do **not** instruct the user to run **Developer: Reload Window** when config writes succeeded. If MCP still shows stale errors after config changes, **one** full IDE restart is enough (same as Section 0.3).

### 0.6 — Verify without extension UI (replaces Quick Note + Query)

```bash
# Health
curl -sf -m 5 "$BACKEND_URL/api/v1/system/health"

# Write probe
curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories" \
  -H 'Content-Type: application/json' \
  -d '{"content":"PowerMem setup probe: dragonfruit-zx9","infer":false}'

# Search probe
curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"dragonfruit-zx9","limit":5}'
```

Confirm the search response includes `dragonfruit-zx9`. If MCP stdio mode is used, optionally verify `powermem-mcp` starts: `"$PMEM_MCP" stdio` with `POWERMEM_ENV_FILE` set (then interrupt).

### 0.7 — Agent completion message

Report only:

1. Backend URL and health status.
2. MCP mode: streamable HTTP vs stdio (and why, from the probe).
3. Extension installation status:
   - **For Qoder:** confirm extension was extracted to `~/.qoder/extensions/` AND registered in `extensions.json`.
   - **For Cursor/VS Code:** confirm VSIX was installed via CLI.
4. Redacted `.env` summary — never echo full API keys.
5. That seamless mode + linked MCP are active; no F5 / Setup wizard / Reload Window steps remain.
6. **Critical: ONE restart only** — all configuration (backend, extension, settings, MCP) is complete. After **one** IDE restart (Qoder/Cursor/VS Code), everything will work:
   - Extension status bar visible and connected
   - MCP tools available to AI assistant
   - Seamless memory operations active

If anything failed, fall back to Sections 1–5 and say which automated step failed.

---

## 1. Install the Extension Once

> **Agents:** complete [Section 0.2](#02--ensure-backend-is-running-env-required) first, then [Section 0.3](#03--install-or-refresh-the-extension-no-f5). **Humans:** continue below (Section 2 → 3 before Section 1 if the backend is not running yet).

Do this first. If the extension is already installed and the **PowerMem** commands
are visible in the command palette, skip this section.

Use the path that matches how you received PowerMem.

### Qoder-specific notes

Qoder uses VS Code's extension system under the hood. The PowerMem VS Code extension (id: `OceanBase.powermem-vscode`) works in Qoder without modification.

**Agent automation (Section 0.3):** The Python script automatically:
1. Extracts the VSIX to `~/.qoder/extensions/oceanbase.powermem-vscode-{version}/`
2. Registers the extension in `~/.qoder/extensions/extensions.json`
3. No manual steps or CLI commands needed

**Configuration files written by agent:**
- **User settings**: `$HOME/Library/Application Support/Qoder/User/settings.json` (Section 0.4)
- **MCP config**: `$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` (Section 0.5)
- **Extension**: `~/.qoder/extensions/oceanbase.powermem-vscode-{version}/` (Section 0.3)

**After one Qoder restart:**
- Extension activates automatically (status bar appears)
- MCP tools connect to backend
- Seamless memory operations work
- No further configuration needed

**Manual install** (only if agent automation unavailable):
1. Open Qoder → Extensions view (`Cmd+Shift+X`)
2. Click `...` → "Install from VSIX..."
3. Select: `apps/vscode-extension/powermem-vscode-*.vsix`
4. Restart Qoder once

### From source (human / dev only)

Open `apps/vscode-extension/` in VS Code or Cursor, install dependencies, and launch the extension host:

```bash
cd apps/vscode-extension
npm install
npm run compile
```

Press **F5** only for **extension development** (debugging `src/extension.ts`). For day-to-day use, package and install a VSIX (Section 0.3 or “From a packaged VSIX” below) so commands work in your normal IDE window after one restart.

### From a packaged VSIX

Build and install without the UI:

```bash
cd apps/vscode-extension
npm install && npm run compile
npx --yes @vscode/vsce package --no-dependencies --allow-missing-repository --no-rewrite-relative-links
cursor --install-extension powermem-vscode-*.vsix --force   # Cursor
# code --install-extension powermem-vscode-*.vsix --force  # VS Code
```

Humans can instead use the Extensions view → **Install from VSIX...**.

After the **first** VSIX install, restart the IDE once so `onStartupFinished` registers the status bar and commands. Re-runs of setup do not require another restart if the extension id is already listed.

## 2. Prerequisites

Complete these steps before configuring the backend in Section 3. This procedure is
idempotent: reuse an existing `.env` and an already-installed `powermem-server` when
present.

**`.env` must be created or changed interactively.** Collect each value from the user
(prompt or structured questions). Before writing `.env`, show a redacted summary and wait for
explicit confirmation. Never invent API keys or patch secrets silently.

You also need VS Code, Cursor, Qoder, or another VS Code compatible IDE/client that can use
PowerMem.

### Step 2.1 — Detect context

Determine whether you are in the PowerMem source tree or using a PyPI/install-only
setup:

- **SOURCE** — the repo root contains `pyproject.toml` with `name = "powermem"`, or
  both `src/powermem/` and `apps/vscode-extension/` exist.
- **PIP** — PowerMem is installed from PyPI/uv and you are not building from this
  checkout.

Tell the user which path you will take. For **SOURCE**, Step 2.3 installs
`powermem-server` from this checkout when it is missing. For **PIP**, install
`powermem[server]` for the HTTP server only, or add `seekdb` when using the default
embedded seekdb storage.

### Step 2.2 — Configure `.env` (interactive)

PowerMem reads configuration from `.env` in the working directory where you start
`powermem-server` or MCP. At minimum you need LLM provider, API key, and model — the
same three variables documented in [`.env.example`](../../.env.example) at the repo
root. Everything else in that file has safe defaults, but the embedded seekdb storage
path requires installing the `seekdb` extra.

**Agents and humans must interactively collect values.** Ask the user to supply each
setting in the chat (or via the IDE’s question UI). Do not silently reuse values from
`.env.tmp`, `.env.example`, backups, or another project path. If the user declines to
create or update `.env`, skip this step and continue with whatever configuration the
running backend already uses.

#### 2.2.1 — Pick the `.env` directory

Prefer the PowerMem repo root when running from source; otherwise use the directory
you will `cd` into before starting the backend.

#### 2.2.2 — Detect existing configuration

Check whether `.env` already exists there with `LLM_PROVIDER`, `LLM_API_KEY`, and
`LLM_MODEL` set to real values (not placeholders such as `your_api_key_here`).

- **If fully configured:** reuse the file. Do not re-prompt unless a value is missing,
  invalid, or the user asks to change it.
- **If missing or incomplete:** run the interactive flow below. Do not offer to copy
  from `.env.tmp` or similar files as a shortcut; the user must enter (or explicitly
  paste) each value for the file you are about to write.

#### 2.2.3 — Interactive prompts (required when creating or changing `.env`)

Ask **one setting at a time** (or use structured multiple-choice where it fits). At
minimum collect:

| Variable | Prompt the user for |
|----------|---------------------|
| `LLM_PROVIDER` | Provider id, e.g. `openai`, `anthropic`, `qwen`, `deepseek`, `ollama`, `vllm` |
| `LLM_API_KEY` | API key or token (skip or leave empty only for local providers such as `ollama` / `vllm`) |
| `LLM_MODEL` | Model name, e.g. `gpt-4o-mini`, `qwen-plus`, `deepseek-chat` |

If the user chose an OpenAI-compatible gateway that is not `https://api.openai.com/v1`,
also ask for the matching `*_LLM_BASE_URL` from [`.env.example`](../../.env.example)
(for example `OPENAI_LLM_BASE_URL`).

Example agent wording:

> PowerMem needs a `.env` in `<directory>`. What should `LLM_PROVIDER` be?  
> (Then, after the answer:) What is your `LLM_API_KEY`?  
> (Then:) What is `LLM_MODEL`?

Wait for the user’s reply after each question. If they refuse to provide a secret, do
not write `.env`; explain that `powermem-server` / MCP may fail until configuration
exists elsewhere.

#### 2.2.4 — Confirm, then write

1. Show what will be written: provider and model in clear text; mask `LLM_API_KEY`
   (for example `sk-…xxxx`).
2. Ask: **“Create/update `.env` with these values?”** — require an explicit yes/no.
3. Only after **yes**:
   - If `.env` does not exist: `cp .env.example .env` in that directory.
   - Set `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL` (and optional base URL) to the
     values the user supplied in this session.
   - Leave other keys at the safe defaults from `.env.example` unless the user asked
     to change them.

**Alternative (user-driven):** after installing the CLI extra, the user can run
`pmem config init` interactively in the same directory instead of an agent writing
the file.

Never echo the full API key back in chat or logs.

### Step 2.3 — Install `powermem-server` when needed

Check whether `powermem-server` is already available:

```bash
command -v powermem-server
powermem-server --help
```

- **If the command exists and works:** skip installation.
- **SOURCE path (fresh checkout):** install editable from the repo root so the server
  includes the latest HTTP API and `/mcp` endpoint:

  ```bash
  cd /path/to/powermem
  pip install -e ".[server,mcp,cli,seekdb]"
  ```

  Re-run `command -v powermem-server` to confirm the entry point is on `PATH`.

- **PIP path:** install the server extra if missing:

  ```bash
  pip install "powermem[server,mcp,cli,seekdb]"
  ```

If `pip install` fails with `externally-managed-environment`, create and use a virtual
environment in the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[server,mcp,cli,seekdb]"   # SOURCE path
# or: pip install "powermem[server,mcp,cli,seekdb]"   # PIP path
```

Backend startup priority for Section 3:

1. Reuse an existing healthy HTTP API server:
   `curl -s -m 5 http://localhost:8848/api/v1/system/health`
2. If none exists, start from the directory containing `.env`:
   `powermem-server --host 0.0.0.0 --port 8848`
3. If the HTTP API cannot be started, fall back to MCP-only:
   `powermem-mcp streamable-http 8848`
4. Use `powermem-mcp stdio` or `sse` only when the target client requires it.

## 3. Configure the backend

### Step 3.1 — Reuse an existing HTTP API server when available

Another IDE may already have started `powermem-server`. Check before starting a new process:

```bash
curl -s -m 5 http://localhost:8848/api/v1/system/health
```

If the response is healthy, reuse `http://localhost:8848`. Do not start a duplicate server.

### Step 3.2 — Start HTTP API if needed

If nothing healthy is listening, start the server from a directory where PowerMem can read its `.env`:

```bash
powermem-server --host 0.0.0.0 --port 8848
```

Then rerun the health check. This is the preferred setup for the VS Code extension:
the status bar, **Query memories**, **Add selection to memory**, **Quick note**, and
**Dashboard** features use the HTTP API. Do not assume this HTTP API also exposes
MCP at `/mcp`; probe it in the next step before writing AI-tool MCP config.

### Step 3.3 — Choose the MCP endpoint for AI tools

First check whether the healthy HTTP API also exposes streamable HTTP MCP at
`http://localhost:8848/mcp`:

```bash
curl -i -s -m 5 -X POST http://localhost:8848/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"powermem-setup-probe","version":"0.0.0"}}}'
```

If this returns 200/202 or any valid MCP protocol response, MCP clients may use:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If it returns 404 Not Found, the API server is healthy but does not expose MCP.
This is the common cause of Cursor showing PowerMem as errored: Cursor first tries
streamable HTTP, then falls back to SSE, and both fail because `/mcp` is missing.
Do not introduce a second port. Keep all PowerMem endpoints on `8848`: either use a
`powermem-server` build that exposes `/mcp`, or stop the API server and run the
MCP-only server on the same port. MCP-only mode is for AI tools only; the extension
UI still needs the HTTP API endpoints.

```bash
powermem-mcp streamable-http 8848
```

Then configure the MCP client with:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If `powermem-mcp streamable-http 8848` fails, inspect its stderr before changing
client config. Common causes are missing package dependencies, missing `.env` values,
or port 8848 already being in use by a `powermem-server` process that does not expose
`/mcp`. Stop or replace that process before starting MCP-only mode on 8848.

Use local stdio only when the target client must launch MCP as a local command or
cannot connect to remote streamable HTTP:

```bash
powermem-mcp stdio
```

Use SSE only when the client explicitly requires SSE:

```bash
powermem-mcp sse 8848
```

MCP-only is enough for MCP-native AI tools, but it does not provide the extension's HTTP endpoints such as `/api/v1/system/health` or `/api/v1/memories/search`.

### Step 3.4 — Save extension settings

> **Agents:** write user `settings.json` per [Section 0.4](#04--probe-mcp-and-write-ide-settings-replaces-powermem-setup). Do not open **PowerMem: Setup**.

**Human fallback** (when automation is unavailable):

1. Open the command palette.
2. Run **PowerMem: Setup**.
3. Set **Backend URL** to the healthy HTTP API server, usually `http://localhost:8848`.
4. Set **API key** only if your server requires `X-API-Key`.
5. Leave **MCP server path** empty only if the probe above confirms `{backendUrl}/mcp` works. If `{backendUrl}/mcp` returns 404, do not use **PowerMem: Link to AI Tools** to claim MCP is working. Keep the setup on port `8848` by either replacing the API server with a build that exposes `/mcp`, or by stopping the API server and running MCP-only mode on `8848`.
6. Set **MCP server path** only for local stdio MCP, for example `powermem-mcp stdio`.
7. Run **Test connection**. If it fails and HTTP cannot be fixed, document that the setup is MCP-only and skip extension UI verification that depends on HTTP.

The extension stores these settings under VS Code settings:

| Setting | Purpose |
|---------|---------|
| `powermem.backendUrl` | PowerMem HTTP API base URL |
| `powermem.apiKey` | Optional API key sent as `X-API-Key` |
| `powermem.connectionMode` | `mcp` by default for AI tools; `http` when a client needs HTTP context |
| `powermem.mcpServerPath` | Optional local MCP command/path |
| `powermem.userId` | Optional memory user scope |
| `powermem.projectName` | Optional project scope |

## 4. Link AI tools

Configure only the current IDE or target client. Do not write config for unrelated IDEs unless the user explicitly asks for a multi-client setup.

### Current IDE: VS Code

No external AI-tool config is required for the extension's own UI. After **PowerMem: Setup** succeeds, use the status bar and commands directly:

- **PowerMem: Query Memories**
- **PowerMem: Add Selection to Memory**
- **PowerMem: Quick Note**
- **PowerMem: Dashboard**

### Current IDE: Cursor

> **Agents:** use [Section 0.5](#05--link-mcp-for-the-current-ide-replaces-manual-mcpjson--reload-window) (`cursor --add-mcp` or merge `~/.cursor/mcp.json`). Do not ask the user to reload the window unless MCP stays errored after a full IDE restart.

If you are running in Cursor, configure Cursor only. The recommended configuration is
remote streamable HTTP. Use `http://localhost:8848/mcp` only if the probe in
Step 3.3 confirmed the API server exposes `/mcp`. Manually ensure this entry exists
in `~/.cursor/mcp.json` (human fallback):

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If the HTTP API server returns 404 for `/mcp`, do not switch to another port. Use a
`powermem-server` build that exposes `/mcp`, or stop the current API server and start
MCP-only streamable HTTP on `8848`:

```bash
powermem-mcp streamable-http 8848
```

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

Use local stdio only when Cursor cannot connect to a remote MCP URL:

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

After changing `~/.cursor/mcp.json` by hand, restart Cursor once so the MCP client is
recreated. Prefer `cursor --add-mcp` during automated setup to avoid a separate reload step.
A stale failed MCP client can keep showing an error until the IDE restarts.

### Current IDE: Qoder

> **Agents:** use [Section 0.5](#05--link-mcp-for-the-current-ide-replaces-manual-mcpjson--reload-window) to write Qoder MCP config automatically. Do not ask the user to manually edit JSON files.

Qoder is configured automatically by the agent during Section 0.5. The agent writes:

1. **Qoder settings.json** (`$HOME/Library/Application Support/Qoder/User/settings.json`) — extension settings including `powermem.backendUrl`, `powermem.connectionMode`, and `powermem.seamlessMode`.
2. **Qoder MCP config** (`$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json`) — MCP server entry pointing to `http://localhost:8848/mcp` (or stdio fallback).

After one Qoder restart, both the PowerMem extension and MCP tools are active automatically.

**Manual setup** (human fallback only when automation is unavailable):

For Qoder IDE, add a server named `powermem` in **MCP** or **Connectors & MCP**.
Use the healthy `powermem-server` endpoint only if Step 3.3 confirmed `/mcp` exists:

```json
{
  "mcpServers": {
    "powermem": {
      "type": "streamable-http",
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If you are using MCP-only mode instead, keep it on the same port:

```json
{
  "mcpServers": {
    "powermem": {
      "type": "streamable-http",
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

Use local stdio only when Qoder cannot connect to remote streamable HTTP:

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

For Qoder CLI:

```bash
qodercli mcp add -s user powermem -- powermem-mcp stdio
```

Then reload MCP in Qoder if it is already running:

```text
/mcp reload
```

Full details: [`../../docs/integrations/qoder.md`](../../docs/integrations/qoder.md).

### Codex, OpenCode, and generic MCP clients

Do not configure Codex or OpenCode through this VS Code extension setup. They use
the generic MCP client flow instead:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

Full details: [`../../docs/integrations/codex.md`](../../docs/integrations/codex.md), [`../../docs/integrations/opencode.md`](../../docs/integrations/opencode.md), and [`../../docs/integrations/mcp_client.md`](../../docs/integrations/mcp_client.md).

### Other supported clients

If the current target is another supported client, write only that client's config:

| Client | Config written |
|--------|----------------|
| Claude Desktop / Code MCP provider | `~/.claude/providers/powermem.json` |
| Windsurf | `~/.windsurf/context/powermem.json` |
| GitHub Copilot | `~/.github/copilot/powermem.json` |

The command **PowerMem: Link to AI Tools** currently writes all supported client configs. Use it only when that broad setup is desired; otherwise edit the current client's config manually or follow the per-client guide under [`../../docs/integrations/`](../../docs/integrations/).

After linking, restart each AI tool if it does not pick up config automatically.
For VS Code/Cursor: **agents** only require a restart when the extension was newly
installed (Section 0.3) or MCP remains broken after `cursor --add-mcp`. **Humans**
may use **Developer: Reload Window** as an alternative to a full restart.

## 5. Verify

> **Agents:** run [Section 0.6](#06--verify-without-extension-ui-replaces-quick-note--query) (HTTP `curl` probes). Skip status bar, **Quick Note**, and **Query Memories** unless automation failed.

**Human fallback** after a VSIX install or manual MCP edit: restart the IDE once, then:

For the preferred HTTP API path, confirm the backend is healthy:

```bash
curl -s -m 5 http://localhost:8848/api/v1/system/health
```

**For Qoder:** verify the extension and MCP are working:

**Important:** All setup (extension install, settings, MCP) was completed BEFORE this restart. This is the **only** restart needed.

1. Restart Qoder once after setup completes.
2. After restart, verify in this order:
   - Extension loaded: check status bar shows "PowerMem" (usually bottom-right)
   - Extension commands: open command palette (`Cmd+Shift+P`), search "PowerMem" — commands should appear
   - MCP connected: open MCP settings, confirm `powermem` shows as connected
   - MCP tools: expand `powermem` server, verify tools listed: `add_memory`, `search_memories`, etc.
3. Test memory operations:
   - Add test memory: use **PowerMem: Quick Note** or MCP `add_memory` with content `PowerMem Qoder setup probe: dragonfruit-zx9`
   - Search: use **PowerMem: Query Memories** or MCP `search_memories` for `dragonfruit-zx9`
   - Confirm result appears

If anything is missing, the extension may not have been registered. Check `~/.qoder/extensions/extensions.json` contains `OceanBase.powermem-vscode`.

For VS Code/Cursor, verify the extension UI:

1. The VS Code status bar shows **PowerMem** without a warning icon.
2. Run **PowerMem: Quick Note** and save a short probe such as `PowerMem VS Code setup probe: dragonfruit-zx9`.
3. Run **PowerMem: Query Memories** and search for `dragonfruit-zx9`.
4. Confirm the saved memory appears in the search results.

For MCP-only fallback, verify from the target MCP client instead:

1. Confirm the `powermem` MCP server is connected.
2. Confirm tools such as `add_memory` and `search_memories` are listed.
3. Add and search a probe memory containing `dragonfruit-zx9`.
4. If the client still shows an error, read the MCP output/log before retrying. For
   Cursor, check the MCP server log for `powermem`; a 404 response from
   `http://localhost:8848/mcp` means the process listening on `8848` does not expose
   MCP. Replace it with a unified API+MCP server build, or stop it and run MCP-only
   mode on `8848`.

## 6. Troubleshooting

### Status bar shows disconnected

The extension calls `GET {powermem.backendUrl}/api/v1/system/health` on startup (and when you choose **Reconnect**). Any non-OK response or connection error leaves the bar on **PowerMem disconnected. Click to setup.**

- Run [Section 0.2.0](#020--probe-existing-backend-first-idempotent-auto-fix) first. If that probe fails, clear stale listeners and start exactly one `.venv`-based server.

- Confirm `$REPO_ROOT/.env` exists with valid `LLM_PROVIDER`, `LLM_API_KEY` (if required), and `LLM_MODEL` before starting the server ([Section 2.2](#step-22--configure-env-interactive)).
- Check whether another IDE already has a healthy `powermem-server` at `http://localhost:8848`:
  `curl -sf http://localhost:8848/api/v1/system/health`
- If not, `cd` to the directory that contains `.env`, then start:
  `powermem-server --host 0.0.0.0 --port 8848` (inspect logs if it exits immediately).
- Confirm `powermem.backendUrl` in user settings matches the server URL (default `http://localhost:8848`).
- If the server requires auth, set `powermem.apiKey`.
- After fixing the backend, use **PowerMem → Reconnect** or restart the IDE once.
- MCP-only (`powermem-mcp streamable-http 8848` without HTTP API) does **not** satisfy the extension health check.
- Common setup mistake: install/restart the extension (Section 0.3) before `.env` and a healthy backend (Section 0.2) — always complete backend + `.env` first.

### AI tool does not see PowerMem

- Confirm you configured the current target IDE/client, not every possible client.
- Restart or reload the target AI tool after editing config.
- Open the target config file and confirm it contains a `powermem` entry.
- **For Qoder:** the agent auto-writes config in Section 0.5. If MCP still fails after restart, verify:
  - `$HOME/Library/Application Support/Qoder/User/settings.json` has `powermem.*` settings
  - `$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` has `powermem` in `mcpServers`
  - If needed, manually run `/mcp reload` in Qoder's chat or restart Qoder
- For Codex or OpenCode, use the generic MCP client setup in [`../mcp-client/SETUP.md`](../mcp-client/SETUP.md).

### MCP starts but tools fail

- Confirm the configured remote MCP URL actually exposes `/mcp`.
- If Cursor shows PowerMem as errored and the log says `Streamable HTTP error`
  followed by `Not Found` and an SSE fallback 404, the configured URL is wrong for
  the running process. Keep the port as `8848`; replace the running API server with
  one that exposes `/mcp`, or stop it and run
  `powermem-mcp streamable-http 8848` for MCP-only mode, then reload/restart the
  IDE.
- If using stdio MCP, confirm `powermem-mcp stdio` works on your `PATH`.
- Check server logs for request errors and fix missing `.env` values.

## 7. Uninstall

See [`UNINSTALL.md`](UNINSTALL.md).
