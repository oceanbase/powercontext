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
key; it does not generate Scope IDs from profiles, users, repositories, or directories. In a linked Git worktree that
has no workspace binding of its own, resolution uses the binding of the same directory in the main checkout, so a
checkout's binding also covers its linked worktrees. A binding made in the worktree takes precedence, and binding or
clearing there changes only the worktree's own binding.

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
| `POWERCONTEXT_HERMES_PROMPT_RECALL` | Prepare and inject context for each prompt and offer `powercontext_prepare_context`; defaults to `true`, and `false` [leaves prompt-time context to the gateway](#leave-prompt-time-context-to-the-gateway) |
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
| Recall / prefetch | Query (trimmed; when longer than 8192 characters or 8192 UTF-8 bytes, cut to its most recent text within both bounds; recognized credentials replaced with `[REDACTED]`), Scope and preparation options; prepared context returns to Hermes | PowerContext Server | `prompt_recall=true`, active provider, nonempty query and resolved Scope; each turn prepares its own prompt, and the post-turn background prefetch sends nothing | `POWERCONTEXT_HERMES_PROMPT_RECALL=false`; selecting a different memory provider stops all of this provider's operations | No preparation request and no injected context, and `powercontext_prepare_context` is not offered; capture, the other tools and flushes keep working. Capture switches alone leave recall available |
| Completed turn | Bounded user/assistant text pair, plus session metadata; not the complete tool transcript. A turn whose user or assistant text contains a recognized credential is skipped as a whole | PowerContext Server, as a Source | `capture_turns=true`; automatic writes are suppressed outside the primary agent context | Set `POWERCONTEXT_HERMES_CAPTURE_TURNS=false` before entering sensitive text | Stops this Source capture; recall, pre-compression capture and explicit writes remain independent |
| Session end | Flush request for already accepted inputs, not another upload of the session transcript | PowerContext Server | `flush_on_session_end=true`; waits for background writes and checks extraction capability | `POWERCONTEXT_HERMES_FLUSH_ON_SESSION_END=false` | Stops this flush trigger; accepted Sources remain and other processing triggers are unaffected |
| Before compression | New user/assistant evidence window, excluding system/tool messages; recognized credentials in each message are replaced with `[REDACTED]`, and the message stays in the window | PowerContext Server, as a Source | `capture_pre_compress=false`; opt in with `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=true` | `POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=false` | Stops this capture and its subsequent extraction attempt; a required checkpoint fails rather than reporting success |
| Explicit remember / built-in memory mirror | Requested Memory content or change, with exact references for updates | PowerContext Server | Explicit operation, or primary-context `on_memory_write` for add/replace/remove | Do not invoke the write or mirror-producing built-in operation; no dedicated mirror switch | Turning off turn capture does not disable these writes |
| Evaluation trace | Prompt/query, recalled context and correlation metadata; detected secrets are redacted | Local JSONL files, by default under `$HERMES_HOME/powercontext/evaluation-trace/` | `evaluation_trace=false`; opt in with `POWERCONTEXT_HERMES_EVALUATION_TRACE=true` | `POWERCONTEXT_HERMES_EVALUATION_TRACE=false` | Stops new trace records without disabling recall or capture; existing files remain sensitive |

Recall queries and completed-turn capture follow the [automatic prompt privacy](index.md#automatic-prompt-privacy)
policy: recognized credentials are replaced in the query, and a turn whose user or assistant text contains one is not captured.
Pre-compression capture replaces the same recognized credentials in each message, then applies the plugin's own
redaction, and keeps the message in the window, so a credential in the transcript does not make a required checkpoint
fail. A turn that completed-turn capture skips can therefore still reach the Server in a pre-compression window, with
its recognized credentials replaced.

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

## Leave prompt-time context to the gateway

When Hermes sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each model request and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. The provider's recall appends such a block to each prompt, so turn recall
off. Turn off turn capture as well, because the gateway captures the turns instead. Keep the defaults where Hermes
calls its model directly, because the gateway does not carry that traffic.

### Turn the provider's prompt recall and turn capture off

Set both switches where Hermes starts, or store `"prompt_recall": false` and `"capture_turns": false` in
`$HERMES_HOME/powercontext/config.json`, then restart Hermes:

```bash
export POWERCONTEXT_HERMES_PROMPT_RECALL=false
export POWERCONTEXT_HERMES_CAPTURE_TURNS=false
hermes
```

With recall off, the provider sends no preparation request and injects no context into the prompt. It also does not
offer `powercontext_prepare_context`, because the tool's result stays in the conversation and Core would reject every
later request of that conversation. The other `powercontext_*` tools, `/pc` commands and the session-end flush keep
working. With turn capture off, the provider no longer stores completed turns, but the session-end flush still requests
processing of the Scope's pending Sources, which include the turns the gateway captured in that Scope. Leave
`capture_pre_compress` off (the default): it would store the new user and assistant messages before each compression,
which the gateway has already captured as turns.

Start a new Hermes session (`/new`) after the change. Hermes stores the context that recall appended to each earlier
user message and resends it with the conversation history, and it keeps `powercontext_prepare_context` results. The
gateway does not remove either from a request, so Core rejects every later request of a session that has them.

### Point Hermes at the gateway

Add a named provider for the gateway's `/gateway/v1` to `~/.hermes/config.yaml` with the Responses transport, and
select it:

```yaml
providers:
  powercontext-gateway:
    api: http://127.0.0.1:8787/gateway/v1
    key_env: PC_GATEWAY_KEY
    transport: codex_responses

model:
  provider: custom:powercontext-gateway
  default: approved-model
  max_tokens: 32000
```

Put the gateway's `PC_GATEWAY_API_KEY` value in `~/.hermes/.env` as `PC_GATEWAY_KEY`; Hermes sends it as the bearer
token. On the Responses route the gateway calls the model without streaming and replays the checked response as a
stream, so a streamed turn completes and is captured. Keep the Responses transport. With
`transport: chat_completions`, the gateway forwards a streamed request on the Chat Completions route. An enforcing Core
withholds streamed Chat Completions responses, and the gateway returns HTTP 400. With a non-enforcing Core, including
the default `disabled` mode, the gateway releases the stream unchanged. With `PC_GATEWAY_SEDIMENT=on`, it also
assembles the assistant's text from a terminal stream and captures the completed turn.

### Choose the Core target

Core needs a target for the profile that `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` names, in every privacy mode, including
`disabled`. Set that variable to the Responses profile containing Hermes' approved endpoint, model and protocol. Without
that profile, Core withholds each request with `target_unsupported`, and the gateway returns HTTP 400 for every turn.
See [Configure a supported target](../operate/inference-protection.md#configure-a-supported-target).

Hermes 0.20.4 sends `store: false` and `prompt_cache_key` on every Responses request to a custom endpoint, and
`max_output_tokens` only when `model.max_tokens` is set. An enforcing Core admits its requests when:

- `model.max_tokens` is set, and the target declares `context_window_tokens`, `max_output_tokens` at least
  `model.max_tokens`, and `validated_utf8_token_bound`. Without an output limit, Core withholds each request with
  `output_limit_required`;
- the target lists `provider-default` in `allowed_cache_retention`. Otherwise Core withholds each request with
  `cache_retention_unapproved`;
- the target lists in `response_models` the model names the provider returns when they differ from `model`.
  Otherwise Core withholds each response with `response_model_mismatch`.

The window check counts each UTF-8 byte of the request as a token, so leave room in `context_window_tokens` for
Hermes' tool definitions, system prompt and conversation. Restart the gateway after changing Core's mode, because it
discovers the mode at startup. See [Context reuse and final admission](../operate/context-protection.md) and
[Inference privacy protection](../operate/inference-protection.md) before choosing a mode.

### Configure the gateway

- Set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to the Core profile for Hermes' Responses route. Do not set
  `PC_GATEWAY_UPSTREAM_URL*`; those legacy variables prevent startup. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to
  the provider key, `PC_GATEWAY_API_KEY` to the key Hermes sends, `PC_GATEWAY_CORE_TOKEN` to the Gateway service
  credential, and `PC_GATEWAY_SEDIMENT=on`. Core access control must be `enforced`; grant the service identity
  `gateway.user` and `server.observer`, and grant `scope.contributor` when capture is enabled. See
  [Gateway model destination](../operate/deploy-server.md#gateway-model-destination) for the binding commands.
- The gateway prepares and captures every conversation in the one Scope that `PC_GATEWAY_DEFAULT_SCOPE` names, not in
  the Scope this provider resolves for each workspace. Set `PC_GATEWAY_DEFAULT_SCOPE` to the Scope that
  `hermes powercontext status` prints as `scope_id`, or set the provider's `scope_id` and `PC_GATEWAY_DEFAULT_SCOPE` to
  the same existing Scope.
- Run the gateway in a combined deployment (`PC_GATEWAY_CORE_UDS`), which injects Hermes' requests by default.
  Hermes 0.20.4 sends no per-session header to a custom Responses endpoint, and a provider's `extra_headers` are the
  same for every session, so the gateway infers the conversation from the request instead. Hermes sends the whole
  history on every request, without `previous_response_id` or `conversation`. It puts the system prompt, which it
  builds once per session, in `instructions`, and the history starts with the first user message. The gateway
  therefore prepares once per user message and replays that context to the tool-loop steps.
- The inferred ID is local to the gateway process and serves only this replay. Sessions that open with the same
  system prompt and first user message share one. The gateway still captures each completed turn. Without a
  conversation header, capture has no conversation identity that survives a gateway restart, and prefix extraction is
  not available. A compression rebuilds Hermes' system prompt and rewrites the history, so afterwards the gateway can
  infer a new conversation and prepare again.
- Auxiliary requests that Hermes sends to the same provider, such as a title or a compression summary, carry their own
  prompt, so the gateway infers a separate conversation for each and prepares it. With `PC_GATEWAY_SEDIMENT=on` it also
  captures the completed response as a turn.
- A standalone gateway (`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. Do not put a fixed
  `X-PowerContext-Conversation-ID` in `extra_headers`, because the gateway would then treat every session as one
  conversation.

See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection) and [Gateway authentication
boundaries](../operate/deploy-server.md#gateway-authentication-boundaries).

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
