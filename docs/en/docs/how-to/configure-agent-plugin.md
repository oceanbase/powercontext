---
title: Configure Agent Plugin
description: Load reusable PowerContext skills and MCP configuration in compatible agents.
---

# Configure Agent Plugin

PowerContext provides a portable Agent Plugin package for agents that can load
Agent Plugin skills and MCP configuration.

Clone the repository or use an existing source checkout that contains the
integration package:

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
```

The package root is:

```text
integrations/agent-plugin/powercontext/
```

It contains:

- `plugin.json`: portable Agent Plugin metadata.
- `mcp.json`: MCP configuration for the PowerContext Streamable HTTP endpoint.
- `skills/project-context/SKILL.md`: reusable instructions for Memory and
  Handoff workflows.

Start a PowerContext Server before loading the package:

```bash
uv run powercontext server run
```

The package points compatible agents to:

```text
http://127.0.0.1:8000/mcp
```

## Load it in VS Code

VS Code supports local Agent Plugin directories through `chat.pluginLocations`.
Use this procedure to verify that the package is loadable by a real Agent
Plugin host:

1. Open VS Code settings as JSON.
2. Enable Agent Plugins and register the PowerContext package root:

   ```json
   {
     "chat.plugins.enabled": true,
     "chat.pluginLocations": {
       "/absolute/path/to/cloned/powercontext/integrations/agent-plugin/powercontext": true
     }
   }
   ```

3. Reload VS Code.
4. Confirm that the PowerContext plugin appears in the Agent Plugins view and
   that the `project-context` skill and `powercontext` MCP server are available
   to chat.

The registered path must point at the package root that contains `plugin.json`,
`mcp.json`, and `skills/`.

The package does not start the Server, add MCP tools, or implement Runtime or
Memory behavior. Memory search, writes, revisions, Handoff behavior, and
persistence remain owned by the PowerContext Server and its existing MCP tools.

Authentication is managed by the loading agent or client. Agent Plugins 1.0.0
does not define a portable credential-reference field for remote MCP servers, so
the checked-in `mcp.json` contains no static credentials or token placeholders.
Do not put bearer tokens in `mcp.json`.

Use the package when an agent supports Agent Plugin skills and MCP
configuration, and you want explicit Memory and Handoff operations without an
agent-specific integration.
