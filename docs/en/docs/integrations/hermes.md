---
title: Hermes
description: Install the Hermes MemoryProvider and slash-command companion, then connect them to PowerContext Server.
status: community
---

# Hermes

`community`

The integration contains a standard Hermes `MemoryProvider` and a standalone slash-command plugin. Hermes remains
responsible for the conversation and memory lifecycle; the provider sends recall, capture, and explicit operations to
a separately running PowerContext Server. The companion registers `/pc` and `/powercontext` before the provider is
activated. Backend failures do not interrupt the Hermes conversation.

## Prerequisites

- Hermes Agent 0.20.4 or newer, available on `PATH`;
- PowerContext CLI and Server installed from `master`;
- a running PowerContext Server.

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

## Install both plugins

In another terminal, install or refresh both plugins from the matching revision:

```bash
powercontext setup hermes
powercontext doctor hermes
```

The setup command copies the provider to `$HERMES_HOME/plugins/powercontext` and installs
`$HERMES_HOME/plugins/powercontext-command`. It enables the companion unless explicitly disabled, without granting
built-in tool override permissions. If `memory.provider` is absent, setup selects PowerContext automatically;
an existing provider choice, including an explicitly empty value, is preserved. Setup does not start the Server.

To switch from an existing provider or change provider settings, run the Hermes memory setup wizard and select
`PowerContext`:

```bash
hermes memory setup
```

On Hermes 0.20.4, use the generic command above. `hermes memory setup powercontext` selects the provider but does not
open its configuration wizard. Restart Hermes after setup.

## Verify recall and writes

```bash
hermes powercontext status
hermes powercontext remember preference "The user prefers uv"
hermes powercontext search "Python package manager"
```

Inside an interactive Hermes session, `/pc status` should reach the same active provider. Use `/pc ` followed by
Tab/Down to inspect the available Memory, Handoff, Experience, Skill, review, statistics, trace, and Scope
commands. Hermes 0.20.4 does not provide enough invocation context to route gateway slash commands safely, so the
companion rejects gateway invocations; use the provider's Hermes tools in gateway sessions.

The provider uses `http://127.0.0.1:8000` by default. The Server resolves an explicit Scope first, then durable session
and workspace bindings, and finally its default Scope. Hermes hashes the workspace path only as an external binding
key; it does not generate Scope IDs from profiles, users, repositories, or directories.

## Configure the connection

The wizard writes non-sensitive settings to `$HERMES_HOME/powercontext/config.json`. Environment variables override
the file:

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_HERMES_CONFIG` | Config file path; defaults to `$HERMES_HOME/powercontext/config.json` |
| `POWERCONTEXT_HERMES_BASE_URL` | PowerContext Server URL |
| `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP` | Explicitly permit non-loopback plaintext HTTP; defaults to `false` |
| `POWERCONTEXT_HERMES_AUTHORIZATION` | Complete authorization header, such as `Bearer <token>` |
| `POWERCONTEXT_HERMES_TOKEN` | Bare-token shorthand used when `AUTHORIZATION` is absent |
| `POWERCONTEXT_HERMES_SCOPE_ID` | Explicit server-owned Scope ID |
| `POWERCONTEXT_HERMES_MAX_BYTES` | Maximum prepared-context size, from 512 to 32768 bytes |
| `POWERCONTEXT_HERMES_TIMEOUT` | HTTP request timeout in seconds |
| `POWERCONTEXT_HERMES_CAPTURE_TURNS` | Capture completed turns as Sources |
| `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END` | Run Memory extraction at session end |
| `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS` | Capture filtered new turns before compression; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE` | Record recalled context in sensitive local JSONL traces; disabled by default |
| `POWERCONTEXT_HERMES_EVALUATION_TRACE_PATH` | Override the evaluation trace directory |

Let the Hermes wizard store authorization in its protected `.env` secret store; do not put the token in
`config.json`. Plain HTTP is allowed on loopback by default; use HTTPS for remote access or explicitly set
`POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP=true`. This does not disable HTTPS certificate validation. See
[Connect to a remote Server](../operate/connect-remote-server.md) for guided setup and endpoint-bound saved consent.
Evaluation traces contain prompts and recalled context; keep them local and protect them as sensitive data.

## Data flow and capture controls

These controls apply to the active PowerContext provider. Environment variables override the provider configuration.
Turning off turn capture does not turn off recall, pre-compression capture, or explicit writes.

