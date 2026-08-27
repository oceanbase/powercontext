# PowerContext Hermes Memory Provider

This plugin implements Hermes' `MemoryProvider` interface using the PowerContext
HTTP API. See [`integrations/hermes/README.md`](../../README.md) for setup,
configuration, scope isolation, and runtime behavior.

Requires Hermes Agent v0.20.4 or newer.

The plugin deliberately uses only the Python standard library for HTTP, so it
can be copied into Hermes without adding an HTTP client dependency. Its
provider configuration is read from `$HERMES_HOME/powercontext/config.json`.

To install or refresh the provider from a matching PowerContext release tag:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext doctor hermes
```

The setup command copies this exclusive provider to
`$HERMES_HOME/plugins/powercontext` and installs the standalone
`powercontext-command` companion at `$HERMES_HOME/plugins/powercontext-command`.
It also enables the companion without granting built-in tool override
permissions. The command requires the Hermes CLI to be installed and available
on `PATH`.

The provider participates in Hermes' generic memory setup wizard. Run the
command below and select `PowerContext` from the provider list:

```bash
hermes memory setup
```

On Hermes v0.20.4, `hermes memory setup powercontext` only selects the provider
and does not open the provider configuration wizard.

Non-sensitive values are saved to `powercontext/config.json`; authorization is saved
through Hermes' `.env` secret store.

Automatic Source-to-Memory extraction is available only when the PowerContext
server reports `memory_extraction: true`. Configure a server generation model
with `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` and its provider
credentials, then restart the server. Source capture and retrieval continue to
work when extraction is disabled.

Pre-compression capture is opt-in. Set `capture_pre_compress: true` in the
provider configuration, or set
`POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=1`. When enabled, only new user and
assistant turns are captured; system/tool messages are excluded and detected
secrets are redacted before sending them to PowerContext.

Evaluation tracing is also opt-in. Set `evaluation_trace: true` or
`POWERCONTEXT_HERMES_EVALUATION_TRACE=1` to record context injections in
per-session JSONL files under `$HERMES_HOME/powercontext/evaluation-trace/`.
Each event includes the current session ID, optional parent session ID, scope,
turn number, and event ID. The trace contains prompts and recalled context and
must be treated as sensitive local data.

The provider also supports the complete PowerContext operation surface through
Hermes tools: Memory listing/revision/change tracking, Work Contract and
Handoff flows, Experience/Skill proposal and generation, External Skills
discovery/import, Artifact Candidate review, context/source operations, and
statistics. Explicitly mutating tools should only be used with user
authorization.

When the provider is active, it also registers the bundled powercontext skill
guide so Hermes has the workflow and authorization rules for those operations.

Workstream persistence is enabled by default. When the current directory is a
Git workspace, Hermes reads the shared
.git/powercontext/codex-workspace.json binding used by the other integrations.
An explicit scope_id configuration takes precedence. The /pc workstream
command can inspect, create, or clear the binding.

The standalone companion registers `/pc` and `/powercontext` during normal
Hermes plugin discovery, so both aliases are known before the first Agent is
created. Type `/pc ` or `/powercontext ` and press Tab/Down to see the
available first-level PowerContext commands. Once this provider is active, the
companion forwards either command to the current interactive Hermes Agent.

Hermes v0.20.4 does not pass gateway session, user, workspace, or scope
context to plugin slash-command handlers. The companion consequently fails
closed for gateway invocations rather than selecting another session's
provider. Use the provider's Hermes tools for gateway sessions until Hermes
exposes that invocation context.

```text
/pc trace status
/pc trace enable
/pc trace disable
/pc trace sessions
/pc trace show [--session SESSION_ID]
/pc trace clear [--session SESSION_ID]
/pc status
/pc search QUERY
/pc list [--inactive]
/pc changes [SINCE_REVISION]
/pc stats [today|7d|30d]
/pc remember KIND TEXT [REASON]
/pc revise CITATION_JSON KIND TEXT [REASON]
/pc retire CITATION_JSON [REASON]
/pc flush
/pc handoff {contract|current|acknowledge|outcome|activate|prepare|finalize|commit|continue} PAYLOAD_JSON
/pc experience {propose|generate|get} PAYLOAD_JSON
/pc skill {propose|generate|get} PAYLOAD_JSON
/pc external-skills {scan|list|resolve|import} [PAYLOAD_JSON]
/pc review {list|get|approve|reject|revise} [PAYLOAD_JSON]
/pc workstream {status|bind SCOPE_ID|clear}
/pc call OPERATION [PAYLOAD_JSON]
```

For the required citation format and copy-paste examples for `/pc get`,
`/pc revise`, and `/pc retire`, see the [memory entry operation guide](../../README.md#read-revise-or-retire-a-memory-entry).

## CLI commands

After enabling the provider and restarting Hermes so it discovers the command
tree, use:

```bash
hermes powercontext --help
hermes powercontext status
hermes powercontext search "Python project management"
hermes powercontext remember preference "The user prefers uv"
hermes powercontext flush
hermes powercontext call get_stats '{"period":"7d"}'
```

Use `--scope-id` to inspect a specific scope:

```bash
hermes powercontext search "deployment decision" --scope-id hermes-smoke-test
```
