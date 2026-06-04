# PowerMem — automated Claude Code setup

This file is a **prompt for Claude Code**. Open Claude Code in your terminal and say:

> Read and follow `apps/claude-code-plugin/SETUP.md` to set up PowerMem memory for Claude Code.

Claude Code will then run the steps below: detect whether you are in the PowerMem
source tree or not, ask you for the few required secrets, and wire PowerMem up as a
**globally enabled** plugin so every `claude` session (interactive AND non-interactive
`claude -p`) uses it automatically — no per-session `--plugin-dir` flag.

---

Set up PowerMem memory for Claude Code on this machine **globally**. Do the whole
integration autonomously and ask me for any secret you need — never invent credentials.

**⚠️ `.env` changes always require user approval.** Before modifying `.env` for any
reason (LLM config, embedder settings, storage switches, ...), show the user the
current values, propose the exact change, and WAIT for confirmation. Never patch
`.env` silently.

This procedure is **idempotent**: it is safe to re-run. Each step must detect existing
state and either skip, reuse, or refresh it instead of failing or duplicating work.

1. DETECT CONTEXT. The current directory is the PowerMem source tree if a
   pyproject.toml here has name = "powermem" (or src/powermem/ and
   apps/claude-code-plugin/ both exist). Tell me which path you will take:
     - SOURCE  -> build & deploy from this checkout and install the Claude Code
                  plugin GLOBALLY in HTTP mode (hooks -> REST; needs Go 1.22+).
     - PIP     -> install from PyPI and connect via the powermem-mcp server
                  (the plugin is NOT on PyPI, so pip users integrate over MCP).

**⚠️ RULE: Every time you need to modify `.env` — for any reason, even a
single variable — you MUST stop and ask the user what value to use. Show
the current content of the relevant lines, propose the change, and WAIT
for the user's confirmation before writing. Never silently patch `.env`.**

2. COLLECT CONFIG (idempotent). If a .env already exists in the working directory
   with LLM_PROVIDER / LLM_API_KEY / LLM_MODEL set, REUSE it and only ask me about
   anything missing. Otherwise ask for: LLM provider (anthropic / openai / qwen /
   ...), LLM API key, and LLM model. Use zero-config defaults for the rest
   (storage = embedded seekdb, embedder = local all-MiniLM-L6-v2) unless I say
   otherwise.
   **Before writing or patching .env, you MUST:**
     a. Show me the current `.env` lines that will change (or note it is new).
     b. Propose the exact new/changed values.
     c. WAIT for my explicit "yes" before applying the write.
   Copy .env.example if present, then fill
   LLM_PROVIDER / LLM_API_KEY / LLM_MODEL. For a custom endpoint, the var is the
   provider-prefixed *_LLM_BASE_URL (e.g. OPENAI_LLM_BASE_URL, QWEN_LLM_BASE_URL) —
   verify the exact spelling against .env.example.full; a typo is silently ignored.
   Never echo my key back in full.

