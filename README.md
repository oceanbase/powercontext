<p align="center">
    <a href="https://github.com/oceanbase/oceanbase">
        <img alt="OceanBase Logo" src="docs/images/oceanbase_Logo.png" width="50%" />
    </a>
</p>

<p align="center">
    <a href="https://pepy.tech/project/powermem">
        <img src="https://img.shields.io/pypi/dm/powermem" alt="PowerMem PyPI - Downloads">
    </a>
    <a href="https://github.com/oceanbase/powermem">
        <img src="https://img.shields.io/github/commit-activity/m/oceanbase/powermem?style=flat-square" alt="GitHub commit activity">
    </a>
    <a href="https://pypi.org/project/powermem" target="blank">
        <img src="https://img.shields.io/pypi/v/powermem?color=%2334D058&label=pypi%20package" alt="Package version">
    </a>
    <a href="https://github.com/oceanbase/powermem/blob/master/LICENSE">
        <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-green.svg" />
    </a>
    <a href="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg">
        <img alt="pyversions" src="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg" />
    </a>
    <a href="https://deepwiki.com/oceanbase/powermem">
        <img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg" />
    </a>
    <a href="https://discord.com/invite/74cF8vbNEs">
        <img src="https://img.shields.io/badge/Discord-Join%20Discord-5865F2?logo=discord&logoColor=white" alt="Join Discord">
    </a>
</p>

