---
title: 安装和运行
description: 安装 PowerContext 1.2.0，并运行本地 Server。
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

本页使用 PowerContext 1.2.0。包与 Agent 集成保持版本一致：
Python 包版本为 `1.2.0`，对应 Git tag 为 `powercontext-v1.2.0`。

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
powercontext setup codex --ref powercontext-v1.2.0
```

宿主支持范围和维护状态见[能力矩阵](../integrations/capabilities.md)。
标为 `experimental` 的能力在此版本中仍属于试验性能力。

## 安装应用

需要在 macOS、Linux 或 Windows 上准备 Python 3.11 或更新版本、Git 和
[`uv`](https://docs.astral.sh/uv/)，然后从 PyPI 安装 PowerContext：

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
```

如需从源码安装同一版本：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.2.0"
```

Git 安装命令不会留下需要自行管理的仓库工作副本。Git 会沿用本机的凭据配置，包括 credential helper 和 SSH 设置。
如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。`--force` 还会从所选 Git ref 当前指向的 commit
刷新已安装工具；如果不加该参数，`uv` 可能只提示相同 requirement 已安装，而不会获取更新后的 `master`。

安装其他分支或 tag 时，替换最后一个 `@` 后的 ref。`master` 分支可能包含尚未发布的改动。
Agent 的安装、连接参数和验证步骤见[各自的集成文档](../integrations/index.md)，并使用与 Server 相同的 ref。

## 运行本地 Server

受支持的个人 macOS 和 Linux 安装推荐使用原生当前用户服务管理器：

```bash
powercontext service install
powercontext service status
```

首次安装会在后台启动 Server。关闭终端不会停止服务；机器重启后，服务会在登录时恢复。
Linux 需要可用的 `systemd --user` 管理器。安装服务是显式步骤，安装包或 Agent 插件不会自动完成它。
远程或共享部署见[部署 Server](../operate/deploy-server.md)。

没有配置环境文件时，Server 使用以下默认值：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 创建默认 Scope；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

使用向导生成的配置时，安装服务必须传入 `--env-file .env`。服务不会自动发现 `.env`，也不会复制调用者的 shell 环境。
将自定义存储路径、模型设置和凭据保存在受保护的文件中；已经使用 `POWERCONTEXT_HOME` 时，也要保留该配置。

Dashboard 是个人使用和演示的可选内容查看器，默认关闭。它不需要单独安装前端或配置模型。
本地免 token 启用时，在环境文件中设置以下值：

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

需要认证时，将 `POWERCONTEXT_SERVER_ACCESS_MODE` 设为 `enforced`，并将 `POWERCONTEXT_SERVER_AUTH_TOKEN` 设为自己的长随机凭据。
[配置向导](configure-server-environment.md#本地-dashboard-与可选认证)在本地开启 Dashboard 时也提供这一选择。

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
```

打开 `http://127.0.0.1:8000/dashboard/home`，更改端口后使用实际端口。未启用认证时可直接进入页面；
启用后使用 Server Token 登录，已连接的 Agent 也需配置该 token 来访问 API 和 MCP。CLI 不会自动读取目录中的 `.env` 文件。

首次访问选择 Server 默认 Scope，未保存内容时显示空状态。通过 Agent 或公开 API 保存一条 Memory，
再刷新同一 Scope 的记忆页即可查看。经验、技能、交接和用量也来自实际保存记录；页面不采集会话、不运行生成，
也不批准候选。Dashboard 和 Agent 必须连接同一个 Server、使用同一个 Scope。

打开**画像**查看已保存内容，通过**版本历史**阅读历史修订或核对来源；阅读旧版本不会改变当前画像。
在**交接**目录条目或详情页点击**导出 Markdown**，可下载该精确版本的完整正文、遗漏和引用。
若登录失效，重新登录后会返回所选详情，再次点击导出即可。

