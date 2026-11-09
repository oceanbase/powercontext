# OAuth 订阅

Gateway 直接持有认证、发送请求并解析模型结果，不需要串联 republic、Magpie 或 Goose 服务。
一次进程配置一个订阅渠道；模型仍由请求指定，必须与账号权益和该模型的接口匹配。
下表由人工维护，不通过代码名单或 CI 门禁约束 Python 与 Rust 的支持范围。

| 渠道 | 认证与续期所有权 | 模型接口 | 权益与验证边界 |
| --- | --- | --- | --- |
| ChatGPT / Codex | 显式指定官方 CLI `auth.json`，每次读取；不兑换或改写 CLI 刷新令牌 | Responses；请求 `store:false`、`stream:true`，完整终态转成 Response JSON 后交 Core 检查 | 账号须有对应 Codex 权益；access token 被拒时由 CLI 重新登录或续期，Gateway 不重放请求 |
| GitHub Copilot | Gateway 设备授权或显式指定含 `oauth_token` 的私有文件；按 GitHub token 缓存短期 Copilot session，提前 120 秒重新交换 | Chat Completions / Responses；由模型本身支持的 endpoint 决定 | 支持 github.com 默认服务；不支持 GHE、任意 session endpoint、CLI direct-token、Auto 模型协商或自动接受条款 |
| Grok | 显式指定官方 CLI `auth.json`；距到期 60 秒时以 `auth.json.lock` OS 锁协调刷新，持锁重读、保留其他字段、原子替换 | xAI HTTP OAuth 的 Chat Completions / Responses | 对应 republic 的 `api.x.ai/v1` 路径；不宣称与 Magpie 插件 `cli-chat-proxy.grok.com` 的 SuperGrok 权益路径等价 |
| Qwen Code | Gateway 自有设备授权与独立凭据文件；标准刷新、文件锁、原子替换 | 当前 Plus / Pro OAuth 的 Chat Completions，固定 `api.qwen.ai/compatible-mode/v1` | 与旧版免费 OAuth、Bailian Coding Plan 和 API Key Token Plan 分开；需当前 Qwen 订阅权益 |
| Kimi Code | Gateway 自有设备授权与独立凭据文件；声明 PowerContext 产品和设备身份；标准刷新、文件锁、原子替换 | Coding 服务的 Chat Completions / Responses；不转换 Anthropic-only 模型 | 需要对应地区、账号和模型的 Coding 权益；不将 Moonshot Open Platform API Key 算为 OAuth 订阅 |
| Gemini CLI / Code Assist | 显式指定 CLI `oauth_creds.json`，只读 access token 与 expiry；CLI 保留续期和 onboarding 所有权 | Chat 入口转最终 Code Assist envelope，非流式 `generateContent`；完整响应转最终 Chat JSON 后检查 | 通过 `loadCodeAssist` 确认项目及 tier；未完成 onboarding、过期登录或未知字段拒绝；不等同 ADC、Gemini API Key 或个人 Gemini 权益保证 |
| Claude Code 订阅 | 使用真实 CLI 的既有 `claude.ai` 登录；CLI 保留续期和内部 HTTP／重试所有权 | 新建单轮 `--print` 文本执行，禁用 tools、MCP 与会话持久化；不提供 Responses | Gateway 检查的是实际送入 stdin 的完整 JSON prompt 及归一化 Chat 结果；enforcing／Core 能力未知时在模型子进程启动前拒绝，不声称观察内部 HTTP |

这些实现使用合成凭据、受控 HTTP 与真实临时文件验证。没有读取用户真实 token，没有调用付费模型，
没有执行真实服务、订阅额度或官方 CLI 版本的验收；Grok 的订阅权益、Kimi、Copilot 的兼容字段接纳和
模型／账号权限，均不能由本地通过的测试推定。Kimi 的 `kimi_cli`／`0.1.0` 与 Copilot 的编辑器版本字段
沿用成熟生态的协议兼容标识；User-Agent 标识 PowerContext，设备 UUID 属于独立授权。

## 配置

