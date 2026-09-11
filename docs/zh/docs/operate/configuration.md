---
title: 配置选项
description: PowerContext 路径、Server、Client 和推理环境变量。
---

# 配置选项

Windows 支持为 `experimental`。

PowerContext 进程启动时从环境变量读取配置。当前工作目录存在 `.env` 时，`server run` 会自动加载该文件。使用
`--env-file <path>` 可改为加载指定文件且不再合并 `.env`；使用 `--no-env-file` 可禁用文件加载。`server run` 的配置
优先级为：CLI 参数、进程环境变量、所选环境文件、默认值。Agent 宿主可按自身规则加载环境文件。

生成、脱敏查看、校验和启动配置文件的完整流程见[配置 Server 环境](../get-started/configure-server-environment.md)。所有环境
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
- macOS：`~/Library/Application Support/powercontext`；
- Windows：`%LOCALAPPDATA%\\powercontext`。

默认 SQLite 数据库是该目录下的 `powercontext.db`。后台处理器的意图与调度检查点保存在同一数据库中。
已有部署须先完成[停机迁移](artifact-processing-migration.md)。

## Server

Server 配置使用 `POWERCONTEXT_SERVER_` 前缀。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | 监听地址 |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | 监听端口 |
| `POWERCONTEXT_SERVER_WORKSPACE` | Server 启动目录 | 本机项目级 Agent Skill 目录的解析根目录 |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | 启用 Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP 路径 |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `false` | 个人与演示 Dashboard；要求静态 Bearer 鉴权，不支持注入认证或授权 Provider |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | 旧静态 Bearer 兼容开关；`true` 自动映射为 `ACCESS_MODE=enforced`，并要求设置 `AUTH_TOKEN` |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | 未设置 | 旧静态 Bearer token；未注入 Authentication Provider 时作为兼容认证并映射为内置管理员 |
| `POWERCONTEXT_SERVER_ACCESS_MODE` | `disabled` | 唯一正式 Access 开关：`disabled` 或 `enforced` |
| `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` | `powercontext` | `server` Access Resource 使用的稳定部署标识 |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID` | 未设置 | 多用户 enforced 部署中供定时任务使用的显式 service Principal |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_DESCRIPTION` | 未设置 | 定时 service Principal 的可选展示描述 |
| `POWERCONTEXT_SERVER_PUBLIC_URL` | 未设置 | 远端技能注册引导使用的可达基础地址；默认要求 HTTPS |
| `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP` | `false` | 显式允许远端技能接收端接口和注册引导使用明文 HTTP |
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | 在鉴权关闭时显式允许绑定非 loopback 地址 |
| `POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED` | `true` | 启用 Handoff Report 及其 API route |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | operational log 级别 |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` 或结构化 `json` 输出 |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | 记录外部 HTTP 和逻辑 MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | 在 `/metrics` 暴露 Prometheus metrics |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | 启用 span recording 和 OTLP export |
| `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` | 本地持久化密钥 | 用于签名 REST 分页 cursor 的共享密钥，至少 32 字节 |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | 存储后端：`sqlite`、`seekdb` 或 `oceanbase` |
| `POWERCONTEXT_SERVER_DATABASE_URL` | 用户数据目录下的 SQLite 文件 | SQLite 或 OceanBase 的 SQLAlchemy 异步 URL；seekdb 不设置 |
| `POWERCONTEXT_SERVER_DATABASE_PATH` | 用户数据目录下的 `seekdb` 目录 | 嵌入式 seekdb 路径；仅在 `DATABASE_KIND=seekdb` 时使用 |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Runtime 保留的非活动 scope composition 数量；进行中的 scope 不会被驱逐 |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | 单次 activation 最多处理的 Source 数量 |
| `POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES` | `8` | 显式 `assembly.sections[].limit` 之和的上限；正整数，各类别单独上限仍适用 |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory 选择策略：`coding` 或 `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | 在 Memory 粗召回后应用 listwise rerank |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | 交给 reranker 的粗排候选池大小 |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS` | 未设置 | Memory 自动准入间隔；`SCHEDULE_SECONDS` 保留为兼容别名 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` | 未设置 | Topic Memory 自动准入间隔；未设置时不接纳新的自动调用 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SOURCE_WINDOW_LIMIT` | `10` | 每个 Topic Memory Window 的 Source 数量上限，硬上限为 100；一次 Scope 调用可完成多个 Window |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MAX_CANDIDATES` | `20` | 处理时考虑的历史 Topic 候选上限 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_RRF_THRESHOLD` | `70` | 归一化到 `0..100` 的 RRF 接受阈值 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MIN_CANDIDATES` | `5` | 达到阈值的候选过少时保证的最小历史召回数 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_MAX_WORKERS` | `10` | Topic 独立 Worker 额度；`ARTIFACT_PROCESSING_MAX_WORKERS` 是其兼容别名 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | 包括启动的 Scope 调用总超时；旧 `ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` 是其兼容别名 |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE` | `all` | 进程角色：`all`、`api` 或 `background` |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE` | `global` | `global` 一条 Lease；`dedicated` 每个注册 Family 一条 Lease |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES` | 根据模型推导 | JSON Family 列表；API 端可无模型凭据地声明处理能力 |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_WORKERS` | `1` | Memory 独立 Worker 额度 |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_MAX_WORKERS` | `1` | Experience 独立 Worker 额度 |
| `POWERCONTEXT_SERVER_RUNTIME_SKILL_MAX_WORKERS` | `1` | Skill Worker 并发额度，用于 Dream 派生 |
| `POWERCONTEXT_SERVER_RUNTIME_SKILL_WORKER_TIMEOUT_SECONDS` | `600` | 一次 Skill Scope 调用的总超时 |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_ENABLED` | `true` | 接受已声明 Experience/Skill Family 的显式 Dream 请求；不自动挑选制品 |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_MAX_PENDING_PER_SCOPE` | `32` | 同 Scope 排队和执行中的 DreamRun 总上限 |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_BUDGET` | `{}` | JSON 预算，可收紧证据上限、最多 2 次模型调用和首次执行起 120 秒总时间 |
| `POWERCONTEXT_SERVER_RUNTIME_GENERATION_CONCURRENCY` | `4` | Runtime 前台同步生成并发；后台 Worker 使用各 Family 额度 |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS` | `4` | Profile 独立 Worker 额度；别名 `PROFILE_MAX_CONCURRENCY` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | Memory Scope 总超时 |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_WORKER_TIMEOUT_SECONDS` | `600` | Experience Scope 总超时 |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_WORKER_TIMEOUT_SECONDS` | `600` | Profile Scope 总超时 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | 未设置 | 配置的 extraction、generation、Handoff 和 rerank 操作共用的 Pydantic AI 模型 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` | provider 默认值 | 自定义 generation provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` | `{}` | generation client 静态 header JSON object；value 按 secret 处理 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS` | `{}` | Pydantic AI generation model settings JSON object |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | 单次结构化 generation 操作的超时秒数 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS` | `2` | 单次结构化 generation 操作最多发起的 provider 请求数，包含重试 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_CONTEXT_WINDOW_TOKENS` | `125000` | Topic 处理预算使用的 generation model 总上下文窗口 |
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
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | 未设置 | Experience 自动准入间隔；未设置时保留已接受工作，停止新的自动准入 |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | 自动生成本机项目 target | 覆盖默认值的 host identity 和显式 Agent Skill targets JSON object |

Topic Worker 对尚未推进的 Scope Cursor 强制使用持久额度：跨全部重试最多 3 次尝试、512 次预留 provider 请求和
64,000,000 个估算 token 容量单位。Window 的 canonical evidence（包含 metadata）最多 4,194,304 个字符，并限制
嵌套复杂度。耗尽后保留 Source、Cursor、Pending 和同 Scope 尾部，停止后续 provider 调用；flush 和重启均不重置。
运维可检查 `pc_topic_memory_work_budgets` 与结构化错误以明确修复。

Topic generation 只允许有界标量设置：`max_tokens`、`temperature`、`top_p`、`top_k`、`seed`、`presence_penalty`、
`frequency_penalty`、`timeout`、`openai_reasoning_effort`、`openai_text_verbosity`、`service_tier`、
`openai_service_tier`、`anthropic_service_tier`、`anthropic_effort`；Topic Embedding 只允许 `dimensions` 和 `truncate`。
background、隐藏历史、native tools 和 `extra_body` 会使 Topic 处理不可用，普通推理仍可继续；显式配置自动 Topic 调度时
则启动失败。支持的 provider 前缀为 `openai`、`openai-chat`、
`openai-responses`、`anthropic`、`azure`、`azure-responses`、`deepseek`、`openrouter`，以及本地 `test` 模型；Embedding
还必须受其 SDK adapter 支持。Topic 禁用 SDK transport 重试和自动 continuation，非 Topic 推理保留既有设置行为。

未设置 cursor 签名密钥时，使用文件 SQLite 的 Server 会在数据库旁创建权限受限的密钥文件；其他持久化后端会在
PowerContext 用户数据目录创建密钥。内存 SQLite 使用进程内密钥。多副本部署必须为所有副本配置相同的
`POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET`，这样重启或下一请求落到其他副本后，已签发 cursor 仍然有效。
在已签发 cursor 仍需有效期间，不要泄漏或轮换该值。

Access Control 默认关闭。在 `enforced` 模式下，API 和 MCP 请求必须通过所选 Authentication Provider 建立 Principal；
liveness 和 readiness endpoint 仍然公开。内置 `static-bearer` Provider 接受
`Authorization: Bearer <token>`。明文 HTTP 仅在 loopback 地址（`localhost`、`::1` 及 `127.0.0.0/8` 网段内的任意
地址）上受信任。当 Server 绑定到非 loopback 地址且鉴权关闭时会拒绝启动；此时应启用鉴权、改回绑定 loopback，或在
TLS 由上游终止或网络本身受控的场景下，
显式设置 `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` 主动选择接受。通过网络暴露启用鉴权的
Server 前必须配置 TLS。

`POWERCONTEXT_SERVER_ACCESS_MODE` 是唯一正式开关。`disabled` 在可信本地边界内跳过授权决策；`enforced` 启用统一策略执行点、
Binding 和审计。Authorization 默认使用 builtin，实现替换通过 `create_server_app(access_control=...)` 注入；Authentication
通过 `create_server_app(authentication_provider=...)` 注入。若没有注入 Authentication Provider，Server 只接受旧
`AUTH_TOKEN` 作为静态 Bearer 兼容认证，并把固定的 `server-token` Principal 初始化为内置管理员。两者都没有时拒绝启动。
旧 `AUTH_ENABLED=true + AUTH_TOKEN` 配置会自动映射为 `ACCESS_MODE=enforced`。

Authentication 负责建立 Principal，Access Control 负责判断该 Principal 能做什么。Principal ID 是部署内全局唯一且不复用
的标识；`description` 只用于展示，不参与身份判定。内置静态 token 始终只代表一个 service Principal，因此不能区分
用户 A 和用户 B。兼容静态 token 会为这个 Principal 显式写入 Server 与各 scope 所需的 role。需要让不同用户或 group
获得不同权限时，应注入部署侧 Authentication Provider 与相应的 AccessControlService。

Memory、Topic Memory、Experience、Profile 的 Source 后台处理优先使用 `ACCESS_BACKGROUND_PRINCIPAL_ID` 指定的 service Principal，
缺省时回退到固定静态 Principal。该身份须在每个被处理的 scope 上拥有 `scope.contribute`，并拥有被修改的现有 Artifact 的写权限。
新 Entry、Artifact 与 Candidate 的 owner 或 owner attestation 和处理完成确认同事务提交。
enforced 部署启用后台能力时，若身份或授权 provider 无法在子进程重建，启动会失败；关闭自动 schedule 仍需恢复已接受的工作，
因此不能免除此检查。内置 provider 支持重建；注入的 provider 和模型对象仍可用于关闭后台能力
（`ARTIFACT_PROCESSING_FAMILIES=[]`）的同步 SDK/Server 操作。

Dream 在 Experience/Skill Worker 内重建原请求者的当前权限，Candidate 的归属仍是原请求者。
后台 service Principal 的权限不会替代该身份。OceanBase 分离部署可在无模型的 API 进程声明
`ARTIFACT_PROCESSING_FAMILIES=["experience","skill"]`，后台声明相同能力并配置模型；各进程的自动周期只能引用已声明的 Family。
SQLite 使用 `all`。Dream 的模型标识在首次执行时固定，关闭自动周期不会阻止已接受的显式 Dream 请求完成。

未配置 Server 身份的 SDK Worker 不需要 Server 授权依赖。内置后台 Worker 使用内置 Source Definition。
自定义 Source Registry 须为每个启用的 Family 提供自定义 processing binding，或通过
`ARTIFACT_PROCESSING_FAMILIES=[]` 关闭内置后台 Family；否则启动在接受工作前失败。
仅关闭 schedule 不足以满足要求，因为显式请求仍会启动 Worker。同步 SDK Context 和纯 API 组合仍支持自定义 Source Registry。

受鉴权保护的 `/metrics` 暴露 `powercontext_server_artifact_processing_*` 指标，只使用 `family` 标签，涵盖 Worker 额度、
ready/retry 队列、未确认 Scope 数、发现与调用耗时，以及完成、失败、超时次数。未确认数反映最近一次发现结果；计数器随
Supervisor 实例重建而重置。

远程和多用户部署必须使用 `enforced`。此模式下，HTTP、MCP 和 metrics 共用同一个 Server PEP。`/v1/access/me` 返回
`server`/`scope`/`artifact` Resource Kind、Provider 的 batch/list/relationship 能力与 Family profile。Managed Skill 的
导出和安装不再引入单独的 Access action：接收者先获得逻辑 Skill identity 上的 `artifact.read`，再自行决定是否以及如何
安装一个精确 Revision。

内置 Access schema 使用配置好的 SQLite、seekdb 或 OceanBase，但由 Server 独立持有，不进入 Runtime 领域。自定义部署
可以向 `create_server_app` 注入 `AccessControlService`。内置的可写外部 adapter `CasbinAuthorizationProvider` 使用
embedded Casbin 判定固定 action vocabulary，并把 canonical Binding Store 作为持久化 adapter，因此在不维护第二份影子
策略的前提下支持 point/batch check、safe resource filter、create/revoke、过期和 CAS。组装时将它同时作为 decision
provider 与 `relationships`，relational repository 仍作为 audit store。

`AuthZenAuthorizationProvider` 是对接 OpenID AuthZEN Authorization API 1.0 `evaluation`/`evaluations` endpoint 的
decision-only adapter。其 capability 应配置为 `multi_requirement_check=true`、`relationship_management=false` 和
`safe_resource_filtering=false`；此时 self-service Binding mutation 和授权资源列表会返回 503，而不会虚报不安全的能力。
该 adapter 只接受 HTTPS endpoint 或 loopback HTTP，拒绝 URL 内嵌 credential，也不会把 PDP response body 或原始错误
暴露出去。authentication middleware 仍必须绑定不透明的 `PrincipalRef`；`scope_id` 只用于资源分区，不能建立身份。

Python Client 和 CLI 对一般出站请求应用相同规则：配置的明文 `http://` Server URL 仅接受 loopback 主机；远端 Skill
Receiver 的内部 PoC 显式例外见下文。当代码的 `http://` base URL 只是路由标签、实际传输是安全的，例如进程内 ASGI
应用、Unix domain socket 或由代理终止 TLS 时，必须自行传入 `http_client` 并显式设置
`trust_transport_security=True`。

