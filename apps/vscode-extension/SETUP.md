# PowerMem — VS Code extension setup

This file is a setup checklist for humans and a prompt for AI coding agents.
Supported IDEs (priority order): **Cursor → VS Code → Qoder → CodeFuse**. Connect
PowerMem through the best available backend and configure only the current IDE
unless the user explicitly asks for every supported client. For Codex, OpenCode,
and other generic MCP clients, use `apps/mcp-client/SETUP.md`.

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

**Flow:** [0.1](#01--detect-os-and-ide) detect → [0.2](#02--ensure-backend-is-running-env-required) backend
→ [shared helpers](#shared-helpers-for-0306) → **one IDE block** in priority order
([0.3](#03--cursor-setup) Cursor → [0.4](#04--vs-code-setup) VS Code →
[0.5](#05--qoder-setup) Qoder → [0.6](#06--codefuse-setup) CodeFuse) →
[0.7](#07--verify-without-extension-ui) verify → [0.8](#08--agent-completion-message) report.

**Note:** By default, automation configures only the **currently detected IDE**. To install PowerMem
for multiple IDEs (e.g., both Cursor and Qoder), explicitly state: *"Install PowerMem for Cursor and Qoder."*

The backend must be healthy **before** any IDE-specific step.

**Critical: Complete ALL setup steps (0.1–0.7) BEFORE restarting the IDE.** This ensures:
- Backend is running and healthy **before the IDE opens**
- Extension is installed and registered
- IDE settings are written
- MCP configuration is linked
- **Only ONE restart needed** after everything is configured
- **The status bar shows "disconnected" when `GET {backendUrl}/api/v1/system/health` fails.** This is almost always because `powermem-server` is not running — not an MCP or extension bug. MCP can still work while the status bar shows disconnected, but the extension UI requires the HTTP API.

Goal: after **one full IDE restart** (quit and reopen — do not use "Reload Window" in CodeFuse),
PowerMem backend, MCP, extension settings, and status bar work without further manual setup.

**macOS persistence requirement:** setup must leave the backend running via **LaunchAgent**
(`scripts/powermem-server-service.sh`), not a short-lived shell background job. If LaunchAgent
registration fails and only a detached `nohup` process was started, the backend may die later and
Cursor will show **disconnected** after restart even though extension settings are correct.

### 0.1 — Detect OS and IDE

Automated setup is supported on **macOS** and **Linux** only. On Windows, install
the extension manually and use the generic MCP/client docs.

```bash
EXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/../.." && pwd)"
BACKEND_URL="${BACKEND_URL:-http://localhost:8848}"
OS_NAME="$(uname -s)"
case "$OS_NAME" in
  Darwin) POWERMEM_OS=macos ;;
  Linux)  POWERMEM_OS=linux ;;
  *)      echo "Unsupported automated setup OS: $OS_NAME"; exit 1 ;;
esac
export EXT_DIR REPO_ROOT BACKEND_URL POWERMEM_OS
```

Configure **one current IDE** unless the user explicitly asks for every client.
Detect the IDE (priority: env vars → active process → CLI), then run its matching
subsection after 0.2:

| IDE | Detection signal | Run |
|-----|------------------|-----|
| Qoder | `$QODER_IDE`/`$QODER_AGENT`/`$QODER` set, Qoder user data dir + active process, or `qoder` on `PATH` | [0.5](#05--qoder-setup) |
| Cursor | `$CURSOR` set, agent runs inside Cursor, or `cursor` on `PATH` | [0.3](#03--cursor-setup) |
| VS Code | `$VSCODE_PID` set (not Cursor), or `code` on `PATH` without Cursor | [0.4](#04--vs-code-setup) |
| CodeFuse | `$CODEFUSE` set, CodeFuse user data dir + active process, or `codefuse` on `PATH` | [0.6](#06--codefuse-setup) |
| Unknown | none of the above | Ask the user once |

**Detection note:** Qoder sets `$QODER_IDE` and `$QODER_AGENT` (not `$QODER`). The parent process chain walk (tier 2) definitively identifies the spawning IDE regardless of priority order.

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

  # Priority 3: Active process + user data dir (fallback)
  if [ -d "$HOME/.qoder" ] && pgrep -f "Qoder" >/dev/null 2>&1; then echo qoder; return; fi
  if { [ -d "$HOME/Library/Application Support/Cursor" ] || [ -d "$HOME/.config/Cursor" ]; } \
     && pgrep -f "Cursor" >/dev/null 2>&1; then echo cursor; return; fi
  if { [ -d "$HOME/Library/Application Support/Code" ] || [ -d "$HOME/.config/Code" ]; } \
     && pgrep -f "/Code" >/dev/null 2>&1 && ! pgrep -f "Cursor" >/dev/null 2>&1; then echo vscode; return; fi
  if { [ -d "$HOME/.codefuse" ] || [ -d "$HOME/Library/Application Support/CodeFuse" ]; } \
     && pgrep -f "CodeFuse" >/dev/null 2>&1; then echo codefuse; return; fi

  # Priority 4: CLI availability (least reliable - just means it's installed)
  command -v qoder >/dev/null 2>&1 && { echo qoder; return; }
  command -v cursor >/dev/null 2>&1 && { echo cursor; return; }
  command -v code >/dev/null 2>&1 && { echo vscode; return; }
  command -v codefuse >/dev/null 2>&1 && { echo codefuse; return; }

  echo unknown
}
POWERMEM_IDE="$(detect_current_ide)"
echo "Detected IDE: $POWERMEM_IDE"
export POWERMEM_IDE
```

### 0.2 — Ensure backend is running (`.env` required)

Do this **before** installing or restarting the extension (0.3–0.6). The extension
marks the status bar **connected** only when `GET {backendUrl}/api/v1/system/health`
succeeds; MCP-only on port 8848 does **not** satisfy that check.

#### 0.2.1 — Probe existing backend first (idempotent)

Always probe the current `powermem-server` before starting or killing anything.
If an existing server already passes health, memory write, search, and `/mcp`
checks, **reuse it** and skip the start step.

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
start a replacement server.

#### 0.2.2 — `.env` must exist before starting the server

PowerMem reads `.env` from the directory you use as `WorkingDirectory` when starting
`powermem-server` (for automation, use `$REPO_ROOT`).

1. **Verify** `$REPO_ROOT/.env` exists and has real values for at least
   `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL`.
2. **Do not** start `powermem-server` until `.env` is written and confirmed.
3. Quick check:

```bash
test -f "$REPO_ROOT/.env" || { echo "Missing $REPO_ROOT/.env — complete Section 2.2 first"; exit 1; }
grep -qE '^LLM_PROVIDER=.+' "$REPO_ROOT/.env" || exit 1
grep -qE '^LLM_MODEL=.+' "$REPO_ROOT/.env" || exit 1
```

#### 0.2.3 — Start or reuse HTTP API

Run [0.2.1](#021--probe-existing-backend-first-idempotent) first. Use only the repo
`.venv` virtual environment; do not create or source `venv`. Always `cd` to
`$REPO_ROOT` before starting.

**Common preparation (both OS):**

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
fi
```

**macOS — start via LaunchAgent helper:**

Uses `scripts/powermem-server-service.sh`, which:
1. Copies `scripts/powermem-server-launch.sh` to `~/bin/powermem-server-launch.sh` (launchd often
   cannot execute scripts directly from deep repo paths or `.venv` entry points).
2. Registers LaunchAgent label `ai.powermem.server` pointing at the `~/bin` wrapper with
   `POWERMEM_REPO_ROOT` set in the plist.

**Do not** point LaunchAgent `ProgramArguments` directly at `.venv/bin/powermem-server` — macOS
`launchctl bootstrap` returns `Bootstrap failed: 5: Input/output error` for venv Python entry scripts.
The wrapper must live under `~/bin`; launchd often cannot execute scripts from deep repo paths.

```bash
if [ "$POWERMEM_OS" = "macos" ] && [ "${POWERMEM_BACKEND_READY:-0}" != "1" ]; then
  bash "$REPO_ROOT/scripts/powermem-server-service.sh" start
fi
```

#### 0.2.4 — Verify backend persistence (macOS/Linux)

After [0.2.3](#023--start-or-reuse-http-api), confirm the backend will survive closing the
setup shell and reopening the IDE:

```bash
bash "$REPO_ROOT/scripts/powermem-server-service.sh" status
curl -sf -m 5 "$BACKEND_URL/api/v1/system/health" || exit 1
```

**macOS — prefer LaunchAgent loaded:**

```bash
launchctl print "gui/$(id -u)/ai.powermem.server" >/dev/null 2>&1 && \
  echo "LaunchAgent running" || echo "WARNING: LaunchAgent not loaded — backend may stop later"
```

If status reports `mode LaunchAgent`, the backend should auto-start on login. If it reports
`mode detached` or LaunchAgent is missing, re-run `bash "$REPO_ROOT/scripts/powermem-server-service.sh" restart`
and inspect `/tmp/powermem-server.launchd.err`. Do **not** tell the user to restart the IDE until
`status` is healthy.

**Linux — start via systemd user service:**

```bash
if [ "$POWERMEM_OS" = "linux" ] && [ "${POWERMEM_BACKEND_READY:-0}" != "1" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/powermem-server.service" <<EOF
[Unit]
Description=PowerMem Server

[Service]
WorkingDirectory=$REPO_ROOT
ExecStart=$REPO_ROOT/.venv/bin/powermem-server --host 0.0.0.0 --port 8848
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now powermem-server.service
  else
    ( cd "$REPO_ROOT" && exec "$REPO_ROOT/.venv/bin/powermem-server" --host 0.0.0.0 --port 8848 \
      >> /tmp/powermem-server.log 2>&1 ) &
    disown 2>/dev/null || true
  fi
fi
```

**Verify (both OS):**

```bash
for i in $(seq 1 90); do
  curl -sf -m 3 "$BACKEND_URL/api/v1/system/health" && break
  sleep 2
done
curl -sf -m 5 "$BACKEND_URL/api/v1/system/health" || {
  echo "Backend still unhealthy — check service logs and .env"; exit 1
}
curl -sf -m 30 -X POST "$BACKEND_URL/api/v1/memories" \
  -H 'Content-Type: application/json' \
  -d '{"content":"PowerMem setup probe: dragonfruit-zx9","infer":false}' || {
  echo "Memory API failed — inspect server logs"; exit 1
}
```

**Service management:**

```bash
# macOS only
bash "$REPO_ROOT/scripts/powermem-server-service.sh" start|status|stop|restart|uninstall

# Linux only (systemd)
systemctl --user status|restart|stop powermem-server.service
```

Logs: macOS writes `/tmp/powermem-server.launchd.log` (stdout) and
`/tmp/powermem-server.launchd.err` (stderr, including Python stack traces); Linux uses
`journalctl --user -u powermem-server.service`.

Common macOS log messages:

| Log | Meaning |
|-----|---------|
| `Bootstrap failed: 5: Input/output error` | LaunchAgent pointed at `.venv/bin/powermem-server` or a repo-path script launchd cannot execute — fixed by `~/bin/powermem-server-launch.sh` wrapper + `POWERMEM_REPO_ROOT`. If the wrapper is already correct, the label may be **disabled** in launchd: run `launchctl enable "gui/$(id -u)/ai.powermem.server"` then `bash scripts/powermem-server-service.sh restart` |
| `Uvicorn running on http://0.0.0.0:8848` | Server started successfully |
| `Shutting down` / `Finished server process` | Graceful stop (restart, `stop` command, or IDE unrelated) |
| `[powermem-server-launch] Missing .../.venv/bin/powermem-server` with `POWERMEM_REPO_ROOT=/Users/you` | Launch wrapper in `~/bin` resolved the wrong repo root — fixed by `EnvironmentVariables.POWERMEM_REPO_ROOT` in the LaunchAgent plist |

### Shared helpers (for 0.3–0.6)

Run once per setup session before the matching IDE block.

#### Build VSIX

```bash
build_powermem_vsix() {
  cd "$EXT_DIR"
  npm install
  npm run compile
  npx --yes @vscode/vsce package --no-dependencies --allow-missing-repository --no-rewrite-relative-links
  export VSIX="$(ls -t powermem-vscode-*.vsix | head -1)"
  echo "Built: $EXT_DIR/$VSIX"
}
```

#### Probe MCP endpoint

```bash
probe_mcp_for_ide() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 5 -X POST "$BACKEND_URL/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"setup-probe","version":"0"}}}')
  if [ "$code" = "200" ] || [ "$code" = "202" ]; then
    unset PMEM_MCP_PATH
  else
    export PMEM_MCP_PATH=stdio
  fi
  echo "MCP probe HTTP $code → mode ${PMEM_MCP_PATH:-http}"
}
```

#### Write extension settings

Set `POWERMEM_IDE` to `Cursor`, `Code`, `Qoder`, or `CodeFuse`:

```bash
write_powermem_settings() {
  POWERMEM_IDE="$1" python3 <<'PY'
import json, os, platform

ide = os.environ["POWERMEM_IDE"]
if platform.system() == "Darwin":
    base = os.path.expanduser(f"~/Library/Application Support/{ide}/User")
else:
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    base = os.path.join(xdg, f"{ide}/User")
path = os.path.join(base, "settings.json")

os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.isfile(path):
    with open(path) as f:
        data = json.load(f)
data.update({
    "powermem.enabled": True,
    "powermem.backendUrl": os.environ.get("BACKEND_URL", "http://localhost:8848"),
    "powermem.connectionMode": "mcp",
    "powermem.mcpServerPath": os.environ.get("PMEM_MCP_PATH", ""),
    "powermem.seamlessMode": True,
})
with open(path, "w") as f:
    json.dump(data, f, indent=4)
    f.write("\n")
print(f"Settings written: {path}")
PY
}
```

#### Write MCP config (file-based IDEs)

Pass the target `mcp.json` path as the first argument. Uses `PMEM_MCP_PATH` env
(stdio vs HTTP) from [probe step](#probe-mcp-endpoint):

```bash
write_powermem_mcp_file() {
  POWERMEM_MCP_FILE="$1" python3 <<'PY'
import json, os
from pathlib import Path

mcp_path = Path(os.environ["POWERMEM_MCP_FILE"])
mcp_path.parent.mkdir(parents=True, exist_ok=True)

data = {"mcpServers": {}}
if mcp_path.is_file():
    data = json.loads(mcp_path.read_text())
data.setdefault("mcpServers", {})

repo_root = os.environ.get("REPO_ROOT", "")
if os.environ.get("PMEM_MCP_PATH") == "stdio":
    pmem_mcp = os.environ.get("PMEM_MCP", "powermem-mcp")
    data["mcpServers"]["powermem"] = {
        "command": pmem_mcp,
        "args": ["stdio"],
        "env": {"POWERMEM_ENV_FILE": f"{repo_root}/.env"},
    }
else:
    backend = os.environ.get("BACKEND_URL", "http://localhost:8848")
    data["mcpServers"]["powermem"] = {"url": f"{backend}/mcp"}

mcp_path.write_text(json.dumps(data, indent=4) + "\n")
print(f"MCP config written: {mcp_path}")
PY
}
```

### 0.3 — Cursor setup

Run when `POWERMEM_IDE=cursor` or user chose Cursor. Requires healthy backend from [0.2](#02--ensure-backend-is-running-env-required).

#### 0.3.1 — Install extension

```bash
build_powermem_vsix
cursor --list-extensions 2>/dev/null | grep -q 'oceanbase.powermem-vscode' || \
  cursor --install-extension "$EXT_DIR/$VSIX" --force
```

#### 0.3.2 — Probe MCP and write settings

```bash
probe_mcp_for_ide
write_powermem_settings Cursor
```

#### 0.3.3 — Link MCP

Prefer `cursor --add-mcp`:

```bash
if [ "${PMEM_MCP_PATH:-}" = "stdio" ]; then
  PMEM_MCP="$(command -v powermem-mcp || echo "$REPO_ROOT/.venv/bin/powermem-mcp")"
  cursor --add-mcp "{\"name\":\"powermem\",\"command\":\"$PMEM_MCP\",\"args\":[\"stdio\"],\"env\":{\"POWERMEM_ENV_FILE\":\"$REPO_ROOT/.env\"}}"
else
  cursor --add-mcp '{"name":"powermem","url":"http://localhost:8848/mcp"}'
fi
```

If `--add-mcp` fails, fall back to file merge:

```bash
write_powermem_mcp_file "$HOME/.cursor/mcp.json"
```

### 0.4 — VS Code setup

Run when `POWERMEM_IDE=vscode` or user chose VS Code.

#### 0.4.1 — Install extension

```bash
build_powermem_vsix
code --list-extensions 2>/dev/null | grep -q 'oceanbase.powermem-vscode' || \
  code --install-extension "$EXT_DIR/$VSIX" --force
```

#### 0.4.2 — Probe MCP and write settings

```bash
probe_mcp_for_ide
write_powermem_settings Code
```

#### 0.4.3 — MCP config (optional)

No external MCP config is required for the VS Code extension UI. If the user also
wants VS Code AI-tool MCP, merge the `powermem` entry into the workspace/user MCP
config using the same pattern as the Cursor block above.

### 0.5 — Qoder setup

Run when `POWERMEM_IDE=qoder` or user chose Qoder. Qoder has no extension CLI.

#### 0.5.1 — Install extension (Python extraction)

```bash
build_powermem_vsix
python3 <<'PY'
import json, os, subprocess, shutil, tempfile
from pathlib import Path

vsix = os.path.join(os.environ["EXT_DIR"], os.environ["VSIX"])

ext_dir = Path.home() / ".qoder/extensions/oceanbase.powermem-vscode-0.1.0"
ext_json = Path.home() / ".qoder/extensions/extensions.json"

if ext_dir.exists():
    shutil.rmtree(ext_dir, ignore_errors=True)
ext_dir.mkdir(parents=True, exist_ok=True)

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

print(f"Installed {ext_id} v{version}")
PY
```

#### 0.5.2 — Probe MCP and write settings

```bash
probe_mcp_for_ide
write_powermem_settings Qoder
```

#### 0.5.3 — Link MCP

```bash
if [ "$(uname -s)" = "Darwin" ]; then
  QODER_MCP="$HOME/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json"
else
  QODER_MCP="$HOME/.qoder/mcp.json"
fi
write_powermem_mcp_file "$QODER_MCP"
```

After all steps complete, **fully quit and reopen Qoder** — do not use "Reload Window".

**Manual install** (only if agent automation unavailable):
1. Open Qoder → Extensions view (`Cmd+Shift+X`)
2. Click `...` → "Install from VSIX..."
3. Select: `apps/vscode-extension/powermem-vscode-*.vsix`
4. Restart Qoder once

### 0.6 — CodeFuse setup

Run when `POWERMEM_IDE=codefuse` or user chose CodeFuse.

#### 0.6.1 — Install extension

```bash
build_powermem_vsix
if command -v codefuse >/dev/null 2>&1; then
  codefuse --list-extensions 2>/dev/null | grep -q 'oceanbase.powermem-vscode' || \
    codefuse --install-extension "$EXT_DIR/$VSIX" --force
else
  echo "CodeFuse CLI unavailable — use Qoder-style Python extraction with .codefuse paths (see 0.5.1)"
fi
```

#### 0.6.2 — Probe MCP and write settings

```bash
probe_mcp_for_ide
write_powermem_settings CodeFuse
```

#### 0.6.3 — Link MCP

```bash
if [ "$(uname -s)" = "Darwin" ]; then
  CODEFUSE_MCP="$HOME/Library/Application Support/CodeFuse/User/globalStorage/mcp.json"
else
  CODEFUSE_MCP="$HOME/.codefuse/mcp.json"
fi
write_powermem_mcp_file "$CODEFUSE_MCP"
```

After all steps complete, **fully quit and reopen CodeFuse** — do **not** use
"Reload Window" (CodeFuse may not fully reload extension code on a soft reload).

**Manual install** (only if agent automation unavailable):
1. Open CodeFuse → Extensions view (`Cmd+Shift+X`)
2. Click `...` → "Install from VSIX..."
3. Select: `apps/vscode-extension/powermem-vscode-*.vsix`
4. Fully quit and reopen CodeFuse

### 0.7 — Verify without extension UI

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

Confirm the search response includes `dragonfruit-zx9`.

Re-run [0.2.4](#024--verify-backend-persistence-macoslinux) immediately before telling the
user to restart the IDE.

### 0.8 — Agent completion message

Report only:

1. Backend URL and health status.
2. Backend persistence mode: **LaunchAgent** (preferred) vs **detached nohup** (fragile).
3. MCP mode: streamable HTTP vs stdio (and why, from the probe).
4. Extension installation status (VSIX installed via CLI or Python extraction).
5. Redacted `.env` summary — never echo full API keys.
6. That seamless mode + linked MCP are active; no F5 / Setup wizard / Reload Window steps remain.
7. **Critical: ONE full restart required** — but only after `powermem-server-service.sh status` is healthy. If the status bar shows **disconnected** after restart, the backend stopped — run `bash scripts/powermem-server-service.sh start`, then click the status bar → **Reconnect**.

If anything failed, fall back to Sections 1–5 and say which automated step failed.

---

> ## Restart Your IDE to Activate PowerMem
>
> All setup steps are now complete. **You must fully quit and reopen the IDE** for PowerMem to activate:
>
> | IDE | How to restart |
> |-----|---------------|
> | **Cursor** | `Cmd+Q` (macOS) → reopen Cursor, or use "Developer: Reload Window". |
> | **VS Code** | `Cmd+Q` (macOS) → reopen VS Code, or use "Developer: Reload Window". |
> | **Qoder** | `Cmd+Q` (macOS) → reopen Qoder. |
> | **CodeFuse** | `Cmd+Q` (macOS) → reopen CodeFuse. **Do not use "Reload Window"** — CodeFuse may not fully reload extension code on a soft reload. |
>
> **Before restarting, make sure the backend is running and persistent:**
>
> ```bash
> bash scripts/powermem-server-service.sh status
> curl -sf -m 5 http://localhost:8848/api/v1/system/health && echo "Backend healthy" || echo "Backend not running — start it first"
> ```
>
> **Why "disconnected" after restart?** The extension checks only
> `GET http://localhost:8848/api/v1/system/health`. If `powermem-server` is not running,
> the status bar shows **disconnected** even when MCP config in `~/.cursor/mcp.json` is correct.
> Fix: `bash scripts/powermem-server-service.sh start`, then status bar → **Reconnect**.
>
> **After restart you should see:**
> - PowerMem status bar item (bottom-right) showing **connected**
> - PowerMem commands in the command palette (`Cmd+Shift+P` → search "PowerMem")
> - MCP tools available to the AI assistant

---

## 1. Install the Extension Once (manual)

> **Agents:** complete [Section 0.2](#02--ensure-backend-is-running-env-required) first, then the matching IDE block (0.3–0.6). **Humans:** continue below.

If the extension is already installed and the **PowerMem** commands are visible in
the command palette, skip this section.

### From source (dev only)

```bash
cd apps/vscode-extension
npm install
npm run compile
```

Press **F5** only for **extension development** (debugging `src/extension.ts`). For
day-to-day use, package and install a VSIX.

### From a packaged VSIX

```bash
cd apps/vscode-extension
npm install && npm run compile
npx --yes @vscode/vsce package --no-dependencies --allow-missing-repository --no-rewrite-relative-links
# Then install for your IDE (priority order):
# cursor --install-extension powermem-vscode-*.vsix --force
# code --install-extension powermem-vscode-*.vsix --force
# Qoder: Extensions view → Install from VSIX...
# codefuse --install-extension powermem-vscode-*.vsix --force
```

After the **first** VSIX install, fully quit and reopen the IDE so
`onStartupFinished` registers the status bar and commands.

## 2. Prerequisites

Complete these steps before configuring the backend in Section 3.

### Step 2.1 — Detect context

Determine whether you are in the PowerMem source tree or using a PyPI/install-only setup:

- **SOURCE** — the repo root contains `pyproject.toml` with `name = "powermem"`.
- **PIP** — PowerMem is installed from PyPI/uv.

### Step 2.2 — Configure `.env` (interactive)

PowerMem reads configuration from `.env` in the working directory where you start
`powermem-server`. At minimum you need `LLM_PROVIDER`, `LLM_API_KEY`, and `LLM_MODEL`.

**Agents must interactively collect values.** Ask the user to supply each setting.
Do not silently reuse values from `.env.tmp` or `.env.example`.

#### 2.2.1 — Pick the `.env` directory

Prefer the PowerMem repo root when running from source.

#### 2.2.2 — Detect existing configuration

Check whether `.env` already exists with real values (not placeholders).

- **If fully configured:** reuse the file.
- **If missing or incomplete:** run the interactive flow below.

#### 2.2.3 — Interactive prompts

Ask **one setting at a time**:

| Variable | Prompt the user for |
|----------|---------------------|
| `LLM_PROVIDER` | Provider id, e.g. `openai`, `anthropic`, `qwen`, `deepseek`, `ollama`, `vllm` |
| `LLM_API_KEY` | API key or token (skip for local providers) |
| `LLM_MODEL` | Model name, e.g. `gpt-4o-mini`, `qwen-plus`, `deepseek-chat` |

#### 2.2.4 — Confirm, then write

1. Show what will be written (mask `LLM_API_KEY`).
2. Ask: **"Create/update `.env` with these values?"**
3. Only after **yes**: write the file.

### Step 2.3 — Install `powermem-server` when needed

```bash
command -v powermem-server || {
  cd /path/to/powermem
  pip install -e ".[server,mcp,cli,seekdb]"
}
```

## 3. Configure the backend (manual)

### Step 3.1 — Reuse an existing HTTP API server

```bash
curl -s -m 5 http://localhost:8848/api/v1/system/health
```

If healthy, reuse `http://localhost:8848`. Do not start a duplicate server.

### Step 3.2 — Start HTTP API if needed

```bash
powermem-server --host 0.0.0.0 --port 8848
```

### Step 3.3 — Choose the MCP endpoint for AI tools

Probe whether the HTTP API also exposes streamable HTTP MCP:

```bash
curl -i -s -m 5 -X POST http://localhost:8848/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"powermem-setup-probe","version":"0.0.0"}}}'
```

If 200/202: use `{"url": "http://localhost:8848/mcp"}`.
If 404: use MCP-only mode on the same port or stdio fallback.

### Step 3.4 — Save extension settings

> **Agents:** write user `settings.json` per the matching IDE block (0.3–0.6).

**Human fallback:**
1. Open command palette → **PowerMem: Setup**.
2. Set **Backend URL** to `http://localhost:8848`.
3. Run **Test connection**.

## 4. Link AI tools (manual)

Configure only the current IDE or target client.

### Cursor

Merge into `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

### VS Code

No external AI-tool config required for the extension's own UI.

### Qoder

**MCP config location by OS:**

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Qoder/SharedClientCache/extension/local/mcp.json` |
| Linux | `~/.qoder/mcp.json` |

Add `mcpServers.powermem` with `{"url": "http://localhost:8848/mcp"}`.

### CodeFuse

**MCP config location by OS:**

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/CodeFuse/User/globalStorage/mcp.json` |
| Linux | `~/.codefuse/mcp.json` |

Add `mcpServers.powermem` with `{"url": "http://localhost:8848/mcp"}`.

### Codex, OpenCode, and generic MCP clients

Use `apps/mcp-client/SETUP.md` instead.

### Other supported clients

| Client | Config location |
|--------|-----------------|
| Claude Desktop / Code MCP provider | `~/.claude/providers/powermem.json` |
| Windsurf | `~/.windsurf/context/powermem.json` |
| GitHub Copilot | `~/.github/copilot/powermem.json` |

## 5. Verify (manual)

### Cursor

1. Status bar shows **PowerMem** without a warning icon.
2. Run **PowerMem: Quick Note** → save `PowerMem setup probe: dragonfruit-zx9`.
3. Run **PowerMem: Query Memories** → search `dragonfruit-zx9`.
4. Confirm the saved memory appears.

### VS Code

Same steps as Cursor (status bar, Quick Note, Query Memories).

### Qoder

1. Restart Qoder once after setup.
2. Verify: status bar, commands in palette, MCP connected, tools listed.
3. Test: add memory, search for it.

### CodeFuse

1. Fully quit and reopen CodeFuse (not "Reload Window").
2. Verify: status bar, commands in palette, MCP connected, tools listed.
3. Test: add memory, search for it.
4. If status bar shows "disconnected": check **View → Output → PowerMem** for logs.

## 6. Troubleshooting

### Status bar shows disconnected

**Root cause:** the extension calls `GET {powermem.backendUrl}/api/v1/system/health` on
startup. **Disconnected = that request failed**, which almost always means `powermem-server`
is not listening on port 8848. This is independent of MCP configuration.

Diagnose in order:

```bash
# 1. Is the backend up?
bash scripts/powermem-server-service.sh status
curl -sf -m 5 http://localhost:8848/api/v1/system/health || echo "backend down"

# 2. macOS — is LaunchAgent loaded? (preferred persistence)
launchctl print "gui/$(id -u)/ai.powermem.server" 2>&1 | head -5

# 3. Recent server logs (no crash line often means process was never persistent)
tail -30 /tmp/powermem-server.launchd.log
tail -30 /tmp/powermem-server.launchd.err
```

Fix steps:

- Run the probe from [0.2.1](#021--probe-existing-backend-first-idempotent). If it fails, start the backend with the matching OS branch from [0.2.3](#023--start-or-reuse-http-api).
- On macOS, if `launchctl bootstrap` previously failed, ensure setup uses
  `scripts/powermem-server-launch.sh` (see [0.2.3](#023--start-or-reuse-http-api)).
- Confirm `$REPO_ROOT/.env` exists with valid `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`.
- Confirm `powermem.backendUrl` in settings matches the server URL.
- After the backend is healthy again, click the status bar → **Reconnect** (or restart the IDE).
- MCP-only mode does **not** satisfy the extension health check.

#### CodeFuse-specific: status bar stuck on disconnected

1. Ensure a **full restart** (not "Reload Window").
2. Check **View → Output → PowerMem** for health check logs.
3. Start the backend **before** opening CodeFuse.
4. Rebuild and reinstall the extension if building from source.

### AI tool does not see PowerMem

- Confirm you configured the current target IDE/client.
- Restart the AI tool after editing config.
- Verify the MCP config file contains a `powermem` entry.
- For Codex/OpenCode, use `apps/mcp-client/SETUP.md`.

### MCP starts but tools fail

- Confirm the configured MCP URL actually exposes `/mcp`.
- If using stdio, confirm `powermem-mcp stdio` works on your `PATH`.
- Check server logs for request errors.

## 7. Uninstall

See [`UNINSTALL.md`](UNINSTALL.md).

---

> **Reminder:** All PowerMem features become active after **one full IDE restart** (quit and reopen). See the restart table at the end of [Section 0.8](#08--agent-completion-message) for IDE-specific instructions.
