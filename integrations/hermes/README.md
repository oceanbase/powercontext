# PowerContext integration for Hermes Agent

`community`

This directory contains a standard Hermes `MemoryProvider` backed by a running
PowerContext server. It keeps Hermes responsible for memory lifecycle and Agent
orchestration while PowerContext provides external storage, retrieval, context
preparation, and memory lifecycle operations.

The integration requires Hermes Agent v0.20.4 or newer.

## Install with the PowerContext CLI

With Hermes installed and available on `PATH`, install or refresh the provider
from the matching PowerContext `master` revision:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref master
```

The command copies the exclusive memory provider to
`$HERMES_HOME/plugins/powercontext` and enables its standalone `/pc` command
companion at `$HERMES_HOME/plugins/powercontext-command`. Verify the
installation with:

```bash
powercontext doctor hermes
```

Then run `hermes memory setup` and select `PowerContext` to configure the
provider. Hermes v0.20.4 or newer is required.

<details>
<summary>Manual directory installation (alternative)</summary>

### Manual directory installation

`powercontext setup hermes` performs this copy automatically for a user-level
installation. Use the manual method only for a project-local provider or when
the PowerContext CLI is not available.

Copy both Hermes plugins into the user plugin directory:

```bash
cp -R integrations/hermes/plugins/powercontext \
  "$HERMES_HOME/plugins/powercontext"
cp -R integrations/hermes/plugins/powercontext-command \
  "$HERMES_HOME/plugins/powercontext-command"
```

For project-local installation, copy both directories to `.hermes/plugins/`
and enable project plugins with `HERMES_ENABLE_PROJECT_PLUGINS=1`. Then enable
the standalone companion:

```bash
hermes plugins enable powercontext-command --no-allow-tool-override
```

</details>

Start PowerContext separately:

```bash
powercontext server run
```

## Configuration

The provider uses `http://127.0.0.1:8000` by default. Run the generic Hermes
memory setup wizard and select `PowerContext` to configure and activate it
interactively; the wizard writes non-sensitive values to
`$HERMES_HOME/powercontext/config.json`, stores the authorization header in
Hermes' `.env` file, and sets `memory.provider`:

```bash
hermes memory setup
```

On Hermes v0.20.4, use the generic command above instead of
`hermes memory setup powercontext`; the provider-specific shortcut does not
open the configuration wizard.

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
| `POWERCONTEXT_HERMES_MAX_BYTES` | Maximum prepared context size, 512–32768 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP request timeout in seconds |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | Capture completed turns as PowerContext Sources |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | Run memory extraction at session end |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | Capture filtered new user/assistant turns before compression; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | Record recalled context in per-session local JSONL files; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | Override the evaluation trace directory |

The client accepts HTTPS and loopback HTTP by default. To configure a
non-loopback HTTP endpoint explicitly:

```bash
powercontext setup hermes --server-url http://memory.example:8000 --allow-insecure-http
```

Setup stores nonsecret settings under `hosts.hermes` in
`~/.config/powercontext/clients.json` (`POWERCONTEXT_CLIENT_CONFIG_FILE` overrides
the path). Hermes' native `base_url` configuration remains supported; when it
is absent, the provider also checks `POWERCONTEXT_CLIENT_SERVER_URL` and saved
client settings before the loopback default. The host URL environment overrides
native configuration.

A direct client `allow_insecure_http` argument overrides
`POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP`, then
`POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP`, then saved consent. Native Hermes
configuration can store `allow_insecure_http` with `base_url`; this pair takes
precedence over shared saved settings. Saved consent belongs only to the
matching endpoint after stripping trailing slashes and `/mcp`. Changing an
endpoint through the setup wizard or a session override clears inherited
consent. Invalid boolean values are rejected.

HTTP sends request content and authorization headers without encryption. This
opt-in does not change HTTPS certificate verification.

Hermes asks the Server to resolve an explicit Scope, durable session and
workspace bindings, or the Server default, in that order. Workspace paths are
hashed only as external binding keys. Hermes does not generate Scope IDs from
profiles, users, repositories, or directories.

## Runtime behavior

- `prefetch()` calls `/v1/context/prepare` and injects bounded context as
  untrusted historical evidence.
- `queue_prefetch()` performs the same preparation in the background and caches
  the exact query for the next turn.
- `sync_turn()` captures the completed turn through `/v1/sources/content` in a
  non-blocking single-worker queue.
- `on_session_end()` waits for queued writes and calls `/v1/memory/flush`.
- `on_pre_compress()` optionally persists only filtered new user/assistant turns
  and flushes them before Hermes discards old messages. It is disabled by
  default and uses stable source IDs for overlapping compression windows. The
  provider advertises the pre-compress checkpoint API v2 contract: it captures
  the host-normalized evidence list when Hermes supplies one, and a checkpoint
  that cannot be committed raises, so `compression.checkpoint_required` keeps the
  uncompressed transcript instead of discarding it behind a failed capture.
