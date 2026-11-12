---
title: Deploy the Server
description: Run PowerContext with persistent data, health checks, authentication, and a safe network boundary.
---

# Deploy the Server

For client address configuration and the `--allow-insecure-http` confirmation, see
[Connect to a remote Server](connect-remote-server.md). This is a client option; it does not change the Server listener
or authentication configuration.

Windows support is `experimental`.

`powercontext server run` is a foreground process. On a personal macOS, Linux, or Windows workstation, PowerContext can register
that same Server runner with the native current-user service manager. Managed deployments should continue to use a
container platform or an administrator-owned service manager.

## Run a persistent personal Server

Install and start the optional current-user service:

```bash
powercontext service install
powercontext service status
```

Linux uses `systemd --user` and writes logs to the user journal. macOS uses a per-user LaunchAgent, and Windows uses a current-user Task Scheduler task; both write stdout and stderr below the PowerContext user data directory. `service status` reports the exact log selector or path.

Personal services support loopback addresses only. Even with authentication enabled, setting
`POWERCONTEXT_SERVER_HTTP_HOST` to a non-loopback address makes `service install` reject the installation. To allow
access from another machine, use a container or an administrator-owned service manager, or put a same-host reverse proxy
in front of the loopback Server.

On Windows, the command asks whether to enable startup at the current user's next login when neither
`--start-on-login` nor `--no-start-on-login` is supplied; pressing Enter keeps login auto-start disabled. Use either
option for a non-interactive choice.

For an explicit Server configuration, protect the environment file before installing:

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
```

The successful installation summary prints the environment file actually used. If Bearer authentication is enabled,
read `POWERCONTEXT_SERVER_AUTH_TOKEN` from that file; the command never prints the token value. Authentication is
disabled by default, so no token is generated automatically.

On Windows, remove inherited access and grant the file only to the current user, `SYSTEM`, and local `Administrators` before validation, for example:

```powershell
icacls $env:USERPROFILE\powercontext.env /inheritance:r /grant:r "${env:USERNAME}:(F)" "SYSTEM:(F)" "Administrators:(F)"
```

This `icacls` command changes ACLs only; it does not change the file owner. If the owner is not the current user, fix the
owner first.

The native definition stores only the absolute file path and non-content file identity metadata. On Windows this
includes the current user's owner SID, which is revalidated whenever the launcher starts. It does not copy
credentials or the caller's shell environment. Re-run `service install` after upgrading PowerContext or changing the
environment file. After an explicit `service stop`, run `service install` from the new environment, passing the same
`--env-file` when one is configured. This updates the registration even if the old Python executable has been removed,
while keeping the service stopped and automatic activation suppressed. Run `service start` when it is safe to resume.
An unverified database migration continues to block registration updates.

Remove the registration without deleting Server data or logs with:

```bash
powercontext service uninstall
```

## Choose the network boundary

The default Server listens on `127.0.0.1:8000` without authentication. This is suitable for clients on the same
machine. Do not change the listener to a non-loopback address while authentication is disabled.

For access from another machine:

1. enable bearer authentication;
2. keep the Server behind a TLS-terminating reverse proxy or private network boundary;
3. provide the token through a secret manager or protected process environment;
4. allow access to the data directory only for the Server operator.

The built-in command serves HTTP and has no TLS options. Terminate HTTPS outside PowerContext.

## Run from an installed tool

Install PowerContext as described in [Install and run](../get-started/install-and-run.md), then choose a persistent data directory:

```bash
export POWERCONTEXT_HOME=/srv/powercontext
powercontext server run
```

The process must be able to create and update this directory. The default SQLite database and scheduler state are
stored below it. Supply the same environment variables whenever your service manager restarts the process.

`server run` loads `.env` from its current directory when present. Managed deployments should export the variables,
configure them in the service manager or container platform, or pass one explicit file so startup does not depend on
the working directory:

```bash
powercontext config validate --env-file /etc/powercontext/powercontext.env
powercontext server run --env-file /etc/powercontext/powercontext.env
```

The file may contain provider credentials or a bearer token, so restrict it to the Server operator. For `server run`,
process environment variables override same-named file values. `config init` creates a model-free base configuration; see
[Enable extraction and vector search](../get-started/configure-models.md)
when you need to add inference models and enable the full capability set. See [Configuration](configuration.md) for all
configuration parameters and their defaults.

Whether the Server runs in the foreground, in Docker, or as a personal service, startup or installation output warns
that missing models may affect some artifact features and links to the
[configuration reference](https://powercontext.oceanbase.io/en/docs/reference/configuration/).
The notice is omitted when both generation and embedding models are configured.

## Listen on a Unix domain socket

On Linux and macOS, a same-host proxy or gateway can connect to the Server over HTTP on a Unix domain socket (UDS).
Set `POWERCONTEXT_SERVER_HTTP_UDS` to select this listener:

```bash
export POWERCONTEXT_SERVER_HTTP_UDS="$HOME/.pc-run/core.sock"
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

