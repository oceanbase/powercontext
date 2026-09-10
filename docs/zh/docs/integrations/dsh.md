---
status: community
title: DeepSeek Harness
description: 安装 PowerContext DeepSeek Harness 插件并控制其本地行为。
---

# DeepSeek Harness

`community`

## 安装匹配的 Server 和插件

先安装 DeepSeek Harness，并确保 Web profile 可用。真实宿主验收固定使用 DSH 0.1.2-rc.1。
选择以下一种 PowerContext 安装方式，让 Server 和插件保持匹配。

使用本站对应的配置向导版本：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup dsh
```

按照本站流程验收时，Server 和插件都使用这个源码分支。

开发版从同一个 checkout 安装两个组件，并记录 commit：

```bash
git clone --branch master https://github.com/oceanbase/powercontext.git powercontext-dsh-dev
git -C powercontext-dsh-dev rev-parse HEAD
uv tool install --force "./powercontext-dsh-dev[cli,server]"
powercontext setup dsh --source ./powercontext-dsh-dev
```

更新时执行 `git -C powercontext-dsh-dev pull --ff-only`，记录新的 commit，再重复两个安装命令。
本地目录必须包含仓库提交的已构建文件 `lib/index.js`。
`setup dsh --source oceanbase/powercontext --ref master` 会直接复用有效缓存 checkout，不会 fetch；
重复运行不代表更新了移动分支。只有残缺 checkout 会被替换。

`setup dsh` 调用 `dsh plugin --profile web add`，不会启动 Server。安装完成后重启 DSH。

## 启动 Server 和宿主

需要自动将 Source 提取为 Memory 时，生成并校验 Server 配置：

```bash
powercontext config init --output powercontext.env
powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

Server 是前台进程，保持这个终端运行。配置 generation 和定时处理；embedding 用于向量和混合检索，
显式写入 Memory 和全文检索不依赖 embedding。无模型的 Server 可以健康运行，同时关闭自动提取并返回空召回。
模型与处理配置参见[完整 Memory 闭环](../get-started/configure-models.md)。

在另一个终端指定同一个 Server，然后启动 DSH：

```bash
export POWERCONTEXT_DSH_BASE_URL=http://127.0.0.1:8000
dsh web
```

PowerShell 使用 `$env:POWERCONTEXT_DSH_BASE_URL = "http://127.0.0.1:8000"`，然后运行 `dsh web`。
Server 使用其他监听地址时同步修改 URL。鉴权使用 `POWERCONTEXT_DSH_AUTHORIZATION`，
不要把 Server 的模型凭据复制到插件配置。使用 workspace binding 或 Server 默认 Scope 时不设置
`POWERCONTEXT_DSH_SCOPE_ID`；需要覆盖时，指定一个已经存在的 Scope。

环境变量优先于插件 patch 配置，patch 配置优先于默认值。环境变量必须存在于启动 DSH 的进程中；
在其他终端修改变量不会更新已经运行的宿主。

## 诊断运行中的配置

在出现问题的 DSH 会话内运行 `/pc doctor`。报告显示配置来源，分别检查 liveness、readiness、运行能力、
路由声明、当前 Scope 和只读 prepare 操作。Scope 失败不会遮蔽健康检查。
端点摘要仅显示 origin、配置来源和是否存在路径前缀，不打印凭据、前缀正文、查询参数或 fragment。

失败项提供操作名、稳定 code、可用的 HTTP status/request ID 和具体恢复操作。
协议错误还提供 `protocol_issue`，指出 JSON、状态码或 PreparedContext 字段违反的具体规则。
Readiness 保留已识别的依赖状态，包括 HTTP 503 的检查结果，不透传 Server 原始错误文字和召回内容。
`ok: true` 表示这些只读检查通过，并不代表已经采集或处理了数据。

| 结果 | 含义和处理方式 |
| --- | --- |
| `invalid_endpoint` | 修正实际使用的 HTTP(S) base URL，移除 userinfo、query 和 fragment，凭据改用 Authorization。 |
| `connection_refused` / `dns_lookup_failed` | 分别检查监听地址是否启动、配置的主机名能否解析。 |
| `request_timeout` | 检查指定操作的 Server 延迟、依赖和实际请求超时设置。 |
| `connection_failed` | 传输失败且未提供更具体原因；检查端点、代理、网络和 Server 日志。 |
| `authentication_failed` / `authorization_failed` | 分别检查宿主凭据、当前主体对该操作和 Scope 的权限。 |
| `not_ready` / `degraded` | 检查报告指出的依赖，例如 `database` 或 `inference.generation`，按该项恢复提示处理。 |
| `required_route_missing` | 指定操作返回无业务码的 404；检查代理路由、base path 和版本匹配，404 本身不能确定是版本问题。 |
| `required_route_undeclared` | Server 的 OpenAPI 文档缺少列出的操作声明。 |
| `contract_unavailable` | 无法核对路由声明；通过同一 base path 提供 `/openapi.json`，或单独核对部署的契约。 |
| `scope_not_found` / `unscoped` | 检查显式 Scope 覆盖、workspace binding 和 Server 默认 Scope；Doctor 不修改它们。 |
| `invalid_response` | 响应未通过协议校验，即使 HTTP status 是 200。 |
| `extraction_disabled` / prepare `empty` | 正常的受限能力或空结果，不能据此判断 hook 或 Server 故障。 |

