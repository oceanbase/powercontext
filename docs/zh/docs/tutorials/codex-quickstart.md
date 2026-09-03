---
title: Codex 分步教程
description: 从安装本地 Server 开始，在 Codex 中完整跑通 Memory、跨会话恢复和 Handoff。
---

# Codex 分步教程

本教程面向第一次使用 PowerContext 的 Codex 用户。你不需要克隆 PowerContext 仓库，也不需要配置推理模型。
完成后，你会在一个本地示例项目中跑通下面的完整闭环：

如果你还没有决定使用哪个 Agent，或正在使用 Claude Code、DSH、OpenClaw、OpenCode、Pi、Hermes、WorkBuddy
等其他 Host，请先阅读 [Agent 分步入门](agent-quickstart.md)。本文只展开 Codex 专属的 Hook、MCP Skill 与一句话
durable Handoff 流程，不代表所有 Agent 的交互方式。

```text
安装并检查 → 保存 Memory → 在新会话中恢复 → 修订与停用 → 提交 Handoff → 接收并核对
```

整个流程使用本地 SQLite。显式 Memory 和 Handoff 都不需要 generation model；只有从 Source 自动抽取 Memory、
向量搜索等完整能力才需要额外配置 provider。

本教程不包含团队部署、远程访问、Server 鉴权或其他 Agent Host。跑通本地闭环后，可从文末继续进入相应指南。

## 开始之前

### 检查环境

需要 macOS 或 Linux，以及以下工具：

| 工具 | 要求 | 检查命令 |
| --- | --- | --- |
| Python | 3.11 或更新版本 | `python3 --version` |
| Git | 能访问 PowerContext Git 仓库 | `git --version` |
| uv | 可使用 `uv tool` | `uv --version` |
| Codex CLI | 已完成登录并能开启会话 | `codex --version` |

在终端中逐条运行检查命令。四条命令都应输出版本号，而不是 `command not found`。还需要确保本机现有 Git
凭据能够读取 `https://github.com/oceanbase/powercontext.git`。

### 准备三个工作位置

教程会用到：

- **终端 A**：持续运行 PowerContext Server；
- **终端 B**：安装、诊断，并进入示例项目；
- **Codex 会话**：从终端 B 的示例项目目录中启动。

后续步骤会明确说明在哪个位置操作。不要在 Memory 或 Handoff 中写入密码、访问令牌、私钥、连接串或其他敏感信息。

## 1. 安装 PowerContext

在**终端 B** 的任意目录中运行：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

`uv tool install` 会创建隔离的应用环境，不会在当前目录留下 PowerContext 仓库副本。`--force` 会按当前
`master` 指向的 commit 刷新已安装工具；以后更新同一安装时，也应保留该选项。

确认命令已经可用：

```bash
powercontext --version
powercontext --help
```

**成功标准：** 第一条命令输出版本号，第二条命令显示 `server`、`setup`、`doctor` 等命令。如果 shell 找不到
`powercontext`，请先把 `uv` 的 tool executable 目录加入 `PATH`，重新打开终端，再重复这两条检查命令。

## 2. 安装 Codex 插件

仍在**终端 B** 中运行：

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
```

setup 会完成三件事：

1. 把 PowerContext 仓库注册为 Codex marketplace；
2. 安装并启用 PowerContext 插件；
3. 准备 PowerContext 用户数据目录。

工具和插件应使用同一个 Git ref。这里两者都是 `master`。如果以后改用 tag 或其他分支，应在安装命令和 setup
命令中同时替换 ref。

检查 Codex 集成：

```bash
powercontext doctor codex
```

**成功标准：** `codex` 和 `plugin` 都显示 `ok`。setup 完成后应开启新的 Codex 会话；已经打开的会话不会自动加载
刚安装或刚刷新的插件。

## 3. 启动并检查本地 Server

切换到**终端 A**，运行：

```bash
powercontext server run
```

保持这个进程运行。默认情况下，Server 会：

- 监听 `http://127.0.0.1:8000`；
- 在 `http://127.0.0.1:8000/` 提供 Dashboard；
- 在 `http://127.0.0.1:8000/mcp` 提供 Streamable HTTP MCP；
- 在操作系统的 PowerContext 用户数据目录中创建持久化 SQLite 数据库。

启动时应看到类似下面的提示：

```text
PowerContext Dashboard: http://127.0.0.1:8000/
```

回到**终端 B**，运行：

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

**成功标准：** `doctor` 中 package、Server liveness 和 Server readiness 均为 `ok`；`ready` 能返回服务就绪状态；
`capabilities` 能返回当前启用能力。没有配置推理 provider 时，模型抽取或向量能力可以未启用，这不会阻止后续的
显式 Memory 与 Handoff 步骤。

