---
template: docs-overview.html
title: PowerContext documentation
description: Install PowerContext, connect Codex, and choose the right integration surface.
page_type: docs-overview
overview:
  intro: Start the local Server, connect an agent host, and look up the interface or configuration you need.
  sections:
    - title: Get started
      description: Install PowerContext and carry project context into a new agent session.
      cards:
        - title: Codex quickstart
          description: Install the Server and verify Memory across Codex sessions.
          href: en/docs/tutorials/codex-quickstart/
        - title: Install and run
          description: Install from a Git ref and start the local Server.
          href: en/docs/how-to/install-and-run/
        - title: Troubleshoot
          description: Resolve setup, storage, and connection issues.
          href: en/docs/how-to/troubleshoot/
    - title: Work with context
      description: Choose between durable Memory and a task-specific Handoff, then move work between sessions without losing intent.
      cards:
        - title: Memory and Handoff
          description: Understand what persists and what belongs to one transfer.
          href: en/docs/explanation/memory-and-handoff/
        - title: Hand off work in Codex
          description: Transfer current work to another task, session, or model.
          href: en/docs/how-to/handoff-with-codex/
    - title: Integrations
      description: Connect agents and observability tools to the same Server.
      cards:
        - title: Configure Codex
          description: Prepare context, capture prompts, and maintain Memory.
          href: en/docs/how-to/configure-codex/
        - title: Claude Code
          description: Share project Memory between Claude Code and Codex.
          href: en/docs/how-to/configure-claude-code/
        - title: Trace with Phoenix
          description: Inspect transport, application, and inference spans.
          href: en/docs/how-to/trace-with-phoenix/
    - title: Reference
      description: Look up stable public surfaces and configuration boundaries.
      cards:
        - title: Interfaces
          description: Codex, DSH, CLI, Python, HTTP, and MCP.
          href: en/docs/reference/interfaces/
        - title: Configuration
          description: Paths, Server, inference, and integration settings.
          href: en/docs/reference/configuration/
        - title: API reference
          description: Public Python modules, models, and contracts.
          href: en/modules/
    - title: Development
      description: Read the contracts behind Memory and remote access.
      cards:
        - title: Core protocol
          description: Source, Artifact, Trigger, and application contracts.
          href: en/development/core-protocol/
        - title: Memory layer
          description: Storage, recall, maintenance, and candidate review.
          href: en/development/memory-layer/
        - title: Remote access
          description: HTTP, MCP, and Python client implementation.
          href: en/development/remote-access-implementation/
---
