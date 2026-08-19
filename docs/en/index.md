---
template: home.html
title: PowerContext
description: PowerContext turns human-agent work into handoff-ready context.
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: Open source · Project-scoped · Runs locally
    title:
      - Project context
      - that lasts.
    lead: PowerContext lets a later agent session recover decisions, outcomes, current state, and next steps without relying on chat history.
    note: Connect through Codex, DeepSeek Harness, Python, HTTP, or MCP.
    actions:
      - label: Get started
        href: en/docs/
        kind: primary
      - label: View on GitHub
        href: https://github.com/oceanbase/powercontext
        kind: secondary
  continuity:
    label: Across sessions
    title: Pick up the project where you left it.
    lead: PowerContext stores project Memory outside the conversation. Integrations prepare relevant context before the next turn.
    steps:
      - title: Capture
        description: Record prompts as Source evidence while a host session runs.
      - title: Maintain
        description: Remember, search, revise, retire, and audit project Memory.
      - title: Recall
        description: Prepare bounded context for a later session in the same project.
  ownership:
    label: Local by default
    title:
      - Run it where
      - your work lives.
    lead: Run the Server locally with SQLite. Codex, DeepSeek Harness, the CLI, Python, HTTP, and MCP use the same persistent Memory.
    command: powercontext server run
    primary_action:
      label: Read the quickstart
      href: en/docs/tutorials/codex-quickstart/
    secondary_action:
      label: Explore documentation
      href: en/docs/
---
