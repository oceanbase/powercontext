---
title: 配置 Codex
description: 安装 PowerContext Codex 插件并控制其本地行为。
---

# 配置 Codex

## 安装或刷新插件

执行：

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
```

该命令会把仓库添加为 Codex marketplace，安装 PowerContext 插件，并创建用户数据目录。重复执行是安全的。
`--ref` 应与安装 PowerContext 工具时使用的 ref 一致。

配置完成后开启新的 Codex 会话。通过 `/hooks` 查看 PowerContext `UserPromptSubmit` Hook，并在收到提示时
授予信任。

## 理解插件行为

插件通过两条路径访问同一个 Server：

- Prompt Hook 请求 Runtime 准备一个最终、有界的上下文值，然后独立地把用户提示词采集为
  Source 证据；
- MCP 为 Codex 提供记忆、检索、修订、停用和审计 Memory 的显式工具。

存在 Git remote 时，Memory scope 根据规范化后的 remote 生成；否则根据项目路径生成。在同一项目中开启
的新 Codex 会话会得到相同 scope。只有在 scope 必须独立于这两者时，才设置
`POWERCONTEXT_CODEX_SCOPE_ID`。

Codex 开始分析提示词前，Hook 只调用一次 `POST /v1/context/prepare`，请求 8000-byte 总预算。它严格校验
`powercontext.prepared-context.v1`，并原样注入返回内容。Runtime 负责把 Memory 内容标记为不可信历史、保留
精确 citation，并完成最终选择与渲染。显式搜索仍可通过 Client 和 MCP 使用，但不会成为第二次自动召回。

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

## 连接启用鉴权的本地 Server

从本地 secret manager 加载一个 token，然后启用鉴权并启动 Server：

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配 Authorization header 的环境中启动 Codex：

```bash
export POWERCONTEXT_CODEX_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
codex
```

修改该变量后需要重启 Codex。插件的 MCP 配置从环境读取这个可选 header，Prompt Hook 读取同一个值。不要把
token 写入 `.mcp.json`、Server URL 或静态 MCP header。

没有设置该变量或值为空，并且 Server 未启用鉴权时，插件行为与默认状态完全一致。如果 Server 已启用鉴权，
但 header 缺失或错误，Hook 会正常降级并写出 `authentication_failed` 诊断；MCP tools 不可用，但不会阻塞
Codex 会话。

Server 不可用时，Hook 的恢复和采集会正常降级，不会阻塞 Codex。显式 Memory 工具会报告服务不可用。

正常空结果或召回失败时，Hook 会向 stderr 写一行不含正文的 JSON 诊断。outcome 包括 `empty`、
`authentication_failed`、`version_mismatch`、`server_unavailable` 和 `invalid_response`；事件不会包含 query、
scope、prepared content、citation、response body 或 authorization value。
