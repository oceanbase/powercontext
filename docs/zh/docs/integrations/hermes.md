---
title: Hermes
description: 安装 Hermes MemoryProvider 和独立 slash-command 插件，并连接到 PowerContext Server。
status: community
---

# Hermes

`community`

该集成包含标准 Hermes `MemoryProvider` 和独立 slash-command 插件。Hermes 继续负责对话和 Memory 生命周期，
provider 把召回、采集和显式操作发送给单独运行的 PowerContext Server；该插件会在 provider 激活前注册
`/pc` 和 `/powercontext`。后端故障不会中断 Hermes 对话。

## 前置条件

- `PATH` 中存在 Hermes Agent 0.20.4 或更新版本；
- 从 `master` 安装 PowerContext CLI 和 Server；
- PowerContext Server 正在运行。

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

## 安装两个插件

在另一个终端从同一 revision 安装或刷新两个插件：

```bash
powercontext setup hermes
powercontext doctor hermes
```

Setup 命令会把 provider 复制到 `$HERMES_HOME/plugins/powercontext`，安装
`$HERMES_HOME/plugins/powercontext-command`。除非用户已显式禁用，否则会启用 slash-command 插件，且不授予内置工具覆盖权限。
未配置 `memory.provider` 时，setup 自动选择 PowerContext；已有 provider 选择（包括显式空值）会保留。
Setup 不会启动 Server。

需要从现有 provider 切换到 PowerContext，或调整 provider 配置时，运行 Hermes Memory 设置向导并选择 `PowerContext`：

```bash
hermes memory setup
```

Hermes 0.20.4 应使用上面的通用命令。`hermes memory setup powercontext` 只选择 provider，不会打开配置向导。
设置完成后重启 Hermes。

## 验证召回和写入

```bash
hermes powercontext status
hermes powercontext remember preference "The user prefers uv"
hermes powercontext search "Python package manager"
```

在交互式 Hermes 会话中，`/pc status` 应连接到同一个 active provider。输入 `/pc ` 后按 Tab/Down，可查看
Memory、Handoff、Experience、Skill、审核、统计、trace 和 Scope 命令。Hermes 0.20.4 没有为 gateway
slash command 提供足够的调用上下文，因此该插件会拒绝 gateway 调用；gateway 会话应使用 provider 提供的
Hermes tools。

provider 默认连接 `http://127.0.0.1:8000`。Server 依次解析显式 Scope、持久 session binding、持久 workspace
binding 和默认 Scope。Hermes 只把 workspace 路径哈希用作外部 binding key，不会根据 profile、user、repository
或目录生成 Scope ID。在没有自己 workspace binding 的 linked Git worktree 中，解析会使用主 checkout 中同一目录的绑定，
所以 checkout 的绑定也覆盖它的 linked worktree。在 worktree 中建立的绑定优先；在那里绑定或清除，
只会改动该 worktree 自己的绑定。

## 配置连接

向导把非敏感设置写入 `$HERMES_HOME/powercontext/config.json`。环境变量会覆盖文件：

