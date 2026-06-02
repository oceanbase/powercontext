# PowerMem Plugin for Claude Code

The full Claude Code integration guide — the auto-setup prompt, manual steps, the
two connection modes (HTTP / MCP), hooks, configuration, troubleshooting, and
uninstall — now lives in the docs and is the single source of truth:

**➡ [docs/integrations/claude_code.md](../../docs/integrations/claude_code.md)**

This directory still contains the plugin itself (`.claude-plugin/`, `hooks/`,
`skills/`, `config/`, `.mcp.json`). To load it:

```bash
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin
```

## Troubleshooting — Error Handling Prompt

When PowerMem encounters an issue (memory writes fail, search returns nothing,
hooks aren't firing, or the server crashes), the server logs everything to
`/tmp/powermem-server.log`. Add this to your Claude Code prompt to self-diagnose:

```
If powermem-server has an error (HTTP 503, 500, or the hook calls fail):
1. Read /tmp/powermem-server.log and look for ERROR-level lines or stack traces
2. Common issues and fixes:
   - "Vector dimension mismatch" → old seekdb data uses a different embedder
     dimension. Delete the seekdb_data/ directory and restart:
       rm -rf ./seekdb_data && powermem-server --host 0.0.0.0 --port 8848
   - "libomp.dylib already initialized" (OMP Error #15) → multiple OpenMP
     runtimes loaded. Restart with:
       KMP_DUPLICATE_LIB_OK=TRUE powermem-server --host 0.0.0.0 --port 8848
   - "Connection timed out" downloading HF models → Hugging Face unreachable.
     Use the China mirror:
       HF_ENDPOINT=https://hf-mirror.com powermem-server --host 0.0.0.0 --port 8848
   - "open seekdb failed" / "opened by other process" → a stale lock file.
     Kill all powermem-server processes, then clean and restart:
       pkill -9 -f powermem-server && rm -rf ./seekdb_data && powermem-server --host 0.0.0.0 --port 8848
   - "ModuleNotFoundError" → missing pip dependency. Install it:
       pip install <missing-package>
3. After applying a fix, re-run the failing operation and confirm the log shows
   the request succeeded (200 status, no ERROR lines).
```
