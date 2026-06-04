# PowerMem

**面向 AI 应用与智能体的持久化、自进化记忆层。**

[![PyPI version](https://img.shields.io/pypi/v/powermem)](https://pypi.org/project/powermem/)
[![PyPI downloads](https://img.shields.io/pypi/dm/powermem)](https://pypi.org/project/powermem/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/powermem/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-oceanbase%2Fpowermem-181717?logo=github)](https://github.com/oceanbase/powermem)
[![Discord](https://img.shields.io/badge/Discord-社区-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · 中文 · [日本語](README_JP.md)*

PowerMem 融合向量、全文与图检索，由 LLM 驱动记忆抽取，并叠加艾宾浩斯型时间衰减；原生支持**经验 (Experience) + 技能 (Skill) 双层蒸馏**的自进化记忆、多智能体隔离、用户画像，以及文本/图像/音频等多模态信号。

---

## 性能基准

### [LOCOMO](https://github.com/snap-research/locomo)

| 维度 | PowerMem | 基线 | 提升 |
|------|----------|-------------------------|-------------|
| 准确率 | **87.79%** | 52.9% | **+65.9%** |
| 检索 p95 延迟 | **1.44 s** | 17.12 s | **-91.6%** |
| Token 开销 | **~0.9 k** | 26 k | **-96.5%** |

### [AppWorld](https://github.com/StonyBrookNLP/appworld)

| 维度 | PowerMem | 基线 | 提升 |
|------|----------|-------------------------|-------------|
| 通过率 | **39%** | 24% | **+62.5%** |
| 平均步数 | **6.2** | 9.5 | **-34.7%** |
| 总 Token | **1.74 M** | 2.56 M | **-32.0%** |

复现脚本：[`benchmark/`](benchmark/)。背后机制：**Experience + Skill 双层蒸馏 + 4 路混合检索 + LLM 自归并**（API: `memory.distill_all() / add_skill / add_experience / search_*`，示例 [`examples/experience_skill_usage.py`](examples/experience_skill_usage.py)）。

---

## 集成 

PowerMem 为最常见的 AI 客户端提供官方插件与安装指南。它们均指向同一后端（HTTP 服务、MCP 服务或本地 `pmem` CLI）——无需为每个客户端重写配置 schema。所有智能体共享同一记忆服务。

### AI 智能体与 IDE

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

### SDK 与应用

| 应用 / 框架 | 说明 |
|-----------------|---------|
| Python SDK | `pip install powermem`，见 [快速开始](#quick-start-python-sdk) |
| LangChain / LangGraph | `pip install powermem`，见 [LangChain 指南](docs/integrations/langchain.md) |
| Go 应用 | [SDK](#sdks) |
| Java 应用 | [SDK](#sdks) |
| TypeScript 应用 | [SDK](#sdks) |
| 任意 MCP 客户端 | `powermem-mcp sse`（默认 :8848），见 [MCP 客户端指南](docs/integrations/mcp_client.md) |
| HTTP REST 应用 | `powermem-server --host 0.0.0.0 --port 8848`，见 [API 服务](docs/api/0005-api_server.md) |

<a id="openclaw-clawdbot"></a>

### OpenClaw（ClawdBot）

[OpenClaw](https://github.com/openclaw/openclaw) 通过插件 [`memory-powermem`](https://github.com/ob-labs/memory-powermem) 获得长期记忆。

```bash
openclaw plugins install memory-powermem
```

默认 **CLI 模式** — 插件调用内置 `pmem`，数据写入 `~/.openclaw/` 下的 SQLite，并复用 OpenClaw 已注入的模型。无需单独启动服务，也无需额外 `.env`。若团队更倾向共享 PowerMem API，可切换到 **HTTP 模式**（见插件 README 中的 `requestConfig.memory_db`）。

完整指南：[OpenClaw 集成](docs/integrations/openclaw.md)。

<div align="center">

<img src="docs/images/openclaw_powermem.jpeg" alt="PowerMem 与 OpenClaw" width="640"/>

</div>

<a id="claude-code"></a>

### Claude Code

#### 推荐方式 — 让 Claude Code 自行完成配置

先下载代码并进入目录：

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

在终端打开 Claude Code，粘贴下面这一行：

```text
Read and follow apps/claude-code-plugin/SETUP.md to set up PowerMem memory for Claude Code.
```

Claude Code 会阅读 [`apps/claude-code-plugin/SETUP.md`](apps/claude-code-plugin/SETUP.md)，向你询问少量必要密钥，并端到端完成全部配置。

#### 手动配置

希望手动安装？完整步骤 — 环境变量、MCP 模式、`remember` / `recall` 技能、Windows 钩子、排错与卸载 — 见 **[docs/integrations/claude_code.md](docs/integrations/claude_code.md)**。

<a id="cursor-vs-code-windsurf-github-copilot-qoder"></a>

### Cursor、VS Code、Windsurf、GitHub Copilot、Qoder

#### 推荐方式 — 让 IDE 智能体代为配置

先下载代码并进入目录：

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

在 IDE 的 AI 智能体窗口粘贴下面这一行：

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

智能体会按 [`apps/vscode-extension/SETUP.md`](apps/vscode-extension/SETUP.md) 操作：优先使用可复用的 `powermem-server` HTTP API 后端，仅在 HTTP 不可用时回退到纯 MCP，并只配置当前 IDE/客户端，而非无关工具。

#### 手动配置

希望亲手接线？按 IDE 查阅对应指南：

| 客户端 | 说明 |
|--------|---------|
| VS Code | [`docs/integrations/vs_code.md`](docs/integrations/vs_code.md) |
| Cursor | [`docs/integrations/cursor.md`](docs/integrations/cursor.md) |
| Windsurf | [`docs/integrations/windsurf.md`](docs/integrations/windsurf.md) |
| GitHub Copilot | [`docs/integrations/github_copilot.md`](docs/integrations/github_copilot.md) |
| Qoder | [`docs/integrations/qoder.md`](docs/integrations/qoder.md) |

同一扩展还提供 **Query memories**、**Add selection to memory**、**Quick note**，以及状态栏 **Dashboard**。详见 [`apps/vscode-extension/README.md`](apps/vscode-extension/README.md) 与完整 [VS Code 指南](docs/integrations/vs_code.md)。

<a id="any-mcp-client"></a>

### 任意 MCP 客户端

适用于 Claude Desktop、Codex、Cline、OpenCode、Roo Code、Goose 或其他兼容 MCP 的客户端。请使用 MCP Client 模式。

先下载代码并进入目录：

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

在 MCP 客户端或 IDE 的 AI 智能体窗口粘贴下面这一行：

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

智能体会按 [`apps/mcp-client/SETUP.md`](apps/mcp-client/SETUP.md) 操作：直接使用 `powermem-mcp`，优先在 `8848` 端口使用 SSE，仅在必要时回退到 streamable HTTP 或 stdio，且只配置目标 MCP 客户端。

希望手动安装？见 [通用 MCP 客户端指南](docs/integrations/mcp_client.md)。卸载见 [`apps/mcp-client/UNINSTALL.md`](apps/mcp-client/UNINSTALL.md)。暴露的接口：`add_memory`、`search_memories`、`get_memory_by_id`、`update_memory`、`delete_memory`、`delete_all_memories`、`list_memories`。完整参考：[MCP Server](docs/api/0004-mcp.md)。各客户端说明：[Cline](docs/integrations/cline.md)、[Codex](docs/integrations/codex.md)、[OpenCode](docs/integrations/opencode.md)。

### LangChain & LangGraph

```bash
pip install powermem langchain langchain-openai
```

示例：

- [LangChain 医疗问答 Bot](examples/langchain/README.md)
- [LangGraph 客服机器人](examples/langgraph/README.md)

完整框架指南：[LangChain 与 LangGraph 集成](docs/integrations/langchain.md)。

<a id="sdks"></a>

### SDK

| 语言 | 包 / 仓库 |
|----------|---------|
| Python | `pip install powermem`（本仓库） |
| Go | [`ob-labs/powermem-go`](https://github.com/ob-labs/powermem-go) |
| Java | [`ob-labs/powermem-java`](https://github.com/ob-labs/powermem-java) |
| TypeScript | [`ob-labs/powermem-ts`](https://github.com/ob-labs/powermem-ts) |

---

<a id="quick-start-python-sdk"></a>

## 快速开始（Python SDK）

**前置条件：** 将 [.env.example](.env.example) 复制为 `.env`，仅需配置 **LLM** 的 API key。如需零配置本地存储，请安装 `seekdb` extra（`pip install "powermem[seekdb]"`，或与 `server` / `mcp` 组合安装），这样默认的 **OceanBase** provider 才能在未配置 host 时启动 **嵌入式 seekdb**。未安装 `seekdb` 时，请设置 `OCEANBASE_HOST` 连接远端 OceanBase 集群，或改用 `sqlite` / `postgres`。默认 embedder 是本地的 `all-MiniLM-L6-v2`（384 维），无需 API key，首次使用时自动下载。如需更高性能或开启高级特性，可改用 [.env.example.full](.env.example.full)，其中按组件分组记录了所有可调参数。安装后执行 `pmem config init` 可交互式生成同样的配置。详见 [入门指南](docs/guides/0001-getting_started.md)。

### 安装

```bash
# 仅核心（SDK；不包含 CLI/server/MCP/seekdb）
pip install powermem

# 带 CLI（pmem / powermem-cli）
pip install "powermem[cli]"

# 仅带 HTTP API Server（powermem-server；不安装 seekdb）
pip install "powermem[server]"

# 仅带 MCP Server（powermem-mcp；不安装 seekdb）
pip install "powermem[mcp]"

# 带 seekdb（零配置本地存储 / embedder 必需）
pip install "powermem[seekdb]"

# HTTP API Server + seekdb
pip install "powermem[server,seekdb]"

# MCP Server + seekdb
pip install "powermem[mcp,seekdb]"

# 常用本地完整安装
pip install "powermem[cli,server,mcp,seekdb]"
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
powermem-server --host 0.0.0.0 --port 8848
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
| Storage | OceanBase（含图存储）、嵌入式 seekdb、PostgreSQL/pgvector、SQLite |

---

## 文档

- [入门指南](docs/guides/0001-getting_started.md) — 安装、`.env`、首个 `Memory` 用法
- [配置指南](docs/guides/0003-configuration.md) — 配置模型、存储后端、环境变量
- [架构说明](docs/architecture/overview.md) — 组件、存储布局与检索流程
- [API 与服务](docs/api/overview.md) — REST、MCP、HTTP 服务与 Python 侧 API
- [CLI 使用指南](docs/guides/0012-cli_usage.md) — `pmem`、交互 Shell、备份与迁移
- [多智能体](docs/guides/0005-multi_agent.md) — 作用域、隔离与跨智能体共享
- [集成说明](docs/guides/0009-integrations.md) — LangChain 等框架接入
- [生态集成](docs/integrations/overview.md) — AI 客户端与 IDE（[Claude Code](docs/integrations/claude_code.md) 等）
- [Docker 与部署](docker/README.md) — 镜像、Compose、运行 API 服务
- [开发说明](docs/development/overview.md) — 本地开发、测试与贡献

更多：[子存储](docs/guides/0006-sub_stores.md)、[指南索引](docs/guides/overview.md)。

## 示例

- [场景与 Notebook](docs/examples/overview.md) — 按场景分步说明（基础用法、多模态、遗忘曲线、稀疏向量、子存储等）
- 客户端 / IDE 侧入口（OpenClaw、Claude Code、VS Code 扩展、MCP、LangChain、LangGraph）见上方 [集成](#集成--选客户端复制一行即可接入) 一节。

## 版本要点

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.2.0 | 2026-04 | Experience + Skill 双层蒸馏与 `distill_all()`（自进化记忆，AppWorld +15 pts）；OB MASS Embedding；Qwen VL 多模态 Embedding；OceanBase Zero Mode 兼容；LOCOMO 准确率提升至 87.79% |
| 1.1.0 | 2026-04-02 | OceanBase 存储支持嵌入式 seekdb，无需单独部署数据库服务；[IDE 集成](apps/README.md)（VS Code 扩展、Claude Code 插件） |
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
