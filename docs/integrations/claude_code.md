# Claude Code

Give [Claude Code](https://code.claude.com) persistent, self-evolving memory through the first-party plugin (`memory-powermem`, under [`apps/claude-code-plugin/`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/)).

This page is the single source of truth for the Claude Code integration — the plugin's own [`README.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/README.md) links here.

## Fastest path — let Claude Code set itself up

Open Claude Code in your terminal and paste this one line:

```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```

Claude Code reads [`apps/claude-code-plugin/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/SETUP.md) — the canonical automated-setup prompt — which detects whether you are in the PowerMem **source tree** (developer) or anywhere else (**pip user**), asks you for the few required secrets, and wires everything up end-to-end.

Prefer to wire it by hand? The full plugin reference below covers every option.

---

## Features

- **Two connection modes** (aligned with the PowerMem VS Code extension). **HTTP mode is the default** (standard): REST-only via hooks, no PowerMem MCP tools in chat. **MCP mode** is optional when you want `search_memories` / `add_memory` in the conversation. See [Configuration](#configuration).
- **HTTP mode (default)**: Root `.mcp.json` ships with empty `mcpServers`. Hooks use **`POST /api/v1/memories`** (`POWERMEM_BASE_URL`, default `http://localhost:8848`).
- **MCP mode (optional)**: Copy [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json) to `.mcp.json` (or run `apply-connection-mode.sh mcp`). Claude gets PowerMem tools over **HTTP** `…/mcp` or **stdio**.
- **Skills**: `/memory-powermem:remember` and `/memory-powermem:recall` — effective in **MCP mode**; in default HTTP mode they cannot drive tools.
- **Seamless REST capture**: Hooks run in **both** modes. Optional **file poller** — see [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md).
- **Auto-retrieval (no MCP required, on by default)**: The `UserPromptSubmit` hook calls **`POST /api/v1/memories/search`** with the user’s prompt and injects hits via [`additionalContext`](https://code.claude.com/docs/en/hooks#userpromptsubmit). Set **`POWERMEM_PROMPT_SEARCH=0`** (or `false` / `no` / `off`) to disable — saves a search round-trip per turn. Works in **HTTP and MCP** modes.

## Runtime requirements (end users)

| Piece | Needs Python? | Notes |
|--------|----------------|-------|
| Claude Code | No | |
| MCP tools | No | **Off by default** (HTTP mode). Run `apply-connection-mode.sh mcp` to enable. |
| **Hooks** (transcript / compact → HTTP API) | **No** | Native binaries under `hooks/bin/` + `run-hook.sh` (macOS/Linux) or PowerShell on Windows. **`POWERMEM_BASE_URL` defaults to `http://localhost:8848`.** |
| Optional **file poller** | No | Same binary: `sh hooks/run-hook.sh poll` — see [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md). |

**macOS / Linux:** default `hooks/hooks.json` runs `sh …/run-hook.sh`. POSIX `sh` is always present.

**Windows (native, no Git Bash):** if `sh` is missing, merge the commands from [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) into your Claude `settings.json` so hooks call `powershell.exe -File …/run-hook.ps1`. The zip includes `hooks/bin/powermem-hook-windows-amd64.exe` (add `windows/arm64` to the build script if you need it).

**Rebuilding binaries** (developers / CI): Go **1.22+**, then `bash scripts/build-hook-binaries.sh` or `make build-claude-hook` from the repo root. `make package-claude-plugin` builds them automatically before zipping.

## Prerequisites

1. **PowerMem HTTP API** reachable from the machine running Claude (e.g. `powermem-server --port 8848`). Default hooks use **`http://localhost:8848`** — override with `POWERMEM_BASE_URL` for a remote server.
2. **MCP mode only:** additionally expose MCP (same host, usually `/mcp`) or stdio `powermem-mcp`, and switch `.mcp.json` via [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json).
3. **Claude Code** (VS Code extension or CLI) with plugin support.

## Manual Installation

Set up the integration **from source** — this is **HTTP mode** (the default): hooks push transcripts to the REST API and inject search results per turn, with no in-chat tools.

### Step 1 — Download the source

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

### Step 2 — Configure `.env`

Copy the template and set your **LLM API key** — the only required credential. Storage defaults to the embedded **seekdb** (no separate database), and the embedder to a local `all-MiniLM-L6-v2` model (no API key, auto-downloaded on first use).

```bash
cp .env.example .env
# then edit .env and set at least:
#   LLM_PROVIDER=anthropic        # or openai / qwen / ...
#   LLM_API_KEY=sk-...
#   LLM_MODEL=claude-3-5-sonnet-latest
```

Every available setting is documented under [Configuration](#configuration); `pmem config init` can also generate `.env` interactively.

### Step 3 — Install PowerMem and build the hook binaries

`pip install -e '.[server,seekdb]'` provides the `powermem-server` and `pmem` commands plus the zero-config local seekdb path; `make build-claude-hook` compiles the native Go hook binaries (requires **Go 1.22+**):

```bash
pip install -e '.[server,seekdb]'
make build-claude-hook        # outputs apps/claude-code-plugin/hooks/bin/
```

### Step 4 — Start the HTTP API server

Hooks default to `http://localhost:8848`. Leave this running (or start it as a background service):

```bash
powermem-server --host 0.0.0.0 --port 8848
```

### Step 5 — Load the plugin into Claude Code

```bash
claude --plugin-dir "$(pwd)/apps/claude-code-plugin"
```

### Step 6 — Verify

End the session (or run `/compact`), then look for `POST /api/v1/memories` in the server log; run `/hooks` inside Claude Code to confirm the entries are registered. See [Troubleshooting](#troubleshooting-no-requests-while-vibe-coding) if nothing shows up.

---

### Other ways to load the plugin

#### Option A: Load from directory (development)

```bash
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin
```

#### Option B: Install from marketplace

When the PowerMem marketplace entry is available, install it with:

```text
/plugin marketplace add oceanbase/powermem
/plugin install memory-powermem@powermem
/reload-plugins
/memory-powermem:init
```

The marketplace step installs the Claude Code plugin connector. The
`/memory-powermem:init` step prepares the PowerMem backend in a plugin-local venv
and installs `powermem` from PyPI by default. The PyPI release must include the
backend features required by the plugin, including the default local embedding
dependencies. For branch testing before release, install the marketplace from a
branch and run init with `POWERMEM_INIT_PACKAGE` pointing at the same Git branch
or commit:

```text
/plugin marketplace add owner/powermem@<branch>
/plugin install memory-powermem@powermem
/reload-plugins
```

```bash
POWERMEM_INIT_PACKAGE='powermem @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
```

#### Option C: Pack and copy to another machine (offline / internal)

From the **powermem repo root**:

```bash
make package-claude-plugin
```

Or run the script directly:

```bash
bash apps/claude-code-plugin/scripts/package-plugin.sh
```

This writes **`apps/claude-code-plugin/dist/powermem-claude-code-plugin-<version>.zip`**. Share that zip (USB, internal artifact server, etc.).

**On the other computer:**

1. Unzip → you get a folder `powermem-claude-code-plugin/` containing `.claude-plugin/`, `hooks/`, `skills/`, `.mcp.json`, etc.
2. Point Claude Code at that folder (absolute path recommended):

   ```bash
   # Optional: hooks default to http://localhost:8848 if POWERMEM_BASE_URL is unset
   export POWERMEM_BASE_URL=https://your-team-powermem.example.com   # team server only
   claude --plugin-dir /path/to/powermem-claude-code-plugin
   ```

3. Requirements on that machine: **no Python**; use **macOS/Linux** `sh` or follow **Windows** PowerShell hooks above. **HTTP API** must be reachable for hooks (and `/mcp` too if you enable MCP mode).

To publish a zip **with MCP enabled by default**, replace root `.mcp.json` with `config/mcp-mode.mcp.json` before `make package-claude-plugin`, or document that users run `apply-connection-mode.sh mcp`.

## Uninstall and update

### Uninstall

How you remove the plugin depends on how you enabled it:

| How you installed | What to do |
|-------------------|------------|
| **`claude --plugin-dir /path/to/...`** | Stop passing `--plugin-dir` (remove it from shell aliases, scripts, or IDE task). Optionally delete the plugin folder. Nothing is left in `~/.claude` **unless** you also changed global settings (see below). |
| **Zip / copied folder** | Delete the unzipped directory. Stop using `--plugin-dir` pointing at it. |
| **Git clone / repo path** | Stop using `--plugin-dir` for that path; remove the clone if you no longer need it. |
| **Marketplace / built-in plugin UI** | Run `/plugin uninstall memory-powermem@powermem`, then `/reload-plugins`. To remove the marketplace entry as well, run `/plugin marketplace remove powermem`. |
| **You merged [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) into `settings.json`** | Edit `~/.claude/settings.json` or `.claude/settings.json` in the project and remove the `UserPromptSubmit` / `SessionEnd` / `PostCompact` hook entries that call `run-hook.ps1` (or restore a backup). Otherwise hooks keep running even after the plugin folder is deleted. |

The hook binary only **writes** to your PowerMem server; it does not install a system daemon. No separate “service uninstall” is required.

### Update

| Install style | Update steps |
|---------------|--------------|
| **Zip** | Download the new `.zip`, replace the old folder (delete the previous `powermem-claude-code-plugin` tree, unzip the new one to the same or a new path), then start Claude with `--plugin-dir` pointing at the new folder. |
| **Repo / `git`** | `git pull` (or fetch the release you want), run `make package-claude-plugin` or `bash scripts/package-plugin.sh` if you need a fresh zip, then restart Claude Code. |
| **Marketplace** | Run `/plugin uninstall memory-powermem@powermem`, reinstall from the marketplace, then run `/reload-plugins`. If the backend package changed, re-run `/memory-powermem:init` so the plugin-local venv installs the new PyPI release. |

After updating, restart the Claude Code session (or the whole app) so MCP config, skills, and hooks reload.

## Configuration

### Two PowerMem modes (HTTP default, MCP optional)

Same **MCP / HTTP** split as elsewhere in PowerMem. **Standard shipping = HTTP mode**: root `.mcp.json` has **`mcpServers: {}`**. **Hooks always use REST** in both modes.

| Mode | Plugin root `.mcp.json` | Claude in-chat | Silent capture (hooks → REST) |
|------|-------------------------|----------------|--------------------------------|
| **HTTP mode (default)** | Empty `mcpServers` — same as [`config/http-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/http-mode.mcp.json) | No PowerMem MCP tools | Yes (`POWERMEM_BASE_URL`, default `http://localhost:8848`) |
| **MCP mode** | Includes `powermem` — [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json) | Yes — `search_memories`, `add_memory`, … | Yes |

**Switch mode** (from the plugin directory):

```bash
bash scripts/apply-connection-mode.sh http  # restore standard (default) HTTP-only mode
bash scripts/apply-connection-mode.sh mcp   # enable in-chat PowerMem tools
```

Restart Claude Code after changing `.mcp.json`. See [`config/README.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/README.md).

**Naming note:** In **MCP mode**, `transport: "http"` means “connect to the **MCP** endpoint over HTTP” (`https://host/mcp`), not “replace MCP with REST.” **HTTP mode** means “no MCP entry for PowerMem”; REST is still used by hooks.

### MCP mode: team or local URL

After `apply-connection-mode.sh mcp`, edit `.mcp.json` or `config/mcp-mode.mcp.json` before copying. Same host as your REST API, MCP path is usually `/mcp`:

```json
{
  "mcpServers": {
    "powermem": {
      "transport": "http",
      "url": "https://powermem.example.com/mcp"
    }
  }
}
```

**stdio MCP** (local `powermem-mcp` process) — in **MCP mode**, replace the `powermem` block with:

```json
{
  "mcpServers": {
    "powermem": {
      "transport": "stdio",
      "command": "powermem-mcp",
      "args": ["stdio"]
    }
  }
}
```

Ensure PowerMem is installed (`pip install "powermem[mcp,seekdb]"`) and a `.env` is available when using stdio.

### HTTP mode: REST only (standard)

This is the **default** root `.mcp.json`. Claude has **no** PowerMem MCP tools; skills that reference those tools have nothing to call. **Hooks** still send transcripts / compact summaries to `POST /api/v1/memories`. To reset after trying MCP: `bash scripts/apply-connection-mode.sh http`.

### Seamless recording (hooks + HTTP API)

The plugin ships [`hooks/hooks.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.json), [`hooks/run-hook.sh`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/run-hook.sh), and **native** `hooks/bin/powermem-hook-*` (built from [`cmd/powermem-hook`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/cmd/powermem-hook/)). When the plugin is enabled, Claude Code merges these hooks:

| Hook | What happens |
|------|----------------|
| `UserPromptSubmit` | By default, **`POST …/api/v1/memories/search`** with the submitted `prompt`; top results are injected as **additional context** for that turn ([Claude Code hooks](https://code.claude.com/docs/en/hooks#userpromptsubmit)). Set **`POWERMEM_PROMPT_SEARCH=0`** (or `false` / `no` / `off`) to skip search (hook still registered; overhead is small when disabled). |
| `SessionEnd` | Full **transcript** from `transcript_path` (parsed JSONL: user/assistant/summary lines) → **`POST …/api/v1/memories`**. |
| `PostCompact` | The **`compact_summary`** field after `/compact` or auto-compact → **`POST …/api/v1/memories`**. |

**Write** hooks use `POST {POWERMEM_BASE_URL}/api/v1/memories`. **Prompt search** uses `POST {POWERMEM_BASE_URL}/api/v1/memories/search`. Neither path requires MCP.

Optional environment variables (where you launch Claude Code):

| Variable | Required | Description |
|----------|----------|-------------|
| `POWERMEM_BASE_URL` | No | Defaults to **`http://localhost:8848`** (same host as default `.mcp.json`, without `/mcp`). Set for a team gateway, e.g. `https://powermem.example.com`. |
| `POWERMEM_API_KEY` | If server uses auth | Sent as `X-API-Key` |
| `POWERMEM_USER_ID` | No | Defaults to OS login name |
| `POWERMEM_AGENT_ID` | No | Optional `agent_id` on memories |
| `POWERMEM_HOOK_MAX_CHARS` | No | Transcript cap (default `120000`) |
| `POWERMEM_INFER_TRANSCRIPT` | No | Set `1` to enable server-side infer on large transcripts (default off) |
| `POWERMEM_INFER_COMPACT` | No | Set `0` to disable infer on compact summaries (default on) |
| `POWERMEM_PROMPT_SEARCH` | No | **Default: on** — injects semantic search results on every user prompt via `UserPromptSubmit`. Set **`0`** / **`false`** / **`no`** / **`off`** to disable. |
| `POWERMEM_PROMPT_SEARCH_LIMIT` | No | Max memories returned per prompt (default **8**, cap **30**). |
| `POWERMEM_PROMPT_SEARCH_MAX_CHARS` | No | Cap on injected context string (default **24000**). |

**SessionEnd timeout:** Claude Code defaults to a short timeout for `SessionEnd` hooks. The hook **returns immediately** and uploads in a **detached worker process**, so large transcripts still upload without blocking exit. If you ever switch to a synchronous upload inside the hook, raise `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` (see [Claude Code hooks – SessionEnd](https://code.claude.com/docs/en/hooks#sessionend)).

### Troubleshooting: “no requests” while vibe-coding

What you see is often **expected**:

1. **Default HTTP mode** — There are **no** PowerMem MCP tools during chat, so Claude does **not** call `/mcp` on each message. **`POST /api/v1/memories`** (writes) still come from **`SessionEnd`** / **`PostCompact`**, not every reply. By default, **`POST /api/v1/memories/search`** runs **on each user message** via `UserPromptSubmit`; set **`POWERMEM_PROMPT_SEARCH=0`** to turn that off.
2. **Not every hook is per-turn** — `SessionEnd` runs when the **session ends** (quit, `/clear`, `/resume` switch, etc.). `PostCompact` runs after **manual or auto compact**, not after every reply.
3. **Those GETs** (`/system/status`, `/memories/stats`, …) usually come from another client (e.g. **PowerMem VS Code extension** dashboard), not from Claude Code hooks.

**How to verify hooks:**

- **End the Claude Code session** (exit the CLI session that used `--plugin-dir`), then check server logs for **`POST /api/v1/memories`** (the worker runs shortly after exit).
- Or trigger **`/compact`** (or wait for auto-compact) and look for a compact-summary write.
- In Claude Code, type **`/hooks`** and confirm `UserPromptSubmit` (if present) / `SessionEnd` / `PostCompact` list this plugin’s command (see [hooks menu](https://code.claude.com/docs/en/hooks#the-hooks-menu)).

**If you want traffic during the conversation:**

- **`POWERMEM_PROMPT_SEARCH` is on by default**, so each user message triggers **`POST /api/v1/memories/search`** and retrieved memories are **injected automatically** (no MCP tools needed). Set **`POWERMEM_PROMPT_SEARCH=0`** to turn that off.
- Or switch to **MCP mode** (`bash scripts/apply-connection-mode.sh mcp`) so Claude can call memory tools when it chooses — traffic goes to **`/mcp`**, not necessarily the same paths as the dashboard GETs.
- Or rely on **VS Code extension** save capture / `sh hooks/run-hook.sh poll` for file-based writes.

### Optional: workspace file watcher (CLI / no VS Code)

If engineers use **Claude Code without** the [PowerMem VS Code extension](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) (which already **auto-captures on save** against `powermem.backendUrl`), run the native poller:

```bash
export POWERMEM_BASE_URL=https://powermem.example.com
export POWERMEM_API_KEY=...   # if required
export POWERMEM_WATCH_ROOT=/path/to/repo
sh hooks/run-hook.sh poll
```

See [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md) for environment variables.

## Usage

- **Default (HTTP mode):** Hooks capture to REST automatically; no PowerMem tools in chat. **Per-prompt semantic retrieval is on by default** (see [Seamless recording](#seamless-recording-hooks--http-api)); set **`POWERMEM_PROMPT_SEARCH=0`** to disable.
- **MCP mode:** Run `apply-connection-mode.sh mcp`, then PowerMem tools appear; use **/memory-powermem:remember** / **recall** with real tool backing. Per-prompt injection stays **on by default**; set **`POWERMEM_PROMPT_SEARCH=0`** if you only want explicit MCP tool use.
- In **both** modes, transcript/compact hooks write to REST (`POWERMEM_BASE_URL`, default `http://localhost:8848`) without the model calling tools.

## Links

- [PowerMem](https://github.com/oceanbase/powermem)
- [PowerMem MCP docs](../api/0004-mcp.md)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
