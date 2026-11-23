# syntax=docker/dockerfile:1.7

FROM docker.io/astral/uv:0.10.12 AS uv


FROM rust:1.95.0-slim-bookworm AS rust

FROM python:3.11-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup

ARG DEBIAN_MIRROR_URL

RUN --mount=type=bind,source=docker/configure_debian_mirror.py,target=/tmp/configure-debian-mirror.py \
    python /tmp/configure-debian-mirror.py \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:$PATH" \
    RUSTUP_TOOLCHAIN=1.95.0 \
    UV_COMPILE_BYTECODE=1 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_HTTP_RETRIES=5 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
COPY scripts/build_lexical.py ./scripts/build_lexical.py

ARG POWERCONTEXT_VERSION

RUN --mount=type=cache,id=powercontext-uv,target=/root/.cache/uv \
    test -n "${POWERCONTEXT_VERSION}" \
    && SETUPTOOLS_SCM_PRETEND_VERSION="${POWERCONTEXT_VERSION}" uv sync \
    --frozen \
    --no-dev \
    --no-editable \
    --extra cli \
    --extra server \
    --extra tracing-otlp \
    --refresh-package powercontext \
    && test "$(.venv/bin/python -c 'from importlib.metadata import version; print(version("powercontext"))')" \
    = "${POWERCONTEXT_VERSION}"


FROM python:3.11-slim-bookworm AS runtime

RUN groupadd --gid 10001 powercontext \
    && useradd --uid 10001 --gid powercontext --home-dir /app --no-create-home --shell /usr/sbin/nologin powercontext \
    && mkdir -p /data \
    && chown powercontext:powercontext /data

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POWERCONTEXT_HOME=/data \
    POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0 \
    POWERCONTEXT_SERVER_HTTP_PORT=8000

# The container binds 0.0.0.0 so a published port is reachable, and its network namespace is the
# controlled boundary the unauthenticated-bind guard is designed for. Declare that opt-in here so the
# documented `docker run` starts out of the box; production deployments should still enable bearer
# auth or terminate TLS upstream (see docker/README.md).
ENV POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true

COPY --from=builder --chown=powercontext:powercontext /app/.venv /app/.venv

USER powercontext

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"]

ENTRYPOINT ["powercontext"]
CMD ["server", "run"]
