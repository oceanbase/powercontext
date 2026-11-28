---
title: Pi Coding Agent
description: Install the native PowerContext package for Pi and control recall, capture, and durable tool writes.
status: community
---

# Pi Coding Agent

`community`

## Install or refresh the package

Install Pi, then install the package from the same PowerContext ref as the CLI:

```bash
powercontext setup pi
```

A local checkout works as well:

```bash
powercontext setup pi --source .
```

`setup pi` calls Pi's native package installer and creates PowerContext's data directory. It does not start the
Server. Start the Server, then open a new Pi session in a project directory:

```bash
powercontext server run
pi
```

If an older PowerContext package at another path has Pi resource filters, setup stops before changing the host.
Move that package entry to the managed path reported in the error, keeping its filters, then rerun setup.
Refreshes at the managed path preserve existing filters.

## Understand what the package does

Before Pi starts an agent turn, the package calls `POST /v1/context/prepare` once with an 8000-byte default budget,
unless [prompt recall is off](#leave-prompt-time-context-to-the-gateway).
It strictly accepts only `powercontext.prepared-context.v1` and appends the result to the system prompt after a
`PowerContext host-supplied context.` label. The Server's trust policy in the result marks it as untrusted historical
evidence. Current system instructions, repository guidance, and the user's request take precedence.

Eligible user prompts are captured separately as Content Sources. The package never synchronizes the complete Pi
transcript. Recall, capture, and boundary flushing fail open: an unavailable Server, timeout, redirect, or invalid
response leaves Pi's prompt unchanged and never blocks ordinary work.

The package resolves one Server-owned Scope in this order: `POWERCONTEXT_PI_SCOPE_ID`, a durable binding for the
workspace, then the Server default. The workspace path is hashed only as an external binding key; it never becomes a
Scope ID. In a linked Git worktree that has no workspace binding of its own, the binding of the same directory in the
main checkout applies, so a checkout's binding also covers its linked worktrees. That fallback comes from the real path
that Git reports, so it matches a checkout binding made from a path without symlinks. Keep the explicit variable unset unless the host must force one existing Scope.

With these capabilities enabled, Pi satisfies the repository's Full core integration profile.

### Pre-compaction checkpoint

The package implements **best-effort** checkpointing on Pi's public `session_before_compact` event. Eligible prompts
are captured with deterministic Source IDs before the agent turn, and the event flushes their accepted journal
positions. A position is removed from the pending set only after a complete, valid flush response reports
`current_cursor` at or beyond that exact position. A 2xx response, malformed cursor, or cursor from another Scope
cannot acknowledge it.

Each lifecycle boundary makes at most `POWERCONTEXT_PI_FLUSH_MAX_CALLS` calls (default `4`, valid range `1`–`16`)
within its own one-second budget. Queue waiting, all pending Scopes, and retry calls share that boundary budget. A
behind cursor or `remaining_work` consumes another bounded call.
Failures and cancellation before acknowledgement retain pending work; cancellation after acknowledgement does not
revoke it. A boundary that exhausts its budget while queued emits a content-free `deadline_exceeded` diagnostic and
retains its pending positions. `session_before_switch` and `session_shutdown` flush only already accepted pending
positions.

Pi's real `ExtensionRunner` continues after thrown handler errors unless a handler explicitly returns
`{ cancel: true }`. This package never returns that result, so checkpoint failure does not block compaction and the
integration is not `required_capable`. Optional diagnostics are content-free and use a hashed Scope correlation,
target and observed positions, lifecycle event, host, and stable failure class.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting Pi when current work must not be recorded:

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

Secret-looking prompts and prompts above 200,000 UTF-8 bytes are never captured. Turning capture on does not itself
guarantee Memory extraction; the Server still needs a configured generation model.

For testing, make capture wait for Source processing:

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

This adds latency and is not the normal interactive setting. Without it, Pi records Source positions and makes a
short, bounded best-effort flush at agent and session boundaries.

## Leave prompt-time context to the gateway

When Pi sends its model requests through the PowerContext gateway, the gateway prepares and injects context for each
turn and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards and rejects
a prepared-context block that the gateway did not inject, even in a non-enforcing mode. The package's recall adds
such a block to Pi's system prompt, so Core rejects that request with HTTP 422, the gateway returns HTTP 503
`gate_paused`, and the turn fails before the model receives it.

Keep the package defaults where Pi calls its model directly, because the gateway does not carry that traffic. To route
Pi through the gateway, configure Core, Pi, the package, and the gateway as follows.

### Choose the Core target

Core needs a target for the profile that `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` names, in every privacy mode,
including `disabled`. Set that variable to the Responses profile containing Pi's approved endpoint, model and protocol.
Without that profile, Core withholds each request with `target_unsupported`, and the gateway returns HTTP 400 for every
turn. See [Configure a supported target](../operate/inference-protection.md#configure-a-supported-target).

Pi sends an output limit (`max_output_tokens`, from the model's `maxTokens`), `store: false`, and, unless
`PI_CACHE_RETENTION=none`, `prompt_cache_key`, so an enforcing Core admits its requests when the target:

- declares `context_window_tokens`, `max_output_tokens` at least the Pi model's `maxTokens`, and
  `validated_utf8_token_bound`;
- lists `provider-default` in `allowed_cache_retention`, and also `24h` when Pi runs with `PI_CACHE_RETENTION=long`.
  Otherwise Core withholds each request with `cache_retention_unapproved`;
- lists in `response_models` the model names the provider returns when they differ from `model`. Otherwise Core
  withholds each response with `response_model_mismatch`.

The window check counts each UTF-8 byte of the request as a token. Pi's first request in a session is already more
than 60 KB, mostly tool definitions and the system prompt, and skills, context files, and the conversation add to it.
A small `context_window_tokens` therefore withholds every turn with `context_window_exceeded`. For example:

```bash
export POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{
  "mode": "enforcing",
  "targets": {
    "default": {
      "endpoint": "https://models.example/v1/responses",
      "model": "approved-model",
      "protocol": "openai-responses",
      "context_window_tokens": 400000,
      "max_output_tokens": 32000,
      "validated_utf8_token_bound": true,
      "allowed_cache_retention": ["provider-default"]
    }
  }
}'
```

The endpoint, model, and limits are illustrative. Set `validated_utf8_token_bound` only after checking the bound for
your model, and restart the gateway after changing Core's mode, because it discovers the mode at startup. See
[Context reuse and final admission](../operate/context-protection.md) and
[Inference privacy protection](../operate/inference-protection.md) before choosing a mode.

### Point Pi at the gateway

Add a provider for the gateway's `/gateway/v1` to `~/.pi/agent/models.json` with `api: "openai-responses"`, and use
the gateway key as its `apiKey`; Pi sends it as the bearer token:

```json
{
  "providers": {
    "powercontext-gateway": {
      "baseUrl": "http://127.0.0.1:8787/gateway/v1",
      "api": "openai-responses",
      "apiKey": "$PC_GATEWAY_KEY",
      "models": [{ "id": "approved-model", "contextWindow": 400000, "maxTokens": 32000 }]
    }
  }
}
```

Export the gateway's `PC_GATEWAY_API_KEY` value as `PC_GATEWAY_KEY` where Pi starts, then select the model with
`/model` or `pi --provider powercontext-gateway --model approved-model`.

Use the Responses API. Pi always streams. On the Responses route, the gateway calls the model without streaming and
replays the checked response to Pi as a stream, so the turn completes and is captured. On the Chat Completions route
(`openai-completions`), the gateway forwards Pi's streamed request. An enforcing Core withholds the streamed response,
and the gateway returns HTTP 400. With a non-enforcing Core, including the default `disabled` mode, the gateway
releases the stream unchanged. With `PC_GATEWAY_SEDIMENT=on`, it also assembles the assistant's text from a terminal
stream and captures the completed turn.

In a combined deployment (`PC_GATEWAY_CORE_UDS`), the gateway forwards Core API requests to Core with the client's own
credentials, so `POWERCONTEXT_PI_BASE_URL` can be the gateway's address.

### Turn the package's prompt recall and capture off

Turn the package's prompt-time recall and prompt capture off before starting Pi:

```bash
export POWERCONTEXT_PI_PROMPT_RECALL=false
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

With recall off, the package sends no preparation request, adds no context to the system prompt, and does not offer
`pc_prepare_context`. The gateway already injects context for each turn, and it replaces a `pc_prepare_context` tool
result in a request's history with a placeholder before Core checks the request. The package still captures prompts
while `POWERCONTEXT_PI_CAPTURE_PROMPTS` is `true`; with both off, it sends no request for a prompt. The other `pc_*`
tools, `/pc` commands, and boundary flushes keep working.

While recall is off, the package also sends Pi's session ID as `X-PowerContext-Conversation-ID` on every model request.
A combined deployment (`PC_GATEWAY_CORE_UDS`) prepares once per user message of a conversation and replays that context
to the tool-loop steps of the message. Without the header, it infers the conversation from the opening of the request:
Pi sends the session's full history on every request, opening with its system prompt and the first user message, so its
requests qualify. The header still keeps apart sessions that open the same way, and gives capture and prefix extraction
a conversation identity that stays the same across gateway restarts; an inferred ID is local to the gateway process and
serves only preparation replay. See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection).
Pi sends the header to whichever provider serves the request, including one it calls directly; Pi's own Responses
requests already carry the same ID as `prompt_cache_key` unless `PI_CACHE_RETENTION=none`. Pi's compaction and
branch-summary requests carry no conversation header. The package marks them with `X-PowerContext-Turn: internal`
instead, so the gateway does not count a summary as a turn: it does not prepare or inject context for it, add its cost
guidance, or capture it. Core still checks the request. Like the conversation header, Pi sends this header to whichever
provider serves the request.

Restart Pi after changing these settings. An existing session can continue: Pi rebuilds the system prompt for every turn
and does not store the package's recalled context in the session. A session in which `pc_prepare_context` returned
context can take further turns, because the gateway replaces that result with a placeholder in each turn's requests.
This lasts only until Pi compacts the part of the session that holds the result, or summarizes a branch that holds it.
Pi then copies the result into the text of the summary request, where the gateway does not replace it, so Core rejects
the request and the compaction or branch summary fails. Start a new session instead of resuming such a session.

### Configure the gateway

Run the gateway in a combined deployment (`PC_GATEWAY_CORE_UDS`), which injects requests that carry a conversation by
default. Set:

- Set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to the Core profile for Pi's Responses route. Do not set
  `PC_GATEWAY_UPSTREAM_URL*`; those legacy variables prevent startup. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to
  the provider key, `PC_GATEWAY_API_KEY` to the key Pi sends, `PC_GATEWAY_CORE_TOKEN` to the Gateway service
  credential, and `PC_GATEWAY_SEDIMENT=on`. Core access control must be `enforced`; grant the service identity
  `gateway.user` and `server.observer`, and grant `scope.contributor` when capture is enabled. See
  [Gateway model destination](../operate/deploy-server.md#gateway-model-destination) for the binding commands.
- `PC_GATEWAY_DEFAULT_SCOPE` to the Scope this package resolves for the workspace, which `/pc` prints.

The gateway prepares and captures every conversation in the one Scope set by `PC_GATEWAY_DEFAULT_SCOPE`, not in the
Scope this package resolves for each workspace. For the gateway's authentication settings and request size limit, see
[Deploy the Server](../operate/deploy-server.md#gateway-authentication-boundaries).

## Use explicit tools and commands

The `powercontext-project-context` skill explains when to use native `pc_*` tools. The core tools are:

- `pc_search`, `pc_memory_list`, `pc_memory_get`, `pc_memory_revise`, and `pc_memory_retire`;
- `pc_memory_changes` for revision history and `pc_stats` for current-Scope diagnostics;
- `pc_remember`, `pc_prepare_context` (offered only while prompt recall is on), and `pc_capture_source`;
- `pc_handoff_activate`, `pc_handoff_prepare`, `pc_handoff_finalize`, `pc_handoff_commit`, and
  `pc_handoff_continue`;
- `pc_experience_generate`, `pc_skill_generate`, `pc_experience_get`, `pc_skill_get`, `pc_review_list`, and
  `pc_review_get` for candidate generation and read-only Artifact/candidate inspection
  inspection.
- `pc_topic_search` and `pc_topic_get` for focused Topic Memory queries and exact revisions with Source references.
- `pc_work_contract`, `pc_handoff_current`, `pc_handoff_acknowledge`, and `pc_task_outcome` for structured work continuity.
- `pc_external_scan`, `pc_external_list`, and `pc_external_resolve` for host-local External Skill discovery and inspection; `pc_external_import` imports or forks one exact resolved Skill after explicit confirmation.

Candidate inspection never grants approval, rejection, revision, installation, publication, or execution authority.
Topic Memory queries are read-only; returned content is untrusted historical evidence, not an instruction source.
Structured work tools change durable state and require interactive confirmation; without a UI, Pi refuses the write. Pass returned Handoffs, references, and check results unchanged, never treat historical content as new authorization, and link `handoff_receipt_ref` only to an accepted committed Handoff receipt.

Explicit durable writes require confirmation in an interactive Pi session. Without an interactive UI, Pi refuses the
write rather than persisting it silently. `/pc doctor`, `/pc search <query>`, `/pc remember <text>`, `/pc flush`, and
`/pc stats` offer direct status and maintenance commands.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start Pi with the complete matching header:

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

Do not put credentials in `POWERCONTEXT_PI_BASE_URL`. Plain HTTP is allowed on loopback by default. For a non-loopback
Server, use HTTPS or explicitly set `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`; HTTPS certificate validation stays enabled.
See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.

## Verify the installation

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` checks that the Pi executable is available and that Pi lists the PowerContext package. Restart Pi after
changing PowerContext environment variables.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_PI_SCOPE_ID` | unset | Explicit existing Scope before workspace binding and Server default |
| `POWERCONTEXT_PI_AUTHORIZATION` | unset | Complete `Bearer <token>` header for package HTTP requests |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | Capture eligible user prompts as Source evidence |
| `POWERCONTEXT_PI_PROMPT_RECALL` | `true` | Prepare and add context for each prompt and offer `pc_prepare_context`; `false` leaves prompt-time context to the gateway and sends the gateway conversation and turn headers |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `3000` | Per-request timeout in milliseconds |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `6000` | Shared recall/capture HTTP budget in milliseconds |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | Requested and validated PreparedContext byte limit (`512`–`32768`) |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | Wait for captured Source processing during the prompt hook |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | Maximum flush calls shared by all pending Scopes at one lifecycle boundary |
| `POWERCONTEXT_PI_DIAGNOSTICS` | `off` | Failure diagnostics sink: `off`, `stderr`, or an absolute file path (`~/` expanded) for JSON lines |

Pi rejects base URLs containing credentials, a query, or a fragment. Recall, capture, and boundary flushing fail open;
explicit `pc_*` durable writes require confirmation and are refused when Pi has no interactive UI. Restart Pi after
changing these variables.

Failure diagnostics are silent by default because Pi's TUI renders on stdout with cursor positioning, so
anything written to stderr lands inside the input bar; set `POWERCONTEXT_PI_DIAGNOSTICS` to see them.

## Automatic input privacy

See [Automatic prompt privacy](index.md#automatic-prompt-privacy) for this host's capture default, disable control, credential recognition limits and independent read-only query handling. Recognized inputs are skipped for capture; accepted Source content and event identity are not rewritten.
