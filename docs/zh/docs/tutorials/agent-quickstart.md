---
title: Agent 分步入门
description: 选择一个受支持的 Agent，安装 PowerContext，并跑通跨会话 Memory 与可选的跨 Agent Handoff。
---

# Agent 分步入门

本教程面向第一次使用 PowerContext 的 Agent 用户。你可以使用 Codex、Claude Code、DeepSeek Harness、
OpenClaw、OpenCode、Pi、Hermes 或 WorkBuddy，也可以把通用 Agent Plugin 加载到支持 Skill 与 MCP 的 Host。

如果你已经有自己的 AI 应用，并不使用这些 Agent Host，请改用
[HTTP API 生命周期教程](api-quickstart.md)，直接跑通第一个受治理的上下文闭环。

完成后，你会跑通下面的公共闭环：

```text
安装 Server → 选择 Agent → 验证集成 → 保存 Memory → 换会话恢复 → 按 Host 能力交接
```

公共步骤使用本地 SQLite，不要求配置 generation model。显式 Memory 与已有的 Handoff 操作可以直接使用；从
Source 自动抽取 Memory、向量搜索和模型生成能力需要另行配置 provider。

不同 Agent 的集成表面并不完全相同。本教程会明确区分：

- 自动准备上下文与显式 Memory 工具；
- 一句话 Handoff 与多步骤 Handoff；
- 只有 Memory、暂时没有完整 Handoff UI 的集成；
- 交互式 Agent Host 与需要写代码接入的 Python Agent 应用。

## 1. 选择你的 Agent 路线

先根据正在使用的 Host 选择一行。setup 命令只安装 PowerContext 集成，不会替你安装 Agent 本身。

| Agent Host | 安装集成 | 启动或激活 | Memory 与自动恢复 | Handoff 路径 |
| --- | --- | --- | --- | --- |
| Codex | `powercontext setup codex` | `codex` | Prompt Hook + MCP Memory | `交接` 可在一轮中提交 durable Handoff |
| Claude Code | `powercontext setup claude-code` | `claude` | Prompt Hook + MCP Memory | `handoff this work` 可在一轮中提交 durable Handoff |
| DeepSeek Harness | `powercontext setup dsh` | `dsh web` | 每个 model step 准备上下文 + `pc_*` Memory tools | `pc_capture_source`、activate、finalize、commit、continue |
| OpenClaw | `powercontext setup openclaw` | `openclaw` | before-prompt recall + 五个 `powercontext_memory_*` tools | 当前集成只提供 Memory，不提供完整 Handoff UI |
| OpenCode | `powercontext setup opencode` | `opencode` | 每个正常 turn 准备上下文 + `pc_*` tools | capture、activate、finalize、commit、continue |
| Pi | `powercontext setup pi` | `pi` | 每个 prompt 准备上下文 + `pc_*` tools | capture、activate、finalize、commit、continue |
| Hermes | `powercontext setup hermes` | 先运行 `hermes memory setup`，再启动 Hermes | MemoryProvider + `/pc` companion | `/pc` 与 provider operations 提供 Handoff 生命周期 |
| WorkBuddy | `powercontext setup workbuddy` | 重启 WorkBuddy | Prompt Hook + MCP Memory | `交接` 可在一轮中提交 durable Handoff |
| 兼容 Agent Plugin 的 Host | 手动加载 Agent Plugin 目录 | 按 Host 方式重新加载 | 显式 MCP Memory；没有通用 Prompt Hook | 通用 `project-context` Skill + MCP Handoff |

