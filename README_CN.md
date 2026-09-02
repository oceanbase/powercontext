# PowerContext

为人和 Agent 交接并继续工作而生的上下文。

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

工作很少会由开始它的人或 Agent 独自完成。你把任务交给 Agent，Agent 推进一部分，之后可能由你或其他人接手。推理过程和当前状态却常常留在那段对话里。

PowerContext 让上下文始终跟随你的工作。它保存发生了什么、为什么这样判断、现在进展到哪里，以及下一步是什么。工作交接时，你或下一个 Agent 可以理解现状并继续推进。

![你和 Agent 交接工作，并基于已存储的上下文继续推进](docs/assets/readme-workflow.svg)

[官方网站](https://powercontext.oceanbase.io/zh/) · [阅读文档](https://powercontext.oceanbase.io/zh/docs/)

## 随工作流转的上下文

上下文不会随某次对话或某个 Agent 消失。它始终限定在对应工作范围内，并与来源保持关联。条目可以随着工作变化而修订或停用，同时保留历史。PowerContext 通过 Memory、Experience、Skills 和 Handoffs 维护这些上下文。

## 与你使用的 Agent 一起工作

安装[正式发布的版本](https://pypi.org/project/powercontext/)：

```bash
uv tool install "powercontext[cli,server]==0.1.0"

# To use the latest unreleased code instead:
# uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

在单独的终端中启动本地 Server：

```bash
powercontext server run
```

Server 默认将上下文保存到本地 SQLite 数据库。

然后为 Agent 配置集成。例如：

```bash
powercontext setup codex --ref v0.1.0  # --ref also accepts a Git commit, such as 55616dca.
```

其他 Agent 的配置方式和部署选项请继续阅读 [Agent 配置指南](https://powercontext.oceanbase.io/zh/docs/tutorials/agent-quickstart/)。支持的 Agent Client 和 IDE 可通过 MCP 或专用集成连接。

<table>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/cursor_dark.svg"><img src="https://svgl.app/library/cursor_light.svg" alt="Cursor" width="48" height="48" /></picture></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Cursor</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://svgl.app/library/vscode.svg" alt="VS Code" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>VS Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/tutorials/codex-quickstart.md"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /></a><br /><a href="docs/zh/docs/tutorials/codex-quickstart.md"><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/windsurf-dark.svg"><img src="https://svgl.app/library/windsurf-light.svg" alt="Windsurf" width="48" height="48" /></picture></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Windsurf</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.githubassets.com/images/modules/site/copilot/copilot.png" alt="GitHub Copilot" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>GitHub Copilot</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.com/QoderAI.png?size=120" alt="Qoder" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Qoder</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://svgl.app/library/opencode-dark.svg"><img src="https://svgl.app/library/opencode.svg" alt="OpenCode" width="48" height="48" /></picture></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.com/openclaw.png?size=120" alt="OpenClaw" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>OpenClaw</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.com/anthropics.png?size=120" alt="Claude Desktop" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Claude Desktop</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/reference/interfaces.md"><img src="https://github.com/cline.png?size=120" alt="Cline" width="48" height="48" /></a><br /><a href="docs/zh/docs/reference/interfaces.md"><sub><b>Cline</b></sub></a></td>
<td></td>
</tr>
</table>

应用还可以通过异步 Python Client、HTTP API、MCP 或进程内 Core SDK 使用 PowerContext。请参考[接口说明](https://powercontext.oceanbase.io/zh/docs/reference/interfaces/)选择入口。

## 使用 PowerContext 后有什么变化

![PowerContext 在 LoCoMo 和 SWE-bench Pro 上的紧凑对比图](docs/assets/readme-benchmark-summary.svg)

这些对比的评测方法、完整结果和适用边界请见[官网评测页](https://powercontext.oceanbase.io/zh/benchmarks/)。

## 参与构建 PowerContext

```bash
make install
make check
make test
```

完整开发流程请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 进一步了解

- [核心概念](https://powercontext.oceanbase.io/zh/docs/explanation/core-concepts/)
- [理解 Memory 和 Handoff](https://powercontext.oceanbase.io/zh/docs/explanation/memory-and-handoff/)
- [理解 Experience 与 Skill 生命周期](https://powercontext.oceanbase.io/zh/docs/explanation/experience-and-skill-lifecycle/)

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的后续项目。

## 许可证

PowerContext 基于 [Apache License 2.0](LICENSE) 发布。
