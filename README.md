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

## ✨ Highlights

<div align="center">

<img src="docs/images/benchmark_metrics_en.svg" alt="PowerMem LOCOMO Benchmark Metrics" width="900"/>

</div>

- 🎯 **Accurate**: **[48.77% Accuracy Improvement]** More accurate than full-context in the LOCOMO benchmark (78.70% VS 52.9%)
- ⚡ **Agile**: **[91.83% Faster Response]** Significantly reduced p95 latency for retrieval compared to full-context (1.44s VS 17.12s)
- 💰 **Affordable**: **[96.53% Token Reduction]** Significantly reduced costs compared to full-context without sacrificing performance (0.9k VS 26k)

# 🧠 PowerMem - Intelligent Memory System

In AI application development, enabling large language models to persistently "remember" historical conversations, user preferences, and contextual information is a core challenge. PowerMem combines a hybrid storage architecture of vector retrieval, full-text search, and graph databases, and introduces the Ebbinghaus forgetting curve theory from cognitive science to build a powerful memory infrastructure for AI applications. The system also provides comprehensive multi-agent support capabilities, including agent memory isolation, cross-agent collaboration and sharing, fine-grained permission control, and privacy protection mechanisms, enabling multiple AI agents to achieve efficient collaboration while maintaining independent memory spaces.

## 🚀 Core Features

### 👨‍💻 Developer Friendly
- 🔌 **[Lightweight Integration](docs/examples/scenario_1_basic_usage.md)**: Provides a simple Python SDK, automatically loads configuration from `.env` files, enabling developers to quickly integrate into existing projects. Also supports [CLI](docs/guides/0012-cli_usage.md) (`pmem`), [MCP Server](docs/api/0004-mcp.md), and [HTTP API Server](docs/api/0005-api_server.md) integration methods

