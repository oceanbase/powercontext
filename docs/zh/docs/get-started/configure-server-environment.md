---
title: 配置 Server 环境
description: 通过显式环境文件生成、检查、校验并运行 PowerContext。
---

# 配置 Server 环境

当 Server 需要推理、调度、存储或部署设置时，使用显式环境文件。

## 1. 生成文件

```bash
powercontext config init --output .env
```

命令默认打开中英文向导。首次使用时，依次按 `LC_ALL`、`LC_MESSAGES`、`LANG` 判断默认语言；都未设置时，再读取系统语言
（包括 macOS 语言偏好）。无法判断或语言不受支持时使用英语。可以在首屏切换，也可以显式指定 `--language en` 或
`--language zh`。再次配置已有文件时，会记住上次使用的向导语言。

选择语言后，先选择存储、使用场景和记忆能力，再配置 Dashboard 与访问方式，以及所选能力必需的模型连接。
基础记忆通过 Agent 显式保存和全文召回，不要求独立模型 API；自动处理和语义检索分别需要对应的模型配置。
已有环境文件可以直接沿用，也可以按模块调整。

首次本地配置默认关闭 Dashboard 和认证。需要时启用 Dashboard，或显式设置
`POWERCONTEXT_SERVER_ACCESS_MODE=enforced` 和 `POWERCONTEXT_SERVER_AUTH_TOKEN` 开启认证。
远程配置会启用认证；修改已有配置时，接受默认选项会保留已有 Dashboard 和认证设置。

配置 Agent 时每次选择一个 Agent；完成后可以继续添加，已配置项不会再次出现。每个 Agent 可分别使用默认 Scope、绑定已有
Scope，或计划创建独立 Scope。独立 Scope 使用 `codex-<随机串>`、`claude-code-<随机串>` 形式的标题，但真正的
`scope_id` 必须使用 Server 创建后返回的不透明 ID，向导不会把标题冒充为 ID。

保存前会展示配置供你检查。命令只生成文件和后续操作说明，不启动 Server、安装 Agent 插件、迁移数据库，也不探测远程
存储或模型端点。它可以只读检查已有本地 SQLite 元数据，但这不代表部署兼容性已经验证。
保存完整记忆配置也不代表记忆提取已经跑通。

选择嵌入式 seekdb 且缺少依赖时，向导会先征求同意，再在后台增量安装；保存配置后若安装尚未结束，会显示活动进度并等待。
安装失败会给出手动安装命令。它不会因此自动启动服务。

需要保留原来的无模型基础模板时，使用：

```bash
powercontext config init --template --output .env
```

模板模式替换已有文件需要 `--force`；如果会移除推理设置或 provider 凭据，还会要求一次默认选择“否”的确认。
向导模式会预览所选改动并保留无关设置。两种模式在替换已有文件前都会创建备份。

在 macOS 和 Linux 上，生成的环境文件和备份均使用 `0600` 权限。通过向导的隐藏输入、环境或 secret manager 提供
provider 凭据，不要把它们写入命令行参数。

Windows 支持为 `experimental`。将文件用于个人服务前，按[部署 Server](../operate/deploy-server.md)限制其 ACL。

### 选择或修改 Web / Server 端口

Dashboard、HTTP API 和 MCP 共用一个 Server 监听端口，没有独立的 Dashboard 端口。
运行 `powercontext config init --output .env`，选择本地使用场景，在 **Dashboard 与访问** 中输入端口，例如 `18000`。
首次配置时，向导建议使用 `17429`；已有配置则以保存的端口为默认值。所选端口会显式写入配置文件，
运行时和 `--template` 的默认端口仍为 `8000`。允许范围为 `1–65535` 的整数。
向导会探测所选本机 Server 监听端口。若已被占用，可选择换一个端口或继续使用；继续使用时，必须在启动 Server 前
先停止占用进程，向导不会自动杀进程。探测不会预留端口，也不会检查另一台电脑上的 SSH 转发端口；
无法确认可用性时，会提示启动前自行检查。

修改已有文件时，选择 **只修改指定模块**，再选择 **Dashboard 与访问**，修改端口后选择 **查看并保存** 并确认。
也可以通过相同步骤恢复 `8000`。向导将端口保存为 `POWERCONTEXT_SERVER_HTTP_PORT`；
已有 Agent 的连接地址需要更新时，请重新配置 Agent 连接。

通过 `powercontext server run --env-file .env` 启动服务。如果 Server 已在运行，请先停止，再使用该文件重新启动；
保存文件不会自动重载或重启进程。选择 `18000` 后，Dashboard 入口为 `http://127.0.0.1:18000/`，
MCP 地址为 `http://127.0.0.1:18000/mcp`。CLI 参数和进程环境变量仍优先于文件中的配置。

远程访问时，公开 HTTPS URL 与内部监听端口相互独立。使用 SSH 转发时，分别选择 Server 端口和客户端转发端口，
然后执行向导生成的隧道命令。

### 为另一个 Agent 安装相同连接

安装入口为每个 Agent 统一解析一次连接地址，插件、MCP 配置和按 URL 绑定的凭据共用该结果。
默认发现当前目录的 `.env`，也可以显式指定文件；读取配置不会执行文件中的命令：

```bash
powercontext setup --env-file .env codex
powercontext setup --env-file .env pi
```

`--env-file` 必须放在 Agent 子命令之前。安装时会检查客户端地址与已有 Agent 配置是否冲突，不会静默覆盖。
没有客户端地址时，使用配置中的公开 URL，或根据 `POWERCONTEXT_SERVER_HTTP_PORT` 生成本地地址。
存在冲突时，可显式选择，例如 `powercontext setup --env-file .env codex --server-url http://127.0.0.1:18000`。
进程环境中冲突的 URL 变量仍需清除或修改，因为它们可能在运行时覆盖安装配置。

此流程统一连接地址，不迁移所有 Agent 的密钥存储；Hermes 和 OpenClaw 仍沿用各自的原生认证配置。
修改连接后，请重启已运行的 Agent。

## 2. 检查并校验

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

`config show` 会隐藏已识别的凭据。校验接受只包含 Server 设置的最小环境文件；配置 inference model 或依赖 inference
的 Runtime 功能时，还会检查 Runtime 组装，但不会输出机密。

## 3. 使用同一份配置启动

```bash
powercontext server run
```

`server run` 会发现当前目录的 `.env`。使用 `--env-file <path>` 可选择其他文件，使用 `--no-env-file` 可禁用文件加载。
配置优先级依次为 CLI 参数、进程环境变量、所选文件和默认值。命令会显示实际加载文件的绝对路径，但不会输出凭据。

保持 Server 运行，在另一个终端回到配置目录，加载向导生成的客户端配置后再检查：

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

这会为检查命令提供客户端地址和 Server Token；无需将模型 API key 加载到客户端环境。
接下来按 `.env.next-steps.md` 创建 Scope、安装插件，并按[快速开始](quickstart.md)验收真实记忆。

全部变量、默认值和优先级规则见[配置](../operate/configuration.md)。