Alternatively, pass `powercontext server run --uds /absolute/path/core.sock`. The CLI option overrides the environment
setting. With UDS selected, the Server opens no TCP listener and ignores configured `HTTP_HOST` and `HTTP_PORT` values.
Explicit `--host` or `--port` options are rejected when combined with an effective UDS setting. With UDS unset, the
existing TCP behavior applies. UDS supports the `all` and `api` roles; `api` still requires OceanBase. The `background`
role rejects UDS because its infrastructure listener uses TCP.

When a trusted same-user proxy terminates HTTPS and is the sole external entry to this private socket, set
`POWERCONTEXT_SERVER_HTTP_UDS_EXTERNAL_SCHEME=https` and preserve the browser-facing `Host` header. This fixed
operator declaration makes Dashboard Origin validation use HTTPS and issue Secure cookies. It applies to **every**
request on the socket, so do not use it for a proxy that also forwards plaintext external traffic. Unset it for direct
HTTP-over-UDS access. Only `http` and `https` are accepted; setting it without `HTTP_UDS` is rejected.
The Server still ignores `X-Forwarded-Proto` and `X-Forwarded-For`, even with `FORWARDED_ALLOW_IPS=*`, and does not
replace the native UDS peer with a loopback identity. `PUBLIC_URL` advertises an endpoint; it does not grant scheme trust.

Choose a short, private run directory:

- The socket path must be absolute, contain neither `..` nor NUL, and occupy at most 103 bytes in the filesystem
  encoding. Windows rejects UDS configuration without falling back to TCP.
- The Server creates a missing parent directory with mode `0700`. An existing immediate parent must be a real directory,
  owned by the Server's effective user, with no group or other access. A symlink parent is rejected. The socket has mode
  `0600`, so the gateway must run as the same user.
- A `core.sock.lock` file with mode `0600` beside the socket serializes startup and remains after shutdown. Graceful shutdown
  removes only the socket created by that process. After a crash, restart reclaims a socket only when its identity matches
  the lock record and a connection attempt confirms it is stale. Files, symlinks, live sockets, and unrecognized sockets
  are preserved and cause startup to fail. Keep the lock file in place for crash recovery.

Verify readiness and an authenticated API request from the same user:

```bash
curl --fail --unix-socket "$POWERCONTEXT_SERVER_HTTP_UDS" http://localhost/health/ready
curl --fail --unix-socket "$POWERCONTEXT_SERVER_HTTP_UDS" \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://localhost/v1/capabilities
```

For the Python Client, supply an HTTPX UDS transport:

```python
import asyncio
import os

import httpx

from powercontext.client import PowerContextClient


async def main():
    transport = httpx.AsyncHTTPTransport(uds=os.environ["POWERCONTEXT_SERVER_HTTP_UDS"], retries=0)
    async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
        client = PowerContextClient(
            "http://localhost",
            token=os.environ["POWERCONTEXT_DEPLOYMENT_TOKEN"],
            http_client=http,
            trust_transport_security=True,
        )
        print((await client.get_capabilities()).model_dump_json())


asyncio.run(main())
```

