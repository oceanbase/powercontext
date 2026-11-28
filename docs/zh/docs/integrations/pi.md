---
title: Pi Coding Agent
description: 为 Pi 安装 PowerContext 原生包，并配置召回、采集和持久记忆写入。
status: community
---

# Pi Coding Agent

`community`

## 安装或刷新 package

先安装 Pi，再从与 PowerContext CLI 相同的 ref 安装 package：

```bash
powercontext setup pi
```

也可以使用本地 checkout：

```bash
powercontext setup pi --source .
```

`setup pi` 会调用 Pi 的原生 package 安装器，并创建 PowerContext 数据目录；它不会启动 Server。启动 Server 后，
在项目目录中开启新的 Pi 会话：

```bash
powercontext server run
pi
```

如果其他路径下的旧 PowerContext 包带有 Pi 资源筛选，setup 会在修改宿主前停止。
请将该包的注册路径改为报错中给出的受管理路径，保留筛选设置后重新执行 setup。
已位于受管理路径的包在更新时保留现有筛选。

## 了解 package 的行为

Pi 开始 agent turn 前，package 会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`，
[关闭提示词召回](#把提示词时上下文交给-gateway)时除外。它只严格接受
`powercontext.prepared-context.v1`，并在 `PowerContext host-supplied context.` 标签之后把结果追加到 system prompt。
结果中 Server 的信任策略（trust policy）将其标记为不可信历史证据。当前 system instruction、仓库规范和用户请求
始终优先。

符合条件的用户提示词会被独立采集为 Content Source。package 不会同步完整 Pi transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：Pi 的 prompt 不变，普通工作不会被阻塞。

package 按 `POWERCONTEXT_PI_SCOPE_ID`、workspace 持久 binding、Server 默认 Scope 的顺序解析一个由 Server
管理的 Scope。workspace 路径只会哈希为外部 binding key，不会成为 Scope ID。仅在宿主必须固定到某个已有
Scope 时设置显式变量。在没有自己 workspace binding 的 linked Git worktree 中，
解析会使用主 checkout 中同一目录的绑定，所以 checkout 的绑定也覆盖它的 linked worktree。这个回退来自 Git 报告的真实路径，
因此只有 checkout 的绑定建立时所用路径不含符号链接，才能匹配。

启用这些能力后，Pi 已满足仓库定义的 Full 核心接入 profile。

### 压缩前 checkpoint

package 通过 Pi 公开的 `session_before_compact` 事件提供 **best-effort** checkpoint。符合条件的 prompt 会在 agent
turn 前使用确定性 Source ID 采集，事件会 flush 这些已接受的 journal position。只有完整、合法的 flush 响应满足
`current_cursor >= position` 时，position 才会从 pending 集合移除；仅有 HTTP 2xx、畸形 cursor 或其他 Scope 的
cursor 都不能完成确认。

每个 lifecycle 边界最多调用 `POWERCONTEXT_PI_FLUSH_MAX_CALLS` 次（默认 `4`，有效范围 `1`–`16`），并拥有各自的
一秒预算；排队时间、所有 pending Scope 和重试调用共享该边界的预算。cursor 落后或 `remaining_work` 为 true 时会在
预算内继续调用。确认前失败或取消会保留 pending work；确认后
发生取消不会撤销确认。边界在排队期间耗尽预算时，会发出不含内容的 `deadline_exceeded` 诊断并保留 pending position。
`session_before_switch` 和 `session_shutdown` 只 flush 已接受的 pending position。

Pi 的真实 `ExtensionRunner` 会在 handler 抛错后继续运行；只有 handler 显式返回 `{ cancel: true }` 才会停止压缩。
该 package 不返回这一结果，因此 checkpoint 失败不会阻止压缩，集成不具备 `required_capable` 语义。可选诊断不含
内容，只记录 Scope 哈希关联值、目标/观测 position、生命周期事件、宿主和稳定失败分类。

## 控制提示词采集

默认开启提示词采集。当前工作不应被记录时，请在启动 Pi 前关闭：

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

看起来包含密钥的提示词，以及超过 200,000 UTF-8 bytes 的提示词，永远不会被采集。打开采集本身不保证会产生
Memory；Server 仍需配置 generation model。

测试时可让采集等待 Source 处理完成：

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

这会增加延迟，并不适合日常交互。未开启时，Pi 会记录 Source position，并在 agent 和会话边界以短时间预算尽力
flush。

## 把提示词时上下文交给 gateway

Pi 的模型请求经过 PowerContext gateway 时，gateway 为每一轮准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on` 后，gateway
也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，即使 Core 处于非
enforcing 模式也是如此。package 的召回会把这样的块加入 Pi 的 system prompt，因此 Core 以 HTTP 422 拒绝这个请求，gateway
返回 HTTP 503 `gate_paused`，这一轮在模型收到请求前失败。

