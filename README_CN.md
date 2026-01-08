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
    <a href="https://zread.ai/oceanbase/powermem" target="_blank"><img src="https://img.shields.io/badge/Ask_Zread-_.svg?style=flat&color=00b0aa&labelColor=000000&logo=data%3Aimage%2Fsvg%2Bxml%3Bbase64%2CPHN2ZyB3aWR0aD0iMTYiIGhlaWdodD0iMTYiIHZpZXdCb3g9IjAgMCAxNiAxNiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTQuOTYxNTYgMS42MDAxSDIuMjQxNTZDMS44ODgxIDEuNjAwMSAxLjYwMTU2IDEuODg2NjQgMS42MDE1NiAyLjI0MDFWNC45NjAxQzEuNjAxNTYgNS4zMTM1NiAxLjg4ODEgNS42MDAxIDIuMjQxNTYgNS42MDAxSDQuOTYxNTZDNS4zMTUwMiA1LjYwMDEgNS42MDE1NiA1LjMxMzU2IDUuNjAxNTYgNC45NjAxVjIuMjQwMUM1LjYwMTU2IDEuODg2NjQgNS4zMTUwMiAxLjYwMDEgNC45NjE1NiAxLjYwMDFaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00Ljk2MTU2IDEwLjM5OTlIMi4yNDE1NkMxLjg4ODEgMTAuMzk5OSAxLjYwMTU2IDEwLjY4NjQgMS42MDE1NiAxMS4wMzk5VjEzLjc1OTlDMS42MDE1NiAxNC4xMTM0IDEuODg4MSAxNC4zOTk5IDIuMjQxNTYgMTQuMzk5OUg0Ljk2MTU2QzUuMzE1MDIgMTQuMzk5OSA1LjYwMTU2IDE0LjExMzQgNS42MDE1NiAxMy43NTk5VjExLjAzOTlDNS42MDE1NiAxMC42ODY0IDUuMzE1MDIgMTAuMzk5OSA0Ljk2MTU2IDEwLjM5OTlaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik0xMy43NTg0IDEuNjAwMUgxMS4wMzg0QzEwLjY4NSAxLjYwMDEgMTAuMzk4NCAxLjg4NjY0IDEwLjM5ODQgMi4yNDAxVjQuOTYwMUMxMC4zOTg0IDUuMzEzNTYgMTAuNjg1IDUuNjAwMSAxMS4wMzg0IDUuNjAwMUgxMy43NTg0QzE0LjExMTkgNS42MDAxIDE0LjM5ODQgNS4zMTM1NiAxNC4zOTg0IDQuOTYwMVYyLjI0MDFDMTQuMzk4NCAxLjg4NjY0IDE0LjExMTkgMS42MDAxIDEzLjc1ODQgMS42MDAxWiIgZmlsbD0iI2ZmZiIvPgo8cGF0aCBkPSJNNCAxMkwxMiA0TDQgMTJaIiBmaWxsPSIjZmZmIi8%2BCjxwYXRoIGQ9Ik00IDEyTDEyIDQiIHN0cm9rZT0iI2ZmZiIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIvPgo8L3N2Zz4K&logoColor=ffffff" alt="zread"/></a>
    <a href="https://discord.com/invite/74cF8vbNEs">
        <img src="https://img.shields.io/badge/Discord-Join%20Discord-5865F2?logo=discord&logoColor=white" alt="Join Discord">
    </a>
</p>