| 变量 | 用途 |
| --- | --- |
| `POWERCONTEXT_HERMES_CONFIG` | 配置文件路径；默认为 `$HERMES_HOME/powercontext/config.json` |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext Server URL |
| `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP` | 显式允许非环回明文 HTTP；默认为 `false` |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | 完整 authorization header，例如 `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | 未设置 `AUTHORIZATION` 时使用的裸 token 简写 |
| `POWERCONTEXT_HERMES_SCOPE_ID` | 显式的服务端 Scope ID |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Prepared Context 上限，范围为 512 到 32768 字节 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP 请求超时秒数 |
| `POWERCONTEXT_HERMES_PROMPT_RECALL` | 是否为每个提示词准备并注入上下文，并提供 `powercontext_prepare_context`；默认为 `true`，设为 `false` 时[把提示词时上下文交给 gateway](#把提示词时上下文交给-gateway) |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | 是否把完成的 turn 采集为 Source |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | 是否在会话结束时执行 Memory extraction |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | compression 前采集过滤后的新 turn；默认关闭 |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | 把召回上下文记录到敏感的本地 JSONL trace；默认关闭 |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | 覆盖 evaluation trace 目录 |

应由 Hermes 向导把 authorization 保存到受保护的 `.env` secret store，不要把 token 写入 `config.json`。环回地址默认
允许明文 HTTP；远程连接推荐 HTTPS，也可显式设置 `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP=true`，
该选项不会关闭 HTTPS 证书校验。引导安装和绑定地址的持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。
Evaluation trace 包含 prompt 和召回上下文，应保留在本机并按敏感数据保护。

## 数据流与捕获控制

这些控制作用于已激活的 PowerContext provider，环境变量优先于 provider 配置。
关闭 turn 捕获不会关闭召回、压缩前捕获或显式写入。

| 事件/入口 | 发送的数据 | 直接接收方 | 默认/触发条件 | 关闭方式 | 关闭后影响 |
| --- | --- | --- | --- | --- | --- |
| 召回 / prefetch | query（去掉首尾空白；超过 8192 个字符或 8192 个 UTF-8 字节时保留同时满足两项上限的最近文本；识别到的凭据替换为 `[REDACTED]`）、Scope 和准备选项；Prepared Context 返回 Hermes | PowerContext Server | `prompt_recall=true`、provider 已激活、query 非空且 Scope 已解析；每轮按当前 prompt 准备，turn 结束后的后台预取不发请求 | `POWERCONTEXT_HERMES_PROMPT_RECALL=false`；切换 memory provider 会停止该 provider 的全部操作 | 不发送准备请求，也不注入上下文，不提供 `powercontext_prepare_context`；捕获、其他工具和 flush 照常工作。仅关闭捕获不影响召回 |
| 完成 turn | 有长度上限的 user/assistant 文本对，以及 session 元数据；不是完整工具 transcript。用户或助手文本含可识别凭据的 turn 整条跳过 | PowerContext Server，保存为 Source | `capture_turns=true`；非主 Agent 上下文抑制自动写入 | 输入敏感文本前设置 `POWERCONTEXT_HERMES_CAPTURE_TURNS=false` | 停止该 Source 捕获；召回、压缩前捕获和显式写入仍独立工作 |
| 会话结束 | 针对已接受输入的 flush 请求，不会重新上传会话 transcript | PowerContext Server | `flush_on_session_end=true`；等待后台写入并检查提取能力 | `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END=false` | 停止此 flush 触发；已接受的 Source 保留，其他处理触发不受影响 |
| 压缩前 | 新 user/assistant 证据窗口，排除 system/tool 消息；每条消息中识别到的凭据替换为 `[REDACTED]`，消息仍保留在窗口中 | PowerContext Server，保存为 Source | `capture_pre_compress=false`；用 `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=true` 显式开启 | `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=false` | 停止此捕获及其后续提取尝试；必需 checkpoint 会失败而非报告成功 |
| 显式 remember / 内置 Memory mirror | 请求的 Memory 内容或变更，更新携带精确引用 | PowerContext Server | 显式操作，或主上下文中 add/replace/remove 的 `on_memory_write` | 不调用该写入或产生 mirror 的内置操作；没有专用 mirror 开关 | 关闭 turn 捕获不会禁用这些写入 |
| Evaluation trace | prompt/query、召回上下文及关联元数据；检测到的 secret 会脱敏 | 本地 JSONL 文件，默认位于 `$HERMES_HOME/powercontext/evaluation-trace/` | `evaluation_trace=false`；用 `POWERCONTEXT_HERMES_EVALUATION_TRACE=true` 显式开启 | `POWERCONTEXT_HERMES_EVALUATION_TRACE=false` | 停止新增 trace，不禁用召回或捕获；已有文件仍是敏感数据 |

召回 query 与完成 turn 捕获遵循[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)：query 中识别到的凭据会被替换，
用户或助手文本含凭据的 turn 不会被捕获。压缩前捕获替换每条消息中同样识别到的凭据，再应用插件自身的脱敏规则，
并把消息保留在窗口中，所以 transcript 中的凭据不会导致必需 checkpoint 失败。
因此，完成 turn 捕获跳过的 turn 仍可能出现在压缩前窗口中，识别到的凭据替换后发送到 Server。

压缩前捕获优先使用宿主提供的规范化 `evidence_messages`，避免把先前压缩摘要再次作为新证据。
provider 声明 checkpoint API v2，必需 checkpoint 未提交时会抛出异常。这描述的是 provider 契约：仓库的
[集成能力矩阵](capabilities.md) 仍将 Hermes pre-compaction 声明为 `unsupported`，并未确立经过真实宿主验证的
required-capable 生命周期。checkpoint 时 Source 已持久化与提取成功是不同结果；Prepared Context、checkpoint
已提交、宿主注入、模型使用和答案正确也不能等同。Secret 匹配只是一组有限规则，不保证清除所有敏感信息。

数据有三个独立方向：Hermes 向 Server 发送 query、捕获内容和显式操作；Server 可能向配置的 embedding、reranking
或 generation 后端发送 query、候选或证据；Hermes 可能把返回的历史上下文放进自己的模型请求。
环回 Server URL 只描述第一段连接，不能推出后续没有网络外发。使用第三方插件代码本身也不等于第三方网络服务接收数据。
部署和支持载荷的边界见 [Server 配置](../operate/configuration.md)、
[推理隐私保护](../operate/inference-protection.md) 和[上下文复用与最终准入](../operate/context-protection.md)。
推理保护默认 `disabled`；`shadow` 和 `advisory` 只观察请求，不阻止交付，也不检查响应。
安装该 provider 不代表所有 Hermes 模型流量都具备 enforcing 保护。

改变这些开关不会删除已有 Source、Memory、revision、trace 或模型供应商记录。
关闭 generation 不会关闭 Source 捕获；Source 已接受也不代表 Memory 已生成。

## 把提示词时上下文交给 gateway

Hermes 的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；
设置 `PC_GATEWAY_SEDIMENT=on` 后，gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，
拒绝不是 gateway 注入的 prepared-context 块，在每种隐私模式下都是如此：
Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。
provider 的召回会向每个提示词追加这样的块，因此请关闭召回；gateway 会采集各轮，因此也请关闭 turn 采集。
Hermes 直接调用模型时，gateway 不承载这些流量，请保留默认值。

### 关闭 provider 的提示词时召回和 turn 采集

在启动 Hermes 的环境中设置这两个开关，
或在 `$HERMES_HOME/powercontext/config.json` 中保存 `"prompt_recall": false` 和 `"capture_turns": false`，
然后重启 Hermes：

```bash
export POWERCONTEXT_HERMES_PROMPT_RECALL=false
export POWERCONTEXT_HERMES_CAPTURE_TURNS=false
hermes
```

关闭召回后，provider 不发送准备请求，也不向提示词注入上下文。它也不提供 `powercontext_prepare_context`，
因为该工具的结果会留在对话中，Core 会拒绝该对话之后的每个请求。
其他 `powercontext_*` 工具、`/pc` 命令和会话结束时的 flush 照常工作。
关闭 turn 采集后，provider 不再保存完成的 turn，但会话结束时的 flush 仍会请求处理该 Scope 中待处理的 Source，
其中包括 gateway 在该 Scope 中采集的各轮。
请保持 `capture_pre_compress` 关闭（默认值）：开启后，它会在每次压缩前保存新的用户和助手消息，
而 gateway 已把这些内容作为各轮采集。

修改后请新建 Hermes 会话（`/new`）。Hermes 会保存召回追加到之前每条用户消息中的上下文，并随对话历史重新发送，
也会保留 `powercontext_prepare_context` 的结果。gateway 不会从请求中移除这两者，因此对于含有它们的会话，
Core 会拒绝之后的每个请求。

### 让 Hermes 指向 gateway

在 `~/.hermes/config.yaml` 中添加一个指向 gateway `/gateway/v1`、使用 Responses transport 的命名 provider，
并选中它：

```yaml
providers:
  powercontext-gateway:
    api: http://127.0.0.1:8787/gateway/v1
    key_env: PC_GATEWAY_KEY
    transport: codex_responses

