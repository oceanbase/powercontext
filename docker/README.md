# Docker Directory

This directory contains all Docker-related files for PowerMem Server.

## Files

- `Dockerfile` - Multi-stage Docker build file for PowerMem Server
- `docker-compose.yml` - Docker Compose configuration file with seekdb support
- `docker-entrypoint.sh` - Container entrypoint script
- `.dockerignore` - Files to exclude from Docker build context
- `DOCKER.md` - Complete Docker deployment documentation

## Quick Start

### Build Docker Image

From the project root directory:

```bash
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
```

### Run with Docker Compose (with seekdb)

From the project root directory:

```bash
# Without password (default)
docker-compose -f docker/docker-compose.yml up -d

# With password (set via command line)
SEEKDB_ROOT_PASSWORD=your_password docker-compose -f docker/docker-compose.yml up -d

# Alternatively, export the variable first
export SEEKDB_ROOT_PASSWORD=your_password
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

## Services

### PowerMem Server
- Port: 8000
- Health check: `http://localhost:8000/api/v1/system/health`
- Database: Connected to seekdb without password

### seekdb Database
- MySQL Port: 2881
- seekdb Web Dashboard: 2886
- Data persistence: Docker volume `seekdb_data`
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
- Port: `8000`
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
- The `.env` file should be in the project root directory and will be mounted into the container
- seekdb data is persisted in a Docker volume named `seekdb_data`
- On macOS with Docker version > 4.9.0, there are known issues with seekdb. Consider using an older Docker version if needed.
- **Password Management**: 
  - Default: No password (`SEEKDB_ROOT_PASSWORD` not set)
  - To set a password: Use command line: `SEEKDB_ROOT_PASSWORD=your_password docker-compose -f docker/docker-compose.yml up -d`
  - Password change: Stop services, set new password via command line, restart services