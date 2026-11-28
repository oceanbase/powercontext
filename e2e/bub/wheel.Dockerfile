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


FROM docker.io/astral/uv:0.10.12 AS uv
FROM docker.io/library/rust:1.97.1-slim-bookworm AS rust
FROM docker.io/library/python:3.12-slim-bookworm AS build

COPY --from=uv /uv /bin/uv
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/local/cargo/bin:$PATH" \
    RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_TOOLCHAIN=1.97.1

WORKDIR /workspace/powercontext
COPY pyproject.toml README.md LICENSE ./
COPY LICENSES/ LICENSES/
COPY scripts/build_lexical.py scripts/build_lexical.py
COPY src/ src/
ARG POWERCONTEXT_VERSION
RUN test -n "$POWERCONTEXT_VERSION" \
    && SETUPTOOLS_SCM_PRETEND_VERSION="$POWERCONTEXT_VERSION" uv build --wheel --out-dir /wheels

FROM scratch AS wheel
COPY --from=build /wheels/ /