[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

## ✨ 亮点

<div align="center">

<img src="docs/images/benchmark_metrics_cn.svg" alt="PowerMem LOCOMO 压测指标" width="900"/>

</div>

- 🎯 **更准**：**[准确率提升 48.77%]** 在 LOCOMO 基准测试中，相比于 full-context 更准确（78.70% VS 52.9%）
- ⚡ **更快**：**[响应速度快 91.83%]** 相比于 full-context，检索的 p95 延迟显著降低（1.44s VS 17.12s）
- 💰 **更省**：**[Token 用量降低 96.53%]** 相比于full-context，在不牺牲性能的前提下显著降低成本（0.9k VS 26k）

# 🧠 PowerMem - 智能AI记忆系统

在 AI 应用开发中，如何让大语言模型持久化地"记住"历史对话、用户偏好和上下文信息是一个核心挑战。PowerMem 融合向量检索、全文检索和图数据库的混合存储架构，并引入认知科学的艾宾浩斯遗忘曲线理论，为 AI 应用构建了强大的记忆基础设施。系统还提供完善的多智能体支持能力，包括智能体记忆隔离、跨智能体协作共享、细粒度权限控制和隐私保护机制，让多个 AI 智能体能够在保持独立记忆空间的同时实现高效协作。

## 🚀 核心特性

### 👨‍💻 开发者友好
- 🔌 **[轻量级接入方式](docs/examples/scenario_1_basic_usage.md)**：提供简洁的 Python SDK 支持，自动从 `.env` 文件加载配置，让开发者快速集成到现有项目中

### 🧠 智能记忆管理
- 🔍 **[记忆的智能提取](docs/examples/scenario_2_intelligent_memory.md)**：通过 LLM 自动从对话中提取关键事实，智能检测重复、更新冲突信息并合并相关记忆，确保记忆库的准确性和一致性
- 📉 **[艾宾浩斯遗忘曲线](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**：基于认知科学的记忆遗忘规律，自动计算记忆保留率并实现时间衰减加权，优先返回最近且相关的记忆，让 AI 系统像人类一样自然"遗忘"过时信息

### 👤 用户画像支持
- 🎭 **[用户画像](docs/examples/scenario_9_user_memory.md)**：基于用户历史对话和行为数据，自动构建和更新用户画像，适用于个性化推荐、AI 陪伴等场景，让 AI 系统更好地理解和服务每个用户

### 🤖 多智能体支持
- 🔐 **[智能体共享/隔离记忆](docs/examples/scenario_3_multi_agent.md)**：为每个智能体提供独立的记忆空间，支持跨智能体记忆共享和协作，通过作用域控制实现灵活的权限管理

### 🎨 多模态支持
- 🖼️ **[文本、图像、语音记忆](docs/examples/scenario_7_multimodal.md)**：自动将图像和音频转换为文本描述并存储，支持多模态混合内容（文本+图像+音频）的检索，让 AI 系统理解更丰富的上下文信息

### 💾 深度优化数据存储
- 📦 **[支持子存储（Sub Stores）](docs/examples/scenario_6_sub_stores.md)**：通过子存储实现数据
的分区管理，支持自动路由查询，显著提升超大规模数据的查询性能和资源利用率
- 🔗 **[混合检索](docs/examples/scenario_2_intelligent_memory.md)**：融合向量检索、全文搜索和图检索的多路召回能力，通过 LLM 构建知识图谱并支持多跳图遍历，精准检索复杂的记忆关联关系

## 🚀 快速开始

### 📥 安装

```bash
pip install powermem
```

### 💡 基本使用（SDK）

**✨ 最简单的方式**：从 `.env` 文件读取配置自动创建记忆！[配置文件参考](.env.example)

```python
from powermem import Memory, auto_config

# 自动从 .env 加载配置并初始化
config = auto_config()
memory = Memory(config=config)

# 添加记忆
memory.add("用户喜欢咖啡", user_id="user123")

# 搜索记忆
results = memory.search("用户偏好", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

更多详细示例和使用模式，请参阅[入门指南](docs/guides/0001-getting_started.md)。

### 🌐 HTTP API Server

PowerMem 还提供了生产就绪的 HTTP API 服务器，通过 RESTful API 暴露所有核心记忆管理功能。这使得任何支持 HTTP 调用的应用程序都能集成 PowerMem 的智能记忆系统，无论使用何种编程语言。

**与 SDK 的关系**：API 服务器底层使用相同的 PowerMem SDK，并共享相同的配置（`.env` 文件）。它提供了与 Python SDK 相同的记忆管理功能的 HTTP 接口，使 PowerMem 可供非 Python 应用程序使用。

**启动 API 服务器**：

```bash
# 方法 1：使用 CLI 命令（pip 安装后）
powermem-server --host 0.0.0.0 --port 8000

# 方法 2：使用 Docker
# 构建并运行 Docker 容器
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

# 或使用 Docker Compose（推荐）
docker-compose -f docker/docker-compose.yml up -d

```

启动后，API 服务器提供：
- 所有记忆操作的 RESTful API 端点
- 交互式 API 文档，访问 `http://localhost:8000/docs`
- API Key 认证和限流支持
- 与 SDK 相同的配置（通过 `.env` 文件）

完整的 API 文档和使用示例，请参阅 [API 服务器文档](docs/api/0005-api_server.md)。

### 🔌 MCP Server

PowerMem 还提供了模型上下文协议（MCP）服务器，支持与 Claude Desktop 等 MCP 兼容客户端集成。MCP 服务器通过 MCP 协议暴露 PowerMem 的记忆管理功能，使 AI 助手能够无缝访问和管理记忆。

**与 SDK 的关系**：MCP 服务器使用相同的 PowerMem SDK 并共享相同的配置（`.env` 文件）。它提供了与 Python SDK 相同的记忆管理功能的 MCP 接口，使 PowerMem 可供 MCP 兼容的 AI 助手使用。

**安装**：

```bash
# 安装 PowerMem（必需）
pip install powermem

# 安装 uvx（如果尚未安装）
# 在 macOS/Linux 上：
curl -LsSf https://astral.sh/uv/install.sh | sh

# 在 Windows 上：
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**启动 MCP 服务器**：

```bash
# SSE 模式（推荐，默认端口 8000）
uvx powermem-mcp sse

# SSE 模式，自定义端口
uvx powermem-mcp sse 8001

# Stdio 模式
uvx powermem-mcp stdio

# Streamable HTTP 模式（默认端口 8000）
uvx powermem-mcp streamable-http

# Streamable HTTP 模式，自定义端口
uvx powermem-mcp streamable-http 8001
```

**与 Claude Desktop 集成**：

在 Claude Desktop 配置文件中添加以下配置：

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

MCP 服务器提供记忆管理工具，包括添加、搜索、更新和删除记忆。完整的 MCP 文档和使用示例，请参阅 [MCP 服务器文档](docs/api/0004-mcp.md)。

## 🔗 集成与演示

- 🔗 **LangChain 集成**：基于 LangChain + PowerMem + OceanBase 构建医疗支持机器人，[查看示例](examples/langchain/README.md)
- 🔗 **LangGraph 集成**：基于 LangGraph + PowerMem + OceanBase 构建客户服务机器人，[查看示例](examples/langgraph/README.md)

## 📚 文档

- 📖 **[入门指南](docs/guides/0001-getting_started.md)**：安装和快速开始指南
- ⚙️ **[配置指南](docs/guides/0003-configuration.md)**：完整的配置选项
- 🤖 **[多智能体指南](docs/guides/0005-multi_agent.md)**：多智能体场景和示例
- 🔌 **[集成指南](docs/guides/0009-integrations.md)**：集成指南
- 📦 **[子存储指南](docs/guides/0006-sub_stores.md)**：子存储的使用方法和示例
- 📋 **[API 文档](docs/api/overview.md)**：完整的 API 参考
- 🏗️ **[架构指南](docs/architecture/overview.md)**：系统架构和设计
- 📓 **[示例](docs/examples/overview.md)**：交互式 Jupyter 笔记本和使用案例
- 👨‍💻 **[开发者文档](docs/development/overview.md)**：开发者文档

## ⭐ 重点发布说明

| Version | Release Date | Function |
|---------|-------|---------|
| 0.3.0 |  2026.01.09 | <ul><li>生产就绪的 HTTP API 服务器，提供所有记忆操作的 RESTful 端点</li><li>Docker 支持，便于部署和容器化</li>></ul> |
| 0.2.0 | 2025.12.16 | <ul><li>高级用户画像管理，支持 AI 应用的"千人千面"</li><li>扩展多模态支持，包括文本、图像和音频记忆</li></ul> |
| 0.1.0 | 2025.11.14 | <ul><li>核心记忆管理功能，支持持久化存储记忆</li><li>支持向量、全文和图的混合检索</li><li>基于 LLM 的事实提取智能记忆</li><li>支持基于艾宾浩斯遗忘曲线的全生命周期记忆管理</li><li>支持 Multi-Agent 记忆管理</li><li>多存储后端支持（OceanBase、PostgreSQL、SQLite）</li><li>支持通过多跳图检索的方式处理知识图谱的检索</li></ul> |

## 💬 支持

- 🐛 **问题反馈**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- 💭 **讨论交流**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 📄 许可证

本项目采用 Apache License 2.0 许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。