---
title: 配置
description: PowerContext 路径、Server、Client、推理和 Agent 集成环境变量。
---

# 配置

PowerContext 进程启动时从环境变量读取配置。CLI 不会自动搜索 `.env` 文件。接受 `--env-file` 的命令会从该文件加载环境变量（包括
Server 与 provider 设置），并覆盖进程中的同名值。Agent 宿主可按自身规则加载环境文件。

生成、脱敏查看、校验和启动配置文件的完整流程见[配置 Server 环境](../how-to/configure-server-environment.md)。所有环境
文件都应视为包含机密的部署产物。

`service install` 还要求该文件是当前用户拥有的普通非符号链接文件，且 group 和 other 均无访问权限。服务会记录文件
身份；文件被替换或其 owner、权限、内容发生变化后会拒绝启动。确认修改是预期行为后，请重新执行 `service install`。

## 用户数据

`POWERCONTEXT_HOME` 可覆盖已安装 Server 使用的数据目录：

```bash
export POWERCONTEXT_HOME=/srv/powercontext
```

未覆盖时，默认目录为：

- Linux：`$XDG_DATA_HOME/powercontext`，未设置时为 `~/.local/share/powercontext`；
- macOS：`~/Library/Application Support/powercontext`。

默认 SQLite 数据库是该目录下的 `powercontext.db`。启用定时处理时，调度状态保存在同一目录的
`scheduler.db`。

## Server

Server 配置使用 `POWERCONTEXT_SERVER_` 前缀。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | 监听地址 |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | 监听端口 |
| `POWERCONTEXT_SERVER_WORKSPACE` | Server 启动目录 | 本机项目级 Agent Skill 目录的解析根目录 |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | 启用 Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP 路径 |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | HTTP 和 MCP 是否要求一个静态 Bearer token |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | 未设置 | 静态 Bearer token；启用鉴权时必须设置 |
| `POWERCONTEXT_SERVER_PUBLIC_URL` | 未设置 | 远端技能注册引导使用的可达基础地址；默认要求 HTTPS |
| `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP` | `false` | 显式允许远端技能接收端接口和注册引导使用明文 HTTP |
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | 在鉴权关闭时显式允许绑定非 loopback 地址 |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `true` | 在 Server 根路径 `/` 启用 Dashboard |
| `POWERCONTEXT_SERVER_DASHBOARD_SCOPES` | `[]` | Dashboard 可选择的 scope JSON 数组 |
| `POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED` | `true` | 启用 Handoff Report 及其 API route |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | operational log 级别 |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` 或结构化 `json` 输出 |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | 记录外部 HTTP 和逻辑 MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | 在 `/metrics` 暴露 Prometheus metrics |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | 启用 span recording 和 OTLP export |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | 存储后端：`sqlite`、`seekdb` 或 `oceanbase` |
| `POWERCONTEXT_SERVER_DATABASE_URL` | 用户数据目录下的 SQLite 文件 | SQLite 或 OceanBase 的 SQLAlchemy 异步 URL；seekDB 不设置 |
| `POWERCONTEXT_SERVER_DATABASE_PATH` | 用户数据目录下的 `seekdb` 目录 | 嵌入式 seekDB 路径；仅在 `DATABASE_KIND=seekdb` 时使用 |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Runtime 保留的非活动 scope composition 数量；进行中的 scope 不会被驱逐 |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | 单次 activation 最多处理的 Source 数量 |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory 选择策略：`coding` 或 `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | 在 Memory 粗召回后应用 listwise rerank |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | 交给 reranker 的粗排候选池大小 |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | 未设置 | Scheduler 间隔；未设置即不启用 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | 未设置 | 配置的 extraction、generation、Handoff 和 rerank 操作共用的 Pydantic AI 模型 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` | provider 默认值 | 自定义 generation provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` | `{}` | generation client 静态 header JSON object；value 按 secret 处理 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS` | `{}` | Pydantic AI generation model settings JSON object |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | 单次结构化 generation 操作的超时秒数 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS` | `2` | 单次结构化 generation 操作最多发起的 provider 请求数，包含重试 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL` | 未设置 | Pydantic AI embedding model；必须同时设置 profile ID 和 dimension |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL` | provider 默认值 | 自定义 OpenAI-compatible embeddings base URL |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS` | `{}` | embedding client 静态 header JSON object；value 按 secret 处理 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL_SETTINGS` | `{}` | Pydantic AI embedding model settings JSON object |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID` | 未设置 | vector index 使用的模型、dimension 和 normalization 的稳定标识 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION` | 未设置 | 向 embedding model 请求并校验的正整数输出维度 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` | `unit` | vector normalization：`unit` 或 `none` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS` | `30` | 单次 embedding 请求的超时秒数 |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE` | `10` | 单次 embedding 请求最多发送的文本数量 |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL` | generation model | LLM rerank 可选的独立 Pydantic AI model |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL` | 继承值或 provider 默认值 | 自定义 LLM reranker provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS` | `{}` | LLM reranker client 静态 header JSON object；value 按 secret 处理 |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL_SETTINGS` | `{}` | Pydantic AI reranker model settings JSON object |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_TIMEOUT_SECONDS` | generation 超时 | LLM reranker 超时 |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MAX_REQUESTS` | generation request limit | 单次 rerank operation 的最大 model request 数量 |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | 未设置 | Experience 孵化间隔；未设置即不启用该 job |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | 自动生成本机项目 target | 覆盖默认值的 host identity 和显式 Agent Skill targets JSON object |

静态 Bearer 鉴权默认关闭。启用后，API 和 MCP 请求必须携带 `Authorization: Bearer <token>`；liveness 和
readiness endpoint 仍然公开。明文 HTTP 仅在 loopback 地址（`localhost`、`::1` 及 `127.0.0.0/8` 网段内的任意
地址）上受信任。当 Server 绑定到非 loopback 地址且鉴权关闭时会拒绝启动；此时应启用鉴权、改回绑定 loopback，或在
TLS 由上游终止或网络本身受控的场景下，
显式设置 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` 主动选择接受。通过网络暴露启用鉴权的
Server 前必须配置 TLS。

