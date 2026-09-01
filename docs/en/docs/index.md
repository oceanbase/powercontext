---
template: docs-overview.html
title: Choose an Agent or API
description: Add the HTTP Memory API to an existing AI application, or configure an Agent and verify Memory and Handoff.
page_type: docs-overview
overview:
  intro: If you are new to PowerContext, choose the entry that matches your application. Use HTTP directly from an existing AI application, or start with the Agent quickstart for Codex, Claude Code, OpenCode, and other hosts.
  sections:
    - title: Start here
      description: Complete an API or Agent quickstart first. The remaining guides help you continue in the Agent you use.
      cards:
        - title: HTTP API lifecycle tutorial
          description: Use one small Python flow to connect context, Experience, Skill, and Review; use Scalar for endpoint reference.
          href: en/docs/tutorials/api-quickstart/
          featured: true
        - title: Agent step-by-step quickstart
          description: Choose a host, install and diagnose it, complete Memory, then use its one-line, pc_*, or /pc Handoff path.
          href: en/docs/tutorials/agent-quickstart/
          featured: true
        - title: Complete Codex tutorial
          description: Go deeper into the Codex Hook, MCP Skill, Memory lifecycle, and one-line durable Handoff.
          href: en/docs/tutorials/codex-quickstart/
        - title: Hand off current work
          description: Use Work Contract, Handoff, Acknowledgement, and Task Outcome for the complete task loop.
          href: en/docs/how-to/handoff-with-codex/
        - title: Continue in Claude Code
          description: Open the same project Memory from Claude Code and Codex.
          href: en/docs/how-to/configure-claude-code/
        - title: Use DeepSeek Harness
          description: Prepare context before each model step and use pc_* Memory and Handoff tools.
          href: en/docs/how-to/configure-dsh/
        - title: Continue in Pi
          description: Open project context in Pi with the native package.
          href: en/docs/how-to/configure-pi/
        - title: Continue in OpenClaw
          description: Open project context in OpenClaw with the memory plugin.
          href: en/docs/how-to/configure-openclaw/
        - title: Continue in OpenCode
          description: Recall and maintain project context with the native OpenCode plugin.
          href: en/docs/how-to/configure-opencode/
        - title: Continue in Hermes
          description: Use the MemoryProvider, /pc companion, and Handoff lifecycle operations.
          href: en/docs/how-to/configure-hermes/
        - title: Continue in WorkBuddy
          description: Use the Prompt Hook, MCP Memory, and one-line durable Handoff.
          href: en/docs/how-to/configure-workbuddy/
        - title: Load an Agent Plugin
          description: Use reusable PowerContext skills and MCP configuration in compatible agents.
          href: en/docs/how-to/configure-agent-plugin/
    - title: Understand and operate
      description: Decide what persists, configure the Server, or resolve a broken setup.
      cards:
        - title: Full-capability Quick Start
          description: Generate one validated configuration and verify extraction, vector search, and an Agent loop.
          href: en/docs/how-to/full-capability-runtime/
        - title: Core concepts
          description: Understand scopes, evidence, revisioned Artifacts, prepared context, and work continuity.
          href: en/docs/explanation/core-concepts/
        - title: Memory and Handoff
          description: Learn what belongs in durable Memory and what should remain a temporary Handoff.
          href: en/docs/explanation/memory-and-handoff/
        - title: Experience and Skill lifecycle
          description: Understand how evidence becomes a reviewed Artifact Revision and when it becomes available.
          href: en/docs/explanation/experience-and-skill-lifecycle/
        - title: Configuration
          description: Set storage, providers, interfaces, and runtime behavior.
          href: en/docs/reference/configuration/
        - title: Deploy the Server
          description: Run a persistent Server with health checks, authentication, and a safe network boundary.
          href: en/docs/how-to/deploy-server/
        - title: HTTP API
          description: Look up every Server path, error semantic, and the complete OpenAPI contract.
          href: en/docs/reference/http-api/
        - title: Review Candidates
          description: Inspect, revise, approve, or reject pending Experience and Skill proposals.
          href: en/docs/how-to/review-candidates/
        - title: Create an Experience
          description: Generate an Experience from exact evidence, review it, and verify the approved Revision.
          href: en/docs/how-to/create-and-review-experience/
        - title: Create a managed Skill
          description: Generate and review a managed Skill, then export one exact Revision to Codex.
          href: en/docs/how-to/create-and-export-skill/
        - title: Handoff Report
          description: Inspect scopes, save Handoff Revisions, and understand current report availability.
          href: en/docs/how-to/use-handoff-report/
        - title: Troubleshoot
          description: Diagnose connection, configuration, and integration problems.
          href: en/docs/how-to/troubleshoot/
---
