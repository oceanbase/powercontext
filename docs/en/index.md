---
title: PowerContext
description: Keep project knowledge and active work available across people, agents, and sessions.
home:
  hero:
    label: Open source · local-first
    title:
      - Keep work moving
      - across sessions.
    lead: PowerContext keeps decisions, constraints, evidence, and current progress with the project. When a person or agent takes over, they can verify the state and continue without reconstructing the last conversation.
    action:
      label: Install and get started
      href: en/docs/get-started/quickstart/
    learn_more:
      label: How it works
      href: en/docs/get-started/core-concepts/
    community:
      star_label: Star on GitHub
      contributors_label: View all contributors
      contribute_label: Contribute
      license_label: Apache-2.0 license
    cursors:
      visitor: You
      claude_code: Claude Code
      codex: Codex
      opencode: OpenCode
    figure:
      description: A rotary table carries the project Scope, with its Memory cards, Source slips, review tray and Handoff cartridge, from the station of the session that is ending to the station of the next session.
      figure: Fig. 1
      title: The handoff turntable
      hint: Choose a station to turn the table
      readout:
        from: Ending session · Handoff prepared
        to: Next session · Handoff acknowledged
      steps:
        - Work Contract
        - Prepared Handoff
        - Acknowledgement
        - Task Outcome
    sessions:
      ending:
        title: Claude Code
        action: Hand off
        action_done: Handed off
        lines:
          - Hand off this work with PowerContext.
          - Checks 41 passed · 1 blocked
          - Saved atomic-memory/m_k2f9@2
        result_label: Prepared
        result: handoff/h_7c3d@3
      next:
        title: Codex
        action: Acknowledge
        action_done: Acknowledged
        prompt: Continue this PowerContext Handoff.
        waiting: Waiting for a Handoff…
        result_label: Accepted
        received:
          - label: Objective
            value: Retry failed webhooks
          - label: Memories
            value: "4"
          - label: Sources
            value: 3 links
          - label: Next
            value: Unblock signature check
  photo_credit: Photo by {author} on {source}
  onboarding:
    title: From installation to your first memory
    lead: Start with a Server, configure the capabilities you need, then connect your Agent. The guide covers local use and access from another device.
    preview_label: Guided setup preview.
    preview_note: These instructions use the official oceanbase/powercontext master branch.
    repository_label: View this source branch
    guide_label: Follow the complete guide
    copy_label: Copy command
    copied_label: Copied
    steps:
      - title: Install PowerContext
        description: Use Python 3.11 or later, Git, and uv. Install this branch on the machine that will run the Server.
        command: uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
      - title: Follow the configuration wizard
        description: Choose your storage, access scenario, memory capabilities, Dashboard, and Agents. It writes configuration files and tailored next steps.
        command: powercontext config init
      - title: Start the Server
        description: Run from the directory containing your generated .env. Open the Dashboard URL and use the token shown by the wizard, if enabled.
        command: powercontext server run --env-file .env
      - title: Connect your Agent and verify memory
        description: Follow .env.next-steps.md to create or select a Scope and connect your Agent. Verify that a real conversation becomes a Source, then check memory processing if enabled.
  continuity:
    title: The session ends before the work does.
    lead: You make a decision, change the code, and stop before the task is complete. PowerContext keeps the useful context with the project so the next session can continue from the current state.
    visual_label: How PowerContext carries context from one work session to the next
    steps:
      - title: Current session
        items:
          - Decisions
          - Constraints
          - Evidence
          - Verified progress
      - title: PowerContext
        items:
          - Memory
          - Handoff
      - title: Next session
        items:
          - Relevant context
          - Current objective
          - Source links
          - Next action
    dashboard:
      title: PowerContext Dashboard
      scope: payments-api
      list_label: Memory
      revision_label: Revision
      current_label: current
      state_label: State
      sources_label: Source references
      states:
        active: active
        forgotten: forgotten
        merged: merged
        retired: retired
      entries:
        - id: m_k2f9
          title: Webhook retries back off exponentially, five attempts at most
          revision: 3
          state: active
        - id: m_p41x
          title: The signing secret comes from the vault, never from .env
          revision: 1
          state: active
        - id: m_w8tz
          title: Retry queue lives in Redis
          revision: 2
          state: retired
        - id: m_c3ha
          title: Use idempotency keys on webhook POST
          revision: 1
          state: merged
        - id: m_j06n
          title: Staging deploys wait for manual approval
          revision: 1
          state: forgotten
      body: Failed webhook deliveries retry with exponential backoff from 2 s, capped at five attempts. After the fifth failure the event moves to the dead-letter list for review.
      sources:
        - Claude Code session · retry design
        - "Pull request #482 review"
        - Task Outcome · partial
  ecosystem:
    title:
      - The work changes hands.
      - The context stays.
    lead: A task may start in one agent and continue in another. PowerContext keeps its knowledge, progress, and proven methods with the project so the next agent can pick up from the current state.
    visual_label: A task moves between agents while PowerContext keeps its project artifacts available
    agents_label: Work begins in a connected agent
    all_agents_label: View all supported agents
    docs_label: Open setup guide
    runtime_label: Context accumulates within the project scope
    artifacts_label: The work leaves reusable artifacts
    artifacts:
      - name: Memory
        description: Durable knowledge
      - name: Handoff
        description: Active task state
      - name: Experience
        description: Reviewed approach
      - name: Skill
        description: Exported procedure
    output_label: A different agent continues the work
    status:
      official: official
      community: community
      evaluation: evaluation
---
