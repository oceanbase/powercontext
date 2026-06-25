---
description: Initialize PowerMem for Claude Code after the plugin is installed. Use when the user asks to set up, initialize, or repair PowerMem.
---

Initialize PowerMem for Claude Code.

Read `apps/claude-code-plugin/SETUP.md`, section "Installed plugin initialization",
and follow only that section.

Do not run the source/developer setup flow from `SETUP.md`: do not build hook
binaries, do not stage the plugin, do not run `claude plugin marketplace add`, do
not run `claude plugin install`, and do not build the dashboard.

Use the plugin scripts as directed by that section:

- `scripts/status.sh`
- `scripts/init.sh`

Remember that `scripts/init.sh` ensures uv and starts the backend with the
uvx-style launcher. Package depends on the storage backend: SQLite (default)
uses `uvx --from 'powermem[server,extras]' powermem-server` (pulls
`sentence-transformers` for the local huggingface embedder); OceanBase uses
`uvx --from 'powermem[server,seekdb]' powermem-server`. If the user is testing
unpublished backend changes, run the script with
`POWERMEM_INIT_PACKAGE='powermem[server,extras] @ git+https://github.com/oceanbase/powermem.git@<branch-or-sha>'`
(SQLite) or the matching `[server,seekdb]` spec (OceanBase) so that value is
passed to `uvx --from` instead of using the default PyPI package.

## Interactive configuration via AskUserQuestion

Before running `scripts/init.sh`, check what the user already has configured:

1. Run `sh "$CLAUDE_PLUGIN_ROOT/scripts/status.sh"` to check server health and read
   `~/.powermem/.env` for existing config.
2. **Storage preference** — read `DATABASE_PROVIDER` from `~/.powermem/.env`. If set,
   note the current backend. Also check `POWERMEM_INIT_DATABASE_PROVIDER` env var.
3. **LLM credentials** — check env vars (`POWERMEM_INIT_LLM_API_KEY`, `LLM_API_KEY`,
   `ANTHROPIC_API_KEY`, `POWERMEM_INIT_LLM_AUTH_TOKEN`, `LLM_AUTH_TOKEN`,
   `ANTHROPIC_AUTH_TOKEN`) and `~/.claude/settings.json` (`env.ANTHROPIC_API_KEY`,
   `env.ANTHROPIC_AUTH_TOKEN`, `env.LLM_API_KEY`).
4. **Embedding preference** — check `POWERMEM_INIT_EMBEDDING_PROVIDER` / `EMBEDDING_PROVIDER`
   env vars, and whether cloud API keys exist (`OPENAI_API_KEY`, `DASHSCOPE_API_KEY`,
   `QWEN_API_KEY`, `SILICONFLOW_API_KEY`).

### When the server is already healthy

If `status.sh` reports the server is healthy AND `.env` exists with
`DATABASE_PROVIDER` set, **do not ask the Storage question**. Instead:

- Tell the user: "PowerMem is running with **<current_backend>** storage backend."
- Only re-run `init.sh` if the user explicitly wants to change the storage
  backend — in that case, ask Question 1 and pass `POWERMEM_INIT_DATABASE_PROVIDER`
  to `init.sh`.
- For LLM credentials and embedding: still ask if those values are unset.

### When config is missing (server not healthy, or `.env` missing, or values unset)

Use the **AskUserQuestion** tool to collect any missing decisions. Ask up to 3
questions in a single call when possible.

### Question 1 — Storage backend (ask only if `DATABASE_PROVIDER` not in `.env` AND `POWERMEM_INIT_DATABASE_PROVIDER` unset)
- **header**: "Storage"
- **question**: "Which storage backend should PowerMem use?"
- **options**:
  - "SQLite (Recommended)" — Single-user, local, zero-config. Data in `~/.powermem/powermem.db`.
  - "OceanBase" — Multi-agent sharing / production. Requires OceanBase instance.

Map answer → `POWERMEM_INIT_DATABASE_PROVIDER=sqlite` (or `oceanbase`).

### Question 2 — LLM provider (ask ONLY if no LLM credentials detected)
- **header**: "LLM"
- **question**: "No LLM credentials found. How should PowerMem handle LLM-based features (fact extraction, profile extraction, query rewrite)?"
- **options**:
  - "No-LLM mode (Recommended)" — Skip LLM features. Memory CRUD still works. Reconfigurable later via `/init`.
  - "Anthropic" — Use Anthropic API. Will ask for API key + model next.
  - "OpenAI" — Use OpenAI API. Will ask for API key + model next.

If the user picks Anthropic or OpenAI, make a **follow-up AskUserQuestion call**
with 2 questions (free-text via the "Other" option):
- **header**: "API Key" — question: "Paste the API key for <provider>"
- **header**: "Model" — question: "Which model? (e.g. claude-sonnet-4-6, gpt-4o)"

Map answers → `POWERMEM_INIT_LLM_PROVIDER`, `POWERMEM_INIT_LLM_API_KEY`,
`POWERMEM_INIT_LLM_MODEL`. For OpenAI/Anthropic, set a sensible `POWERMEM_INIT_LLM_BASE_URL`
if the user doesn't provide one.

### Question 3 — Embedding (ask ONLY if cloud API key detected AND embedding unset)
- **header**: "Embedding"
- **question**: "A cloud API key (<provider>) was detected. Use local or cloud embedding?"
- **options**:
  - "Local HuggingFace (Recommended)" — `all-MiniLM-L6-v2`, 384-dim, downloads automatically, no API key needed.
  - "Cloud (<provider>)" — Use the detected API key for embedding.

Map answer → `POWERMEM_INIT_EMBEDDING_PROVIDER=huggingface` (or `default` for
OceanBase path, or the detected cloud provider name).

## Running init.sh

After collecting answers, run init.sh with `POWERMEM_NON_INTERACTIVE=1` to bypass
the shell-level interactive prompts (the AskUserQuestion flow replaces them):

```sh
POWERMEM_NON_INTERACTIVE=1 \
  POWERMEM_INIT_DATABASE_PROVIDER=<sqlite|oceanbase> \
  [POWERMEM_INIT_LLM_PROVIDER=<provider>] \
  [POWERMEM_INIT_LLM_API_KEY=<key>] \
  [POWERMEM_INIT_LLM_MODEL=<model>] \
  [POWERMEM_INIT_LLM_BASE_URL=<url>] \
  [POWERMEM_INIT_EMBEDDING_PROVIDER=<provider>] \
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/init.sh"
```

Never print API keys in your output. Mask secrets as `<hidden>` in summaries.

The default local embedding model (`all-MiniLM-L6-v2`) is downloaded
automatically by PowerMem at startup — no `init.sh` flag is needed. CN networks
download through ModelScope and bridge into the HuggingFace cache; other networks
download from HuggingFace directly. `POWERMEM_INIT_PRELOAD_MODEL` is deprecated
and now a no-op; do not recommend it.
