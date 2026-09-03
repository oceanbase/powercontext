---
title: 安装和运行
description: 从 Git 安装 PowerContext，并运行本地 Server。
---

# 安装和运行

如果你是第一次使用 PowerContext，请先跟随 [Agent 分步入门](../tutorials/agent-quickstart.md)选择 Host，并从零
跑通 Memory 与该 Host 支持的 Handoff 路径。本指南集中说明安装角色、Server 启动方式、seekDB、诊断和更新，
便于已经明确目标的用户按需查找操作。

## 安装应用

需要在 macOS、Linux 或 Windows 上准备 Python 3.11 或更新版本、Git 和
[`uv`](https://docs.astral.sh/uv/)，然后从指定 Git ref 直接安装 PowerContext：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

该命令不会留下需要自行管理的仓库工作副本。Git 会沿用本机的凭据配置，包括 credential helper 和 SSH 设置。
如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。`--force` 还会从所选 Git ref 当前指向的 commit
刷新已安装工具；如果不加该参数，`uv` 可能只提示相同 requirement 已安装，而不会获取更新后的 `master`。

安装指定分支或 tag 时，替换最后一个 `@` 后的 `master`。配置集成时应使用同一个 ref：

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

单宿主命令仍是显式路径。一级宿主目录包含 `codex`、`claude-code`、`dsh`、`openclaw`、`opencode`、`pi`
和 `hermes`。若要一次安装多个宿主，可重复传入 `--host`；在 TTY 上省略 `--host` 则从目录中选择。不带子命令的
`powercontext setup` 仍然只打印帮助：

```bash
powercontext setup select --host codex --host dsh --source oceanbase/powercontext --ref <ref>
```

未传入 `--server-url` 时，Claude Code 和 OpenClaw 保留 `http://127.0.0.1:8000` 默认值；显式传入该选项时会覆盖
两个被选中宿主的地址。Codex、DSH、OpenCode、Pi 和 Hermes 只有在
通过现有安装后诊断后才会报告为 installed。安装 Hermes 后，还需运行 `hermes memory setup` 并选择 PowerContext，
然后再启动 Hermes。

WorkBuddy 仍可通过 `powercontext setup workbuddy` 安装，但不在 `setup select` 中。站点导航中的集成指南说明了
各宿主的前置条件、专有选项和行为。

## 运行本地 Server

```bash
powercontext server run
```

未设置环境变量时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 创建默认 Scope，并在 `/` 启用 Dashboard；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

启动成功后，终端会输出 Dashboard 地址，例如 `http://127.0.0.1:8000/`。Dashboard 与 HTTP API、MCP 共用 Server
的监听地址和端口。Dashboard 初始化失败时，Server 会记录包含直接原因的 warning，并继续提供其他接口；可通过
`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` 显式关闭 Dashboard。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

这种最小启动方式不会启用依赖模型的抽取或向量搜索。如需生成并校验一份显式环境文件以启用这些能力，请继续阅读
[完整功能 Quick Start](full-capability-runtime.md)。

## 使用嵌入式 seekDB

在有兼容 `pylibseekdb` wheel 的 Linux 和 macOS 系统上可以使用嵌入式 seekDB；Windows 不支持该嵌入式
后端。安装或替换工具时加入可选的 seekDB extra：

```bash
uv tool install --force "powercontext[cli,server,seekdb] @ git+https://github.com/oceanbase/powercontext.git@master"
```

从 SQLite 切换时，需要从 Server 进程环境中删除 `POWERCONTEXT_SERVER_DATABASE_URL`；seekDB 不接受显式的
SQLAlchemy 数据库 URL。然后选择 seekDB 后端并启动 Server：

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

CLI 不会自动搜索 `.env` 文件。请在 shell 中导出这些值、在启动 Server 的进程管理器或容器中配置，或者通过
`powercontext server run --env-file <path>` 显式传入文件。

PowerContext 固定使用 seekDB 内置的 `test` 数据库。未设置 `POWERCONTEXT_SERVER_DATABASE_PATH` 时，实例保存在
PowerContext 用户数据目录的 `seekdb` 子目录中；如果设置了 `POWERCONTEXT_HOME`，默认路径为
`$POWERCONTEXT_HOME/seekdb`。只有需要其他位置时才设置 `POWERCONTEXT_SERVER_DATABASE_PATH`。

在另一个终端确认 Server 和数据库已经就绪：

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

## 验证安装

```bash
powercontext doctor
powercontext doctor integrations
powercontext doctor codex
powercontext doctor claude-code
powercontext doctor dsh
powercontext doctor openclaw
powercontext doctor opencode
powercontext doctor pi
powercontext doctor hermes
powercontext doctor workbuddy
powercontext ready
powercontext capabilities
```

`doctor` 检查已安装的包、Server 存活状态和 Server 就绪状态，不要求安装集成。Server 就绪检查涵盖数据库和
每个已配置的推理服务。Runtime 或数据库故障返回 `not_ready`；推理服务故障返回 `degraded`，不会使数据库
操作退出流量。`doctor integrations` 是可选的一级宿主只读总览，缺失 CLI 不会让该命令失败。
各个 `doctor <host>` 命令分别检查一个可选宿主 CLI 及其全部 PowerContext 集成项。WorkBuddy 提供独立的
`doctor workbuddy` 命令，但不出现在一级宿主总览中。内容命令会经过公开 HTTP SDK 路径。`ready` 和
`capabilities` 用于查看运行中服务的就绪状态和已启用能力。完整的状态解释和恢复步骤见
[排查问题](troubleshoot.md)。

需要长期运行进程、使用 Docker、启用鉴权或允许远程访问时，请继续阅读[部署 Server](deploy-server.md)。

## 更新或替换安装

使用指定 ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
powercontext setup codex --source oceanbase/powercontext --ref <ref>
```

对每个已安装宿主重复 setup 命令，并使用同一个 ref。更新后重启 Server，再开启新的宿主会话。只要没有修改
`POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite 数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
