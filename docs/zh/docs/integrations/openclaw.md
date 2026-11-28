---
title: OpenClaw
description: 为 OpenClaw 安装 PowerContext memory 插件，并控制召回、采集、scope 和持久化写入。
status: community
---

# OpenClaw

`community`

## 安装或刷新插件

从同一个 `master` revision 安装 CLI 和插件：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup openclaw
```

未显式指定地址时，setup 会读取环境变量和已保存的客户端配置；均未配置时使用 `http://127.0.0.1:8000`。

也可以使用本地 checkout：

```bash
powercontext setup openclaw --source .
```

`setup openclaw` 会用 pnpm 构建插件，用 `pnpm pack` 打包，再通过
`openclaw plugins install --force --no-enable <archive>` 安装该归档。随后它只补充缺失的激活设置：启用插件，把它选为
`memory` 插件槽，开启 `autoRecall`、`autoCapture` 和会话访问，在首次激活时把 PowerContext 工具加入
`tools.alsoAllow`，并重启 OpenClaw gateway。如果你已禁用或拒绝该插件、在非空的 `plugins.allow` 中没有列出它，
或选择了其他 memory 插件，setup 不会激活它。它不会启动 Server。启动 Server 后，开启新的 OpenClaw 会话：

```bash
powercontext server run
openclaw
```

setup 和插件都要求 OpenClaw 2026.8.1-beta.2 或更新版本。`plugins install` 不支持 `--no-enable` 的版本（如
2026.8.1）会在安装时启用插件。在这些版本上，setup 运行
`openclaw plugins install --force --accept-capabilities <archive>`（仅当该版本列出 `--accept-capabilities` 时才传入），
并先检查安装不会覆盖你的选择。如果 `plugins.deny` 列出了该插件、`plugins.allow` 非空且不含该插件，
或在该插件和全部插件都未被禁用时 `plugins.slots.memory` 选择了其他插件，setup 会直接停止，不做任何改动。在这些版本上，
插件在安装完成时即已激活，早于 setup 写入其 endpoint；如果之后的 setup 步骤失败，请排除原因后重新运行 setup。

## 数据流与捕获控制

以下设置位于 `plugins.entries.memory-powercontext.config`。`autoRecall` 和 `autoCapture` 默认都为 `true`，
两者独立工作；修改配置后重启 gateway。

| 事件/入口 | 发送的数据 | 直接接收方 | 默认/触发条件 | 关闭方式 | 关闭后影响 |
| --- | --- | --- | --- | --- | --- |
| `before_prompt_build` | 最新用户文本（或 prompt 回退值），识别出的凭据替换为 `[REDACTED]` 后去掉首尾空白，超过 8192 个字符或 8192 个 UTF-8 字节时保留同时满足两项上限的最近文本；以及 Scope 和 prepare 预算 | 配置的 PowerContext Server；返回上下文进入宿主模型请求 | `autoRecall=true`，符合条件的私密会话 | `autoRecall=false` | 停止自动准备/注入；捕获和显式工具仍独立工作 |
| 成功的 `agent_end` | 加上 `User:` 前缀的最新用户文本、确定性 Source ID 和来源元数据；文本含有识别出的凭据时不发送 | 配置的 PowerContext Server | `autoCapture=true`，符合条件的私密会话 | `autoCapture=false` | 停止新增自动 Source；召回和显式写入仍可用 |
| `before_compaction` | 捕获启用时发送同样有界的最新用户 Source；发送 Scope 执行有界 flush | 配置的 PowerContext Server | best-effort 边界事件 | `autoCapture=false` 关闭新增捕获，不关闭 pending flush | 仍可 flush 已接受的 position；不会阻止压缩 |
| `session_end` / `gateway_stop` | 用于 flush 已接受 pending position 的 Scope；不发送新 transcript | 配置的 PowerContext Server | 存在 pending position 且 extraction 可用 | 没有独立的 flush 关闭设置 | 关闭捕获会阻止新增 Source，但不会取消已有 pending 工作 |
| 显式 search/get/store/revise/retire | 请求的 query、引用或修改载荷 | 配置的 PowerContext Server | 显式工具执行，受宿主控制和访问检查约束 | 不调用，或在 OpenClaw 中限制工具 | 自动召回/捕获开关不会关闭显式工具 |

