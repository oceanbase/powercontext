---
template: post.html
page_type: blog-post
title: 对话结束后，项目记忆仍然存在
description: PowerContext 如何让持久的项目上下文跨 Agent 会话继续可用。
hide:
  - footer
---

# 对话结束后，项目记忆仍然存在

PowerContext 把持久的项目上下文保存在对话之外，让后续 Agent 会话能够找回真正需要的信息。

PowerContext 维护者 · 项目记忆

---

## 项目才是稳定的作用域

聊天会结束，摘要会变化，Agent 宿主也会更换。PowerContext 根据规范化的 Git 远程地址或本地路径识别项目，再把 Memory
关联到项目，而不是某一次对话。

## Memory 有明确的生命周期

Agent 可以记录、搜索、修订、停用和审计 Memory。修订历史与引用会被保留，因此修正条目不会抹去旧状态背后的证据。

## 召回范围始终受控

每次 Agent 执行前，Runtime 可以从相关的项目 Memory 中准备一份有界上下文。Codex 与 DeepSeek Harness 使用这条路径，MCP
和 HTTP 仍提供显式的 Memory 操作。

[← 返回博客](../)
