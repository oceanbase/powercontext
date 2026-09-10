# Deployed acceptance

Run against an isolated CE workspace and test PC Scopes with a configured memory extraction pipeline.

1. Build the API, Web and dify-agent services from `feat/agent-v2-mem`. Install both official-CLI-built plugin packages.
2. Validate the PC provider connection and bind two different runtime users. Confirm the wrong credential and a missing
   binding produce diagnostic errors without falling back to a default Scope.
3. Legacy Agent: choose the PC strategy and two callbacks. Invoke a real business tool that returns a unique fact. Confirm
   the callbacks are absent from the model tool schema and capture contains the visible tool result.
4. Flush the first user's evidence. Start a new run and confirm recall contains that fact. Invoke as the second user and
   verify the fact is absent. Repeat with an explicitly configured business identifier to verify intentional sharing.
5. Repeat the same scenario through Workflow Agent V2 and a standalone Agent app. Save/reload/publish/reopen the Agent
   configuration and verify references survive. Confirm no credential secret is present in the stored config or DSL export.
6. Force long history and tool results past the input budget. Confirm tool-call/result pairing remains valid, recall is
   counted in the context budget, and persisted V2 history contains no external-memory instruction block.
7. Stop PC, return 401/403/429/503 and delay callbacks past 10 seconds. The agent should continue with degraded-memory
   diagnostics, with at most one failing invocation per callback per run. Restore PC and verify the next run retries.
8. Cancel during a model stream and pause/resume a V2 human-input request. Native history must remain resumable. Do not
   assume that an unacknowledged final capture survives a terminated process.
9. Inspect Sources for known credential fields, PC bearer tokens, hidden reasoning and oversized payloads. Check truncation
   markers and that ingestion only becomes recallable memory after extraction.
10. Validate both packages with the current official CLI and submit separately with README, privacy policy and license.
    Use the publisher's Marketplace account; V2 automatic-memory claims must name the Dify release that includes the layer.

Record the exact Dify, plugin-daemon, SDK, model plugin and PC revisions plus observed results. Do not count HTTP transport
stubs or an in-process Server test as a deployed acceptance run.
