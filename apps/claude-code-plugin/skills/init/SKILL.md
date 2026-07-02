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

**CLAUDE_PLUGIN_ROOT handling (read first):** the harness injects
`$CLAUDE_PLUGIN_ROOT` when this skill runs. If it is unset, discover it once
and `export` it on its **own line** — never write
`CLAUDE_PLUGIN_ROOT=... sh "$CLAUDE_PLUGIN_ROOT/..."` on one line, because the
shell expands `$CLAUDE_PLUGIN_ROOT` *before* the assignment takes effect,
producing an empty path (`/scripts/status.sh: No such file or directory`).
Correct pattern:

```sh
export CLAUDE_PLUGIN_ROOT=$(find ~/.claude/plugins/cache/powermem/memory-powermem -maxdepth 2 -name scripts -type d 2>/dev/null | head -1 | xargs dirname)
sh "$CLAUDE_PLUGIN_ROOT/scripts/status.sh"
```

Before running `scripts/init.sh`, check what the user already has configured:

1. Run `sh "$CLAUDE_PLUGIN_ROOT/scripts/status.sh"` to check server health and read
   `~/.powermem/.env` for existing config. **This is the only command you need to
   run for health checks.** Do not compose your own `curl`/`ss`/`netstat` one-liners
   — those tools return non-zero exit codes when the server is down (the expected
   state during init), and the Bash tool reports that as an error even though the
   output is fine. If you must run such a command, append `|| true` so the pipeline
   exits 0.
2. **Server mode detection** — check `POWERMEM_BASE_URL` env var and
   `~/.powermem/runtime.env` first. The Server (Local/Remote) question is only
   asked when neither is set.
   - **`POWERMEM_BASE_URL` is set to a remote URL** (not `localhost` / `127.0.0.1`)
     → remote mode. **Skip ALL AskUserQuestion rounds** (Server, Storage, LLM,
     Embedding). Run init.sh directly; it will detect the URL and write
     `runtime.env`.
   - **`POWERMEM_BASE_URL` is set to a local URL** (`localhost` / `127.0.0.1`)
     → local mode with a known port. **Skip the Server question.** Then:
     - If `status.sh` reports the server healthy → report current backend, no
       questions (same as "When the server is already healthy" below).
     - If not healthy → still ask Storage/LLM/Embedding (Questions 1-3) to
       configure the local server, but skip Question 0.
   - **`POWERMEM_BASE_URL` unset, but `~/.powermem/runtime.env` exists** →
     same logic as above, reading the URL from `runtime.env`.
   - **Both unset** → ask Question 0 (Server mode). If the user picks Remote,
     ask the follow-up URL + API key questions, then skip Questions 1-3.
3. **Storage preference** — read `DATABASE_PROVIDER` from `~/.powermem/.env`. If set,
   note the current backend. Also check `POWERMEM_INIT_DATABASE_PROVIDER` env var.
4. **LLM credentials** — check env vars (`POWERMEM_INIT_LLM_API_KEY`, `LLM_API_KEY`,
   `ANTHROPIC_API_KEY`, `POWERMEM_INIT_LLM_AUTH_TOKEN`, `LLM_AUTH_TOKEN`,
   `ANTHROPIC_AUTH_TOKEN`) and `~/.claude/settings.json` (`env.ANTHROPIC_API_KEY`,
   `env.ANTHROPIC_AUTH_TOKEN`, `env.LLM_API_KEY`).
5. **Embedding preference** — check `POWERMEM_INIT_EMBEDDING_PROVIDER` / `EMBEDDING_PROVIDER`
   env vars, and whether cloud API keys exist (`OPENAI_API_KEY`, `DASHSCOPE_API_KEY`,
   `QWEN_API_KEY`, `SILICONFLOW_API_KEY`).
