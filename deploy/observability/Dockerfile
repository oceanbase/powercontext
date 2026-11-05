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
FROM python:3.11-slim-bookworm@sha256:eb769d88622468aea09986df8172151981bf6622b5eec58dc38301e20673ff84
COPY --from=uv /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
RUN --mount=type=cache,id=pc-observability-uv,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra cli --extra server --extra tracing-otlp
COPY deploy/observability/run-host.py deploy/observability/init-db.py deploy/observability/model-fixture.py /app/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN mkdir /data && chmod 700 /data
ENTRYPOINT ["powercontext", "server", "run"]
