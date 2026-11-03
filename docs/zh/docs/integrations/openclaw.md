---
status: community
title: OpenClaw
description: 为 OpenClaw 安装 PowerContext memory 插件，并控制召回、采集、scope 和持久化写入。
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

`setup openclaw` 会用 pnpm 构建插件，通过 `openclaw plugins install --link --force` 安装，把它启用为 `memory`
插件槽，把 PowerContext 工具加入 `tools.alsoAllow`，并重启 OpenClaw gateway。它不会启动 Server。启动 Server 后，
开启新的 OpenClaw 会话：

```bash
powercontext server run
openclaw
```

插件要求 OpenClaw 2026.8.1-beta.2 或更新版本。

## 数据流与捕获控制

以下设置位于 `plugins.entries.memory-powercontext.config`。`autoRecall` 和 `autoCapture` 默认都为 `true`，
两者独立工作；修改配置后重启 gateway。

| 事件/入口 | 发送的数据 | 直接接收方 | 默认/触发条件 | 关闭方式 | 关闭后影响 |
| --- | --- | --- | --- | --- | --- |
| `before_prompt_build` | 最新用户文本（或 prompt 回退值），限制为 8192 UTF-8 字节，以及 Scope 和 prepare 预算 | 配置的 PowerContext Server；返回上下文进入宿主模型请求 | `autoRecall=true`，符合条件的私密会话 | `autoRecall=false` | 停止自动准备/注入；捕获和显式工具仍独立工作 |
| 成功的 `agent_end` | 加上 `User:` 前缀的最新用户文本、确定性 Source ID 和来源元数据 | 配置的 PowerContext Server | `autoCapture=true`，符合条件的私密会话 | `autoCapture=false` | 停止新增自动 Source；召回和显式写入仍可用 |
| `before_compaction` | 捕获启用时发送同样有界的最新用户 Source；发送 Scope 执行有界 flush | 配置的 PowerContext Server | best-effort 边界事件 | `autoCapture=false` 关闭新增捕获，不关闭 pending flush | 仍可 flush 已接受的 position；不会阻止压缩 |
| `session_end` / `gateway_stop` | 用于 flush 已接受 pending position 的 Scope；不发送新 transcript | 配置的 PowerContext Server | 存在 pending position 且 extraction 可用 | 没有独立的 flush 关闭设置 | 关闭捕获会阻止新增 Source，但不会取消已有 pending 工作 |
| 显式 search/get/store/revise/retire | 请求的 query、引用或修改载荷 | 配置的 PowerContext Server | 显式工具执行，受宿主控制和访问检查约束 | 不调用，或在 OpenClaw 中限制工具 | 自动召回/捕获开关不会关闭显式工具 |

自动捕获排除 group、channel 和 incognito 会话。`captureMaxChars` 默认 `4000`（范围 `128`–`20000`）：
包含 `User: ` 前缀的最新用户文本超限时会被**截断**，而不是拒绝，使用 JavaScript 字符串长度和切片。
这不是 UTF-8 字节限制，也不是完整 transcript 同步。此捕获路径筛选会话/消息类型并截断文本，不执行全面 secret 脱敏。
资格检查或任何已配置的 secret 检测规则，都不能保证识别所有敏感内容，也不是完整 DLP。

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

## 验证安装

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` 会检查 OpenClaw CLI 是否存在，以及 `openclaw plugins list --enabled --json` 是否将
`memory-powercontext` 报告为已加载并选中 memory slot。修改 PowerContext 配置后需要重启 OpenClaw gateway。
