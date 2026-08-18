---
title: PowerContext 文档
description: 安装 PowerContext、连接 Codex，并选择合适的集成方式。
page_type: docs-overview
---

# PowerContext 文档

启动本地 Server，连接 Agent 宿主，再按需查找接口或配置。

## 开始使用

安装 PowerContext，把项目上下文带入下一次 Agent 会话。

<div class="pc-card-grid">
  <a class="pc-card" href="tutorials/codex-quickstart.md"><strong>Codex 快速入门</strong><span>安装 Server，并在不同 Codex 会话之间验证 Memory。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="how-to/install-and-run.md"><strong>安装和运行</strong><span>从 Git 引用安装并启动本地 Server。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="how-to/troubleshoot.md"><strong>排查问题</strong><span>处理安装、存储和连接问题。</span><i aria-hidden="true">→</i></a>
</div>

## 使用项目上下文

根据内容的生命周期选择长期 Memory 或单次 Handoff，并在任务、会话和模型之间继续工作。

<div class="pc-card-grid">
  <a class="pc-card" href="explanation/memory-and-handoff.md"><strong>理解 Memory 和 Handoff</strong><span>区分长期保留的内容与单次交接信息。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="how-to/handoff-with-codex.md"><strong>在 Codex 中交接工作</strong><span>把当前工作移交到另一个任务、会话或模型。</span><i aria-hidden="true">→</i></a>
</div>

## 集成方式

让 Agent 和观测工具连接到同一个 Server。

<div class="pc-card-grid">
  <a class="pc-card" href="how-to/configure-codex.md"><strong>配置 Codex</strong><span>准备上下文、采集提示词并维护 Memory。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="how-to/configure-claude-code.md"><strong>配置 Claude Code</strong><span>在 Claude Code 与 Codex 之间共享项目 Memory。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="how-to/trace-with-phoenix.md"><strong>用 Phoenix 查看 trace</strong><span>检查传输、应用和推理 span。</span><i aria-hidden="true">→</i></a>
</div>

## 参考

查找稳定的公共接口和配置边界。

<div class="pc-card-grid">
  <a class="pc-card" href="reference/interfaces.md"><strong>接口</strong><span>Codex、DSH、CLI、Python、HTTP 和 MCP。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="reference/configuration.md"><strong>配置</strong><span>路径、Server、推理和集成设置。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="../modules/"><strong>API 参考</strong><span>公开的 Python 模块、模型和契约。</span><i aria-hidden="true">→</i></a>
</div>

## 开发

了解 Memory 和远程访问背后的契约。

<div class="pc-card-grid">
  <a class="pc-card" href="../development/core-protocol/"><strong>Core Protocol</strong><span>Source、Artifact、Trigger 和应用契约。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="../development/memory-layer/"><strong>Memory Layer</strong><span>存储、恢复、维护和候选审查。</span><i aria-hidden="true">→</i></a>
  <a class="pc-card" href="../development/remote-access-implementation/"><strong>远程访问</strong><span>HTTP、MCP 和 Python client 的实现。</span><i aria-hidden="true">→</i></a>
</div>
