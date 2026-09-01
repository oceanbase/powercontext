---
template: home.html
title: PowerContext
description: Continue work in a new session without restating decisions, constraints, and progress. Use a supported Agent or connect your own AI over HTTP.
hide:
  - navigation
  - toc
  - footer
home:
  hero:
    label: Open source. Data stays local by default.
    title:
      - Start a new session.
      - Pick up where you left off.
    lead: Save confirmed decisions, constraints, and progress in PowerContext. Supported Agents and AI applications connected over HTTP can use them in later sessions.
    note: You choose the model and control identity and write access. PowerContext keeps Memory with its sources and version history.
    actions:
      - label: Start with an Agent
        href: en/docs/tutorials/agent-quickstart/
        kind: primary
      - label: Connect your AI over HTTP
        href: en/docs/tutorials/api-quickstart/
        kind: secondary
  continuity:
    label: Continue across sessions
    title: You do not have to repeat the background.
    lead: Save information once when it needs to last. Later sessions can find it, check where it came from, and see how it changed.
    steps:
      - title: Save
        description: Record the decisions, constraints, and next steps that should outlast the current chat.
      - title: Continue
        description: Open the same work in another Agent or application without pasting the earlier conversation.
      - title: Check
        description: Review the saved objective, progress, evidence, and omissions before continuing.
  ownership:
    label: Memory and Handoff
    title:
      - Remember what will matter later.
      - Hand off what is in progress.
    lead: Memory keeps information that stands on its own and will matter later. You can revise or retire an entry while preserving its history.
    handoff: A Handoff saves the current objective and progress. It also records blockers and the next action so another person, Agent, or later session can continue. Proven approaches can become Experience. Recurring work can become Skills.
    result: "LOCOMO: 90.78% correct · 1.38 s p95 search latency"
    command: powercontext server run
    primary_action:
      label: Read the Agent quickstart
      href: en/docs/tutorials/agent-quickstart/
    secondary_action:
      label: Connect your AI over HTTP
      href: en/docs/tutorials/api-quickstart/
---
