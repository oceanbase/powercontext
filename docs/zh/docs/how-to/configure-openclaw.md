---
title: 配置 OpenClaw
description: 安装和使用 OpenClaw 的 PowerContext Memory provider。
---

# 配置 OpenClaw

PowerContext 作为 OpenClaw 的唯一 Memory provider 接入。OpenClaw 继续管理 Agent 与会话身份、transcript 和生命周期
Hook；插件通过 PowerContext Server 完成限定 scope 的召回和持久化 Memory。

## 安装并配置插件

先安装 OpenClaw 2026.8.1-beta.2 或更高版本以及 `pnpm`，再使用包含 OpenClaw 集成的 PowerContext CLI 执行：

```bash
powercontext setup openclaw \
  --source oceanbase/powercontext \
  --ref master \
  --server-url http://127.0.0.1:8000
```

也可以使用本地 checkout：

```bash
powercontext setup openclaw --source . --server-url http://127.0.0.1:8000
```

setup 会构建并链接插件，将它设为 OpenClaw Memory slot，开启自动召回和采集，把插件工具加入
`tools.alsoAllow`，然后重启 Gateway。再次执行 setup 可刷新现有安装。setup 不会启动 PowerContext Server。

在一个终端中启动 Server：

```bash
powercontext server run
```

再在另一个终端中开启新的 OpenClaw 会话：

```bash
openclaw tui
```

## 选择 Memory scope

默认的 `agent` scope 会按 OpenClaw Agent 隔离 Memory。除非多个 Agent 需要明确共享项目 Memory，否则应保留此设置。
当 OpenClaw 能为一次 turn 提供唯一且可信的项目身份时，可改用 project scope：

```bash
powercontext setup openclaw \
  --source oceanbase/powercontext \
  --ref master \
  --server-url http://127.0.0.1:8000 \
  --scope-mode project
```

群组、频道和 incognito 会话不会被采集或搜索。召回和采集会正常降级，因此 PowerContext Server 不可用时不会
阻塞普通 OpenClaw turn。

## 使用 Memory 工具

插件提供 `powercontext_memory_search` 和 `powercontext_memory_get` 读取工具。显式持久化修改使用
`powercontext_memory_store`、`powercontext_memory_revise` 和 `powercontext_memory_retire`。setup 默认开启自动召回和
Source 采集。

## 连接启用鉴权的 Server

Server 开启鉴权时，请在 OpenClaw Gateway 的运行环境中设置 `POWERCONTEXT_CLIENT_API_TOKEN`，其值必须与 Server
token 一致。插件会在请求时读取该变量并作为 Bearer credential 发送；不要把凭据写进 `--server-url`。通过不可信
网络连接 Server 时应使用 HTTPS。

## 验证或停用集成

确认 OpenClaw 已加载插件并选中对应 Memory slot：

```bash
openclaw plugins list
openclaw config get plugins.slots.memory
openclaw config get plugins.entries.memory-powercontext.config.endpoint
```

恢复 OpenClaw 内置的文件 Memory：

```bash
openclaw config set plugins.slots.memory memory-core
openclaw config set plugins.entries.memory-powercontext.enabled false
openclaw gateway restart
```

随后可运行 `openclaw plugins uninstall memory-powercontext` 移除已链接的插件。
