# syntax=docker/dockerfile:1.7
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM docker.io/astral/uv:0.10.12@sha256:72ab0aeb448090480ccabb99fb5f52b0dc3c71923bffb5e2e26517a1c27b7fec AS uv
FROM rust:1.95.0-slim-bookworm AS rust
FROM python:3.11-slim-bookworm@sha256:eb769d88622468aea09986df8172151981bf6622b5eec58dc38301e20673ff84 AS builder
COPY --from=uv /uv /bin/uv
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/*
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo \
    PATH="/usr/local/cargo/bin:$PATH" RUSTUP_TOOLCHAIN=1.95.0
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
COPY scripts/build_lexical.py ./scripts/build_lexical.py
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
ARG PC_CI_PYTHON_MIRROR
RUN --mount=type=cache,id=pc-observability-uv,target=/root/.cache/uv \
    --mount=type=bind,source=scripts/ci_python_mirror.py,target=/tmp/ci_python_mirror.py \
    PC_CI_PYTHON_MIRROR="${PC_CI_PYTHON_MIRROR}" python /tmp/ci_python_mirror.py \
    sync --frozen --no-dev --no-editable --extra cli --extra server --extra tracing-otlp
FROM python:3.11-slim-bookworm@sha256:eb769d88622468aea09986df8172151981bf6622b5eec58dc38301e20673ff84
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY deploy/observability/run-host.py deploy/observability/init-db.py deploy/observability/model-fixture.py /app/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir /data && chmod 700 /data
ENTRYPOINT ["powercontext", "server", "run"]
