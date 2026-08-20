---
template: home.html
title: PowerContext
description: Keep project decisions, constraints, and next steps available across Codex and Claude Code sessions.
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: Open source · Project scoped · Local by default
    title:
      - Start a new session.
      - Keep moving.
    lead: PowerContext keeps project decisions, constraints, and next steps outside the chat. When Codex or Claude Code opens the project again, the relevant context is ready.
    note: Codex, Claude Code, Python, HTTP, and MCP share the same project Memory.
    actions:
      - label: Start with Codex
        href: en/docs/tutorials/codex-quickstart/
        kind: primary
      - label: How context carries over
        href: en/docs/explanation/memory-and-handoff/
        kind: secondary
  continuity:
    label: One project, two sessions
    title: Do not start from scratch.
    lead: Record a decision once. The next session can recover it with its source and exact revision.
    steps:
      - title: Save
        description: In Codex, record that a Handoff stays temporary until the user asks to commit it.
      - title: Continue
        description: Open the same project in Claude Code without replaying the earlier chat.
      - title: Check
        description: Recover the rule together with its source and exact revision.
  ownership:
    label: Memory and Handoff
    title:
      - Keep what lasts.
      - Hand off the work.
    lead: Memory keeps decisions, constraints, conventions, and next steps in a searchable history. Revise or retire an entry without losing the record.
    handoff: A Handoff captures the current objective, verified progress, blockers, and next action. Commit it when the work becomes a project milestone.
    result: "LOCOMO: 90.78% correct · 1.38 s p95 search latency"
    command: powercontext server run
    primary_action:
      label: Read the quickstart
      href: en/docs/tutorials/codex-quickstart/
    secondary_action:
      label: Explore documentation
      href: en/docs/
---