3a. SOURCE path (global install):
    - pip install -e '.[server,seekdb]'   (no-op if already installed editable from this checkout with these extras)
    - Build the hook binaries FIRST — they get copied into Claude's plugin cache at
      install time, so they must exist on disk before step "install":
        if Go 1.22+ is present:  make build-claude-hook
        else tell me, and offer to install Go or fall back to the PIP path below.
    - Ensure the plugin's root .mcp.json stays empty ({}) — default HTTP mode.
    - STAGE the plugin into a stable, Claude-owned location so the marketplace does
      NOT depend on this checkout — you can move or delete the repo afterwards and
      memory keeps working. Copy the whole plugin dir (built binaries included) into
      ~/.claude/marketplaces/powermem:
        DEST="$HOME/.claude/marketplaces/powermem"
        mkdir -p "$DEST"
        rsync -a --delete "<ABS_PATH>/apps/claude-code-plugin/" "$DEST/"
          # no rsync? rm -rf "$DEST" && cp -a "<ABS_PATH>/apps/claude-code-plugin/." "$DEST/"
      The binaries from `make build-claude-hook` must already be on disk before this
      copy. Re-copy on every re-run so the staged dir tracks your latest build.
    - Register the marketplace from the STAGED dir (it ships
      .claude-plugin/marketplace.json) — never from the repo:
        claude plugin marketplace add "$DEST"
      If it reports "already on disk", refresh it instead:
        claude plugin marketplace update powermem
    - Install + enable the plugin globally (user scope). Install auto-enables it:
        claude plugin install memory-powermem@powermem --scope user
      IMPORTANT idempotency rule: a plain re-install is a no-op and does NOT refresh
      the cached copy. If the plugin is already installed AND you just rebuilt the
      binaries or changed the plugin, force a refresh:
        claude plugin uninstall memory-powermem@powermem
        claude plugin install   memory-powermem@powermem --scope user
      (Enablement is preserved across uninstall+reinstall.)
    - Start the API server only if it is not already healthy (idempotent):
        curl -s http://localhost:8848/api/v1/system/health   # if not healthy:
        powermem-server --host 0.0.0.0 --port 8848 &         # run in background
    - Confirm the plugin is enabled:  claude plugin list  (look for
      memory-powermem@powermem). Do NOT print a --plugin-dir command — it is global
      now; every `claude` and `claude -p` loads it automatically.

3b. PIP path:
    - Install the MCP extra in the environment that Claude will use, then:
      pip install "powermem[mcp,seekdb]"
      powermem-mcp --help
    - Register the MCP server globally so it persists across sessions (stdio = no
      port), run from the directory holding the .env. Idempotent: if `claude mcp get
      powermem` already exists, remove it first, then add:
        claude mcp remove powermem 2>/dev/null; claude mcp add powermem -- powermem-mcp stdio
    - If registration succeeds but Claude reports the MCP server as failed, debug the
      MCP process before retrying setup:
        claude mcp list
        claude mcp get powermem
        command -v powermem-mcp
        powermem-mcp --help
      Then run the MCP server directly from the same directory that contains `.env`
      and capture stderr separately:
        powermem-mcp stdio >/tmp/powermem-mcp.stdout 2>/tmp/powermem-mcp.stderr &
        MCP_PID=$!; sleep 10; kill "$MCP_PID" 2>/dev/null || true; wait "$MCP_PID" 2>/dev/null || true
        sed -n '1,120p' /tmp/powermem-mcp.stderr
        sed -n '1,40p' /tmp/powermem-mcp.stdout
      Expected: stderr may contain normal startup diagnostics; stdout must be empty
      until a JSON-RPC request arrives. If stdout contains banners, warnings, stack
      traces, or any non-JSON text, Claude cannot parse the MCP stream. Fix that
      output pollution before claiming the MCP path works.