Python Client 和 CLI 对一般出站请求应用相同规则：配置的明文 `http://` Server URL 仅接受 loopback 主机；远端 Skill
Receiver 的内部 PoC 显式例外见下文。当代码的 `http://` base URL 只是路由标签、实际传输是安全的，例如进程内 ASGI
应用、Unix domain socket 或由代理终止 TLS 时，必须自行传入 `http_client` 并显式设置
`trust_transport_security=True`。

安全的 Docker 和远程访问配置见[部署 Server](../how-to/deploy-server.md)。

Dashboard 默认启用，并与 HTTP API、MCP 共用监听地址和端口。默认未配置 scope，页面会显示空状态；Dashboard
初始化失败只记录包含直接原因的 warning，不影响 Server 的 HTTP API、MCP 和健康检查启动。

Server 默认把启动目录作为 workspace，并自动提供两个可写的本机项目级目标：Codex 使用
`<workspace>/.agents/skills`，Claude Code 使用 `<workspace>/.claude/skills`。目录不存在时不会报错；用户首次在
Dashboard 中确认安装后才会创建目录。以 systemd、容器或其他不保证工作目录的方式启动时，应设置一次
`POWERCONTEXT_SERVER_WORKSPACE`，之后页面不再要求用户填写 Skill 路径。

远端技能接收端需要通过不同于当前 Dashboard 访问地址的外部入口连接时，只需在 Server 上配置一次
`POWERCONTEXT_SERVER_PUBLIC_URL`。Skills Dashboard 会自动用它生成注册命令，不再要求每次添加目标时填写地址。
未配置时，Dashboard 自动使用当前 HTTPS 来源；显式启用不安全开关后，也可以使用当前 HTTP 来源。两者都不可用时，
注册命令使用远端命令行已经配置的服务地址。

一期 PoC 如果运行在受保护的内部测试网络，可以让 Server 和 Receiver 双端显式同意直连 HTTP：Server 设置
`POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true`，并用 `POWERCONTEXT_SERVER_PUBLIC_URL` 公布 `http://` 地址；
Receiver 注册时同时传入 `--allow-insecure-http`。Dashboard 会显示明文传输警告，并自动把该参数加入注册命令。
Server 未打开开关时，远端接口仍拒绝非 loopback HTTP；Receiver 未传参数时，CLI 会在发送一次性注册口令之前拒绝
该 URL。许可会写入权限为 owner-only 的 Receiver 配置，因此 `remote-watch` 和 systemd user service 会沿用同一策略，
unit 文件不需要保存凭据或额外参数。该开关不提供 TLS、网络隔离或防窃听能力，不能用于公网或不可信网络；长期部署
应使用 HTTPS。