安全的 Docker 和远程访问配置见[部署 Server](deploy-server.md)。

Server 默认把启动目录作为 workspace，并自动提供两个可写的本机项目级目标：Codex 使用
`<workspace>/.agents/skills`，Claude Code 使用 `<workspace>/.claude/skills`。目录不存在时不会报错，只有显式发布操作
才会创建目录。以 systemd、容器或其他不保证工作目录的方式启动时，应设置一次
`POWERCONTEXT_SERVER_WORKSPACE`。

远端技能接收端需要通过稳定的外部入口连接时，在 Server 上配置
`POWERCONTEXT_SERVER_PUBLIC_URL`。否则注册命令可以使用远端命令行已经配置的服务地址。

一期 PoC 如果运行在受保护的内部测试网络，可以让 Server 和 Receiver 双端显式同意直连 HTTP：Server 设置
`POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true`，并用 `POWERCONTEXT_SERVER_PUBLIC_URL` 公布 `http://` 地址；
Receiver 注册时同时传入 `--allow-insecure-http`。
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

Handoff Report API route 独立默认启用。Selection、检查和导出步骤见
[使用 Handoff Report](../workflows/use-handoff-report.md)。

默认 `all` 角色会启动 Artifact Processing Supervisor。OceanBase 部署可以拆分 `api` 和 `background`；
`powercontext server run --role background` 不启动 HTTP、MCP 或 Dashboard listener，多个后台候选者通过数据库 Lease
自动选出一个 active Leader。SQLite 与嵌入式 seekdb 只支持单进程 `all`。未设置正数间隔时，Topic Memory 自动波次
保持关闭；显式 flush 工作的恢复不依赖该间隔。Topic Worker 要求使用文件 SQLite；内存 SQLite 配合 generation
model 的配置会在声明处理能力之前被拒绝。请通过 `POWERCONTEXT_SERVER_DATABASE_URL` 指定持久数据库路径，例如
`sqlite+aiosqlite:////srv/powercontext/runtime.db`。Memory、Topic Memory、Experience、Profile 均使用统一 Supervisor，OceanBase 拆分角色也可启用其周期。
SQLite 和 embedded seekdb 仍要求单宿主 `all`。两模式均保留逐 Family 独立额度和总超时，不借用其他 Family 空闲额度。
关闭自动准入仍恢复已接受请求。API 与后台须保持 mode、注册 Family 和可触发能力一致；模型仅在执行端必需。
切换模式须[协调停机迁移](artifact-processing-migration.md)，不能混用模式启动。
显式同时配置的新旧别名值不同时拒绝启动，同值接受。

