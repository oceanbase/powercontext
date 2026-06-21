# PowerMem — automated Claude Code teardown

This file is a **prompt for Claude Code**. Open Claude Code in your terminal and say:

> Read and follow `apps/agent-plugin/UNINSTALL.md` to remove PowerMem from Claude Code.

It reverses everything `SETUP.md` did: it unregisters the plugin/MCP server, removes the
staged marketplace copy (~/.claude/marketplaces/powermem), stops the PowerMem API server,
cleans up legacy package installs when present, and (with my confirmation) cleans up build
artifacts and stored data.

---

Remove the PowerMem Claude Code integration from this machine. Do it autonomously.

This procedure is **idempotent and re-runnable**: running it any number of times is safe,
including on a machine where PowerMem is only partially installed or already fully gone.
Treat every "not found / not installed / already removed / port already free" condition as
SUCCESS — append `|| true` (or equivalent) to each command so no such case aborts the run.
A clean machine must produce all-"already absent" results and still finish at exit 0.
Report what was actually removed vs. already absent.

Do NOT delete my LLM API key, my .env, or my stored memories without explicit
confirmation — those steps are gated below.

1. DETECT CONTEXT. The current directory is the PowerMem source tree if a
   pyproject.toml here has name = "powermem" (or src/powermem/ and
   apps/agent-plugin/ both exist). Tell me which path applies:
     - SOURCE  -> global plugin install (HTTP hooks) was used.
     - PYPI/MCP -> the powermem-mcp server (MCP) was used.
   If unsure, check both: `claude plugin list` (look for memory-powermem@powermem)
   and `claude mcp list` (look for powermem). If NEITHER is present, PowerMem is already
   unregistered — say so, then still run the remaining steps (they will all be harmless
   no-ops) and go straight to SUMMARIZE.

2. STOP THE API SERVER (idempotent). Prefer the Makefile target in the source tree
   (it already exits 0 when nothing is running):
       make server-stop 2>/dev/null || true
   If that target is unavailable or the server was started another way, fall back to a
   port-based stop (default port 8848). The trailing `; true` keeps it green when the
   port is already free:
       PID=$(lsof -t -i:8848 2>/dev/null); [ -n "$PID" ] && { kill "$PID" 2>/dev/null; sleep 2; kill -9 "$PID" 2>/dev/null; }; true
   Then confirm nothing answers (either branch is fine, never errors):
       curl -s -m 3 http://localhost:8848/api/v1/system/health >/dev/null 2>&1 && echo "still up" || echo "server down"
   Also remove stale PID files if present: rm -f ~/.powermem/powermem.pid ~/.powermem/server.pid .server.pid 2>/dev/null || true

3a. SOURCE path — remove the global plugin + marketplace (idempotent):
    - Disable then uninstall the plugin (skip silently if not installed):
        claude plugin disable   memory-powermem@powermem 2>/dev/null || true
        claude plugin uninstall memory-powermem@powermem 2>/dev/null || true
    - Remove the marketplace registration (skip silently if not present):
        claude plugin marketplace remove powermem 2>/dev/null || true
    - Remove the staged marketplace copy created by SETUP's STAGE step (rm -rf never
      errors when the dir is already gone). This is plugin build output, not user data:
        rm -rf "$HOME/.claude/marketplaces/powermem" 2>/dev/null || true
    - Verify it is gone: `claude plugin list` must not show memory-powermem, and
      ~/.claude/settings.json enabledPlugins must not contain
      "memory-powermem@powermem". If a stale enabledPlugins entry remains, remove
      just that key (leave my other plugins untouched).

3b. PYPI/MCP path — remove the MCP server registration (idempotent):
        claude mcp remove powermem 2>/dev/null || true
    Verify `claude mcp list` no longer lists powermem.

4. REMOVE LEGACY PYTHON PACKAGE INSTALLS (idempotent). Marketplace init starts the
   backend with `uvx --from` and does not install `powermem` into a plugin venv. If
   this machine previously used a source, MCP, or legacy venv install, uninstall
   that package from the environment used for setup. Skip quietly if not installed:
        [ -n "${VIRTUAL_ENV:-}" ] && uv pip uninstall --python "$VIRTUAL_ENV/bin/python" powermem 2>/dev/null || true
        pip uninstall -y powermem 2>/dev/null || true  # legacy installs
   Verify legacy installs are gone: `python -c "import powermem"` should fail in the
   current environment, and `which powermem-server` should return nothing unless it is
   intentionally provided by another environment.

5. OPTIONAL CLEANUP — ask me before each of these; they are not required to disable
   the integration, and some destroy data:
    - Build artifacts (SOURCE): delete the compiled hook binaries (rm -rf never errors
      when the dir is already gone):
        rm -rf apps/agent-plugin/hooks/bin
      (You may also restore the committed default if it drifted:
        git checkout -- apps/agent-plugin/.mcp.json 2>/dev/null || true)
    - Stored memories (DESTRUCTIVE — this erases all my saved memories): the embedded
      seekdb/OceanBase data lives in `./seekdb_data/` (or the path in my .env).
      For SQLite storage mode, data lives in `./sqlite_data/` when configured.
      Only delete it if I explicitly say so.
    - Secrets: do NOT touch my .env unless I explicitly ask. If I do, redact the key
      in any output.

6. SUMMARIZE: which path applied, what was removed vs. already absent, confirmation
   that the server is stopped and the plugin/MCP server is no longer registered, and
   list anything left in place by design (e.g. .env, sqlite_data/, seekdb_data/,
   the powermem package) so I know what — if anything — to clean up manually.

For the install procedure, see SETUP.md. For the full manual reference, see
../../docs/integrations/claude_code.md
