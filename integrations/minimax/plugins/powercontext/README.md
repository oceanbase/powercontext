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

Install the complete native plugin from the same checkout:

```bash
uv run powercontext setup minimax --source .
uv run powercontext doctor minimax --json
```

For custom/profile installations, pass `--minimax-data-dir /path/to/data`; for a launcher outside PATH, pass `--minimax-command /path/to/mcode`. The data root is selected from that flag, `MINIMAX_DATA_DIR`, `MAVIS_DATA_DIR`, the saved target, or the selected CLI’s native marketplace directory. With no CLI or selected profile, the fallback is `~/.minimax`. Existing private MCP configuration and user files are preserved; conflicting manual or modified installations require migration before replacement.

Files copied, native discovery, and CLI Server access are separate checks. Missing `mcode` leaves native verification incomplete and the overall command nonzero. The supported CLI qualification is recorded in the [MiniMax guide](https://powercontext.oceanbase.io/en/docs/integrations/minimax/); desktop discovery requires separate qualification.

Refresh Local plugins with `Ctrl+R` in `/plugins`, or restart MiniMax using the selected data directory. Setup preserves existing disablement. To check that MiniMax Code has discovered the enabled package, run:

```bash
mcode plugin list --marketplace local --json
```

The listing should show `powercontext` enabled with one Skill and one MCP server. The Skill is named `powercontext-project-context`; the MCP server is named `powercontext`.

MiniMax Code uses its own account or model provider. Saving and reading explicit memories and handing off current work do not require a separate PowerContext generation model. Vector search and model-generated content use the model services configured on PowerContext Server.

## Connect to a protected server

The distributed package defaults to a local server without authentication. For a protected deployment, supply `POWERCONTEXT_MINIMAX_AUTHORIZATION` through the process environment or a private environment file. Keep the file outside version control and restrict access to it. For example, `/path/to/private.env` can contain:

```dotenv
POWERCONTEXT_MINIMAX_AUTHORIZATION=<your-server-token>
```

Configure an installed plugin and check the result:

```bash
uv run powercontext config agent minimax \
  --server-url https://your-powercontext-server.example \
  --env-file /path/to/private.env
uv run powercontext doctor minimax --json
```

Use `http://127.0.0.1:8000` for a protected server on the same machine. Use the same data directory as your installation: `MINIMAX_DATA_DIR` and the `MAVIS_DATA_DIR` fallback override the saved target. For a first installation, run `setup minimax --source .` with the same `--server-url` and `--env-file` options.

MiniMax CLI 0.6.9 exposes private and plugin MCP connections independently, potentially as `powercontext` and `powercontext_2`. Setup/config synchronize their endpoints and headers so both tool sets reach the selected server. Restart MiniMax Code after changing the connection. Configuration does not automatically enable a disabled plugin or private entry.

If you edit MiniMax Code's private configuration manually, merge this entry into the existing `mcpServers` object in `<effective MiniMax data directory>/mcp.json`:

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

Replace the URL and token with your deployment values; use `http://127.0.0.1:8000/mcp` for a protected local server. After editing an enabled private entry, synchronize the installed plugin connection before restarting MiniMax Code:

```bash
uv run powercontext config agent minimax
uv run powercontext doctor minimax --json
```

A disabled private entry stays disabled. To configure the active plugin connection while keeping that entry disabled, use the explicit `--server-url` and authorization flow above.

Keep real tokens out of the source/distributed package, `plugin.json`, and examples. Setup/config store literal headers in the private `mcp.json` and synchronize them into the installed `plugins/powercontext/powercontext.mcp.json`; let these commands update the managed plugin connection. Keep both installed connection files private and out of version control. On Linux/macOS, setup/config apply owner-only permissions to updated connection files; restrict manually edited files and the credential environment file to mode `600`. On Windows, protect these files with appropriate ACLs. A `${TOKEN}` placeholder in the private JSON does not load a token from the environment.

For server-side setup, see [Enable authentication](https://powercontext.oceanbase.io/en/docs/operate/deploy-server/#enable-authentication). The PowerContext server token authenticates MCP requests; model provider credentials belong in the server's model configuration.

This package contains a Skill and native MCP configuration, with no local
PowerContext HTTP client. MiniMax Code owns the MCP connection and its HTTP/TLS
policy; `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP` and PowerContext's saved client
settings do not control that native transport. Configure remote endpoints
through setup/config so both MCP connections are updated. Prefer HTTPS: HTTP transmits
content and authorization headers without encryption.

## Your data

The server stores context in a local SQLite database by default. Queries and content you ask to save go to that server. If you enable generation or embedding services, the server may send relevant content to the configured model endpoints.

The plugin saves context when requested; it does not automatically capture every conversation. Existing memory stays on the server when you close MiniMax Code.

## Troubleshooting

If PowerContext is unavailable, check that the server is running and that MiniMax Code can reach the configured endpoint (`http://127.0.0.1:8000/mcp` by default). If the plugin is missing from the listing, check that it is installed and enabled in the MiniMax data directory you are using.

For a custom endpoint, check the URL in your private MCP configuration. A `401` response means the server did not accept the credentials; correct the Bearer token, run the configuration and doctor commands above to synchronize both connections, then restart MiniMax Code. If doctor reports that the private and plugin MCP connections differ, run `config agent minimax` for the same data directory; editing only the private file does not update the plugin connection. A `403` response means the authenticated identity lacks permission for the requested operation.

An empty search means no matching memory was found. Ask to save the relevant decision or constraint if you want it available later. A failed save remains incomplete; the plugin reports the failure so you can retry after resolving the connection or server issue.

## Support

- [Manage context](https://powercontext.oceanbase.io/en/docs/workflows/)
- [Report an issue](https://github.com/oceanbase/powercontext/issues)

Maintained by PowerContext Team. Contact: [open_oceanbase@oceanbase.com](mailto:open_oceanbase@oceanbase.com).

PowerContext is licensed under the [Apache License 2.0](LICENSE).
