---
title: 安装和运行
description: 使用 Bash 或 PowerShell 安装最新版 PowerContext，配置镜像并运行 Server。
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

## 使用推荐安装脚本

脚本在独立的 uv tool 环境中安装 CLI 和本地 Server。已有兼容的 uv/Python 时会直接复用；缺少时在用户目录安装
uv 和 Python 3.12，无需管理员权限。

macOS 或 Linux（使用 Bash，需要 `curl` 或 `wget`）：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

Windows（PowerShell 5.1 或更新版本）：

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1)))"
```

先执行安装完成时打印的 PATH 命令，再在终端运行 `powercontext`。安装器不会修改 shell 启动文件或 Windows 的持久
PATH。`UV_INSTALL_DIR` 指定缺少 uv 时的安装目录，`UV_TOOL_BIN_DIR` 指定 PowerContext 可执行文件目录。

默认安装可以无人值守执行。使用 `--host codex` 安装选定的 Agent 集成；多个宿主可重复传入 `--host`。
只有集成安装需要 Git 及对应宿主的前置条件。`--no-hosts` 可以显式表达默认行为，不能与 `--host` 同时使用。
宿主支持范围见[能力矩阵](../integrations/capabilities.md)。

连接已有远程 Server 时，加上 `--profile client`，只安装 CLI 和 Client 依赖。默认 `--profile local` 包含本地 Server。
两种模式都不会启动 Server、注册服务或覆盖配置与数据。本地安装使用 `powercontext config init`，继续阅读
[快速开始](quickstart.md)；Client-only 安装按[远程连接指南](../operate/connect-remote-server.md)设置地址和认证。

## 配置并安装个人服务

个人 macOS/Linux 推荐使用原生当前用户服务。安装器可以显式串联配置向导和服务注册：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --configure --service --env-file "$HOME/.config/powercontext/powercontext.env"
```

`--configure` 通过控制终端打开现有向导，需要显式 `--env-file`。保存后才能继续；取消会中止后续服务和宿主安装。
个人服务选择 loopback 绑定；Linux 需要可用的 `systemd --user`。无人值守环境使用已有受保护文件：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --service --env-file /path/to/powercontext.env
```

安装器验证配置，执行 `service install`、`service status`，再通过 `doctor --env-file` 检查同一文件中的 Server。
显式 `--host` 也会使用该文件及实际安装版本对应的 ref。Client-only 模式在安装前拒绝 `--configure` 和 `--service`；
单独使用 `--env-file` 可以为显式宿主安装提供连接配置，而不安装本地服务。

Windows 的相同选项仍为试验性支持；`--service` 显式启用登录自启动。`--configure` 需要交互控制台。
已有文件的 ACL 保护方式见[部署 Server](../operate/deploy-server.md)。

这些选项要求所选发行版提供 `config init --require-write` 和 `doctor --env-file`。缺少时，安装器报告失败阶段并保留
Runtime；使用兼容发行版，或分开执行配置与服务命令。Runtime 安装、配置保存、服务注册和 Server 就绪分别报告。
`degraded` 诊断返回非零状态；后续失败不会回滚包文件、删除配置或移除已提交的服务注册。

任何文件修改，包括写入返回的 Scope ID 后，都要使用原文件重新注册：

```bash
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
powercontext doctor --env-file /path/to/powercontext.env
```

`doctor --env-file` 以文件为准，覆盖调用者 shell 的默认配置。Server 文件选择其监听地址，Client-only 文件选择 Client URL；
显式 `--server-url` 可以覆盖诊断目标。生成的后续步骤仍负责引导 Scope 创建和 Agent 验收；服务运行不代表 Agent 工作流已完成。

## 选择版本

默认 `--version latest` 安装或升级到所选包源中与当前 Python 兼容的最新稳定版，排除预发布版本。
`--version` 也支持指定准确版本，包括 `1.3.0rc1` 这样的显式预发布版本；源中没有指定版本时会报错，不会换成其他版本。

例如，安装 PowerContext 1.2.0 及其 Codex 集成：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

Python 包版本为 `1.2.0`，对应 Git tag 为 `powercontext-v1.2.0`。使用 `latest` 时，安装器读取实际安装的 CLI 版本，
并使用对应的 tag 安装集成，不会使用会移动的 `master` 分支。稍后添加集成时可以执行：

```bash
powercontext setup codex --ref "powercontext-v$(powercontext --version)"
```

需要交互选择宿主时，运行 `powercontext setup select --ref "powercontext-v$(powercontext --version)"`。

PowerShell 也支持这里的双引号表达式。集成安装失败时，已安装的 Runtime 会保留，脚本返回错误并给出重试提示。
安装成功不代表 Server 就绪或宿主工作流可用；仍需执行下文检查及集成文档中的验证。

安装器在报告成功前验证版本和所选模式的命令。包安装成功后仍可能无法通过命令验证，此时旧可执行文件可能已被替换。
请查看错误中指出的命令，再指定已知可用的 `--version` 重试。安装器不回滚包文件。

## 使用镜像重试依赖下载

包索引、uv 二进制和 Python 发行版是三类独立下载。更换包索引不会改变 uv 或 Python 的下载位置。

| 下载内容 | 显式配置 | 中国区域自动源 | 全球默认源 |
| --- | --- | --- | --- |
| PowerContext 和 Python 包 | `--index-url URL`、uv 索引环境变量或 `uv.toml` | 清华 PyPI 镜像 | PyPI |
| uv 安装器 | `POWERCONTEXT_UV_INSTALLER_URL` | USTC uv release 镜像 | Astral 安装器 |
| uv 二进制 | `UV_DOWNLOAD_URL` 或 `UV_INSTALLER_GITHUB_BASE_URL` | USTC uv release 镜像 | Astral release 渠道 |
| Python 发行版 | `UV_PYTHON_INSTALL_MIRROR` | NJU python-build-standalone 镜像 | uv 默认渠道 |

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --region cn
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --index-url https://pypi.org/simple
```