4. VERIFY with a real round-trip — do not claim success without data. Run the exact
   commands below and substitute nothing except the noted placeholder. Do NOT mark
   this step done until you have seen a non-empty search result actually come back.

   SOURCE/HTTP path — run a/b/c/d/e in order:

   a. Confirm the server answers (output must contain "status":"healthy"):
        curl -s -m 5 http://localhost:8848/api/v1/system/health

   b. WRITE a probe memory. CRITICAL SCHEMA: the request body is a single "content"
      STRING field — NOT a mem0-style "messages" array. Sending {"messages":[...]}
      returns HTTP 422 `{"detail":[{"type":"missing","loc":["body","content"]...}]}`.
      Use a unique user_id so the probe is isolated from real data:
        curl -s -m 60 -X POST http://localhost:8848/api/v1/memories \
          -H 'Content-Type: application/json' \
          -d '{"content":"PowerMem setup probe: my favorite test fruit is dragonfruit-zx9.","user_id":"powermem_setup_probe"}'
      Expected: JSON with "success": true and a data[0].memory_id (a long numeric
      string). The call can take 10-30s because the LLM extracts facts — KEEP the
      -m 60 timeout and WAIT; do not background it or abort early. Save the returned
      data[0].memory_id (a.k.a. data[0].id) — you need it for cleanup in (e).

   c. SEARCH it back. CRITICAL SCHEMA: the body field is "query" (not "question" or
      "text"), with the SAME user_id you wrote with:
        curl -s -m 30 -X POST http://localhost:8848/api/v1/memories/search \
          -H 'Content-Type: application/json' \
          -d '{"query":"what is my favorite test fruit","user_id":"powermem_setup_probe","limit":5}'
      Expected: data.total >= 1 and data.results[0].content mentions dragonfruit-zx9.
      If data.total is 0 the round-trip FAILED — do NOT report success. Re-check the
      server log and the embedder, retry the write in (b) once, then escalate to me.

   d. SHOW me both the write JSON and the search JSON (this is the proof of success).

   e. CLEAN UP the probe — delete by the id from (b), then confirm it is gone:
        curl -s -m 10 -X DELETE http://localhost:8848/api/v1/memories/<MEMORY_ID>
      Re-run the search from (c): data.total must now be 0.

   f. BONUS (proves global + headless wiring; do it if you can). Run a headless
      prompt from an UNRELATED dir with NO --plugin-dir, then check the logs for
      the two hook-driven calls it triggers:
        ( cd /tmp && claude -p "Reply with exactly: probe ok" )
      Then in `server.log` (powermem-server) and `seekdb_data/log/seekdb.log`
      (seekdb), AFTER that run, you MUST see both:
        POST /api/v1/memories/search   <- UserPromptSubmit hook (auto-recall)
        POST /api/v1/memories          <- SessionEnd hook (auto-save)
      Seeing both proves PowerMem loads automatically in every `claude`/`claude -p`.

   PIP/MCP path: confirm `claude mcp list` shows powermem as "connected" (not
   "failed"). If it shows failed, run the MCP diagnostics below and do not report
   success until the direct MCP process starts cleanly and Claude shows it connected.

   a. Inspect Claude's registered MCP config and status:
        claude mcp list
        claude mcp get powermem
      Verify the command is exactly `powermem-mcp stdio` unless you intentionally
      chose a different binary. Also verify `command -v powermem-mcp` succeeds in the same
      shell where `claude` runs.

   b. Run the MCP server directly and inspect both streams:
        powermem-mcp stdio >/tmp/powermem-mcp.stdout 2>/tmp/powermem-mcp.stderr &
        MCP_PID=$!; sleep 10; kill "$MCP_PID" 2>/dev/null || true; wait "$MCP_PID" 2>/dev/null || true
        sed -n '1,120p' /tmp/powermem-mcp.stderr
        sed -n '1,40p' /tmp/powermem-mcp.stdout
      Interpret the output:
        - stderr has import errors: install/repair the package or virtual environment.
        - stderr has missing LLM/API key/config errors: fix `.env` after asking the
          user for approval; never silently patch `.env`.
        - stderr has model download or network timeouts: pre-download the model or
          switch to a configured remote embedder.
        - stdout has non-JSON text before any request: remove noisy prints/logging
          from stdout; MCP stdio stdout is protocol-only.
        - both files are empty after timeout: the process likely started and waited
          for JSON-RPC input; continue with Claude-side connection checks.

   c. If direct startup is clean but Claude still says failed, refresh the registration:
        claude mcp remove powermem
        claude mcp add powermem -- powermem-mcp stdio
        claude mcp list
      If it still fails, start Claude with debug logging if available in the installed
      Claude Code version, then look for the first MCP spawn or JSON-RPC parse error.

