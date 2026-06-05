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

**🔒 DATA SAFETY — API Key Masking (MANDATORY):**
When displaying ANY `.env` content — current values, proposed changes, confirmation
summaries, or any other output — you MUST mask `LLM_API_KEY` and any other secret
values (passwords, tokens, keys):
- **Key ≥ 10 chars:** show only first 4 + last 4 characters (e.g. `sk-a…b12x`)
- **Key < 10 chars:** show `***`
Non-secret values (provider, model, base URLs, storage type, etc.) may be shown in full.

**⚠️ `.env` changes always require user approval.** Before modifying `.env` for any
reason (LLM config, embedder settings, storage switches, ...), show the user the
**masked** current values (using the rules above), propose the exact change, and WAIT
for confirmation. Never patch `.env` silently.

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
the **masked** current content of the relevant lines (per the 🔒 DATA SAFETY
rules above), propose the change, and WAIT for the user's confirmation before
writing. Never silently patch `.env`.**

2. COLLECT CONFIG (idempotent). If a .env already exists in the working directory
   with LLM_PROVIDER / LLM_API_KEY / LLM_MODEL set to real values (not placeholders
   like `your_api_key_here`), REUSE it — skip directly to step 3a/3b. Only collect
   what is missing. Use zero-config defaults for everything else (storage = embedded
   seekdb, embedder = local all-MiniLM-L6-v2) unless I say otherwise.

   **2a. Auto-detect or manual?** Use AskUserQuestion (single-select):

   | Option | Description |
   |--------|-------------|
   | Yes, auto-detect | Read Claude Code's current LLM config from `~/.claude/settings.json` |

   If the user selects "Yes, auto-detect" (or "Other" and types "yes"/"auto"):

   Read `~/.claude/settings.json`.

   **Model and provider** — read `env.ANTHROPIC_MODEL` (Claude Code's standard model
   key); fall back to the top-level `model` field if absent. Both use the format
   `<provider>/<model>` — split on the first `/`:
     - `"deepseek/deepseek-v4-pro"` → `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-v4-pro`
     - `"anthropic/claude-sonnet-4-6"` → `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-sonnet-4-6`
     - If neither field is present or has no `/`, ask for model and provider in 2e.

   ⚠️ **Anthropic model name normalization**: Claude Code's `settings.json` uses
   **dots** for version numbers (e.g. `claude-sonnet-4.6`), but the Anthropic API
   requires **dashes** (e.g. `claude-sonnet-4-6`). After splitting on `/`, if
   `LLM_PROVIDER=anthropic`, replace every `.` with `-` in the version suffix of
   `LLM_MODEL`. Rule: `claude-<name>-<major>.<minor>` → `claude-<name>-<major>-<minor>`.
   Example: `claude-sonnet-4.6` → `claude-sonnet-4-6`, `claude-haiku-4.5` → `claude-haiku-4-5`.

   **API key** — Claude Code always stores its credentials under `ANTHROPIC_*` keys
   regardless of the actual model or provider. Read directly:
     - `env.ANTHROPIC_AUTH_TOKEN` (preferred) or `env.ANTHROPIC_API_KEY`

   **Base URL** — read directly:
     - `env.ANTHROPIC_BASE_URL` (if absent, leave blank — PowerMem will use the
       provider's default endpoint)

   Show a **masked** summary of what was detected (per 🔒 DATA SAFETY rules).
   If a field is not found, ask for it manually as a plain chat question. Then
   jump to **2f**.

   If the user does NOT select auto-detect, fall back to the manual flow:

   **2b.** Ask: "Any custom base URL? (paste it, or say `no` to use the default)"

   **2c.** Ask: "What provider id? (e.g. openai, anthropic, qwen, deepseek, ollama)"

   **2d.** Ask: "Please paste your API key." — skip if provider is `ollama` or `vllm`.

   **2e.** Ask: "Which model? (e.g. gpt-4o-mini, claude-sonnet-4-6, qwen-plus)"

   **For 2b–2e, ask each question as a plain chat message, one at a time.**
   Do NOT use AskUserQuestion for these free-text inputs.

   **2f. Confirm and write.** Show a **masked** summary of what will be written (per
   🔒 DATA SAFETY rules above), then WAIT for explicit "yes" before writing. Copy
   `.env.example` if `.env` does not exist, then fill `LLM_PROVIDER` / `LLM_API_KEY`
   / `LLM_MODEL`. If a base URL was given, write it to the provider-prefixed
   `*_LLM_BASE_URL` (e.g. `OPENAI_LLM_BASE_URL`) — verify spelling against
   `.env.example.full`; a typo is silently ignored.

3a. SOURCE path (global install):
    - pip install -e '.[server,mcp,seekdb]'
      ⚠️ All three extras are required: `[server]` adds fastapi/uvicorn; `[mcp]` adds
      fastmcp, which is checked at import time and calls sys.exit(1) if missing —
      this kills the HTTP server before it can start even in HTTP-only mode;
      `[seekdb]` adds the embedded seekdb storage backend (default).
    - Immediately after pip install, detect which Python interpreter was used. Read
      the shebang from the freshly-installed `powermem-server` entry point — this is
      the only reliable way to guarantee that the model-download script, the pip call
      inside it, and the server all use the exact same interpreter and site-packages.
      On many Linux systems the default `python` points to a version below 3.11 and `python3` may point to a different minor
      version than what pip used; relying on bare `python` or `pip` will silently use
      the wrong environment and the download will fail with an ImportError:
        POWERMEM_PYTHON=$(head -1 "$(command -v powermem-server)" \
          | sed 's|#!||;s| .*||')
        echo "Using interpreter: $POWERMEM_PYTHON"   # e.g. /usr/bin/python3.11
    - Start the embedding model download in the background immediately after pip
      install, so it runs in parallel with the remaining setup steps (hook build,
      plugin stage/install). Use ModelScope — NOT sentence_transformers or
      huggingface_hub, which will hang silently if HuggingFace is unreachable:
        $POWERMEM_PYTHON -m pip install -q modelscope && $POWERMEM_PYTHON -c "
        from modelscope import snapshot_download
        snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')
        import os, shutil, urllib.request, json
        src = os.path.expanduser('~/.cache/modelscope/hub/models/AI-ModelScope/all-MiniLM-L6-v2')
        hub = os.path.expanduser('~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2')
        try:
            resp = urllib.request.urlopen('https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2', timeout=5)
            rev = json.load(resp)['sha']
        except Exception:
            rev = 'fa97f6e7cb1a59073dff9e9d8ba1c7c1591cc08d'
        snap = os.path.join(hub, 'snapshots', rev)
        os.makedirs(snap, exist_ok=True)
        os.makedirs(os.path.join(hub, 'refs'), exist_ok=True)
        open(os.path.join(hub, 'refs', 'main'), 'w').write(rev)
        skip = {'configuration.json', 'data_config.json'}
        [shutil.copytree(os.path.join(src,n), os.path.join(snap,n))
         if os.path.isdir(os.path.join(src,n))
         else shutil.copy2(os.path.join(src,n), os.path.join(snap,n))
         for n in os.listdir(src)
         if n not in skip and not os.path.exists(os.path.join(snap,n))]
        print('Model download and cache bridge complete.')
        " >> /tmp/powermem-model-download.log 2>&1 &
        POWERMEM_MODEL_DL_PID=$!
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
      the cached copy. If you just rebuilt the binaries or changed the plugin, force
      a refresh regardless of whether the plugin was previously installed:
        claude plugin uninstall memory-powermem@powermem 2>/dev/null || true
        claude plugin install   memory-powermem@powermem --scope user
      The `|| true` swallows the "not found" error when the plugin was never installed
      — uninstall fails with exit code 1 in that case, which would abort a script.
      (Enablement is preserved across uninstall+reinstall.)
    - Wait for the background model download (started above) to finish before
      starting the server. If it is already done this is a no-op:
        if [ -n "${POWERMEM_MODEL_DL_PID:-}" ] && kill -0 "$POWERMEM_MODEL_DL_PID" 2>/dev/null; then
          echo "Waiting for model download to finish..."
          wait "$POWERMEM_MODEL_DL_PID"
        fi
        grep -q "complete" /tmp/powermem-model-download.log 2>/dev/null \
          || { echo "Model download failed. Check /tmp/powermem-model-download.log"; exit 1; }
    - Start the API server only if it is not already healthy (idempotent).
      ⚠️ STARTUP TIME: first launch takes 60–120s (seekdb init + embedder load).
      Exit code 7 means the port is not yet bound — do NOT kill and restart.
        curl -s http://localhost:8848/api/v1/system/health | grep -q healthy \
          || { powermem-server --host 0.0.0.0 --port 8848 &
               echo "Waiting for server (first launch can take 60–120s)..."
               for i in $(seq 1 30); do
                 sleep 5
                 curl -s -m 3 http://localhost:8848/api/v1/system/health \
                   | grep -q healthy \
                   && echo "Server ready after $((i*5))s." && break
                 echo "  still starting... ($((i*5))s)"
               done; }
    - Once the server is healthy, use AskUserQuestion (single-select) to ask whether
      to build and open the Web Dashboard:

      | Option | Description |
      |--------|-------------|
      | Yes, build dashboard | Run `make build-dashboard` to compile dashboard assets, then open `http://localhost:8848/dashboard/` in the browser |
      | No, skip | Continue without building the dashboard |

      If the user selects "Yes, build dashboard", run:
        make build-dashboard
      Wait for the command to finish, then open `http://localhost:8848/dashboard/` in
      the default browser. The running server is not restarted.

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
**Problem**: Server hangs or reports "timed out thrown while requesting HEAD" on startup.
**Cause**: The embedding model is not cached and the network is unreachable.
**Fix**: Follow the model pre-download step in Step 3a (ModelScope download + HF hub
bridge). Quick reference:
```bash
# Detect the correct interpreter first (same one powermem uses):
POWERMEM_PYTHON=$(head -1 "$(command -v powermem-server)" | sed 's|#!||;s| .*||')
$POWERMEM_PYTHON -m pip install modelscope
$POWERMEM_PYTHON -c "from modelscope import snapshot_download; \
                     snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')"
# Verify (note: models/ subdirectory is required):
ls ~/.cache/modelscope/hub/models/AI-ModelScope/all-MiniLM-L6-v2/
```
⚠️ Do NOT use bare `pip` or `python` here — on many systems the default `python` version is below 3.11
and `pip` may target a different minor version than what powermem was installed under.
Using the shebang from `powermem-server` guarantees all three steps (pip, download,
bridge) run in the same environment.
Then run the bridge script from Step 3a to populate the HuggingFace hub cache
structure — the embedder's cache-detection function checks `~/.cache/huggingface/hub/`,
not the ModelScope layout.

⚠️ **DO NOT** use `sentence_transformers.SentenceTransformer(...)` or `huggingface_hub`
to download — these pull from HuggingFace, which is unreachable in China and many
corporate networks. Always download via ModelScope, then bridge to the HF hub format.

To confirm which sources are reachable:
```bash
curl -s -m 10 -o /dev/null -w "ModelScope: HTTP %{http_code}\n" \
  https://www.modelscope.cn/api/v1/models/AI-ModelScope/all-MiniLM-L6-v2
curl -s -m 10 -o /dev/null -w "HuggingFace: HTTP %{http_code}\n" \
  https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
```

#### [E008] curl Exit Code 7 After Server Start
**Problem**: `curl` returns exit code 7 ("Failed to connect") right after launching
`powermem-server`.
**Cause**: The server process is running but not yet listening on port 8848. First
launch takes 60–120s (seekdb table creation + embedder load + uvicorn bind).
**Fix**: Use the polling loop from Step 3a. Do NOT kill and restart — restarting
resets initialization and makes startup take even longer.

#### [E009] Server Exits Immediately — `fastapi`, `uvicorn`, or `fastmcp` Missing
**Problem**: Server exits immediately after launch with "Missing dependencies" error.
**Cause**: `pip install -e .` installs only base dependencies. `fastmcp` is checked
at **import time** and calls `sys.exit(1)` if absent — `try/except` cannot catch this,
so even the HTTP-only server is killed before it starts.
**Fix**:
```bash
pip install -e '.[server,mcp]'
```

#### [E010] Anthropic `temperature` + `top_p` both sent → 400
**Problem**: Memory writes return `success:true` but `data:[]`; `server.log` reports
`Error code: 400 - temperature and top_p cannot both be specified for this model`.
**Cause**: `base.py._get_common_params` populates both `temperature` and `top_p` by
default. The Anthropic API (and most Anthropic-compatible proxies) rejects requests
that include both parameters simultaneously.
**Fix**: In `src/powermem/integrations/llm/anthropic.py`, drop `top_p` from `params`
before calling the API:
```python
params = self._get_supported_params(messages=messages, **kwargs)
params.pop("top_p", None)   # Anthropic rejects requests with both temperature and top_p
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

#### [E011] Python Version Below 3.11
**Problem**: `pip install` fails or venv created with wrong Python version.
PowerMem requires Python >= 3.11 (`pyproject.toml: requires-python = ">=3.11"`).
**Fix**: Verify and use Python 3.11+:
```bash
python3 --version  # must be >= 3.11
# If too old, find and use a newer Python:
python3.11 --version
python3.11 -m venv venv
source venv/bin/activate
```

#### [E012] pip Too Old for Editable Install
**Problem**: `pip install -e .` fails with "File setup.py not found. Directory cannot
be installed in editable mode."
**Cause**: pip < 21.3 does not support editable installs via pyproject.toml (PEP 660).
**Fix**: Upgrade pip first:
```bash
source venv/bin/activate
pip install --upgrade pip
pip install -e '.[server,mcp,seekdb]'
```

#### [E013] Internal PyPI Mirror Missing Packages
**Problem**: `pip install` fails with "No matching distribution found for pyobvector"
(or pyseekdb, onnxruntime, etc.).
**Cause**: The configured pip index (e.g. internal mirror at `yum.tbsite.net`) does not
mirror all required packages.
**Fix**: If the package is already installed elsewhere (e.g. system Python 3.11), copy
it into the venv:
```bash
source venv/bin/activate
SITE_PKGS=$(python -c "import site; print(site.getsitepackages()[0])")
# Find the missing package in another Python installation:
find ~/.local/lib/python3.11/site-packages -maxdepth 1 -name "pyobvector*" -type d
# Copy both the package and its .dist-info:
cp -r ~/.local/lib/python3.11/site-packages/pyobvector "$SITE_PKGS/"
cp -r ~/.local/lib/python3.11/site-packages/pyobvector-*.dist-info "$SITE_PKGS/"
```
⚠️ After copying packages manually, you must also install their transitive
dependencies — see [E014].

#### [E014] Missing Transitive Dependencies After Manual Copy
**Problem**: Server starts but returns "Memory service unavailable: storage backend
initialization failed" with `ModuleNotFoundError: No module named 'onnxruntime'`
(or `tenacity`, `tokenizers`).
**Cause**: When packages like pyobvector/pyseekdb are copied manually into the venv,
their transitive dependencies (onnxruntime, tenacity, tokenizers, etc.) are not
automatically resolved by pip.
**Fix**: Install the missing transitive dependencies:
```bash
source venv/bin/activate
pip install onnxruntime tenacity tokenizers
```
To verify no other dependencies are missing after manual copying:
```bash
python -c "import pyobvector" 2>&1   # should produce no output
python -c "import pyseekdb" 2>&1    # should produce no output
```

## PRE-CHECK & PREREQUISITES

1. **Verify Python version**: `python3 --version` (must be >= 3.11, see [E011])
2. **Verify Go version**: `go version` (must be 1.22+)
3. **Verify pip version**: `python3 -m pip --version` (must be >= 21.3, see [E012])
4. **Check mirror access**: `pip config list` — if using an internal mirror, verify
   it has `pyobvector`, `pyseekdb`, and `onnxruntime`; see [E013] if not.

## STEP BY STEP PROVEN PATH

Given the encountered errors, here are the tested workarounds for each approach:

### Method A: SOURCE Path (Current Directory Build)
```bash
# Create virtual environment to avoid PEP 668 issues
# (use python3.11 explicitly if the default python3 is < 3.11)
python3 -m venv venv
source venv/bin/activate

# Upgrade pip first (needed for pyproject.toml editable installs)
pip install --upgrade pip

# Install everything with ALL required extras
pip install -e '.[server,mcp,seekdb]'

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
