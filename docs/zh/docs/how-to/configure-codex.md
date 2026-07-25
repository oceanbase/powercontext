---
title: 配置 Codex
description: 安装 PowerContext Codex 插件并控制其本地行为。
---

# 配置 Codex

## 安装或刷新插件

执行：

```bash
powercontext setup codex --source oceanbase/powercontext --ref main
```

该命令会把仓库添加为 Codex marketplace，安装 PowerContext 插件，并创建用户数据目录。重复执行是安全的。
`--ref` 应与安装 PowerContext 工具时使用的 ref 一致。

配置完成后开启新的 Codex 会话。通过 `/hooks` 查看 PowerContext `UserPromptSubmit` Hook，并在收到提示时
授予信任。

## 理解插件行为

插件通过两条路径访问同一个 Server：

- Prompt Hook 检索相关 Memory，并把用户提示词采集为 Source 证据；
- MCP 为 Codex 提供记忆、检索、修订、停用和审计 Memory 的显式工具。

存在 Git remote 时，Memory scope 根据规范化后的 remote 生成；否则根据项目路径生成。在同一项目中开启
的新 Codex 会话会得到相同 scope。只有在 scope 必须独立于这两者时，才设置
`POWERCONTEXT_CODEX_SCOPE_ID`。

## 控制提示词采集

默认开启提示词采集。如果当前工作不应被记录，请在启动 Codex 前关闭：

```bash
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
codex
```

采集的提示词会成为 Source 证据。开启采集并不保证自动生成 Memory；后者需要配置 generation model。
显式调用 `remember_memory` 不需要模型。

仅在测试时，可以让 Hook 等待 Source 处理完成：

```bash
export POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true
```

这会给每个提示词增加推理延迟，不适合作为日常交互配置。

Server 不可用时，Hook 的恢复和采集会正常降级，不会阻塞 Codex。显式 Memory 工具会报告服务不可用。
