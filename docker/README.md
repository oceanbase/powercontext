# PowerContext Server container

Build the image from the repository root:

```bash
POWERCONTEXT_VERSION=$(uvx --from hatchling --with hatch-vcs hatchling version)
docker build \
  --file docker/Dockerfile \
  --build-arg "POWERCONTEXT_VERSION=${POWERCONTEXT_VERSION}" \
  --tag powercontext-server:local \
  .
```

Run the Server with persistent SQLite and scheduler data:

```bash
docker run --rm \
  --name powercontext-server \
  --publish 127.0.0.1:8000:8000 \
  --volume powercontext-data:/data \
  powercontext-server:local
```

The image listens on `0.0.0.0:8000` inside the container. Keep the host-side publish address on loopback unless bearer
authentication and a TLS-terminating network boundary are configured. See
[`Deploy the Server`](../docs/en/docs/how-to/deploy-server.md) for the remote-access setup.

The image stores its default data under `/data` and exposes a Docker health check backed
by `GET /health/ready`. Runtime or database failures return `not_ready` with HTTP 503. A configured inference failure
returns `degraded` with HTTP 200, so database-backed operations remain in traffic while the response exposes the
affected capability. Provider checks make one minimal real request at startup. `ready` and `misconfigured` results
are cached for 300 seconds; `timeout` and `unavailable` results are retried after 30 seconds, and concurrent health
requests share one refresh. Checks use the Runtime's credentials and never expose them in the response. Configure
another database or inference provider with the same `POWERCONTEXT_SERVER_*` environment variables used by a regular
Server installation.

## Network exposure

PowerContext refuses to start an unauthenticated Server on a non-loopback address unless
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` opts in. The image binds `0.0.0.0` so a published
port is reachable, and its network namespace is the controlled boundary that opt-in is meant for, so the image sets
it by default and the `docker run` above starts without extra configuration. Access is still governed by which ports
you publish (`--publish`) and the surrounding network. For an exposed deployment, put the Server behind a
TLS-terminating proxy and enable bearer authentication with
`POWERCONTEXT_SERVER_AUTH_ENABLED=true` and `POWERCONTEXT_SERVER_AUTH_TOKEN=...`; when authentication is enabled the
opt-in is no longer required.

The `Build Docker image` GitHub workflow builds downloadable Linux amd64 and arm64 image archives for pull requests,
changes merged to `master`, and manual runs. Publishing a GitHub Release pushes a multi-platform image to Docker Hub.
Repository configuration must provide `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets plus a `DOCKER_PUSH_BASE`
variable such as `oceanbase`; Release tags must use `vX.Y.Z` or `X.Y.Z` semantic versioning.
