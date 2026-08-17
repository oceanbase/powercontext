---
title: 配置 Pi
description: 安装 PowerContext 原生 Pi package，并控制召回、采集和持久化工具写入。
---

# 配置 Pi

## 安装或刷新 package

先安装 Pi，再从与 PowerContext CLI 相同的 ref 安装 package：

```bash
powercontext setup pi --source oceanbase/powercontext --ref master
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

## 了解 package 的行为

Pi 开始 agent turn 前，package 会以默认 8000-byte 预算调用一次 `POST /v1/context/prepare`。它只严格接受
`powercontext.prepared-context.v1`，并把结果标记为不可信历史证据。当前 system instruction、仓库规范和用户请求
始终优先。

符合条件的用户提示词会被独立采集为 Content Source。package 不会同步完整 Pi transcript。Server 不可用、超时、
重定向或响应不符合契约时，召回、采集和边界 flush 都会正常降级：Pi 的 prompt 不变，普通工作不会被阻塞。

scope 优先由规范化 Git remote 推导，无法取得时才根据项目路径推导。只有 scope 必须独立于两者时才设置显式值。

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

## 使用显式工具和命令

`project-context` skill 会说明何时调用原生 `pc_*` 工具。核心工具包括：

- `pc_search`、`pc_memory_list`、`pc_memory_get`、`pc_memory_revise`、`pc_memory_retire`；
- `pc_remember`、`pc_prepare_context`、`pc_capture_source`；
- `pc_handoff_activate`、`pc_handoff_prepare`、`pc_handoff_finalize`、`pc_handoff_commit`、
  `pc_handoff_continue`。

显式持久化写入在交互式 Pi 会话中必须确认；没有交互 UI 时，Pi 会拒绝写入而不会静默持久化。`/pc doctor`、
`/pc search <query>`、`/pc remember <text>`、`/pc flush` 和 `/pc stats` 可直接查看状态和维护内容。

## 连接启用鉴权的 Server

在受保护环境中启动启用鉴权的 Server：

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

使用完整匹配 header 启动 Pi：

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

不要把凭据放进 `POWERCONTEXT_PI_BASE_URL`。package 只允许 loopback Server 使用明文 HTTP；远程 Server 必须使用
HTTPS。

## 验证安装

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` 会检查 Pi 可执行文件是否存在，以及 Pi 是否列出了 PowerContext package。修改 PowerContext 环境变量后
需要重启 Pi。
