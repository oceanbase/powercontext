# OAuth 行为契约

`contract.json` 是 Python 与 Rust runner 共用的语言无关场景，全部凭据均为合成值。
当前 profile 是 Codex CLI 只读接入与 Grok CLI 文件续期；Copilot 与 ChatGPT Plan Usage
采用 Pydantic AI 原生接口，其不同的授权协议不强行套用文件续期 profile。

## 格式与执行

- `schema_version` 为格式版本；runner 必须拒绝未知版本和未知 action，不能静默跳过场景。
- `profiles` 声明凭据所有权、允许的 HTTPS origin、资源及 token 端点。时间为 Unix 秒。
  Grok 在距到期时间不超过 60 秒时刷新；Codex 不主动检查或刷新 CLI access token。
- 每个 scenario 独立建立临时 `auth.json`，`credentials` 是官方文件格式的完整 JSON；
  `null` 表示内容畸形。`now` 是确定性时钟，不能依赖运行机器日期。
- `http.resource_response` 是每次资源请求的受控响应；`http.token_responses` 是每次
  token 兑换的响应。`text` 可表示无效 JSON；`headers` 可表示重定向。
  `wait_for_cancel` 让 token HTTP 交互阻塞，runner 必须等到该交互开始再取消调用。
  所有未声明的 HTTP 目标均应让测试失败。
- `actions` 依次表示调用方行为：`request` 发起一次资源请求；`replace_credentials`
  模拟外部原子替换文件；`concurrent_requests` 使用独立凭据实例同时请求同一个文件；
  `cancel_during_refresh` 取消正在等待 token 响应的调用。
  不指定 `url` 时使用 profile 的资源端点。
- `outcome` 为 `success`、`http_error`、`auth_error` 或 `cancelled`。
  `http_error` 必须保留指定 HTTP 状态；认证失败不得将文件内容、路径或服务返回的
  `synthetic-secret-error` 暴露在错误链中。取消必须传播给调用方。
- `expect.resource_tokens` 是实际发送到资源端点的 Bearer token；并发场景按多重集比较，
  其他场景按调用方 action 顺序比较。Codex 请求同时携带当前文件的 `chatgpt-account-id`。
- `expect.refresh_forms` 是实际 token 请求的 URL 编码表单，必须无 Authorization header。
  这个外部兑换预算保护单次刷新令牌及计费行为，不约束私有函数调用次数。
- `expect.store.mode=unchanged` 要求文件与最近一次外部替换后的字节完全一致；
  `updated` 要求通过重新读取磁盘文件得到指定 access token、refresh token 及 expiry，
  其余字段（包括不相关 provider）保持原值。缺省 refresh token 时保留旧值；显式空值无效。
  不约束 JSON 排版或额外的刷新时间字段。

资源请求 401 不触发自动兑换或重放。刷新拒绝、畸形响应和取消不得替换凭据或发送资源请求。
目标 origin 校验必须在凭据加载与续期之前发生，HTTP 客户端不跟随重定向。
Codex 的 `refresh_url=null` 是禁止兑换其 CLI 刷新令牌的所有权约束。

## 证据边界

场景只定义期望行为，不自行证明跨进程互斥、OS 文件锁、原子持久化或模型计费重放。
runner 必须标明实际执行边界。Python 的共享场景 runner 使用真实临时文件和模拟 HTTP；
跨进程保证另用真实 spawned workers、共享凭据文件和受控本地 HTTP 服务验证。
取消场景不能只检查返回的错误，还应确认之后没有后台写入或资源请求，并能再次使用锁。
对真实 provider、CLI、平台文件锁及订阅额度的兼容结论需要对应环境验收。

## 协议来源与范围

Grok 的 issuer、client ID、凭据 scope、principal 字段和同名 OS 锁来自
[republic Grok 实现](https://github.com/bubbuild/republic/blob/main/src/republic/providers/grok.py)
及其[行为测试](https://github.com/bubbuild/republic/blob/main/tests/test_grok.py)。
本 profile 对应 republic 的 `https://api.x.ai/v1` OAuth 路径。
[Magpie Grok](https://github.com/yetone/magpie/blob/main/internal/provider/grok.go)
目前由插件提供 `cli-chat-proxy.grok.com` 请求与 CLI 驱动续期，属于不同的执行契约，
这里不宣称覆盖该插件全部行为。
[Magpie Claude 桥接](https://github.com/yetone/magpie/blob/main/internal/gateway/claude_subscription.go)
还包含常驻 Claude Code、MCP 工具回合、取消恢复和会话缓存；不属于 HTTP OAuth 契约。

上游原始测试向量另存于 [upstream/README.md](upstream/README.md)，包含精确 commit、原始测试路径、适配差异与许可证。当前文件描述的 27 项为本项目补充契约，不冒充上游 golden。
