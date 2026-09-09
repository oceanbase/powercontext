---
title: 快速开始
description: 安装 PowerContext、接入一个 Agent，并在新会话中恢复已保存的决策。
---

# 快速开始

通过一个持续运行的 PowerContext Server，在 Agent 会话之间保留项目上下文。本页使用 Codex 和显式 Memory 写入，
不需要生成模型或 Embedding 服务。

## 1. 安装并启动

需要 Python 3.11+、Git、uv 和已安装的 Codex。PowerContext 支持 macOS 和 Linux；Windows 支持为 `experimental`。
宿主和可选数据库的要求见[安装与运行](install-and-run.md)。以下命令使用当前尚未发布的 `master` 集成：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

保持该终端运行。默认 Server 监听 `http://127.0.0.1:8000`，将数据持久化到本地 SQLite，
在 `/` 提供 Dashboard，在 `/mcp` 提供 MCP。

安装带 tag 的发布版见[安装与运行](install-and-run.md#选择版本)。Server 包与集成应使用相同的 tag 或 commit。

## 2. 接入 Agent

在另一个终端执行：

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext doctor codex
```

在需要保留上下文的项目中打开 Codex。安装后启动新会话以加载插件。插件通过会话或工作区绑定解析已有 Scope，
没有绑定时使用 Server 默认 Scope。不同项目不会自动获得独立 Scope；需要隔离项目时，按
[Scope 与访问控制](../workflows/scopes-and-access.md)建立边界。

其他宿主使用各自的[接入指南](../integrations/index.md)。WorkBuddy 需要单独执行 `setup workbuddy`；
Hermes 还需要运行 `hermes memory setup` 并选择 PowerContext。

## 3. 保存并恢复一条决策

请 Agent 显式保存一条不含敏感信息的决策：

```text
Remember this project decision in PowerContext: use uv for Python dependency management.
```

请它从 PowerContext 搜索该决策。成功的写入和读取应返回决策及其 Memory citation。
随后在同一项目中打开新的 Agent 会话，询问：

```text
Search PowerContext: which tool does this project use for Python dependency management?
```

核对返回的决策和 citation。数据能否恢复取决于是否使用相同的 Server 数据和解析后的 Scope，
不依赖原会话继续打开。当前项目文件与用户指令仍优先于历史上下文。

## 继续使用

- [Memory 与上下文](../workflows/memory-and-context.md)：修订、退役已保存信息并控制召回。
- [工作连续性](../workflows/memory-and-handoff.md)：通过 Handoff 交接当前工作。
- [启用提取与向量搜索](configure-models.md)：配置模型处理采集的 Sources。
- [部署 Server](../operate/deploy-server.md)：运行持久的个人服务。
- [诊断与恢复](../operate/troubleshoot.md)：排查安装、Server 或召回问题。
