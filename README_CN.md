# PowerContext

为人和 Agent 交接并继续工作而生的上下文。

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

工作很少会由开始它的人或 Agent 独自完成。你把任务交给 Agent，Agent 推进一部分，之后可能由你或其他人接手。推理过程和当前状态却常常留在那段对话里。

PowerContext 让上下文跟随工作，跨越不同的对话。你回来时，可以看到已经发生了什么，并从当前进展继续。新的 Agent 也能从同一处接手。

![你和 Agent 交接工作，并基于已存储的上下文继续推进](docs/assets/readme-workflow.svg)

[官方网站](https://powercontext.oceanbase.io/zh/) · [阅读文档](https://powercontext.oceanbase.io/zh/docs/)

## 从当前进展继续

你接手时，会先看到当前工作需要的上下文：已经确认的决定、约束、进展、证据和下一步。你可以从这里继续，也可以把工作交给其他人或 Agent，不需要重新翻阅全部记录。

你决定哪些信息以后仍然有用，哪些内容需要随任务交给下一位接手者。PowerContext 把长期信息保存为 Memory，把当前目标和状态组织成 Handoff。你可以把能够复用的做法记录为 Experience 或 Skill。PowerContext 将每项内容限定在对应的工作范围内，并保留它的来源和历史版本。

## 开始使用

安装发布版和匹配的 Codex 集成。让 Server 在独立终端中持续运行：

```bash
uv tool install "powercontext[cli,server]==0.2.0"
powercontext server run
```

在另一个终端执行：

```bash
powercontext setup codex --ref powercontext-v0.2.0
```

需要 Python 3.11+。支持 macOS 和 Linux；Windows 为 `experimental`。
当前 `master`、其他宿主和验证步骤见 [Quick Start](docs/zh/docs/get-started/quickstart.md)与
[安装指南](docs/zh/docs/get-started/install-and-run.md)。包和集成应保持相同 ref。

## 集成

| 类型 | 集成 | 标签 |
| --- | --- | --- |
| Agent Host | Codex | `official` |
| Agent Hosts | Claude Code、DeepSeek Harness、Hermes、OpenClaw、OpenCode、Pi、WorkBuddy | `community` |
| Python Agent 框架 | Pydantic AI、LangChain、LangGraph | `community` |
| 评测 | Bub | `evaluation` |

`official` 表示 PowerContext 项目维护，`community` 表示社区贡献，`evaluation` 表示仅用于评测。
标签不代表宿主厂商背书或各集成能力相同。接入见 [Plugins、MCP、Skills 与安装](docs/zh/docs/integrations/index.md)，
`released`、`master_only` 和 `experimental` 状态见[能力矩阵](docs/zh/docs/integrations/capabilities.md)。

## 文档

- [管理上下文](docs/zh/docs/workflows/index.md)：Memory、Handoff、Experience、Skill、Sources、Scope 和 Artifact。
- [部署与运维](docs/zh/docs/operate/index.md)：个人服务、日志、指标、Tracing 与恢复。
- [开发与 API](docs/zh/docs/develop/index.md)：HTTP、Python 与应用接入。
- [基准测试](https://powercontext.oceanbase.io/zh/benchmarks/)：方法、结果与限制。
- [参与贡献](CONTRIBUTING.md)。

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的后继项目。

## 许可证

PowerContext 使用 [Apache License 2.0](LICENSE) 许可证。