5. SUMMARIZE: path taken, where .env lives, where the staged marketplace lives
   (~/.claude/marketplaces/powermem — independent of this repo), the server URL,
   how memory is wired
   (HTTP hooks vs MCP tools — recall is auto-injected on UserPromptSubmit, not a
   tool the model calls; writes happen on SessionEnd/PostCompact), confirmation that
   it is enabled globally, and the fact that I just run `claude` (or `claude -p`)
   with nothing extra. Note: the background server does not survive a reboot — offer
   to set up a systemd user service for autostart.

## Re-running / refreshing later

This file is safe to re-run end to end. The only manual-feeling case is refreshing
the cached plugin after you change the plugin or rebuild the Go hooks at the SAME
version: rebuild (`make build-claude-hook`), re-copy the result into the staged
marketplace (`rsync -a --delete <ABS_PATH>/apps/claude-code-plugin/ ~/.claude/marketplaces/powermem/`),
then force-refresh the cache with `claude plugin uninstall memory-powermem@powermem`
followed by `claude plugin install memory-powermem@powermem --scope user` (or bump the
version in .claude-plugin/plugin.json so `claude plugin update memory-powermem` picks it up).

To turn it off without uninstalling: `claude plugin disable memory-powermem@powermem`
(re-enable with `claude plugin enable ...`). To disable only prompt-time search
injection, set POWERMEM_PROMPT_SEARCH=0. The hook talks to POWERMEM_BASE_URL
(default http://localhost:8848).

For the full manual reference, see ../../docs/integrations/claude_code.md


## 🚨 COMPREHENSIVE ERROR HANDLING GUIDE

Every real-world setup encounters issues. This guide documents specific error scenarios
and their resolutions discovered during actual setup attempts:

### Log File Locations
- **powermem-server**: `server.log` (RotatingFileHandler, 10MB max, 5 backups)
- **seekdb**: `seekdb_data/log/seekdb.log` (native C++ engine log)

### Error Resolution Checklist

#### [E001] PEP 668 System Protection
**Problem**: `pip install` fails with "externally-managed-environment"
**Fix**: Use virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -e '.[server,seekdb]'
```

#### [E002] Missing Server Dependencies
**Problem**: Server startup fails with missing packages
**Fix**: Install missing dependencies
```bash
pip install 'powermem[server,seekdb]'
```

#### [E003] SeekDB File Locking
**Problem**: "open seekdb failed OB_ERROR(4000)" or "db opened by other process"
**Fix**: Clean corrupted data
```bash
pkill -f powermem-server
rm -rf seekdb_data
powermem-server --host 0.0.0.0 --port 8848 &
```

#### [E004] Missing Go for Hooks
**Problem**: `make build-claude-hook` fails
**Fix**: Install Go 1.22+
```bash
brew install go
make build-claude-hook
```

#### [E005] Storage Backend Initialization
**Problem**: 503 errors on API calls despite server health
**Fix**: Use SQLite alternative
```bash
STORAGE_TYPE=sqlite SQLITE_DB_PATH=sqlite_data/powermem.db powermem-server --host 0.0.0.0 --port 8848 &
```

#### [E006] Model Download Timeout
**Problem**: Server hangs for 30-60s on startup, "timed out thrown while requesting HEAD"
**Fix**: The embedder now auto-detects cache and falls back with a 30s timeout.
If the model is not cached, download it manually:
```bash
python -c "from modelscope import snapshot_download; snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')"
```

#### [E007] Claude MCP Server Shows Failed
**Problem**: `claude mcp list` shows `powermem` as failed or disconnected.
**Fix**: Inspect Claude's registered command, then run the MCP server directly and
check stdout/stderr separately:
```bash
claude mcp list
claude mcp get powermem
command -v powermem-mcp
powermem-mcp --help

powermem-mcp stdio >/tmp/powermem-mcp.stdout 2>/tmp/powermem-mcp.stderr &
MCP_PID=$!; sleep 10; kill "$MCP_PID" 2>/dev/null || true; wait "$MCP_PID" 2>/dev/null || true
sed -n '1,120p' /tmp/powermem-mcp.stderr
sed -n '1,40p' /tmp/powermem-mcp.stdout
```
Diagnosis:
- Import errors in stderr mean the package or virtual environment is broken.
- Missing key/config errors mean `.env` must be corrected after user approval.
- Network/model timeout errors mean the embedder or model cache needs attention.
- Any banner, warning, stack trace, or non-JSON text in stdout breaks MCP stdio.
- Empty stdout/stderr after 10 seconds usually means the process started and is
  waiting for JSON-RPC input; re-check Claude-side registration next.

## PRE-CHECK & PREREQUISITES

1. **Verify Go version**: `go version` (must be 1.22+)
2. **Check dependencies**: `python3 -m pip --version`
3. **Install uvx**: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## STEP BY STEP PROVEN PATH

Given the encountered errors, here are the tested workarounds for each approach:

### Method A: SOURCE Path (Current Directory Build)
```bash
# Create virtual environment to avoid PEP 668 issues
python3 -m venv venv
source venv/bin/activate

# Install everything with extras
pip install -e '.[server,seekdb]'

# Build and stage Claude hooks
make build-claude-hook

# Register marketplace
DEST="$HOME/.claude/marketplaces/powermem"
mkdir -p "$DEST"
rsync -a --delete "$(pwd)/apps/claude-code-plugin/" "$DEST/"
claude plugin marketplace add "$DEST"
claude plugin install memory-powermem@powermem --scope user

# Start server (logs go to server.log automatically)
powermem-server --host 0.0.0.0 --port 8848 &
```

### Method B: PIP Path (Recommended for Stability)
```bash
# Clean virtual environment approach
python3 -m venv venv
source venv/bin/activate
pip install 'powermem[mcp,seekdb]'
claude mcp remove powermem 2>/dev/null
claude mcp add powermem -- powermem-mcp stdio
```

### Method C: Troubleshooting Installation
```bash
# Common troubleshooting commands
lsof -i :8848    # Check if port is in use
pkill -f powermem-server  # Kill any running server
rm -rf seekdb_data  # Reset SeekDB if corrupted

# Check logs
tail -f server.log              # PowerMem server errors
tail -f seekdb_data/log/seekdb.log  # SeekDB engine errors
```

## FINAL VALIDATION STEPS

After setup, verify the complete round-trip:

```bash
# 1. Health check
curl -s http://localhost:8848/api/v1/system/health

# 2. Write test memory
curl -s -X POST http://localhost:8848/api/v1/memories \
  -H 'Content-Type: application/json' \
  -d '{"content":"PowerMem setup verification complete","user_id":"setup_test"}'

# 3. Search test
curl -s -X POST http://localhost:8848/api/v1/memories/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"setup verification","user_id":"setup_test","limit":1}'
```

## SYSTEMD AUTOSTART (Optional)
```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/powermem.service << EOF
[Unit]
Description=PowerMem Memory Server
After=network.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=/bin/bash -c 'source venv/bin/activate && powermem-server --host 0.0.0.0 --port 8848'
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable powermem.service
systemctl --user start powermem.service
```

## SUMMARY

**Path taken**: **[Based on observed errors, recommend PIP approach for stability]**
- **.env location**: $(pwd)/.env
- **Virtual environment**: $(pwd)/venv  
- **Plugin marketplace**: ~/.claude/marketplaces/powermem
- **Server URL**: http://localhost:8848
- **Memory system**: SQLite storage with HTTP hooks
- **Global enablement**: Complete via `claude plugin install`
- **Usage**: Run `claude` command with no extra flags needed

**Quick commands for daily use**:
```bash
# Start server
source venv/bin/activate
powermem-server --host 0.0.0.0 --port 8848

# Check status
systemctl --user status powermem.service

# Quick restart
ps aux | grep powrmem
```

Your Claude Code is now configured with automatic memory recall and persistence worldwide.
