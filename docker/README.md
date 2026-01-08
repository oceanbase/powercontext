# Docker Directory

This directory contains all Docker-related files for PowerMem Server.

## Files

- `Dockerfile` - Multi-stage Docker build file for PowerMem Server
- `docker-compose.yml` - Docker Compose configuration file
- `docker-entrypoint.sh` - Container entrypoint script
- `.dockerignore` - Files to exclude from Docker build context
- `DOCKER.md` - Complete Docker deployment documentation

## Quick Start

### Build Docker Image

From the project root directory:

```bash
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
```

### Run with Docker Compose

From the project root directory:

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### Run with Docker

From the project root directory:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

## Documentation

For detailed documentation, see [DOCKER.md](./DOCKER.md).

## Notes

- All Docker commands should be run from the **project root directory**, not from the `docker/` directory
- The build context is the project root, so paths in Dockerfile are relative to the project root
- The `.env` file should be in the project root directory and will be mounted into the container

