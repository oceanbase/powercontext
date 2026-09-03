---
title: Deploy the Server
description: Run PowerContext with persistent data, health checks, authentication, and a safe network boundary.
---

# Deploy the Server

`powercontext server run` is a foreground process. On a personal macOS, Linux, or Windows workstation, PowerContext can register
that same Server runner with the native current-user service manager. Managed deployments should continue to use a
container platform or an administrator-owned service manager.

## Run a persistent personal Server

Install and start the optional current-user service:

```bash
powercontext service install
powercontext service status
```

On Windows, the command asks whether to enable startup at the current user's next login when neither
`--start-on-login` nor `--no-start-on-login` is supplied; pressing Enter keeps login auto-start disabled. Use either
option for a non-interactive choice.

Linux uses `systemd --user` and writes logs to the user journal. macOS uses a per-user LaunchAgent, and Windows uses a current-user Task Scheduler task; both write stdout and stderr below the PowerContext user data directory. `service status` reports the exact log selector or path.

For an explicit Server configuration, protect the environment file before installing:

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
```

On Windows, remove inherited access and grant the file only to the current user, `SYSTEM`, and local `Administrators` before validation, for example:

```powershell
icacls $env:USERPROFILE\powercontext.env /inheritance:r /grant:r "$env:USERNAME:(F)" "SYSTEM:(F)" "Administrators:(F)"
```

The native definition stores only the absolute file path and non-content file identity metadata. On Windows this
includes the current user's owner SID, which is revalidated whenever the launcher starts. It does not copy
credentials or the caller's shell environment. Re-run `service install` after upgrading PowerContext or changing the
environment file. Remove the registration without deleting Server data or logs with:

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

Install PowerContext as described in [Install and run](install-and-run.md), then choose a persistent data directory:

```bash
export POWERCONTEXT_HOME=/srv/powercontext
powercontext server run
```

The process must be able to create and update this directory. The default SQLite database and scheduler state are
stored below it. Supply the same environment variables whenever your service manager restarts the process.

PowerContext does not search for a `.env` file automatically. Export the variables, configure them in the service
manager or container platform, or pass one explicit file:

```bash
powercontext config validate --env-file /etc/powercontext/powercontext.env
powercontext server run --env-file /etc/powercontext/powercontext.env
```

The file may contain provider credentials or a bearer token, so restrict it to the Server operator. Values in the
file override same-named process values; inherited `POWERCONTEXT_SERVER_*` variables that are absent from the file
are ignored. See the [Full-capability Quick Start](full-capability-runtime.md) to generate a validated file
interactively.

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
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN"
powercontext server run
```

For Docker, pass the already-loaded variables without putting the token value in the command:

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  --env POWERCONTEXT_SERVER_AUTH_ENABLED=true \
  --env POWERCONTEXT_SERVER_AUTH_TOKEN \
  powercontext-server:local
```

Clients then send `Authorization: Bearer <token>`. The liveness and readiness endpoints remain public so an
orchestrator can probe them. API, MCP, metrics, and `/openapi.json` require authentication. The `/docs` shell remains
public, but requests made from the interactive reference require authentication.
The Server's web-page shells and static assets remain public so they can show a sign-in form; they do not return
protected data without the token. Open the Dashboard, Skills, Review, or Handoff Report page and enter the same token
there. It remains in the current browser tab's session storage rather than being added to the URL.

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
can make the response `degraded` with HTTP 200 while database-backed operations remain available.

After enabling authentication, verify a protected endpoint as well:

```bash
curl --fail \
  --header "Authorization: Bearer ${POWERCONTEXT_DEPLOYMENT_TOKEN}" \
  http://127.0.0.1:8000/v1/capabilities
```

See [HTTP API](../reference/http-api.md) for request examples and [Configuration](../reference/configuration.md) for
all Server settings.

## Protect and back up data

- Back up the directory selected by `POWERCONTEXT_HOME`, or the Docker volume mounted at `/data`.
- Stop writes or stop the Server while taking a filesystem-level SQLite backup.
- Keep database backups and bearer tokens out of the repository.
- Test restoration before relying on a backup procedure.