```bash
# Reuse a CLI-owned Codex login without taking ownership of its refresh token.
PC_GATEWAY_SUBSCRIPTION=codex \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/codex/auth.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-codex \
./target/release/pc-gateway

# Create a fresh gateway-owned login. The destination must not exist.
install -d -m 700 /private/powercontext/oauth
./target/release/pc-gateway login qwen /private/powercontext/oauth/qwen.json

PC_GATEWAY_SUBSCRIPTION=qwen \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/powercontext/oauth/qwen.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-qwen \
./target/release/pc-gateway

# The same login command supports copilot and kimi.
./target/release/pc-gateway login copilot /private/powercontext/oauth/copilot.json
./target/release/pc-gateway login kimi /private/powercontext/oauth/kimi.json

# Reuse an onboarded Gemini CLI login; project discovery uses that same access token.
PC_GATEWAY_SUBSCRIPTION=gemini \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/gemini/oauth_creds.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-gemini \
./target/release/pc-gateway

# Claude CLI owns its login. Core must confirm a non-enforcing deployment.
PC_GATEWAY_SUBSCRIPTION=claude \
CLAUDE_CLI_EXECUTABLE=/usr/local/bin/claude \
PC_GATEWAY_UPSTREAM_PROFILE=approved-claude-cli \
./target/release/pc-gateway
```

HTTP 渠道的 `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS` 必须显式设置；Gateway 不搜索用户主目录或系统 keychain。
Claude 不需要此变量，由 CLI 自己读取其登录。API Key、cloud mode、base URL 或 OAuth token 的环境覆盖会拒绝
Claude 执行；可以用 `CLAUDE_CLI_EXECUTABLE` 指定已安装的官方 CLI。Gateway 不自行下载或更新 CLI。
Grok 使用 `grok` 和官方 CLI 的 `auth.json`；Codex 使用 `codex`。Qwen、Kimi 的 CLI 文件不能直接作为
Gateway 自有文件使用，也不能将 CLI 的 refresh token 复制后视作独立登录。
新登录写入权限为 `0600`，已有文件不覆盖；重新授权应写入新路径，再由运维更新配置。
凭据目录只允许服务用户写入，存放在支持 OS 锁和原子 rename 的本地文件系统上。

启用 HTTP 订阅时，内置默认值只指向上表所述 HTTPS endpoint；Claude 固定内部目标为 `claude-cli://execute`。
`PC_GATEWAY_UPSTREAM_URL_CHAT` 和
`PC_GATEWAY_UPSTREAM_URL_RESPONSES` 可显式关闭相应路由（空值）或选择该 provider 允许的 endpoint；
不允许任意主机、用户信息、非默认端口、query、fragment 或重定向。Kimi 可选择 `.com` 或 `.ai` 的
`/coding/v1` endpoint，但登录与地区权益仍需人工确认。Qwen、Gemini、Claude 不提供 Responses 路由；
Codex 不提供 Chat 路由。Gemini 与 Claude 拒绝客户端 streaming 请求。
API Key 配置仅用于未启用订阅的模式；客户端请求的 header、body 或自报内部标记都不能选择凭据。

Core 必须批准对应 `PC_GATEWAY_UPSTREAM_PROFILE`。认证及固定产品元数据不放进模型内容载体；
最终协议变换后的请求正文仍需通过每次 `before_send` 检查。Codex 与 Kimi Responses 的完整 SSE
终态先转成 JSON，再交 `before_release`；缺失终态、截断或超限不释放、不重发。
401、403、429、5xx 和请求送达不确定都不会触发认证层自动重放模型请求。
标准 OAuth 兑换和设备码轮询由 `oauth2` 5.x 执行；Copilot 的 GitHub session 交换是单独的非标准接口。

Gemini 的 Core profile 使用 `protocol=google-codeassist-chat`、实际 Code Assist endpoint 和模型；
最终预算位于 `request.generationConfig.maxOutputTokens`，由 Chat 的 `max_tokens` 或
`max_completion_tokens` 转换。响应转换在 `before_release` 之前完成。文本、普通函数工具及工具回传可转换；
图像、未知字段、严格函数 schema 与不受检 opaque 内容不算完整支持；含 `thoughtSignature` 的响应拒绝释放，
不能宣称支持需要签名回传的工具循环。明文 thought 保留为 `reasoning_content`，纳入最终结果检查。
`loadCodeAssist` 失败不触发模型调用。
可用 `GOOGLE_CLOUD_PROJECT` 或 `GOOGLE_CLOUD_PROJECT_ID` 提供账号已配置的项目，不自动 onboarding。

