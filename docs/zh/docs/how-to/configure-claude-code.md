---
title: 配置 Claude Code
description: 安装 PowerContext Claude Code 插件，并配置召回、提示词采集和认证。
---

# 配置 Claude Code

## 检查前置条件

先安装 PowerContext 和 Claude Code，并确认执行 setup 的环境可以找到这两个命令：

```bash
powercontext --version
claude --version
```

Python package 和插件应使用同一个 PowerContext 仓库 ref。Hook 会校验带版本的 Prepared Context contract，
因此旧 Server 与新插件混用时，召回可能被禁用，但不会阻塞 Claude Code。

## 安装或更新插件

执行：

```bash
powercontext setup claude-code --source oceanbase/powercontext --ref master
```

修改 Claude Code 设置前，setup 会报告设置项、插件缓存、持久化数据位置、所需权限和准确的回滚命令。
之后命令会注册 Marketplace、以 user scope 安装插件，并通过 Claude Code 的 JSON 输出确认插件已启用。

用户设置项、Marketplace registry、按版本保存的插件缓存和插件数据目录都由 Claude Code 管理。
PowerContext 通过平台无关的路径处理，从 `CLAUDE_CONFIG_DIR` 或 Claude Code 默认配置目录解析需要展示的位置。
setup 会把实际变更交给 Claude Code，并在第一次变更前输出解析后的位置。

使用本地 checkout 时，传入目录：

```bash
powercontext setup claude-code --source ./powercontext
```

安装完成后启动 Server，再开启新的 Claude Code 会话：

```bash
powercontext server run
claude
```

使用 `/hooks` 确认 `UserPromptSubmit` Hook，使用 `/mcp` 确认 `powercontext` Server。

再次执行 setup 会更新插件配置并验证已安装版本，不会删除已有的 PowerContext Server 数据。

## 理解插件行为

对于每条用户 prompt，Hook 会：

1. 推导与 Codex 集成一致的项目 scope；
2. 最多调用一次 `POST /v1/context/prepare`；
3. 严格校验 `powercontext.prepared-context.v1`，再通过 `additionalContext` 原样注入；
4. 独立地将 prompt 采集为普通 Content Source 证据。

配置 generation model 后，Source pipeline 可能进一步提取 Memory。提示词采集不会调用 `remember_memory`，
Hook 也不会把普通 prompt 标记为 `task-outcome`。

v1 不安装 `Stop` Hook，不读取 transcript，也不自动采集 Claude 的最终回复。Memory 写入和持久化 Handoff
里程碑仍然是由随附 Skill 指导的显式 MCP 操作。

scope 按以下顺序解析：

1. 显式设置的 `POWERCONTEXT_CLAUDE_SCOPE_ID`；
2. Git 顶层目录中规范化后的 `remote.origin.url`；
3. 从解析后的项目目录生成的 `local:sha256:<digest>` 标识。

因此，在 Git 项目中，Claude Code 和 Codex 会话共享规范化后的 remote scope。对于本仓库，两者都会得到：

```text
git:github.com/oceanbase/powercontext
```

local fallback 在同一个解析后目录中保持稳定，但不用于连接无关的 checkout。只有确实需要主动隔离或共享时，
才设置显式 scope。

## 使用显式 Memory 和 Handoff 操作

随附的 MCP Server 暴露已有的 PowerContext 操作。Claude 可以搜索和列出 Memory；只有用户明确要求持久化变更时，
才创建、修订或废弃 Memory entry。

转交任务时，随附 Skill 会引导 Claude 依次采集 Source、激活 Handoff、检查 Draft、完成 finalization，再把完整的
Prepared Handoff 传给 `continue_handoff`。Prepared Handoff 是临时载体；`commit_handoff` 会创建持久化里程碑，
只有用户明确要求时才调用。

自动召回不依赖 Claude 是否决定调用 MCP。反过来，MCP Memory 写入也不能替代 prompt 采集：启用采集后，Hook
会把每条 prompt 保存为普通 Source 证据，之后是否从 Source 生成 Memory 由 Server 决定。

## 配置 Server 地址和提示词采集

安装时设置 endpoint：

```bash
powercontext setup claude-code \
  --server-url http://127.0.0.1:9000 \
  --no-capture-prompts
```

Claude Code 会把这些非敏感选项保存在用户级 `pluginConfigs` 中。也可以只覆盖一次 Hook 进程：

```bash
export POWERCONTEXT_CLAUDE_SERVER_URL=http://127.0.0.1:9000
export POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false
claude
```

只有 Memory scope 必须有意区别于 Git remote 和本地项目路径时，才设置
`POWERCONTEXT_CLAUDE_SCOPE_ID`。

`POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE=true` 会让 Hook 等待 Source 处理，只适合测试，不适合日常交互。

timeout 和 flush 控制项见[配置参考](../reference/configuration.md)。这些设置作用于 Hook
进程；MCP client 仍由 Claude Code 管理。

## 连接启用认证的 Server

从 secret manager 加载 token，再启动 Server：

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配完整 header 的环境中启动 Claude Code：

```bash
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
claude
```

Hook 与 MCP `headersHelper` 都读取该进程环境变量。变量不存在时，helper 不会发送 `Authorization` header。
不要把 token 放入 Server URL、插件选项、`.mcp.json`、Source metadata 或日志。

明文 HTTP 只允许连接 `127.0.0.1`、`localhost` 或 `::1`。Claude Code 连接远程 Server 时必须使用 HTTPS。

## 理解失败行为

召回与采集彼此独立，并且都会 fail open。召回失败不会阻止 prompt 采集，采集失败也不会移除有效的召回上下文。
无论哪种情况，Claude Code 都会继续处理当前 prompt。

| 条件 | Hook 行为 |
| --- | --- |
| Prepared Context 为空 | 不注入内容，并记录 `empty` outcome |
| HTTP 401 | 不注入内容，并记录 `authentication_failed` |
| HTTP 404 | 不注入内容，并记录 `version_mismatch` |
| HTTP 503 或 Server 不可用 | 不注入内容，并记录 `server_unavailable` |
| 未知 schema、错误 JSON 或超大响应 | 不注入内容，并记录 `invalid_response` |

诊断只包含 outcome 和安全的数字 metadata，不包含 prompt、scope、Prepared Context 正文、Authorization 值或
响应正文。插件会拒绝重定向，并限制响应大小和 wall-clock 时间。

## 诊断或回滚

在不连接 Server 的情况下检查 CLI 和已启用插件：

```bash
powercontext doctor claude-code
```

如果 setup 在创建新的 Marketplace 或插件项后失败，它只删除本次 setup 创建的对象；setup 前已存在的 Marketplace
或插件会保留。修正命令报告的 Claude CLI 或仓库错误后可重新执行 setup，该操作可以安全重复。

移除插件与 Marketplace：

```bash
claude plugin uninstall powercontext@powercontext --scope user
claude plugin marketplace remove powercontext --scope user
```

从最后一个 scope 卸载插件时，Claude Code 也会删除 `${CLAUDE_PLUGIN_DATA}`；除非卸载时传入
`--keep-data`。