普通 Runtime 启动会初始化并恢复所配置的检索索引。Topic Worker 复用该数据库，不再为每个 Window 重建无关的
Memory/Experience 检索投影；Topic 索引校验与发布守卫仍然执行。如果空库切换了 Topic 检索形态或 Embedding
profile，应使用相同配置重新打开已有 Runtime；旧 Runtime 会以 retrieval-shape 错误拒绝 Topic 搜索、精确读取和
当前 Head 浏览，而不是读取另一个向量空间。

普通 Runtime 启动会初始化并恢复所配置的检索索引。Topic Worker 复用该数据库，不再为每个 Window 重建无关的
Memory/Experience 检索投影；Topic 索引校验与发布守卫仍然执行。如果空库切换了 Topic 检索形态或 Embedding
profile，应使用相同配置重新打开已有 Runtime；旧 Runtime 会以 retrieval-shape 错误拒绝 Topic 搜索、精确读取和
当前 Head 浏览，而不是读取另一个向量空间。

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

Experience 孵化使用独立的 Supervisor binding 和持久化 Source cursor。每次调用按 `SOURCE_WINDOW_LIMIT` 检查有限 Source 窗口，只把 metadata 包含 `"kind": "task-outcome"` 的 Content Source
暴露给模型。该 job 会在 Review Inbox 中创建 pending Experience Candidate；它不会自动批准、进入
PreparedContext、创建 managed Skill、将它导出到 Agent target 或执行任何内容。Memory 与 Experience 保持独立的周期、
Worker 额度和业务 Cursor；关闭某一间隔仅停止该 Family 的新自动准入，保留已接受工作。
设置与验证步骤见[创建并审核 Experience](../workflows/create-and-review-experience.md)。

