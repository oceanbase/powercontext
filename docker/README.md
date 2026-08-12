# PowerContext Server container

Build the image from the repository root:

```bash
docker build --file docker/Dockerfile --tag powercontext-server:local .
```

Run the Server with persistent SQLite and scheduler data:

```bash
docker run --rm \
  --name powercontext-server \
  --publish 8000:8000 \
  --volume powercontext-data:/data \
  powercontext-server:local
```

The image listens on `0.0.0.0:8000`, stores its default data under `/data`, and exposes a Docker health check backed
by `GET /health/ready`. Readiness verifies the database and each configured inference provider. Provider checks make
one minimal real request at startup and refresh at most once every 300 seconds; they use the same credentials as the
Runtime and never expose them in the response. Configure another database or inference provider with the same
`POWERCONTEXT_SERVER_*` environment variables used by a regular Server installation.

The `Build Docker image` GitHub workflow builds downloadable Linux amd64 and arm64 image archives for pull requests,
changes merged to `main`, and manual runs. Publishing a GitHub Release pushes a multi-platform image to Docker Hub.
Repository configuration must provide `DOCKER_USERNAME` and `DOCKER_PASSWORD` secrets plus a `DOCKER_PUSH_BASE`
variable such as `oceanbase`; Release tags must use `vX.Y.Z` or `X.Y.Z` semantic versioning.