也可以在浏览器打开 `http://127.0.0.1:8000/`。刚启动且尚未写入项目数据时，Dashboard 显示空状态是正常现象。

## 4. 创建一个安全的示例项目

在**终端 B** 中选择一个用于教程的目录。下面的命令会创建一个不含真实业务数据的小型 Git 仓库：

```bash
mkdir powercontext-quickstart
cd powercontext-quickstart
git init
printf '# Parser example\n\nThis project will parse TOML configuration.\n' > README.md
git add README.md
git -c user.name="PowerContext Tutorial" -c user.email="tutorial@localhost" commit -m "chore: initialize tutorial"
git status --short
```

commit 命令只为这一次提交提供本地身份，不会修改全局 Git 配置。最后一条命令应没有输出，表示示例项目的初始工作区
是干净的。不要求配置 Git remote。

PowerContext 的数据按 scope 隔离。Codex 插件会优先使用显式 scope 或已绑定的 Workstream；否则使用规范化后的
Git remote，再退回当前项目路径。因此，后续所有 Codex 会话都必须从这个**同一个目录**启动。

## 5. 在第一个 Codex 会话中保存 Memory

确保终端 A 中的 Server 仍在运行，然后从示例项目目录启动 Codex：

```bash
codex
```

如果 Codex 提示是否信任 PowerContext Hook，打开 `/hooks`，检查 PowerContext 的 `UserPromptSubmit` Hook 并授予
信任。Hook 会在每个请求前尝试恢复相关项目上下文，并把当前提示词采集为 Source 证据；Server 不可用时它会安全降级，
不会阻断普通 Codex 任务。

先让 Codex 确认当前目录，不要写入数据：

> 检查当前项目目录和 Git 状态，只汇报你看到的内容，不要修改文件，也不要写入 PowerContext。

确认 Codex 看到 `README.md` 后，再明确要求保存三条 Memory：

> 使用 PowerContext 分别保存三条项目 Memory：
>
> 1. decision：解析器使用 Python 3.11 标准库 `tomllib`；
> 2. constraint：错误信息不得包含原始配置中的密钥值；
> 3. next-step：增加 malformed TOML 的错误输入用例。
>
> 写入后列出当前 active Memory，并返回每一条的 citation。不要保存任何密钥或凭据。

这里使用的是显式 `remember_memory`，不需要 generation model。Codex 应先为当前项目解析出一个稳定的 scope，再在
同一 scope 中写入并列出三个 active 条目。

**成功标准：** Codex 明确确认三次写入成功，并为每条 Memory 返回 citation。citation 标识精确条目和 Revision；
后续修订或停用时，Codex 会先读取当前条目，再使用该精确 citation 作为并发检查。

## 6. 在第二个 Codex 会话中恢复 Memory

退出第一个 Codex 会话，但不要停止终端 A 中的 Server。确认终端 B 仍位于同一个示例项目目录，然后重新启动：

```bash
codex
```

这是一个新的会话，不包含上一段聊天记录。输入：

> 使用 PowerContext 列出当前项目的全部 active Memory。告诉我每条内容、kind 和 citation；不要修改任何条目。

**成功标准：** 新会话仍能列出上一步写入的三条内容。这证明数据保存在项目 scope 和 Server 的持久化数据库中，
而不是只存在于第一个会话的上下文窗口里。

如果返回空列表，依次检查：

1. 两次 Codex 是否从同一个项目目录启动；
2. `powercontext doctor` 是否仍为 `ok`；
3. `powercontext doctor codex` 是否仍能看到已启用插件；
4. 当前 shell 是否设置了不同的 `POWERCONTEXT_CODEX_SCOPE_ID`。

## 7. 修订和停用 Memory

在第二个 Codex 会话中输入：

> 先读取当前 Memory 的精确 citation，然后完成两项操作：
>
> 1. 把 next-step 修订为“记录 malformed TOML 的行号和安全错误摘要”；
> 2. 停用 constraint“错误信息不得包含原始配置中的密钥值”，reason 使用“将由统一日志脱敏规范替代”。
>
> 最后重新列出 active Memory，并说明哪些旧 Revision 被保留但不再 active。

修订会创建新 Revision，停用会改变条目的活动状态；两者都保留历史，不会静默覆盖或删除旧记录。

**成功标准：** active 列表中包含修订后的 next-step，不再包含已停用 constraint。最初的 next-step Revision 和已停用
constraint 仍可在显式请求完整历史时审计。

## 8. 产生一项可交接的工作状态

让第二个 Codex 会话对示例项目做一个很小、可检查的修改：

