# Claude Code integration

`plugins/powercontext` contains the PowerContext plugin distributed through the
Claude Code marketplace at the repository root.

The plugin is a client of a running PowerContext Server:

- `UserPromptSubmit` recalls one final bounded Prepared Context and injects it
  unchanged;
- the same hook captures the current user prompt as ordinary Content Source
  evidence by default;
- MCP exposes explicit Memory and Handoff operations;
- Server and transport failures never block normal Claude Code work.

The plugin resolves the current Scope through the Server. An explicit Scope has
priority, followed by durable session and workspace bindings, then the Server's
default Scope. It does not use a `Stop` hook and does not capture Claude's final
response in v1.

Validate the marketplace and plugin from a repository checkout:

```bash
claude plugin validate --strict .
claude plugin validate --strict integrations/claude-code/plugins/powercontext
```

Run the integration contract, Hook, CLI, and service-chain tests:

```bash
uv run pytest \
  tests/claude_code_plugin \
  tests/test_system_cli.py \
  tests/e2e/test_claude_code_service_chain.py \
  tests/e2e/test_mcp_transport.py
```

The service-chain tests load the checked-in `.mcp.json`, execute its header
helper, and exercise explicit Memory and Handoff workflows against public and
authenticated Server instances. Contract tests also reject machine-specific
Windows paths in the distributed integration files.

The default Server endpoint is `http://127.0.0.1:8000`. Set
`POWERCONTEXT_CLAUDE_AUTHORIZATION` to a complete `Bearer <token>` value
before starting Claude Code when the Server requires authentication. The MCP
header helper emits no `Authorization` header when this value is absent.