```bash
export POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
export POWERCONTEXT_SERVER_PUBLIC_URL=http://powercontext.internal.example:8765
export POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true
powercontext server run

# 在远端项目中：
powercontext --server-url http://powercontext.internal.example:8765 \
  skill remote-enroll --workspace "$PWD" --install-service --allow-insecure-http
```

示例中的非 loopback opt-in 与 Receiver 传输例外彼此独立：它表示操作者接受该监听器上的所有 Server route 在没有
Server 级 Bearer token 时可达。部署条件允许时，应优先启用鉴权，或在仅绑定 loopback 的 Server 前终止 TLS。

启用 Bearer 鉴权后，`/`、`/skills`、`/reviews`、`/handoff-reports` 的 HTML 外壳及其静态资源仍保持公开，以便
浏览器渲染登录表单；数据请求仍受鉴权保护。在表单中输入 Server token 后，浏览器只把它保存在当前标签页的 session
storage 中。如果连这些登录页也不能暴露，应同时关闭 Dashboard 和 Handoff Report。

Handoff Report 独立默认启用，路径为 `/handoff-reports`。没有任何 scope 包含 committed Handoff 时，页面显示无数据
模板预览。Scope discovery、检查、Revision 写入和导出步骤见[使用 Handoff Report](../how-to/use-handoff-report.md)。

指定 SQLite 路径并启用定时提取的示例：

```bash
export POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:////srv/powercontext/runtime.db
export POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

`OPENAI_API_KEY` 等 provider 凭据由所配置的推理 provider 读取。不要把密钥放入命令行参数、文档或
Memory。请把 `provider:model-name` 替换为 Pydantic AI 支持的模型标识。定时提取需要同时配置 generation
model 和 `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS`；显式 Memory 写入不需要这两项配置。

默认的 `coding` 抽取 profile 保留跨任务工作上下文，例如偏好、决策、约束、昂贵事实和未完成进度。当产品
需要从对话证据中保留可独立回答的人物事实、关系、事件、精确日期、列表和历史状态时，可选择
`conversation`：

```bash
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE=conversation
```

profile 只影响后续 Source 处理，不会重新解释已有的 Memory revision。

当宽范围 Hybrid recall 比一次额外结构化 generation request 的延迟和 token 成本更重要时，可以启用面向回答的 Memory
rerank：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT=30
```

Rerank 默认关闭。启用后，Runtime 会召回并融合配置的候选池，再使用 temperature 为 0 的 generation model，选择不超过
search request 最终 `limit` 的结果。它不会修改已存储 Memory 或索引。Provider 与结构化输出失败仍作为 inference error
显式返回；如果搜索必须独立于模型可用性，请关闭 rerank。算法、并发与 API 边界见
[RFC 0080](/zh/rfcs/0080_memory_search_reranking/)。

内置 reranker 是 LLM listwise reranker，不是独立的 cross-encoder protocol。默认复用 generation model 及其 provider
settings。设置 `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL` 后，该 LLM operation 可以使用独立的 model、base URL、
headers、settings、timeout 和 request limit。

同一个 generation model 也控制显式 Experience generation、managed Skill generation，以及语义化的 Skill
fork/evolution。External Skill 精确导入和完整 package 上传不使用模型：PowerContext 会校验并保存 canonical package
bytes，再创建 package digest 完全相同的 pending Candidate。未配置模型时，语义生成会在持久化 Candidate 前返回
capability error；Review、package 检查与下载、精确导入、usage recording 和 external Skill scan/list/resolve 仍可使用。

Experience 孵化使用独立的 APScheduler job 和持久化 Source cursor。每次 activation 固定检查最多 32 条 Source，并且只把 metadata 包含 `"kind": "task-outcome"` 的 Content Source
暴露给模型。该 job 会在 Review Inbox 中创建 pending Experience Candidate；它不会自动批准、进入
PreparedContext、创建 managed Skill、将它导出到 Agent target 或执行任何内容。Memory 和 Experience job 共用
`POWERCONTEXT_HOME` 下的 APScheduler sidecar，但拥有独立的 job identity 和业务 cursor；取消其中一个 interval
只会移除对应 job。
设置与验证步骤见[创建并审核 Experience](../how-to/create-and-review-experience.md)。

