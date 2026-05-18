# PowerMem

**Persistent, self-evolving memory for AI agents and applications.**

[![PyPI version](https://img.shields.io/pypi/v/powermem)](https://pypi.org/project/powermem/)
[![PyPI downloads](https://img.shields.io/pypi/dm/powermem)](https://pypi.org/project/powermem/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/powermem/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-oceanbase%2Fpowermem-181717?logo=github)](https://github.com/oceanbase/powermem)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*English · [中文](README_CN.md) · [日本語](README_JP.md)*

PowerMem combines vector, full-text, and graph retrieval with LLM-driven memory extraction and Ebbinghaus-style time decay. It ships **two-layer Experience + Skill distillation** for self-evolving memory, multi-agent isolation, user profiles, and multimodal signals (text, image, audio).

---

## Benchmarks

### [LOCOMO](https://github.com/snap-research/locomo)

| Metric | PowerMem | Baseline | Improvement |
|--------|----------|-------------------------|-------------|
| Accuracy | **87.79%** | 52.9% | **+65.9%** |
| Search p95 latency | **1.44 s** | 17.12 s | **-91.6%** |
| Tokens | **~0.9 k** | 26 k | **-96.5%** |

### [AppWorld](https://github.com/StonyBrookNLP/appworld)

| Metric | PowerMem | Baseline | Improvement |
|--------|----------|-------------------------|-------------|
| Pass | **39%** | 24% | **+62.5%** |
| Avg steps | **6.2** | 9.5 | **-34.7%** |
| Total tokens | **1.74 M** | 2.56 M | **-32.0%** |

Reproduce: [`benchmark/`](benchmark/). Under the hood: **two-layer Experience + Skill distillation + 4-way hybrid retrieval + LLM auto-merge** (API: `memory.distill_all() / add_skill / add_experience / search_*`, demo [`examples/experience_skill_usage.py`](examples/experience_skill_usage.py)).

---

## Integrations — pick your client, copy one line

PowerMem ships first-party plugins for the most common AI clients. All of them point at the same backend (HTTP server or local `pmem` CLI) — no per-client schema rewrites.

| Client / framework | One-line install | Mode |
|--------------------|------------------|------|
| OpenClaw (ClawdBot) | `openclaw plugins install memory-powermem` | CLI (default), HTTP optional |
| Claude Code | `git clone https://github.com/oceanbase/powermem && claude --plugin-dir powermem/apps/claude-code-plugin` | HTTP (default), MCP optional |
| Cursor / VS Code / Codex / Windsurf / GitHub Copilot | Install the [PowerMem VS Code extension](apps/vscode-extension/) and run **PowerMem: Link to AI tools** | MCP or HTTP, per client |
| Claude Desktop / Cline / any MCP client | `uvx powermem-mcp sse` | MCP (SSE / stdio / streamable-http) |
| LangChain / LangGraph | `pip install powermem`, see [examples](#examples) | Python SDK |
| Go / Java / TypeScript apps | See [SDKs](#sdks) below | HTTP REST |

### OpenClaw (ClawdBot)

[OpenClaw](https://github.com/openclaw/openclaw) gains long-term memory through the [`memory-powermem`](https://github.com/ob-labs/memory-powermem) plugin.

```bash
openclaw plugins install memory-powermem
```

Defaults to **CLI mode** — the plugin invokes a bundled `pmem` against SQLite under `~/.openclaw/`, using the model OpenClaw already injects. No separate server, no extra `.env`. Switch to **HTTP mode** when a team-shared PowerMem API is preferred (see the plugin's README for `requestConfig.memory_db`).

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem with OpenClaw" width="640"/>

</div>

### Claude Code

```bash
# From a clone of this repo
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin

# Or unpack a packaged release zip and pass --plugin-dir to it
make package-claude-plugin   # builds apps/claude-code-plugin/dist/<version>.zip
```

HTTP mode is on by default:

- `UserPromptSubmit` -> `POST /api/v1/memories/search` and the top results are injected as `additionalContext`.
- `SessionEnd` / `PostCompact` -> `POST /api/v1/memories` writes the transcript or compact summary.
- No MCP setup, no Python needed on the user's machine (hooks ship as native binaries under `hooks/bin/`).

Switch to MCP mode for in-chat `search_memories` / `add_memory` tools:

```bash
bash scripts/apply-connection-mode.sh mcp
```

Full reference: [`apps/claude-code-plugin/README.md`](apps/claude-code-plugin/README.md).

### Cursor, VS Code, Codex, Windsurf, GitHub Copilot

Install the **PowerMem VS Code extension** once (works in VS Code and Cursor). The **PowerMem: Link to AI tools** command auto-writes the right MCP or HTTP config for every supported client:

| Client | Config path written |
|--------|---------------------|
| Cursor | `~/.cursor/mcp.json` (merged) |
| Claude (Desktop / Code) | `~/.claude/providers/powermem.json` |
| Codex | `~/.codex/context.json` (merged) |
| Windsurf | `~/.windsurf/context/powermem.json` |
| GitHub Copilot | `~/.github/copilot/powermem.json` |

The same extension also provides **Query memories**, **Add selection to memory**, **Quick note**, and a status-bar **Dashboard**. See [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md).

### Any MCP client (Claude Desktop, Cline, …)

```bash
uvx powermem-mcp sse                  # SSE on :8000 (recommended)
uvx powermem-mcp stdio                # stdio
uvx powermem-mcp streamable-http      # streamable HTTP
```

Client config (Claude Desktop and most MCP clients):

```json
{
  "mcpServers": {
    "powermem": { "url": "http://localhost:8000/mcp" }
  }
}
```

Exposed tools: `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, `list_memories`. Full reference: [MCP Server](docs/api/0004-mcp.md).

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

End-to-end runnable demos:

- [LangChain healthcare bot](examples/langchain/README.md)
- [LangGraph customer service bot](examples/langgraph/README.md)

### SDKs

| Language | Package |
|----------|---------|
| Python | `pip install powermem` (this repo) |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

## Quick start (Python SDK)

**Prerequisites:** Copy [.env.example](.env.example) to `.env` and set **LLM** and **embedding** credentials. The default database is SQLite; OceanBase can use **embedded SeekDB** without running a separate database service. After install, `pmem config init` walks you through the same setup interactively. See [Getting started](docs/guides/0001-getting_started.md).

### Install

```bash
pip install powermem
```

### SDK

Run from a directory that contains your configured `.env`:

```python
from powermem import Memory, auto_config

memory = Memory(config=auto_config())

memory.add("User likes coffee", user_id="user123")

for r in memory.search("user preferences", user_id="user123").get("results", []):
    print("-", r.get("memory"))
```

More patterns: [Getting Started](docs/guides/0001-getting_started.md).

### CLI (`pmem`, 1.0+)

```bash
pmem memory add "User prefers dark mode" --user-id user123
pmem memory search "preferences" --user-id user123
pmem shell                           # interactive REPL
```

Full reference: [CLI usage](docs/guides/0012-cli_usage.md).

### HTTP API server + Dashboard

Uses the same `.env` as the SDK. Dashboard is served under `/dashboard/`.

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker / Compose: see [API Server](docs/api/0005-api_server.md) and [Docker & deployment](docker/README.md). The official image is `oceanbase/powermem-server:latest`.

---

## Capabilities

**Memory pipeline and retrieval** — [Smart extraction and updates](docs/examples/scenario_2_intelligent_memory.md); [Experience + Skill distillation (self-evolving)](docs/examples/scenario_6_sub_stores.md); [Ebbinghaus-style decay](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md); [Hybrid retrieval (vector / full-text / graph)](docs/examples/scenario_2_intelligent_memory.md); [Sub stores and routing](docs/examples/scenario_6_sub_stores.md).

**Profiles and multi-agent** — [User profile](docs/examples/scenario_9_user_memory.md); [Shared / isolated memory and scopes](docs/examples/scenario_3_multi_agent.md).

**Multimodal** — [Text, image, audio](docs/examples/scenario_7_multimodal.md).

**Provider matrix**

| Layer | Providers (built in) |
|-------|----------------------|
| LLM | Anthropic, OpenAI, Azure OpenAI, Gemini, Qwen (+ ASR), DeepSeek, Ollama, vLLM, SiliconFlow, Z.AI, LangChain-wrapped |
| Embedding | OpenAI, Azure OpenAI, Qwen (+ VL multimodal, sparse), Gemini, Vertex AI, AWS Bedrock, Ollama, LM Studio, HuggingFace, Together, SiliconFlow, Z.AI, OceanBase MASS, LangChain-wrapped |
| Rerank | Jina, Qwen, Z.AI, generic |
| Storage | OceanBase (+ graph), embedded SeekDB, PostgreSQL/pgvector, SQLite |

---

## Docs

- [Getting started](docs/guides/0001-getting_started.md) — install, `.env`, and first `Memory` usage
- [Configuration](docs/guides/0003-configuration.md) — settings model, storage backends, environment variables
- [Architecture](docs/architecture/overview.md) — major components, storage layout, and retrieval flow
- [API & services](docs/api/overview.md) — REST, MCP, HTTP server, and Python-facing APIs
- [CLI](docs/guides/0012-cli_usage.md) — `pmem` commands, interactive shell, backup and migration
- [Multi-agent](docs/guides/0005-multi_agent.md) — scopes, isolation, and cross-agent sharing
- [Integrations](docs/guides/0009-integrations.md) — LangChain and other framework wiring
- [Docker & deployment](docker/README.md) — images, Compose, and running the API server
- [Development](docs/development/overview.md) — local setup, tests, and contributing

More topics: [Sub stores](docs/guides/0006-sub_stores.md), [guides index](docs/guides/overview.md).

## Examples

- [Scenarios & notebooks](docs/examples/overview.md) — walkthroughs by use case (basic usage, multimodal, forgetting curve, sparse vectors, sub stores, and more)
- See [Integrations](#integrations--pick-your-client-copy-one-line) above for client-side and IDE-side entry points (OpenClaw, Claude Code, VS Code extension, MCP, LangChain, LangGraph).

## Release highlights

| Version | Date | Notes |
|---------|------|--------|
| 1.2.0 | 2026-04 | Experience + Skill two-layer distillation and `distill_all()` (self-evolving memory; AppWorld +15 pts); OB MASS embedding; Qwen VL multimodal embedding; OceanBase Zero Mode compatibility; LOCOMO accuracy lifted to 87.79% |
| 1.1.0 | 2026-04-02 | Embedded SeekDB for OceanBase storage without a separate database service; [IDE integrations](apps/README.md) (VS Code extension, Claude Code plugin) |
| 1.0.0 | 2026-03-16 | CLI (`pmem`): memory ops, config, backup/restore/migrate, interactive shell, completions; Web Dashboard |
| 0.5.0 | 2026-02-06 | Unified SDK/API config (pydantic-settings); OceanBase native hybrid search; memory query + list sorting; user-profile language customization |
| 0.4.0 | 2026-01-20 | Sparse vectors for hybrid retrieval; profile-based query rewriting; schema upgrade & migration tools |
| 0.3.0 | 2026-01-09 | Production HTTP API Server; Docker |
| 0.2.0 | 2025-12-16 | Advanced profiles; multimodal (text/image/audio) |
| 0.1.0 | 2025-11-14 | Core memory + hybrid retrieval; LLM extraction; forgetting curve; multi-agent; OceanBase/PostgreSQL/SQLite; graph search |

## Support

- [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

## License

Apache License 2.0 — see [LICENSE](LICENSE).
