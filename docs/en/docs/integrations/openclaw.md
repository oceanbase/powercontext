---
title: OpenClaw
description: Install the PowerContext memory plugin for OpenClaw and control recall, capture, scope, and durable memory writes.
status: community
---

# OpenClaw

`community`

## Install or refresh the plugin

Install the CLI and plugin from the same `master` revision:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup openclaw
```

Without an explicit URL, setup resolves environment and saved client settings; otherwise it uses `http://127.0.0.1:8000`.

A local checkout works as well:

```bash
powercontext setup openclaw --source .
```

`setup openclaw` builds the plugin with pnpm, packs it with `pnpm pack`, and installs the archive with
`openclaw plugins install --force --no-enable <archive>`. It then adds only the activation settings that are absent:
it enables the plugin, selects it for the `memory` plugin slot, turns on `autoRecall`, `autoCapture` and
conversation access, adds the PowerContext tools to `tools.alsoAllow` on first activation, and restarts the OpenClaw
gateway. If you have disabled or denied the plugin, left it out of a non-empty `plugins.allow`, or selected another
memory plugin, setup leaves it inactive. It does not start the Server. Start the Server, then start a new OpenClaw
session:

```bash
powercontext server run
openclaw
```

Setup and the plugin require OpenClaw 2026.8.1-beta.2 or newer. Releases whose `plugins install` has no
`--no-enable`, such as 2026.8.1, enable a plugin while installing it. On those releases setup runs
`openclaw plugins install --force --accept-capabilities <archive>` (`--accept-capabilities` only when the release
lists it) and first checks that installation would not override your choices. It stops without changing anything if
`plugins.deny` lists the plugin, `plugins.allow` is non-empty and omits it, or `plugins.slots.memory` selects another
plugin while neither this plugin nor all plugins are disabled. On those releases the plugin is active as soon as
installation finishes, before setup writes its endpoint; if a later setup step fails, fix the cause and rerun setup.

## Data flow and capture controls

The settings below belong to `plugins.entries.memory-powercontext.config`. `autoRecall` and `autoCapture`
both default to `true` and operate independently; restart the gateway after configuration changes.

| Event/entry | Data sent | Direct recipient | Default/trigger | Disable | Effect when disabled |
| --- | --- | --- | --- | --- | --- |
| `before_prompt_build` | Latest user text (or prompt fallback) with recognized credentials replaced by `[REDACTED]`, then trimmed; when longer than 8192 characters or 8192 UTF-8 bytes, cut to its most recent text within both bounds; plus Scope and prepare budget | Configured PowerContext Server; returned context enters the host model request | `autoRecall=true`, eligible private session | `autoRecall=false` | Automatic preparation/injection stops; capture and explicit tools remain independent |
| Successful `agent_end` | Latest user text prefixed with `User:`, deterministic Source ID and origin metadata; nothing when the text contains a recognized credential | Configured PowerContext Server | `autoCapture=true`, eligible private session | `autoCapture=false` | New automatic Sources stop; recall and explicit writes remain available |
| `before_compaction` | Same bounded latest-user Source when capture is enabled; Scope for bounded flush | Configured PowerContext Server | Best-effort boundary event | `autoCapture=false` disables new capture, not pending flush | Previously accepted positions can still be flushed; compaction is not blocked |
| `session_end` / `gateway_stop` | Scope for flushing already accepted pending positions; no new transcript | Configured PowerContext Server | Pending positions and available extraction | No separate flush-off setting | Turning off capture prevents new Sources, but does not cancel existing pending work |
| Explicit search/get/store/revise/retire | Requested query, reference, or mutation payload | Configured PowerContext Server | Explicit tool execution subject to host controls and access checks | Do not invoke, or restrict the tools in OpenClaw | Automatic recall/capture switches do not disable explicit tools |