[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

# PowerMem — Intelligent Memory for AI Applications

**PowerMem** is long-term memory infrastructure for AI apps: **hybrid vector + full-text + graph** retrieval, **Ebbinghaus-style forgetting**, and **LLM-driven memory extraction**, with **multi-agent isolation/sharing**, **user profiles**, and **multimodal** inputs (text, image, audio). The same feature set is available via **Python SDK**, **CLI (`pmem`)**, **HTTP API Server (with Dashboard)**, and **MCP Server**, all sharing **one `.env` configuration**.

> **News:** [OpenClaw](https://github.com/openclaw-ai/openclaw) can use PowerMem as long-term memory via [`memory-powermem`](https://github.com/ob-labs/memory-powermem) (`openclaw plugins install memory-powermem`).

## Why PowerMem

<div align="center">

<img src="docs/images/benchmark_metrics_en.svg" alt="PowerMem LOCOMO Benchmark Metrics" width="900"/>

</div>

On the [LOCOMO](https://github.com/snap-research/locomo) benchmark vs. stuffing full conversation context:

- **More accurate**: ~**48.77%** relative accuracy gain (78.70% vs. 52.9%)
- **Lower latency**: retrieval **p95** ~**1.44s** vs. **17.12s** (~**91.83%** faster)
- **Fewer tokens**: ~**0.9k** vs. **26k** (~**96.53%** reduction) without sacrificing the above

## Core Features

### Developer-friendly

- **[Lightweight integration](docs/examples/scenario_1_basic_usage.md)**: Python SDK with `.env` auto-loading; also [CLI](docs/guides/0012-cli_usage.md) (`pmem`), [MCP Server](docs/api/0004-mcp.md), and [HTTP API Server](docs/api/0005-api_server.md)

### Intelligent memory management

- **[Smart extraction](docs/examples/scenario_2_intelligent_memory.md)**: LLM-based fact extraction, deduplication, conflict resolution, and merging
- **[Ebbinghaus forgetting curve](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**: time- and relevance-aware decay; prioritize recent, useful memories

### User profiles

- **[User profile](docs/examples/scenario_9_user_memory.md)**: profiles from history and behavior—personalization, companions, and similar use cases

### Multi-agent

- **[Shared / isolated memory](docs/examples/scenario_3_multi_agent.md)**: per-agent spaces, cross-agent collaboration, scope-based permissions

### Multimodal

- **[Text, image, audio](docs/examples/scenario_7_multimodal.md)**: media summarized to text for storage and mixed retrieval

### Storage & retrieval

- **[Sub stores](docs/examples/scenario_6_sub_stores.md)**: partitioning and automatic routing for very large corpora
- **[Hybrid retrieval](docs/examples/scenario_2_intelligent_memory.md)**: vector + full-text + graph (multi-hop), with LLM-assisted graph construction

## Quick Start

Below: **Install** → **Python SDK** → **CLI (`pmem`)** → **HTTP API & Dashboard** → **MCP**. Options: [.env.example](.env.example) and the [configuration guide](docs/guides/0003-configuration.md).

### Install

```bash
pip install powermem
```

### Basic usage (SDK)

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

memory.add("User likes coffee", user_id="user123")

results = memory.search("user preferences", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

More patterns: [Getting Started](docs/guides/0001-getting_started.md).

### PowerMem CLI (1.0.0+)

`pmem` covers memory CRUD, config, backup/restore, and an interactive shell.

```bash
pmem memory add "User prefers dark mode" --user-id user123
pmem memory search "preferences" --user-id user123

pmem config show
pmem config init
pmem stats --json

pmem shell
```

Full reference: [CLI usage](docs/guides/0012-cli_usage.md).

### HTTP API Server & Dashboard

Uses the **same** PowerMem SDK and `.env` as your code. Exposes REST, a **`/dashboard/`** UI, and **`/docs`** (OpenAPI).

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker & Compose:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

docker-compose -f docker/docker-compose.yml up -d
```

Details: [API Server](docs/api/0005-api_server.md).

### MCP Server

Same SDK and `.env`; exposes memory tools to MCP clients (e.g. Claude Desktop).

```bash
pip install powermem
# Install uv / uvx: https://docs.astral.sh/uv/getting-started/

uvx powermem-mcp sse
uvx powermem-mcp sse 8001
uvx powermem-mcp stdio
uvx powermem-mcp streamable-http
uvx powermem-mcp streamable-http 8001
```

Example Claude Desktop config (SSE):

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Details: [MCP Server](docs/api/0004-mcp.md).

## Ecosystem & integrations

### Examples

- **LangChain**: [medical support chatbot](examples/langchain/README.md)
- **LangGraph**: [customer service bot](examples/langgraph/README.md)

## Documentation

| Topic | Link |
|------|------|
| Getting started | [Guide](docs/guides/0001-getting_started.md) |
| CLI | [CLI usage](docs/guides/0012-cli_usage.md) |
| Configuration | [Configuration](docs/guides/0003-configuration.md) |
| Multi-agent | [Multi-Agent](docs/guides/0005-multi_agent.md) |
| Integrations | [Integrations](docs/guides/0009-integrations.md) |
| Sub stores | [Sub stores](docs/guides/0006-sub_stores.md) |
| API | [API overview](docs/api/overview.md) |
| Architecture | [Architecture](docs/architecture/overview.md) |
| Examples | [Examples](docs/examples/overview.md) |
| Development | [Development](docs/development/overview.md) |

## Release highlights

| Version | Date | Notes |
|---------|------|--------|
| 1.0.0 | 2026-03-16 | CLI (`pmem`): memory ops, config, backup/restore/migrate, interactive shell, completions; Web Dashboard |
| 0.5.0 | 2026-02-06 | Unified SDK/API config (pydantic-settings); OceanBase native hybrid search; memory query + list sorting; user-profile language customization |
| 0.4.0 | 2026-01-20 | Sparse vectors for hybrid retrieval; profile-based query rewriting; schema upgrade & migration tools |
| 0.3.0 | 2026-01-09 | Production HTTP API Server; Docker |
| 0.2.0 | 2025-12-16 | Advanced profiles; multimodal (text/image/audio) |
| 0.1.0 | 2025-11-14 | Core memory + hybrid retrieval; LLM extraction; forgetting curve; multi-agent; OceanBase/PostgreSQL/SQLite; graph search |

## Support

- **Issues**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