启用认证时，所有 token 持有者使用同一个身份。多成员 RBAC 部署应保持 Dashboard 关闭，通过 API、MCP 或宿主集成访问内容。
网络与凭据配置见[部署 Server](../operate/deploy-server.md)。

这种最小启动方式不会启用依赖模型的抽取或向量搜索。如需生成并校验一份显式环境文件以启用这些能力，请继续阅读
[启用提取与向量搜索](configure-models.md)。

### 开发或调试时在前台运行

开发、调试、临时使用或没有受支持的个人服务管理器时，可执行：

```bash
powercontext server run --env-file /path/to/powercontext.env
```

没有环境文件时使用 `powercontext server run`。保持该终端打开，在另一个终端执行客户端命令。
按 `Ctrl-C` 停止 Server；机器重启后需要手动启动。将已有前台实例改为个人服务前，先按 `Ctrl-C` 停止，
再使用同一份配置安装服务。如果个人服务已占用该地址，先执行 `powercontext service stop`；恢复后台运行时，
先停止前台进程，再执行 `powercontext service start`。

## 使用嵌入式 seekDB

在有兼容 `pylibseekdb` wheel 的 Linux 和 macOS 系统上可以使用嵌入式 seekDB；Windows 不支持该嵌入式
后端。安装或替换工具时加入可选的 seekDB extra：

```bash
uv tool install --force "powercontext[cli,server,seekdb]==1.2.0"
```

通过配置向导选择 seekDB，或编辑受保护的环境文件。从 SQLite 切换时，需要从文件中删除
`POWERCONTEXT_SERVER_DATABASE_URL`；seekDB 不接受显式的 SQLAlchemy 数据库 URL。在文件中设置后端：

```dotenv
POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
```

使用同一份文件校验并安装或更新服务：

```bash
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
```

PowerContext 固定使用 seekDB 内置的 `test` 数据库。未设置 `POWERCONTEXT_SERVER_DATABASE_PATH` 时，实例保存在
PowerContext 用户数据目录的 `seekdb` 子目录中；如果设置了 `POWERCONTEXT_HOME`，默认路径为
`$POWERCONTEXT_HOME/seekdb`。只有需要其他位置时才设置 `POWERCONTEXT_SERVER_DATABASE_PATH`。

按[快速开始](quickstart.md)加载客户端连接配置，再确认服务就绪：

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

个人服务的具体要求、Docker、鉴权和远程访问见[部署 Server](../operate/deploy-server.md)。

## 更新或替换安装

升级已有部署前，先备份数据库和配置。1.1.0 会在 Server 启动时升级旧标签表约束，以支持 Topic Memory 标签。
先停止旧 Server 实例，再启动一个升级后的实例，待结构升级完成后再启动其他实例。对于 1.0.0 之前的数据库，
如果尚未完成[Artifact 处理迁移](../operate/artifact-processing-migration.md)，还需要先执行该迁移。
Server、客户端和 Agent 集成需一起升级。Dashboard 需要显式启用，本地使用可选择静态 Bearer 认证，
见[部署 Server](../operate/deploy-server.md)；远程明文 HTTP 连接需要客户端明确同意，
见[连接远程 Server](../operate/connect-remote-server.md)。

升级到 1.2.0：

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
```

使用其他 Git ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

完成所需迁移后，使用同一份受保护的文件重新执行 `powercontext service install --env-file .env`，更新注册的程序和配置。
只有原服务未配置环境文件时，才省略 `--env-file`。
服务每次启动都会校验文件身份，因此任何文件修改，包括只涉及客户端的 Scope 设置，也需要重新安装。
如果此前显式执行了 `powercontext service stop`，安装会保留停止状态；准备恢复时执行 `powercontext service start`。
使用 `powercontext service status` 和上述就绪检查确认恢复。

按[各自的集成文档](../integrations/index.md)更新已安装宿主，并使用同一个 ref，再开启新的宿主会话。
前台用户需要手动重启 Server。只要没有修改 `POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite 数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client]==1.2.0"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
