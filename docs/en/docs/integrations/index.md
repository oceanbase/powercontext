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

Installation guides: [Codex](codex.md), [Claude Code](claude-code.md), [DSH](dsh.md), [Hermes](hermes.md),
[OpenClaw](openclaw.md), [OpenCode](opencode.md), [Pi](pi.md), and [WorkBuddy](workbuddy.md).
MiniMax uses the generated native package described in [plugin distribution](../../development/plugin-distribution.md).

Each integration connects to a separately running Server. Follow its guide in the table for installation, connection settings, authentication, and diagnostics.

For remote endpoints, see [Connect to a remote Server](../operate/connect-remote-server.md): PowerContext clients
allow loopback HTTP by default and require explicit consent for non-loopback HTTP. Host-native MCP policies remain separate.

Setup and host doctor load shared Python rules from the selected integration source without an extra package install.
See [source selection and updates](../../development/plugin-distribution.md#develop-and-distribute).
To select multiple hosts interactively, run:

```bash
powercontext setup select
```

Choose a compatible integration source/ref. The selector lists targets from the selected source; consult each Agent guide for its supported installation path.

## Python Agent frameworks

All 3 adapters are `community`. Use the shared Python setup with an application interpreter:

```bash
powercontext setup langchain --python /app/.venv/bin/python
powercontext doctor langchain --server
```

The same setup and doctor flow covers Bub, OpenDAL, MiniMax, and the portable Agent Plugin.
See [plugin distribution](../../development/plugin-distribution.md) for installation locations and generated resources.

Use the [Pydantic AI](pydantic-ai.md), [LangChain](langchain.md), or [LangGraph](langgraph.md) adapter.

## Evaluation integrations

[Bub is for evaluation only](evaluation.md). It is not part of the Agent Host or Python Agent framework support lists.

## Integration catalog

Inspect the [build-derived catalog](capabilities.md) for native bindings. Check installed state with
`powercontext doctor integrations --json`; inspect the host's current tool catalog for permissions.

Keep the integration source compatible with the client and Server contracts. See [installation requirements](../get-started/install-and-run.md).
