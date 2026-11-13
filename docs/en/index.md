---
title: PowerContext
description: Keep project knowledge and active work available across people, agents, and sessions.
home:
  hero:
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
    demo_label: Claude Code prepares a Handoff, PowerContext records it in the project Scope, and Codex acknowledges it and continues the work.
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
    board:
      title: PowerContext Dashboard
      scope: payments-api
      kind: Handoff
      heading: Retry failed webhooks
      ref: handoff/h_7c3d
      revision_label: Revision
      waiting_label: Waiting
      steps:
        - name: Work Contract
          note: Objective and checks agreed
        - name: Prepared Handoff
          note: Claude Code · 41 checks passed, 1 blocked
        - name: Acknowledgement
          note: Codex resolved revision 3
        - name: Task Outcome
          note: Recorded when the work ends
      carried_label: Travels with the work
      carried:
        - label: Memory
          value: 4 active
        - label: Sources
          value: 3 links
        - label: Next action
          value: Unblock signature check
  features:
    title: The session ends before the work does.
    lead: You make a decision, change the code, and stop before the task is complete. PowerContext keeps the useful context with the project so the next session can continue from the current state.
    scope: payments-api
    memory:
      title: Decisions and constraints outlive the session, as revisions you can read back.
      label: The Dashboard's Memory list, with one entry open at its saved revision
      window: Memory
      revision_label: Revision
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
    handoff:
      title: The next session starts from the current state, not the whole transcript.
      label: A prepared Handoff listing the objective, verified progress, what is blocked and the next action
      window: Handoff
      ref: handoff/h_7c3d@3
      fields:
        - label: Objective
          value: Retry failed webhooks
        - label: Verified
          value: 41 checks passed
        - label: Blocked
          value: Signature check fails on replayed events
        - label: Next action
          value: Unblock signature check
    review:
      title: What worked is reviewed before anyone reuses it.
      label: An Experience candidate in the Review Inbox, waiting for approval or rejection
      window: Review Inbox
      kind: Experience candidate
      fields:
        - label: Situation
          value: Replayed webhooks failed signature checks
        - label: Action
          value: Verify against the raw body before parsing
        - label: Outcome
          value: 41 of 41 replays accepted
        - label: Lesson
          value: Sign and verify bytes, not parsed JSON
      approve: Approve
      reject: Reject
    skill:
      title: A reviewed procedure becomes a Skill, exported on purpose.
      label: A managed Skill with its validation checks, approved revision and export target
      window: Skill
      name: replay-failed-webhooks
      description: Replay dead-lettered webhook events safely.
      checks_label: Validation checks
      checks:
        - Idempotency key present
        - Signature verified before replay
      status: Approved · revision 2
      export: Exported to Codex
    recall:
      title: Recall is bounded. Each turn gets a byte budget, not a dump.
      label: A PreparedContext result showing how each section fills a shared byte budget
      window: PreparedContext
      status: ready
      budget_label: Byte budget
      budget: 2.9 of 4 KB
      sections:
        - name: Memory
          size: 1.6 KB
          share: 40
        - name: Experience
          size: 0.9 KB
          share: 22
        - name: Topic Memory
          size: 0.4 KB
          share: 10
  ecosystem:
    title:
      - The work changes hands.
      - The context stays.
    lead: A task may start in one agent and continue in another. PowerContext keeps its knowledge, progress, and proven methods with the project so the next agent can pick up from the current state.
    docs_label: Open setup guide
    all_agents_label: View all supported agents
    status:
      official: official
      community: community
      evaluation: evaluation
    setup_title: Connect several agents at once
    setup_lead: The setup command installs, configures, and diagnoses each host you name against the same Server.
    interfaces_title: Or build on the same contract
    interfaces:
      - name: HTTP
        description: The complete remote application contract.
        href: en/docs/develop/http-api/
      - name: Python Client
        description: Typed access from your own application.
        href: en/docs/develop/api-quickstart/
      - name: MCP
        description: A curated, Agent-facing projection at /mcp.
        href: en/docs/integrations/
      - name: Core protocols
        description: Assemble your own Source, Artifact, and Trigger implementations.
        href: en/docs/develop/interfaces/
  proof:
    title: Measured on public benchmarks.
    lead: Both results come from the published harness. The benchmarks page states the methods and where the numbers stop applying.
    stats:
      - value: 90.78%
        label: LoCoMo accuracy across 1,540 scored questions
        detail: Full-context replay scores 52.9% on the same questions.
      - value: 25.4%
        label: fewer input tokens on SWE-bench Pro
        detail: About 225 million per 100 tasks with PowerContext on, 302 million with it off.
    link:
      label: Methods, results, and limitations
      href: en/benchmarks/
  onboarding:
    title: From installation to your first memory
    lead: Start with a Server, configure the capabilities you need, then connect your Agent. The guide covers local use and access from another device.
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
---