### Agent Skill 目标

零配置流程使用上述 workspace 中的 Codex 和 Claude Code 项目级目录。只有需要自定义路径、用户级 target、环境兼容性
事实或显式关闭本机发现时，才需要通过一个 JSON 值覆盖默认的 host-local target。基础 JSON 结构和验证流程见
[配置 Agent Skill target](../how-to/configure-agent-skill-targets.md)。包含兼容性信息的覆盖示例如下：

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills",
      "allow_managed_publish": true,
      "environment": {
        "operating_system": "linux",
        "architecture": "x86_64",
        "commands": {"python": "3.13.2", "bash": "5.2"},
        "network_policy": "restricted",
        "writable_roots": ["workspace"],
        "dependency_install_policy": "denied",
        "environment_names": ["CI"]
      }
    },
    {
      "target_id": "claude-project",
      "agent_kind": "claude_code",
      "installation_scope": "project",
      "path": "/srv/project/.claude/skills",
      "allow_managed_publish": true
    }
  ]
}'
```
显式设置 `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` 会完整替换自动生成的两个项目级 target；设置为
`{"host_id": null, "targets": []}` 可以关闭本机发现和发布。每个 target ID 必须唯一；`agent_kind` 支持 `codex` 和
`claude_code`，installation scope 支持 `user`、`project` 和 `plugin`。PowerContext 只扫描默认或显式 target 的直接
Skill package 子目录，不会推断用户 home 目录、安装 package 或授予执行权限。自动生成的两个项目级 target 允许用户
在 Dashboard 中显式安装；自定义 target 的 `allow_managed_publish` 默认是 `false`，设为 `true` 后，authenticated Skills Library 或 Review
页面可以把 approved managed Skill 显式创建或安全更新到该 target。页面仍不能提交任意路径，也不会覆盖外部或
已被修改的 package。发布会物化 Review 通过的完整精确 package（包括 scripts 和 references），不会执行其中内容，
也不会向 package 注入 sidecar。相同页面只能在 binding 与 tree digest 仍匹配时安全取消发布；本地漂移和外部内容
会保持不动。`host_id`、locator 和 registration 都是本地环境状态，不是跨 host contract。已有的
`codex_roots` 配置继续作为 Codex-only 兼容格式被接受；新配置应使用 `targets`。

可选的 `environment` object 只包含已观测且不含密钥的兼容性事实。Command value 是版本标签；
`environment_names` 只记录名称，绝不记录值。PowerContext 不会为了构造该 profile 而探测或执行 package script。
未配置时，包含 script 的 package 会显示未知兼容性；配置后，Skills Library 会把已知 script interpreter 与已观测
command name 对比，并显示带原因的 Assessment。Assessment 不会授予 network、filesystem、dependency install 或
environment 访问权。

Server 始终创建 non-recording OpenTelemetry request context，从 inbound span 派生 `X-PowerContext-Request-ID`。如需为
CLI 管理的 Server 启用 recording 和 export，请安装 `powercontext[cli,server,tracing-otlp]`、启用 tracing，
并使用 `OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS` 和 `OTEL_SERVICE_NAME` 等标准
OpenTelemetry 环境变量进行配置。不使用 `powercontext` command 的 programmatic Server integration 可以省略
`cli` extra。

启用 tracing 后，PowerContext 自己构造的 generation 与 embedding 调用也会产生 span，且不记录 prompt、模型响应、
Memory 内容或向量。可运行的配置见 [用 Phoenix 查看 trace](../how-to/trace-with-phoenix.md)。

使用 OceanBase 时，通过环境或 secret manager 提供 URL：

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase
export POWERCONTEXT_SERVER_DATABASE_URL="$OCEANBASE_URL"
```

URL 必须使用 `mysql+aoceanbase` driver，包含明确的端口和数据库，并设置 `charset=utf8mb4`。对应
tenant 必须使用 MySQL 兼容模式。

### Embedding 与 SQLite 向量检索

