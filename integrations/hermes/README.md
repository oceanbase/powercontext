# PowerContext integration for Hermes Agent

`community`

Use PowerContext as the Hermes memory provider to recall project history, save Memory, and hand off work. Hermes manages the Agent and conversation; a separate PowerContext Server stores and retrieves the data.

## Prerequisites

Install Hermes Agent v0.20.4 or newer and the PowerContext CLI, with both commands available on `PATH`. The examples use a local Server at `http://127.0.0.1:8000`; an existing remote Server can be selected during configuration.

## Install and verify

Install or refresh the integration from the PowerContext revision matching your Server:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref master
```

Setup installs the memory provider under `$HERMES_HOME/plugins/powercontext`, the `/pc` command companion under `$HERMES_HOME/plugins/powercontext-command`, and the `powercontext-project-context` Skill under `$HERMES_HOME/skills`. Hermes discovers the Skill through that standard directory.

For a local deployment, start the Server in another terminal:

```bash
powercontext server run
```

Activate the provider through Hermes' memory wizard, select `PowerContext`, then check the installation:

```bash
hermes memory setup
powercontext doctor hermes
```

The wizard writes nonsecret settings to `$HERMES_HOME/powercontext/config.json`, stores the authorization header in Hermes' `.env`, and sets `memory.provider`. On Hermes v0.20.4, use `hermes memory setup`; the provider-specific `hermes memory setup powercontext` shortcut does not open the wizard.

## Configuration

Configuration can also be stored manually in `$HERMES_HOME/powercontext/config.json`:

```json
{
  "base_url": "http://127.0.0.1:8000",
  "max_bytes": 8000,
  "timeout": 5,
  "capture_turns": true,
  "flush_on_session_end": true,
  "capture_pre_compress": false,
  "evaluation_trace": false
}
```

Environment variables override file values:

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_HERMES_CONFIG` | Path to a JSON config file (defaults to `$HERMES_HOME/powercontext/config.json`). |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext server URL |
| `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP` | Explicit non-loopback HTTP consent; overrides common and saved consent, including `false`. |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | Token shorthand; used when `AUTHORIZATION` is absent |
| `POWERCONTEXT_HERMES_SCOPE_ID` | Explicit server-owned Scope ID |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Maximum prepared context size, 512 to 32768 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP request timeout in seconds |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | Capture completed turns as PowerContext Sources |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | Run memory extraction at session end |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | Capture filtered new user/assistant turns before compression; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | Record recalled context in per-session local JSONL files; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | Override the evaluation trace directory |

The client accepts HTTPS and loopback HTTP by default. To configure a non-loopback HTTP endpoint explicitly:

```bash
powercontext setup hermes --server-url http://memory.example:8000 --allow-insecure-http
```

Setup stores nonsecret settings under `hosts.hermes` in `~/.config/powercontext/clients.json` (`POWERCONTEXT_CLIENT_CONFIG_FILE` overrides the path). `POWERCONTEXT_HERMES_BASE_URL` overrides the native `base_url` configuration. When neither is set, the provider checks `POWERCONTEXT_CLIENT_SERVER_URL`, saved client settings, and then the loopback default.

`POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP` overrides `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP`, then saved consent. Native Hermes configuration can store `allow_insecure_http` with `base_url`; this pair takes precedence over shared saved settings. Saved consent belongs only to the matching endpoint after stripping trailing slashes and `/mcp`. Changing an endpoint through the setup wizard or a session override clears inherited consent. Invalid boolean values are rejected.

HTTP sends request content and authorization headers without encryption. This opt-in does not change HTTPS certificate verification.

Hermes asks the Server to resolve an explicit Scope, durable session and workspace bindings, or the Server default, in that order. Workspace paths are hashed only as external binding keys. Hermes does not generate Scope IDs from profiles, users, repositories, or directories.

## Automatic memory behavior

| Event | PowerContext behavior |
| --- | --- |
| Context recall | Prepares bounded context and presents it as untrusted historical evidence. Background preparation caches the exact query for the next turn. |
| Completed primary-Agent turn | Queues Source capture without blocking the conversation. |
| Session end | Waits for queued writes and requests Memory extraction when the Server supports it. |
| Transcript compression | When enabled, captures filtered new user/assistant turns before compression and requests extraction. Overlapping captures use stable Source identities. |
| Built-in Hermes memory change | Mirrors additions, replacements, and removals to PowerContext. Conflicting updates stop for review. |

Turn capture and memory mirroring are disabled for `cron`, `flush`, and `subagent` Agent contexts and for `cron` or `subagent` session platforms. Recall remains available. Source capture and Memory generation are separate: accepted Sources can remain stored even when extraction is disabled or fails.

Tools expose Memory, Topic Memory, Work Contract, Handoff, Experience/Skills, external Skills, candidate review, context/source operations, and statistics. Writes require the user's corresponding authorization. Review a candidate before authorizing approval or rejection.

## Session commands