### Agent Skill 目标

零配置流程使用上述 workspace 中的 Codex 和 Claude Code 项目级目录。只有需要自定义路径、用户级 target、环境兼容性
事实或显式关闭本机发现时，才需要通过一个 JSON 值覆盖默认的 host-local target。基础 JSON 结构和验证流程见
[配置 Agent Skill target](../workflows/configure-agent-skill-targets.md)。包含兼容性信息的覆盖示例如下：

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
Skill package 子目录，不会推断用户 home 目录、安装 package 或授予执行权限。自定义 target 的
`allow_managed_publish` 默认是 `false`；设为 `true` 后，显式发布操作可以把 approved managed Skill 安全创建或更新到该
target。发布操作不能提交任意路径，也不会覆盖外部或已被修改的 package。发布会物化 Review 通过的完整精确 package
（包括 scripts 和 references），不会执行其中内容，也不会向 package 注入 sidecar。只有 binding 与 tree digest 仍匹配
时才能安全取消发布；本地漂移和外部内容会保持不动。`host_id`、locator 和 registration 都是本地环境状态，不是跨
host contract。已有的
`codex_roots` 配置继续作为 Codex-only 兼容格式被接受；新配置应使用 `targets`。

可选的 `environment` object 只包含已观测且不含密钥的兼容性事实。Command value 是版本标签；
`environment_names` 只记录名称，绝不记录值。PowerContext 不会为了构造该 profile 而探测或执行 package script。
未配置时，包含 script 的 package 会显示未知兼容性；配置后，Skills Library 会把已知 script interpreter 与已观测
command name 对比，并返回带原因的 Assessment。Assessment 不会授予 network、filesystem、dependency install 或
environment 访问权。

