---
title: 安装和运行
description: 安装 PowerContext 1.4.1，并运行本地 Server。
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

本页使用 PowerContext 1.4.1。包与 Agent 集成保持版本一致：
Python 包版本为 `1.4.1`，对应 Git tag 为 `powercontext-v1.4.1`。

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext setup codex --ref powercontext-v1.4.1
```

宿主支持范围和维护状态见[能力矩阵](../integrations/capabilities.md)。
标为 `experimental` 的能力在此版本中仍属于试验性能力。

## 安装应用

需要在 macOS、Linux 或 Windows 上准备 Python 3.11 或更新版本、Git 和
[`uv`](https://docs.astral.sh/uv/)，然后从 PyPI 安装 PowerContext：

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
```

如需从源码安装同一版本：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.4.1"
```

只连接远程 Server 的机器安装 `cli` extra 即可使用 `setup`、`doctor` 和远程内容命令。运行本地 Server 或使用
`service` 命令时，还需安装 `server`。如果命令 provider 缺少依赖，CLI 会报告 provider 和缺失模块，便于修复安装。

Git 安装命令不会留下需要自行管理的仓库工作副本。Git 会沿用本机的凭据配置，包括 credential helper 和 SSH 设置。
如需使用 SSH，请把 HTTPS URL 换成当前环境允许的 Git URL。`--force` 还会从所选 Git ref 当前指向的 commit
刷新已安装工具；如果不加该参数，`uv` 可能只提示相同 requirement 已安装，而不会获取更新后的 `master`。

安装其他分支或 tag 时，替换最后一个 `@` 后的 ref。`master` 分支可能包含尚未发布的改动。
Agent 的安装、连接参数和验证步骤见[各自的集成文档](../integrations/index.md)，并使用与 Server 相同的 ref。

## 运行本地 Server

```bash
powercontext server run
```

没有环境变量或环境文件时，Server 会：

- 监听 `127.0.0.1:8000`；
- 在 `/mcp` 启用 Streamable HTTP MCP；
- 创建默认 Scope；
- 在操作系统的用户数据目录中创建持久化 SQLite 数据库；
- 无需推理服务即可支持显式 Memory 操作。

按 `Ctrl-C` 可正常关闭。再次运行该命令会打开同一个数据库。

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
powercontext server run --env-file /path/to/powercontext.env
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
uv tool install --force "powercontext[cli,server,seekdb]==1.4.1"
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

PowerContext 默认使用专用数据库 `powercontext`，先创建缺失的数据库，再初始化应用表。
设置 `POWERCONTEXT_SERVER_DATABASE_DATABASE=my_context` 可以指定其他名称；缺失时创建，已有时复用。
包含反引号的库名会在初始化之前被拒绝。
未设置 `POWERCONTEXT_SERVER_DATABASE_PATH` 时，实例保存在
PowerContext 用户数据目录的 `seekdb` 子目录中；如果设置了 `POWERCONTEXT_HOME`，默认路径为
`$POWERCONTEXT_HOME/seekdb`。只有需要其他位置时才设置 `POWERCONTEXT_SERVER_DATABASE_PATH`。

在另一个终端确认 Server 和数据库已经就绪：

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

### 已有 seekDB 部署

升级前先备份 seekDB 实例和配置，再显式保留旧数据库：

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
export POWERCONTEXT_SERVER_DATABASE_DATABASE=test
powercontext server run
```

保持原来的 `POWERCONTEXT_SERVER_DATABASE_PATH` 或 `POWERCONTEXT_HOME`。实例目录和数据库名称是两个独立配置，
修改其中任何一个都可能连接到不同的数据。服务和容器也需要使用相同配置。

如果没有显式配置库名，而 `test` 中存在以 PowerContext 的 `pc_` 为前缀的表，启动会在创建应用表前停止，
要求选择数据库。系统不会自动选择、复制或删除旧库。显式选择 `test` 可以继续使用旧数据；显式选择
`powercontext` 则使用新库。配置向导也会询问数据库名称，已有用户应填写 `test`。

需要迁移时，先继续使用 `test`，直到完成备份并准备好针对当前引擎版本验证过的导出、导入流程。
停止所有源库写入者，准备目标库，按该流程导出、导入结构和数据，校验身份、关联关系，并通过 PowerContext
读取迁移结果后再切换配置。自动建库不执行数据迁移。不能假定任意 SQL 转储都能跨引擎或表结构版本使用；
已有表仍需通过兼容性检查，表结构升级需要显式维护。
代码图引用绑定实例和数据库，仅复制关系表数据不等于迁移代码图存储；校验流程应包含目标库的 Code 索引重建和查询。
显式继续使用 `test` 会保留旧代码图身份。

切换后由用户选择保留期，旧库保持停写。记录源和目标实例路径、库名、备份位置、停写及切换时间、校验结果和复核日期。
手动删除旧库前，确认所有部署均已停止使用它，并且仍有可用备份。目标库的新写入不会同步回源库，保留源库不代表
可以无损回退。PowerContext 不会自动迁移、发送到期提醒、比较新旧库或删除旧库。

## 使用 OceanBase

