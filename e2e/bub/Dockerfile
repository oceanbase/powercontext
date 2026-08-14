# syntax=docker/dockerfile:1.7

FROM docker.io/astral/uv:0.10.12 AS uv

FROM docker.io/library/docker:27.5.1-dind AS docker

FROM python:3.12-slim-bookworm

COPY --from=uv /uv /uvx /bin/
COPY --from=docker /usr/local/bin/ /usr/local/bin/
COPY --from=docker /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git iproute2 iptables pigz socat \
    && rm -rf /var/lib/apt/lists/* \
    && docker compose version

ENV PATH="/workspace/powercontext/e2e/bub/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POWERCONTEXT_E2E_REPOSITORY=/workspace/powercontext \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /workspace/powercontext

COPY . .

RUN --mount=type=cache,id=powercontext-bub-e2e-uv,target=/root/.cache/uv \
    uv sync --project e2e/bub --frozen --no-dev --no-editable

COPY --chmod=0755 e2e/bub/container-entrypoint.sh /usr/local/bin/powercontext-e2e-container

ENTRYPOINT ["powercontext-e2e-container"]