Use `/pc` or `/powercontext` in an interactive Hermes session with the PowerContext provider active. Type either alias followed by a space and press Tab/Down to see the available commands:

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
/pc revise REFERENCE_JSON KIND TEXT
/pc retire REFERENCE_JSON
/pc flush
/pc handoff {contract|current|acknowledge|outcome|activate|prepare|finalize|commit|continue} PAYLOAD_JSON
/pc experience {propose|generate|get} PAYLOAD_JSON
/pc skill {propose|generate|get} PAYLOAD_JSON
/pc external-skills {scan|list|resolve|import} [PAYLOAD_JSON]
/pc review {list|get|approve|reject|revise} [PAYLOAD_JSON]
/pc scope {status|bind SCOPE_ID|clear}
/pc call OPERATION [PAYLOAD_JSON]
```

### Read, revise, or retire a memory entry

Atomic search returns `hits[].memory` records. Copy the actual `artifact` and `state_version` values; do not invent entry IDs or a collection revision. `score` is the fused RRF rank score, not confidence or similarity. Response `matched_by` channels are `text` and `vector`; search request modes remain `auto`, `fts`, `vector`, or `hybrid`.

```text
/pc remember preference "Prefers uv for Python project management"
/pc search uv
```

The response has this shape (the identifiers are illustrative; use the actual returned values):

```json
{
  "mode": "fts",
  "hits": [{
    "memory": {
      "artifact": {"family": "atomic-memory", "artifact_id": "am_example", "revision": 1},
      "kind": "preference",
      "text": "Prefers uv for Python project management",
      "state": "active",
      "state_version": 0,
      "merged_into_id": null
    },
    "score": 0.01639344262295082,
    "matched_by": ["text"]
  }]
}
```

Use the captured reference for exact reads and conditional changes:

```text
/pc get {"artifact":{"family":"atomic-memory","artifact_id":"am_example","revision":1},"state_version":0}
/pc revise {"artifact":{"family":"atomic-memory","artifact_id":"am_example","revision":1},"state_version":0} preference "Prefers uv for Python projects"
/pc retire {"artifact":{"family":"atomic-memory","artifact_id":"am_example","revision":1},"state_version":0}
```

Use the latest returned reference for each change; these example commands are alternatives. You can revise active or forgotten records. Merged and retired records cannot be edited. `/pc retire` forgets a record reversibly. After a conflict, read and inspect the current record before retrying.

`/pc get` also accepts an exact Atomic ArtifactRef (`{"family":"atomic-memory","artifact_id":...,"revision":...}`). Use the returned Atomic references for reads and changes; legacy Memory citations are unsupported. `/pc changes` does not provide collection history. Use exact Artifact revision reads instead.

### Evaluation tracing

Trace enable/disable changes the current Hermes process only. Configure `evaluation_trace` or `POWERCONTEXT_HERMES_EVALUATION_TRACE` when tracing should be enabled for future sessions. Trace files may contain prompts and recalled context, so keep them local and review them as sensitive data.

## CLI commands

After restarting Hermes so it discovers the new command tree:

```bash
hermes powercontext --help
hermes powercontext status
hermes powercontext search "Python project management"
hermes powercontext remember preference "The user prefers uv"
hermes powercontext flush
hermes powercontext get '{"artifact":{"family":"atomic-memory","artifact_id":"am_example","revision":1},"state_version":0}'
hermes powercontext call get_stats '{"period":"7d"}'
```

Use `--scope-id` when inspecting a scope explicitly:

```bash
hermes powercontext search "deployment decision" --scope-id hermes-smoke-test
```

## Troubleshooting

Use `powercontext doctor hermes` to inspect the installed plugins and connection. Recall and automatic capture failures are logged without request content and normally allow the conversation to continue. Direct operation failures must still be reported to the user.

### Sources are captured but Memory is missing

Automatic extraction needs a PowerContext generation model. Configure `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` and the provider credentials, restart the Server, then check:

```bash
powercontext capabilities
```

The output must report `Memory extraction: enabled` before `hermes powercontext flush` or automatic extraction can create Memory. If extraction is disabled, the integration retains captured Sources and skips the flush.

### Compression checkpoint failed

Pre-compression capture is disabled by default. Enable `capture_pre_compress` when a transcript must be stored before compression. When Hermes requires a checkpoint through `compression.checkpoint_required`, a checkpoint failure keeps the uncompressed transcript. A later Memory extraction failure does not invalidate an already stored checkpoint.

### Gateway session commands are unavailable

Hermes v0.20.4 does not pass gateway session, user, workspace, or scope context to plugin slash-command handlers. The companion therefore fails closed for gateway invocations instead of routing a command to another session's PowerContext scope. Use the provider's Hermes tools for gateway sessions until Hermes exposes that invocation context.

### Memory mirroring reports a conflict

Mirroring retains versioned references in `$HERMES_HOME/powercontext-memory-map.json`. Inspect the conflicting Atomic record before retrying; the integration does not silently replace a stale write basis.

Existing map entries containing only legacy entry IDs are read-only. To resume mirroring an old item, inspect its migrated Atomic record and explicitly replace that item's value with the actual `{ "artifact": ..., "state_version": ... }` snapshot. Keep the item key and verify its text and Scope. An entry ID alone cannot identify the Atomic record.

## Manual directory installation

`powercontext setup hermes` performs this copy automatically for a user-level installation. Use the manual method only for a project-local provider or when the PowerContext CLI is not available.

Copy both Hermes plugins and the standard Skill:

```bash
mkdir -p "$HERMES_HOME/plugins" "$HERMES_HOME/skills"
cp -R integrations/hermes/plugins/powercontext \
  "$HERMES_HOME/plugins/powercontext"
cp -R integrations/hermes/plugins/powercontext-command \
  "$HERMES_HOME/plugins/powercontext-command"
cp -R integrations/hermes/plugins/powercontext/skills/powercontext-project-context \
  "$HERMES_HOME/skills/powercontext-project-context"
```

For project-local installation, copy both directories to `.hermes/plugins/` and enable project plugins with `HERMES_ENABLE_PROJECT_PLUGINS=1`. Keep the Skill in the standard user directory above, then enable the standalone companion:

```bash
hermes plugins enable powercontext-command --no-allow-tool-override
```

## Package provider option

Hermes also supports `hermes_agent.memory_providers` entry points. If this integration is distributed as a package, point the entry point at the provider package's `register` function:

```toml
[project.entry-points."hermes_agent.memory_providers"]
powercontext = "powercontext_hermes:register"
```

The directory layout is the reference implementation for direct installation.
