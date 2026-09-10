---
title: 安装和运行
description: 安装 PowerContext 1.0.0 RC2，并运行本地 Server。
---

# 安装和运行

跨机器连接 Agent 时，请阅读[连接远程 Server](../operate/connect-remote-server.md)，了解地址引导确认、
非交互安装及绑定地址的明文 HTTP 同意设置。

首次使用请从 [Quick Start](quickstart.md)开始。本页说明版本选择、平台要求、安装角色、启动、诊断和更新。

## 平台支持

| 平台 | 状态 |
| --- | --- |
| macOS、Linux | 支持 |
| Windows | `experimental` |

Windows 的 CLI、Server 和个人服务支持为试验性；各 Agent Host 仍需满足自身的平台要求。
使用 Bash 语法的示例需要 Bash 环境，不可直接粘贴到 PowerShell。嵌入式 seekDB 不支持 Windows。

## 选择版本

本页使用 PowerContext 1.0.0 RC2 预发布版本，供测试使用。包与 Agent 集成保持版本一致：
Python 包版本为 `1.0.0rc2`，对应 Git tag 为 `powercontext-v1.0.0rc2`。

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
powercontext setup codex --ref powercontext-v1.0.0rc2
```

宿主支持范围和维护状态见[能力矩阵](../integrations/capabilities.md)。
标为 `experimental` 的能力在这个候选版本中仍属于试验性能力。

## 安装应用

需要在 macOS、Linux 或 Windows 上准备 Python 3.11 或更新版本、Git 和
[`uv`](https://docs.astral.sh/uv/)，然后从 PyPI 安装 PowerContext：

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
```

如需从源码安装同一版本：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.0.0rc2"
```

Git 安装命令不会留下需要自行管理的仓库工作副本。Git 会沿用本机的凭据配置，包括 credential helper 和 SSH 设置。
如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。`--force` 还会从所选 Git ref 当前指向的 commit
刷新已安装工具；如果不加该参数，`uv` 可能只提示相同 requirement 已安装，而不会获取更新后的 `master`。

安装其他分支或 tag 时，替换最后一个 `@` 后的 ref。`master` 分支可能包含尚未发布的改动。
Agent 的安装、连接参数和验证步骤见[各自的集成文档](../integrations/index.md)，并使用与 Server 相同的 ref。

## 运行本地 Server

```bash
powercontext server run
```

未设置环境变量时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 创建默认 Scope；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

Dashboard 是个人使用和演示的可选内容查看器，默认关闭。它不需要单独安装前端或配置模型。
需要使用时，在受保护的环境文件中设置以下值，并将 token 示例替换为自己的长随机凭据：

```dotenv
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=enforced
POWERCONTEXT_SERVER_AUTH_TOKEN=replace-with-your-random-token
```

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext server run --env-file /path/to/powercontext.env
```

打开 `http://127.0.0.1:8000/dashboard/home`，输入同一个 token。更改端口后使用实际端口。
该 token 同时用于 Server API 和 MCP，已连接的 Agent 也需配置它。CLI 不会自动读取目录中的 `.env` 文件。

首次登录选择 Server 默认 Scope，未保存内容时显示空状态。通过 Agent 或公开 API 保存一条 Memory，
再刷新同一 Scope 的记忆页即可查看。经验、技能、交接和用量也来自实际保存记录；页面不采集会话、不运行生成，
也不批准候选。Dashboard 和 Agent 必须连接同一个 Server、使用同一个 Scope。

所有 token 持有者使用同一个身份。多成员 RBAC 部署应保持 Dashboard 关闭，通过 API、MCP 或宿主集成访问内容。
网络与凭据配置见[部署 Server](../operate/deploy-server.md)。

这种最小启动方式不会启用依赖模型的抽取或向量搜索。如需生成并校验一份显式环境文件以启用这些能力，请继续阅读
[启用提取与向量搜索](configure-models.md)。

## 使用嵌入式 seekDB

在有兼容 `pylibseekdb` wheel 的 Linux 和 macOS 系统上可以使用嵌入式 seekDB；Windows 不支持该嵌入式
后端。安装或替换工具时加入可选的 seekDB extra：

```bash
uv tool install --force "powercontext[cli,server,seekdb]==1.0.0rc2"
```

从 SQLite 切换时，需要从 Server 进程环境中删除 `POWERCONTEXT_SERVER_DATABASE_URL`；seekDB 不接受显式的
SQLAlchemy 数据库 URL。然后选择 seekDB 后端并启动 Server：

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

当前目录存在 `.env` 时，`server run` 会自动加载该文件。可以在 shell 中导出变量来覆盖文件值，使用
`--env-file <path>` 选择其他文件，或使用 `--no-env-file` 忽略环境文件。进程管理器和容器通常应提供显式环境，
不要依赖其工作目录。

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
powercontext ready
powercontext capabilities
```

`doctor` 检查已安装的包、Server 存活状态和 Server 就绪状态，不要求安装集成。Server 就绪检查涵盖数据库和
每个已配置的推理服务。Runtime 或数据库故障返回 `not_ready`；推理服务故障返回 `degraded`，不会使数据库
操作退出流量。`ready` 和 `capabilities` 用于查看运行中服务的就绪状态和已启用能力。
Agent 诊断见[各自的集成文档](../integrations/index.md)；Server 状态解释和恢复步骤见[排查问题](../operate/troubleshoot.md)。

需要长期运行进程、使用 Docker、启用鉴权或允许远程访问时，请继续阅读[部署 Server](../operate/deploy-server.md)。

## 更新或替换安装

升级到 1.0.0 RC2：

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
```

使用其他 Git ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

按[各自的集成文档](../integrations/index.md)更新已安装宿主，并使用同一个 ref。更新后重启 Server，再开启新的宿主会话。只要没有修改
`POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite 数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client]==1.0.0rc2"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
