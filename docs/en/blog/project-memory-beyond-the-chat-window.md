---
template: post.html
page_type: blog-post
title: Project memory beyond the chat window
description: How PowerContext keeps durable project context available across agent sessions.
hide:
  - footer
---

# Project memory beyond the chat window

PowerContext keeps durable project context outside the conversation, so a later agent session can recover what
matters.

PowerContext maintainers · Project memory

---

## The project is the stable scope

Chat sessions end, summaries change, and agent hosts come and go. PowerContext resolves a project from its normalized
Git remote or local path, then attaches Memory to that project instead of one conversation.

## Memory has a lifecycle

Agents can remember, search, revise, retire, and audit Memory. Revision history and citations remain available, so
correcting an entry does not erase the evidence behind its earlier state.

## Recall stays bounded

Before an agent turn, the Runtime can prepare one bounded context value from relevant project Memory. Codex and
DeepSeek Harness use this path, while explicit Memory tools remain available through MCP and HTTP.

[← Back to Blog](../)
