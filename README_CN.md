# PowerContext

**不止于记忆**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的升级版本，也是面向人机协作的上下文运行层。它将共同推进的工作沉淀为可理解、可交接、可延续的项目上下文。

## 核心能力

| 能力 | 核心价值 |
| --- | --- |
| Memory 抽取与管理 | 显式记录值得长期复用的决策、约束、结果、状态和下一步；配置生成模型后，也可以从 Source 中提取 Memory。修订和停用均保留历史 |
| 请求时有界召回 | 在 Agent 处理请求前，按项目 scope、相关性和字节预算生成一份通过 schema 验证、带有 citation 的 `PreparedContext`；召回失败不会阻断原任务 |
| Handoff 任务交接 | 将目标、已验证进度、阻塞项、下一步和证据整理为可检查的工作包，让另一个会话、任务、模型或 Agent Host 从明确状态继续工作 |
| Source 与证据链 | 保存知识的原始来源，并用精确 citation 关联 Memory 和 Artifact；采集 prompt 只会生成 Source，不会直接将其变成 Memory |
| Experience 和 Skill 治理 | 模型或调用方只能提交 Candidate；Review 通过后才会形成不可变的 revision，Skill 还需显式导出，不会自行批准、安装或执行 |
| 本地与服务化部署 | 本地开发可直接使用 SQLite，团队部署可选 OceanBase，并通过 HTTP/OpenAPI、MCP、身份验证和 OpenTelemetry 接入现有系统 |

## Benchmarks

### [LoCoMo](https://github.com/snap-research/locomo)

| 指标 | PowerContext | [PowerMem](https://www.powermem.ai/benchmark) | 全上下文基线 |
| --- | ---: | ---: | ---: |
| 准确率 | **90.78%**（1,398/1,540） | 87.79% | 52.9% |
| 搜索 p95 延迟 | **1.38 秒** | 1.44 秒 | 17.12 秒 |
| 每个问题的回答 token 数 | **约 1.65k** | 约 0.9k | 26k |

PowerContext 的结果覆盖了该 benchmark 全部 10 个对话中的 1,540 道计分题。数据集选择、检索、judge、延迟、token 和 Artifact 边界详见
[LoCoMo 评估详情](benchmark/locomo/README.md)。

### [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os)

在 [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os) 全部 **731 项任务**的配对评估中，
启用 PowerContext 后，任务解决率从 **82.35%** 提升至 **86.73%**，提高了 **4.38 个百分点**。

本次评估在 Codex 环境中运行，PowerContext OFF 与 ON 两组均使用 `gpt-5.6-sol` 模型。

| 结果 | PowerContext OFF | PowerContext ON | 变化 |
| --- | ---: | ---: | ---: |
| 已解决任务 | 602 / 731 | **634 / 731** | **+32** |
| 任务解决率 | 82.35% | **86.73%** | **+4.38 个百分点** |

[SWE-bench Pro public v2 评估详情](evaluation/README.md)。

---

## 插件

PowerContext 为 Codex、Claude Code 和 DeepSeek Harness 提供官方插件与安装指南。三个集成都通过
PowerContext Server 使用同一套作用域数据和保留历史的契约；插件不会自行启动或内嵌 Server。

### 官方集成

<table>
<tr>
<td align="center" width="120"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></td>
<td align="center" width="120"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></td>
<td align="center" width="120"><img src="https://github.com/deepseek-ai.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></td>
</tr>
</table>


## 快速开始

你需要 macOS 或 Linux、Python 3.11 或更高版本、[`uv`](https://docs.astral.sh/uv/) 和 Codex CLI。

### 1. 安装 PowerContext 和 Plugin

```bash
uv tool install "powercontext[cli,server]==0.0.2"

# Choose one or more integrations.
powercontext setup codex --source oceanbase/powercontext --ref v0.0.2
powercontext setup claude-code --source oceanbase/powercontext --ref v0.0.2
powercontext setup dsh --source oceanbase/powercontext --ref v0.0.2
```

第一条命令会在隔离环境中安装 CLI 和本地 Server。第二条命令会从匹配的仓库 tag 安装对应的 Plugin。如需刷新现有安装，
请再次运行 setup。

### 2. 启动并验证本地 Server

在一个终端中保持 Server 运行：

```bash
powercontext server run
```

在另一个终端中验证服务和 Plugin：

```bash
powercontext doctor
powercontext doctor codex  # or: claude-code / dsh
```

默认情况下，Server 监听 `127.0.0.1:8000`，在 `/mcp` 提供 Streamable HTTP MCP，并将数据持久化到本地
SQLite 数据库。显式 Memory 操作无需配置 inference provider 即可使用。

## 开发

安装锁定的开发环境和 Hook：

```bash
make install
```

提交 Pull Request 前，请运行主要验证命令：

```bash
make check
make test
make docs-test
```

修改 `openapi/powercontext.yaml` 后，请运行 `make contract-test`。完整工作流程参见
[CONTRIBUTING.md](CONTRIBUTING.md)，实现指南参见
[`docs/zh/development/`](docs/zh/development/core-protocol.md)。

## 社区

欢迎在 [Discord](https://discord.com/invite/74cF8vbNEs) 中提问和反馈。如需报告可复现的缺陷或提出范围明确的功能请求，
请使用 [GitHub Issues](https://github.com/oceanbase/powercontext/issues)。

## 许可证

PowerContext 使用 [Apache License 2.0](LICENSE) 许可证。
