---
title: 配置 DeepSeek Harness
description: 安装 PowerContext DeepSeek Harness 插件并控制其本地行为。
---

# 配置 DeepSeek Harness

## 安装或刷新插件

先安装 DeepSeek Harness，并确保 web profile 可用。然后执行：

```bash
powercontext setup dsh --source oceanbase/powercontext --ref master
```

该命令会从 `integrations/dsh/plugins/powercontext` 安装插件，并创建用户数据目录。该目录必须包含已构建的 `lib/index.js`。重复执行是安全的：有效 checkout 会复用，同一 ref 下的残缺 checkout 会被替换。`--ref` 应与安装 PowerContext 工具时使用的 ref 一致。`--source` 可以是 GitHub slug，也可以是 `https://github.com/...` URL。

本地 checkout 同样可以：

```bash
powercontext setup dsh --source .
```

`setup dsh` 内部会执行 `dsh plugin --profile web add`。配置完成后重新打开 `dsh web`。

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

- `/pc doctor` 不依赖 Scope 解析，继续检查 liveness 和 readiness，并保留两个检查结果。
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