连接已有 OceanBase **MySQL 兼容模式的用户租户**。完整 Server 需要先准备所选数据库，并通过显式维护初始化
Server 表结构，再启动服务。PowerContext 不负责创建 OceanBase 实例、租户或账户。
确认网络可达，按上文步骤安装 Server，然后配置连接：

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase
# 凭据应保存在密钥管理服务或受保护的环境文件中。
export POWERCONTEXT_SERVER_DATABASE_URL='mysql+aoceanbase://user%40tenant:encoded-password@host:2881/?charset=utf8mb4'
```

使用租户账户和实际的直连或 OBProxy 地址、端口。URL 用户名和密码中的保留字符需要百分号编码，例如
`user@tenant` 编码为 `user%40tenant`。URL 必须使用 `mysql+aoceanbase` 驱动、显式端口和 `charset=utf8mb4`。

示例省略库名，因此使用 `powercontext`。自定义库名放在 URL 路径中，例如 `/team_context?charset=utf8mb4`。
已有 URL 中显式指定的库名保持有效。库名仅通过 URL 路径指定，不接受 `db` 或 `database` 查询参数。
数据库名称支持 1～64 个 BMP 字符，不允许控制字符、斜杠、反斜杠、点、反引号、
首尾空白或系统数据库名称；PowerContext 不会静默裁剪或改名，服务器自身的标识符大小写规则仍然适用。
包含反引号的名称会在配置校验阶段被拒绝，不会连接或创建数据库。请使用 `powercontext`、`team_context`
或不含反引号的中文名称。

Python SDK 初始化时先尝试连接目标库，已有可用库不要求额外建库权限。只有明确的“数据库不存在”错误才触发实例或租户级连接，
执行 `CREATE DATABASE IF NOT EXISTS`，随后重新连接目标库并初始化应用表。认证、网络错误不会触发建库或切库。
如果服务器拒绝访问但无法确认库是否存在，错误会要求管理员同时检查数据库和权限。

首次部署完整 Server 时，需要先创建所选数据库。SDK 自动初始化因权限不足无法建库时，也可由管理员执行同一步骤。
请在**同一租户**中执行以下 SQL；自定义部署应替换为实际库名，然后为应用账户授予所需数据库权限：

```sql
CREATE DATABASE IF NOT EXISTS `powercontext` CHARACTER SET utf8mb4;
```

完整 Server 接下来应按[数据库迁移](../operate/database-migrations.md)指南，显式指定证据目录和协调模式，
对登记的 Server bundle 执行 plan、apply 和 verify。数据库就绪后再运行 `powercontext server run`。
Server 启动检查就绪状态，不初始化 OceanBase 表结构，也不会自动升级已有库。SDK 调用方完成建库后可以使用原 URL 重试初始化。
创建缺失表仍然需要建表权限；已有完整可用库不会新增建库权限要求。
初始化不会删除或重建已有数据库，不会在失败后切换其他数据库，也不授权升级已有表结构。
并发创建使用幂等建库、建表 DDL，并处理索引重名竞争；这不提供已有表结构迁移的协调能力，也不改变嵌入式 seekDB
对实例进程所有权的限制。

Python 调用方使用 `OceanBaseProfile.open()` 时可省略 URL 中的库名，并按需建库。
`OceanBaseProfile.open_existing()` 只连接已准备好的数据库；`attach()` 要求调用方的 engine 已显式选择一个已有数据库。
两者都不替换 engine 或改变连接目标。只读巡检（`plan`、`verify` 与 `db-migrate` 的检查类命令）不会创建缺失的
OceanBase 或内嵌 seekDB 库；显式的 seekDB 写入维护可以在应用迁移包前准备配置指定的数据库。

只读 seekDB 巡检要求实例目录已初始化。路径缺失或目录为空时，会在启动 native 引擎前报错，不创建父目录或存储文件。
路径为文件，或非空目录中没有可识别的 native 存储时，也会拒绝操作并保留原有内容。普通启动仍可初始化新实例。

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

<!-- upgrade-support:start -->
1.3.0 和 1.4.0 升级必须通过 SQLite、seekDB 和 OceanBase 的完整 Server 验收。当前迁移路径尚未验证，请勿升级已有数据库。
<!-- upgrade-support:end -->

已有部署应保持当前可用版本，等待来源版本及后端具备已验收的升级路径。试用新建部署时，请使用独立程序环境、
数据库位置和端口。替换程序包不会完成数据库迁移；备份也不能使不受支持的迁移变为可用。

支持声明由发布矩阵和安装包验收共同检查，详见[准备发布](../../development/releasing.md)。
[Artifact 处理迁移](../operate/artifact-processing-migration.md)和
[Atomic Memory 迁移](../operate/atomic-memory-migration.md)各自的验证结果不代表完整 Server 就绪。

升级已有数据库前，请先检查 [Dream 协调升级要求](../workflows/review-dream-revisions.md#协调升级)。
统一 Candidate 路由将 `/v1/artifact-candidates/*` 替换为 `/v1/candidates/*`，不提供别名，Dream 客户端要求契约 2。
已有 Dream 存储需排空未完成任务后显式迁移。[完整 Server 迁移](../operate/database-migrations.md)要求
`state=ready` 和 `server_ready=true`；局部 Artifact 或 Atomic Memory 验证不能替代它。不要绕过迁移门禁。

安装 1.4.1 到独立环境以试用新建部署：

```bash
uv venv /path/to/powercontext-preview
uv pip install --python /path/to/powercontext-preview/bin/python "powercontext[cli,server]==1.4.1"
```

在独立环境安装其他 Git ref：

```bash
uv pip install --python /path/to/powercontext-preview/bin/python "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

连接新建部署的客户端及 Agent 集成应使用匹配的 ref。保留原部署的数据库位置和配置，不要用原数据库测试新版启动。
Windows 的虚拟环境解释器位于 `Scripts/python.exe`。

## 为 Python 项目安装角色

如果应用需要导入异步 Client SDK，应把它加入该应用自己的环境：

```bash
uv add "powercontext[client]==1.4.1"
```

进程内 Python 组合使用 `builtin`，服务使用 `server`，Python SDK 使用 `client`，基于 Server 的命令行使用
`cli`。只安装在 `uv tool` 隔离环境中的 extra 不能被另一个 Python 项目直接导入。