`--region auto|cn|global` 优先于 `POWERCONTEXT_INSTALL_REGION`。自动选择依次参考本地命名时区、locale 地区，最后采用
全球源，不请求网络定位服务。自动镜像不可用时可回退官方源；中国区域包镜像可达时，即使缺少请求版本，也保持选中。
解析或文件下载失败由 uv 报告，不会再换源重试安装。需要换源时使用 `--region global` 或显式指定来源。
显式配置的源不会自动回退。镜像可能有同步延迟：`latest` 指所选源中的最新兼容稳定版。

已有的 uv 索引环境变量和配置文件优先于自动包镜像。`--index-url` 只覆盖默认索引，额外 uv 索引仍保留其优先级。
私有源凭据应使用 uv 认证配置，不放在脚本参数里。uv 不读取 `PIP_INDEX_URL` 或 `PIP_EXTRA_INDEX_URL`。
已有 Python 镜像设置和 `uv.toml` 同样会禁用自动 Python 镜像选择。`UV_ASTRAL_MIRROR_URL` 会透传给支持它的 uv 版本。
这些设置只作用于本次安装，不改写持久包管理配置；已有 uv 会直接复用，不会自动升级。

需要检查脚本内容，或在 PowerShell 传入多个选项时，可以先保存脚本：

```powershell
irm https://powercontext.oceanbase.io/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1 --region cn --version 1.2.0
```

已有 uv、兼容 Python 和完整依赖缓存时，可以使用 `UV_OFFLINE=1` 重装。这不等于离线发行包；缺少下载内容时会明确报错。

## 手动安装包或源码

已自行管理 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/) 时，可以直接安装包：

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
```

如需从源码安装同一版本（需要 Git）：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.2.0"
```

Git 安装命令不会留下需要自行管理的仓库工作副本。Git 会沿用本机的凭据配置，包括 credential helper 和 SSH 设置。
如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。`--force` 还会从所选 Git ref 当前指向的 commit
刷新已安装工具；如果不加该参数，`uv` 可能只提示相同 requirement 已安装，而不会获取更新后的 `master`。

安装其他分支或 tag 时，替换最后一个 `@` 后的 ref。`master` 分支可能包含尚未发布的改动。
Agent 的安装、连接参数和验证步骤见[各自的集成文档](../integrations/index.md)，并使用与 Server 相同的 ref。

## 运行本地 Server

个人 macOS/Linux 推荐安装原生当前用户服务：

```bash
powercontext service install
powercontext service status
powercontext doctor
```

开发、调试、临时使用或没有可用原生 manager 的平台使用 `powercontext server run`。已有前台实例时，先停止再安装服务。
服务安装始终是显式操作。

没有环境变量或环境文件时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 创建默认 Scope；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

原生服务管理器负责启动和重启；升级或修改配置后重新注册。前台 `server run` 可以通过 `Ctrl-C` 正常关闭。
两种入口都会打开同一个已配置数据库。

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
powercontext doctor --env-file /path/to/powercontext.env
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

## 使用嵌入式 seekDB

在有兼容 `pylibseekdb` wheel 的 Linux 和 macOS 系统上可以使用嵌入式 seekDB；Windows 不支持该嵌入式
后端。安装或替换工具时加入可选的 seekDB extra：

```bash
uv tool install --force "powercontext[cli,server,seekdb]==1.2.0"
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

升级已有部署前，先备份数据库和配置。1.1.0 会在 Server 启动时升级旧标签表约束，以支持 Topic Memory 标签。
先停止旧 Server 实例，再启动一个升级后的实例，待结构升级完成后再启动其他实例。对于 1.0.0 之前的数据库，
如果尚未完成[Artifact 处理迁移](../operate/artifact-processing-migration.md)，还需要先执行该迁移。
Server、客户端和 Agent 集成需一起升级。Dashboard 需要显式启用，本地使用可选择静态 Bearer 认证，
见[部署 Server](../operate/deploy-server.md)；远程明文 HTTP 连接需要客户端明确同意，
见[连接远程 Server](../operate/connect-remote-server.md)。

个人服务可以通过 `powercontext service uninstall` 停止，数据会保留。包升级后，使用原配置路径重新执行
`service install --env-file`。

重新运行安装器可升级到最新稳定版；需要保持指定版本时，加上 `--version`：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

使用其他 Git ref 替换现有工具：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

按[各自的集成文档](../integrations/index.md)更新已安装宿主，并使用同一个 ref。更新后使用原来的文件重新执行 `powercontext service install --env-file /path/to/powercontext.env`，
检查 `service status` 和 `doctor --env-file`，再开启新的宿主会话。只要没有修改
`POWERCONTEXT_HOME` 或数据库 URL，现有 SQLite 数据会继续保留。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client]==1.2.0"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
