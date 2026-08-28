# syntax=docker/dockerfile:1.7

FROM docker.io/astral/uv:0.10.12 AS uv


FROM python:3.11-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_CONCURRENT_DOWNLOADS=4 \
    UV_HTTP_RETRIES=5 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

ARG POWERCONTEXT_VERSION

RUN --mount=type=cache,id=powercontext-uv,target=/root/.cache/uv \
    test -n "${POWERCONTEXT_VERSION}" \
    && SETUPTOOLS_SCM_PRETEND_VERSION="${POWERCONTEXT_VERSION}" uv sync \
    --frozen \
    --no-dev \
    --no-editable \
    --extra cli \
    --extra server \
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
