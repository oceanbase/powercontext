# PowerMem Plugin for Claude Code

The plugin uses **SQLite** as the default storage backend for coding agent scenarios
(zero external dependencies, works out of the box). Set
`POWERMEM_INIT_DATABASE_PROVIDER=oceanbase` before running `/memory-powermem:init`
to use OceanBase/seekdb instead (production and cluster deployments).

The full Claude Code integration guide — the auto-setup prompt, manual steps, the
two connection modes (HTTP / MCP), hooks, configuration, troubleshooting, and
uninstall — now lives in the docs and is the single source of truth:

**➡ [docs/integrations/claude_code.md](../../docs/integrations/claude_code.md)**

This directory still contains the plugin itself (`.claude-plugin/`, `hooks/`,
`skills/`, `config/`, `.mcp.json`). To load it:

```bash
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin
```

Git/marketplace installs and release plugin zips include prebuilt native hook
binaries under `hooks/bin/`. Developers can refresh them with
`make build-claude-hook` from the repository root, or
`bash apps/claude-code-plugin/scripts/package-plugin.sh` before loading a local
source directory directly.

## Marketplace install

Once the PowerMem marketplace entry is available, install the Claude Code plugin
with:

```text
/plugin marketplace add oceanbase/powermem
/plugin install memory-powermem@powermem
/reload-plugins
/memory-powermem:init
```

`/reload-plugins` is required after install or update so Claude Code loads newly
installed skills such as `/memory-powermem:init`, `/memory-powermem:status`,
`/memory-powermem:stop`, and `/memory-powermem:reset`.

The marketplace install only installs the Claude Code plugin connector. The
`/memory-powermem:init` step prepares the backend by ensuring `uv`, then starts
PowerMem with the uvx-style launcher. The package spec depends on the storage
backend: the default SQLite path uses `powermem[server,extras]` (pulls
`sentence-transformers` for the local `huggingface` embedder), while the
OceanBase path uses `powermem[server,seekdb]`. If `uv` is missing, init
installs it automatically: non-CN networks use the official Astral installer,
while CN networks use the USTC mirror at
`https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/LatestRelease/`.
CN package resolution uses `--default-index https://pypi.tuna.tsinghua.edu.cn/simple`.

The PyPI package used by init must include the backend features and dependencies
required by the plugin, including the default local embedding path
(`sentence-transformers` / `all-MiniLM-L6-v2`). If you are testing plugin changes
that depend on unpublished backend code, set `POWERMEM_INIT_PACKAGE` to a Git URL;
init passes it to `uvx --from`:

```bash
# Default SQLite path
POWERMEM_INIT_PACKAGE='powermem[server,extras] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
# OceanBase path
POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  POWERMEM_INIT_DATABASE_PROVIDER=oceanbase \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
```

For marketplace branch testing before merge, you can also add the marketplace
from the same branch:

```text
/plugin marketplace add https://github.com/owner/powermem.git#<branch>
/plugin install memory-powermem@powermem
/reload-plugins
```

The default local embedding model (`all-MiniLM-L6-v2`) is downloaded
automatically by PowerMem at startup: cache hit → load from disk; cache miss
+ CN → ModelScope; cache miss + non-CN → HuggingFace. No `init.sh` flag is
needed. (`POWERMEM_INIT_PRELOAD_MODEL` is deprecated and now a no-op.)

Uninstall:

```text
/plugin uninstall memory-powermem@powermem
/plugin marketplace remove powermem
/reload-plugins
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
   - "Connection timed out" downloading HF models → PowerMem's internal
     downloader already routes CN networks to ModelScope and non-CN to
     HuggingFace. If it still fails (e.g. CDN unreachable), pre-download
     the model manually:
       python -c "from modelscope import snapshot_download; snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')"
   - "open seekdb failed" / "opened by other process" → a stale lock file.
     Kill all powermem-server processes, then clean and restart:
       pkill -9 -f powermem-server && rm -rf ./seekdb_data && powermem-server --host 0.0.0.0 --port 8848
   - "ModuleNotFoundError" → missing Python dependency. Re-run init with a package
     version or Git ref that includes the dependency. Use the spec that matches
     your storage backend:
       # SQLite (default): needs sentence-transformers via [server,extras]
       POWERMEM_INIT_PACKAGE='powermem[server,extras] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
         sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
       # OceanBase: needs pyseekdb via [server,seekdb]
       POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
         POWERMEM_INIT_DATABASE_PROVIDER=oceanbase \
         sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
3. After applying a fix, re-run the failing operation and confirm the log shows
   the request succeeded (200 status, no ERROR lines).
```