Vector search 需要全部三个 embedding identity 变量：model、稳定 profile ID 和正数 dimension。normalization 默认
为 `unit`；timeout 和 batch size 是可选控制项。SQLite vector 和 hybrid search 使用内置 sqlite-vec extension。Server
打开数据库时会探测它，已安装的 library 与 platform 或 SQLite build 不兼容时启动会失败。没有 embedding profile 时，
full-text search 仍可用。配置和 capability 验证步骤见[配置向量检索](../how-to/configure-vector-search.md)。

## CLI Server 连接

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | 未设置 | 发送给启用鉴权的 Server 的 Bearer token |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP 超时秒数 |

`powercontext` 为 Server URL 和 timeout 提供对应的单次命令参数。Token 只能通过环境变量提供，避免出现在
命令行参数中。

## Codex 插件

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | 未设置 | Hook 与 MCP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Hook 共享 HTTP 时间预算 |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | 每个提示词最多执行的 flush 次数 |

Codex Hook 外层超时为十秒。Server 不可用或拒绝鉴权时，恢复、采集和 flush 独立降级，不会阻塞 Codex。
该变量必须存在于启动 Codex 的进程环境中；修改后需要重启 Codex。

## Claude Code 插件

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLAUDE_SERVER_URL` | `http://127.0.0.1:8000` | Hook 使用的 Server base URL |
| `POWERCONTEXT_CLAUDE_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_CLAUDE_AUTHORIZATION` | 未设置 | Hook 与 MCP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS` | `true` | 把用户 prompt 采集为普通 Source 证据 |
| `POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS` | `4` | 召回、采集和可选 flush 共用的 Hook HTTP 时间预算 |
| `POWERCONTEXT_CLAUDE_FLUSH_MAX_CALLS` | `4` | 每个 prompt 最多执行的 flush 次数；有效值为 1 到 16 |

`powercontext setup claude-code` 会把 `server_url` 和 `capture_prompts` 保存为非敏感的 Claude Code 插件
选项。启动 Claude Code 的进程中，对应的 `POWERCONTEXT_CLAUDE_*` 环境变量优先级更高。
Authorization 只能来自环境变量，不能加入 Server URL 或插件选项。

`UserPromptSubmit` Hook 的外层超时为十秒。召回与采集共用一个 wall-clock 时间预算，但会独立降级。
明文 HTTP 只允许连接 loopback endpoint；远程 Server 必须使用 HTTPS。修改环境变量后需要重启 Claude Code。

## DeepSeek Harness 插件

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | 插件使用的 Server 地址 |
| `POWERCONTEXT_DSH_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_DSH_AUTHORIZATION` | 未设置 | 插件 HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |

`timeoutMs`、`requestTimeoutMs`、`maxBytes` 和 `flushMaxCalls` 是插件 patch 配置。Server 不可用时，召回和采集会降级；修改这些变量后需要重启 `dsh web`。

## Pi package

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL；非 loopback endpoint 必须使用 HTTPS |
| `POWERCONTEXT_PI_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_PI_AUTHORIZATION` | 未设置 | package HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | 把符合条件的用户提示词采集为 Source 证据 |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `1000` | 单请求超时，单位毫秒 |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `4000` | 召回/采集共享 HTTP 时间预算，单位毫秒 |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | 请求并校验的 PreparedContext byte 上限（`512`–`32768`） |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | 在 prompt hook 中等待已采集 Source 的处理 |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | 一个 pending Source 最多 flush 次数 |

Pi 会拒绝包含凭据、query 或 fragment 的 base URL。召回、采集和边界 flush 都会正常降级；显式 `pc_*` 持久化写入
必须确认，Pi 没有交互 UI 时会被拒绝。修改这些变量后需要重启 Pi。

## 其他 Agent 集成

部分集成使用自己的配置文件或环境变量前缀，具体指南是这些设置的准确信息源：

- [Hermes](../how-to/configure-hermes.md)
- [LangChain](../how-to/configure-langchain.md)
- [LangGraph](../how-to/configure-langgraph.md)
- [OpenClaw](../how-to/configure-openclaw.md)
- [OpenCode](../how-to/configure-opencode.md)
- [Pydantic AI 适配器预览](../how-to/configure-pydantic-ai.md)
- [WorkBuddy](../how-to/configure-workbuddy.md)
