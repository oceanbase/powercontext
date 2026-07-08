# Docker Directory

This directory contains all Docker-related files for PowerMem Server.

中文快速部署说明见 [README_CN.md](./README_CN.md).

## Files

- `Dockerfile` - Multi-stage Docker build file for PowerMem Server
- `docker-compose.yml` - Docker Compose configuration file with seekdb support
- `docker-entrypoint.sh` - Container entrypoint script
- `.dockerignore` - Files to exclude from Docker build context
- `DOCKER.md` - Complete Docker deployment documentation

## Quick Start

Prepare the Compose configuration file first:

```bash
cp .env.example docker/.env
# Edit docker/.env and set the LLM provider, model, API key, and base URL you use.
```

### Build Docker Image

From the project root directory:

```bash
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
```

### Run with Docker Compose (with seekdb)

From the project root directory:

```bash
# Without password (default)
docker compose -f docker/docker-compose.yml up -d --build

# With password (set via command line)
SEEKDB_ROOT_PASSWORD=your_password docker compose -f docker/docker-compose.yml up -d --build

# Alternatively, export the variable first
export SEEKDB_ROOT_PASSWORD=your_password
docker compose -f docker/docker-compose.yml up -d --build

# Verify the API server
curl http://localhost:8848/api/v1/system/health

# View logs and stop the stack
docker compose -f docker/docker-compose.yml logs -f powermem-server
docker compose -f docker/docker-compose.yml down
```

Compose loads `docker/.env` through `env_file` for LLM, embedding, server, and other application settings. It intentionally sets `DATABASE_PROVIDER` and `OCEANBASE_*` to the bundled seekdb container; use single-container deployment or a custom Compose override when connecting to an existing database service.

### Run with Docker

From the project root directory:

```bash
docker run -d \
  --name powermem-server \
  -p 8848:8848 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

## Services

### PowerMem Server
- Port: 8848
- Health check: `http://localhost:8848/api/v1/system/health`
- Database: Connected to bundled seekdb; password is controlled by `SEEKDB_ROOT_PASSWORD`

### seekdb Database
- MySQL Port: 2881
- seekdb Web Dashboard: 2886
- Data persistence: Docker volume `docker_seekdb_data`
- Default database: `powermem`
- Password: Controlled by `SEEKDB_ROOT_PASSWORD` environment variable
  - Not set (default): No password
  - Set via command line: Use specified password

## Connecting to seekdb

### Without password (default)
```bash
mysql -h 127.0.0.1 -P 2881 -u root
```

### With password (if SEEKDB_ROOT_PASSWORD is set)
```bash
mysql -h 127.0.0.1 -P 2881 -u root -p
# Enter the password when prompted
```

### seekdb Web Dashboard
Open browser to: `http://localhost:2886`
- Username: `root`
- Password: Same as `SEEKDB_ROOT_PASSWORD` environment variable (not set by default)

## Default Configuration

The `docker-compose.yml` file includes default configuration values:

**PowerMem Server:**
- Host: `0.0.0.0`
- Port: `8848`
- Workers: `4`
- Authentication: Disabled
- CORS: Enabled for all origins

**seekdb:**
- Password: Controlled by `SEEKDB_ROOT_PASSWORD` environment variable (not set by default)
- CPU: 4 cores
- Memory: 4GB
- Database: `powermem`
- Data persistence: Docker volume

## Documentation

For detailed documentation, see [DOCKER.md](./DOCKER.md).

## Notes

- All Docker commands should be run from the **project root directory**, not from the `docker/` directory
- The build context is the project root, so paths in Dockerfile are relative to the project root
- The Compose `.env` file should be copied to `docker/.env`; Docker Compose loads it through `env_file`
- seekdb data is persisted in a Docker volume named `docker_seekdb_data`
- On macOS with Docker version > 4.9.0, there are known issues with seekdb. Consider using an older Docker version if needed.
- **Password Management**: 
  - Default: No password (`SEEKDB_ROOT_PASSWORD` not set)
  - To set a password: Use command line: `SEEKDB_ROOT_PASSWORD=your_password docker compose -f docker/docker-compose.yml up -d --build`
  - Password change: Stop services, set new password via command line, restart services