路由检查读取 Server 已有的 `/openapi.json`，区分“声明支持”和“实际探测通过”。
Doctor 不执行 capture、remember、flush、binding 修改或注入。契约不可读时标为未检查；
Scope 不可用时跳过 prepare 并说明原因。实际写入与处理由下方显式验收负责。

独立 CLI 的 `powercontext doctor dsh` 仅检查 Web profile 注册，并明确报告没有观察到运行中宿主的配置，
没有执行 Server 检查。退出成功只表示注册检查通过。
`powercontext doctor` 使用自己的 `--server-url` / `POWERCONTEXT_CLIENT_SERVER_URL`；
对齐 URL 后可复用它的 service/health 诊断，但不能认为它观察到了 DSH 的覆盖配置。

## 验证采集、处理和新会话召回

这是会写入测试证据的显式验收。完成上述匹配安装和提取配置后：

1. 运行 `/pc doctor`，确认健康、Scope 和 prepare 检查通过，自动提取已启用。
2. 发送一个独特的项目事实，例如：“The aurora deployment color is violet-cedar-1457.”
3. 分别核实 Source 接收和处理。等待已配置的 Scheduler，或显式运行 `/pc flush`。
   按[Memory 闭环 API 检查](../get-started/configure-models.md)确认处理游标达到 Source position，
   并找到引用该 Source 的 Memory entry。flush 完成但没有生成 entry，不能证明提取成功。
4. 在同一 workspace/Scope 下打开新会话，询问 aurora 的部署颜色，展开实际召回的 snapshot 检查事实。
   仅凭模型回答正确，不能证明发生了召回。

