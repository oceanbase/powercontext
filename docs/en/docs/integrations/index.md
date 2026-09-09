---
title: Connect Agents
description: Choose Plugins, MCP, Skills, an Agent Host, or a Python framework adapter.
---

# Connect Agents

Choose the integration mechanism, then the host or framework:

- **Plugins** package host configuration, tools, hooks, or Skills. Dedicated plugins can join the host lifecycle;
  the portable Agent Plugin provides MCP configuration and a Skill.
- **MCP** exposes Agent tools at the Server's `/mcp` endpoint. MCP alone does not provide automatic prompt capture,
  recall hooks, or Skill installation.
- **Skills** provide discoverable workflow instructions. Installing a Skill does not start the Server or grant host
  tool permissions. Use the [portable Agent Plugin](agent-plugin.md) in a compatible host, or
  [export a managed Skill](../workflows/configure-agent-skill-targets.md).

## Agent Hosts

The 8 Agent Hosts use `official` for PowerContext project maintenance and `community` for community contributions.
`official` does not imply endorsement by a host vendor.

| Agent Host | Stewardship | Integration |
| --- | --- | --- |
| [Codex](codex.md) | `official` | Prompt Hook + MCP + Skill |
| [Claude Code](claude-code.md) | `community` | Prompt Hook + MCP + Skill |
| [DeepSeek Harness](dsh.md) | `community` | HTTP plugin + pc_* + /pc |
| [Hermes](hermes.md) | `community` | MemoryProvider + /pc |
| [OpenClaw](openclaw.md) | `community` | Memory plugin + lifecycle hooks |
| [OpenCode](opencode.md) | `community` | HTTP plugin + pc_* |
| [Pi Coding Agent](pi.md) | `community` | Extension + pc_* + /pc |
| [WorkBuddy](workbuddy.md) | `community` | Prompt Hook + MCP + Skill |

Each integration connects to a separately running Server. Follow its guide in the table for installation, connection settings, authentication, and diagnostics.

To select multiple hosts interactively, run:

```bash
powercontext setup select --source oceanbase/powercontext --ref master
```

Use the same ref as the Server. The selector lists hosts in the CLI catalog; consult each Agent guide for its supported installation path.

## Python Agent frameworks

All 3 adapters are `community`. Install them in the application environment; do not use
`powercontext setup <host>`:

| Framework | Integration | Current availability |
| --- | --- | --- |
| [Pydantic AI](pydantic-ai.md) | Toolset for Memory read/write and context | `experimental` |
| [LangChain](langchain.md) | Middleware for context and optional Source capture | `master_only` |
| [LangGraph](langgraph.md) | Recall hook and Memory tools | `master_only` |

## Evaluation integrations

[Bub is for evaluation only](evaluation.md). It is not part of the Agent Host or Python Agent framework support lists.

## Capabilities and availability

The [capability matrix](capabilities.md) is generated from `integrations/capabilities.toml`.
The current capability sets for all 8 hosts and Bub are marked `master_only`; Pydantic AI is `experimental`.
`released` means available from a specified release tag, `master_only` means implemented but unreleased, and
`experimental` carries no stability guarantee. These labels are separate from stewardship and the
Minimal / Recommended / Full capability profiles.

Use the same release tag or commit for the Server and integration. Check [installation requirements](../get-started/install-and-run.md):
Windows support is `experimental`, and does not imply that every host or optional backend supports Windows.
