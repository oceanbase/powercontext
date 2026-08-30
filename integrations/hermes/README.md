# PowerContext integration for Hermes Agent

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
  "scope_id": "hermes:{profile}:{user_id}",
  "max_bytes": 8000,
  "timeout": 5,
  "capture_turns": true,
  "flush_on_session_end": true,
  "capture_pre_compress": false,
  "evaluation_trace": false,
  "workstream_persistence": true
}
```

Environment variables override file values:

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_HERMES_CONFIG` | Path to a JSON config file (defaults to `$HERMES_HOME/powercontext/config.json`). |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext server URL |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | Token shorthand; used when `AUTHORIZATION` is absent |
| `POWERCONTEXT_HERMES_SCOPE_ID` | Explicit scope or scope template |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Maximum prepared context size, 512–32768 |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP request timeout in seconds |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | Capture completed turns as PowerContext Sources |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | Run memory extraction at session end |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | Capture filtered new user/assistant turns before compression; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | Record recalled context in per-session local JSONL files; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | Override the evaluation trace directory |
| `POWERCONTEXT_HERMES_WORKSTREAM` | Read the shared Git-private Workstream scope binding; enabled by default |

The default scope template is `hermes:{profile}:{user_id}`. The provider uses
the active Hermes profile and gateway user identifier when available. For local
CLI sessions without a user identifier, it derives a stable identifier from
the active `HERMES_HOME` path. This prevents cross-profile memory leakage while
keeping memories available across sessions.

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
  default and uses stable source IDs for overlapping compression windows.
- `on_memory_write()` mirrors built-in Hermes memory additions as explicit
  entries and retires the mapped PowerContext entry for replacements/removals.
- Agent tools expose the complete PowerContext operation groups: Memory
  search/list/read/write/change tracking, Work Contract and Handoff flows,
  Experience/Skill proposal and generation, External Skills discovery/import,
  Artifact Candidate review, context/source operations, and statistics.
- Mutating operations are described as explicit user-authorized actions. Artifact
  approval and rejection should only be used after the candidate has been
  reviewed.
- When Workstream persistence is enabled, Hermes reads
  .git/powercontext/codex-workspace.json, the same Git-private binding used by
  the other integrations. An explicit scope_id configuration takes precedence.
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

### Read, revise, or retire a memory entry

`/pc get` and `/pc retire` do not accept a search keyword or a bare
`entry_id`. They require the complete `citation` object returned by
`/pc search`, including the current Memory revision and the entry version.
Copy only the `hits[].citation` value from the search response, not the whole
hit object.

For example, first write a memory entry and then search for it:

```text
/pc remember preference "Prefers uv for Python project management"
/pc search uv
```

The relevant part of the `/pc search uv` response includes both the returned
text and the citation needed by the exact-entry commands. The identifiers and
revision below are illustrative; always copy them from the current response:

```json
{
  "memory": {
    "family": "memory",
    "artifact_id": "memory",
    "revision": 2
  },
  "mode": "fts",
  "hits": [
    {
      "citation": {
        "memory_ref": {
          "family": "memory",
          "artifact_id": "memory",
          "revision": 2
        },
        "entry_id": "mem_ent_8f9653d66a664398aa18bc5c88e0283d",
        "entry_version_id": "mem_ver_b12a8e6434254cae8a747792905006ed"
      },
      "text": "Prefers uv for Python project management (venv, dependency resolution, lockfile) over pip/Poetry/pip-tools."
    }
  ]
}
```

Copy the `hits[0].citation` object from the actual response and use it as
follows:

```text
/pc get {"memory_ref":{"family":"memory","artifact_id":"memory","revision":2},"entry_id":"mem_ent_8f9653d66a664398aa18bc5c88e0283d","entry_version_id":"mem_ver_b12a8e6434254cae8a747792905006ed"}
/pc retire {"memory_ref":{"family":"memory","artifact_id":"memory","revision":2},"entry_id":"mem_ent_8f9653d66a664398aa18bc5c88e0283d","entry_version_id":"mem_ver_b12a8e6434254cae8a747792905006ed"} "no longer needed"
```

To revise instead of retiring, use the same citation with:

```text
/pc revise {"memory_ref":{"family":"memory","artifact_id":"memory","revision":2},"entry_id":"mem_ent_8f9653d66a664398aa18bc5c88e0283d","entry_version_id":"mem_ver_b12a8e6434254cae8a747792905006ed"} preference "Prefers uv for Python project management" "updated preference"
```

`retire` is a logical retirement; it removes the entry from active memory but
keeps its history. Because every memory mutation advances the artifact
revision, do not reuse this citation after `revise` or another write. Search
again and use the newest citation before the next `get`, `revise`, or `retire`.

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