### 🧠 Intelligent Memory Management
- 🔍 **[Intelligent Memory Extraction](docs/examples/scenario_2_intelligent_memory.md)**: Automatically extracts key facts from conversations through LLM, intelligently detects duplicates, updates conflicting information, and merges related memories to ensure accuracy and consistency of the memory database
- 📉 **[Ebbinghaus Forgetting Curve](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**: Based on the memory forgetting patterns from cognitive science, automatically calculates memory retention rates and implements time-decay weighting, prioritizing recent and relevant memories, allowing AI systems to naturally "forget" outdated information like humans

### 👤 User Profile Support
- 🎭 **[User Profile](docs/examples/scenario_9_user_memory.md)**: Automatically builds and updates user profiles based on historical conversations and behavioral data, applicable to scenarios such as personalized recommendations and AI companionship, enabling AI systems to better understand and serve each user

### 🤖 Multi-Agent Support
- 🔐 **[Agent Shared/Isolated Memory](docs/examples/scenario_3_multi_agent.md)**: Provides independent memory spaces for each agent, supports cross-agent memory sharing and collaboration, and enables flexible permission management through scope control

### 🎨 Multimodal Support
- 🖼️ **[Text, Image, and Audio Memory](docs/examples/scenario_7_multimodal.md)**: Automatically converts images and audio to text descriptions for storage, supports retrieval of multimodal mixed content (text + image + audio), enabling AI systems to understand richer contextual information

### 💾 Deeply Optimized Data Storage
- 📦 **[Sub Stores Support](docs/examples/scenario_6_sub_stores.md)**: Implements data partition management through sub stores, supports automatic query routing, significantly improving query performance and resource utilization for ultra-large-scale data
- 🔗 **[Hybrid Retrieval](docs/examples/scenario_2_intelligent_memory.md)**: Combines multi-channel recall capabilities of vector retrieval, full-text search, and graph retrieval, builds knowledge graphs through LLM and supports multi-hop graph traversal for precise retrieval of complex memory relationships

## 🚀 Quick Start

### 📥 Installation

```bash
pip install powermem
```

### 💡 Basic Usage(SDK)

**✨ Simplest Way**: Create memory from `.env` file automatically! [Configuration Reference](.env.example)

```python
from powermem import Memory, auto_config

# Load configuration (auto-loads from .env)
config = auto_config()
# Create memory instance
memory = Memory(config=config)

# Add memory
memory.add("User likes coffee", user_id="user123")

# Search memories
results = memory.search("user preferences", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

For more detailed examples and usage patterns, see the [Getting Started Guide](docs/guides/0001-getting_started.md).

### ⌨️ PowerMem CLI (1.0.0+)

PowerMem provides a command-line interface (`pmem`) for memory operations, configuration, backup/restore, and an interactive shell—without writing Python code.

```bash
# Add and search memories
pmem memory add "User prefers dark mode" --user-id user123
pmem memory search "preferences" --user-id user123

# Configuration and statistics
pmem config show
pmem config init          # Interactive .env wizard
pmem stats --json

# Interactive shell
pmem shell
```

For full CLI reference and examples, see the [CLI Usage Guide](docs/guides/0012-cli_usage.md).

### 🌐 HTTP API Server & Dashboard

PowerMem provides a production-ready HTTP API server that exposes all core memory management capabilities through RESTful APIs. It also serves a **Dashboard** (at `/dashboard/`) as the web admin UI.

**Relationship with SDK**: The API server uses the same PowerMem SDK under the hood and shares the same configuration (`.env` file). It provides an HTTP interface to the same memory management features available in the Python SDK, making PowerMem accessible to non-Python applications.

**Starting the API Server (with Dashboard)**:

```bash
# Method 1: Using CLI command (after pip install)
powermem-server --host 0.0.0.0 --port 8000

# Method 2: Using Docker (API server + dashboard in one container)
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

# Or use Docker Compose (recommended)
docker-compose -f docker/docker-compose.yml up -d
```

Once started, the same server provides:
- RESTful API endpoints for all memory operations
- **Dashboard** at `http://localhost:8000/dashboard/`
- Interactive API documentation at `http://localhost:8000/docs`
- API Key authentication and rate limiting support
- Same configuration as SDK (via `.env` file)

For complete API documentation and usage examples, see the [API Server Documentation](docs/api/0005-api_server.md).

### 🔌 MCP Server

PowerMem also provides a Model Context Protocol (MCP) server that enables integration with MCP-compatible clients such as Claude Desktop. The MCP server exposes PowerMem's memory management capabilities through the MCP protocol, allowing AI assistants to access and manage memories seamlessly.

**Relationship with SDK**: The MCP server uses the same PowerMem SDK and shares the same configuration (`.env` file). It provides an MCP interface to the same memory management features, making PowerMem accessible to MCP-compatible AI assistants.

**Installation**:

```bash
# Install PowerMem (required)
pip install powermem

# Install uvx (if not already installed)
# On macOS/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows:
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Starting the MCP Server**:

```bash
# SSE mode (recommended, default port 8000)
uvx powermem-mcp sse

# SSE mode with custom port
uvx powermem-mcp sse 8001

# Stdio mode
uvx powermem-mcp stdio

# Streamable HTTP mode (default port 8000)
uvx powermem-mcp streamable-http

# Streamable HTTP mode with custom port
uvx powermem-mcp streamable-http 8001
```

**Integration with Claude Desktop**:

Add the following configuration to your Claude Desktop config file:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

The MCP server provides tools for memory management including adding, searching, updating, and deleting memories. For complete MCP documentation and usage examples, see the [MCP Server Documentation](docs/api/0004-mcp.md).

## 🔗 Integrations & Demos
- 🔗 **openclaw Memory Plugin**: Use PowerMem as long-term memory in [openclaw](https://github.com/openclaw/openclaw) via extraction, Ebbinghaus forgetting curve, multi-agent isolation. [View Plugin](https://github.com/ob-labs/openclaw-extension-powermem)
- 🔗 **LangChain Integration**: Build medical support chatbot using LangChain + PowerMem + OceanBase, [View Example](examples/langchain/README.md)
- 🔗 **LangGraph Integration**: Build customer service chatbot using LangGraph + PowerMem + OceanBase, [View Example](examples/langgraph/README.md)

## 📚 Documentation

- 📖 **[Getting Started](docs/guides/0001-getting_started.md)**: Installation and quick start guide
- ⌨️ **[CLI Usage Guide](docs/guides/0012-cli_usage.md)**: PowerMem CLI (pmem) reference (1.0.0+)
- ⚙️ **[Configuration Guide](docs/guides/0003-configuration.md)**: Complete configuration options
- 🤖 **[Multi-Agent Guide](docs/guides/0005-multi_agent.md)**: Multi-agent scenarios and examples
- 🔌 **[Integrations Guide](docs/guides/0009-integrations.md)**: Integrations Guide
- 📦 **[Sub Stores Guide](docs/guides/0006-sub_stores.md)**: Sub stores usage and examples
- 📋 **[API Documentation](docs/api/overview.md)**: Complete API reference
- 🏗️ **[Architecture Guide](docs/architecture/overview.md)**: System architecture and design
- 📓 **[Examples](docs/examples/overview.md)**: Interactive Jupyter notebooks and use cases
- 👨‍💻 **[Development Documentation](docs/development/overview.md)**: Developer documentation

## ⭐ Highlights Release Notes

| Version | Release Date | Function |
|---------|--------------|---------|
| 1.0.0 | 2026.03.16   | <ul><li>PowerMem CLI (pmem): memory operations, config management, backup/restore/migrate, interactive shell, and shell completion</li><li>Web Dashboard for memory management and visualization</li></ul> |
| 0.5.0 | 2026.02.06   | <ul><li>Unified configuration governance across SDK/API Server (pydantic-settings based)</li><li>Added OceanBase native hybrid search support</li><li>Enhanced Memory query handling and added sorting support for memory list operations</li><li>Added user profile support for custom native-language output</li></ul> |
| 0.4.0 | 2026.01.20   | <ul><li>Sparse vector support for enhanced hybrid retrieval, combining dense vector, full-text, and sparse vector search</li><li>User memory query rewriting - automatically enhances search queries based on user profiles for improved recall</li><li>Schema upgrade and data migration tools for existing tables</li></ul> |
| 0.3.0 | 2026.01.09   | <ul><li>Production-ready HTTP API Server with RESTful endpoints for all memory operations</li><li>Docker support for easy deployment and containerization</li></ul> |
| 0.2.0 | 2025.12.16   | <ul><li>Advanced user profile management, supporting "personalized experience" for AI applications</li><li>Expanded multimodal support, including text, image, and audio memory</li></ul> |
| 0.1.0 | 2025.11.14   | <ul><li>Core memory management functionality, supporting persistent storage of memories</li><li>Hybrid retrieval supporting vector, full-text, and graph search</li><li>Intelligent memory extraction based on LLM fact extraction</li><li>Full lifecycle memory management supporting Ebbinghaus forgetting curve</li><li>Multi-Agent memory management support</li><li>Multiple storage backend support (OceanBase, PostgreSQL, SQLite)</li><li>Support for knowledge graph retrieval through multi-hop graph search</li></ul> |

## 💬 Support

- 🐛 **Issue Reporting**: [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- 💭 **Discussions**: [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.