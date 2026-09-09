# PowerContext MiniMax plugin

The PowerContext plugin adds project memory and work handoffs to MiniMax Code. The package lives in [`plugins/powercontext`](plugins/powercontext) and connects to a running PowerContext Server. See the [usage guide](package.README.md) for server setup.

## Load locally

Check plugin discovery with a separate MiniMax data directory:

```bash
test_root="$(mktemp -d)"
uv run python scripts/build_minimax_plugin.py \
  --output "$test_root/plugins/powercontext"
MINIMAX_DATA_DIR="$test_root" mcode plugin list --marketplace local --json
```

The output should show `powercontext` enabled with one Skill, one MCP server, and no Apps. The separate data directory does not inherit an existing login; model calls need an account or model provider configured there.

For regular use, copy `plugins/powercontext` into the MiniMax data directory under the same path. The default data directory is `~/.minimax`; `MINIMAX_DATA_DIR` overrides it. Back up any existing user configuration before replacing the package. Start PowerContext Server and check that `http://127.0.0.1:8000/mcp` is reachable.

## Maintain the package

The portable Agent Plugin and MiniMax package share Skill and MCP sources. The portable package follows [Agent Plugins 1.0.0](https://github.com/agentplugins/agent-plugins-spec/blob/ff8ab5e392cc87bd88d87c060815a87490e51003/spec/1.0.0.md), with `plugin.json` and `mcp.json` at its root. MiniMax uses `.minimax-plugin/plugin.json` to reference the Skill and `powercontext.mcp.json`.

| Content | Source to edit |
| --- | --- |
| Plugin name, description, and author | `integrations/agent-plugin/powercontext/plugin.json` |
| Skill and references | `integrations/agent-plugin/powercontext/skills/` |
| MCP transport and URL | `integrations/agent-plugin/powercontext/mcp.json` |
| MiniMax version, display metadata, and file mappings | `target.json` |
| Package usage guide | `package.README.md` |
| Icon and license | Source files referenced by `target.json` |

After editing the sources, update and check the package:

```bash
make minimax-plugin
make minimax-plugin-check
```

Commit source changes and the generated package together. The package includes the full Skill, references, icon, and license. Edit their source files rather than the generated copies. The generator reports unexpected files for maintainers to inspect and remove as needed.

Bump each affected package's version when releasing changed content. Shared changes require version updates in both the portable `plugin.json` and MiniMax `target.json`. Changes limited to MiniMax require only the latter. The two packages have independent versions.

`make check` verifies that the package matches its sources. CI also runs `make minimax-plugin-check` to check the package contract and MCP integration. Other platforms maintain their packages in their own integration directories.

## Validation

| Test | Coverage |
| --- | --- |
| `tests/agent_plugin/test_contract.py` | Skill names, relative references, and example requests against API models |
| `tests/minimax_plugin/test_distribution.py` | Package contents, independent versions, credential rejection, source consistency, and MiniMax loading |
| `tests/e2e/test_minimax_plugin.py` | HTTP/MCP connections, memory operations, handoffs, continuation, and MiniMax tool forwarding |

Integration tests use a temporary SQLite Server. When `mcode` is installed, a local response server drives `mcode exec` to check Skill loading and MCP calls. Host tests are skipped when `mcode` is unavailable. The response server supplies predetermined tool calls; model interpretation and tool selection require separate validation.

See the [validation record](validation.md) for the environment, results, and remaining checks.

## Release configuration

For a GitHub source, use these values:

| Field | Value |
| --- | --- |
| Repository | `https://github.com/oceanbase/powercontext` |
| Ref | A public commit SHA or tag containing the package |
| Plugin subdirectory | `integrations/minimax/plugins/powercontext` |