在同一 checkout 执行 `make dsh-runtime-test`，可运行不依赖外部模型服务的确定性验收。
固定版本的真实宿主 fixture 分别检查 Source 接收、处理、新会话召回和 snapshot 持久化。
模型响应是测试 fixture，不能证明外部推理服务的行为。
详见[运行时验收说明](https://github.com/oceanbase/powercontext/blob/master/integrations/dsh/plugins/powercontext/tests/runtime/README.md)。

## 理解插件行为

插件通过两条路径访问同一个 Server：

- 每轮模型开口前，先请求 Runtime 准备一个最终、有界的上下文值，再把用户输入采集为 Source 证据；
- 具名 `pc_*` 工具通过公开 HTTP API 记忆、检索、修订、停用和审计 Memory。

插件按 `POWERCONTEXT_DSH_SCOPE_ID`、session workspace 持久 binding、Server 默认 Scope 的顺序解析一个由
Server 管理的 Scope。workspace 路径只会哈希为外部 binding key。缺少 workspace 时使用 Server 默认 Scope，
不会把 Harness 进程目录作为 Scope。

插件在模型分析提示词前只调用一次 `POST /v1/context/prepare`。显式 `remember_memory` 不需要模型。

## 排查工具和命令的直接调用失败

Scope 解析失败时，具名工具和依赖 Scope 的 `/pc` 命令会返回受控失败，并在执行请求的操作前停止。
插件不会因此创建 binding 或换用其他 Scope 重试。取消信号和现有的单请求超时也适用于 Scope 解析。

在 DeepSeek Harness 内：

- `/pc doctor` 独立检查健康、能力、路由和 Scope；Scope 解析失败时保留其他层的结果，并跳过 prepare。
- `/pc capabilities` 直接查询 Server 能力，无需解析 Scope。
- 未知子命令或缺少参数时，在本地返回用法说明，不访问 Server。
- 裸 `/pc` 显示已解析的 Scope 和 Server origin。解析失败时返回错误，但仍显示 `scope=unresolved`、受控错误信息
  和 `/pc doctor` 恢复提示。配置中的 Scope ID 不会被当作已解析成功；显示的 origin 不包含凭据、路径、查询参数和 fragment。
- `search`、`remember`、`flush`、`review`、`skills scan` 和 `stats` 必须成功解析 Scope；`stats` 仅查询当前 Scope。

| 结果 code | 含义 |
| --- | --- |
| `not_found` | 业务 404。可选的 `error_code` 保留已识别的公开原因，例如 `scope_not_found` 或 `memory_not_found`。 |
| `version_mismatch` | 必需端点返回了没有业务码的 404。应检查 Server 端点和插件、Server 的兼容性；该结果不能证明具体的部署原因。 |
| `authentication_failed` | Server 返回 401，应检查 Authorization 配置。 |
| `unavailable` | 连接失败、超时、取消或 HTTP 503。原生诊断使用 `server_unavailable`。 |
| `unscoped` | resolver 执行完成，但没有返回 Scope。 |
| `invalid_response` | 客户端识别到无效的 Server 响应。 |

已有冲突和校验错误码（如 `revision_conflict`、`invalid_request`）保持原有含义。失败结果保留可用的 HTTP status 和
request ID，提示文字使用固定内容，不透传 Server message。未知错误码不会出现在 `error_code` 或诊断中，
也不会仅因无法识别就被判为版本不匹配。

## 排查自动召回和采集

普通消息也会触发 Scope 解析、上下文准备、提示词采集和可选 flush。这些自动阶段失败时，Harness 对话继续。
Scope 解析失败会停止本轮后续的 PowerContext 操作，不会换用其他 Scope 或创建 binding。

`powercontext.dsh` 日志通过 `scope_resolve`、`context_prepare`、`capture_content_source`、`flush_memory`
或 `context_inject` 标识失败阶段。诊断使用固定结果和已识别的公开错误码，不包含 Server message、
提示词内容、凭据或请求路径。同类重复警告在 60 秒内降噪。logger 自身失败也不会丢弃已准备的上下文或打断对话。

能否看到日志取决于 DSH profile 的原生 exporter 配置。本次测试的 DSH 0.1.2-rc.1 Web profile 默认不向终端
导出这些警告。如果 profile 使用 Cordis 的 console exporter（`@deepseek-ai/cordis-plugin-logger-console`），
需要将其 `config.levels.default` 设为 `2` 以包含警告；设为 `3` 可同时查看 debug 事件。
在启动 `dsh web` 的终端中查看 `powercontext.dsh` 记录。这里使用宿主 logger，不增加模型消息或独立日志面板。

必需路由的 404 只有在没有业务错误码时才记录为 `version_mismatch`。Scope 的业务 404 则记录
`invalid_response` 和 `error_code: scope_not_found`。resolver 正常结束但未返回 Scope 时记录
`skipped` 和 `reason: scope_unresolved`。有效的空召回属于正常结果，只写 debug 日志。
Scope 解析失败时，仍可使用 `/pc doctor` 和 `/pc capabilities` 检查 Server。

上下文准备和采集相互独立：prepare 失败后仍可采集输入；capture 或 flush 失败不会丢弃已经准备好的上下文。
Source 被接收不代表已经生成 Memory，后者需要 Server 成功处理。取消会停止后续操作；单个请求超时仍沿用
现有的单请求行为。

## 查看召回的上下文

非空 PreparedContext 只追加一次，消息带有 `source.form=snapshot` 和名为 `PowerContext` 的 section。
在 DSH 0.1.2-rc.1 Web 中，展开已完成轮次的“已思考”过程内容，再展开“上下文注入 — powercontext-dsh”。
其他宿主版本也可能将它展示在上下文浏览器中。section 与发给模型、
保存到会话日志的文字一致，包含不可信历史证据的提示，以及当前请求替换此前快照的说明。
重新打开会话历史时，这些元数据仍然保留。

空结果和自动失败不会生成 snapshot，也不会向模型注入错误通知。展示使用宿主已有的 snapshot 能力，
不新增 PowerContext 面板，也不声称存在 Server 尚未返回的 receipt 或来源信息。

## 控制提示词采集

默认开启提示词采集。如果当前工作不应被记录，请在启动 DeepSeek Harness 前关闭：

```bash
export POWERCONTEXT_DSH_CAPTURE_PROMPTS=false
dsh web
```

仅在测试时让插件等待 Source 处理完成：

```bash
export POWERCONTEXT_DSH_FLUSH_ON_CAPTURE=true
```

这会给每个提示词增加推理延迟，不是日常交互设置。`timeoutMs`、`requestTimeoutMs`、`maxBytes` 和 `flushMaxCalls` 是插件 patch 配置，不是环境变量。

## 连接启用鉴权的本地 Server

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配 Authorization header 的环境中启动 DeepSeek Harness：

```bash
export POWERCONTEXT_DSH_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
dsh web
```

不要把 token 写进 patch 文件或 Server URL。Server 不可用时，召回和采集会正常降级。插件加载仍然需要 DeepSeek Harness 的 peer 模块。

## 验证安装

```bash
powercontext doctor
powercontext doctor dsh
```

`doctor` 检查已安装的包和 Server。`doctor dsh` 检查 DeepSeek Harness CLI，以及 dump-config 是否包含插件 id `powercontext-dsh`。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | 插件使用的 Server 地址 |
| `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_DSH_SCOPE_ID` | 未设置 | 在 workspace binding 和 Server 默认值之前显式选择已有 Scope |
| `POWERCONTEXT_DSH_AUTHORIZATION` | 未设置 | 插件 HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |

`timeoutMs`、`requestTimeoutMs`、`maxBytes` 和 `flushMaxCalls` 是插件 patch 配置。Server 不可用时，召回和采集会降级；修改这些变量后需要重启 `dsh web`。

环回地址默认允许明文 HTTP；远程 HTTP 需要显式设置 `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP=true`，
HTTPS 证书校验仍然启用。主机地址变量依次读取 `BASE_URL`、`SERVER_URL`、`ENDPOINT`，然后才读取
`POWERCONTEXT_CLIENT_SERVER_URL`。安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。
