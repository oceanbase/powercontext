# Claude Code

Give [Claude Code](https://code.claude.com) persistent, self-evolving memory through the first-party plugin (`memory-powermem`, under [`apps/claude-code-plugin/`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/)).

This page is the single source of truth for the Claude Code integration — the plugin's own [`README.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/README.md) links here.

## Fastest path — let Claude Code set itself up

Open Claude Code in your terminal and paste this one line:

```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```

Claude Code reads [`apps/claude-code-plugin/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/SETUP.md) — the canonical automated-setup prompt — which detects whether you are in the PowerMem **source tree** (developer) or anywhere else (**PyPI/MCP user**), asks you for the few required secrets, and wires everything up end-to-end.

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
| **Hooks** (event-driven writes/search → HTTP API) | **No** | Git/marketplace installs and release plugin zips include native binaries under `hooks/bin/` + `run-hook.sh` (macOS/Linux) or PowerShell on Windows. **`POWERMEM_BASE_URL` defaults to `http://localhost:8848`.** |
| Optional **file poller** | No | Same binary: `sh hooks/run-hook.sh poll` — see [watcher/README.md](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/watcher/README.md). |

**macOS / Linux:** default `hooks/hooks.json` runs `sh …/run-hook.sh`. POSIX `sh` is always present.

**Windows (native, no Git Bash):** if `sh` is missing, merge the commands from [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) into your Claude `settings.json` so hooks call `powershell.exe -File …/run-hook.ps1`. Git/marketplace installs and release zips include `hooks/bin/powermem-hook-windows-amd64.exe` (add `windows/arm64` to the build script if you need it).

**Rebuilding binaries** (developers / CI): Go **1.22+**, then `make build-claude-hook` or `bash apps/claude-code-plugin/scripts/build-hook-binaries.sh` from the repo root. Commit refreshed `hooks/bin/powermem-hook-*` binaries when hook code changes so Git/marketplace installs remain runnable. `make package-claude-plugin` also rebuilds them automatically before zipping.

## Prerequisites

1. **PowerMem HTTP API** reachable from the machine running Claude (e.g. `powermem-server --port 8848`). Default hooks use **`http://localhost:8848`** — override with `POWERMEM_BASE_URL` for a remote server.
2. **MCP mode only:** additionally expose MCP (same host, usually `/mcp`) or stdio `powermem-mcp`, and switch `.mcp.json` via [`config/mcp-mode.mcp.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/config/mcp-mode.mcp.json).
3. **Claude Code** (VS Code extension or CLI) with plugin support.

## Manual Installation

Set up the integration **from source** — this is **HTTP mode** (the default): hooks send event-driven writes/search to the REST API and inject search results per turn, with no in-chat tools.

### Step 1 — Download the source

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

### Step 2 — Configure `.env`

Copy the template and set your Anthropic credential. For direct Anthropic API
access use `LLM_API_KEY`; for a Claude Code-style bearer-token gateway use
`LLM_AUTH_TOKEN` together with `ANTHROPIC_LLM_BASE_URL`. Storage defaults to a
local **SQLite** database (no separate service), and the embedder to a local
`sentence-transformers/all-MiniLM-L6-v2` model (no API key, auto-downloaded on
first use). Set `DATABASE_PROVIDER=oceanbase` / the OceanBase settings when you
want the embedded seekdb path instead.

```bash
cp .env.example .env
# then edit .env and set at least:
#   LLM_PROVIDER=anthropic        # or openai / qwen / ...
#   LLM_API_KEY=sk-...
#   LLM_MODEL=claude-3-5-sonnet-latest
#
# or for an Anthropic-compatible gateway:
#   LLM_PROVIDER=anthropic
#   LLM_AUTH_TOKEN=...
#   ANTHROPIC_LLM_BASE_URL=https://your-gateway.example.com
#   LLM_MODEL=anthropic/claude-sonnet-4.6
```

Every available setting is documented under [Configuration](#configuration); `pmem config init` can also generate `.env` interactively.

### Step 3 — Install uv

PowerMem uses `uv` for Python environment creation and package installation.
Install it once:

```bash
# Non-CN networks
curl -LsSf https://astral.sh/uv/install.sh | sh

# CN networks
export UV_DOWNLOAD_URL=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/
curl -sL https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/uv-installer.sh | sh

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
uv --version
```

### Step 4 — Install PowerMem

`uv pip install -e '.[server,extras]'` provides the `powermem-server` and
`pmem` commands plus the zero-config local SQLite path and local embedder.
Git/marketplace installs include native hook binaries under
`apps/claude-code-plugin/hooks/bin/`, so Go is only needed when refreshing those
binaries from hook source changes:

```bash
uv venv venv --python python3.11
source venv/bin/activate
uv pip install --python "$VIRTUAL_ENV/bin/python" -e '.[server,extras]'
# Optional after hook source changes:
# make build-claude-hook      # refreshes apps/claude-code-plugin/hooks/bin/
```

### Step 5 — Start the HTTP API server

Hooks default to `http://localhost:8848`. Leave this running (or start it as a background service):

```bash
powermem-server --host 0.0.0.0 --port 8848
```

### Step 6 — Load the plugin into Claude Code

```bash
claude --plugin-dir "$(pwd)/apps/claude-code-plugin"
```

### Step 7 — Verify

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
`/memory-powermem:init` step prepares the PowerMem backend by ensuring `uv`, then
starts it with the uvx-style launcher. The package spec depends on the storage
backend: the default SQLite path uses `powermem[server,extras]` (pulls
`sentence-transformers` for the local `huggingface` embedder), while the
OceanBase/seekdb path uses `powermem[server,seekdb]`. The PyPI release must
include the backend features required by the plugin, including the default local
embedding dependencies. If `uv` is missing, init installs it automatically:
non-CN networks use the official Astral installer, while CN networks use the USTC
mirror at
`https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/`.
CN package resolution uses `--default-index https://pypi.tuna.tsinghua.edu.cn/simple`.
For branch testing before release, install the marketplace from a branch and run
init with `POWERMEM_INIT_PACKAGE` pointing at the same Git branch or commit; init
passes it to `uvx --from`:

```text
/plugin marketplace add https://github.com/owner/powermem.git#<branch>
/plugin install memory-powermem@powermem
/reload-plugins
```

```bash
# Default SQLite path
POWERMEM_INIT_PACKAGE='powermem[server,extras] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
# OceanBase/seekdb path
POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  POWERMEM_INIT_DATABASE_PROVIDER=oceanbase \
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
| **You merged [`hooks/hooks.windows.example.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.windows.example.json) into `settings.json`** | Edit `~/.claude/settings.json` or `.claude/settings.json` in the project and remove every PowerMem hook entry that calls `run-hook.ps1` (or restore a backup). Otherwise hooks keep running even after the plugin folder is deleted. |

The hook binary only **writes** to your PowerMem server; it does not install a system daemon. No separate “service uninstall” is required.

### Update

| Install style | Update steps |
|---------------|--------------|
| **Zip** | Download the new `.zip`, replace the old folder (delete the previous `powermem-claude-code-plugin` tree, unzip the new one to the same or a new path), then start Claude with `--plugin-dir` pointing at the new folder. |
| **Repo / `git`** | `git pull` (or fetch the release you want), run `make package-claude-plugin` or `bash apps/claude-code-plugin/scripts/package-plugin.sh` if you need a fresh zip, then restart Claude Code. |
| **Marketplace** | Run `/plugin uninstall memory-powermem@powermem`, reinstall from the marketplace, then run `/reload-plugins`. If the backend package changed, re-run `/memory-powermem:init` so uvx resolves the new PyPI release. |

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

Ensure PowerMem is installed (`uv pip install --python "$VIRTUAL_ENV/bin/python" "powermem[server,seekdb]"`) and a `.env` is available when using stdio.

### HTTP mode: REST only (standard)

This is the **default** root `.mcp.json`. Claude has **no** PowerMem MCP tools; skills that reference those tools have nothing to call. **Hooks** still send event-driven writes such as session transcripts, compact snapshots, tool outcomes, and lifecycle events to `POST /api/v1/memories`. To reset after trying MCP: `bash scripts/apply-connection-mode.sh http`.

### Seamless recording (hooks + HTTP API)

The plugin ships [`hooks/hooks.json`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/hooks.json), [`hooks/run-hook.sh`](https://github.com/oceanbase/powermem/blob/main/apps/claude-code-plugin/hooks/run-hook.sh), and **native** `hooks/bin/powermem-hook-*` binaries built from [`cmd/powermem-hook`](https://github.com/oceanbase/powermem/tree/main/apps/claude-code-plugin/cmd/powermem-hook/). When the plugin is enabled, Claude Code merges these hooks:

| Hook | What happens |
|------|----------------|
| `SessionStart` | By default, **`POST …/api/v1/memories/search`** with bounded session metadata such as `cwd`, `session_title`, `source`, and `agent_type`; hits are injected as **additional context** before the first turn. Set **`POWERMEM_SESSION_START_SEARCH=0`** to disable. |
| `UserPromptSubmit` | By default, **`POST …/api/v1/memories/search`** with the submitted `prompt`; top results are injected as **additional context** for that turn ([Claude Code hooks](https://code.claude.com/docs/en/hooks#userpromptsubmit)). Set **`POWERMEM_PROMPT_SEARCH=0`** (or `false` / `no` / `off`) to skip search (hook still registered; overhead is small when disabled). |
| `SessionEnd` | Full **transcript** from `transcript_path` (parsed JSONL: user/assistant/summary lines) → **`POST …/api/v1/memories`**. |
| `PostCompact` | The **`compact_summary`** field after `/compact` or auto-compact → **`POST …/api/v1/memories`**. |
| `PreCompact` | Bounded tail snapshot from `transcript_path` before compaction → **`POST …/api/v1/memories`**, with transcript fingerprint and byte offsets for auditability. |
| `PostToolUse` | Structured summaries for high-signal successful tools (`Write`, `Edit`, `MultiEdit`, `Bash`, `Agent`, `ExitPlanMode` by default) → **`POST …/api/v1/memories`**. Content and metadata are scrubbed, and deterministic event IDs are included when `session_id` and `tool_use_id` are available. |
| `PostToolUseFailure` | Structured summaries for failed tools → **`POST …/api/v1/memories`** with `success=false`, error metadata, bounded scrubbed summaries, and interrupts skipped by default. |
| `Stop` | Optional lightweight per-turn rollup after the main agent finishes responding → **`POST …/api/v1/memories`**. Disabled by default because it can run frequently; set **`POWERMEM_CAPTURE_STOP_ROLLUP=1`** to enable. |
| `SubagentStart` / `SubagentStop` | Lifecycle observations keyed by the hook event name, not by `transcript_path`; metadata keeps bounded allowlisted link fields, not the full raw lifecycle payload. |
| `TaskCreated` / `TaskCompleted` | Task lifecycle observations keyed by the hook event name, with link fields such as `session_id`, `task_id`, and `tool_use_id` when present. |

**Write** hooks use `POST {POWERMEM_BASE_URL}/api/v1/memories`. **Prompt and session-start search** use `POST {POWERMEM_BASE_URL}/api/v1/memories/search`. Neither path requires MCP.

Optional environment variables (where you launch Claude Code):

| Variable | Required | Description |
|----------|----------|-------------|
| `POWERMEM_BASE_URL` | No | Defaults to **`http://localhost:8848`** (same host as default `.mcp.json`, without `/mcp`). Set for a team gateway, e.g. `https://powermem.example.com`. |
| `POWERMEM_API_KEY` | If server uses auth | Sent as `X-API-Key` |
| `POWERMEM_USER_ID` | No | Defaults to OS login name |
| `POWERMEM_AGENT_ID` | No | Optional `agent_id` on memories |
| `POWERMEM_HOOK_MAX_CHARS` | No | Transcript cap (default `120000`) |
| `POWERMEM_HOOK_SCRUB` | No | Deterministic local scrubber for hook payloads. Default `1`; set `0` / `false` / `no` / `off` only if you want raw hook data sent to the configured server. |
| `POWERMEM_HOOK_PRIVACY_LEVEL` | No | `standard` (default) redacts high-confidence credential patterns; `strict` also redacts common emails and phone numbers. |
| `POWERMEM_HOOK_SECRET_ACTION` | No | `redact` (default) replaces matched values; `block` skips the memory write successfully when a high-confidence credential is found. |
| `POWERMEM_HOOK_PATH_PRIVACY` | No | Path handling for content and metadata: `home` (default; home paths become `~/...`, other absolute paths keep only the basename), `basename`, `omit`, or `full`. |
| `POWERMEM_HOOK_SEARCH_SECRET_POLICY` | No | Prompt-search handling for high-confidence credential patterns: `skip` (default), `redact`, or `off` (disables only the search secret skip/redact policy; path privacy and strict PII scrubbing still apply when hook scrubbing is enabled). |
| `POWERMEM_INFER_TRANSCRIPT` | No | Set `1` to enable server-side infer on large transcripts (default off) |
| `POWERMEM_INFER_COMPACT` | No | Set `0` to disable infer on compact summaries (default on) |
| `POWERMEM_PROMPT_SEARCH` | No | **Default: on** — injects semantic search results on every user prompt via `UserPromptSubmit`. Set **`0`** / **`false`** / **`no`** / **`off`** to disable. |
| `POWERMEM_PROMPT_SEARCH_LIMIT` | No | Max memories returned per prompt (default **8**, cap **30**). |
| `POWERMEM_PROMPT_SEARCH_MAX_CHARS` | No | Cap on injected context string (default **24000**). |
| `POWERMEM_SESSION_START_SEARCH` | No | **Default: on** — injects semantic search results at `SessionStart` using bounded session metadata. Set **`0`** / **`false`** / **`no`** / **`off`** to disable. |
| `POWERMEM_SESSION_START_LIMIT` | No | Max memories returned for `SessionStart` search (default **6**, cap **30**). |
| `POWERMEM_SESSION_START_MAX_CHARS` | No | Cap on `SessionStart` injected context string (default **16000**). |
| `POWERMEM_CAPTURE_PRECOMPACT` | No | Set `0` / `false` / `no` / `off` to disable `PreCompact` snapshots (default on). |
| `POWERMEM_PRECOMPACT_MAX_CHARS` | No | Max transcript tail characters for `PreCompact` snapshots (default **120000**). |
| `POWERMEM_PRECOMPACT_TAIL_LINES` | No | Max transcript tail lines for `PreCompact` snapshots (default **200**). |
| `POWERMEM_INFER_PRECOMPACT` | No | Set `1` to enable server-side infer for `PreCompact` snapshots (default off). |
| `POWERMEM_CAPTURE_TOOL_SUCCESS` | No | Set `0` / `false` / `no` / `off` to disable `PostToolUse` capture (default on). |
| `POWERMEM_TOOL_SUCCESS_INCLUDE` | No | Comma-separated allowed tool names. Defaults to `Write,Edit,MultiEdit,Bash,Agent,ExitPlanMode`; `*` allows all tools. |
| `POWERMEM_TOOL_SUCCESS_EXCLUDE` | No | Comma-separated denied tool names. Exclude wins over include; `*` disables all tool success capture. |
| `POWERMEM_TOOL_EVENT_MAX_CHARS` | No | Max characters for a structured tool event memory (default **6000**). |
| `POWERMEM_INFER_TOOL_EVENTS` | No | Set `1` to enable server-side infer for tool event memories (default off). |
| `POWERMEM_CAPTURE_TOOL_FAILURES` | No | Set `0` / `false` / `no` / `off` to disable `PostToolUseFailure` capture (default on). |
| `POWERMEM_CAPTURE_INTERRUPTS` | No | Set `1` to capture interrupted tool events; interrupts are skipped by default. |
| `POWERMEM_TOOL_FAILURE_MAX_CHARS` | No | Max characters for a structured tool failure memory (default **6000**). |
| `POWERMEM_INFER_TOOL_FAILURES` | No | Set `1` to enable server-side infer for failed tool memories (default off). |
| `POWERMEM_CAPTURE_STOP_ROLLUP` | No | Set `1` to enable lightweight `Stop` rollup capture (default off). |
| `POWERMEM_STOP_MAX_CHARS` | No | Max characters for the `Stop` final-message preview (default **3000**). |
| `POWERMEM_INFER_STOP` | No | Set `1` to enable server-side infer for `Stop` rollups (default off). |
| `POWERMEM_CAPTURE_SUBAGENTS` | No | Set `0` / `false` / `no` / `off` to disable subagent lifecycle capture (default on). |
| `POWERMEM_CAPTURE_TASKS` | No | Set `0` / `false` / `no` / `off` to disable task lifecycle capture (default on). |
| `POWERMEM_INFER_LIFECYCLE_EVENTS` | No | Set `1` to enable server-side infer for all lifecycle events (default off). |
| `POWERMEM_INFER_SUBAGENT_STOP` | No | Set `1` to enable infer for `SubagentStop` when the generic lifecycle infer flag is off. |
| `POWERMEM_INFER_TASK_COMPLETED` | No | Set `1` to enable infer for `TaskCompleted` when the generic lifecycle infer flag is off. |

The hook scrubber runs before hook writes/searches, including session, compact, tool, stop, lifecycle, prompt-search, workspace-file, and detached-worker handoff paths. Write metadata includes a `privacy` object with the active level, path mode, action, and redaction counts; original matched values are not recorded there.

**SessionEnd timeout:** Claude Code defaults to a short timeout for `SessionEnd` hooks. The hook **returns immediately** and uploads in a **detached worker process**, so large transcripts still upload without blocking exit. If you ever switch to a synchronous upload inside the hook, raise `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` (see [Claude Code hooks – SessionEnd](https://code.claude.com/docs/en/hooks#sessionend)).

### Troubleshooting: “no requests” while vibe-coding

What you see is often **expected**:

1. **Default HTTP mode** — There are **no** PowerMem MCP tools during chat, so Claude does **not** call `/mcp` on each message. **`POST /api/v1/memories/search`** runs on each user message via `UserPromptSubmit` by default; set **`POWERMEM_PROMPT_SEARCH=0`** to turn that off.
2. **Write hooks are event-driven** — **`POST /api/v1/memories`** writes can come from `SessionEnd`, `PostCompact`, `PreCompact`, default-on tool success/failure captures, and default-on subagent/task lifecycle events. They still do not run after every assistant reply unless you opt into `Stop` rollups with **`POWERMEM_CAPTURE_STOP_ROLLUP=1`**.
3. **Those GETs** (`/system/status`, `/memories/stats`, …) usually come from another client (e.g. **PowerMem VS Code extension** dashboard), not from Claude Code hooks.

**How to verify hooks:**

- **End the Claude Code session** (exit the CLI session that used `--plugin-dir`), then check server logs for a `SessionEnd` **`POST /api/v1/memories`** write (the worker runs shortly after exit).
- Or trigger **`/compact`** (or wait for auto-compact) and look for `PreCompact` / `PostCompact` writes.
- Run a high-signal tool such as `Bash`, `Write`, or `Edit` and look for `PostToolUse` or `PostToolUseFailure` writes.
- In Claude Code, type **`/hooks`** and confirm the hook events in the table above list this plugin’s command (see [hooks menu](https://code.claude.com/docs/en/hooks#the-hooks-menu)).

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
- In **both** modes, event-driven write hooks send session, compact, tool, and lifecycle memories to REST (`POWERMEM_BASE_URL`, default `http://localhost:8848`) without the model calling tools.

## Links

- [PowerMem](https://github.com/oceanbase/powermem)
- [PowerMem MCP docs](../api/0004-mcp.md)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
