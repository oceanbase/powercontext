# PowerContext

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="theme/powercontext/assets/images/powercontext-reverse.png">
  <img alt="PowerContext" src="theme/powercontext/assets/images/powercontext-color.png" width="480" />
</picture>

**不止于记忆**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

</div>

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的升级版本，也是面向人机协作的上下文运行层。它将共同推进的工作沉淀为可理解、可交接、可延续的项目上下文。

## 快速开始

你需要 macOS 或 Linux、Python 3.11 或更高版本，以及 [`uv`](https://docs.astral.sh/uv/)。先选择入口：

- 已经有自己的 AI 应用，不使用 Agent Host：跟着 [HTTP API 生命周期教程](docs/zh/docs/tutorials/api-quickstart.md)，
  通过 HTTP 跑通第一个 Source、Memory、PreparedContext、Experience、Skill 和 Review 闭环；
- 使用 Codex、Claude Code、DSH、OpenCode 等 Host：跟着
  [Agent 分步入门](docs/zh/docs/tutorials/agent-quickstart.md)，按 Host 的真实能力接入 Memory、自动恢复和 Handoff。

下面保留更短的公共安装路径。

### 1. 安装 PowerContext；Agent 用户再安装集成

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"

# 只有 Agent Host 路线需要选择一个或多个集成。所有 setup 命令都从 master 分支安装。
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup claude-code --source oceanbase/powercontext --ref master
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext setup openclaw --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext setup pi --source oceanbase/powercontext --ref master
powercontext setup workbuddy --source oceanbase/powercontext --ref master

# 或者一次性安装多个 Host。
powercontext setup select --host codex --host claude-code --host opencode \
  --source oceanbase/powercontext --ref master
```

第一条命令会在隔离环境中从最新 `master` revision 安装 CLI 和本地 Server；每条 setup 命令都会从同一个
`master` revision 安装对应集成。如需刷新现有集成，请再次运行 setup。HTTP API 用户只需第一条安装命令，可以
跳过所有 `powercontext setup` 命令。

### 2. 启动并验证本地 Server

在一个终端中保持 Server 运行：

```bash
powercontext server run
```

在另一个终端中验证服务和 Plugin：

```bash
powercontext doctor
powercontext doctor integrations
powercontext doctor codex  # 请把 codex 换成已安装的宿主。
```

默认情况下，Server 监听 `127.0.0.1:8000`，在 `/mcp` 提供 Streamable HTTP MCP，并将数据持久化到本地
SQLite 数据库。显式 Memory 操作无需配置 inference provider 即可使用。

### 3. 跑通 Agent Memory 与 Handoff 闭环

从一个项目目录开启新会话，并按照 [Agent 分步入门](docs/zh/docs/tutorials/agent-quickstart.md)中的提示词操作。
教程会带你完成：

1. 选择并诊断一个已经安装的 Agent Host；
2. 保存显式项目 Memory，并在另一个会话中恢复；
3. 按 Host 使用一句话、`pc_*` 或 `/pc` Handoff；
4. 用 DSH → OpenCode 验证不依赖 Codex 的 exact Revision continuation。

第一个闭环不需要 generation model。需要从 Source 自动抽取 Memory 或使用向量搜索时，再继续配置推理服务。
Codex 专属 Hook 和一句话流程见 [Codex 完整教程](docs/zh/docs/tutorials/codex-quickstart.md)。

### 4. 或者，为自己的 AI 接入 HTTP API

不使用 Agent Host 时，在每次模型请求前调用 `POST /v1/context/prepare`，把返回的只读、不可信历史上下文交给
模型；只有在用户或业务策略明确授权后，才调用 `POST /v1/memory/remember` 保存长期 Memory。
[HTTP API 生命周期教程](docs/zh/docs/tutorials/api-quickstart.md)提供一个小型 Python 学习路径；全部 endpoint 和
schema 请查 [Scalar API 参考](https://oceanbase.github.io/powercontext/api/)。

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

![LOCOMO benchmark comparison showing PowerContext accuracy, search latency, and answer token usage against PowerMem and a full-context baseline](docs/assets/locomo-benchmark-comparison.svg)

### [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os)

![SWE-bench Pro public v2 comparison showing an increase from 82.35% with PowerContext off to 86.73% with PowerContext on](docs/assets/swe-bench-pro-public-v2-comparison.svg)

本次评估在 Codex 环境中运行，PowerContext OFF 与 ON 两组均使用 `gpt-5.6-sol` 模型。

---

## 集成

PowerContext 为 Codex、Claude Code、DeepSeek Harness、Hermes Agent、Pi Coding Agent、OpenClaw、OpenCode、
WorkBuddy、Bub、Pydantic AI、LangChain 和 LangGraph 提供官方集成与安装指南。
这些集成都通过 PowerContext Server 使用同一套作用域数据和保留历史的契约；宿主集成不会自行启动或内嵌 Server。

### 官方集成

<table>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-codex.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-claude-code.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-dsh.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-pi.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120" alt="Pi Coding Agent" width="48" height="48" /><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-openclaw.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-opencode.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120" alt="OpenCode" width="48" height="48" /><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-langchain.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-langgraph.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120" alt="LangGraph" width="48" height="48" /><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

Python Agent 应用可以使用 [LangChain 中间件](docs/zh/docs/how-to/configure-langchain.md)、
[LangGraph 节点与工具适配器](docs/zh/docs/how-to/configure-langgraph.md)、
[Pydantic AI 中间件](docs/zh/docs/how-to/configure-pydantic-ai.md) 或
[Bub 插件](integrations/bub/README.md)。

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
