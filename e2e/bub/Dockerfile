# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.10.12 AS uv

FROM python:3.12-slim-bookworm

COPY --from=uv /uv /uvx /bin/

ENV PATH="/workspace/powercontext/e2e/bub/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /workspace/powercontext

COPY . .

RUN --mount=type=cache,id=powercontext-bub-e2e-uv,target=/root/.cache/uv \
    uv sync --project e2e/bub --frozen --no-dev --no-editable

ENTRYPOINT ["powercontext-e2e"]
