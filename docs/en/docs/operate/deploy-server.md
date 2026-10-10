---
title: Deploy the Server
description: Run PowerContext with persistent data, health checks, authentication, and a safe network boundary.
---

# Deploy the Server

For client address configuration and the `--allow-insecure-http` confirmation, see
[Connect to a remote Server](connect-remote-server.md). This is a client option; it does not change the Server listener
or authentication configuration.

Windows support is `experimental`.

For supported personal macOS and Linux installations, the recommended lifecycle uses the native current-user service
manager. `powercontext server run` remains available for development, debugging, temporary use, and unsupported
platforms. Managed deployments use a container platform or an administrator-owned service manager.

## Run a persistent personal Server

Install and start the current-user service explicitly. For defaults without an environment file:

```bash
powercontext service install
powercontext service status
```

Linux uses `systemd --user` and writes logs to the user journal. macOS uses a per-user LaunchAgent, and Windows uses a current-user Task Scheduler task; both write stdout and stderr below the PowerContext user data directory. `service status` reports the exact log selector or path.

On macOS and Linux, the service keeps running after the terminal closes and resumes when you log in after a reboot.
This is a per-user service, not a system service that starts before login. Linux requires an available user service manager; installation
does not enable linger. If your platform or session does not support personal services, use the foreground command
or an operator-managed deployment.

Stop an existing foreground Server with `Ctrl-C` before installing. If the endpoint is already live, installation
can register the service without starting a manager-owned Server; a live endpoint alone does not confirm the handover.
Use `service status` to verify both an active owned manager and Server liveness. Check business readiness separately
with `powercontext ready` and configured capabilities with `powercontext capabilities`, using the client connection settings.

Personal services support loopback addresses only. Even with authentication enabled, setting
`POWERCONTEXT_SERVER_HTTP_HOST` to a non-loopback address makes `service install` reject the installation. To allow
access from another machine, use a container or an administrator-owned service manager, or put a same-host reverse proxy
in front of the loopback Server.

On Windows, the command asks whether to enable startup at the current user's next login when neither
`--start-on-login` nor `--no-start-on-login` is supplied; pressing Enter keeps login auto-start disabled. Use either
option for a non-interactive choice.

For a wizard-generated `.env` or another explicit Server configuration, always pass the protected file:

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
credentials or the caller's shell environment, and does not discover `.env` in the current directory. Put custom
storage paths, model settings, and credentials in this file. Re-run `service install --env-file /path/to/powercontext.env`
after upgrading PowerContext or changing the environment file. This includes writing client-only Scope IDs to a shared
file: the next service start rejects a changed file until its registration is updated. After an explicit `service stop`,
run `service install` from the new environment, passing the same
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