Claude 的 Core profile 使用 `protocol=claude-cli-prompt` 和唯一 endpoint `claude-cli://execute`。
该协议在 Core enforcing 时始终 withhold；Gateway 也在 enforcing／能力未知时拒绝启动，不能自动降级。
非 enforcing 模式只提供 prompt／结果观察，不覆盖 CLI 内部 HTTP、预算和重试。JSON 文本结果可由调用方解析；
无工具循环、模型参数、原生 schema 输出、常驻会话或取消恢复。POSIX 取消会杀死实际进程组，丢弃输出；
其他平台及真实 CLI 版本还需对应环境验收。

Codex 强制 `stream:true` 的请求需在 approved Responses profile 上允许流式请求；输出仍全量检查后释放。
Codex 官方 endpoint 不支持 `max_output_tokens`，所以不能满足 Core 要求显式可执行输出上限的 enforcing
预算。Gateway 不伪造上限、不删除 Core 预算约束，结果可能为 `output_limit_required`。

取消正在等待的刷新会关闭其 future 并释放锁，不留下后台 writer。服务器是否已消费旋转令牌无法由客户端
取消证明；若远端已旋转而本地未获响应，需重新登录。进程崩溃或磁盘写入失败也可能需要重新登录，
OS 锁和原子替换不能消除这种跨 HTTP 与文件系统的提交窗口。

## 协议适配与明确不支持的范围

Grok Responses 迁移了 Magpie 的普通函数 namespace 展平（包括历史调用与 `tool_choice`）、不支持的工具
过滤、孤立 `tool_choice` 移除、`external_web_access` 移除、reasoning 的 null `content` 移除，
以及未加密 `agent_message` 转普通用户消息。`call_id` 和工具结果保持配对。密封或未知内容仍交 Core 判断。
本实现对超过 64 字节的展平名称、非 object 根或顶层 union 工具 schema 明确拒绝，不迁移 Magpie 的
hash 截断与 schema union 合并；不宣称覆盖 Magpie 全部 Grok 测试。
Codex 请求保持 Responses 格式，不做 Chat 转换。Claude HTTP OAuth 未实现；真实 CLI 的受限单轮执行与
Magpie 常驻 Claude Code、MCP 工具回传、取消回滚和会话恢复是不同契约，不能宣称等价。
Gemini 关闭 Gateway 注入，避免把 `/messages` 复用定位冒充最终 `/request/contents` 定位。

内部生成经 Gateway 仍有独立边界：全局 injection／sediment 无法可信区分内部生成，可能递归召回或反复沉淀。
仅更改 Server 内部模型 base URL 不构成完整支持；不要将普通客户端自报 header 当作检查豁免。

## 验证与来源

```bash
cargo test --locked -p pc-gateway-auth
cargo test --locked -p pc-gateway-core --test executor_semantics
cargo test --locked --workspace -- --skip live_
```

`live_context_check` 的四项测试另依赖本机已配置的 Core、enforcing 策略与 `coding` profile；
不包含在上述受控验证命令中。

共享 runner 直接读取 `tests/fixtures/oauth/contract.json` 与 `upstream/republic-grok.json`，拒绝未知 schema、profile 和 action；
用真实本地 token／resource HTTP 服务执行其文件热加载、轮换、拒绝、目标限制、重定向与取消场景。
它不替代 Core 执行器测试。跨进程刷新另外启动真实 Rust test workers，验证同一文件只兑换一次并能重新读取。
设备授权和 provider 专属会话的测试位于 Rust 认证模块内，不复制公共夹具。
共同 provider 向量也从 `tests/fixtures/oauth/upstream/provider-vectors.json` 消费；额外 Magpie wire fixture
保留原始输入及独立断言。精确版本与测试出处记录在 fixture 来源字段和公共 README；统一许可见
[LICENSES/oauth.md](../LICENSES/oauth.md)。
