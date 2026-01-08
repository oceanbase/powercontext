# Docker Deployment Guide for PowerMem Server

This guide provides instructions for building and running PowerMem Server using Docker.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Building the Docker Image](#building-the-docker-image)
- [Running the Container](#running-the-container)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Docker Compose](#docker-compose)
- [Production Deployment](#production-deployment)
- [Troubleshooting](#troubleshooting)

## Prerequisites

- Docker 20.10 or later
- Docker Compose 2.0 or later (optional, for docker-compose setup)

## Quick Start

### Build and Run

```bash
# Build the Docker image (from project root)
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .

# Run the container with shared .env file (recommended)
# This allows both SDK and Server to use the same configuration
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

The server will be available at `http://localhost:8000`.

**Note**: If you have a `.env` file that's shared between the SDK and Server, use the first command with volume mount (`-v`) to ensure both components read from the same configuration file. See [Shared .env File](#shared-env-file-for-sdk-and-server) for more details.

### Using Docker Compose

The `docker/docker-compose.yml` file is pre-configured to:
- Automatically load environment variables from `.env` file
- Mount the `.env` file as a read-only volume at `/app/.env`
- Enable both SDK and Server to use the same configuration

```bash
# Start the server (from project root)
docker-compose -f docker/docker-compose.yml up -d

# View logs
docker-compose -f docker/docker-compose.yml logs -f

# Stop the server
docker-compose -f docker/docker-compose.yml down
```

**Note**: The Docker Compose setup automatically handles the shared `.env` file configuration, so both your local SDK and the containerized Server will use the same configuration values.

## Building the Docker Image

### Basic Build

```bash
# Build from project root directory
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile .
```

### Build with Custom Tag

```bash
# Build from project root directory
docker build -t oceanbase/powermem-server:v0.2.1 -f docker/Dockerfile .
```

### Build with Mirror Sources (for slow network)

If you're experiencing slow download speeds or network timeouts, you can use mirror sources for both pip and apt-get:

```bash
# Using Tsinghua mirror (China) - speeds up both pip and apt-get
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
  --build-arg DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn .

# Using Aliyun mirror (China) - speeds up both pip and apt-get
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  --build-arg PIP_TRUSTED_HOST=mirrors.aliyun.com \
  --build-arg DEBIAN_MIRROR=mirrors.aliyun.com .

# Using only pip mirror (if apt-get is fast enough)
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn .

# Using only Debian mirror (if pip is fast enough)
docker build -t oceanbase/powermem-server:latest -f docker/Dockerfile \
  --build-arg DEBIAN_MIRROR=mirrors.aliyun.com .
```

**Note**: 
- The Dockerfile has been configured with a longer timeout (300 seconds) for pip to handle slow network connections.
- Using Debian mirror can significantly speed up `apt-get` operations (especially when installing gcc, g++, etc.).
- The `docker-build-mirror` Makefile target automatically configures both pip and Debian mirrors.

### Build Arguments (if needed in future)

Currently, the Dockerfile uses a multi-stage build to optimize image size. The build process:

1. **Builder stage**: Installs all dependencies and builds the package
2. **Final stage**: Creates a minimal runtime image with only necessary files

## Running the Container

### Basic Run

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  oceanbase/powermem-server:latest
```

### Run with Environment Variables

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -e POWERMEM_SERVER_HOST=0.0.0.0 \
  -e POWERMEM_SERVER_PORT=8000 \
  -e POWERMEM_SERVER_WORKERS=4 \
  -e POWERMEM_SERVER_API_KEYS=key1,key2,key3 \
  -e POWERMEM_SERVER_AUTH_ENABLED=true \
  -e POWERMEM_SERVER_LOG_LEVEL=INFO \
  oceanbase/powermem-server:latest
```

### Run with Environment File

Create a `.env` file:

```env
POWERMEM_SERVER_HOST=0.0.0.0
POWERMEM_SERVER_PORT=8000
POWERMEM_SERVER_WORKERS=4
POWERMEM_SERVER_API_KEYS=your-api-key-1,your-api-key-2
POWERMEM_SERVER_AUTH_ENABLED=true
POWERMEM_SERVER_LOG_LEVEL=INFO
POWERMEM_SERVER_CORS_ENABLED=true
POWERMEM_SERVER_CORS_ORIGINS=*
```

Run with the environment file:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  --env-file .env \
  oceanbase/powermem-server:latest
```

### Run with Shared .env File (SDK and Server)

When both the SDK and Server need to use the same `.env` file, you can mount it as a volume. This allows the Server running in Docker to read the same configuration file that the SDK uses locally:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

**Note**: The `--env-file` flag loads environment variables from `.env` into the container's environment, while the volume mount (`-v`) makes the `.env` file accessible inside the container at `/app/.env` so the Server's configuration loader can read it directly. This ensures both SDK and Server use the exact same configuration values.

**Benefits of this approach**:
- Single source of truth: One `.env` file for both SDK and Server
- Consistent configuration: Both components read from the same file
- Easy updates: Modify `.env` once, both components pick up changes (after container restart)

### Run with Volume Mounts (for persistent data)

If you need to mount volumes for logs or configuration:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v ./logs:/app/logs \
  -v ./config:/app/config \
  --env-file .env \
  oceanbase/powermem-server:latest
```

## Configuration

### Shared .env File for SDK and Server

PowerMem Server and SDK are designed to share the same `.env` file. The `.env` file contains configuration for both:

- **Server configuration**: `POWERMEM_SERVER_*`, `POWERMEM_AUTH_*`, `POWERMEM_RATE_LIMIT_*`, etc.
- **SDK configuration**: `DATABASE_PROVIDER`, `OCEANBASE_*`, `POSTGRES_*`, `LLM_*`, `EMBEDDING_*`, etc.

When running the Server in Docker, you have two options:

#### Option 1: Mount .env File (Recommended)

Mount the `.env` file as a read-only volume so the Server can read it directly:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  -v $(pwd)/.env:/app/.env:ro \
  --env-file .env \
  oceanbase/powermem-server:latest
```

This approach:
- Allows the Server to read `.env` file directly (same as SDK)
- Ensures both SDK and Server use identical configuration
- Makes it easy to update configuration by editing the `.env` file

#### Option 2: Environment Variables Only

If you prefer not to mount the file, you can use `--env-file` to load environment variables:

```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  --env-file .env \
  oceanbase/powermem-server:latest
```

**Note**: With Docker Compose, the `.env` file is automatically mounted and loaded. See the `docker/docker-compose.yml` file for details.


## Environment Variables

The `.env` file contains configuration for both the PowerMem SDK and Server. The following sections describe the variables used by the Server. For SDK configuration variables (database, LLM, embedding providers), refer to the [Configuration Guide](../docs/guides/0003-configuration.md).

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_HOST` | `0.0.0.0` | Host to bind the server to |
| `POWERMEM_SERVER_PORT` | `8000` | Port to bind the server to |
| `POWERMEM_SERVER_WORKERS` | `4` | Number of worker processes |
| `POWERMEM_SERVER_RELOAD` | `false` | Enable auto-reload (development only) |

### Authentication Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_AUTH_ENABLED` | `true` | Enable API key authentication |
| `POWERMEM_SERVER_API_KEYS` | `` | Comma-separated list of API keys |

### Rate Limiting Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_RATE_LIMIT_ENABLED` | `true` | Enable rate limiting |
| `POWERMEM_SERVER_RATE_LIMIT_PER_MINUTE` | `100` | Requests per minute per IP |

### Logging Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `POWERMEM_SERVER_LOG_FORMAT` | `json` | Log format (json or text) |

### CORS Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_CORS_ENABLED` | `true` | Enable CORS |
| `POWERMEM_SERVER_CORS_ORIGINS` | `*` | Comma-separated list of allowed origins |

### API Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `POWERMEM_SERVER_API_TITLE` | `PowerMem API` | API title |
| `POWERMEM_SERVER_API_VERSION` | `v1` | API version |
| `POWERMEM_SERVER_API_DESCRIPTION` | `PowerMem HTTP API Server - Intelligent Memory System` | API description |

### SDK Configuration Variables (Shared with Server)

The following variables are used by the SDK but may also be referenced by the Server for database connections:

- `DATABASE_PROVIDER`: Database provider (`sqlite`, `oceanbase`, `postgres`)
- `OCEANBASE_*`: OceanBase database configuration
- `POSTGRES_*`: PostgreSQL database configuration
- `SQLITE_*`: SQLite database configuration
- `LLM_*`: LLM provider configuration
- `EMBEDDING_*`: Embedding provider configuration

For complete SDK configuration options, refer to the [Configuration Guide](../docs/guides/0003-configuration.md).

## Docker Compose

A `docker/docker-compose.yml` file is provided for easier deployment:

```yaml
version: '3.8'

services:
  powermem-server:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: powermem-server
    ports:
      - "8000:8000"
    environment:
      - POWERMEM_SERVER_HOST=0.0.0.0
      - POWERMEM_SERVER_PORT=8000
      - POWERMEM_SERVER_WORKERS=4
      - POWERMEM_SERVER_API_KEYS=${POWERMEM_SERVER_API_KEYS:-}
      - POWERMEM_SERVER_AUTH_ENABLED=${POWERMEM_SERVER_AUTH_ENABLED:-true}
      - POWERMEM_SERVER_LOG_LEVEL=${POWERMEM_SERVER_LOG_LEVEL:-INFO}
      - POWERMEM_SERVER_CORS_ENABLED=${POWERMEM_SERVER_CORS_ENABLED:-true}
      - POWERMEM_DATABASE_URL=${POWERMEM_DATABASE_URL:-}
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

### Using Docker Compose

```bash
# Start services (from project root)
docker-compose -f docker/docker-compose.yml up -d

# View logs
docker-compose -f docker/docker-compose.yml logs -f powermem-server

# Stop services
docker-compose -f docker/docker-compose.yml down

# Rebuild and restart
docker-compose -f docker/docker-compose.yml up -d --build
```

## Production Deployment

### Security Best Practices

1. **Use Secrets Management**: Never hardcode API keys or passwords. Use Docker secrets or environment variable injection.

2. **Run as Non-Root User**: The Docker image runs as a non-root user (`powermem`) by default.

3. **Network Security**: Use Docker networks to isolate containers and restrict access.

4. **Resource Limits**: Set appropriate resource limits:

```bash
docker run -d \
  --name powermem-server \
  --memory="2g" \
  --cpus="2" \
  -p 8000:8000 \
  oceanbase/powermem-server:latest
```

5. **Health Checks**: The image includes a health check. Monitor container health:

```bash
docker ps  # Check STATUS column
docker inspect --format='{{.State.Health.Status}}' powermem-server
```

### Production Docker Compose Example

```yaml
version: '3.8'

services:
  powermem-server:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    image: oceanbase/powermem-server:latest
    container_name: powermem-server
    restart: always
    ports:
      - "8000:8000"
    environment:
      - POWERMEM_SERVER_WORKERS=8
      - POWERMEM_SERVER_LOG_LEVEL=INFO
    env_file:
      - .env.production
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 4G
        reservations:
          cpus: '2'
          memory: 2G
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

### Reverse Proxy Setup

For production, it's recommended to use a reverse proxy (nginx, traefik, etc.):

```nginx
# nginx.conf example
upstream powermem {
    server powermem-server:8000;
}

server {
    listen 80;
    server_name api.powermem.example.com;

    location / {
        proxy_pass http://powermem;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Troubleshooting

### Container Won't Start

1. **Check logs**:
```bash
docker logs powermem-server
```

2. **Check environment variables**:
```bash
docker exec powermem-server env | grep POWERMEM
```

3. **Check if .env file is mounted correctly**:
```bash
# Check if .env file exists in container
docker exec powermem-server ls -la /app/.env

# View .env file contents (if mounted)
docker exec powermem-server cat /app/.env
```

4. **Verify database connection**:
```bash
docker exec powermem-server python -c "import psycopg; psycopg.connect('${POWERMEM_DATABASE_URL}')"
```

### Health Check Failing

1. **Check if server is running**:
```bash
docker exec powermem-server curl -f http://localhost:8000/api/v1/system/health
```

2. **Check server logs**:
```bash
docker logs powermem-server --tail 50
```

### Port Already in Use

If port 8000 is already in use, change the port:

```bash
docker run -d \
  --name powermem-server \
  -p 8001:8000 \
  -e POWERMEM_SERVER_PORT=8000 \
  oceanbase/powermem-server:latest
```

### Database Connection Issues

The entrypoint script waits up to 60 seconds for the database. If you need more time or want to disable the wait, you can modify the `docker-entrypoint.sh` script.

### Permission Issues

The container runs as user `powermem` (UID 1000). If you mount volumes, ensure proper permissions:

```bash
sudo chown -R 1000:1000 ./logs
```

### .env File Not Found or Not Readable

If the Server can't read the `.env` file:

1. **Verify the file is mounted**:
```bash
docker exec powermem-server ls -la /app/.env
```

2. **Check file permissions**:
```bash
# Ensure .env file is readable
chmod 644 .env
```

3. **Verify mount path**:
   - The `.env` file should be mounted at `/app/.env` inside the container
   - Use `-v $(pwd)/.env:/app/.env:ro` to mount it as read-only

4. **Alternative: Use environment variables only**:
   If mounting the file doesn't work, you can use `--env-file` to load variables:
```bash
docker run -d \
  --name powermem-server \
  -p 8000:8000 \
  --env-file .env \
  oceanbase/powermem-server:latest
```

**Note**: When using `--env-file`, the Server will read from environment variables, but the SDK running locally will still read from the `.env` file. This is fine as long as both have the same values.