| Event/entry | Data sent | Direct recipient | Default/trigger | Disable | Effect when disabled |
| --- | --- | --- | --- | --- | --- |
| Recall / prefetch | Query (trimmed; when longer than 8192 characters or 8192 UTF-8 bytes, cut to its most recent text within both bounds; not credential-scrubbed), Scope and preparation options; prepared context returns to Hermes | PowerContext Server | Active provider, nonempty query and resolved Scope; cached prefetch may be reused | Select a different memory provider to stop this provider's automatic recall; there is no separate recall switch here | PowerContext automatic recall stops; capture switches alone leave recall available |
| Completed turn | Bounded user/assistant text pair without secret redaction, plus session metadata; not the complete tool transcript | PowerContext Server, as a Source | `capture_turns=true`; automatic writes are suppressed outside the primary agent context | Set `POWERCONTEXT_HERMES_CAPTURE_TURNS=false` before entering sensitive text | Stops this Source capture; recall, pre-compression capture and explicit writes remain independent |
| Session end | Flush request for already accepted inputs, not another upload of the session transcript | PowerContext Server | `flush_on_session_end=true`; waits for background writes and checks extraction capability | `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END=false` | Stops this flush trigger; accepted Sources remain and other processing triggers are unaffected |
| Before compression | New user/assistant evidence window, excluding system/tool messages and redacting detected secrets | PowerContext Server, as a Source | `capture_pre_compress=false`; opt in with `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=true` | `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=false` | Stops this capture and its subsequent extraction attempt; a required checkpoint fails rather than reporting success |
| Explicit remember / built-in memory mirror | Requested Memory content or change, with exact references for updates | PowerContext Server | Explicit operation, or primary-context `on_memory_write` for add/replace/remove | Do not invoke the write or mirror-producing built-in operation; no dedicated mirror switch | Turning off turn capture does not disable these writes |
| Evaluation trace | Prompt/query, recalled context and correlation metadata; detected secrets are redacted | Local JSONL files, by default under `$HERMES_HOME/powercontext/evaluation-trace/` | `evaluation_trace=false`; opt in with `POWERCONTEXT_HERMES_EVALUATION_TRACE=true` | `POWERCONTEXT_HERMES_EVALUATION_TRACE=false` | Stops new trace records without disabling recall or capture; existing files remain sensitive |

Recall requests and completed-turn Sources do not scrub credentials. Redaction of evaluation traces does not redact
the query already sent to the Server; pre-compression filtering applies only to that separate capture path.

Pre-compression capture prefers the host-normalized `evidence_messages` when supplied, so earlier compression
summaries are not reintroduced as fresh evidence. The provider declares checkpoint API v2 and raises on an
uncommitted required checkpoint. This describes the provider contract: the repository's
[integration capability matrix](capabilities.md) still declares Hermes pre-compaction `unsupported`, and does not
establish a verified, required-capable real-host lifecycle. Source durability at checkpoint time is distinct from
successful extraction. Prepared context, a committed checkpoint, host injection, model use and a correct answer
are also distinct outcomes. Secret matching is a limited set of rules, not a guarantee that all sensitive data is removed.

There are three separate data directions: Hermes sends queries, captures and explicit operations to the Server;
the Server may send queries, candidates or evidence to its configured embedding, reranking or generation backends;
and Hermes may include returned historical context in requests to its own model. A loopback Server URL describes
only the first connection, not an absence of subsequent network transmission. Third-party plugin code does not
by itself imply a third-party network recipient. See [Server configuration](../operate/configuration.md),
[inference privacy protection](../operate/inference-protection.md) and
[context reuse and final admission](../operate/context-protection.md) for deployment and supported-payload boundaries.
Inference protection defaults to `disabled`; `shadow` and `advisory` observe requests without blocking delivery or
inspecting responses. Installing this provider does not establish enforcing protection for all Hermes model traffic.

Changing these switches does not erase existing Sources, Memory, revisions, traces or model-provider records.
Disabling generation does not disable Source capture; accepting a Source does not mean a Memory was generated.

## Enable automatic extraction only when needed

Completed-turn capture creates Source evidence. It does not create Memory by itself. Automatic Source-to-Memory
extraction requires a generation model on the Server:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
powercontext capabilities
```

The capability output must report Memory extraction as enabled. Explicit `hermes powercontext remember` writes do not
require a model.
