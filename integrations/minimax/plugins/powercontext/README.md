# PowerContext for MiniMax Code

Context for work that humans and agents hand off and continue.

[Website](https://powercontext.oceanbase.io/) · [Documentation](https://powercontext.oceanbase.io/en/docs/) · [Source](https://github.com/oceanbase/powercontext)

PowerContext keeps project decisions and current progress available across conversations. In MiniMax Code, you can recall why a decision was made, save a constraint for later, or hand off unfinished work with the evidence and next steps someone needs to continue.

## Pick up where the work left off

Ask MiniMax Code to use the context your task needs:

- "Find our earlier decision about database migrations and show its sources."
- "Remember that this project must support Python 3.11."
- "Hand off this work with the current progress, remaining checks, and next step."
- "Resume the latest handoff for this project."

You choose what to keep. Memory stores decisions and constraints that should last beyond a conversation. A Handoff carries the current objective, progress, evidence, and unfinished work. You can also ask to correct outdated memory or retire information that no longer applies.

Search results include references so you can check the source. Preparing a handoff creates a temporary record; ask to commit it when you want a durable checkpoint that a later session can retrieve as the latest handoff.

## Get started

You need MiniMax Code, Python 3.11+, and `uv`. Use the server and plugin from the same repository revision.

Install PowerContext on the machine running MiniMax Code and create its configuration:

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
uv sync --frozen
uv run powercontext config init --output .env
```

Before starting the server, follow [Configure models](https://powercontext.oceanbase.io/en/docs/get-started/configure-models/) to set up generation and embedding models in `.env`, including provider credentials and the embedding dimension. The guide also covers enabling extraction and verifying vector search. `config init` creates a base configuration; it does not configure models for you.

Validate the configuration and start the server with that file:

```bash
uv run powercontext config validate --env-file .env
uv run powercontext server run --env-file .env
```

Keep the server running in its terminal. The plugin connects to `http://127.0.0.1:8000/mcp` by default.

Enable the PowerContext plugin in MiniMax Code. To check that MiniMax Code has discovered it, run:

```bash
mcode plugin list --marketplace local --json
```

The listing should show `powercontext` enabled with one Skill and one MCP server. The Skill is named `powercontext-project-context`; the MCP server is named `powercontext`.

MiniMax Code uses its own account or model provider. Saving and reading explicit memories and handing off current work do not require a separate PowerContext generation model. Vector search and model-generated content use the model services configured on PowerContext Server.

## Connect to a protected server

The bundled connection uses a local server without authentication. If your deployment requires a Bearer token, configure the endpoint and token in MiniMax Code's private `mcp.json`: `~/.minimax/mcp.json`, or `$MINIMAX_DATA_DIR/mcp.json` when that variable is set. Merge this entry into any existing `mcpServers` object:

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "streamable-http",
      "url": "https://your-powercontext-server.example/mcp",
      "headers": {
        "Authorization": "Bearer <your-server-token>"
      }
    }
  }
}
```

Replace the URL and token with your deployment values. Use `http://127.0.0.1:8000/mcp` for a protected server on the same machine. MiniMax Code 0.2.7 gives the enabled user-configured `powercontext` server precedence over the plugin's connection; the Skill remains available. Restart MiniMax Code after changing the configuration.

Keep this file private and out of version control; on Linux and macOS, restrict its permissions to `600`. Do not put the token in the plugin's `powercontext.mcp.json` or `plugin.json`. Enter the token value directly in the private configuration; a `${TOKEN}` placeholder will not load it from the environment.

For server-side setup, see [Enable authentication](https://powercontext.oceanbase.io/en/docs/operate/deploy-server/#enable-authentication). The PowerContext server token authenticates MCP requests; model provider credentials belong in the server's model configuration.

## Your data

The server stores context in a local SQLite database by default. Queries and content you ask to save go to that server. If you enable generation or embedding services, the server may send relevant content to the configured model endpoints.

The plugin saves context when requested; it does not automatically capture every conversation. Existing memory stays on the server when you close MiniMax Code.

## Troubleshooting

If PowerContext is unavailable, check that the server is running and that MiniMax Code can reach `http://127.0.0.1:8000/mcp`. If the plugin is missing from the listing, check that it is installed and enabled in the MiniMax data directory you are using.

For a custom endpoint, check the URL in your private MCP configuration. A `401` response means the server did not accept the credentials; check the Bearer token and restart MiniMax Code after updating it. A `403` response means the authenticated identity lacks permission for the requested operation.

An empty search means no matching memory was found. Ask to save the relevant decision or constraint if you want it available later. A failed save remains incomplete; the plugin reports the failure so you can retry after resolving the connection or server issue.

## Support

- [Manage context](https://powercontext.oceanbase.io/en/docs/workflows/)
- [Report an issue](https://github.com/oceanbase/powercontext/issues)

Maintained by PowerContext Team. Contact: [open_oceanbase@oceanbase.com](mailto:open_oceanbase@oceanbase.com).

PowerContext is licensed under the [Apache License 2.0](LICENSE).
