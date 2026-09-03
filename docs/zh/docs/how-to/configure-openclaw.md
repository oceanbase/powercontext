---
title: 配置 OpenClaw
description: 为 OpenClaw 安装 PowerContext memory 插件，并控制召回、采集、scope 和持久化写入。
---

# 配置 OpenClaw

## 安装或刷新插件

从同一个 `master` revision 安装 CLI 和插件：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup openclaw --source oceanbase/powercontext --ref master
```

未指定 `--server-url` 时，setup 会把插件 endpoint 配置为 Server 默认地址 `http://127.0.0.1:8000`。

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

## 了解插件的行为

OpenClaw 构建 prompt 前，插件会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`。召回内容会被标记为
不可信历史证据。当前 system instruction、仓库规范和用户请求始终优先。

来自 direct/private 会话的符合条件用户提示词会被独立采集为 Content Source，并使用确定性 source id，重复采集是
幂等的。group、channel 和 incognito 会话会被排除。插件不会同步完整 OpenClaw transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：prompt 不变，普通工作不会被阻塞。

插件暴露五个工具：`powercontext_memory_search`、`powercontext_memory_get`、`powercontext_memory_store`、
`powercontext_memory_revise` 和 `powercontext_memory_retire`。写工具需要模型显式调用，由 OpenClaw 控制
side-effecting 工具的执行。

显式 search 和 get 会直接调用 `/v1/memory/search` 与 `/v1/memory/entries/get`，不会调用
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
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
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

不要把凭据放进 endpoint。当前配置同时接受 HTTP 和 HTTPS URL；仅对可信的 loopback Server 使用明文 HTTP，所有远程
Server 都应使用 HTTPS。这是运维安全要求，目前 CLI 和插件不会强制拒绝非 loopback HTTP URL。

## 验证安装

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` 会检查 OpenClaw CLI 是否存在，以及 `openclaw plugins list --enabled --json` 是否将
`memory-powercontext` 报告为已加载并选中 memory slot。修改 PowerContext 配置后需要重启 OpenClaw gateway。