Automatic capture excludes group, channel and incognito sessions. `captureMaxChars` defaults to `4000`
(range `128`–`20000`): the latest user text including the `User: ` prefix is **truncated**, not rejected,
using JavaScript string length/slicing. This is not a UTF-8 byte limit or complete transcript synchronization.
Automatic recall and capture follow the [automatic prompt privacy](index.md#automatic-prompt-privacy) policy.
Recognized credentials are credential assignments such as `api_key=…` or `password: …`, `Authorization` and
`Cookie` headers, bearer and provider tokens, private keys, and the token in the configured `tokenEnv` variable.
The recall query replaces them with `[REDACTED]` before it is bounded, and a latest user text that contains any of
them is not captured; the check reads the whole text before `captureMaxChars` truncation. Ordinary words that only
contain a marker, such as `token budgets`, are not credentials. The rule recognizes common shapes, not every secret.
Session eligibility and truncation are not secret filtering. To stop these automatic paths from sending prompt text
to the Server, set both `autoCapture=false` and `autoRecall=false`, or exclude the session. Explicit tools remain
independent and can still send their arguments.

There are three separate data directions: OpenClaw sends queries, Sources and explicit operations to the configured
Server; enabled Server embedding/reranking/generation may send queries, candidates or evidence to its configured
inference backend; returned historical context enters the model selected by OpenClaw when injected. A loopback
Server only describes the first connection, not the absence of later network calls. Third-party plugin code does
not by itself mean a third-party network service receives the data. See [configuration](../operate/configuration.md),
[inference protection](../operate/inference-protection.md) and [context admission](../operate/context-protection.md).
Protection defaults to disabled; shadow/advisory do not prevent disclosure. Enforcing protection requires supported
targets and checked payloads and must not be assumed for every OpenClaw model request.

A Source acceptance is not completed Memory extraction. Disabling generation does not disable capture; accepted
Sources remain pending when extraction is unavailable. Changing switches does not erase existing Sources, Memory,
revisions, traces or provider-retained records. Preparation, checkpoint acknowledgement, host injection, actual model
use and answer correctness are distinct outcomes. Boundary acknowledgement is described below.

### Recall and explicit tools

When automatic recall is enabled, before OpenClaw builds a prompt the plugin calls `POST /v1/context/prepare` once with an 8000-byte default budget.
Recalled content is labelled as untrusted historical evidence. Current system instructions, repository guidance, and
the user's request take precedence.

Eligible user prompts from direct/private sessions are captured separately as Content Sources with a deterministic
source id, so repeated captures are idempotent. Group, channel, and incognito sessions are excluded. The plugin never
synchronizes the complete OpenClaw transcript. Recall, capture, and boundary flushing fail open: an unavailable
Server, timeout, redirect, or invalid response leaves the prompt unchanged and never blocks ordinary work.

The plugin exposes five memory tools: `powercontext_memory_search`, `powercontext_memory_get`,
`powercontext_memory_store`, `powercontext_memory_revise`, and `powercontext_memory_retire`. The mutating tools
require the model to call them explicitly; OpenClaw controls side-effecting tool execution.

### Pre-compaction checkpoint

The plugin implements **best-effort** pre-compaction checkpointing on OpenClaw's public `before_compaction` event.
It resolves the Scope once, captures the latest eligible user message with a deterministic Source ID, retains the
returned Source journal position, and flushes that same Scope. `agent_end` identity uses the public host `runId`.
Because `before_compaction` has no unique event ID, its less frequent path streams the complete JSON-shaped event
messages through a canonical object-key-ordered hash; semantic duplicate payloads therefore retain one identity while
later payloads remain distinct without constructing a transcript-sized intermediate string. A
checkpoint is acknowledged only by a complete, valid flush response whose `current_cursor` is at or beyond that
position. A 2xx response alone is not an acknowledgement.

If the cursor is behind or `remaining_work` is true, the plugin makes at most `flushMaxCalls` calls (default `4`,
valid range `1`–`16`) within a one-second boundary budget that starts when the flush is queued; `timeoutMs` (default 2500 ms) remains the per-request
ceiling. Accepted positions that are not acknowledged remain pending for Server scheduling or a later bounded flush
at `session_end` or `gateway_stop`. Closing work has priority over queued pre-compaction work, while queue waiting
counts against each event's boundary budget. Closing events flush only
already accepted positions and are not reported as pre-compaction events. A boundary that exhausts its budget while
queued emits a content-free `deadline_exceeded` diagnostic and retains its pending positions.

OpenClaw catches `before_compaction` hook failures and continues compaction, so this integration is not
`required_capable`. Failure diagnostics contain the host, lifecycle event, a hashed Scope correlation, target and
observed positions when known, and a stable failure class. They never include transcript content, prompts, queries,
credentials, or provider exceptions.

Explicit search uses `/v1/memory/search` and get reads the exact Atomic Memory Artifact revision; they do not call
`/v1/context/prepare`. Search limits the query to 8192 characters and clamps the requested result limit to 1–50
(default 10), while each get returns at most 120 lines and 12,000 characters.

## Choose the memory Scope

The plugin asks the Server to resolve one existing Scope before each operation. The Server checks, in order:

1. the plugin's explicit `scopeId`;
2. durable bindings for the OpenClaw session, each ordered active project, and the agent identity;
3. the Server's default Scope.

OpenClaw reports a run's active projects when it builds the prompt, but not at `agent_end` or `before_compaction`.
Capture at `agent_end` uses the projects of the run it captures, and a pre-compaction checkpoint uses those of the
session's most recent prompt, so both reach the project binding that the turn's recall read. Projects that the host
reports to either event itself take precedence.

Agent, project, path, and session identities are binding lookup inputs only. The plugin never derives a Scope ID from
them and never creates a Scope. To select one existing Scope explicitly for all OpenClaw operations:

```bash
openclaw config set plugins.entries.memory-powercontext.config.scopeId scp_0123456789abcdefghjkmnpqrs
openclaw gateway restart
```

Without `scopeId`, provision a durable Server binding only when an OpenClaw host identity must retain a selection;
otherwise the Server default is used. The plugin does not persist bindings because OpenClaw currently has no Scope
selection contract.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

The plugin reads the Bearer token from the environment variable named by the `tokenEnv` config entry, which defaults
to `POWERCONTEXT_CLIENT_API_TOKEN`. The Gateway service must receive that variable in its own environment. Add the
matching secret value to the Gateway service environment or to `~/.openclaw/.env`:

```dotenv
POWERCONTEXT_CLIENT_API_TOKEN=<same token value>
```

Protect the file and restart the Gateway so the plugin receives the updated environment:

```bash
chmod 600 ~/.openclaw/.env
openclaw gateway restart
```

Do not put credentials in the endpoint. The CLI and plugin reject non-loopback HTTP by default. Use HTTPS or explicitly
set `POWERCONTEXT_OPENCLAW_ALLOW_INSECURE_HTTP=true` (or the plugin's `allowInsecureHttp` setting). HTTPS certificate
validation stays enabled. See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.

## Leave prompt-time context to the gateway

This section is about the PowerContext gateway (`pc-gateway`), which sits between OpenClaw and its model provider. It is
not OpenClaw's own Gateway, the service that runs OpenClaw's agents and restarts with `openclaw gateway restart`.

When OpenClaw sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each model request and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. The plugin's recall adds such a block to each prompt, so turn recall off,
and turn capture off because the gateway captures the turns instead. Then restart OpenClaw's Gateway:

```bash
openclaw config set plugins.entries.memory-powercontext.config.autoRecall false --strict-json
openclaw config set plugins.entries.memory-powercontext.config.autoCapture false --strict-json
openclaw gateway restart
```

The memory tools keep working; none of them prepares context. With capture off, the plugin sends no Sources, so its
boundary flushes have nothing to flush; the Server's scheduled extraction processes the turns the gateway captures.
Start a new OpenClaw session after the change, because OpenClaw can keep the context a plugin added to an earlier turn
in the session transcript. Keep the defaults where OpenClaw calls its model directly, because the PowerContext gateway
does not carry that traffic.

Add a provider for the gateway's `/gateway/v1` with `api: "openai-responses"` to `~/.openclaw/openclaw.json`, use the
gateway's `PC_GATEWAY_API_KEY` value as its `apiKey`, and select its model, for example
`powercontext-gateway/approved-model`, as `agents.defaults.model.primary`:

```json5
{
  models: {
    providers: {
      "powercontext-gateway": {
        baseUrl: "http://127.0.0.1:8787/gateway/v1",
        apiKey: "${PC_GATEWAY_KEY}",
        api: "openai-responses",
        models: [
          { id: "approved-model", name: "Approved model", contextWindow: 400000, maxTokens: 32000 },
        ],
      },
    },
  },
}
```

`PC_GATEWAY_KEY` must reach OpenClaw's Gateway service, for example through `~/.openclaw/.env`. On the Responses route
the gateway calls the model without streaming and replays the checked response as a stream, so a streamed turn completes
and is captured. With `api: "openai-completions"`, the gateway forwards a streamed request on the Chat Completions route
and releases the stream unchanged; with `PC_GATEWAY_SEDIMENT=on`, it captures the completed turn from it.

Configure Core and the gateway as well:

- Run Core in a non-enforcing privacy mode (`disabled`, `shadow`, or `advisory`). OpenClaw sends no `store` field to a
  custom provider, so an enforcing Core withholds each of its Responses requests and the gateway returns HTTP 400.
  Restart the gateway after changing the mode, because it discovers the mode at startup.
- For this Responses route, set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to a Core target whose endpoint,
  `openai-responses` protocol and model match the request. Without it, the route returns HTTP 501
  `responses_route_disabled`. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to the provider key and
  `PC_GATEWAY_SEDIMENT=on`. Do not set `PC_GATEWAY_UPSTREAM_URL*`; these legacy variables prevent startup.
- Set a nonempty `PC_GATEWAY_API_KEY` even on loopback, and set `PC_GATEWAY_CORE_TOKEN` to an authenticated service
  identity. Core access control must be `enforced`; grant it `gateway.user` on the selected Scope and `server.observer`
  on the Server resource for `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` (default `powercontext`). Because this guide
  enables capture, grant `scope.contributor` (which includes `scope.read`) rather than `scope.viewer`.
  See [Gateway authentication and target requirements](../operate/deploy-server.md#gateway-model-destination).
- The gateway prepares and captures every conversation in the one Scope that `PC_GATEWAY_DEFAULT_SCOPE` names, not in
  the Scope this plugin resolves for each session or project. Set the plugin's `scopeId` and `PC_GATEWAY_DEFAULT_SCOPE`
  to the same existing Scope.
- OpenClaw 2026.8.1 sends its session headers only to OpenAI's own endpoints, and a provider's `headers` are the same
  for every session, so no OpenClaw request names a conversation. A combined deployment (`PC_GATEWAY_CORE_UDS`) infers
  it instead: on a custom provider OpenClaw sends the session's full history on every request, opening with its system
  prompt and the first user message, so the gateway prepares once per user message and replays that context to the
  tool-loop steps. Sessions that open with the same system prompt and first message share an inferred conversation. A
  standalone gateway (`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. Do not put a fixed
  `X-PowerContext-Conversation-ID` in the provider's `headers`, because the gateway would then treat every session as
  one conversation.

See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection) and [Gateway authentication
boundaries](../operate/deploy-server.md#gateway-authentication-boundaries).

## Verify the installation

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` checks that the OpenClaw CLI is available and that `openclaw plugins list --enabled --json` reports
`memory-powercontext` as loaded and selected for the memory slot. Restart the OpenClaw gateway after changing
PowerContext configuration.
