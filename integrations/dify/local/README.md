# 发布前的本地联调

本目录启动隔离的 Dify CE 开发环境，并通过真实 HTTP、plugin-daemon 和 SDK 进程验证 PC 集成。
Docker 运行 PostgreSQL、Redis、plugin-daemon 和 sandbox；API、Web、Agent runtime 和 PC 使用本地源码运行。
两个 PC 插件通过 SDK remote debugging 注册。调试注册不会验证 `.difypkg` 安装包或市场发布流程。

## 启动

在 PC 仓库根目录执行。Dify 默认位于同级 `../dify`，使用 `feat/agent-v2-mem`；PC 使用 `feat/dify-plugins`。
需要 Docker Desktop、uv、pnpm，以及已安装的 PC、插件、Dify API、runtime 和 Web 依赖。

```bash
uv sync --locked
uv sync --locked --project integrations/dify
# api 与 runtime 使用各自的锁文件；不要混用 SDK 的 Python 3.12 环境。
UV_PROJECT_ENVIRONMENT="$PWD/.powercontext-e2e/dify/venvs/api" uv sync --locked --project ../dify/api --no-dev --python 3.12
UV_PROJECT_ENVIRONMENT="$PWD/.powercontext-e2e/dify/venvs/agent" uv sync --locked --project ../dify/dify-agent --extra server --no-dev --python 3.13
pnpm --dir ../dify install --frozen-lockfile

python integrations/dify/local/prepare.py
docker compose -f integrations/dify/local/compose.yaml --profile agent-app up -d
python integrations/dify/local/run.py migrate
```

分别在前台终端启动各服务：

```bash
python integrations/dify/local/run.py api
python integrations/dify/local/run.py worker
python integrations/dify/local/run.py agent
python integrations/dify/local/run.py web
python integrations/dify/local/run.py pc
```

`run.py` 支持 `--dify`、`--api-env`、`--agent-env` 指定源码和虚拟环境路径。所有监听端口发布在本机 loopback。
Docker Desktop 内的 daemon/sandbox 通过 `host.docker.internal` 访问宿主机 API；原生 Linux 需另行配置可达地址。
本环境使用单独数据库、数据卷和工作区，不读取其他应用的凭据。

API 启动后，初始化测试管理员并获取调试密钥：

```bash
uv run python integrations/dify/local/console.py
# 分别保持这两个进程运行。
python integrations/dify/local/run.py pc-tools
python integrations/dify/local/run.py pc-agent
```

两个插件注册成功后配置提供方：

```bash
uv run python integrations/dify/local/register_provider.py
```

| 服务 | 地址 |
| --- | --- |
| Dify Web | http://127.0.0.1:31300 |
| Dify API | http://127.0.0.1:31501 |
| plugin-daemon / 调试连接 | 127.0.0.1:31502 / 31503 |
| sandbox | http://127.0.0.1:31504 |
| Agent runtime | http://127.0.0.1:31505 |
| PC Server / Dashboard | http://127.0.0.1:31800 |

管理员邮箱为 `pc-dify@example.com`。密码位于 `.powercontext-e2e/dify/credentials.json` 的 `ADMIN_PASSWORD`；
PC Dashboard 使用同文件的 `PC_TOKEN`。本地密钥文件权限为 0600，运行数据与密钥均被 Git 忽略。

## 实际链路验收

以下脚本使用运行中的服务，无 HTTP transport stub 或内存数据库：

```bash
uv run python integrations/dify/local/live_workflow.py
uv run python integrations/dify/local/live_users.py
uv run python integrations/dify/local/live_native_config.py
```

- `live_workflow.py`：创建 Console 工作流，通过实际工具节点写记忆、召回、隔离业务作用域、验证缺失绑定、
  采集 Source，并从 PC HTTP API 读回检查脱敏。
- `live_users.py`：仅在本地测试工作区发布工作流，通过 Service API 的 Alice/Bob 两个真实用户验证跨请求记忆隔离。
- `live_native_config.py`：保存并重新读取 V2 外部记忆配置，验证凭据引用保留且 PC 令牌未进入配置。

工作流节点的常量输入经过 Dify 文本模板处理，因此对象常量使用 **JSON 字符串**，避免 Python 字典被转成单引号文本。
Agent 回调直接通过 daemon 传递 JSON 对象，不经过这个工具节点转换路径。

Service API 的 `sys.user_id` 是调用方传入的标识，插件收到的是 Dify `EndUser.id`。管理员可从
`GET /console/api/apps/{app_id}/workflow-runs/{run_id}` 的 `created_by_end_user.id` 取得实际绑定身份。
不要把 `sys.user_id` 直接当作插件的内部 user ID。业务模式则使用配置中的固定业务标识。

验收事件保存在 `.powercontext-e2e/dify/workflow-events.json`、`user-events.json`、`native-config.json`。
测试应用和 Scopes 会保留，方便在界面检查。重复执行会产生新的测试记忆和运行记录。

## 模型驱动的验收

基础设置不带模型。配置 Dify 模型提供方，以及 `.powercontext-e2e/dify/pc.env` 中的 PC generation/embedding 设置后，
重启 PC，再执行上级 `ACCEPTANCE.md` 中的传统 Agent、V2 自动记忆、提取、压缩、取消和恢复场景。
配置变量见 `docs/zh/docs/get-started/configure-models.md`。仅有显式 `remember_memory` 成功，不能证明采集后的提取已完成。

SDK 调试连接、正式 `.difypkg` 安装和市场发布是三个验收阶段。必须在实际模型场景和安装包验收完成后，才将结果用于发布判断。

## 停止

在各服务前台终端按 Ctrl-C，然后执行：

```bash
docker compose -f integrations/dify/local/compose.yaml --profile agent-app down
```

该命令保留数据卷。需要继续验收时可再次启动，不必重新初始化工作区。