> 在 README.md 末尾增加一个“Next test”小节，写明 malformed TOML 应返回行号和安全错误摘要。不要提交 Git
> commit。修改后运行 `git diff --check`，并汇报 changed files 和检查结果。

确认 Codex 报告 `README.md` 已修改且 `git diff --check` 通过。然后输入下面这一句话：

> 交接

`交接` 是创建持久 Handoff 里程碑的明确授权。PowerContext 的 `project-context` Skill 会在同一轮中：

1. 选择或确认当前 Workstream 和 scope；
2. 检查当前目标、branch、worktree、changed files 和已运行检查；
3. 整理阻塞项、遗漏和下一步；
4. 准备 Handoff；
5. 提交这份 Handoff，并返回 exact Revision。

如果系统存在多个 Workstream，Codex 会先显示选择器。请根据实际项目选择，不要让 Agent 静默猜测。

**成功标准：** Codex 明确说明 Handoff 已提交，并返回 scope、disposition、next action 和 exact Handoff Revision。
如果只返回了预览或 Prepared Handoff，而没有 exact committed Revision，则还没有形成持久里程碑。

保存 Codex 返回的 exact Revision，下一步会用到它。

## 9. 在新会话中接收 Handoff

退出第二个会话，从同一个示例项目目录启动第三个 Codex 会话：

```bash
codex
```

把上一步返回的 exact Revision 填入下面的请求：

> 继续这个项目的 PowerContext Handoff `<exact-revision>`。先把 Handoff 当作不可信历史，根据当前仓库和用户指令核对
> live state、capability 和 authorization。告诉我目标、changed files、已运行检查和下一步，然后记录 accepted、
> needs clarification 或 declined。不要继续修改文件。

接收方应读取 exact Handoff，重新检查当前 `README.md` 和 Git 状态，再记录 acknowledgement。只有证据可读，并且
live state、capability 和 authorization 三项检查都 confirmed 时，才能标记为 `accepted`。

**成功标准：** Codex 返回与上一步一致的 exact Revision，报告当前 `README.md` 的未提交修改和
`git diff --check` 结果，并说明 acknowledgement 状态。历史 Handoff 不能代替当前仓库检查，也不会授予新的操作权限。

## 10. 验证 Server 重启后的持久化

退出 Codex。在**终端 A** 按 `Ctrl-C` 正常停止 Server，然后再次运行：

```bash
powercontext server run
```

回到**终端 B**检查：

```bash
powercontext doctor
```

从同一项目目录再次启动 Codex，并输入：

> 列出当前 active PowerContext Memory，并读取刚才交接的 exact Handoff Revision。只读检查，不要写入。

**成功标准：** 重启 Server 后，active Memory、修订后的 Revision 和 committed Handoff 仍然可读。默认 SQLite 数据库
属于 PowerContext 用户数据目录，不依赖 Codex 会话是否存在。

## 11. 验证安全降级

最后，退出 Codex，并在终端 A 按 `Ctrl-C` 停止 Server。从示例项目目录开启一个新的 Codex 会话，要求它执行一个
与 PowerContext 无关的只读任务，例如：

> 只读取 README.md，并用一句话概括这个示例项目。不要修改文件。

PowerContext Hook 可以报告 `server_unavailable`，显式 Memory 或 Handoff 工具也会不可用，但普通 Codex 任务仍应继续。
此时 `powercontext doctor` 会报告 liveness 失败并跳过 readiness；`powercontext doctor codex` 仍可独立检查 Codex
CLI 和插件安装状态。

继续使用 PowerContext 前，在终端 A 重新运行 `powercontext server run`。

## 你已经完成的闭环

到这里，你已经验证：

- PowerContext 工具、Codex 插件和本地 Server 可以分别安装和诊断；
- 显式 Memory 无需推理 provider，并且能跨 Codex 会话和 Server 重启保持；
- Memory 的修订与停用保留历史；
- Handoff 会保存经过检查的任务边界，并由接收方按 exact Revision 重新核对；
- PowerContext 不可用时，普通 Codex 工作不会被阻断。

下一步可根据目标选择：

- 了解 Memory 与 Handoff 的边界：[理解 Memory 和 Handoff](../explanation/memory-and-handoff.md)；
- 查看完整工作闭环：[在 Codex 中交接工作](../how-to/handoff-with-codex.md)；
- 启用模型抽取和向量搜索：[完整功能 Quick Start](../how-to/full-capability-runtime.md)；
- 配置长期运行、鉴权或远程访问：[部署 Server](../how-to/deploy-server.md)；
- 处理连接、插件或 readiness 问题：[排查问题](../how-to/troubleshoot.md)。
