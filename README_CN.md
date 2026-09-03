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

## 与你使用的 Agent 一起工作

安装 [PowerContext](https://pypi.org/project/powercontext/)：

```bash
uv tool install "powercontext[cli,server]==0.1.0"

# To use the latest unreleased code instead:
# uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

如需让个人 Server 在终端关闭后继续运行，并可以在下次登录后再次启动，请安装原生当前用户服务：

```bash
powercontext service install # 卸载请运行 `powercontext service uninstall`
powercontext service status
```

在 Windows 上，未提供登录启动选项时，安装器会询问是否在下次登录时自动启动；直接按 Enter 的默认选择是不启用。
需要显式选择时，请使用 `--start-on-login` 或 `--no-start-on-login`。

如果想在终端以前台方式使用，可以运行：

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
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-codex.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-claude-code.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-dsh.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/hermesagent.png?raw=true&size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /></picture><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-pi.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/pi.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120" alt="Pi Coding Agent" width="48" height="48" /></picture><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-openclaw.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-opencode.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/opencode.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120" alt="OpenCode" width="48" height="48" /></picture><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-langchain.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/how-to/configure-langgraph.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langgraph.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120" alt="LangGraph" width="48" height="48" /></picture><br /><sub><b>LangGraph</b></sub></a></td>
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
