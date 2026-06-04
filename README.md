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

PowerMem ships first-party plugins and setup guides for the most common AI clients. All of them point at the same backend (HTTP server, MCP server, or local `pmem` CLI) — no per-client schema rewrites. All agents share the same memory server.

### AI agents & IDEs

<table>
<tr>
<td align="center" width="120"><a href="#claude-code"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /></a><br /><a href="#claude-code"><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/cursor_dark.svg"><img src="https://svgl.app/library/cursor_light.svg" alt="Cursor" width="48" height="48" /></picture></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Cursor</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://svgl.app/library/vscode.svg" alt="VS Code" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>VS Code</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/windsurf-dark.svg"><img src="https://svgl.app/library/windsurf-light.svg" alt="Windsurf" width="48" height="48" /></picture></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Windsurf</b></sub></a></td>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://github.githubassets.com/images/modules/site/copilot/copilot.png" alt="GitHub Copilot" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>GitHub Copilot</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><img src="https://github.com/QoderAI.png?size=120" alt="Qoder" width="48" height="48" /></a><br /><a href="#cursor-vs-code-windsurf-github-copilot-qoder"><sub><b>Qoder</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/opencode-dark.svg"><img src="https://svgl.app/library/opencode.svg" alt="OpenCode" width="48" height="48" /></picture></a><br /><a href="#any-mcp-client"><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="#openclaw-clawdbot"><img src="https://github.com/openclaw.png?size=120" alt="OpenClaw" width="48" height="48" /></a><br /><a href="#openclaw-clawdbot"><sub><b>OpenClaw</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/anthropics.png?size=120" alt="Claude Desktop" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Claude Desktop</b></sub></a></td>
<td align="center" width="120"><a href="#any-mcp-client"><img src="https://github.com/cline.png?size=120" alt="Cline" width="48" height="48" /></a><br /><a href="#any-mcp-client"><sub><b>Cline</b></sub></a></td>
<td></td>
</tr>
</table>

### SDKs & apps