Pi 直接调用模型时，gateway 不承载这些流量，请保留 package 的默认值。要让 Pi 经过 gateway，按下面的步骤配置 Core、Pi、
package 和 gateway。

### 选择 Core 目标

在任何隐私模式下（包括 `disabled`），Core 都需要为 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 指定的 profile
配置目标。请将该变量设为包含 Pi 获准 endpoint、模型和协议的 Responses profile。没有该 profile 时，Core
以 `target_unsupported` 暂缓每个请求，gateway 对每一轮都返回 HTTP 400。见[配置受支持的目标](../operate/inference-protection.md#配置受支持的目标)。

Pi 发送输出上限（`max_output_tokens`，取自模型的 `maxTokens`）、`store: false`，以及（除非 `PI_CACHE_RETENTION=none`）
`prompt_cache_key`，因此目标满足以下条件时，enforcing 模式的 Core 会放行它的请求：

- 声明 `context_window_tokens`、不小于 Pi 模型 `maxTokens` 的 `max_output_tokens`，以及 `validated_utf8_token_bound`；
- 在 `allowed_cache_retention` 中列出 `provider-default`；Pi 以 `PI_CACHE_RETENTION=long` 运行时还要列出 `24h`。否则
  Core 以 `cache_retention_unapproved` 暂缓每个请求；
- provider 返回的模型名与 `model` 不同时，在 `response_models` 中列出这些名称。否则 Core 以 `response_model_mismatch`
  暂缓每个响应。

窗口检查把请求的每个 UTF-8 字节计为一个 token。一个会话中 Pi 的第一个请求就超过 60 KB，主要是工具定义和 system prompt，
技能、上下文文件和对话还会让它更大。因此 `context_window_tokens` 过小时，每一轮都会以 `context_window_exceeded` 被暂缓。
例如：

```bash
export POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{
  "mode": "enforcing",
  "targets": {
    "default": {
      "endpoint": "https://models.example/v1/responses",
      "model": "approved-model",
      "protocol": "openai-responses",
      "context_window_tokens": 400000,
      "max_output_tokens": 32000,
      "validated_utf8_token_bound": true,
      "allowed_cache_retention": ["provider-default"]
    }
  }
}'
```

其中的 endpoint、模型和上限只是示例。请先为你的模型核实这个上界，再设置 `validated_utf8_token_bound`；gateway
在启动时发现 Core 的模式，因此修改模式后请重启 gateway。选择模式前请阅读
[上下文复用与最终准入](../operate/context-protection.md)和[推理隐私保护](../operate/inference-protection.md)。

### 让 Pi 指向 gateway

在 `~/.pi/agent/models.json` 中添加指向 gateway `/gateway/v1` 的 provider，设置 `api: "openai-responses"`，并把 gateway
密钥用作它的 `apiKey`；Pi 会把它作为 bearer token 发送：

```json
{
  "providers": {
    "powercontext-gateway": {
      "baseUrl": "http://127.0.0.1:8787/gateway/v1",
      "api": "openai-responses",
      "apiKey": "$PC_GATEWAY_KEY",
      "models": [{ "id": "approved-model", "contextWindow": 400000, "maxTokens": 32000 }]
    }
  }
}
```

在启动 Pi 的环境中，把 gateway 的 `PC_GATEWAY_API_KEY` 的值导出为 `PC_GATEWAY_KEY`，然后用 `/model` 或
`pi --provider powercontext-gateway --model approved-model` 选择这个模型。

请使用 Responses API。Pi 总是流式请求。在 Responses 路由上，gateway 以非流式方式调用模型，
再把检查通过的响应以流的形式重放给 Pi，因此这一轮能够完成并被采集。在 Chat Completions 路由（`openai-completions`）上，
gateway 转发 Pi 的流式请求。enforcing 模式的 Core 会暂缓流式响应，gateway 返回 HTTP 400。非 enforcing 模式的
Core（包括默认的 `disabled` 模式）下，gateway 会原样放行流式响应。设置 `PC_GATEWAY_SEDIMENT=on` 后，gateway
还会拼接终止流式响应中的助手文本，并采集已完成的回合。

在组合部署（`PC_GATEWAY_CORE_UDS`）中，gateway 把 Core API 请求连同客户端自己的凭据转发给 Core，因此
`POWERCONTEXT_PI_BASE_URL` 可以使用 gateway 的地址。

### 关闭 package 的提示词召回和采集

启动 Pi 前，关闭 package 的提示词时召回和提示词采集：

```bash
export POWERCONTEXT_PI_PROMPT_RECALL=false
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

关闭召回后，package 不发送准备请求，不向 system prompt 添加上下文，也不提供 `pc_prepare_context`。gateway
已经为每一轮注入上下文，并会在 Core 检查请求之前，把请求所带历史中的 `pc_prepare_context` 工具结果替换为占位文本。
`POWERCONTEXT_PI_CAPTURE_PROMPTS` 为 `true` 时 package 仍会采集提示词；两项都关闭时，它不会为提示词发送任何请求。其余
`pc_*` 工具、`/pc` 命令和边界 flush 照常工作。

关闭召回后，package 还会在每个模型请求上把 Pi 的会话 ID 作为 `X-PowerContext-Conversation-ID` 发送。
组合部署（`PC_GATEWAY_CORE_UDS`）在一个会话中每条用户消息准备一次，并把结果重放给这条消息的工具循环步骤。
没有这个请求头时，它根据请求的历史开头推导会话：Pi 每次请求都发送会话的完整历史，开头是它的 system prompt
和第一条用户消息，因此 Pi 的请求也能推导。请求头仍能区分开头相同的会话，并为采集和前缀提取提供在 gateway
重启后保持不变的会话标识；推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)。Pi 会把这个请求头发给处理该请求的任何 provider，
包括它直接调用的 provider；除非 `PI_CACHE_RETENTION=none`，Pi 自己的 Responses 请求本来就以 `prompt_cache_key`
携带同一个 ID。Pi 的压缩和分支摘要请求不带会话请求头，package 改为给它们标记 `X-PowerContext-Turn: internal`，
因此 gateway 不会把摘要算作一轮：不为它准备或注入上下文，不添加成本指导，也不采集它。Core 仍会检查这个请求。
和会话请求头一样，Pi 会把这个请求头发给处理该请求的任何 provider。

修改这些设置后请重启 Pi。已有会话可以继续：Pi 每一轮都重新构建 system prompt，不把 package 召回的上下文存进会话。
`pc_prepare_context` 返回过上下文的会话还能继续对话，因为 gateway 会在每一轮的请求中把这个结果替换为占位文本。
但这只持续到 Pi 压缩包含该结果的那部分会话，或为包含该结果的分支生成摘要为止。此时 Pi 会把结果复制进摘要请求的文本，
gateway 不会替换这段文本，因此 Core 拒绝该请求，压缩或分支摘要失败。请开启新会话，不要恢复这样的会话。

### 配置 gateway

以组合部署（`PC_GATEWAY_CORE_UDS`）运行 gateway，它默认注入带有会话标识的请求。设置：

- 将 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 设为 Pi Responses 路由使用的 Core profile。不要设置
  `PC_GATEWAY_UPSTREAM_URL*`；这些旧变量会阻止启动。将 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为供应商密钥，
  将 `PC_GATEWAY_API_KEY` 设为 Pi 发送的密钥，将 `PC_GATEWAY_CORE_TOKEN` 设为 Gateway 服务凭据，并设置
  `PC_GATEWAY_SEDIMENT=on`。Core 的访问控制必须为 `enforced`；为服务身份授予 `gateway.user` 和 `server.observer`，
  开启采集时再授予 `scope.contributor`。绑定命令见[网关模型目标](../operate/deploy-server.md#网关模型目标)。
- `PC_GATEWAY_DEFAULT_SCOPE` 设为 package 为该 workspace 解析的 Scope，`/pc` 会打印这个 Scope。

gateway 把所有会话都放在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集，而不是 package 为每个 workspace
解析的 Scope。gateway 的鉴权设置和请求大小上限见[部署 Server](../operate/deploy-server.md#网关认证边界)。

## 使用显式工具和命令

`powercontext-project-context` skill 会说明何时调用原生 `pc_*` 工具。核心工具包括：

- `pc_search`、`pc_memory_list`、`pc_memory_get`、`pc_memory_revise`、`pc_memory_retire`；
- `pc_memory_changes` 用于查看变更历史，`pc_stats` 用于查看当前 Scope 的统计诊断；
- `pc_remember`、`pc_prepare_context`（仅在提示词召回开启时提供）、`pc_capture_source`；
- `pc_handoff_activate`、`pc_handoff_prepare`、`pc_handoff_finalize`、`pc_handoff_commit`、
  `pc_handoff_continue`；
- `pc_experience_generate`、`pc_skill_generate`、`pc_experience_get`、`pc_skill_get`、`pc_review_list`、
  `pc_review_get`，用于生成候选以及只读查看 Artifact 和候选材料。
- `pc_topic_search`、`pc_topic_get`，用于按主题查询当前 Topic Memory，并读取带 Source 引用的精确版本。
- `pc_work_contract`、`pc_handoff_current`、`pc_handoff_acknowledge`、`pc_task_outcome`，用于结构化工作连续性。
- `pc_external_scan`、`pc_external_list`、`pc_external_resolve`，用于发现和检查宿主本地 External Skill；`pc_external_import` 只在明确确认后导入或 fork 一个精确解析过的 Skill。

查看候选材料不授予批准、拒绝、修订、安装、发布或执行权限。
Topic Memory 查询是只读的；结果属于不可信历史证据，不是指令来源。
结构化工作工具会改变持久状态，必须经过交互确认；无 UI 时拒绝写入。返回的 Handoff、引用和检查结果必须按原值传递，不能把历史内容当作新的授权；`handoff_receipt_ref` 只能引用 accepted committed Handoff 的 Receipt。

显式持久化写入在交互式 Pi 会话中必须确认；没有交互 UI 时，Pi 会拒绝写入而不会静默持久化。`/pc doctor`、
`/pc search <query>`、`/pc remember <text>`、`/pc flush` 和 `/pc stats` 可直接查看状态和维护内容。

## 连接启用鉴权的 Server

在受保护环境中启动启用鉴权的 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

使用完整匹配 header 启动 Pi：

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

不要把凭据放进 `POWERCONTEXT_PI_BASE_URL`。环回地址默认允许明文 HTTP；非环回 Server 使用 HTTPS，或显式设置
`POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`，HTTPS 证书校验仍然启用。
安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。

## 验证安装

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` 会检查 Pi 可执行文件是否存在，以及 Pi 是否列出了 PowerContext package。修改 PowerContext 环境变量后
需要重启 Pi。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_PI_SCOPE_ID` | 未设置 | 在 workspace binding 和 Server 默认值之前显式选择已有 Scope |
| `POWERCONTEXT_PI_AUTHORIZATION` | 未设置 | package HTTP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | 把符合条件的用户提示词采集为 Source 证据 |
| `POWERCONTEXT_PI_PROMPT_RECALL` | `true` | 为每条提示词准备并添加上下文，并提供 `pc_prepare_context`；`false` 时把提示词时上下文交给 gateway，并发送 gateway 会话请求头和轮次请求头 |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `3000` | 单请求超时，单位毫秒 |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `6000` | 召回/采集共享 HTTP 时间预算，单位毫秒 |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | 请求并校验的 PreparedContext byte 上限（`512`–`32768`） |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | 在 prompt hook 中等待已采集 Source 的处理 |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | 一个 lifecycle 边界内所有 pending Scope 共享的最大 flush 调用次数 |
| `POWERCONTEXT_PI_DIAGNOSTICS` | `off` | 失败诊断输出：`off`、`stderr`，或写入 JSON 行的绝对文件路径（支持 `~/`） |

Pi 会拒绝包含凭据、query 或 fragment 的 base URL。召回、采集和边界 flush 都会正常降级；显式 `pc_*` 持久化写入
必须确认，Pi 没有交互 UI 时会被拒绝。修改这些变量后需要重启 Pi。

失败诊断默认不输出：Pi 的 TUI 通过光标定位在 stdout 上渲染，写到 stderr 的内容会落在输入栏里；需要查看时请设置
`POWERCONTEXT_PI_DIAGNOSTICS`。

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