Here `http://localhost` is a routing label; the transport connects to the socket. The existing API paths, request and
response schemas, authentication, and authorization still apply. Socket permissions do not replace Bearer authentication.
`create_server_app()` creates an application; the CLI above owns the listener and its lifecycle.

Personal `service` commands, local `ops` management, and Desktop local management support TCP listeners only and reject
UDS configurations. The configuration wizard also produces TCP configurations. Agent host setup needs an explicit proxy
HTTP(S) URL when the Core uses UDS. Remote Skill Receiver endpoints retain their HTTPS or loopback-peer requirement;
direct UDS does not supply a loopback peer and does not waive that check.

Automatic Source deployment with `POWERCONTEXT_SERVER_SOURCE_PLUGINS_DIR` or
`POWERCONTEXT_SERVER_SOURCE_MANAGEMENT_AUTOSTART` requires `POWERCONTEXT_SERVER_PUBLIC_URL` pointing to the proxy
when the Server uses UDS. The collect manager and runtimes connect through that URL; missing configuration is rejected
before deployment credentials or processes are created. `source load --kind write` accepts the same setting or an explicit
`--server-url`. These commands do not infer a TCP endpoint from `HTTP_PORT` in UDS mode.

This listener provides the Core transport for the [Rust Gateway design (RFC #2199)](https://github.com/oceanbase/powercontext/pull/2199).
The Rust executable, joint launcher, gateway settings, private channel authentication, private routes, and gateway state
tables remain separate work. The design assigns the original public TCP port to the gateway, which calls the Core over
UDS with channel and user authentication.

### Gateway authentication boundaries

`PC_GATEWAY_API_KEY` applies only to `/gateway/v1/chat/completions` and `/gateway/v1/responses`.
A non-loopback Gateway listener requires a valid, nonempty key; that startup check does not authenticate other routes.
In a combined deployment (`PC_GATEWAY_CORE_UDS`), requests forwarded to Core retain the client's credentials.
Core API, MCP and Dashboard requests follow Core's existing authentication and access-control rules.
The Gateway key does not grant Core API access, and UDS does not replace Core authentication.

Before exposing a combined deployment, set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and configure valid Core
authentication credentials, as in the UDS example above. Clients of protected Core routes must use Core credentials.
`PC_GATEWAY_CORE_TOKEN` authenticates Gateway-initiated Core calls; the reverse proxy does not substitute it for
client credentials.

### Gateway context injection

When the gateway connects to the Core over UDS (`PC_GATEWAY_CORE_UDS`, the combined deployment), it injects prepared
context by default into Chat and Responses requests that belong to a conversation. Within a conversation it prepares
once per new user message and replays the exact result for the rest of that turn, such as an agent's tool loop.

The gateway identifies the conversation from the first of these request headers that is present:

| Header | Sent by | Conversation |
| --- | --- | --- |
| `X-PowerContext-Conversation-ID` | a host that integrates with PowerContext | the header value; overrides every other source |
| `x-claude-code-session-id`, with `x-claude-code-agent-id` | Claude Code | the session, separately for each subagent |
| `x-opencode-session-id` | OpenCode | the session; a child session has its own ID |
| `thread-id` (Responses route only) | Codex | the thread; a subagent has its own thread ID |

Headers that a client shares between a parent and its subagents, such as Codex `session-id` and `prompt_cache_key` or
OpenCode `x-opencode-session` and `x-session-affinity`, do not identify a conversation. Claude Code speaks the Anthropic
Messages API, which the gateway does not serve, so its headers apply only when a proxy in front of the gateway
translates its requests to the Chat or Responses route and forwards them. Set `PC_GATEWAY_INJECTION=on` to inject every request
(requests without a conversation ID then prepare individually), `PC_GATEWAY_INJECTION=off` to turn injection off, or
`PC_GATEWAY_INJECTION_RESPONSES=off` to keep only the Responses route uninjected. A standalone gateway
(`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. An empty or failed preparation sends the request
without injection.

Set `PC_GATEWAY_INJECTION_CODE=on` to include code evidence from the Scope's code index in the prepared context; it is
off by default and requires a configured repository for the Scope. When a replayed value no longer passes the check
because a cited source changed (for example, the agent edited a cited file), the gateway discards that conversation's
replay and prepares once more for the request instead of returning the withhold.

### Gateway instruction

`PC_GATEWAY_STEERING_FILE` names a file with a short, fixed instruction that the gateway adds to every request on the
Responses route (`/gateway/v1/responses`); the Chat route is never changed. The gateway reads the file once at startup.
It must be UTF-8, non-empty after trimming, at most 4,096 bytes, and free of PowerContext's reserved
`BEGIN_POWERCONTEXT_` markers; otherwise the gateway exits at startup instead of running without it. Nothing is added
when the variable is unset.

The text becomes the first content block of the developer message that follows the client's first developer message,
which is where Codex places its own `developer_instructions`. Without such a message, the gateway inserts a new developer
message after the client's first developer message, or before the first user message when the request has no developer
message. The text is identical on every request, so it stays part of the cached prefix. Core checks it as part of the
final request like any other request text. It is independent of context injection, capture, and replay.

### Gateway byte capacity

The Rust gateway discovers `context_protection` once at startup through `/v1/capabilities`.
Model routes require the complete `ContextProtectionCapabilities` DTO defined by OpenAPI:
`check_versions`, `payload_versions`, `reuse_versions`, `reuse_families`, `enforcing`, and `limits`
with `payload_bytes`, `binding_bytes`, `prepared_contexts`, `dependencies`, `send_seconds`, and
`correlate_seconds`. Missing or mistyped required fields pause admission even when the input capacity is present.
The check/payload versions must include v1, and `limits.payload_bytes` must be a positive integer.
A failed query, incompatible version, incomplete DTO, or invalid capacity pauses both model routes with HTTP 503;
`/healthz` and configured Core/MCP forwarding remain available. Restore the Core contract and restart
this gateway to rediscover capacity. Every model send and response release still requires its own
Core check. If Core lowers a limit before the gateway restarts, that check can reject the call;
increasing a Core limit does not enlarge this gateway's startup allowance until restart.
Startup diagnostics distinguish transport failure, query rejection, malformed/incomplete responses,
unsupported versions, and invalid capacity using fixed messages without Core error values.

`PC_GATEWAY_MAX_BODY_BYTES` defaults to 16 MiB (1 MiB = 1,048,576 bytes). The effective model input
limit is the minimum of that setting, the advertised input capacity, and the current 1 MiB complete-body
parser ceiling. A zero deployment limit disables model input with HTTP 503 and
`input_capacity_disabled`; it does not indicate a Core discovery failure.
Both Content-Length and consumed chunked request bytes are limited. The executor also
measures the final UTF-8 JSON body after prepared-context injection/replay, Responses steering and protocol
rendering. Initial-body overflow returns HTTP 413; final-body overflow returns HTTP 403 with
`request_payload_too_large`, before any model send. Enlarging configuration cannot bypass the parser ceiling.

Provider response buffering defaults to at most 1 MiB for both fixed-length and chunked/SSE responses.
The transport's explicit buffer limit can reduce this ceiling. Input `payload_bytes` does **not** declare
an output allowance. Core's response configuration, content checks and the JSON/RPC envelope impose their
own limits, so even a body below 1 MiB may be refused. The UDS client's 8 MiB RPC-response parser ceiling
is a separate direction and is not model-body capacity.

A response buffer overflow discards the response and returns HTTP 400 with `response_buffer_limit_exceeded`.
No provider content is released and the call is not retried. The execution observation retains `PossiblySent`
and a `Withheld` terminal: the provider may already have charged for the call. This is not proof of a complete
response or of zero cost. This observation is process-local; this stage does not export a readable call record
with attempts and terminal/sending facts. The G5-A requirement to expose possible billing in call evidence remains
pending integration and an exported-record regression with [#2506](https://github.com/oceanbase/powercontext/pull/2506).
Smaller, completely received responses still pass `before_release` before release.

Transparent Core/MCP forwarding retains the deployment body limit rather than consuming the model input allowance.
`PC_GATEWAY_MAX_CONCURRENT` (default 64) applies independently to model calls and fallback calls, so an active
fallback SSE connection cannot exhaust the model semaphore. The combined ceiling is therefore up to twice the
configured value, excluding exempt health probes; this setting is not a process-wide concurrency ceiling. Each permit stays attached to the response body
until drop; `/healthz` is exempt from both semaphores. A fallback request with a known oversized Content-Length
returns 413 before forwarding. Unknown-length overflow produces a body error: a prefix may already have reached
Core, and the proxy returns 502 if forwarding fails. It is not successful truncation, and the gateway does not
buffer an entire MCP stream to force a uniform error status.

These are per-body and per-class concurrency limits. They do not measure retained bytes or establish a process
RSS ceiling. Process-wide retained-byte accounting and output-capacity discovery belong to the remaining
[G5-B work in #2464](https://github.com/oceanbase/powercontext/issues/2464).

### Memory extraction over the cached prefix

When the gateway runs on the same host as the Server, Atomic Memory extraction for a captured turn can reuse the
provider's prompt cache for the agent's conversation instead of sending the transcript again. It is off by default and
needs both sides:

- **Gateway:** `PC_GATEWAY_PREFIX_EXTRACTION=on` together with capture (`PC_GATEWAY_SEDIMENT=on`). For a Responses
  conversation, the gateway keeps the exact request it sent and the response output of each turn's final response in
  memory, for at most 30 minutes and at most 64 turns. It records an opaque handle in the captured Source's metadata,
  with the input and output token counts of that final response.
  The request and response never enter the Source, evidence, or logs. The captured Source records the turn in full
  (projection `gateway-turn-text.v2`): no field is truncated and no step is omitted, and every argument field of a tool
  call, tool outputs and errors, other messages, and unrecognized items are included. Only the provider's encrypted
  reasoning, IDs, and other transport metadata are left out, and binary data becomes a placeholder with its size.
  Injected memory blocks are not in the Source. Each Source must fit Core's default extraction input on its own, so
  a turn over about 12,000 tokens by Core's character estimate is captured as consecutive Sources. Their metadata
  `turn` names the turn and the part, and the handle is on the last part.
- **Server:** `POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL` set to the gateway's listen address, and
  `POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS` set to `{"Authorization": "Bearer <PC_GATEWAY_API_KEY>"}`
  when the gateway requires a key. A generation model is still required, because normal extraction is the fallback.

Cached input is not free: the provider still bills it at a reduced rate. When a processing window holds exactly one
such turn, the Server first estimates both requests. The prefixed request costs Core's instruction plus the final
response's output tokens at the full rate, plus the cached input tokens multiplied by
`POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_CACHED_INPUT_RATIO` (default `0.1`). Normal extraction costs its
instructions plus the Source evidence. The window must hold exactly one whole turn: one Source, or every part of one
turn in order. The Server uses the prefix only when that estimate is lower. This usually
holds for turns with substantial tool output, and stops holding as a long conversation's prefix grows. It then sends its extraction instruction, the output schema,
and the Source's provenance to `POST /gateway/v1/extract`. The gateway appends them as one user message after the
kept request and response, sets `tool_choice` to `none`, and sends it to the agent's own target and account. Fields
that would change the cached prefix, such as tools, `prompt_cache_key`, reasoning settings, and a response format, are
not changed. The request passes the Server's normal `before_send` and `before_release` checks. The prefix holds the
memory blocks the gateway injected; the extraction instruction states that content between `BEGIN_POWERCONTEXT_*` and
`END_POWERCONTEXT_*` markers is not evidence for the turn. An enforcing Server withholds a
request that replays the provider's encrypted reasoning, so with Codex and similar clients extraction then falls back
to normal extraction. It is not injected and is not captured. The Server validates the reply with the same schema
and rules as normal extraction.

Any other case falls back to normal extraction: a window that is not exactly one whole turn or has no handle, a prefix that is not
estimated to cost less or whose size is unknown, an expired or unknown
handle, a target mismatch, a withheld or blocked check, a provider or gateway error, or a reply that fails validation.
Each attempt is logged as `atomic_memory.prefixed_extraction`; see
[Logs, metrics, and tracing](observability.md#operational-fields-and-correlation). Model usage, including cached input
tokens, is reported like any other generation.

## Run with Docker

Build the image from the repository root:

```bash
POWERCONTEXT_VERSION=$(uvx --from hatchling --with hatch-vcs hatchling version)
docker build \
  --file docker/Dockerfile \
  --build-arg "POWERCONTEXT_VERSION=${POWERCONTEXT_VERSION}" \
  --tag powercontext-server:local \
  .
```

Run it with a named volume and publish the port only on the host loopback interface:

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  powercontext-server:local
```

The image listens on `0.0.0.0:8000` inside the container, so the host-side address in `--publish` is important. The
named volume persists the SQLite database and scheduler state after the container stops.

## Enable authentication

Load a strong token from your secret manager into the Server process environment:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

For Docker, pass the already-loaded variables without putting the token value in the command:

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  --env POWERCONTEXT_SERVER_ACCESS_MODE=enforced \
  --env POWERCONTEXT_SERVER_AUTH_TOKEN \
  powercontext-server:local
```

Clients then send `Authorization: Bearer <token>`. The liveness and readiness endpoints remain public so an
orchestrator can probe them. API, MCP, metrics, and `/openapi.json` require authentication. The `/docs` shell remains
public, but requests made from the interactive reference require authentication.

Personal or demonstration deployments can additionally set `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true` to expose
`/dashboard/home` on the same port. With `ACCESS_MODE=enforced`, it requires the static Bearer configuration above;
startup fails clearly without a token. Local deployments using `ACCESS_MODE=disabled` can open Dashboard without
sign-in. When authentication is enabled, browser sign-in uses the Server token, not a model API key. Credentials are stored in an HttpOnly,
SameSite=Strict Cookie restricted to `/dashboard`, for up to eight hours. HTTPS sets Secure. Reverse proxies must
preserve the external scheme and host for the sign-in same-origin check.

All holders of the static token share one administrator identity. The Dashboard does not support multi-user RBAC or
provide accounts, SSO, invitations, or grant management. Deployments injecting an Authentication Provider or
AccessControlService must disable the Dashboard; an incompatible enabled configuration is rejected at startup.
Disabling it does not affect team API or MCP access. For personal setup, see
[Install and run](../get-started/install-and-run.md).

A shared static token does not enforce human review or prevent an Agent from approving its own Candidate. For separate
submission and approval, use distinct authenticated Principals and grant review permission only to the reviewer.
See [Separate Agent submission from review](../workflows/review-candidates.md#separate-agent-submission-from-review).

### Migrate data written before enforcement

Data accepted while `ACCESS_MODE=disabled` has no Artifact owner and no Candidate owner attestation. After switching to
`enforced`, that data stays readable, but modifying it returns `403` and approving its pending Candidates returns
`503 artifact_owner_pending`, while data created under enforcement keeps working. The Server never derives ownership,
so an administrator migrates the missing attribution explicitly:

1. Back up the database. The offline command below works while the Server is stopped, before or after the switch:
2. Inventory the affected data with `powercontext server access-ownership-migrate --action plan`. The same command
   also runs before the switch, while `ACCESS_MODE=disabled`. Add
   `--background-principal service:BACKGROUND_ID` with the future `ACCESS_BACKGROUND_PRINCIPAL_ID` to check
   every selected Scope for `scope.contribute` and existing Profile write permission. These checks still run
   after all ownership gaps have been filled. HTTP planning accepts `background_principal` when an Access
   store is available; otherwise it returns `503 relationship_management_unavailable`. Use the offline
   command for this check before enabling enforcement.
   For a configured single-token deployment, the check includes the worker's static Scope preset without
   creating bindings. An existing Profile still requires its own Artifact write permission.
3. Backfill with an explicitly named owning Principal — for a single-user static-token deployment that is the
   `server-token` service Principal:

   ```bash
   powercontext server access-ownership-migrate \
     --action apply --owner service:server-token --maintenance-confirmed
   ```

   `apply` requires `--maintenance-confirmed` after stopping every Server process. Pass `--scope-id` to migrate
   Scopes incrementally on large deployments. Every item is one idempotent write, so interrupted or repeated runs
   refill only still-missing items. Existing owners and Candidate attestations are preserved and reported as
   conflicts, never overwritten. Run `apply` again with another `--owner` when different identities must own
   different Scopes; multi-user deployments keep this identity boundary explicit.

   The inventory includes merged and retired Artifact identities so supported restoration remains authorized.
   It also includes committed Handoffs and each published Handoff copy under its recorded identity.
   Offline `apply`, HTTP `apply`, and the HTTP preview report `owner-recovery-pending` or
   `owner-recovery-delivered_relation_missing` when durable recovery evidence already records an identity.
   They preserve that evidence and do not replace its owner. Candidate recovery checks cover the families accepted
   by migration, including Prompt Candidates, independently of which families require standalone Artifact ownership.
   Complete pending ownership delivery through the
   [ownership recovery procedure](../workflows/scopes-and-access.md#recover-external-ownership-writes).
   With an external Access store, migration retains durable ownership delivery work if its audit write fails.
   Repeating the same HTTP `apply` completes that original audit before reporting success, even if the owner
   relation already exists. A preview does not replay pending work.

   Without a stopped-deployment window, the same flow is available over HTTP on a running Server:
   `POST /v1/access/ownership/apply` with `{"owner": {"type": "service", "id": "server-token"}, "dry_run": true}`
   previews first; in enforced mode both HTTP operations require `server.admin`, and `apply` reports
   `503 ownership_migration_unavailable` while `ACCESS_MODE=disabled`.
4. Verify: modify a previously immutable Artifact, approve a previously blocked Candidate, and confirm `plan` now
   reports no ownership gaps. Include the background Principal in the plan and check that `background` is empty.
   Identities without the required Artifact write or Candidate review authority still receive `403`.

## Check the deployment

Use liveness to determine whether the process can answer HTTP requests:

```bash
curl --fail http://127.0.0.1:8000/health/live
```

Use readiness before sending application traffic:

```bash
curl --fail http://127.0.0.1:8000/health/ready
```

Readiness returns HTTP 503 when a required runtime or database binding is unavailable. An optional inference provider
can make the response `degraded` with HTTP 200 while database-backed operations remain available. Therefore,
`curl --fail` checks only the HTTP status and does not treat `degraded` as a failure. If the deployment depends on
inference, also require the response `status` to be `ready`:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready \
  | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["status"]); sys.exit(data["status"] != "ready")'
```

After enabling authentication, verify a protected endpoint as well:

```bash
curl --fail \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://127.0.0.1:8000/v1/capabilities
```

See [HTTP API](../develop/http-api.md) for request examples and [Configuration](configuration.md) for
all Server settings.

## Protect and back up data

- Back up the directory selected by `POWERCONTEXT_HOME`, or the Docker volume mounted at `/data`.
- Stop writes or stop the Server while taking a filesystem-level SQLite backup.
- Keep database backups and bearer tokens out of the repository.
- Test restoration before relying on a backup procedure.
