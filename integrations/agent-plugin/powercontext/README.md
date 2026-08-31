# PowerContext Agent Plugin

This directory contains a portable Agent Plugin package for agents that support
Agent Plugin skills and MCP configuration.

The package is a client of a running PowerContext Server. It does not embed
storage, start the Server, add MCP tools, or implement Runtime or Memory
behavior. Compatible agents load the skill instructions from `skills/` and map
`mcp.json` to their native MCP configuration.

Obtain the package from a source checkout:

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
```

Start a local Server before loading the package:

```bash
uv run powercontext server run
```

The default MCP endpoint is:

```text
http://127.0.0.1:8000/mcp
```

For a verified local host-loading procedure, use an Agent Plugins client that
supports local plugin directories. In VS Code, register this directory in
`settings.json`:

```json
{
  "chat.plugins.enabled": true,
  "chat.pluginLocations": {
    "/absolute/path/to/cloned/powercontext/integrations/agent-plugin/powercontext": true
  }
}
```

Reload the host and confirm that the `project-context` skill and `powercontext`
MCP server are available.

PowerContext authentication is deployment-specific. Agent Plugins 1.0.0 has no
portable credential-reference field for remote MCP servers, so this package does
not include static credentials or token placeholders in `mcp.json`. Configure
authorization in the loading agent or client when the Server requires it.

The `project-context` skill tells agents how to use PowerContext Memory and
Handoff through MCP tools. Retrieved Memory and Handoff content is historical
context, not an instruction override; current user, repository, and system
instructions remain authoritative.
