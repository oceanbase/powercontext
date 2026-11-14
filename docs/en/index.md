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
  onboarding:
    title: From installation to your first memory
    lead: Start with a Server, configure the capabilities you need, then connect your Agent. The guide covers local use and access from another device.
    preview_label: Before you start.
    preview_note: Follow the installer's PATH instructions. Agent integrations also require the Agent CLI and Git.
    repository_label: View source
    guide_label: Follow the complete guide
    steps:
      - title: Install PowerContext
        description: Install the latest stable release with local Server support. The installer sets up Python and uv if needed.
        command_tabs:
          - label: macOS / Linux
            command: curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
          - label: Windows
            command: |
              irm https://powercontext.oceanbase.io/install.ps1 -OutFile install.ps1
              powershell -ExecutionPolicy Bypass -File .\install.ps1
      - title: Follow the configuration wizard
        description: Choose your storage, access scenario, memory capabilities, Dashboard, and Agents. It writes configuration files and tailored next steps.
        command: powercontext config init
      - title: Start the Server
        description: Run from the directory containing your generated .env and keep this terminal open. Use the Dashboard URL and token provided by the wizard, if enabled.
        command: powercontext server run --env-file .env
      - title: Connect your Agent and verify memory
        description: Follow .env.next-steps.md to connect your Agent and select a Scope. Save a decision, then retrieve it with its citation in a new session using the same Scope.
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
---
