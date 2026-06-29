# Multi-stage build for PowerMem Server
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.28
FROM ${UV_IMAGE} as uv

FROM node:20-slim as frontend-builder
WORKDIR /dashboard-build
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY dashboard/package.json dashboard/pnpm-lock.yaml ./
RUN pnpm install
COPY dashboard/ ./
RUN pnpm build

FROM python:3.11-slim as builder
COPY --from=uv /uv /uvx /usr/local/bin/

# Build argument for Python package index URL (optional, for using mirror sources)
# Usage: docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
ARG PIP_TRUSTED_HOST=""

# Build argument for Debian mirror (optional, for faster apt-get)
# Usage: docker build --build-arg DEBIAN_MIRROR=mirrors.aliyun.com
ARG DEBIAN_MIRROR=""

# Set working directory
WORKDIR /build

# Configure Debian mirror if provided, then install build dependencies
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org/debian|http://$DEBIAN_MIRROR/debian|g" /etc/apt/sources.list.d/debian.sources || \
        sed -i "s|http://deb.debian.org/debian|http://$DEBIAN_MIRROR/debian|g" /etc/apt/sources.list || true; \
        sed -i "s|http://security.debian.org/debian-security|http://$DEBIAN_MIRROR/debian-security|g" /etc/apt/sources.list.d/debian.sources || \
        sed -i "s|http://security.debian.org/debian-security|http://$DEBIAN_MIRROR/debian-security|g" /etc/apt/sources.list || true; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files and source code needed for building
COPY pyproject.toml ./
COPY README.md ./
COPY src/ ./src/

# Inject frontend assets before Python package install
COPY --from=frontend-builder /dashboard-build/dist/ ./src/server/dashboard/

# Install the package in build stage
# Increase timeout to handle slow network connections
# Configure uv with optional mirror source
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        export UV_DEFAULT_INDEX="$PIP_INDEX_URL"; \
    fi && \
    if [ -n "$PIP_TRUSTED_HOST" ]; then \
        export UV_INSECURE_HOST="$PIP_TRUSTED_HOST"; \
    fi && \
    uv pip install --system --no-cache '.[server,seekdb]'

# Final stage
FROM python:3.11-slim

# Build argument for Debian mirror (needs to be redeclared in each stage)
ARG DEBIAN_MIRROR=""

# Set working directory
WORKDIR /app

# Configure Debian mirror if provided, then install runtime dependencies
RUN if [ -n "$DEBIAN_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org/debian|http://$DEBIAN_MIRROR/debian|g" /etc/apt/sources.list.d/debian.sources || \
        sed -i "s|http://deb.debian.org/debian|http://$DEBIAN_MIRROR/debian|g" /etc/apt/sources.list || true; \
        sed -i "s|http://security.debian.org/debian-security|http://$DEBIAN_MIRROR/debian-security|g" /etc/apt/sources.list.d/debian.sources || \
        sed -i "s|http://security.debian.org/debian-security|http://$DEBIAN_MIRROR/debian-security|g" /etc/apt/sources.list || true; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 powermem && \
    chown -R powermem:powermem /app

# Copy installed package from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source code
COPY --chown=powermem:powermem src/ ./src/
COPY --chown=powermem:powermem pyproject.toml ./

# Copy entrypoint script
COPY --chown=powermem:powermem docker/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root user
USER powermem

# Expose default port
EXPOSE 8848

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POWERMEM_SERVER_HOST=0.0.0.0 \
    POWERMEM_SERVER_PORT=8848

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8848/api/v1/system/health || exit 1

# Use entrypoint script
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command: API server + PMem Dashboard on port 8848
CMD ["powermem-server", "--host", "0.0.0.0", "--port", "8848"]
