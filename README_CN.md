# PowerMem

**面向 AI 应用与智能体的持久化、自进化的记忆层。**

[![PyPI version](https://img.shields.io/pypi/v/powermem)](https://pypi.org/project/powermem/)
[![PyPI downloads](https://img.shields.io/pypi/dm/powermem)](https://pypi.org/project/powermem/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/powermem/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-oceanbase%2Fpowermem-181717?logo=github)](https://github.com/oceanbase/powermem)
[![Discord](https://img.shields.io/badge/Discord-社区-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · 中文 · [日本語](README_JP.md)*

PowerMem 融合向量、全文与图检索，由 LLM 驱动记忆抽取，并叠加艾宾浩斯型时间衰减；原生支持**经验 (Experience) + 技能 (Skill) 双层蒸馏**的自进化记忆、多智能体隔离、用户画像，以及文本/图像/音频等多模态线索。

---

## 性能基准

### [LOCOMO](https://github.com/snap-research/locomo)

| 维度 | PowerMem | 基线 | 提升 |
|------|----------|--------------------|------|
| 准确率 | **87.79%** | 52.9% | **+65.9%** |
| 检索 p95 延迟 | **1.44 s** | 17.12 s | **-91.6%** |
| Token 开销 | **~0.9 k** | 26 k | **-96.5%** |

### [AppWorld](https://github.com/StonyBrookNLP/appworld)

| 维度 | PowerMem | 基线 | 提升 |
|------|----------|----------------------|------|
| 通过率 | **39%** | 24% | **+62.5%** |
| 平均步数 | **6.2** | 9.5 | **-34.7%** |
| 总 Token | **1.74 M** | 2.56 M | **-32.0%** |

复现脚本：[`benchmark/`](benchmark/)。背后机制：**Experience + Skill 双层蒸馏 + 4 路混合检索 + LLM 自归并**（API: `memory.distill_all() / add_skill / add_experience / search_*`，示例 [`examples/experience_skill_usage.py`](examples/experience_skill_usage.py)）。

---

## 集成 — 选客户端，复制一行命令即可接入

PowerMem 为最常见的 AI 客户端提供了一方插件。所有插件共用同一个后端（HTTP 服务或本地 `pmem` CLI），无需为每个客户端单独改配置。

| 客户端 / 框架 | 一行接入命令 | 模式 |
|---------------|--------------|------|
| OpenClaw（ClawdBot） | `openclaw plugins install memory-powermem` | CLI（默认）/ HTTP（可选） |
| Claude Code | `git clone https://github.com/oceanbase/powermem && claude --plugin-dir powermem/apps/claude-code-plugin` | HTTP（默认）/ MCP（可选） |
| Cursor / VS Code / Codex / Windsurf / GitHub Copilot | 安装 [PowerMem VS Code 扩展](apps/vscode-extension/) 后运行 **PowerMem: Link to AI tools** | MCP 或 HTTP，按客户端而定 |
| Claude Desktop / Cline / 任意 MCP 客户端 | `uvx powermem-mcp sse` | MCP（SSE / stdio / streamable-http） |
| LangChain / LangGraph | `pip install powermem`，参考 [示例](#示例) | Python SDK |
| Go / Java / TypeScript 应用 | 见下方 [多语言 SDK](#多语言-sdk) | HTTP REST |

### OpenClaw（ClawdBot）

[OpenClaw](https://github.com/openclaw/openclaw) 通过插件 [`memory-powermem`](https://github.com/ob-labs/memory-powermem) 获得长期记忆。

```bash
openclaw plugins install memory-powermem
```

**默认 CLI 模式** — 插件内部直接调用打包好的 `pmem`，把数据写入 `~/.openclaw/` 下的 SQLite，并复用 OpenClaw 已注入的模型；**不需要额外启动服务，也不需要单独配 `.env`**。如需团队共享后端，切换到 **HTTP 模式** 即可（在插件 `config` 中配置 `mode: "http"` 与 `requestConfig.memory_db`）。详情见插件仓库 README。

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem 与 OpenClaw" width="640"/>

</div>

### Claude Code

```bash
# 从本仓库直接加载（开发/调试推荐）
claude --plugin-dir /path/to/powermem/apps/claude-code-plugin

# 或者打包成 zip 发到目标机器，再 --plugin-dir 指向解压目录
make package-claude-plugin   # 产物：apps/claude-code-plugin/dist/<version>.zip
```

默认 **HTTP 模式**，开箱即用：

- `UserPromptSubmit` → `POST /api/v1/memories/search`，命中结果通过 `additionalContext` 注入当前对话；
- `SessionEnd` / `PostCompact` → `POST /api/v1/memories`，把整段对话或压缩摘要写回记忆；
- 终端机器**无需 Python**，hook 是预编译的原生二进制（macOS / Linux / Windows）。

如果想让 Claude 在对话中显式调用 `search_memories` / `add_memory` 工具，切到 **MCP 模式** 即可：

```bash
bash scripts/apply-connection-mode.sh mcp
```

完整说明：[`apps/claude-code-plugin/README.md`](apps/claude-code-plugin/README.md)。

### Cursor / VS Code / Codex / Windsurf / GitHub Copilot

安装一次 **PowerMem VS Code 扩展**（在 VS Code 和 Cursor 中都能用），然后执行 **PowerMem: Link to AI tools** 命令 — 扩展会自动给每个支持的客户端写好 MCP 或 HTTP 配置：

| 客户端 | 写入的配置文件 |
|--------|----------------|
| Cursor | `~/.cursor/mcp.json`（合并写入） |
| Claude（Desktop / Code） | `~/.claude/providers/powermem.json` |
| Codex | `~/.codex/context.json`（合并写入） |
| Windsurf | `~/.windsurf/context/powermem.json` |
| GitHub Copilot | `~/.github/copilot/powermem.json` |

同一扩展还提供 **Query memories**、**Add selection to memory**、**Quick note** 命令，以及状态栏 **Dashboard**。详见 [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md)。

### 任意 MCP 客户端（Claude Desktop、Cline……）

```bash
uvx powermem-mcp sse                  # SSE，默认 :8000（推荐）
uvx powermem-mcp stdio                # stdio
uvx powermem-mcp streamable-http      # streamable HTTP
```

Claude Desktop / 多数 MCP 客户端的配置：

```json
{
  "mcpServers": {
    "powermem": { "url": "http://localhost:8000/mcp" }
  }
}
```

暴露的工具：`add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories`、`list_memories`。完整参考：[MCP Server](docs/api/0004-mcp.md)。

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

端到端可跑示例：

- [LangChain 医疗问答 Bot](examples/langchain/README.md)
- [LangGraph 客服机器人](examples/langgraph/README.md)

### 多语言 SDK

| 语言 | 包 / 仓库 |
|------|-----------|
| Python | `pip install powermem`（本仓库） |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

## 快速开始（Python SDK）

**前置条件：** 将 [.env.example](.env.example) 复制为 `.env`，配置 **LLM** 与 **向量嵌入** 凭证。默认数据库是 SQLite；OceanBase 后端可使用 **嵌入式 SeekDB**，不必额外部署数据库进程。安装后执行 `pmem config init` 可交互式生成同样的配置。详见 [入门指南](docs/guides/0001-getting_started.md)。

### 安装

```bash
pip install powermem
```

### SDK 用法

在含已配置 `.env` 的目录下运行：

```python
from powermem import Memory, auto_config

memory = Memory(config=auto_config())

memory.add("用户喜欢咖啡", user_id="user123")

for r in memory.search("用户偏好", user_id="user123").get("results", []):
    print("-", r.get("memory"))
```

更多用法见 [入门指南](docs/guides/0001-getting_started.md)。

### CLI（`pmem`，1.0+）

```bash
pmem memory add "用户偏好深色模式" --user-id user123
pmem memory search "偏好" --user-id user123
pmem shell                           # 交互式 REPL
```

完整说明：[CLI 使用指南](docs/guides/0012-cli_usage.md)。

### HTTP API Server + Dashboard

与 SDK 共用 `.env`，Dashboard 路径 `/dashboard/`。

```bash
powermem-server --host 0.0.0.0 --port 8000
```

Docker / Compose 部署见 [API Server](docs/api/0005-api_server.md) 与 [Docker 说明](docker/README.md)。官方镜像：`oceanbase/powermem-server:latest`。

---

## 能力概览

**记忆管线与检索** — [智能抽取与更新](docs/examples/scenario_2_intelligent_memory.md)；[Experience + Skill 双层蒸馏（自进化）](docs/examples/scenario_6_sub_stores.md)；[艾宾浩斯时间衰减](docs/examples/scenario_8_ebbinghaus_forgetting_curve.md)；[混合检索（向量 / 全文 / 图）](docs/examples/scenario_2_intelligent_memory.md)；[子存储与路由](docs/examples/scenario_6_sub_stores.md)。

**用户画像与多智能体** — [用户画像](docs/examples/scenario_9_user_memory.md)；[共享 / 隔离记忆与作用域](docs/examples/scenario_3_multi_agent.md)。

**多模态** — [文本 / 图像 / 语音](docs/examples/scenario_7_multimodal.md)。

**Provider 矩阵**

| 层 | 已内置的 Provider |
|----|-------------------|
| LLM | Anthropic、OpenAI、Azure OpenAI、Gemini、Qwen（+ ASR 语音）、DeepSeek、Ollama、vLLM、SiliconFlow、Z.AI、LangChain 包装层 |
| Embedding | OpenAI、Azure OpenAI、Qwen（+ VL 多模态、稀疏向量）、Gemini、Vertex AI、AWS Bedrock、Ollama、LM Studio、HuggingFace、Together、SiliconFlow、Z.AI、OceanBase MASS、LangChain 包装层 |
| Rerank | Jina、Qwen、Z.AI、通用接口 |
| Storage | OceanBase（含图存储）、嵌入式 SeekDB、PostgreSQL/pgvector、SQLite |

---

## 文档

- [入门指南](docs/guides/0001-getting_started.md) — 安装、`.env`、首个 `Memory` 用法
- [配置指南](docs/guides/0003-configuration.md) — 配置模型、存储后端、环境变量
- [架构说明](docs/architecture/overview.md) — 组件、存储布局与检索流程
- [API 与服务](docs/api/overview.md) — REST、MCP、HTTP 服务与 Python 侧 API
- [CLI 使用指南](docs/guides/0012-cli_usage.md) — `pmem`、交互 Shell、备份与迁移
- [多智能体](docs/guides/0005-multi_agent.md) — 作用域、隔离与跨智能体共享
- [集成说明](docs/guides/0009-integrations.md) — LangChain 等框架接入
- [Docker 与部署](docker/README.md) — 镜像、Compose、运行 API 服务
- [开发说明](docs/development/overview.md) — 本地开发、测试与贡献

更多：[子存储](docs/guides/0006-sub_stores.md)、[指南索引](docs/guides/overview.md)。

## 示例

- [场景与 Notebook](docs/examples/overview.md) — 按场景分步说明（基础用法、多模态、遗忘曲线、稀疏向量、子存储等）
- 客户端 / IDE 侧入口（OpenClaw、Claude Code、VS Code 扩展、MCP、LangChain、LangGraph）见上方 [集成](#集成--选客户端复制一行命令即可接入) 一节。

## 版本要点

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.2.0 | 2026-04 | Experience + Skill 双层蒸馏与 `distill_all()`（自进化记忆，AppWorld +15 pts）；OB MASS Embedding；Qwen VL 多模态 Embedding；OceanBase Zero Mode 兼容；LOCOMO 准确率提升至 87.79% |
| 1.1.0 | 2026-04-02 | OceanBase 存储支持嵌入式 SeekDB，无需单独部署数据库服务；[IDE 集成](apps/README.md)（VS Code 扩展、Claude Code 插件） |
| 1.0.0 | 2026-03-16 | CLI（`pmem`）：记忆操作、配置、备份/恢复/迁移、交互 Shell、补全；Web Dashboard |
| 0.5.0 | 2026-02-06 | SDK/API 统一配置（pydantic-settings）；OceanBase 原生混合检索；记忆查询与列表排序；用户画像输出语言定制 |
| 0.4.0 | 2026-01-20 | 稀疏向量混合检索；基于画像的查询改写；表结构升级与迁移工具 |
| 0.3.0 | 2026-01-09 | 生产级 HTTP API Server；Docker |
| 0.2.0 | 2025-12-16 | 高级画像；多模态（文本/图像/语音） |
| 0.1.0 | 2025-11-14 | 核心记忆与混合检索；LLM 抽取；遗忘曲线；多智能体；OceanBase/PostgreSQL/SQLite；图检索 |

## 支持

- [GitHub Issues](https://github.com/oceanbase/powermem/issues)
- [GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

## 许可证

Apache License 2.0 — 见 [LICENSE](LICENSE)。
