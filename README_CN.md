# PowerContext

为人和 Agent 交接并继续工作而生的上下文。

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

工作很少会由开始它的人或 Agent 独自完成。你把任务交给 Agent，Agent 推进一部分，之后可能由你或其他人接手。推理过程和当前状态却常常留在那段对话里。

PowerContext 让上下文跟随工作，跨越不同的对话。你回来时，可以看到已经发生了什么，并从当前进展继续。新的 Agent 也能从同一处接手。

![你和 Agent 交接工作，并基于已存储的上下文继续推进](docs/assets/readme-workflow.svg)

[网站](https://frf12.github.io/powercontext/zh/) · [完整安装流程](https://frf12.github.io/powercontext/zh/docs/get-started/quickstart/)

PowerContext 1.0.0 RC2 包含交互式配置向导。下面的命令安装这一预发布版本，并接入相同版本的 Agent 集成，供测试使用。

## 从当前进展继续

你接手时，会先看到当前工作需要的上下文：已经确认的决定、约束、进展、证据和下一步。你可以从这里继续，也可以把工作交给其他人或 Agent，不需要重新翻阅全部记录。

你决定哪些信息以后仍然有用，哪些内容需要随任务交给下一位接手者。PowerContext 把长期信息保存为 Memory，把当前目标和状态组织成 Handoff。你可以把能够复用的做法记录为 Experience 或 Skill。PowerContext 将每项内容限定在对应的工作范围内，并保留它的来源和历史版本。

## 安装、配置并接入 Agent

准备 Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和你使用的 Agent CLI。
需要 Python 3.11+，uv 可以按需安装。支持 macOS 和 Linux；Windows 支持为 `experimental`。

安装 1.0.0 RC2，然后在独立目录里打开交互式配置向导：

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
mkdir -p powercontext-config
cd powercontext-config
powercontext config init --language zh --output .env
```

Python 依赖下载缓慢或失败时，可按[镜像重试步骤](https://frf12.github.io/powercontext/zh/docs/get-started/install-and-run/#使用镜像重试依赖下载)重新安装。

向导会依次询问存储、使用场景、记忆能力、Dashboard、模型 API 和 Agent 连接。
要体验自动 Memory 提取和 Topic Memory，请选择**完整记忆能力**并准备独立的 Generation、Embedding API 凭据；
Agent 官方订阅不会自动给 PowerContext Server 提供这些凭据。选择**基础记忆**则可显式保存与召回，无需额外模型 API。

向导生成一个 `.env` 环境文件和 `.env.next-steps.md`。
选择 seekdb 且缺少依赖时，会在确认后后台增量安装。按照最后打印的连接信息，在当前终端启动 Server：

```bash
powercontext server run --env-file .env
```

保持 Server 运行，在另一个终端回到 `powercontext-config` 目录，仅加载客户端配置并检查服务：

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

接着按 `.env.next-steps.md` 创建并绑定 Scope、安装相同版本的插件，再打开新 Agent 会话。
[完整安装流程](https://frf12.github.io/powercontext/zh/docs/get-started/quickstart/)包含 Codex、Claude Code、Dashboard 登录、
SSH 隧道、HTTPS 前提及逐项验收。例如，匹配本版本的 Codex 安装命令是：

```bash
powercontext setup codex --ref powercontext-v1.0.0rc2
powercontext doctor codex
```

`doctor` 通过只代表集成安装就绪。自动记忆验收需要确认：真实输入进入 Source、产生 Topic、相关输入推动主题演进，
并能在使用同一 Scope 的新会话中召回。

Codex 标为 `official`，其他宿主及 Python Agent 框架标为 `community`，Bub 标为 `evaluation`，仅用于评测。
这些标签表示 PowerContext 集成的维护归属和用途，具体功能及可用状态见
[能力矩阵](https://frf12.github.io/powercontext/zh/docs/integrations/capabilities/)。

<table>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/integrations/codex.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/claude-code.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/dsh.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/hermesagent.png?raw=true&size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /></picture><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/pi.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/pi.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120" alt="Pi Coding Agent" width="48" height="48" /></picture><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/openclaw.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/integrations/opencode.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/opencode.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120" alt="OpenCode" width="48" height="48" /></picture><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/langchain.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/langgraph.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langgraph.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120" alt="LangGraph" width="48" height="48" /></picture><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

应用还可以通过异步 Python Client、HTTP API、MCP 或进程内 Core SDK 使用 PowerContext。请参考[接口说明](https://frf12.github.io/powercontext/zh/docs/develop/interfaces/)选择入口。

想用 Python 逐步体验？从 [22 篇 Jupyter 教程与完整团队工作流](examples/jupyter/README.md)开始，亲手运行 Memory、上下文、交接、Experience、Skill 和真实 Agent。前七篇不需要模型或 API Key。

## 使用 PowerContext 后有什么变化

![PowerContext 在 LoCoMo 和 SWE-bench Pro 上的紧凑对比图](docs/assets/readme-benchmark-summary.svg)

这些对比的评测方法、完整结果和适用边界请见[官网评测页](https://frf12.github.io/powercontext/zh/benchmarks/)。

## 参与构建 PowerContext

```bash
make install
make check
make test
```

完整开发流程请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 进一步了解

- [开始使用](https://frf12.github.io/powercontext/zh/docs/get-started/quickstart/)
- [接入 Agent](https://frf12.github.io/powercontext/zh/docs/integrations/)
- [管理上下文](https://frf12.github.io/powercontext/zh/docs/workflows/)
- [部署与运维](https://frf12.github.io/powercontext/zh/docs/operate/)
- [开发与 API](https://frf12.github.io/powercontext/zh/docs/develop/)

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的后续项目。

## 许可证

PowerContext 基于 [Apache License 2.0](LICENSE) 发布。
