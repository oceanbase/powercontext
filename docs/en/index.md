---
title: PowerContext
description: Shared memory and session handoff service for coding agents. Keep project decisions, constraints, and task progress synced across Claude Code, Codex, and teammates.
home:
  hero:
    title:
      - Shared memory and handoff
      - for your coding agents.
    lead: PowerContext is a local context service that stores your project's decisions, constraints, verified checks, and task progress. Switch between Claude Code, Codex, or teammates and pick up directly from the latest state.
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
    title: One project memory across every session.
    lead: When a session ends mid-task, PowerContext keeps the decisions you made, the code state, and the next step ready for whichever agent takes over.
    scope: payments-api
    memory:
      title: Project decisions and constraints stay saved as versioned revisions.
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
          title: Read the signing secret exclusively from the vault
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
      title: The next session starts from a structured handoff of the current state.
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
      title: Working fixes go through human review before entering shared memory.
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
          value: Sign and verify raw request bytes directly
      approve: Approve
      reject: Reject
      approved: Approved as revision 1
    skill:
      title: Approved workflows become reusable Skills exported to your agents.
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
      title: Recall stays within a strict byte budget, injecting only relevant context each turn.
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
      - Switch coding agents.
      - Keep the full context.
    lead: Start a feature in one coding agent and finish it in another. PowerContext keeps your project's memory, task progress, and approved skills in one shared server.
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
    lead: Both results come from the published evaluation harness. The benchmarks page details the test methods and exact scope.
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
  testimonials:
    title: What people say
    pause: Pause scrolling
    play: Resume scrolling
    profile: "{name} on X"
  onboarding:
    title: From install to your first shared memory
    lead: Run a local PowerContext server, enable the memory features you want, and connect your coding agents. Works locally on your machine or across devices.
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
