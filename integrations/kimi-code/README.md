# PowerContext for Kimi Code

A native Kimi plugin with opt-in workflow profiles: Memory search/save and exact Artifact readback through HTTP MCP, plus bounded prompt recall and Source capture through UserPromptSubmit. Operations share the current Server-resolved Scope: explicit configuration, workspace binding, then Server default. Server owns memory generation; the Hook does not flush.

Install from a local checkout with powercontext setup kimi-code --source /path/to/powercontext --scope-id <existing-id>. Alternatively use `--workspace-scope` and the installed `scripts/scope.mjs bind --cwd <absolute-workspace> --scope-id <existing-id>` to select per-workspace Scopes. Keep Node on PATH, start Server, and open a fresh Kimi session afterwards.

See the [English guide](../../docs/en/docs/integrations/kimi-code.md) or [中文指南](../../docs/zh/docs/integrations/kimi-code.md).

The default `minimal` profile keeps three tools. Update with `--profile recommended` for Work Contract/Handoff/continuation/acknowledgment/outcome, or `--profile full` for Experience, managed/external Skill and candidate workflows as well. Omitted options preserve the installed profile. Open a new session after updates. Larger toolsets increase model schema overhead and do not grant authorization. `powercontext doctor kimi-code --mcp --json` checks current Server tool discovery without executing domain tools; host loading and operation success require separate evidence.

Run `powercontext doctor kimi-code --runtime --json` in the affected workspace to inspect local Hook history. Add `--session-id <id>` for an exact session. The report keeps Scope, prepare, capture and injection outcomes separate; accepted Source capture does not prove Memory generation, and emitted context does not prove model receipt. Observation storage contains no prompt/context bodies or credentials and survives plugin updates.

Real Kimi 2.1.1 on Windows with Node 24.15.0 passed native MCP save, exact-revision readback, fresh-session search, Source capture, scheduled Memory generation with Source evidence, actual model-request context injection, disabled capture, incorrect credentials and Server outage. Inference for both Kimi and the real background worker was controlled through a local OpenAI-compatible service. This establishes real host/Server interoperability, not live-model routing, other versions/platforms, HTTPS ingress, or TUI reload behavior.
