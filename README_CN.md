# PowerContext

**让上下文跟着工作走 —— 跨越会话、跨 Agent、跨人。**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)
[![Ask DeepWiki](https://img.shields.io/badge/Ask-DeepWiki-147DFF)](https://deepwiki.com/oceanbase/powercontext)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

[网站](https://powercontext.oceanbase.io/zh/) · [快速开始](https://powercontext.oceanbase.io/zh/docs/get-started/quickstart/) · [接入 Agent](https://powercontext.oceanbase.io/zh/docs/integrations/) · [评测](https://powercontext.oceanbase.io/zh/benchmarks/) · [接口](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/)

## 快速开始

PowerContext 把可长期复用的项目上下文放在单次对话之外。一次完整体验可以概括为：

```text
Agent 输入 → Source 证据 → Memory / Topic Memory → PreparedContext → 新会话或新 Agent
```

PowerContext 1.3.0 包含交互式配置向导。需要 Python 3.11+、Git 和
[uv](https://docs.astral.sh/uv/getting-started/installation/)；需要时 uv 可以按需提供 Python。
要接入 Agent，还需要安装对应的 CLI。向导中选择“基础记忆”即可在没有模型 API 的情况下显式保存与召回；
选择“完整记忆能力”则需要独立的 Generation、Embedding API 凭据。只想启动不依赖模型 API 的本地 Server 时，
请参考[安装与运行](docs/zh/docs/get-started/install-and-run.md)。
支持 macOS 和 Linux；Windows 支持为 `experimental`。

请选择与你目标匹配的路径：

| 目标 | 选择 | 需要 |
| --- | --- | --- |
| 体验显式保存与召回 | **基础记忆** | CLI 和 Server；无需模型 API |
| 自动处理 Source，使用 Topic Memory 或语义检索 | **完整记忆能力** | 独立的 Generation、Embedding API 凭据；仍受调度和 Scope 策略影响 |
| 用 Python 学习核心工作流 | 前七篇 Jupyter 教程 | 仓库 checkout 和 `uv`；不需要模型或 API Key |

### 1. 安装并运行配置向导

```bash
uv tool install --force "powercontext[cli,server]==1.3.0"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language zh --output .env
powercontext config validate --env-file .env
powercontext server run --env-file .env      # 保持这个终端不关
```

向导会询问存储、本机或远程使用场景、记忆能力、Dashboard/访问控制和 Agent 连接。首次体验可以使用 SQLite；只有确实需要时再选择嵌入式 seekdb，并同意依赖安装。

向导生成一个 `.env` 环境文件和 `.env.next-steps.md`。这些文件可能包含凭据，请勿提交到 Git。保持 Server 运行，在另一个终端回到 `powercontext-config` 目录，重新加载生成的环境文件并检查服务：

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

接着按 `.env.next-steps.md` 创建并绑定 Scope。使用创建请求返回的真实 `scope_id`；向导规划的 `codex-...` 标题不是 ID。
切换目录不会创建隔离；Dashboard 和 Agent 要使用同一个 Server 和 Scope。然后安装相同版本的插件，再打开新 Agent 会话。
[完整安装流程](https://powercontext.oceanbase.io/zh/docs/get-started/quickstart/)包含 Codex、Claude Code、Dashboard 登录、SSH 隧道、HTTPS 前提及逐项验收。例如，匹配本版本的 Codex 安装命令是：

```bash
powercontext setup codex --ref powercontext-v1.3.0
powercontext doctor codex
```

### 2. 验收第一条端到端结果

这一步检查 PowerContext 真正有用的链路：Source 采集 → 后台处理 → 跨会话召回。
在绑定了同一 Scope 的新 Agent 会话中，发送一条具体决定，例如：

```text
我们正在设计 Aurora 验收项目。决定用 uv 管理 Python 依赖，配置文件使用 .env。
第一阶段只在一台 Linux 服务器运行，Mac 通过 SSH 隧道访问。请复述这些约束，暂时不要修改代码。
```

如果开启了 Dashboard，打开向导打印的地址；采集前页面为空是正常现象。然后：

1. 确认这条输入出现在 Source 中。
2. 等待配置的检查周期和模型处理，确认出现相关 Topic Memory。
3. 发送一条相关更新，确认新的 Source 入库，且主题内容或修订记录反映了更新。
4. 使用同一 Scope 开启新的 Agent 会话，要求它带引用找回 Aurora 的决定。

`doctor codex` 只检查安装与连接。Source 采集本身不会创建 Memory 或 Topic Memory，后台处理还要等待配置的调度和模型运行。
基础记忆模式下，可以显式要求 Agent 保存一条决定，再在新会话中搜索；这不能验收自动提取。

### 3. 先用 Python 体验

教程需要在仓库 checkout 内运行，依赖由 `uv` 的依赖组提供，不在 `powercontext` 包内。在仓库根目录执行：

```bash
uv sync --locked --group notebooks
uv run --locked --group notebooks jupyter lab --notebook-dir=examples/jupyter
```

先从 [01 · 让新会话用上项目约定](examples/jupyter/01_memory_across_sessions.ipynb)开始，再通过
[22 篇中文 Jupyter 教程与完整团队工作流](examples/jupyter/README.md)亲手运行 Memory、上下文、交接、Experience、Skill
和真实 Agent。前七篇不需要模型或 API Key。

### 常见问题

- `doctor codex` 只验证 Codex 集成的安装和连接，不代表自动记忆流程已通过。要验收自动记忆，需要确认真实输入进入 Source、产生 Topic、相关输入推动主题演进，并能在使用同一 Scope 的新会话中召回。
- Python 依赖下载缓慢或失败时，可先为 [uv](https://docs.astral.sh/uv/configuration/indexes/) 配置包索引（镜像源）再重试。
- 要体验自动 Memory 提取和 Topic Memory，请选择**完整记忆能力**并准备独立的 Generation、Embedding API 凭据；Agent 官方订阅不会自动给 PowerContext Server 提供这些凭据。选择**基础记忆**则可显式保存与召回，无需额外模型 API。
- 选择 seekdb 且缺少依赖时，会在确认后后台增量安装。
- 可选的召回充分性门控会按 Scope 记录每日搜索工作量和字节预算遗漏，供后续策略评估使用。记录范围与失败行为见[配置说明](docs/zh/docs/operate/configuration.md)。
- Server 部署在另一台机器上时，请使用 HTTPS 或参考[远程连接说明](docs/zh/docs/operate/connect-remote-server.md)。配置向导会从参数或环境变量识别远端 HTTP 地址并要求显式确认；自动化场景使用 `--allow-insecure-http`。

## PowerContext 是什么？

**PowerContext 是一个面向 AI Agent 的开源上下文服务。** 它把工作里值得长期保留的部分——已经确认的决定、约束、证据、进展——从某一次对话里搬出来，这样下一个会话、下一个 Agent、或者下一个接手的人，都能从当前进展继续，而不必重读全部记录。

可以把 PowerContext 理解为三层，也可以直接使用完整 Server 或把同一套契约嵌入其他应用：

| 层 | 负责什么 | 适合什么时候使用 |
| --- | --- | --- |
| Core protocol | `Source`、`Artifact`、`Trigger` 抽象契约 | 嵌入 PowerContext 或扩展组件 |
| Builtin runtime | Memory、Handoff、Experience、Skill 服务，以及调度、推理和受支持的存储后端 | 运行标准本地 Server |
| Server 与 integrations | HTTP/MCP、Python Client、CLI、Dashboard 和 Agent 插件 | 接入 Agent 或应用 |

工作很少会由开始它的人或 Agent 独自完成。你把任务交给 Agent，Agent 推进一部分，之后可能由你或其他人接手。推理过程和当前状态却常常留在那段对话里。PowerContext 把它们从对话里移出来，变成可以查看、带版本号的 Artifact：

![你和 Agent 交接工作，并基于已存储的上下文继续推进](docs/assets/readme-workflow.svg)

每一项内容都属于一个 **Scope**——一条独立的 Source 日志、一套 Memory 生命周期、一个评审收件箱和一份 Handoff 历史。Scope ID 只用于选取数据，它不证明用户身份、不授予工具访问权限，也不授权执行。

```text
Scope
├── Source journal    # 证据：输入、文档、工作契约、任务结果
├── Memory            # atomic-memory/ARTIFACT_ID@REVISION —— 决定、约束、下一步
├── Review Inbox      # 等待评审的 Experience 与 Skill 候选
├── Handoff history   # handoff/ARTIFACT_ID@REVISION —— 跨会话的工作接续
└── Topic Memory      # 由符合条件的 Source 后台提取
```

*下面两张 Dashboard 截图使用的是合成演示记录。*

### Memory —— 活得比会话更久的决定与约束

Memory 是一个带版本的 Artifact 家族，存放可以复用的决定、约束、事实、状态和下一步。条目状态为 `active`、
`forgotten`、`merged` 或 `retired`。通过 `POST /v1/atomic-memory/lifecycle` 遗忘一条，会把它移出主动召回，但内容
和历史仍然可用、可恢复，且不会推进内容 revision。显式写入 Memory 完全不需要模型；从已捕获的 Source 中提取，
才需要配置生成流水线。

> "别重构老的 auth 模块 —— 移动端还在用。"
> 这么贵的上下文，不应该依赖有人每个会话都重讲一遍。

![Dashboard 展示一个 Scope 中活跃的决定和约束，以及对应的 Artifact 与 Source 引用](docs/assets/readme-dashboard-memories-zh.png)

### Handoff —— 当前状态，不是整段对话记录

Handoff 用一个四步循环记录工作接续：

```text
工作契约 → Prepared Handoff → 确认回执 → 任务结果
```

`handoff_current_work` 抓取一个已经检查过的边界，返回一份临时的 Prepared Handoff。只有在你希望留下里程碑时，提交它才会生成持久的 Revision。接收方解析这份 Handoff 并记录确认回执；任务结果则作为 Source 证据保存最终状态与检查项。[Handoff Report](https://powercontext.oceanbase.io/zh/docs/workflows/use-handoff-report/) 会把每个选定 Scope 中最新的一份 Handoff Revision 投影出来供查看与导出——只读，且从不改写 Memory。

![Dashboard 展示一份已提交的 Handoff Revision，其中包含已记录的进展、下一步和关联的 Source 证据](docs/assets/readme-dashboard-handoff-zh.png)

### Experience —— 有用的做法，评审后再复用

Experience 记录一个场景、一次动作、观察到的结果，以及可复用的经验教训。它以 `pending` 候选的身份进入 Scope 内的评审收件箱。评审写入使用 `expected_version`，因此在别的写入方改动了提案之后，决策不会静默生效。通过会写入一个不可变的 Revision；驳回则记录原因。两者都是终态，并且只有已通过的当前版本参与召回——待审、被驳回和历史的 Revision 都会被排除。

### Skill —— 经过评审的流程，需要显式导出

一个受管的 Skill 包含名称、发现描述、指令、校验项和血缘。评审通过**不等于**安装或执行：必须先显式导出某一个明确的已通过 Revision，Agent 宿主才能发现对应的宿主内副本。

## 为什么选 PowerContext？

- **上下文活在对话之外。** Memory、Handoff、Experience、Skill 都是服务端 Artifact，不是某个 Agent 家目录里的文件。从 Codex 换到 Claude Code，或者把工作交给同事，工作本身跟着你走。
- **一切都带版本、可追溯。** 每个 Artifact 都以 `家族/ARTIFACT_ID@REVISION` 寻址。通过替换产生的后续 Revision 不会改变 Artifact ID，读取一个精确的引用会返回那一份历史快照，即使家族主版本已经前进。
- **生成、评审、执行是三个分开的步骤。** 模型可以提出内容，评审通过某个 Revision，导出生成宿主内副本。这三步都不会授予 Agent 执行指令的权限。各个入口目前各自暴露了什么，见[接口说明](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/)。
- **召回是有边界的，不是整包倾倒。** `PreparedContext` 会按请求选择活跃的 Memory 和已通过的 Experience，并可选加入 Profile 快照和 Topic Memory，再套用一个共享的字节预算，返回 `ready` 内容或 `empty`。召回的内容是历史，不是指令权威：接收方仍然要遵循当前指令，并检查工作区的实时状态。
- **复用之前先评审。** Experience 和 Skill 都要经过一次评审决策，无论通过还是驳回都是终态；批准会写入不可变 Revision。启用权限控制后，批准需要 `scope.review`。有审核权限的 Agent 仍可批准自己的 Candidate；需要由人决定时，应使用不同身份和权限[分离 Agent 提交与审核](https://powercontext.oceanbase.io/zh/docs/workflows/review-candidates/#分离-agent-提交与审核)。
- **一个 Server，同一份契约的多个视图。** HTTP 是完整的远端应用契约。Python Client 提供带类型的访问，MCP 是面向 Agent 的精选投影，Core 协议则允许应用自行组装 Source、Artifact 和 Trigger 实现。

### 横向对比

以下按常见的单 Agent 或宿主本地部署方式比较；具体能力取决于各系统的部署与集成方式。

| | 手动翻聊天记录 | 对聊天记录做 RAG | 宿主本地记忆文件（如 CLAUDE.md） | PowerContext |
|---|---|---|---|---|
| 跨会话留存 | 手动 | 是 | 是 | 是 |
| 换 Agent 后仍可用 | 否 | 否 | 否 | 是 |
| 把工作交给另一个人 | 手动 | 手动 | 手动 | Handoff，一等公民 |
| 可复用的流程 | 无 | 无 | 仅自然语言描述 | Experience / Skill |
| 保留历史版本 | 否 | 取决于索引 | 仅通过 git | 不可变 Revision |
| 复用前经过评审 | 不适用 | 否 | 否 | 评审收件箱 |
| 单轮召回有预算约束 | 否 | 部分 | 否 | `PreparedContext` 字节预算 |
| 可以在 Agent 之外查看 | 否 | 否 | 是 | 是 |

## 效果怎么样

![PowerContext 在 LoCoMo 和 SWE-bench Pro 上的紧凑对比图](docs/assets/readme-benchmark-summary.svg)

- **长对话记忆（LoCoMo；类别 1–4 的 1,540 个计分问题）：准确率 90.78%**，PowerMem 为 87.79%，全量上下文
  重放为 52.9% —— 相对后者 **+37.88 个百分点**。
- **编码任务（SWE-bench Pro；731 个公开 v2 任务的配对项目运行，非官方提交）：开启 PowerContext 后任务解决率
  86.73%，关闭时 82.35%** —— **+4.38 个百分点**，多解出 32 题。
- **召回质量（LoCoMo Plus）**：原生 PowerContext 对比检索后用 Jev 做候选筛选。每组模型同时用于 Memory 抽取与回答，向量模型为 `qwen3.7-text-embedding`（1024 维）：

  | 模型 | PowerContext | PowerContext + Jev | 差值 |
  |---|---|---|---|
  | GPT-4o-mini | 40.855% | 49.78% | +8.925 个百分点 |
  | Qwen3.7-plus | 60.37% | 68.947% | +8.577 个百分点 |
  | GPT-4o | 65.082% | 69.28% | +4.198 个百分点 |

评测方法、完整结果和适用边界：[官网评测页](https://powercontext.oceanbase.io/zh/benchmarks/)。有两点我们宁愿写明而不是藏起来：上面这六组 LoCoMo Plus 分数由项目方提供，运行产物尚未公开供复核——公开的是评测工具的复现步骤，不代表这些分数本身已被公开验证。Windows 支持为 `experimental`。

## 接入你的 Agent

Codex 标为 `official`，其他受维护的宿主及 Python Agent 框架标为 `community`，Bub 标为 `evaluation`，仅用于评测。这些标签表示 PowerContext 集成的维护归属和用途；哪些宿主已通过资格验证、各自支持哪些功能，以[能力矩阵](https://powercontext.oceanbase.io/zh/docs/integrations/capabilities/)为准。

<table>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/integrations/codex.md"><img src="assets/codex.png" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/claude-code.md"><img src="assets/claude-code.png" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="integrations/minimax/README.md"><img src="assets/minimax-code.png" alt="MiniMax Code" width="48" height="48" /><br /><sub><b>MiniMax Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/dsh.md"><img src="assets/deepseek.png" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/zcode.md"><img src="assets/zcode.png" alt="ZCode" width="48" height="48" /><br /><sub><b>ZCode</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/kimi-code.md"><img src="https://github.com/MoonshotAI.png?size=120" alt="Kimi Code" width="48" height="48" /><br /><sub><b>Kimi Code</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/hermes-dark.png"><img src="assets/hermes.png" alt="Hermes Agent" width="48" height="48" /></picture><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/pi.md"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/pi-dark.png"><img src="assets/pi.png" alt="Pi Coding Agent" width="48" height="48" /></picture><br /><sub><b>Pi Coding Agent</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/zh/docs/integrations/openclaw.md"><img src="assets/openclaw.png" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/opencode.md"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/opencode-dark.png"><img src="assets/opencode.png" alt="OpenCode" width="48" height="48" /></picture><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/langchain.md"><img src="assets/langchain.png" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/zh/docs/integrations/langgraph.md"><picture><source media="(prefers-color-scheme: dark)" srcset="assets/langgraph-dark.png"><img src="assets/langgraph.png" alt="LangGraph" width="48" height="48" /></picture><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

应用还可以通过异步 Python Client、HTTP API、MCP 或进程内 Core SDK 使用 PowerContext。请参考[接口说明](https://powercontext.oceanbase.io/zh/docs/develop/interfaces/)选择入口。

## 参与构建 PowerContext

```bash
make install
make check
make test
```

完整开发流程请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
编程能力评测、记忆质量评测、性能压测和 Skill 回归统一放在 [`evaluation/`](evaluation/README.md)，按用途选择对应目录。

## 进一步了解

- [开始使用](https://powercontext.oceanbase.io/zh/docs/get-started/quickstart/)
- [接入 Agent](https://powercontext.oceanbase.io/zh/docs/integrations/)
- [管理上下文](https://powercontext.oceanbase.io/zh/docs/workflows/)
- [部署与运维](https://powercontext.oceanbase.io/zh/docs/operate/)
- [开发与 API](https://powercontext.oceanbase.io/zh/docs/develop/)

PowerContext 是 [PowerMem](https://www.powermem.ai/) 的后续项目。

## 贡献者

感谢每一位为 PowerContext 作出贡献的伙伴。❤️

<a href="https://github.com/oceanbase/powercontext/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=oceanbase/powercontext&amp;max=100&amp;columns=12" alt="PowerContext 贡献者" />
</a>

[查看全部贡献者](https://github.com/oceanbase/powercontext/graphs/contributors) ·
[参与贡献](CONTRIBUTING.md)

## 许可证

PowerContext 基于 [Apache License 2.0](LICENSE) 发布。
