# MiniMax local validation

Validation date: 2026-09-09. Environment: Linux, Python 3.14.6, and `mcode 0.2.7`. Plugin version: `0.2.0`. The contract baseline is master commit `4043710a`, with 102 HTTP operations and 30 exposed as MCP tools.

| Check | Result | Coverage |
| --- | --- | --- |
| Standard Skill validation | Passed | Frontmatter, naming, and basic Skill structure. |
| Official portable schemas | Passed | Portable `plugin.json` and `mcp.json` conform to their Agent Plugins 1.0.0 JSON Schemas. |
| `make minimax-plugin-check` | 13 passed | Package contents, independent versions, relative references, example arguments, credential rejection, source consistency, native loading, and the integration scenarios below. |
| Native MiniMax package loading | Passed | The host accepts `.minimax-plugin/plugin.json` with one Skill, one MCP server, and no Apps. |
| Real HTTP/MCP | Passed | Scope resolution, empty search, memory save/search/exact read, temporary handoff, commit, and exact continuation against a real server. Latest remains empty before commit. |
| Scripted `mcode exec` | Passed | A local response server drives the host to load the Skill and call MCP. An independent server read confirms one saved test memory. |
| Custom-model `mcode exec` | Passed | `qwen3.6-27b` uses `openai-completions` to load the Skill, save memory, and search it. Independent server reads confirm the content and references. |
| Custom-model read-only preview | Passed | Only `get_scope` is called; memory contents and versions remain unchanged. |
| Custom-model temporary handoff | Passed | The model calls `handoff_current_work` without `commit_handoff`. Latest remains empty, and the model reports that compatibility tests have not run. |
| MiniMax hosted model | Incomplete | Calls return Token Plan quota error `2056`; model routing remains unverified. |
| Cloud and Marketplace | Not tested | Cloud connectivity and Marketplace publication have not been tested. |

The native Skill identifier is `powercontext:powercontext-project-context`. Tool names use `mcp__powercontext__<operation_id>`. Write tests use a temporary SQLite Server and a separate MiniMax data directory.

Specification checks use the 1.0.0 text and schemas from [agent-plugins-spec](https://github.com/agentplugins/agent-plugins-spec/tree/ff8ab5e392cc87bd88d87c060815a87490e51003), plus the directory checks from [agent-plugins-example](https://github.com/agentplugins/agent-plugins-example/tree/5f3f5084a821aefa792e79500dd8f0462ab83473). Official schemas are checked separately; CI runs the repository's contract tests.

The scripted response server only selects the next tool call. Skill loading, MiniMax tool execution, MCP transport, and PowerContext Server use their real implementations. This test does not assess model interpretation, autonomous tool selection, or adherence to read-only previews.

Custom-model validation connects directly to an OpenAI-compatible service, letting the model choose tools and arguments. Connection settings come from `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` in a local environment file. They are used only in the temporary MiniMax data directory, and credentials are removed after the run. These scenarios use a test Scope and temporary SQLite database.

The model's initial attempt to load `powercontext-project-context` returned a missing-Skill result. Loading succeeded with `powercontext:powercontext-project-context`. Passing scenarios supplied an explicit Scope and the full Skill identifier. These results do not establish reliable natural-language discovery or describe MiniMax hosted-model behavior.

A simple text request to the custom model made no MCP calls. Further validation should cover broader natural-language routing, search versus inventory, recovery from failed calls, and the MiniMax hosted model.