- `on_memory_write()` mirrors built-in Hermes memory additions as explicit
  Atomic records. Replacements revise the same identity with the actual content ETag; removals perform reversible
  forgetting with the captured Artifact reference and `state_version`. Its local map retains real versioned
  snapshots, so a later conflict is reported instead of silently refreshing the write basis.
  Existing maps containing only legacy entry IDs are read-only. To resume mirroring an old item, inspect its migrated
  Atomic record and explicitly replace that item's value in `$HERMES_HOME/powercontext-memory-map.json` with the
  actual `{ "artifact": ..., "state_version": ... }` snapshot. Keep the existing item key and verify its text and Scope;
  the adapter cannot infer an Atomic identity from an entry ID.
- Automatic writes stay off outside a primary agent context: an `agent_context` of
  `cron`, `flush` or `subagent`, or a `cron`/`subagent` session platform, disables
  turn capture and memory mirroring so scheduled runs and delegated children do
  not write into the user's own memory. Recall is unaffected.
- Agent tools expose the complete PowerContext operation groups: Memory
  search/list/read/write/change tracking, Work Contract and Handoff flows,
  Experience/Skill proposal and generation, External Skills discovery/import,
  Artifact Candidate review, context/source operations, and statistics.
- Mutating operations are described as explicit user-authorized actions. Artifact
  approval and rejection should only be used after the candidate has been
  reviewed.
- `/pc scope bind SCOPE_ID` stores a durable workspace binding in PowerContext.
  `/pc scope clear` removes it and resolves the current Scope again.
- When evaluation tracing is enabled, each session gets its own JSONL file under
  `powercontext/evaluation-trace/sessions/`. Events include the session ID,
  parent session ID, scope, turn number, and a unique event ID.
- Session-end and pre-compression flushes first check the server's
  `memory_extraction` capability. If extraction is disabled, captured Sources
  remain available and the flush is skipped without interrupting Hermes.

All backend failures fail open: they are logged without request content and do
not interrupt the Hermes conversation.

Automatic Source-to-Memory extraction requires a PowerContext generation model.
Configure `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` together with the
provider credentials, then restart the server. Verify the result with:

```bash
powercontext capabilities
```

The output must report `Memory extraction: enabled` before
`hermes powercontext flush` or automatic session-end extraction can create
Memory entries.

## Session slash command

The standalone companion registers `/pc` and `/powercontext` during normal
Hermes plugin discovery, before the first Agent is created. Both aliases are
forwarded to the PowerContext Memory Provider for the current interactive
Hermes Agent once it is active. Type `/pc ` or `/powercontext ` and press
Tab/Down to see the available first-level commands:

Hermes v0.20.4 does not pass gateway session, user, workspace, or scope
context to plugin slash-command handlers. The companion therefore fails closed
for gateway invocations instead of routing a command to another session's
PowerContext scope. Use the provider's Hermes tools for gateway sessions until
Hermes exposes that invocation context.

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

Atomic search returns `hits[].memory` records. Copy the actual `artifact` and `state_version` values; do not invent
entry IDs or a collection revision. `score` is the fused RRF rank score, not confidence or similarity.
Response `matched_by` channels are `text` and `vector`; search request modes remain `auto`, `fts`, `vector`, or `hybrid`.

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

Run each mutation with the current returned reference; these example commands are alternatives. Revision reads the
current content ETag and submits `If-Match`. Public manual content edits compare content revision only; they do not
compare lifecycle state versions. Active and forgotten records can be edited; merged and retired records cannot.
`retire` is the retained host command name for reversible forgetting, checked against both the actual Artifact
reference and its captured `state_version`. Refresh and inspect after a conflict before retrying.

Get also accepts a plain exact Atomic ArtifactRef. Genuine legacy `MemoryCitation` objects with `memory_ref`,
`entry_id`, and `entry_version_id` remain supported for exact historical reads only. Legacy revision and retirement
writes are rejected. `/pc changes` explicitly reports unsupported collection history; use precise Artifact revision
reads. The `powercontext_get_memory` and `powercontext_retire_memory` tools take a `reference` object, while
`powercontext_revise_memory_entry` takes that object in `citation`.

Trace enable/disable changes the current Hermes process only. Configure
`evaluation_trace` or `POWERCONTEXT_HERMES_EVALUATION_TRACE` when tracing should
be enabled for future sessions. Trace files may contain prompts and recalled
context, so keep them local and review them as sensitive data.

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

## Package provider option

Hermes also supports `hermes_agent.memory_providers` entry points. If this
integration is distributed as a package, point the entry point at the provider
package's `register` function:

```toml
[project.entry-points."hermes_agent.memory_providers"]
powercontext = "powercontext_hermes:register"
```

The directory layout is the reference implementation for direct installation.
