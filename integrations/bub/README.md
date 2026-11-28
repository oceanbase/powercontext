# PowerContext for Bub

`evaluation`

This package connects Bub to a running PowerContext Server through the public Python client. It adds three tools:

- `powercontext.remember` stores one durable decision, preference, constraint, or procedure.
- `powercontext.search` searches durable memory.
- `powercontext.context` prepares bounded context for a question; it is not offered while prompt recall is off.

Before each model call, the plugin also prepares relevant context and adds it as host-supplied historical evidence,
unless [prompt-time context is left to the gateway](#leave-prompt-time-context-to-the-gateway).
Automatic trajectory capture is opt-in. When enabled, the plugin captures the initial task and completed LLM and tool
events as bounded Content Sources. It periodically flushes those Sources through the Memory pipeline so later model
steps in the same Bub run can recall earlier findings. Provider-hidden reasoning is never available to the hook and is
not captured.

## Configuration

The plugin uses Bub's Pydantic settings extension. Configuration can live in the `powercontext` section of Bub's
configuration file:

```yaml
powercontext:
  base_url: http://127.0.0.1:8000
  capture_events: true
  capture_checkpoint_every: 5
```

Environment variables use the `POWERCONTEXT_BUB_` prefix and take precedence over file values. Values are parsed and
validated by Pydantic before the plugin starts.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_BUB_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_BUB_API_TOKEN` | unset | Bearer token for a Server that requires authentication |
| `POWERCONTEXT_BUB_ALLOW_INSECURE_HTTP` | `false` | Explicitly allow non-loopback HTTP for recall, capture, and tools; HTTPS certificate validation stays enabled |
| `POWERCONTEXT_BUB_SCOPE_ID` | unset | Explicit Scope resolved and validated by the Server before use |
| `POWERCONTEXT_BUB_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_BUB_MAX_BYTES` | `8000` | Maximum prepared-context size |
| `POWERCONTEXT_BUB_CONTEXT_ASSEMBLY` | unset | JSON object selecting prepared-context families and output options |
| `POWERCONTEXT_BUB_PROMPT_RECALL` | `true` | Prepare and inject context before each model call and offer `powercontext.context`; `false` [leaves prompt-time context to the gateway](#leave-prompt-time-context-to-the-gateway) |
| `POWERCONTEXT_BUB_CAPTURE_EVENTS` | `false` | Capture completed Bub events as Content Sources |
| `POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY` | `5` | Flush Memory after this many captured events |
| `POWERCONTEXT_BUB_CAPTURE_MAX_BYTES` | `8192` | Maximum UTF-8 bytes stored for one captured event |
| `POWERCONTEXT_BUB_CAPTURE_LOG` | unset | Optional JSONL evidence path; records metadata but not event content |
| `POWERCONTEXT_BUB_TRUST_TRANSPORT_SECURITY` | `false` | Vouch for a plaintext non-loopback `BASE_URL` on an operator-controlled network (for example a private Compose bridge); otherwise such URLs are refused |

`PowerContextSettings(allow_insecure_http=True)` also permits non-loopback HTTP. Transport settings resolve from
constructor values, host environment, common `POWERCONTEXT_CLIENT_SERVER_URL` /
`POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP`, then the `bub` entry in `~/.config/powercontext/clients.json`
(overridden by `POWERCONTEXT_CLIENT_CONFIG_FILE`). A host value of `false` overrides common `true`.
Saved consent applies only to its saved Server URL and is not reused when the URL changes.

The plugin never derives a Scope ID from the current directory, Git metadata, or a workspace path. It asks the Server
to resolve the configured explicit Scope first, then an opaque stable binding key derived from Bub's workspace identity,
then, in a linked Git worktree, the key of the same directory in the main checkout, and finally the Server default. A
checkout's binding therefore also covers its linked worktrees until a worktree has a binding of its own. Only the real Scope returned by the Server is cached and used for recall, capture, and
tools.

The recall query is the user's text, trimmed, with recognized credentials, such as `api_key=…` assignments, Bearer
values, and common provider tokens, replaced by `[REDACTED]`. When a prompt longer than 8,192 characters or 8,192
UTF-8 bytes is cut to its most recent text, the replacement happens first, so the cut cannot leave a bare credential.
With capture enabled, an initial task that contains a recognized credential is not captured; the capture log records
a `user_prompt` event with status `skipped` and reason `sensitive_content`, without the text. Bub applies the same
recognition as the other automatic PowerContext integrations, described in the
[automatic prompt privacy](../../docs/en/docs/integrations/index.md#automatic-prompt-privacy) guide. Recognition is
best effort.

Captured tool arguments redact values under credential-like keys. Known credential environment values are also
removed from serialized event content. Keep the PowerContext scope and optional capture log protected because normal
tool output can still contain sensitive project data.

Install the package together with PowerContext and Bub:

```bash
uv pip install -e . -e integrations/bub
```

Legacy context retrieval remains available with `powercontext==0.2.0` when `context_assembly` is unset.
Text assembly requires a core and Server that support it; install the core and plugin together from the same checkout
using the command above. On an older core, configuring assembly raises an explicit upgrade message at startup.

## Leave prompt-time context to the gateway

When Bub sends its model requests through the PowerContext gateway, the gateway prepares and injects context for each
model request and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. The plugin adds such a block before each model call, so turn recall off.
Leave event capture off as well, because the gateway captures the turns instead. Keep the defaults where Bub calls its
model directly, because the gateway does not carry that traffic.

### Turn the plugin's prompt recall off

Set the switch where Bub starts, or store `prompt_recall: false` in the `powercontext` section of Bub's configuration
file, and leave `POWERCONTEXT_BUB_CAPTURE_EVENTS` unset or `false`:

```bash
export POWERCONTEXT_BUB_PROMPT_RECALL=false
```

With recall off, the plugin sends no preparation request and adds no context to a model call. It also does not offer
`powercontext.context`, because Bub keeps a tool's result in the session's tape and replays it with every later model
call, and the gateway does not remove the plugin's prepared context from a tool result; Core would reject every later
request of that session. A call to it is answered as an unknown tool. `powercontext.search` and `powercontext.remember`
keep working. With event capture off, the plugin stores no events and sends no flushes. The turns the gateway
captures become Memory through the Server's scheduled extraction, which needs both a generation model and
`POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS`; neither is set by default. See
[Server configuration](../../docs/en/docs/operate/configuration.md#server).

The context the plugin adds before a model call is not stored in the tape, so an existing session can continue behind
the gateway. Start a new session (a new `--session-id`), or reset the tape with Bub's `tape.reset` command, only if the
session already called `powercontext.context`.

### Point Bub at the gateway

Use the `openai` provider with the gateway's `/gateway/v1` as its API base, and the gateway's `PC_GATEWAY_API_KEY` value
as Bub's API key:

```bash
export BUB_MODEL=openai:approved-model
export BUB_API_BASE=http://127.0.0.1:8787/gateway/v1
export BUB_API_KEY="$PC_GATEWAY_API_KEY"
```

The `openai` provider uses the Responses route, and Bub streams every model call. On the Responses route the gateway
calls the model without streaming and replays the checked response as a stream, so a streamed turn completes and is
captured. Do not set `"api_format": "chat"` in `BUB_CLIENT_ARGS` or use the `openai-compatible` provider, which speak
Chat Completions: the gateway forwards a streamed request on that route. An enforcing Core withholds streamed Chat
Completions responses, and the gateway returns HTTP 400. With a non-enforcing Core, including the default `disabled`
mode, the gateway releases the stream unchanged. With `PC_GATEWAY_SEDIMENT=on`, it also assembles the assistant's
text from a terminal stream and captures the completed turn.

### Choose the Core target

Core needs a target for the profile that `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` names, in every privacy mode,
including `disabled`. Set that variable to the Responses profile containing Bub's approved endpoint, model and protocol.
Without that profile, Core withholds each request with `target_unsupported`, and the gateway returns HTTP 400 for every
turn. See [Configure a supported target](../../docs/en/docs/operate/inference-protection.md#configure-a-supported-target).

Bub 0.6.1 sends `max_output_tokens` on every Responses request, set by `BUB_MAX_TOKENS` (16384 unless set). By default
it sends neither `store` nor `prompt_cache_key`. An enforcing Core admits its requests when:

- Bub sends `store: false`. Core withholds a Responses request without it with `provider_state_unsupported`, and the
  gateway returns HTTP 400 for every turn. Bub passes its completion arguments to every model call, and their
  `extra_body` is merged into the request body:

  ```bash
  export BUB_COMPLETION_ARGS='{"extra_body": {"store": false}}'
  ```

- the target declares `context_window_tokens`, `max_output_tokens` at least `BUB_MAX_TOKENS`, and
  `validated_utf8_token_bound`;
- the target lists in `response_models` the model names the provider returns when they differ from the model in
  `BUB_MODEL`. Otherwise Core withholds each response with `response_model_mismatch`.

The window check counts each UTF-8 byte of the request as a token, so leave room in `context_window_tokens` for Bub's
tool definitions, system prompt and session tape. Restart the gateway after changing Core's mode, because it discovers
the mode at startup. See [Context reuse and final admission](../../docs/en/docs/operate/context-protection.md) and
[Inference privacy protection](../../docs/en/docs/operate/inference-protection.md) before choosing a mode.

### Configure the gateway

- Set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to the Core profile for Bub's Responses route. Do not set
  `PC_GATEWAY_UPSTREAM_URL*`; those legacy variables prevent startup. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to
  the provider key, `PC_GATEWAY_API_KEY` to the key Bub sends, `PC_GATEWAY_CORE_TOKEN` to the Gateway service credential,
  and `PC_GATEWAY_SEDIMENT=on`. Core access control must be `enforced`; grant the service identity `gateway.user` and
  `server.observer`, and grant `scope.contributor` when capture is enabled. See
  [Gateway model destination](../../docs/en/docs/operate/deploy-server.md#gateway-model-destination) for the binding commands.
- The gateway prepares and captures every session in the one Scope that `PC_GATEWAY_DEFAULT_SCOPE` names, not in the
  Scope this plugin resolves for each workspace. Set `POWERCONTEXT_BUB_SCOPE_ID` and `PC_GATEWAY_DEFAULT_SCOPE` to the
  same existing Scope, so that `powercontext.search` and `powercontext.remember` use the Scope the gateway fills.
- Run the gateway in a combined deployment (`PC_GATEWAY_CORE_UDS`), which injects Bub's requests by default. Bub 0.6.1
  sends no per-session header, and headers in `BUB_CLIENT_ARGS` are the same for every session, so the gateway infers
  the conversation from the request instead. Bub sends the session's tape since its last anchor on every request,
  without `previous_response_id` or `conversation`. The input starts with the system prompt as a `system` message,
  followed by the first user message of that tape. The gateway therefore prepares once per user message and replays that
  context to the tool-loop steps.
- The inferred ID is local to the gateway process and serves only this replay. Sessions that open with the same
  system prompt and first user message share one; Bub's first message carries its channel and timestamp. The gateway
  still captures each completed turn. Without a conversation header, capture has no conversation identity that
  survives a gateway restart, and prefix extraction is not available. After `tape.handoff` or `tape.reset` the tape
  opens with a different message, so the gateway can infer a new conversation and prepare again.
- A standalone gateway (`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. Do not put a fixed
  `X-PowerContext-Conversation-ID` in `BUB_CLIENT_ARGS`, because the gateway would then treat every session as one
  conversation.

See [Gateway context injection](../../docs/en/docs/operate/deploy-server.md#gateway-context-injection) and
[Gateway authentication boundaries](../../docs/en/docs/operate/deploy-server.md#gateway-authentication-boundaries).
