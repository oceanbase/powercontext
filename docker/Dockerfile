# Multi-stage build for PowerMem Server
FROM python:3.11-slim as builder

# Build argument for pip index URL (optional, for using mirror sources)
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

# Install the package in build stage
# Increase timeout to handle slow network connections
# Configure pip with longer timeout and optional mirror source
RUN pip config set global.timeout 300 && \
    if [ -n "$PIP_INDEX_URL" ]; then \
        pip config set global.index-url "$PIP_INDEX_URL" && \
        if [ -n "$PIP_TRUSTED_HOST" ]; then \
            pip config set global.trusted-host "$PIP_TRUSTED_HOST"; \
        fi; \
    fi && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir .

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
EXPOSE 8000

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POWERMEM_SERVER_HOST=0.0.0.0 \
    POWERMEM_SERVER_PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/system/health || exit 1

# Use entrypoint script
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command
CMD ["powermem-server"]