Server 始终创建 non-recording OpenTelemetry request context，从 inbound span 派生 `X-PowerContext-Request-ID`。如需为
CLI 管理的 Server 启用 recording 和 export，请安装 `powercontext[cli,server,tracing-otlp]`、启用 tracing，
并使用 `OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS` 和 `OTEL_SERVICE_NAME` 等标准
OpenTelemetry 环境变量进行配置。不使用 `powercontext` command 的 programmatic Server integration 可以省略
`cli` extra。

启用 tracing 后，PowerContext 自己构造的 generation 与 embedding 调用也会产生 span，且不记录 prompt、模型响应、
Memory 内容或向量。可运行的配置见 [用 Phoenix 查看 trace](trace-with-phoenix.md)；需要通过
`OTEL_EXPORTER_OTLP_HEADERS` 为 exporter 鉴权的后端示例见 [用 Langfuse 查看 trace](trace-with-langfuse.md)。

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
full-text search 仍可用。配置和 capability 验证步骤见[配置向量检索](../workflows/configure-vector-search.md)。

## CLI Server 连接

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | 未设置 | 发送给启用鉴权的 Server 的 Bearer token |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP 超时秒数 |

`powercontext` 为 Server URL 和 timeout 提供对应的单次命令参数。Token 只能通过环境变量提供，避免出现在
命令行参数中。

## Agent 集成

各 Agent 的安装、连接、认证和环境变量见[对应的集成文档](../integrations/index.md)。
