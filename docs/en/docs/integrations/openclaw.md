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

`setup openclaw` builds the plugin with pnpm, installs it with `openclaw plugins install --link --force`, enables it
as the `memory` plugin slot, adds the PowerContext tools to `tools.alsoAllow`, and restarts the OpenClaw gateway. It
does not start the Server. Start the Server, then start a new OpenClaw session:

```bash
powercontext server run
openclaw
```

The plugin requires OpenClaw 2026.8.1-beta.2 or newer.

## Data flow and capture controls

The settings below belong to `plugins.entries.memory-powercontext.config`. `autoRecall` and `autoCapture`
both default to `true` and operate independently; restart the gateway after configuration changes.

| Event/entry | Data sent | Direct recipient | Default/trigger | Disable | Effect when disabled |
| --- | --- | --- | --- | --- | --- |
| `before_prompt_build` | Latest user text (or prompt fallback), bounded to 8192 UTF-8 bytes without credential scrubbing, plus Scope and prepare budget | Configured PowerContext Server; returned context enters the host model request | `autoRecall=true`, eligible private session | `autoRecall=false` | Automatic preparation/injection stops; capture and explicit tools remain independent |
| Successful `agent_end` | Latest user text prefixed with `User:`, deterministic Source ID and origin metadata | Configured PowerContext Server | `autoCapture=true`, eligible private session | `autoCapture=false` | New automatic Sources stop; recall and explicit writes remain available |
| `before_compaction` | Same bounded latest-user Source when capture is enabled; Scope for bounded flush | Configured PowerContext Server | Best-effort boundary event | `autoCapture=false` disables new capture, not pending flush | Previously accepted positions can still be flushed; compaction is not blocked |
| `session_end` / `gateway_stop` | Scope for flushing already accepted pending positions; no new transcript | Configured PowerContext Server | Pending positions and available extraction | No separate flush-off setting | Turning off capture prevents new Sources, but does not cancel existing pending work |
| Explicit search/get/store/revise/retire | Requested query, reference, or mutation payload | Configured PowerContext Server | Explicit tool execution subject to host controls and access checks | Do not invoke, or restrict the tools in OpenClaw | Automatic recall/capture switches do not disable explicit tools |

Automatic capture excludes group, channel and incognito sessions. `captureMaxChars` defaults to `4000`
(range `128`–`20000`): the latest user text including the `User: ` prefix is **truncated**, not rejected,
using JavaScript string length/slicing. This is not a UTF-8 byte limit or complete transcript synchronization.
Neither automatic recall nor capture performs credential detection or scrubbing, and the plugin has no secret-detection
configuration. Session eligibility and truncation are not secret filtering. To stop these automatic paths from sending
prompt text to the Server, set both `autoCapture=false` and `autoRecall=false`, or exclude the session. Explicit tools
remain independent and can still send their arguments.

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

## Verify the installation

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` checks that the OpenClaw CLI is available and that `openclaw plugins list --enabled --json` reports
`memory-powercontext` as loaded and selected for the memory slot. Restart the OpenClaw gateway after changing
PowerContext configuration.
