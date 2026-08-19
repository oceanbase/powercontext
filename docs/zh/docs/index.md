---
title: PowerContext 文档
description: 安装 PowerContext、连接 Codex，并选择合适的集成方式。
---

# PowerContext 文档

PowerContext 保存供人和 Agent 在后续任务中使用的项目上下文。它以本地或远程 Server 的形式运行，并通过 Codex、
Claude Code、DeepSeek Harness、Python、HTTP 和 MCP 访问同一份持久化数据。

如果这是首次使用，请从 [Codex 快速入门](tutorials/codex-quickstart.md)开始。完成后，你能在同一个项目的后续
Codex 会话中读取并维护 Memory。

## 选择你的路径

- 想完成一次端到端体验：阅读 [Codex 快速入门](tutorials/codex-quickstart.md)。
- 想安装、启动、升级或迁移本地服务：阅读 [安装和运行](how-to/install-and-run.md)。
- 想调整项目 scope、提示词采集或本地鉴权：阅读 [配置 Codex](how-to/configure-codex.md)。
- 想配置其他宿主：阅读 [配置 Claude Code](how-to/configure-claude-code.md) 或
  [配置 DeepSeek Harness](how-to/configure-dsh.md)。
- 想理解长期 Memory 与临时 Handoff 的适用边界：阅读[理解 Memory 和 Handoff](explanation/memory-and-handoff.md)。
- 想把当前工作明确移交给另一个任务、会话或模型：阅读 [在 Codex 中交接工作](how-to/handoff-with-codex.md)。
- 遇到安装、Server、插件或 Hook 问题：阅读 [排查问题](how-to/troubleshoot.md)。

## 查询细节

- [接口](reference/interfaces.md)：Codex、Claude Code、DeepSeek Harness、CLI、Client SDK、Core SDK、HTTP 和 MCP。
- [配置](reference/configuration.md)：默认值和环境变量。
