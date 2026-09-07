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