自动捕获排除 group、channel 和 incognito 会话。`captureMaxChars` 默认 `4000`（范围 `128`–`20000`）：
包含 `User: ` 前缀的最新用户文本超限时会被**截断**，而不是拒绝，使用 JavaScript 字符串长度和切片。
这不是 UTF-8 字节限制，也不是完整 transcript 同步。
自动召回和捕获遵循[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别的凭据包括 `api_key=…`、`password: …` 等凭据赋值，
`Authorization` 和 `Cookie` 头，bearer 与服务商 token，私钥，以及 `tokenEnv` 所指环境变量中的 token。召回查询在截断前
把它们替换为 `[REDACTED]`；最新用户文本含有其中任一项时不采集，检查针对 `captureMaxChars` 截断前的完整文本。
只是包含标记字样的普通词语（例如 `token budgets`）不算凭据。该规则识别常见形态，不能覆盖所有 secret。
会话资格筛选和截断不是 secret 过滤。要阻止这些自动路径向 Server 发送 prompt 文本，需同时设置
`autoCapture=false` 与 `autoRecall=false`，或排除该会话。显式工具仍独立工作，仍可发送其操作参数。

数据流有三个独立方向：OpenClaw 向配置的 Server 发送 query、Source 和显式操作；Server 启用的
embedding/reranking/generation 可能向其配置的推理后端发送 query、候选或 evidence；返回的历史上下文在注入后
进入 OpenClaw 选择的模型。loopback Server 仅说明第一段连接位置，不代表后续没有网络调用。使用第三方插件代码
本身也不表示第三方网络服务收到数据。参见[配置](../operate/configuration.md)、
[推理保护](../operate/inference-protection.md)与[上下文准入](../operate/context-protection.md)。
保护默认关闭；shadow/advisory 不阻止信息外发。enforcing 保护需要受支持的目标和经过检查的载荷，
不能假定每个 OpenClaw 模型请求都受到此保护。

Source 接受不等于 Memory extraction 已完成。关闭 generation 不会关闭捕获；extraction 不可用时，已接受 Source
仍保持 pending。改变开关不会删除已有 Source、Memory、revision、trace 或供应商保留记录。
准备完成、checkpoint 确认、宿主注入、模型实际使用和答案正确是不同结果。边界确认语义见下文。

### 召回与显式工具

自动召回启用时，OpenClaw 构建 prompt 前，插件会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`。召回内容会被标记为
不可信历史证据。当前 system instruction、仓库规范和用户请求始终优先。

来自 direct/private 会话的符合条件用户提示词会被独立采集为 Content Source，并使用确定性 source id，重复采集是
幂等的。group、channel 和 incognito 会话会被排除。插件不会同步完整 OpenClaw transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：prompt 不变，普通工作不会被阻塞。

插件暴露五个 memory 工具：`powercontext_memory_search`、`powercontext_memory_get`、`powercontext_memory_store`、
`powercontext_memory_revise` 和 `powercontext_memory_retire`。写工具需要模型显式调用，由 OpenClaw 控制
side-effecting 工具的执行。

### 压缩前 checkpoint

插件通过 OpenClaw 公开的 `before_compaction` 事件提供 **best-effort** 压缩前 checkpoint。它只解析一次 Scope，
采集最新一条符合条件的 user message，并派生确定性 Source ID；`agent_end` identity 使用宿主公开的 `runId`。
`before_compaction` 没有唯一事件 ID，因此只在这条低频路径上，以规范化的对象字段顺序流式哈希完整、JSON 形态的事件
messages；语义相同的重复 payload 会保留同一个 identity，后续不同 payload 则保持独立，同时不会构造 transcript 大小的
中间字符串。随后保留返回的 Source journal position，并对同一个 Scope 执行 flush。只有完整、合法的 flush 响应满足
`current_cursor >= position` 时才确认
checkpoint；仅有 HTTP 2xx 不算确认。

当 cursor 落后或 `remaining_work` 为 true 时，插件会在从 flush 入队时开始计算的一秒边界总预算内最多调用 `flushMaxCalls` 次（默认 `4`，
有效范围 `1`–`16`）；`timeoutMs`（默认 2500 ms）仍是单次请求上限。未确认的已接受 position 会继续保留，供 Server
调度或之后在 `session_end`、`gateway_stop` 执行有界 flush。关闭任务的优先级高于尚未开始的压缩前任务，但排队等待
也计入各事件的边界预算。关闭事件只 flush 已接受的 position，不会被报告成压缩前事件。边界在排队期间耗尽预算时，
会发出不含内容的 `deadline_exceeded` 诊断并保留 pending position。

OpenClaw 会捕获 `before_compaction` hook 的失败并继续压缩，因此该集成不具备 `required_capable` 语义。失败诊断
只包含宿主、生命周期事件、Scope 哈希关联值、已知的目标/观测 position 和稳定失败分类；不会包含 transcript、
prompt、query、凭据或 provider exception。

显式 search 调用 `/v1/memory/search`，get 读取精确的 Atomic Memory Artifact revision，不会调用
`/v1/context/prepare`。search 将查询限制为 8192 个字符，并把请求的结果上限约束在 1–50（默认 10）；
每次 get 最多返回 120 行、12,000 个字符。

## 选择 memory Scope

插件会在每次操作前请求 Server 解析一个已有 Scope。Server 按以下顺序检查：

1. 插件显式配置的 `scopeId`；
2. OpenClaw session、按 host 顺序排列的 active project，以及 agent identity 的持久 binding；
3. Server 默认 Scope。

OpenClaw 在构建 prompt 时报告本次 run 的 active project，但 `agent_end` 和 `before_compaction` 不携带它们。
`agent_end` 的捕获使用所捕获 run 的 project，pre-compaction checkpoint 使用该 session 最近一次 prompt 的 project，
因此两者都会命中本轮召回所读的 project binding。若 host 在这两个事件中自行报告 project，则以其为准。

agent、project、path 和 session identity 只作为 binding 查询输入。插件不会用它们推导 Scope ID，也不会创建 Scope。
若要让所有 OpenClaw 操作显式使用一个已有 Scope：

```bash
openclaw config set plugins.entries.memory-powercontext.config.scopeId scp_0123456789abcdefghjkmnpqrs
openclaw gateway restart
```

未配置 `scopeId` 时，仅在 OpenClaw host identity 必须保留选择的情况下，才在 Server 端配置持久 binding；否则使用
Server 默认 Scope。OpenClaw 当前没有 Scope 选择契约，因此插件不会自行持久化 binding。

## 连接启用鉴权的 Server

在受保护环境中启动启用鉴权的 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

插件从 `tokenEnv` 配置项指定的环境变量读取 Bearer token，默认是 `POWERCONTEXT_CLIENT_API_TOKEN`。Gateway
服务必须在自己的运行环境中获得该变量。请把同一个 token 的值加入 Gateway 服务环境或 `~/.openclaw/.env`：

```dotenv
POWERCONTEXT_CLIENT_API_TOKEN=<同一个 token 的值>
```

限制文件权限并重启 Gateway，使插件读取更新后的环境：

```bash
chmod 600 ~/.openclaw/.env
openclaw gateway restart
```

不要把凭据放进 endpoint。CLI 和插件默认拒绝非环回明文 HTTP；使用 HTTPS，或显式设置
`POWERCONTEXT_OPENCLAW_ALLOW_INSECURE_HTTP=true`（也可使用插件的 `allowInsecureHttp` 配置）。
HTTPS 证书校验仍然启用。安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。

## 把提示词时上下文交给 gateway

本节所说的 PowerContext gateway（`pc-gateway`）位于 OpenClaw 与其模型供应商之间，不是 OpenClaw 自己的 Gateway，即运行
OpenClaw Agent、通过 `openclaw gateway restart` 重启的服务。

OpenClaw 的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on`
后，gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
在每种隐私模式下都是如此：Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。
插件的召回会向每个提示词添加这样的块，因此请关闭召回；gateway 会采集各轮，因此也请关闭采集。然后重启 OpenClaw 的
Gateway：

```bash
openclaw config set plugins.entries.memory-powercontext.config.autoRecall false --strict-json
openclaw config set plugins.entries.memory-powercontext.config.autoCapture false --strict-json
openclaw gateway restart
```

memory 工具照常工作，它们都不准备上下文。关闭采集后，插件不再提交 Source，边界 flush 没有需要 flush 的内容；Server
的定时抽取会处理 gateway 采集的各轮。修改后请新建 OpenClaw 会话，因为 OpenClaw
可能把插件为之前的轮次添加的上下文保留在会话 transcript 中。OpenClaw 直接调用模型时，PowerContext gateway
不承载这些流量，请保留默认值。

在 `~/.openclaw/openclaw.json` 中添加一个指向 gateway `/gateway/v1`、`api: "openai-responses"` 的 provider，用 gateway
的 `PC_GATEWAY_API_KEY` 值作为它的 `apiKey`，并把它的模型（例如 `powercontext-gateway/approved-model`）设为
`agents.defaults.model.primary`：

```json5
{
  models: {
    providers: {
      "powercontext-gateway": {
        baseUrl: "http://127.0.0.1:8787/gateway/v1",
        apiKey: "${PC_GATEWAY_KEY}",
        api: "openai-responses",
        models: [
          { id: "approved-model", name: "Approved model", contextWindow: 400000, maxTokens: 32000 },
        ],
      },
    },
  },
}
```

`PC_GATEWAY_KEY` 必须进入 OpenClaw Gateway 服务的环境，例如通过 `~/.openclaw/.env`。在 Responses 路由上，gateway
以非流式方式调用模型，再把通过检查的响应以流式重放，因此流式的一轮能完成并被采集。使用 `api: "openai-completions"` 时，
gateway 在 Chat Completions 路由上转发流式请求，并原样放行流式响应；设置 `PC_GATEWAY_SEDIMENT=on` 后，
还会从中采集完成的一轮。

配置 Core 和 gateway 时还需要：

- 请让 Core 运行在非 enforcing 隐私模式（`disabled`、`shadow` 或 `advisory`）。OpenClaw 不向自定义 provider 发送 `store`
  字段，因此 enforcing 模式的 Core 会暂缓它的每个 Responses 请求，gateway 返回 HTTP 400。gateway 在启动时发现 Core
  的模式，修改模式后请重启 gateway。
- 此处使用 Responses 路由，应将 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 设为 Core 中 endpoint、`openai-responses`
  协议和模型均匹配的 target profile。未设置时该路由返回 HTTP 501 `responses_route_disabled`。
  将 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为该目标的服务商 Key，并设置 `PC_GATEWAY_SEDIMENT=on`。
  不要设置 `PC_GATEWAY_UPSTREAM_URL*`；这些旧变量会导致启动失败。
- 即使监听回环地址也必须设置非空 `PC_GATEWAY_API_KEY`，并将 `PC_GATEWAY_CORE_TOKEN` 设为已认证的服务身份。
  Core 访问控制必须为 `enforced`；在所选 Scope 上授予 `gateway.user`，并在 `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID`
  对应的 Server resource 上授予 `server.observer`（默认 deployment ID 为 `powercontext`）。本指南开启了采集，
  因此还要授予 `scope.contributor`（包含 `scope.read`）来替代 `scope.viewer`。
  详见[Gateway 认证与目标要求](../operate/deploy-server.md#网关模型目标)。
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话，而不是插件为每个会话或项目解析的
  Scope。请把插件的 `scopeId` 和 `PC_GATEWAY_DEFAULT_SCOPE` 设为同一个已有的 Scope。
- OpenClaw 2026.8.1 只向 OpenAI 自己的端点发送会话头，而 provider 的 `headers` 对每个会话都相同，
  因此 OpenClaw 的请求不会标识会话。组合部署（`PC_GATEWAY_CORE_UDS`）会改为推导会话：在自定义 provider 上，
  OpenClaw 每次请求都发送会话的完整历史，开头是它的系统提示词和第一条用户消息，因此 gateway 每条用户消息准备一次，
  并把结果重放给工具循环步骤。系统提示词和第一条消息都相同的会话会共享推导出的会话。独立运行的
  gateway（`PC_GATEWAY_CORE_BASE`）只有设置 `PC_GATEWAY_INJECTION=on` 才会注入。不要在 provider 的 `headers`
  中放固定的 `X-PowerContext-Conversation-ID`，否则 gateway 会把所有会话当作同一个会话。

见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)和[网关认证边界](../operate/deploy-server.md#网关认证边界)。

## 验证安装

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` 会检查 OpenClaw CLI 是否存在，以及 `openclaw plugins list --enabled --json` 是否将
`memory-powercontext` 报告为已加载并选中 memory slot。修改 PowerContext 配置后需要重启 OpenClaw gateway。