如果你开发的是 Pydantic AI、LangChain、LangGraph 或 Bub 应用，请先完成 Server 安装与检查，再跳到
[Python Agent 应用路线](#14-python-agent)。这些适配器需要在应用代码中接入，不应伪装成交互式 Host 的 setup
命令。

## 2. 检查公共环境

需要 macOS 或 Linux，以及下面的公共工具：

| 工具 | 要求 | 检查命令 |
| --- | --- | --- |
| Python | 3.11 或更新版本 | `python3 --version` |
| Git | 能读取 PowerContext Git 仓库 | `git --version` |
| uv | 能使用 `uv tool` | `uv --version` |
| 选定的 Agent | 已安装、已完成登录、位于 `PATH` | 运行该 Host 的 `--version` 或诊断命令 |

前三条命令都应输出版本号。还要确认本机现有 Git 凭据能够读取
`https://github.com/oceanbase/powercontext.git`。

准备两个终端：

- **终端 A**：持续运行 PowerContext Server；
- **终端 B**：安装、诊断、进入项目，并启动选定的 Agent。

不要把密码、访问令牌、私钥、连接串或其他敏感信息写入 Memory、Source 或 Handoff。

## 3. 安装 PowerContext CLI 和 Server

在**终端 B**运行：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

该命令创建隔离的应用环境，不会在当前目录留下 PowerContext 仓库副本。`--force` 会按当前 `master` 指向的
commit 刷新已有安装。

确认 CLI 可用：

```bash
powercontext --version
powercontext --help
```

**成功标准：** 第一条命令输出版本号；第二条显示 `server`、`setup`、`doctor` 等命令。

## 4. 安装一个或多个 Agent 集成

### 安装一个 Host

从下面选择一个命令，并让 `--ref` 与 PowerContext 工具使用同一个 revision：

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup claude-code --source oceanbase/powercontext --ref master
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext setup openclaw --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext setup pi --source oceanbase/powercontext --ref master
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext setup workbuddy --source oceanbase/powercontext --ref master
```

只运行你已安装的 Host 对应命令。每个 setup 都会执行该集成的安装后诊断；失败时先处理当前 Host 的前置条件，
不要假设安装已经完成。

Hermes 还需要选择 MemoryProvider：

```bash
hermes memory setup
```

在向导中选择 `PowerContext`，完成后重启 Hermes。

### 一次安装多个一级 Host

Codex、Claude Code、DeepSeek Harness、OpenClaw、OpenCode、Pi 和 Hermes 属于 `setup select` 一级目录。例如：

```bash
powercontext setup select \
  --host claude-code \
  --host dsh \
  --host opencode \
  --source oceanbase/powercontext \
  --ref master
```

每个 Host 会独立报告 `installed`、`failed` 或 `skipped`。一个 Host 失败不会被其他成功结果掩盖。WorkBuddy
不在该目录中，需要单独运行 `powercontext setup workbuddy`。

### 使用通用 Agent Plugin

如果 Host 能加载 Agent Plugin Skill 与 MCP 配置，但没有专属 setup 命令，请按照
[配置 Agent Plugin](../how-to/configure-agent-plugin.md)加载
`integrations/agent-plugin/powercontext/`。这个 package 提供 `project-context` Skill 和指向
`http://127.0.0.1:8000/mcp` 的配置，但不会启动 Server，也没有跨 Host 通用的 Prompt Hook。

## 5. 启动并检查 Server

在**终端 A**运行并保持进程：

```bash
powercontext server run
```

默认 Server：

- 监听 `http://127.0.0.1:8000`；
- 在 `/` 提供 Dashboard；
- 在 `/mcp` 提供 Streamable HTTP MCP；
- 使用 PowerContext 用户数据目录中的持久化 SQLite 数据库。

回到**终端 B**运行：

```bash
powercontext doctor
powercontext ready
powercontext capabilities
powercontext doctor integrations
```

**成功标准：**

- `doctor` 的 package、Server liveness 和 Server readiness 为 `ok`；
- `ready` 与 `capabilities` 能读取当前服务；
- `doctor integrations` 中已安装 Host 的 CLI 和 integration 项为 `ok`；
- 未安装的 Host 可以显示 missing，不会让这个只读总览失败。

WorkBuddy 不在一级总览中，请单独运行 `powercontext doctor workbuddy`。也可以按选定 Host 运行
`powercontext doctor codex`、`doctor claude-code`、`doctor dsh`、`doctor openclaw`、`doctor opencode`、
`doctor pi` 或 `doctor hermes`。

## 6. 创建一个安全的示例项目

在**终端 B**创建一个不含真实业务数据的 Git 项目：

```bash
mkdir powercontext-agent-quickstart
cd powercontext-agent-quickstart
git init
printf '# Parser example\n\nThis project will parse TOML configuration.\n' > README.md
git add README.md
git -c user.name="PowerContext Tutorial" -c user.email="tutorial@localhost" commit -m "chore: initialize tutorial"
git status --short
```

最后一条命令应没有输出。commit 的身份只用于这一次提交，不会修改全局 Git 配置。

后续每个会话都应从这个同一目录启动。多数专属集成会从 Git remote 或项目路径推导稳定 scope；如果显式配置了
scope，则同一条流程中的 Memory 与 Handoff 调用必须始终复用这个 exact `scope_id`。

## 7. 启动 Agent 并检查集成表面

从示例项目目录启动所选 Host：

```bash
codex       # Codex
claude      # Claude Code
dsh web     # DeepSeek Harness
openclaw    # OpenClaw
opencode    # OpenCode
pi          # Pi
hermes      # Hermes，已完成 memory setup 后
```

WorkBuddy 用户应在这个项目中打开或创建任务，并在安装后重启 Host。通用 Agent Plugin 用户应重新加载对应 Host，
确认 `project-context` Skill 与 `powercontext` MCP Server 都可见。

先做只读检查：

> 检查当前项目目录、Git 状态和可用的 PowerContext 集成能力。报告当前 scope 或 scope 来源，并列出 Memory
> 读取工具；不要修改文件，也不要写入 PowerContext。

不同 Host 的工具名会不同：

| 集成 | 应能看到的 Memory 表面 |
| --- | --- |
| Codex、Claude Code、WorkBuddy、Agent Plugin | MCP 的 `search_memory`、`list_memory_entries`、`get_memory_entry` 等 |
| DSH、OpenCode、Pi | `pc_search`、`pc_memory_list`、`pc_memory_get`、`pc_remember` 等 |
| OpenClaw | `powercontext_memory_search`、`get`、`store`、`revise`、`retire` |
| Hermes | MemoryProvider tools，以及 `/pc`、`/powercontext` 或 `hermes powercontext ...` |

如果工具不存在，先退出 Host，重新运行该 Host 的 setup 和 doctor，再开启新会话。不要在集成未加载时继续并把模型的
普通回答误当成 PowerContext 结果。

## 8. 保存并读取显式 Memory

在 Agent 会话中输入：

> 使用这个 Host 提供的 PowerContext 显式 Memory 工具，分别保存三条项目 Memory：
>
> 1. decision：解析器使用 Python 3.11 标准库 `tomllib`；
> 2. constraint：错误摘要不得包含原始配置中的密钥值；
> 3. next-step：增加 malformed TOML 输入用例。
>
> 写入后搜索或列出这些 active Memory，并返回每一条的 citation。不要保存任何密钥或凭据。

DSH、OpenCode 和 Pi 应调用 `pc_remember`；OpenClaw 应调用 `powercontext_memory_store`；MCP 集成应调用
`remember_memory`。这些是 durable mutation，Host 要求确认时应先检查内容再批准。

Hermes 也可以用确定性的 CLI 路径验证单条写入和搜索：

```bash
hermes powercontext remember decision "The parser uses Python 3.11 tomllib"
hermes powercontext search "Python parser"
```

**成功标准：** Agent 或 Hermes CLI 明确报告写入成功，并返回当前 scope 中的内容与精确 citation。显式 Memory
不需要 generation model；Prompt 或 turn capture 只会生成 Source，不等于已经创建 Memory。

## 9. 在同一 Host 的新会话中恢复

退出 Agent 会话，不要停止 Server。确认终端 B 仍位于示例项目目录，再启动同一个 Host。输入：

> 使用 PowerContext 搜索当前项目中关于 `tomllib` 和 malformed TOML 的 active Memory。返回内容、kind 和
> citation；不要修改条目。

**成功标准：** 新会话能恢复上一步保存的三条 Memory。这证明数据来自稳定 scope 和 Server 数据库，而不是上一段
聊天历史。

如果返回空结果，按顺序检查：

1. 两次会话是否从同一个项目目录启动；
2. `powercontext doctor` 和 Host 专属 doctor 是否为 `ok`；
3. Host 是否使用了不同的 profile、agent identity 或显式 scope；
4. OpenClaw 是否仍使用默认 `agent` scope，而你期望的是 project scope。

OpenClaw 需要跨 Agent 使用项目 Memory 时，应重新配置并确认 Host 提供可信 project identity：

```bash
powercontext setup openclaw --scope-mode project
```

不要把 scope 当成权限边界。远程或多用户 Server 仍需要独立配置鉴权和访问控制。

## 10. 修订和停用 Memory

在当前 Agent 会话中输入：

> 先读取当前 Memory 的 exact citation，然后把 next-step 修订为“记录 malformed TOML 的行号和安全错误摘要”。
> 再停用原 constraint，reason 使用“由统一日志脱敏规范替代”。最后重新列出 active Memory，并说明旧 Revision
> 是否仍可审计。

对应工具为：

- MCP：`get_memory_entry`、`revise_memory_entry`、`retire_memory_entry`；
- DSH、OpenCode、Pi：`pc_memory_get`、`pc_memory_revise`、`pc_memory_retire`；
- OpenClaw：`powercontext_memory_get`、`powercontext_memory_revise`、`powercontext_memory_retire`；
- Hermes：provider tools 或 `/pc` 对应命令。

**成功标准：** active 结果包含新 next-step，不再包含已停用 constraint；旧 Revision 被保留，而不是被覆盖或删除。

## 11. 按 Host 能力交接工作

Handoff 用于转交完整任务状态，不应由几条 Memory 代替。不同 Host 应走不同路径。

### 一句话 durable Handoff

Codex、Claude Code 和 WorkBuddy 的 `project-context` Skill 支持明确的命令式请求：

> 交接

Skill 会检查目标、branch、worktree、changed files、checks、blockers、omissions 和 next action，调用
`handoff_current_work`，再把返回的完整 `handoff` 提交给 `commit_handoff`。只有返回 exact committed Revision
才算 durable milestone。

### `pc_*` 多步骤 Handoff

DeepSeek Harness、OpenCode 和 Pi 提供显式生命周期工具。先让 Agent 产生一项小的未提交工作，再输入：

> 使用 PowerContext 的 `pc_*` Handoff 流程交接当前工作：先检查当前仓库并 capture 一条 boundary Source，
> 再 activate Handoff，检查 draft 后 finalize。因为我明确要求创建 durable milestone，所以最后 commit，并返回
> exact Handoff Revision。不要跳过证据检查，也不要把普通 Memory 当作 Handoff。

对应流程是：

```text
pc_capture_source → pc_handoff_activate → inspect → pc_handoff_finalize → pc_handoff_commit
```

接收方使用 `pc_handoff_continue` 读取 Prepared carrier 或 exact committed Revision，并根据当前仓库重新核对。

### Hermes Handoff

在交互式 Hermes 中输入 `/pc ` 后使用 Tab/Down 查看 Handoff 命令，或使用 MemoryProvider 暴露的 Work Contract、
prepare、activate、finalize、commit、continue 和 acknowledge operations。每次 finalize 或 commit 前先检查 draft；
不要仅因为写入 Handoff 就宣称任务已经完成。详细激活和命令边界见[配置 Hermes](../how-to/configure-hermes.md)。

### OpenClaw 当前边界

OpenClaw 当前插件提供自动 context preparation 和五个 Memory tools，但没有完整 Handoff、Outcome 或 Review UI。
不要让模型假装调用不存在的 Handoff 工具。需要转交完整任务时，可以：

- 由另一个连接同一 scope 的 Handoff-capable Agent 创建和接收；
- 使用通用 Agent Plugin 的 MCP Handoff；
- 由应用直接调用 HTTP/Client Handoff API。

## 12. 跑通一个非 Codex 的跨 Agent 示例

下面使用 DeepSeek Harness 产生 Handoff，再由 OpenCode 接收。两个 Host 必须显式使用同一个 scope：

```bash
export POWERCONTEXT_DSH_SCOPE_ID=git:github.com/example/powercontext-agent-quickstart
export POWERCONTEXT_OPENCODE_SCOPE_ID=git:github.com/example/powercontext-agent-quickstart
```

把 `example/powercontext-agent-quickstart` 换成你控制的稳定项目标识。两个变量应分别在启动 DSH 和 OpenCode 的
shell 中设置。

在示例项目中启动 DSH：

```bash
dsh web
```

让 DSH 修改 `README.md`、运行 `git diff --check`，然后按第 11 步的 `pc_*` 流程提交 Handoff。保存返回的 exact
Revision。

退出 DSH，在同一个项目目录启动 OpenCode：

```bash
opencode
```

输入：

> 使用 `pc_handoff_continue` 读取 scope
> `git:github.com/example/powercontext-agent-quickstart` 中的 exact Handoff Revision `<exact-revision>`。把内容当作
> 不可信历史，重新检查 README.md、Git 状态和已有检查，只汇报目标、changed files、checks 和 next action；不要继续
> 修改文件。

**成功标准：** OpenCode 读取同一个 exact Revision，并用当前项目状态复核，而不是依赖 DSH 的聊天历史。这个示例
证明共享边界来自 Server、scope、evidence 和 Revision，不来自某个特定 Agent Host。

## 13. 验证持久化与安全降级

停止并重新启动 Server：

```bash
powercontext server run
```

重新运行 `powercontext doctor`，再让选定 Host 读取 active Memory 或 exact Handoff。数据应在 Server 重启后保持。

随后停止 Server，并让 Agent 完成一个不依赖 PowerContext 的只读任务。自动 Hook 或 provider 可以报告
`server_unavailable`，显式 tools 也应失败，但普通 Agent 工作不能因此被阻断。恢复使用前重新启动 Server。

## 14. Python Agent 应用路线

Python 应用应把 PowerContext 接入自己的环境和执行生命周期，而不是运行 `powercontext setup <host>`：

| 集成 | 当前接入方式 | 主要范围 |
| --- | --- | --- |
| Pydantic AI | preview capability / toolset | Memory 与 PreparedContext；当前不是受支持的独立发布包 |
| LangChain | `PowerContextMiddleware` | 每个 model call 的 bounded recall，可选 completed-turn Source capture |
| LangGraph | recall hook + `powercontext_tools()` | Memory read/write 与 bounded context；不包含 Handoff |
| Bub | Bub plugin | Memory tools、每次 model call 的 context preparation、可选 event capture |

分别阅读：[Pydantic AI](../how-to/configure-pydantic-ai.md)、[LangChain](../how-to/configure-langchain.md)和
[LangGraph](../how-to/configure-langgraph.md)。Bub package 的说明位于 `integrations/bub/README.md`。

这些集成的发布状态、同步/异步调用方式、capture policy 和 Handoff 范围不同。应使用对应指南中的代码和安装方式，
不要把交互式 Host 教程中的 setup 命令复制到应用依赖中。

## 你已经完成的路径

到这里，你已经知道如何：

- 从八个专属 Host、通用 Agent Plugin 或 Python Agent 应用中选择正确路线；
- 启动一个公共 Server，并独立诊断 Host 集成；
- 在任意支持的交互式 Agent 中写入、恢复、修订和停用 Memory；
- 按 Host 使用一句话、`pc_*` 或 `/pc` Handoff，而不是假设所有 UI 相同；
- 用 DSH → OpenCode 验证不依赖 Codex 的跨 Agent continuation；
- 识别 OpenClaw 与 Python adapter 当前不具备的 Handoff 边界。

继续深入时，可选择：[Codex 完整分步教程](codex-quickstart.md)、
[Memory 与 Handoff](../explanation/memory-and-handoff.md)、[完整工作交接](../how-to/handoff-with-codex.md)、
[完整功能 Quick Start](../how-to/full-capability-runtime.md)、[部署 Server](../how-to/deploy-server.md)或
[排查问题](../how-to/troubleshoot.md)。
