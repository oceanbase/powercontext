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

存在 Git remote 时，Memory scope 根据规范化后的 remote 生成；否则根据项目路径生成。只有在 scope 必须独立于这两者时，才设置 `POWERCONTEXT_DSH_SCOPE_ID`。

插件在模型分析提示词前只调用一次 `POST /v1/context/prepare`。显式 `remember_memory` 不需要模型。

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
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
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
