# PowerMem Plugin for Claude Code and Codex CLI

The full Claude Code integration guide — the auto-setup prompt, manual steps, the
two connection modes (HTTP / MCP), hooks, configuration, troubleshooting, and
uninstall — lives in the docs:

**➡ [docs/integrations/claude_code.md](../../docs/integrations/claude_code.md)**

The Codex CLI guide lives here:

**➡ [docs/integrations/codex.md](../../docs/integrations/codex.md)**

This directory contains the plugin descriptors and shared runtime files
(`.claude-plugin/`, `.codex-plugin/`, `hooks/`, `skills/`, `config/`, `.mcp.json`).
To load it directly in Claude Code:

```bash
claude --plugin-dir /path/to/powermem/apps/agent-plugin
```

## Claude Code Marketplace Install

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
PowerMem with the uvx-style launcher
`uvx --from 'powermem[server,seekdb]' powermem-server`. If `uv` is missing, init
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
POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
  sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
```

For marketplace branch testing before merge, you can also add the marketplace
from the same branch:

```text
/plugin marketplace add https://github.com/owner/powermem.git#<branch>
/plugin install memory-powermem@powermem
/reload-plugins
```

## Codex CLI Install

Install the Codex marketplace and plugin:

```bash
codex plugin marketplace add oceanbase/powermem
codex plugin add memory-powermem@powermem
```

For branch testing:

```bash
codex plugin remove memory-powermem 2>/dev/null || true
codex plugin marketplace remove powermem 2>/dev/null || true
codex plugin marketplace add https://github.com/<owner>/powermem.git --ref <branch>
codex plugin add memory-powermem@powermem
```

Start a new Codex thread, then ask Codex:

```text
Use the memory-powermem init skill to initialize PowerMem.
```

After init succeeds, wire MCP to the managed server:

```bash
. "$HOME/.powermem/runtime.env"
codex mcp remove powermem 2>/dev/null || true
codex mcp add powermem --url "${POWERMEM_BASE_URL%/}/mcp"
```

To pre-download the default local embedding model through ModelScope before
starting the server:

```bash
PLUGIN_ROOT="${CODEX_PLUGIN_ROOT:-${POWERMEM_PLUGIN_ROOT:-}}"
[ -n "$PLUGIN_ROOT" ] || { echo "Codex plugin root not found"; exit 1; }
POWERMEM_INIT_PRELOAD_MODEL=1 sh "$PLUGIN_ROOT/scripts/init.sh"
```

Uninstall:

```bash
codex plugin remove memory-powermem 2>/dev/null || true
codex plugin marketplace remove powermem
```

Start a new Codex thread after uninstalling or reinstalling so Codex reloads the
plugin registry.

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
   - "ModuleNotFoundError" → missing Python dependency. Re-run init with a package
     version or Git ref that includes the dependency:
       POWERMEM_INIT_PACKAGE='powermem[server,seekdb] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>' \
         sh "$CLAUDE_PLUGIN_ROOT/scripts/init.sh"
3. After applying a fix, re-run the failing operation and confirm the log shows
   the request succeeded (200 status, no ERROR lines).
```