6. **Region detection (for bilingual prompts)** — run
   `curl -fsSL -m 3 https://ipapi.co/country/ || true` and strip whitespace
   (this is the same source `common.sh`'s `detect_public_ip_country` uses).
   If the output is `CN`, set an internal flag `CN=true`. When `CN=true`,
   append the Chinese translation shown in parentheses (中文) under each
   question/option below to the corresponding AskUserQuestion `question` and
   `option.label` strings. When not `CN`, use English only.

### When the server is already healthy

If `status.sh` reports the server is healthy AND `.env` exists with
`DATABASE_PROVIDER` set, **do not ask the Storage question**. Instead:

- Tell the user: "PowerMem is running with **<current_backend>** storage backend."
- Only re-run `init.sh` if the user explicitly wants to change the storage
  backend — in that case, ask Question 1 and pass `POWERMEM_INIT_DATABASE_PROVIDER`
  to `init.sh`.
- For LLM credentials and embedding: still ask if those values are unset.

### When config is missing (server not healthy, or `.env` missing, or values unset)

Use the **AskUserQuestion** tool to collect missing decisions. The flow is
**three rounds**: Server mode first (its own round), then Storage + LLM
together, then Embedding (whose options depend on the Storage answer).

**Round 1** — ask **only** the Server question (1 question). Skip this round
entirely if `POWERMEM_BASE_URL` is already set (URL itself tells local vs
remote).
- Question 0: Server mode (Local vs Remote)

**Round 2** — after Server mode is known, ask Storage + LLM together (up to
2 questions in one AskUserQuestion call). Skip entirely if Remote was chosen
or remote URL detected.
- Question 1: Storage backend
- Question 2: LLM provider

**Round 3** — after Storage is known, ask Embedding (1 question, options vary
by Storage choice). **Always ask Round 3** — never skip it unless
`EMBEDDING_PROVIDER` is already in `.env` or `POWERMEM_INIT_EMBEDDING_PROVIDER`
is already set. Skipped entirely in remote mode.

### Question 0 — Server mode (ask only if `POWERMEM_BASE_URL` is unset)
- **header**: "Server"
- **question**: "Run a local PowerMem server, or connect to an existing remote one?（运行本地 PowerMem 服务，还是连接已有的远程服务？）"
- **options**:
  - "Local — loopback (本地 - 仅本地访问)" — Start a local server bound to
    `127.0.0.1:8848`. Only this machine can reach it. Recommended for
    single-user / laptop setups.
    （在 127.0.0.1:8848 启动本地服务，仅本机可访问。适合单用户/笔记本环境。）
  - "Local — all interfaces (本地 - 允许其他机器访问)" — Start a local server
    bound to `0.0.0.0:8848`. Other machines / containers on the network can
    reach it. Use when Claude Code runs in a different container or host than
    the server. Exposes the dashboard on all network interfaces — only choose
    this on a trusted network.
    （在 0.0.0.0:8848 启动本地服务，允许同网络其他机器/容器访问。适用于 Claude Code 与服务分处不同容器/主机的场景。会在所有网络接口暴露 dashboard，仅在可信网络下选择。）
  - "Remote (远程)" — Connect to an existing PowerMem server. You'll provide the URL
    (and an optional API key). Storage/LLM/Embedding are determined by the
    remote server; the remaining questions are skipped.
    （连接已有的 PowerMem 服务，需要提供 URL（可选 API key）。Storage/LLM/Embedding 由远程服务决定，后续问题跳过。）

Map the first two answers → `POWERMEM_SERVER_HOST=127.0.0.1` (loopback) or
`POWERMEM_SERVER_HOST=0.0.0.0` (all interfaces). Pass this env var to
`init.sh` so the generated `.env` and launch command use the chosen host.
Loopback is the script default; all-interface binding is an explicit opt-in.

If the user picks **Remote**, make a **follow-up AskUserQuestion call** with
3 questions in one round:
- **header**: "Server URL" — question: "PowerMem server URL (e.g. http://host:port)（PowerMem 服务地址，例如 http://host:port）"
  - The user will type their actual URL via "Other". Provide 2 distinct
    protocol hints as options — do NOT give two duplicate placeholder URLs:
    - "http://host:port (HTTP)" — Plain HTTP, internal/trusted network only.（明文 HTTP，仅限内网/可信网络。）
    - "https://host:port (HTTPS)" — HTTPS, TLS-secured, recommended for cross-network.（HTTPS 加密，跨网络推荐。）
- **header**: "API Key" — question: "API key for the remote server (optional, leave blank if none)（远程服务 API key，可选，没有则留空）"
  - The user types the key via "Other" or picks the no-key option:
    - "No API key (无 key)" — Server has auth disabled, or you'll connect via MCP which doesn't use this key.（服务端未开 auth，或走 MCP 不需要此 key。）
    - "Enter key (输入 key)" — Type the key via "Other".（通过 Other 输入 key。）
- **header**: "Connection" — question: "How should Claude Code connect?（Claude Code 应该如何连接？）"
  - "Hook (REST) (Hook REST)" — Use REST API only. Works if the server has auth disabled, or if you provided a valid API key above.（仅用 REST API。服务端未开 auth 或提供了有效 key 时可用。）
  - "MCP" — Use MCP streamable-http only. Hooks disabled.（仅用 MCP streamable-http，禁用 hook。）
  - "Both (两路并存)" — Enable both. If REST 401s at runtime, hooks fail silently; MCP keeps working.（同时启用。运行时若 REST 返回 401，hook 静默失败，MCP 继续工作。）

Map answers → `POWERMEM_INIT_BASE_URL`, `POWERMEM_INIT_API_KEY` (may be empty),
and `POWERMEM_INIT_CONNECTION_MODE=hook|mcp|both`. Skip Questions 1-3 and
jump to "Running init.sh" with these env vars. Do NOT pass any
`POWERMEM_INIT_DATABASE_PROVIDER` / `POWERMEM_INIT_LLM_*` /
`POWERMEM_INIT_EMBEDDING_*` — they are ignored in remote mode.

### Question 1 — Storage backend (ask only if `DATABASE_PROVIDER` not in `.env` AND `POWERMEM_INIT_DATABASE_PROVIDER` unset AND not remote mode)
- **header**: "Storage"
- **question**: "Which storage backend should PowerMem use?（PowerMem 使用哪个存储后端？）"
- **options**:
  - "SQLite" — Single-user, local, zero-config. Data in `~/.powermem/powermem.db`.
    （单用户、本地、零配置，数据存于 ~/.powermem/powermem.db。）
  - "OceanBase" — Multi-agent sharing / production. Requires OceanBase instance.
    （多 agent 共享 / 生产环境，需要 OceanBase 实例。）

Map answer → `POWERMEM_INIT_DATABASE_PROVIDER=sqlite` (or `oceanbase`).

### Question 2 — LLM provider (ask if `LLM_PROVIDER` not in `.env` AND `POWERMEM_INIT_LLM_PROVIDER` unset)
- **header**: "LLM"
- **question**: "Which LLM provider for fact extraction, profile extraction, and query rewrite?（事实抽取、画像抽取、查询改写用哪个 LLM 提供方？）"
- **options** (vary by credential detection):
  - If credentials detected: "No-LLM mode (无 LLM 模式)" — Skip LLM features（跳过 LLM 功能）, and "<Detected provider> (已探测到凭据)" — Use detected credentials (no key prompt needed)（使用已探测到的凭据，无需再输入 key）.
  - If no credentials: "No-LLM mode (无 LLM 模式)", "Anthropic" — will ask for API key + model（将询问 API key + 模型）, "OpenAI" — will ask for API key + model（将询问 API key + 模型）.

If the user picks a provider without detected credentials, make a **follow-up AskUserQuestion call**:
- **header**: "API Key" — question: "Paste the API key for <provider>（粘贴 <provider> 的 API key）"
- **header**: "Model" — question: "Which model? (e.g. claude-sonnet-4-6, gpt-4o)（使用哪个模型？例如 claude-sonnet-4-6、gpt-4o）"

Map answers → `POWERMEM_INIT_LLM_PROVIDER=noop` (or the chosen provider),
`POWERMEM_INIT_LLM_API_KEY`, `POWERMEM_INIT_LLM_MODEL`, `POWERMEM_INIT_LLM_BASE_URL`.

### Question 3 — Embedding (**MANDATORY follow-up round**, ask if `EMBEDDING_PROVIDER` not in `.env` AND `POWERMEM_INIT_EMBEDDING_PROVIDER` unset)

After the user answers Question 1, ask this in a **separate AskUserQuestion call**.
The options depend on the Storage choice from Question 1.

- **header**: "Embedding"
- **question**: "Which embedding provider should PowerMem use?（PowerMem 使用哪个嵌入提供方？）"
- **options** (vary by storage choice):
  - **If SQLite**: "None (无)", "Local HuggingFace (本地 HuggingFace)" — `all-MiniLM-L6-v2`, 384-dim, downloads automatically, no API key needed（all-MiniLM-L6-v2，384 维，自动下载，无需 API key）, "Cloud (<provider>) (云端 <provider>)" (only if cloud API key detected).
  - **If OceanBase**: "Built-in seekdb (内置 seekdb)" — Local embedding through seekdb, no extra dependencies（通过 seekdb 本地嵌入，无额外依赖）, "Cloud (<provider>) (云端 <provider>)" (only if cloud API key detected). **Do not offer "None" for OceanBase** — OceanBase requires a vector embedding field to store memories, so embeddings are mandatory.

Map answer → `POWERMEM_INIT_EMBEDDING_PROVIDER=none` (SQLite only,
`huggingface` for SQLite, `default` for OceanBase, or the detected cloud provider name).

## Dev-mode detection (local source override)

Before running `init.sh`, check whether `~/.powermem/dev-mode` exists. This
file is **user-private** (lives outside the repo, never committed) and opts
the current machine into running PowerMem from a local source checkout
instead of the published PyPI package.

- If `~/.powermem/dev-mode` exists → read its **first line** and use it as
  the value of `POWERMEM_INIT_PACKAGE` when invoking `init.sh`. The file
  content is a PEP 508 direct reference, e.g.
  `powermem[server,extras,seekdb] @ file:///home/<user>/github/<owner>/powermem`.
  Both `extras` and `seekdb` are listed so the same dev-mode file works
  whether the user picks SQLite or OceanBase as the storage backend.
  Append this env var to **both** the remote-mode and local-mode commands
  below.
- If `~/.powermem/dev-mode` does not exist → use the commands below verbatim
  (no `POWERMEM_INIT_PACKAGE`).

To toggle dev mode on/off the user runs `echo 'powermem[server,extras,seekdb] @ file://<abs-path>' > ~/.powermem/dev-mode` or `rm ~/.powermem/dev-mode`. Do not
create or modify this file yourself unless the user explicitly asks.

## Running init.sh

After collecting answers, run init.sh with the `POWERMEM_INIT_*` environment
variables. Two branches:

### Remote mode (user chose Remote, or `POWERMEM_BASE_URL` / `POWERMEM_INIT_BASE_URL` already points at a non-localhost host)

```sh
POWERMEM_INIT_BASE_URL=<http://remote-host:port> \
  [POWERMEM_INIT_API_KEY=<key-or-blank>] \
  [POWERMEM_INIT_CONNECTION_MODE=<hook|mcp|both>] \
  [POWERMEM_INIT_PACKAGE=<value-from-dev-mode-file>] \
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/init.sh"
```

`POWERMEM_INIT_CONNECTION_MODE` defaults to `both` when unset. init.sh remote
branch behavior:

- Verifies health at `<url>/api/v1/system/health`.
- **mode=hook or both** → writes `~/.powermem/runtime.env` with
  `POWERMEM_BASE_URL=<url>` and optional `POWERMEM_API_KEY=<key>` (only when
  key is non-empty).
- **mode=mcp or both** → registers the `powermem` MCP server in the
  user-scope config via `claude mcp add --scope user --transport http`:
  ```json
  {"type":"http","url":"<url>/mcp"
   [,"headers":{"Authorization":"Bearer <key>"}]}
  ```
  (headers block only added when key is non-empty). User-scope is used so
  the config survives plugin reinstalls (the plugin cache `.mcp.json` is
  wiped on every uninstall+install) and applies to all projects. The
  `claude mcp` CLI decides where to store it (typically `~/.claude.json`
  top-level `mcpServers`), so the location tracks the current Claude Code
  version / platform rather than being hardcoded.
- **mode=mcp** → does NOT write `runtime.env` (hooks unconfigured, won't fire).
- **mode=hook** → writes `runtime.env` only; runs
  `claude mcp remove powermem --scope user` to disable MCP (other MCP
  servers preserved).
- No `.env`, no uvx launch, no PID file. Storage/LLM/Embedding env vars are
  **not** passed and would be ignored.

### Local mode (default)

```sh
POWERMEM_INIT_DATABASE_PROVIDER=<sqlite|oceanbase> \
  [POWERMEM_INIT_LLM_PROVIDER=<noop|anthropic|openai|deepseek|qwen|siliconflow>] \
  [POWERMEM_INIT_LLM_API_KEY=<key>] \
  [POWERMEM_INIT_LLM_MODEL=<model>] \
  [POWERMEM_INIT_LLM_BASE_URL=<url>] \
  [POWERMEM_INIT_EMBEDDING_PROVIDER=<provider>] \
  [POWERMEM_INIT_PACKAGE=<value-from-dev-mode-file>] \
  bash "${CLAUDE_PLUGIN_ROOT}/scripts/init.sh"
```

If `~/.powermem/dev-mode` exists, **always** append
`POWERMEM_INIT_PACKAGE=<first-line-of-dev-mode-file>` to the local-mode
command. Remote mode ignores this var (it short-circuits before uvx launch).

Never print API keys in your output. Mask secrets as `<hidden>` in summaries.

The default local embedding model (`all-MiniLM-L6-v2`) is downloaded
automatically by PowerMem at startup — no `init.sh` flag is needed. CN networks
download through ModelScope and bridge into the HuggingFace cache; other networks
download from HuggingFace directly. `POWERMEM_INIT_PRELOAD_MODEL` is deprecated
and now a no-op; do not recommend it.