| App / framework | Details |
|-----------------|---------|
| Python SDK | `pip install powermem`, see [Quick start](#quick-start-python-sdk) |
| LangChain / LangGraph | `pip install powermem`, see [LangChain guide](docs/integrations/langchain.md) |
| Go apps | [SDKs](#sdks) |
| Java apps | [SDKs](#sdks) |
| TypeScript apps | [SDKs](#sdks) |
| Any MCP client | `powermem-mcp sse` (default :8848), see [MCP client guide](docs/integrations/mcp_client.md) |
| HTTP REST apps | `powermem-server --host 0.0.0.0 --port 8848`, see [API server](docs/api/0005-api_server.md) |

### OpenClaw (ClawdBot)

[OpenClaw](https://github.com/openclaw/openclaw) gains long-term memory through the [`memory-powermem`](https://github.com/ob-labs/memory-powermem) plugin.

```bash
openclaw plugins install memory-powermem
```

Defaults to **CLI mode** — the plugin invokes a bundled `pmem` against SQLite under `~/.openclaw/`, using the model OpenClaw already injects. No separate server, no extra `.env`. Switch to **HTTP mode** when a team-shared PowerMem API is preferred (see the plugin's README for `requestConfig.memory_db`).

Full guide: [OpenClaw integration](docs/integrations/openclaw.md).

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem with OpenClaw" width="640"/>

</div>

### Claude Code

#### Fastest path — let Claude Code set itself up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open Claude Code in your terminal and paste this one line:

```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```

Claude Code reads [`apps/claude-code-plugin/SETUP.md`](apps/claude-code-plugin/SETUP.md), asks you for the few required secrets, and wires everything up end-to-end.

#### Manual setup

Prefer to wire it by hand? See the full walkthrough — environment variables, MCP mode, the `remember` / `recall` skills, Windows hooks, troubleshooting, and uninstall — in **[docs/integrations/claude_code.md](docs/integrations/claude_code.md)**.

### Cursor, VS Code, Windsurf, GitHub Copilot, Qoder

#### Recommended setup — let your IDE agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in your IDE and paste this one line:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

The agent follows [`apps/vscode-extension/SETUP.md`](apps/vscode-extension/SETUP.md): it prefers a reusable `powermem-server` HTTP API backend, falls back to MCP-only only when HTTP is unavailable, and configures the current IDE/client instead of unrelated tools.

#### Manual setup

Prefer to wire it by hand? Use the per-IDE guide:

| Client | Details |
|--------|---------|
| VS Code | [`docs/integrations/vs_code.md`](docs/integrations/vs_code.md) |
| Cursor | [`docs/integrations/cursor.md`](docs/integrations/cursor.md) |
| Windsurf | [`docs/integrations/windsurf.md`](docs/integrations/windsurf.md) |
| GitHub Copilot | [`docs/integrations/github_copilot.md`](docs/integrations/github_copilot.md) |
| Qoder | [`docs/integrations/qoder.md`](docs/integrations/qoder.md) |

The same extension also provides **Query memories**, **Add selection to memory**, **Quick note**, and a status-bar **Dashboard**. See [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md) and the full [VS Code guide](docs/integrations/vs_code.md).

### Any MCP client 

For Claude Desktop, Codex, Cline, OpenCode, Roo Code, Goose, or any other MCP-compatible client. please use MCP Client mode. 
First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in your MCP client or IDE and paste this one line:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

The agent follows [`apps/mcp-client/SETUP.md`](apps/mcp-client/SETUP.md): it uses `powermem-mcp` directly, prefers SSE on port `8848`, falls back to streamable HTTP or stdio only when needed, and configures only the target MCP client.

Prefer to wire it by hand? Use the [Generic MCP client guide](docs/integrations/mcp_client.md). To remove the integration later, follow [`apps/mcp-client/UNINSTALL.md`](apps/mcp-client/UNINSTALL.md). Exposed tools: `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, `list_memories`. Full reference: [MCP Server](docs/api/0004-mcp.md). Client-specific notes: [Cline](docs/integrations/cline.md), [Codex](docs/integrations/codex.md), and [OpenCode](docs/integrations/opencode.md).

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

End-to-end runnable demos:

- [LangChain healthcare bot](examples/langchain/README.md)
- [LangGraph customer service bot](examples/langgraph/README.md)

Full framework guide: [LangChain and LangGraph integration](docs/integrations/langchain.md).

### SDKs

| Language | Package |
|----------|---------|
| Python | `pip install powermem` (this repo) |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

## Quick start (Python SDK)

**Prerequisites:** Copy [.env.example](.env.example) to `.env` and set your **LLM** API key — that is the only required credential. For zero-config local storage, install the `seekdb` extra (`pip install "powermem[seekdb]"`, or combine it with `server` / `mcp`) so the default **OceanBase** provider can boot **embedded seekdb** on disk. Without `seekdb`, set `OCEANBASE_HOST` to point at a remote OceanBase cluster, or switch to `sqlite` / `postgres`. The default embedder is a local `all-MiniLM-L6-v2` model (384 dims) that needs no API key and auto-downloads on first use. Need to tune providers or unlock advanced features? Copy [.env.example.full](.env.example.full) instead — it documents every available knob, grouped by component. After install, `pmem config init` walks you through the same setup interactively. See [Getting started](docs/guides/0001-getting_started.md).

### Install

```bash
# Core only (SDK; no optional CLI/server/MCP/seekdb)
pip install powermem

# With CLI (pmem / powermem-cli)
pip install "powermem[cli]"

# With HTTP API server only (powermem-server; does not install seekdb)
pip install "powermem[server]"

# With MCP server only (powermem-mcp; does not install seekdb)
pip install "powermem[mcp]"

# With seekdb for zero-config local storage/embedder
pip install "powermem[seekdb]"

# HTTP API server + seekdb
pip install "powermem[server,seekdb]"

# MCP server + seekdb
pip install "powermem[mcp,seekdb]"

# Common full local install
pip install "powermem[cli,server,mcp,seekdb]"
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
powermem-server --host 0.0.0.0 --port 8848
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
| Storage | OceanBase (+ graph), embedded seekdb, PostgreSQL/pgvector, SQLite |

---

## Docs

- [Getting started](docs/guides/0001-getting_started.md) — install, `.env`, and first `Memory` usage
- [Configuration](docs/guides/0003-configuration.md) — settings model, storage backends, environment variables
- [Architecture](docs/architecture/overview.md) — major components, storage layout, and retrieval flow
- [API & services](docs/api/overview.md) — REST, MCP, HTTP server, and Python-facing APIs
- [CLI](docs/guides/0012-cli_usage.md) — `pmem` commands, interactive shell, backup and migration
- [Multi-agent](docs/guides/0005-multi_agent.md) — scopes, isolation, and cross-agent sharing
- [Integrations](docs/guides/0009-integrations.md) — LangChain and other framework wiring
- [Ecosystem integrations](docs/integrations/overview.md) — AI clients & IDEs ([Claude Code](docs/integrations/claude_code.md), …)
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
| 1.1.0 | 2026-04-02 | Embedded seekdb for OceanBase storage without a separate database service; [IDE integrations](apps/README.md) (VS Code extension, Claude Code plugin) |
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
