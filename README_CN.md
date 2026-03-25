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

# PowerMem — 智能 AI 记忆系统

**PowerMem** 是面向 AI 应用的长期记忆基础设施：混合 **向量 / 全文 / 图** 检索，结合 **艾宾浩斯遗忘曲线** 与 **LLM 记忆抽取**，支持 **多智能体隔离与协作**、**用户画像** 与 **多模态**（文本、图像、音频）。同一套能力可通过 **Python SDK**、**CLI（`pmem`）**、**HTTP API Server（含 Dashboard）** 与 **MCP Server** 接入，配置统一（`.env`）。

> **动态：** [OpenClaw](https://github.com/openclaw-ai/openclaw) 可通过插件 [memory-powermem](https://github.com/ob-labs/memory-powermem) 使用 PowerMem 作为长期记忆（`openclaw plugins install memory-powermem`）。

## 为什么选择 PowerMem

<div align="center">

<img src="docs/images/benchmark_metrics_cn.svg" alt="PowerMem LOCOMO 压测指标" width="900"/>

</div>

在 [LOCOMO](https://github.com/snap-research/locomo) 基准上相对「全量上下文」方案：

- **更准**：[准确率提升 48.77%]（78.70% VS 52.9%）
- **更快**：[检索 p95 延迟显著降低]（1.44s VS 17.12s，约 91.83%）
- **更省**：[Token 用量显著下降]（约 0.9k VS 26k，约 96.53%）

## 核心特性

### 开发者友好

- **[轻量级接入](docs/examples/scenario_1_basic_usage.md)**：Python SDK 自动从 `.env` 加载配置；另支持 [CLI](docs/guides/0012-cli_usage.md)（`pmem`）、[MCP Server](docs/api/0004-mcp.md)、[HTTP API Server](docs/api/0005-api_server.md)

### 智能记忆管理

- **[智能记忆提取](docs/examples/scenario_2_intelligent_memory.md)**：基于 LLM 抽取事实、去重、消解冲突、合并相关记忆
- **[艾宾浩斯遗忘曲线](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)**：按时间与相关性衰减，优先返回更近、更相关的记忆

### 用户画像

- **[用户画像](docs/examples/scenario_9_user_memory.md)**：从对话与行为构建/更新画像，服务个性化推荐、陪伴等场景

### 多智能体

- **[共享 / 隔离记忆](docs/examples/scenario_3_multi_agent.md)**：按智能体划分记忆空间，支持跨 Agent 协作与基于作用域的权限控制

### 多模态

- **[文本 / 图像 / 语音](docs/examples/scenario_7_multimodal.md)**：媒体转述后入库，支持图文声混合检索

### 存储与检索

- **[子存储 Sub Stores](docs/examples/scenario_6_sub_stores.md)**：分区与自动路由，适合超大规模数据
- **[混合检索](docs/examples/scenario_2_intelligent_memory.md)**：向量 + 全文 + 图（含多跳），并可由 LLM 辅助构图

## 快速开始

下面按顺序介绍：安装 → **Python SDK** → **CLI（`pmem`）** → **HTTP API 与 Dashboard** → **MCP**。配置见 [.env.example](.env.example) 与 [配置指南](docs/guides/0003-configuration.md)。

### 安装

```bash
pip install powermem
```

### 基本使用（SDK）

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

memory.add("用户喜欢咖啡", user_id="user123")

results = memory.search("用户偏好", user_id="user123")
for result in results.get('results', []):
    print(f"- {result.get('memory')}")
```

更多示例见 [入门指南](docs/guides/0001-getting_started.md)。

### PowerMem CLI（1.0.0+）

`pmem` 支持记忆操作、配置、备份/恢复与交互式 shell。

```bash
pmem memory add "用户偏好深色模式" --user-id user123
pmem memory search "偏好" --user-id user123

pmem config show
pmem config init
pmem stats --json

pmem shell
```

详见 [CLI 使用指南](docs/guides/0012-cli_usage.md)。

### HTTP API Server 与 Dashboard

与 SDK **共用**同一套 `.env` 配置；对外提供 REST、`/dashboard/` 管理界面与 `/docs` OpenAPI。

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker 与 Compose 示例：

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest

docker-compose -f docker/docker-compose.yml up -d
```

完整说明见 [API Server 文档](docs/api/0005-api_server.md)。

### MCP Server

与 SDK **共用**配置；通过 MCP 把记忆能力接到 Claude Desktop 等客户端。

```bash
pip install powermem
# 安装 uv / uvx 见 https://docs.astral.sh/uv/getting-started/

uvx powermem-mcp sse              # 推荐；默认 8000
uvx powermem-mcp sse 8001
uvx powermem-mcp stdio
uvx powermem-mcp streamable-http
uvx powermem-mcp streamable-http 8001
```

Claude Desktop 配置示例（SSE）：

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

详见 [MCP 文档](docs/api/0004-mcp.md)。

## 生态与集成

### 示例项目

- **LangChain**：[医疗支持机器人示例](examples/langchain/README.md)
- **LangGraph**：[客服机器人示例](examples/langgraph/README.md)

## 文档索引

| 主题 | 链接 |
|------|------|
| 入门 | [Getting Started](docs/guides/0001-getting_started.md) |
| CLI | [CLI 使用指南](docs/guides/0012-cli_usage.md) |
| 配置 | [Configuration](docs/guides/0003-configuration.md) |
| 多智能体 | [Multi-Agent](docs/guides/0005-multi_agent.md) |
| 集成 | [Integrations](docs/guides/0009-integrations.md) |
| 子存储 | [Sub Stores](docs/guides/0006-sub_stores.md) |
| API | [API 总览](docs/api/overview.md) |
| 架构 | [Architecture](docs/architecture/overview.md) |
| 示例 | [Examples](docs/examples/overview.md) |
| 开发 | [Development](docs/development/overview.md) |

## 版本要点

| 版本 | 发布日期 | 说明 |
|------|----------|------|
| 1.0.0 | 2026-03-16 | CLI（`pmem`）：记忆操作、配置、备份/恢复/迁移、交互式 shell、补全；Web Dashboard |
| 0.5.0 | 2026-02-06 | 统一 SDK/API Server 配置（pydantic-settings）；OceanBase native hybrid search；Memory 查询与列表排序增强；用户画像支持自定义输出语言 |
| 0.4.0 | 2026-01-20 | 稀疏向量混合检索；基于用户画像的查询改写；表结构升级与迁移工具 |
| 0.3.0 | 2026-01-09 | 生产级 HTTP API Server；Docker 支持 |
| 0.2.0 | 2025-12-16 | 用户画像增强；多模态（文本/图像/音频） |
| 0.1.0 | 2025-11-14 | 核心记忆与混合检索；LLM 抽取；遗忘曲线；Multi-Agent；OceanBase/PostgreSQL/SQLite；图搜索 |

## 支持

- **问题反馈**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **讨论**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## 许可证

本项目采用 **Apache License 2.0**，详见 [LICENSE](LICENSE)。