model:
  provider: custom:powercontext-gateway
  default: approved-model
  max_tokens: 32000
```

把 gateway 的 `PC_GATEWAY_API_KEY` 值以 `PC_GATEWAY_KEY` 写入 `~/.hermes/.env`；Hermes 把它作为 bearer token 发送。
在 Responses 路由上，gateway 以非流式方式调用模型，再把通过检查的响应以流式重放，因此流式的一轮能完成并被采集。
请保持使用 Responses transport。
使用 `transport: chat_completions` 时，gateway 在 Chat Completions 路由上转发流式请求。
enforcing 模式的 Core 会扣留流式 Chat Completions 响应，gateway 返回 HTTP 400。
非 enforcing 模式的 Core（包括默认的 `disabled` 模式）下，gateway 会原样放行流式响应。
设置 `PC_GATEWAY_SEDIMENT=on` 后，gateway 还会拼接终止流式响应中的助手文本，并采集已完成的回合。

### 选择 Core 目标

在任何隐私模式下（包括 `disabled`），Core 都需要为 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 指定的 profile
配置目标。请将该变量设为包含 Hermes 获准 endpoint、模型和协议的 Responses profile。没有该 profile 时，Core
以 `target_unsupported` 暂缓每个请求，gateway 对每一轮都返回 HTTP 400。
见[配置受支持的目标](../operate/inference-protection.md#配置受支持的目标)。

Hermes 0.20.4 向自定义端点发送的每个 Responses 请求都带有 `store: false` 和 `prompt_cache_key`，
只有设置了 `model.max_tokens` 时才带 `max_output_tokens`。满足以下条件时，enforcing 模式的 Core 会接受它的请求：

- 设置了 `model.max_tokens`，
  且目标声明了 `context_window_tokens`、不小于 `model.max_tokens` 的 `max_output_tokens` 和 `validated_utf8_token_bound`。
  没有输出上限时，Core 以 `output_limit_required` 暂缓每个请求；
- 目标的 `allowed_cache_retention` 包含 `provider-default`。否则 Core 以 `cache_retention_unapproved` 暂缓每个请求；
- 供应商返回的模型名与 `model` 不同时，目标在 `response_models` 中列出这些名称。
  否则 Core 以 `response_model_mismatch` 暂缓每个响应。

窗口检查把请求的每个 UTF-8 字节计为一个 token，
因此 `context_window_tokens` 要为 Hermes 的工具定义、系统提示词和对话留出余量。
gateway 在启动时发现 Core 的模式，修改模式后请重启 gateway。
选择模式前，请阅读[上下文复用与最终准入](../operate/context-protection.md)和[推理隐私保护](../operate/inference-protection.md)。

### 配置 gateway

- 把 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 设为 Hermes Responses 路由使用的 Core profile。不要设置
  `PC_GATEWAY_UPSTREAM_URL*`；这些旧变量会阻止启动。把 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为供应商密钥，
  把 `PC_GATEWAY_API_KEY` 设为 Hermes 发送的密钥，把 `PC_GATEWAY_CORE_TOKEN` 设为 Gateway 服务凭据，并设置
  `PC_GATEWAY_SEDIMENT=on`。Core 的访问控制必须为 `enforced`；为服务身份授予 `gateway.user` 和 `server.observer`，
  开启采集时再授予 `scope.contributor`。绑定命令见[网关模型目标](../operate/deploy-server.md#网关模型目标)。
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话，
  而不是 provider 为每个 workspace 解析的 Scope。
  请把 `PC_GATEWAY_DEFAULT_SCOPE` 设为 `hermes powercontext status` 输出的 `scope_id`，
  或把 provider 的 `scope_id` 和 `PC_GATEWAY_DEFAULT_SCOPE` 设为同一个已有的 Scope。
- 请以组合部署（`PC_GATEWAY_CORE_UDS`）运行 gateway，它默认会向 Hermes 的请求注入上下文。
  Hermes 0.20.4 不向自定义 Responses 端点发送按会话区分的请求头，而 provider 的 `extra_headers` 对每个会话都相同，
  因此 gateway 改为从请求本身推导会话。
  Hermes 在每个请求中发送完整历史，不使用 `previous_response_id` 或 `conversation`。
  它把每个会话只构建一次的系统提示词放在 `instructions` 中，历史从第一条用户消息开始。
  因此，gateway 每条用户消息只准备一次，并把该上下文重放给工具循环步骤。
- 推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
  系统提示词和第一条用户消息相同的会话共用同一个 ID。
  gateway 仍会采集每个完成的回合。没有会话头时，采集没有在 gateway 重启后保持不变的会话标识，也无法使用前缀提取。
  压缩会重建 Hermes 的系统提示词并改写历史，因此压缩之后，gateway 可能推导出一个新会话，并重新准备。
- Hermes 发往同一 provider 的辅助请求（例如标题或压缩摘要）带有各自的提示词，
  因此 gateway 为每个这类请求推导一个单独的会话并准备上下文；设置 `PC_GATEWAY_SEDIMENT=on` 后，还会把它的完成响应作为一轮采集。
- 独立部署的 gateway（`PC_GATEWAY_CORE_BASE`）只有在 `PC_GATEWAY_INJECTION=on` 时才注入。
  不要在 `extra_headers` 中放固定的 `X-PowerContext-Conversation-ID`，否则 gateway 会把所有会话当作同一个会话。

见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)和[网关认证边界](../operate/deploy-server.md#网关认证边界)。

## 只在需要时启用自动提取

完成轮次采集只创建 Source 证据，不会自行创建 Memory。自动 Source-to-Memory 提取需要在 Server 上配置 generation
model：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
powercontext capabilities
```

能力输出必须显示 Memory extraction 已启用。显式执行 `hermes powercontext remember` 不需要模型。
