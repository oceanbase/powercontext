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
